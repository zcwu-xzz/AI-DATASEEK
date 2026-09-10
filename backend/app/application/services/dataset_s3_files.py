"""Translate a dataset's public file key; file I/O belongs to filesystem-s3."""
from pathlib import PurePosixPath

from app.application.services.dataset_s3 import S3Error
from app.core.config import get_settings
from app.domain.models.dataset import DatasetStorageType
from app.infrastructure.external.sandbox.node_health import LOCAL_DEFAULT_NODE_ID
from app.interfaces.schemas.dataset import dataset_response


def resolve_file(dataset, key):
    if not key or key.startswith('/') or '\\' in key or any(p in {'', '.', '..'} for p in key.split('/')) or any(ord(c) < 32 for c in key):
        raise S3Error('InvalidArgument', 'Invalid object key', 400)
    matches = []
    for item in dataset.files:
        public = dataset_response(dataset.model_copy(update={'files': [item]})).files
        if public and public[0].path == key:
            matches.append(item)
    if len(matches) != 1:
        raise S3Error('NoSuchKey', 'File is missing or its relative path is ambiguous', 404)
    item = matches[0]
    for loc in dataset.locations:
        if not loc.verified or loc.node_id != LOCAL_DEFAULT_NODE_ID:
            continue
        if loc.storage_type == DatasetStorageType.MANAGED_UPLOAD:
            return ('volume', get_settings().dataset_managed_volume, f'{dataset.dataset_id}/{item.path}')
        prefix = f'sources/{loc.location_id}/'
        if item.path.startswith(prefix):
            relative = item.path[len(prefix):]
            name = loc.mount_name or PurePosixPath(loc.source_path).name
            if relative.startswith(name + '/'):
                relative = relative[len(name) + 1:]
            return ('bind', loc.source_path, relative)
    raise S3Error('ServiceUnavailable', 'Dataset is not available on the local download node', 503)
