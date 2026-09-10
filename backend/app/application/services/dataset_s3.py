"""DataSeek adapter for the standalone filesystem S3 gateway management API."""
import hashlib
import json
from pathlib import PurePosixPath
from urllib.parse import urlsplit

import httpx

from app.core.config import get_settings
from app.infrastructure.external.sandbox.dataset_mount_validator import docker_host_source_and_candidates


class S3Error(Exception):
    def __init__(self, code='ServiceUnavailable', message='S3 gateway is unavailable', status=503):
        self.code, self.message, self.status = code, message, status


def gateway_mapping(target):
    """Map verified dataset sources to explicit gateway root aliases, never browser paths."""
    settings = get_settings()
    kind, source, relative = target
    if kind == 'volume':
        if source != settings.dataset_managed_volume:
            raise S3Error(message='Managed dataset volume is not configured for S3')
        return settings.s3_gateway_managed_root, '', relative
    if kind != 'bind': raise S3Error(message='Unsupported dataset source')
    candidate, allowed = docker_host_source_and_candidates(source, settings.dataset_host_path_allowlist, settings.dataset_docker_host_root)
    if not allowed: raise S3Error(message='Dataset source is outside the configured allowlist', status=403)
    try:
        mappings = json.loads(settings.s3_gateway_root_mappings)
        candidates = []
        for mapping in mappings:
            root_path, root_allowed = docker_host_source_and_candidates(mapping['host_path'], settings.dataset_host_path_allowlist, settings.dataset_docker_host_root)
            if not root_allowed: continue
            path = PurePosixPath(candidate)
            base = PurePosixPath(root_path)
            if path == base or base in path.parents:
                suffix = str(path.relative_to(base))
                candidates.append((len(base.parts), mapping['root_id'], '' if suffix == '.' else suffix))
        if not candidates: raise ValueError()
        _, root_id, directory = max(candidates)
        return root_id, directory, relative
    except (ValueError, TypeError, KeyError):
        raise S3Error(message='No S3 gateway root mapping is configured for this dataset') from None


async def issue_credentials(dataset_id: str, user_id: str, key: str, target):
    settings = get_settings()
    internal = settings.s3_gateway_url.rstrip('/')
    url = urlsplit(internal)
    if url.scheme not in {'http', 'https'} or not url.netloc or not settings.s3_gateway_admin_key:
        raise S3Error(message='Independent S3 gateway is not configured')
    root_id, directory, source_key = gateway_mapping(target)
    identity = json.dumps([dataset_id, root_id, directory], ensure_ascii=True)
    bucket = 'ds-' + hashlib.sha256(identity.encode()).hexdigest()[:32]
    headers = {'Authorization': 'Bearer ' + settings.s3_gateway_admin_key}
    try:
        async with httpx.AsyncClient(base_url=internal + '/', headers=headers, timeout=30, follow_redirects=False) as client:
            registration = await client.put('admin/v1/buckets/' + bucket, json={'root': root_id, 'directory': directory})
            registration.raise_for_status()
            response = await client.post('admin/v1/credentials', json={'bucket': bucket, 'key': key, 'source_key': source_key, 'ttl_seconds': 900})
            response.raise_for_status()
            result = response.json()
        fields = {'s3_uri', 'endpoint', 'region', 'access_key_id', 'secret_access_key', 'expires_at', 'download_url', 'filename', 'size', 'relative_path'}
        return {name: result[name] for name in fields}
    except (httpx.HTTPError, ValueError, KeyError):
        raise S3Error(message='The independent S3 gateway could not prepare this file') from None
