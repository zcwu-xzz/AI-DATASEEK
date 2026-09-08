"""Run explicitly inside the deployed backend; prints no credentials or data.

python tests/smoke_dataset_s3.py
"""
import asyncio
import hashlib
import json
import os
import uuid
from datetime import datetime, timezone, timedelta
from urllib.parse import quote, urlsplit

import httpx
from beanie import init_beanie
from minio.credentials import Credentials
from minio.signer import sign_v4_s3

from app.application.services.dataset_s3 import issue_credentials, bucket_name
from app.application.services.dataset_s3_files import FileReader, resolve_file
from app.core.config import get_settings
from app.infrastructure.models.documents import DataCenterDatasetDocument, TemporaryDatasetDocument
from app.infrastructure.storage.mongodb import get_mongodb
from app.infrastructure.storage.redis import get_redis
from app.interfaces.schemas.dataset import dataset_response
from app.domain.models.dataset import DataCenterDataset, DatasetFile, DatasetLocation, DatasetStorageType


def sdk_headers(url, info, method="GET"):
    now = datetime.now(timezone.utc)
    digest = hashlib.sha256(b"").hexdigest()
    headers = {"Host": urlsplit(url).netloc, "X-Amz-Date": now.strftime("%Y%m%dT%H%M%SZ"), "X-Amz-Content-Sha256": digest}
    return sign_v4_s3(method=method, url=urlsplit(url), region=info["region"], headers=headers,
                     credentials=Credentials(info["access_key_id"], info["secret_access_key"]), content_sha256=digest, date=now)


async def main():
    await get_mongodb().initialize()
    await get_redis().initialize()
    db = get_mongodb().client[get_settings().mongodb_database]
    await init_beanie(database=db, document_models=[DataCenterDatasetDocument, TemporaryDatasetDocument])
    created = None
    fixture_dir = os.environ.get("DATASEEK_S3_SMOKE_DIRECTORY")
    if fixture_dir:
        dsid = "tds_s3_smoke_" + uuid.uuid4().hex
        loc = DatasetLocation(location_id="smoke", node_id="local-default", storage_type=DatasetStorageType.HOST_PATH, source_path=fixture_dir, mount_name="fixture", verified=True)
        ds = DataCenterDataset(dataset_id=dsid, data_center_id="s3-smoke", data_center_name="s3-smoke", name="S3 verification", created_by="s3-smoke", files=[DatasetFile(path="sources/smoke/fixture/sample.txt")], locations=[loc])
        created = TemporaryDatasetDocument(dataset_id=dsid, owner_id="s3-smoke", dataset=ds, expires_at=datetime.now(timezone.utc) + timedelta(minutes=15))
        await created.insert()
    candidates = [created] if created else await TemporaryDatasetDocument.find({"expires_at": {"$gt": datetime.now(timezone.utc)}}).to_list()
    if not created:
        candidates += await DataCenterDatasetDocument.find({"enabled": True}).to_list()
    selected = None
    for doc in candidates:
        dataset = doc.to_domain()
        for file in sorted(dataset_response(dataset).files, key=lambda f: f.size)[:3]:
            reader = None
            try:
                reader = FileReader(resolve_file(dataset, file.path))
                meta = await asyncio.to_thread(reader.open)
                selected = (doc, dataset, file, reader, meta)
                break
            except Exception as exc:
                print("Reader failure:", type(exc).__name__, getattr(exc, "message", str(exc)), type(exc.__context__).__name__, flush=True)
                if reader: reader.close()
        if selected: break
    if not selected:
        if created: await created.delete()
        raise RuntimeError("No readable catalog file available for smoke test")
    doc, dataset, file, reader, meta = selected
    user = getattr(doc, "owner_id", None) or dataset.created_by or "admin"
    size = min(1024, meta["size"])
    expected = b"".join(reader.stream(0, size))
    info = await issue_credentials(dataset.dataset_id, user, file.path)
    url = info["endpoint"] + "/" + bucket_name(dataset.dataset_id) + "/" + quote(file.path, safe="/")
    checks = {}
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.get(info["download_url"], headers={"Range": f"bytes=0-{size-1}"} if size else {})
            assert response.status_code == (206 if size else 200), (response.status_code, response.text[:200])
            assert response.content == expected
            checks["presigned_range_matches_source"] = True
            response = await client.head(url, headers=sdk_headers(url, info, "HEAD"))
            assert response.status_code == 200, response.status_code
            assert int(response.headers["content-length"]) == meta["size"]
            checks["sdk_head"] = True
            headers = sdk_headers(url, info)
            if size: headers["Range"] = f"bytes=0-{size-1}"
            response = await client.get(url, headers=headers)
            assert response.content == expected and response.status_code in (200, 206), response.status_code
            checks["sdk_range_matches_source"] = True
            listing = info["endpoint"] + "/" + bucket_name(dataset.dataset_id) + "?list-type=2&encoding-type=url"
            response = await client.get(listing, headers=sdk_headers(listing, info))
            assert response.status_code == 200 and "<KeyCount>1</KeyCount>" in response.text, response.text[:200]
            checks["sdk_list"] = True
            wrong = url + ".unauthorized"
            response = await client.get(wrong, headers=sdk_headers(wrong, info))
            assert response.status_code == 403
            checks["other_object_denied"] = True
            response = await client.get(url)
            assert response.status_code == 403
            checks["unsigned_denied"] = True
            response = await client.put(url, content=b"never-written")
            assert response.status_code == 403
            checks["write_denied"] = True
        print(json.dumps({"success": True, "checks": checks, "bytes_verified": size}))
    finally:
        await get_redis().client.delete("dataset-s3:" + info["access_key_id"])
        if created:
            await created.delete()
        await get_mongodb().shutdown()
        await get_redis().shutdown()


if __name__ == "__main__":
    asyncio.run(main())
