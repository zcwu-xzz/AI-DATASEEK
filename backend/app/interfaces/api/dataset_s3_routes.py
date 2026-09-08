"""S3 read subset: SigV4, Get/HeadObject, ListObjectsV2 and bucket discovery."""
import asyncio
import mimetypes
from datetime import datetime, timezone
from email.utils import formatdate
from urllib.parse import quote
from xml.etree.ElementTree import Element, SubElement, tostring

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

from app.application.services.data_center_dataset_service import DataCenterDatasetService
from app.application.services.dataset_s3 import S3Error, authenticate, issue_credentials
from app.application.services.dataset_s3_files import FileReader, byte_range, resolve_file
from app.domain.models.user import User
from app.interfaces.dependencies import get_current_user
from app.interfaces.schemas.base import APIResponse

router = APIRouter(tags=["dataset-s3"])


def xml_response(root, status=200):
    return Response(tostring(root, encoding="utf-8", xml_declaration=True), status_code=status, media_type="application/xml", headers={"Cache-Control": "no-store"})


def error_response(error):
    root = Element("Error")
    SubElement(root, "Code").text = error.code
    SubElement(root, "Message").text = error.message
    return xml_response(root, error.status)


class DownloadRequest(BaseModel):
    relative_path: str = Field(min_length=1, max_length=4096)


@router.post("/datasets/{dataset_id}/files/s3-download")
async def prepare_download(dataset_id: str, body: DownloadRequest, user: User = Depends(get_current_user)):
    dataset = await DataCenterDatasetService().get_dataset(dataset_id, user_id=user.id)
    try:
        target = resolve_file(dataset, body.relative_path)
        reader = FileReader(target)
        info = await asyncio.to_thread(reader.open)
        await asyncio.to_thread(reader.close)
        result = await issue_credentials(dataset_id, user.id, body.relative_path)
        result.update(filename=body.relative_path.rsplit("/", 1)[-1], size=info["size"], relative_path=body.relative_path)
        response = APIResponse.success(result)
        return Response(response.model_dump_json(), media_type="application/json", headers={"Cache-Control": "no-store"})
    except S3Error as exc:
        return Response(APIResponse(code=exc.status, msg=exc.message).model_dump_json(), status_code=exc.status, media_type="application/json")


