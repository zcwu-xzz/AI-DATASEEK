import hashlib
from datetime import datetime, timezone
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from minio.credentials import Credentials
from minio.signer import sign_v4_s3
from starlette.requests import Request

from app.application.services import dataset_s3 as s3
from app.application.services.dataset_s3_files import byte_range, resolve_file
from app.domain.models.dataset import DataCenterDataset, DatasetFile, DatasetLocation, DatasetStorageType


class MemoryRedis:
    def __init__(self): self.values = {}
    async def set(self, k, v, ex): self.values[k] = v
    async def get(self, k): return self.values.get(k)


@pytest.fixture
def configured(monkeypatch):
    settings = SimpleNamespace(server_host="https://example.test:7443/prefix/", dataset_managed_volume="datasets")
    monkeypatch.setattr(s3, "get_settings", lambda: settings)
    redis = MemoryRedis()
    monkeypatch.setattr(s3, "get_redis", lambda: SimpleNamespace(client=redis))
    return settings


def request(url, method="GET", headers=None):
    parsed = urlsplit(url)
    # Reverse proxy strips the externally configured /prefix.
    from urllib.parse import unquote
    return Request({"type": "http", "method": method, "scheme": "http", "server": ("backend", 8000),
                    "path": unquote(parsed.path.removeprefix("/prefix")), "query_string": parsed.query.encode(),
                    "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]})


@pytest.mark.asyncio
async def test_presigned_and_sdk_signatures_with_proxy_prefix_and_unicode(configured):
    key = "目录/a +%名字.tif"
    info = await s3.issue_credentials("dataset1", "owner", key)
    bucket = s3.bucket_name("dataset1")
    assert info["endpoint"] == "https://example.test:7443/prefix/api/v1/s3"
    ticket = await s3.authenticate(request(info["download_url"]), bucket, key)
    assert ticket["user_id"] == "owner"
    for method in ("GET", "HEAD"):
        from urllib.parse import quote
        url = info["endpoint"] + "/" + bucket + "/" + quote(key, safe="/")
        date = datetime.now(timezone.utc)
        headers = {"Host": urlsplit(url).netloc, "X-Amz-Date": date.strftime("%Y%m%dT%H%M%SZ"), "X-Amz-Content-Sha256": hashlib.sha256(b"").hexdigest()}
        headers = sign_v4_s3(method=method, url=urlsplit(url), region="us-east-1", headers=headers,
                            credentials=Credentials(info["access_key_id"], info["secret_access_key"]),
                            content_sha256=hashlib.sha256(b"").hexdigest(), date=date)
        assert (await s3.authenticate(request(url, method, headers), bucket, key))["key"] == key


@pytest.mark.asyncio
async def test_scope_signature_expiry_and_write_denial(configured, monkeypatch):
    info = await s3.issue_credentials("dataset1", "owner", "a.txt")
    bucket = s3.bucket_name("dataset1")
    for other_bucket, other_key in (("other", "a.txt"), (bucket, "b.txt")):
        with pytest.raises(s3.S3Error):
            await s3.authenticate(request(info["download_url"]), other_bucket, other_key)
    with pytest.raises(s3.S3Error):
        await s3.authenticate(request(info["download_url"] + "&X-Amz-Expires=900"), bucket, "a.txt")
    with pytest.raises(s3.S3Error):
        await s3.authenticate(request(info["download_url"], "HEAD"), bucket, "a.txt")
    monkeypatch.setattr(s3.time, "time", lambda: info["expires_at"] + 1)
    with pytest.raises(s3.S3Error):
        await s3.authenticate(request(info["download_url"]), bucket, "a.txt")
    from app.interfaces.api.dataset_s3_routes import router
    app = FastAPI(); app.include_router(router, prefix="/api/v1")
    client = TestClient(app)
    for method in ("put", "post", "delete", "patch"):
        response = getattr(client, method)("/api/v1/s3/bucket/key")
        assert response.status_code == 403
        assert "<Code>AccessDenied</Code>" in response.text


@pytest.mark.parametrize("header,size,expected", [(None, 10, (0, 10, 200)), ("bytes=2-5", 10, (2, 4, 206)), ("bytes=-3", 10, (7, 3, 206)), ("bytes=8-", 10, (8, 2, 206)), (None, 0, (0, 0, 200))])
def test_ranges(header, size, expected):
    assert byte_range(header, size) == expected


@pytest.mark.parametrize("header", ["bytes=10-", "bytes=-0", "bytes=2-1", "bytes=1-2,4-5", "other=1-2"])
def test_bad_ranges(header):
    with pytest.raises(s3.S3Error): byte_range(header, 10)


def test_mapping_does_not_expose_or_guess_host_paths():
    dataset = DataCenterDataset(
        dataset_id="d1", data_center_id="test", data_center_name="test", name="test", files=[DatasetFile(path="sources/loc/source/dir/a.txt")],
        locations=[DatasetLocation(location_id="loc", node_id="local-default", storage_type=DatasetStorageType.HOST_PATH, source_path="/data/private/source", mount_name="source", verified=True)], metadata={}, nc_view_url=None,
    )
    from app.infrastructure.external.sandbox.node_health import LOCAL_DEFAULT_NODE_ID
    dataset.locations[0].node_id = LOCAL_DEFAULT_NODE_ID
    assert resolve_file(dataset, "dir/a.txt") == ("bind", "/data/private/source", "dir/a.txt")
    for bad in ("/data/private/source/dir/a.txt", "../a.txt", "dir/../a.txt", "dir//a.txt", "b.txt"):
        with pytest.raises(s3.S3Error): resolve_file(dataset, bad)


def test_missing_public_endpoint_fails_closed(configured):
    configured.server_host = None
    with pytest.raises(s3.S3Error): s3.endpoint()


def test_real_reader_rejects_symlinks_and_streams_exact_ranges(tmp_path):
    import json
    import subprocess
    import sys
    from app.application.services.dataset_s3_files import _READER
    (tmp_path / "file").write_bytes(b"0123456789")
    (tmp_path / "link").symlink_to(tmp_path / "file")
    script = _READER.replace("'/dataset'", repr(str(tmp_path)))
    def run(parts, *args):
        return subprocess.run([sys.executable, "-c", script, json.dumps(parts), *args], capture_output=True, timeout=5)
    meta = json.loads(run(["file"], "stat").stdout)
    assert run(["file"], "read", "2", "4", meta["version"]).stdout == b"2345"
    assert run(["link"], "stat").returncode != 0
    assert run(["..", "file"], "stat").returncode != 0
    assert run(["file"], "read", "0", "1", "stale-version").returncode != 0
