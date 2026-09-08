"""Read-only S3 signing and file addressing; no object copies are created."""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from datetime import datetime, timezone
from urllib.parse import quote, urlsplit, urlencode

from fastapi import Request

from app.core.config import get_settings
from app.infrastructure.storage.redis import get_redis

TTL = 900
REGION = "us-east-1"


class S3Error(Exception):
    def __init__(self, code="AccessDenied", message="Access denied", status=403):
        self.code, self.message, self.status = code, message, status


def endpoint() -> str:
    base = (get_settings().server_host or "").rstrip("/")
    parsed = urlsplit(base)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.query or parsed.fragment or parsed.username:
        raise S3Error("InvalidConfiguration", "SERVER_HOST must be an HTTP(S) public base URL", 503)
    return base + "/api/v1/s3"


def bucket_name(dataset_id: str) -> str:
    return "ds-" + hashlib.sha256(dataset_id.encode()).hexdigest()[:32]


def signing_key(secret: str, date: str, region: str) -> bytes:
    key = ("AWS4" + secret).encode()
    for part in (date, region, "s3", "aws4_request"):
        key = hmac.new(key, part.encode(), hashlib.sha256).digest()
    return key


def signature(method, path, query, headers, signed_headers, payload, secret, scope, stamp):
    canonical_query = "&".join(f"{quote(k, safe='-_.~')}={quote(v, safe='-_.~')}" for k, v in sorted(query))
    canonical_headers = "".join(f"{name}:{' '.join(headers[name].split())}\n" for name in signed_headers.split(";"))
    canonical = "\n".join((method, quote(path, safe="/-_.~"), canonical_query, canonical_headers, signed_headers, payload))
    to_sign = "\n".join(("AWS4-HMAC-SHA256", stamp, scope, hashlib.sha256(canonical.encode()).hexdigest()))
    date, region, _, _ = scope.split("/")
    return hmac.new(signing_key(secret, date, region), to_sign.encode(), hashlib.sha256).hexdigest()


async def issue_credentials(dataset_id: str, user_id: str, key: str):
    base = endpoint()
    access = "DS" + secrets.token_hex(12).upper()
    secret = secrets.token_urlsafe(32)
    expires = int(time.time()) + TTL
    bucket = bucket_name(dataset_id)
    # Credentials are scoped to the selected file, not every dataset of a user.
    record = dict(dataset_id=dataset_id, user_id=user_id, key=key, secret=secret, expires=expires, bucket=bucket)
    await get_redis().client.set("dataset-s3:" + access, json.dumps(record), ex=TTL)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    scope = f"{stamp[:8]}/{REGION}/s3/aws4_request"
    url = base + "/" + bucket + "/" + quote(key, safe="/-_.~")
    query = [("X-Amz-Algorithm", "AWS4-HMAC-SHA256"), ("X-Amz-Credential", access + "/" + scope),
             ("X-Amz-Date", stamp), ("X-Amz-Expires", str(TTL)), ("X-Amz-SignedHeaders", "host")]
    sig = signature("GET", urlsplit(base).path + "/" + bucket + "/" + key, query,
                    {"host": urlsplit(base).netloc}, "host", "UNSIGNED-PAYLOAD", secret, scope, stamp)
    return dict(s3_uri=f"s3://{bucket}/{key}", endpoint=base, region=REGION, access_key_id=access,
                secret_access_key=secret, expires_at=expires, download_url=url + "?" + urlencode(query) + "&X-Amz-Signature=" + sig)


async def authenticate(request: Request, bucket: str | None, key: str | None):
    try:
        pairs = list(request.query_params.multi_items())
        if len(pairs) != len(dict(pairs)):
            raise ValueError("duplicate query")
        q = dict(pairs)
        presigned = "X-Amz-Algorithm" in q
        if presigned:
            if q["X-Amz-Algorithm"] != "AWS4-HMAC-SHA256":
                raise ValueError("algorithm")
            credential, signed, supplied, stamp = q["X-Amz-Credential"], q["X-Amz-SignedHeaders"], q["X-Amz-Signature"], q["X-Amz-Date"]
            pairs = [(k, v) for k, v in pairs if k != "X-Amz-Signature"]
            duration = int(q["X-Amz-Expires"])
            if not 0 < duration <= TTL:
                raise ValueError("expiry")
        else:
            auth = request.headers.get("authorization", "")
            if not auth.startswith("AWS4-HMAC-SHA256 "):
                raise ValueError("algorithm")
            fields = dict(part.strip().split("=", 1) for part in auth.split(" ", 1)[1].split(","))
            credential, signed, supplied = fields["Credential"], fields["SignedHeaders"], fields["Signature"]
            stamp, duration = request.headers["x-amz-date"], 300
        access, scope = credential.split("/", 1)
        date, region, service, terminator = scope.split("/")
        if service != "s3" or terminator != "aws4_request" or date != stamp[:8]:
            raise ValueError("scope")
        names = signed.split(";")
        if "host" not in names or names != sorted(set(names)) or any(n != n.lower() for n in names):
            raise ValueError("headers")
        then = datetime.strptime(stamp, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc).timestamp()
        if then > time.time() + 60 or time.time() > then + duration:
            raise ValueError("expired")
        raw = await get_redis().client.get("dataset-s3:" + access)
        if not raw:
            raise ValueError("credentials")
        record = json.loads(raw)
        if record["expires"] <= time.time() or (bucket is not None and bucket != record["bucket"]) or (key is not None and key != record["key"]):
            raise ValueError("scope")
        base = urlsplit(endpoint())
        # Use the configured PUBLIC path and host so proxy prefix stripping does
        # not break signatures. Never trust forwarded host headers for signing.
        suffix = request.url.path.split("/api/v1/s3", 1)[1]
        path = base.path + suffix
        headers = dict(request.headers)
        headers["host"] = base.netloc
        payload = "UNSIGNED-PAYLOAD" if presigned else headers.get("x-amz-content-sha256", hashlib.sha256(b"").hexdigest())
        expected = signature(request.method, path, pairs, headers, signed, payload, record["secret"], scope, stamp)
        if not hmac.compare_digest(supplied, expected):
            raise ValueError("signature")
        return record
    except (ValueError, KeyError, TypeError):
        raise S3Error() from None
