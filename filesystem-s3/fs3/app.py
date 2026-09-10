import mimetypes
import os
import re
import secrets
import time
from datetime import datetime, timezone
from email.utils import formatdate
from urllib.parse import quote
from xml.etree.ElementTree import Element, SubElement, tostring

from fastapi import Depends, FastAPI, Header, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool

from .config import Config
from .errors import S3Error
from .filesystem import Reader, byte_range, list_page, open_relative, parts
from .signing import authenticate, decode_token, issue, page_token
from .store import Store

NS = 'http://s3.amazonaws.com/doc/2006-03-01/'


class BucketRequest(BaseModel):
    root: str = Field(min_length=1, max_length=128)
    directory: str = Field(default='', max_length=4096)


class GrantRequest(BaseModel):
    bucket: str = Field(min_length=3, max_length=63)
    key: str | None = Field(default=None, max_length=4096)
    prefix: str = Field(default='', max_length=4096)
    source_key: str | None = Field(default=None, max_length=4096)
    ttl_seconds: int = Field(default=900, ge=1, le=86400)


def xml(root, status=200, headers=None):
    return Response(tostring(root, encoding='utf-8', xml_declaration=True), status_code=status,
                    media_type='application/xml', headers={'Cache-Control': 'no-store', **(headers or {})})