@router.api_route("/s3", methods=["GET", "HEAD", "PUT", "POST", "DELETE", "PATCH"])
@router.api_route("/s3/", methods=["GET", "HEAD", "PUT", "POST", "DELETE", "PATCH"])
@router.api_route("/s3/{bucket}", methods=["GET", "HEAD", "PUT", "POST", "DELETE", "PATCH"])
@router.api_route("/s3/{bucket}/{key:path}", methods=["GET", "HEAD", "PUT", "POST", "DELETE", "PATCH"])
async def gateway(request: Request, bucket: str | None = None, key: str | None = None):
    reader = None
    try:
        if request.method not in {"GET", "HEAD"}:
            raise S3Error("AccessDenied", "This dataset gateway is read-only", 403)
        if key == "":
            key = None
        ticket = await authenticate(request, bucket, key)
        try:
            dataset = await DataCenterDatasetService().get_dataset(ticket["dataset_id"], user_id=ticket["user_id"])
        except Exception:
            raise S3Error("AccessDenied", "Dataset is no longer available", 403) from None
        target = resolve_file(dataset, ticket["key"])
        if bucket is None:
            root = Element("ListAllMyBucketsResult", xmlns="http://s3.amazonaws.com/doc/2006-03-01/")
            buckets = SubElement(root, "Buckets")
            entry = SubElement(buckets, "Bucket")
            SubElement(entry, "Name").text = ticket["bucket"]
            SubElement(entry, "CreationDate").text = dataset.created_at.isoformat()
            return xml_response(root)
        if key is None:
            q = request.query_params
            if "location" in q:
                return xml_response(Element("LocationConstraint", xmlns="http://s3.amazonaws.com/doc/2006-03-01/"))
            if request.method == "HEAD":
                return Response(headers={"x-amz-bucket-region": "us-east-1"})
            if q.get("list-type") != "2":
                raise S3Error("NotImplemented", "Use ListObjectsV2", 501)
            if q.get("delimiter", "/") != "/" or q.get("encoding-type", "url") != "url":
                raise S3Error("InvalidArgument", "Unsupported listing parameters", 400)
            prefix = q.get("prefix", "")
            root = Element("ListBucketResult", xmlns="http://s3.amazonaws.com/doc/2006-03-01/")
            encode = (lambda value: quote(value, safe="/")) if q.get("encoding-type") == "url" else (lambda value: value)
            SubElement(root, "Name").text = bucket
            SubElement(root, "Prefix").text = encode(prefix)
            SubElement(root, "IsTruncated").text = "false"
            try:
                limit = min(1000, max(0, int(q.get("max-keys", "1000"))))
            except ValueError:
                raise S3Error("InvalidArgument", "Invalid max-keys", 400) from None
            SubElement(root, "MaxKeys").text = str(limit)
            if q.get("encoding-type"):
                SubElement(root, "EncodingType").text = "url"
            matched = limit > 0 and ticket["key"].startswith(prefix) and ticket["key"] > q.get("start-after", "")
            SubElement(root, "KeyCount").text = "1" if matched else "0"
            if matched:
                remainder = ticket["key"][len(prefix):]
                if q.get("delimiter") and "/" in remainder:
                    item = SubElement(root, "CommonPrefixes")
                    SubElement(item, "Prefix").text = encode(prefix + remainder.split("/", 1)[0] + "/")
                else:
                    reader = FileReader(target)
                    info = await asyncio.to_thread(reader.open)
                    await asyncio.to_thread(reader.close)
                    item = SubElement(root, "Contents")
                    SubElement(item, "Key").text = encode(ticket["key"])
                    SubElement(item, "Size").text = str(info["size"])
                    SubElement(item, "ETag").text = '"' + info["version"] + '"'
                    SubElement(item, "LastModified").text = datetime.fromtimestamp(info["mtime"], timezone.utc).isoformat()
                    SubElement(item, "StorageClass").text = "STANDARD"
            return xml_response(root)
        if any(k in request.query_params for k in ("versionId", "partNumber", "acl", "attributes", "uploadId")):
            raise S3Error("NotImplemented", "Object versions and multipart operations are not supported", 501)
        reader = FileReader(target)
        info = await asyncio.to_thread(reader.open)
        etag = '"' + info["version"] + '"'
        headers = {"ETag": etag, "Last-Modified": formatdate(info["mtime"], usegmt=True), "Accept-Ranges": "bytes", "Cache-Control": "private, no-store", "Content-Disposition": "attachment; filename*=UTF-8''" + quote(key.rsplit("/", 1)[-1], safe="")}
        if request.headers.get("if-match") not in (None, "*", etag):
            raise S3Error("PreconditionFailed", "File has changed", 412)
        if request.headers.get("if-none-match") in ("*", etag):
            await asyncio.to_thread(reader.close)
            return Response(status_code=304, headers=headers)
        try:
            start, length, status = byte_range(request.headers.get("range"), info["size"])
        except S3Error as exc:
            await asyncio.to_thread(reader.close)
            response = error_response(exc)
            response.headers["Content-Range"] = f"bytes */{info['size']}"
            return response
        headers["Content-Length"] = str(length)
        if status == 206:
            headers["Content-Range"] = f"bytes {start}-{start + length - 1}/{info['size']}"
        mime = mimetypes.guess_type(key)[0] or "application/octet-stream"
        if request.method == "HEAD":
            await asyncio.to_thread(reader.close)
            return Response(status_code=status, headers=headers, media_type=mime)
        return StreamingResponse(reader.stream(start, length), status_code=status, headers=headers, media_type=mime, background=BackgroundTask(reader.close))
    except S3Error as exc:
        if reader:
            await asyncio.to_thread(reader.close)
        return error_response(exc)