def create_app(config=None):
    config = config or Config.from_env()
    store = Store(config.state_path)
    app = FastAPI(title='Filesystem S3 Gateway', docs_url=None, redoc_url=None, openapi_url=None)
    app.state.config, app.state.store = config, store

    @app.exception_handler(S3Error)
    async def s3_error(request, exc):
        if request.url.path.startswith('/admin/'):
            return JSONResponse({'code': exc.code, 'message': exc.message}, status_code=exc.status, headers={'Cache-Control': 'no-store'})
        root = Element('Error')
        SubElement(root, 'Code').text = exc.code
        SubElement(root, 'Message').text = exc.message
        return xml(root, exc.status)

    def admin(authorization: str = Header(default='')):
        if not secrets.compare_digest(authorization.encode(), ('Bearer ' + config.admin_key).encode()):
            raise S3Error()

    def location(bucket):
        record = store.bucket(bucket)
        root = config.root_paths.get(record['root'])
        if root is None: raise S3Error('ServiceUnavailable', 'Configured root is unavailable', 503)
        return root, record

    def source_path(record, key):
        parts(key)
        return '/'.join(p for p in (record['directory'], key) if p)

    @app.get('/healthz')
    def health():
        return {'status': 'ok', 'service': 'filesystem-s3'}

    @app.get('/admin/v1/roots', dependencies=[Depends(admin)])
    def roots():
        return {'roots': sorted(config.root_paths)}

    @app.put('/admin/v1/buckets/{bucket}', dependencies=[Depends(admin)])
    def register(bucket: str, body: BucketRequest):
        if not re.fullmatch(r'[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]', bucket) or '..' in bucket or re.fullmatch(r'[0-9.]+', bucket):
            raise S3Error('InvalidBucketName', 'Invalid bucket name', 400)
        if body.root not in config.root_paths: raise S3Error('InvalidArgument', 'Unknown configured root', 400)
        parts(body.directory, allow_empty=True)
        fd = open_relative(config.root_paths[body.root], body.directory, directory=True)
        os.close(fd)
        store.register(bucket, {'root': body.root, 'directory': body.directory, 'created': time.time()})
        return {'bucket': bucket, 'root': body.root}

    @app.delete('/admin/v1/buckets/{bucket}', dependencies=[Depends(admin)])
    def delete_bucket(bucket: str):
        store.delete_bucket(bucket)
        return {'revoked': True}

    @app.post('/admin/v1/credentials', dependencies=[Depends(admin)])
    def credentials(body: GrantRequest):
        root, record = location(body.bucket)
        if body.ttl_seconds > config.max_ttl: raise S3Error('InvalidArgument', 'TTL exceeds configured limit', 400)
        if body.prefix:
            parts(body.prefix.rstrip('/'))
        if body.key is not None:
            parts(body.key)
            if body.prefix: raise S3Error('InvalidArgument', 'Use either exact key or prefix scope', 400)
            target = body.source_key if body.source_key is not None else body.key
            reader = Reader(root, source_path(record, target))
            info = reader.info
            reader.close()
        else:
            if body.source_key is not None: raise S3Error('InvalidArgument', 'source_key requires an exact key', 400)
            info = None
        result = issue(config, store, body.bucket, body.key, body.prefix, body.source_key, body.ttl_seconds)
        if info: result.update(size=info['size'], filename=body.key.rsplit('/', 1)[-1], relative_path=body.key)
        return JSONResponse(result, headers={'Cache-Control': 'no-store'})

    @app.delete('/admin/v1/credentials/{access}', dependencies=[Depends(admin)])
    def revoke(access: str):
        store.revoke(access)
        return {'revoked': True}

    def listing(request, bucket, grant, root_path, record):
        q = request.query_params
        if q.get('list-type') != '2': raise S3Error('NotImplemented', 'Use ListObjectsV2', 501)
        if q.get('delimiter', '') not in {'', '/'} or q.get('encoding-type', 'url') != 'url':
            raise S3Error('InvalidArgument', 'Unsupported listing parameters', 400)
        try:
            maximum = int(q.get('max-keys', '1000'))
            if maximum < 0: raise ValueError()
            maximum = min(maximum, 1000)
        except ValueError:
            raise S3Error('InvalidArgument', 'Invalid max-keys', 400) from None
        prefix, delimiter = q.get('prefix', ''), q.get('delimiter', '')
        if prefix: parts(prefix.rstrip('/'), allow_empty=True)
        # Token is bound to scope and parameters; cannot be reused for another grant.
        parameters = [bucket, grant['key'], grant['prefix'], prefix, delimiter]
        after = decode_token(config, q['continuation-token'], parameters) if q.get('continuation-token') else q.get('start-after', '')
        if maximum == 0:
            entries, more = [], False
        elif grant['key'] is not None:
            key = grant['key']
            entries, more = [], False
            if key.startswith(prefix):
                suffix = key[len(prefix):]
                name = prefix + suffix.split('/', 1)[0] + '/' if delimiter and '/' in suffix else key
                if name > after:
                    if name != key: entries = [(name, None)]
                    else:
                        reader = Reader(root_path, source_path(record, grant['source_key'] or key))
                        entries = [(name, reader.info)]; reader.close()
        else:
            entries, more = list_page(root_path, record['directory'], prefix, delimiter, after, maximum, config.scan_limit, grant['prefix'])
        encode = (lambda value: quote(value, safe='/')) if q.get('encoding-type') else (lambda value: value)
        node = Element('ListBucketResult', xmlns=NS)
        for k, v in [('Name', bucket), ('Prefix', encode(prefix)), ('MaxKeys', str(maximum)), ('KeyCount', str(len(entries))), ('IsTruncated', str(more).lower())]:
            SubElement(node, k).text = v
        if delimiter: SubElement(node, 'Delimiter').text = delimiter
        if q.get('encoding-type'): SubElement(node, 'EncodingType').text = 'url'
        if q.get('continuation-token'): SubElement(node, 'ContinuationToken').text = q['continuation-token']
        if more: SubElement(node, 'NextContinuationToken').text = page_token(config, parameters, entries[-1][0])
        for key, info in entries:
            if info is None:
                SubElement(SubElement(node, 'CommonPrefixes'), 'Prefix').text = encode(key)
            else:
                item = SubElement(node, 'Contents')
                for k, v in [('Key', encode(key)), ('Size', str(info['size'])), ('ETag', info['etag']), ('LastModified', datetime.fromtimestamp(info['mtime'], timezone.utc).isoformat()), ('StorageClass', 'STANDARD')]:
                    SubElement(item, k).text = v
        return xml(node)

    @app.api_route('/s3', methods=['GET', 'HEAD', 'PUT', 'POST', 'DELETE', 'PATCH', 'OPTIONS'])
    @app.api_route('/s3/', methods=['GET', 'HEAD', 'PUT', 'POST', 'DELETE', 'PATCH', 'OPTIONS'])
    @app.api_route('/s3/{bucket}', methods=['GET', 'HEAD', 'PUT', 'POST', 'DELETE', 'PATCH', 'OPTIONS'])
    @app.api_route('/s3/{bucket}/{key:path}', methods=['GET', 'HEAD', 'PUT', 'POST', 'DELETE', 'PATCH', 'OPTIONS'])
    async def gateway(request: Request, bucket: str | None = None, key: str | None = None):
        if request.method not in {'GET', 'HEAD'}: raise S3Error('AccessDenied', 'This gateway is read-only', 403)
        key = key or None
        grant = await run_in_threadpool(authenticate, config, store, request, bucket, key)
        root_path, record = await run_in_threadpool(location, grant['bucket'])
        if bucket is None:
            node = Element('ListAllMyBucketsResult', xmlns=NS)
            item = SubElement(SubElement(node, 'Buckets'), 'Bucket')
            SubElement(item, 'Name').text = grant['bucket']
            SubElement(item, 'CreationDate').text = datetime.fromtimestamp(record['created'], timezone.utc).isoformat()
            return xml(node)
        if key is None:
            if request.method == 'HEAD': return Response(headers={'x-amz-bucket-region': config.region})
            if 'location' in request.query_params:
                node = Element('LocationConstraint', xmlns=NS)
                if config.region != 'us-east-1': node.text = config.region
                return xml(node)
            return await run_in_threadpool(listing, request, bucket, grant, root_path, record)
        if any(k in request.query_params for k in ('versionId', 'partNumber', 'acl', 'attributes', 'uploadId', 'uploads', 'tagging', 'torrent', 'select')):
            raise S3Error('NotImplemented', 'Object versions, ACLs and multipart operations are not supported', 501)
        reader = await run_in_threadpool(Reader, root_path, source_path(record, grant['source_key'] or key))
        info = reader.info
        headers = {'ETag': info['etag'], 'Last-Modified': formatdate(info['mtime'], usegmt=True), 'Accept-Ranges': 'bytes',
                   'Cache-Control': 'private, no-store', 'Content-Disposition': "attachment; filename*=UTF-8''" + quote(key.rsplit('/', 1)[-1], safe='')}
        try:
            if request.headers.get('if-match') not in (None, '*', info['etag']):
                raise S3Error('PreconditionFailed', 'File has changed', 412)
            if request.headers.get('if-none-match') in ('*', info['etag']):
                reader.close(); return Response(status_code=304, headers=headers)
            try:
                start, length, status = byte_range(request.headers.get('range'), info['size'])
            except S3Error as exc:
                reader.close()
                node = Element('Error'); SubElement(node, 'Code').text = exc.code; SubElement(node, 'Message').text = exc.message
                return xml(node, exc.status, {'Content-Range': f"bytes */{info['size']}"})
            headers['Content-Length'] = str(length)
            if status == 206: headers['Content-Range'] = f"bytes {start}-{start + length - 1}/{info['size']}"
            mime = mimetypes.guess_type(key)[0] or 'application/octet-stream'
            if request.method == 'HEAD':
                reader.close(); return Response(status_code=status, headers=headers, media_type=mime)
            return StreamingResponse(reader.stream(start, length), status_code=status, headers=headers, media_type=mime, background=BackgroundTask(reader.close))
        except Exception:
            reader.close()
            raise

    return app
