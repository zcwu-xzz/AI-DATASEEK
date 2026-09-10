import json
from types import SimpleNamespace

import httpx
import pytest

from app.application.services import dataset_s3 as s3
from app.application.services.dataset_s3_files import resolve_file
from app.domain.models.dataset import DataCenterDataset, DatasetFile, DatasetLocation, DatasetStorageType


@pytest.fixture
def settings(monkeypatch):
    config = SimpleNamespace(s3_gateway_url='http://gateway:8080', s3_gateway_admin_key='test-admin',
        s3_gateway_managed_root='managed', dataset_managed_volume='datasets', dataset_host_path_allowlist='/data,/mnt',
        dataset_docker_host_root='', s3_gateway_root_mappings=json.dumps([{'host_path':'/data','root_id':'science'}]))
    monkeypatch.setattr(s3, 'get_settings', lambda: config)
    return config


def test_gateway_root_mapping_and_allowlist(settings):
    assert s3.gateway_mapping(('bind','/data/project','a.txt')) == ('science','project','a.txt')
    assert s3.gateway_mapping(('volume','datasets','id/a.txt')) == ('managed','','id/a.txt')
    for source in ['/etc', '/data/../etc', '/mnt/not-mapped', '/database']:
        with pytest.raises(s3.S3Error): s3.gateway_mapping(('bind',source,'a.txt'))


def test_snap_host_paths(settings):
    settings.dataset_docker_host_root = '/var/lib/snapd/hostfs'
    assert s3.gateway_mapping(('bind','/var/lib/snapd/hostfs/data/project','a.txt')) == ('science','project','a.txt')


@pytest.mark.asyncio
async def test_adapter_only_sends_mapping_and_returns_public_contract(settings, monkeypatch):
    calls = []
    public = dict(s3_uri='s3://bucket/dir/a.txt',endpoint='https://files.test/s3',region='us-east-1',access_key_id='test',
                  secret_access_key='secret',expires_at=123,download_url='https://files.test/s3/signed',filename='a.txt',size=5,relative_path='dir/a.txt')
    def handle(request):
        calls.append(json.loads(request.content))
        assert request.headers['authorization'] == 'Bearer test-admin'
        return httpx.Response(200, json={**public, 'internal_path':'/must-not-leak'} if request.method == 'POST' else {})
    original = httpx.AsyncClient
    monkeypatch.setattr(s3.httpx, 'AsyncClient', lambda **kw: original(transport=httpx.MockTransport(handle), **kw))
    result = await s3.issue_credentials('dataset','user','dir/a.txt',('bind','/data/project','a.txt'))
    assert result == public
    assert calls[0] == {'root':'science','directory':'project'}
    assert calls[1]['key'] == 'dir/a.txt' and calls[1]['source_key'] == 'a.txt'
    assert '/data' not in json.dumps(calls)


@pytest.mark.asyncio
async def test_gateway_errors_do_not_leak_paths_or_secrets(settings, monkeypatch):
    original = httpx.AsyncClient
    monkeypatch.setattr(s3.httpx, 'AsyncClient', lambda **kw: original(transport=httpx.MockTransport(lambda r: httpx.Response(500,text='/private/path secret')), **kw))
    with pytest.raises(s3.S3Error) as err:
        await s3.issue_credentials('d','u','a',('bind','/data','a'))
    assert '/private' not in err.value.message


def test_mapping_does_not_expose_or_guess_host_paths():
    from app.infrastructure.external.sandbox.node_health import LOCAL_DEFAULT_NODE_ID
    dataset = DataCenterDataset(dataset_id='d1', data_center_id='test', data_center_name='test', name='test',
        files=[DatasetFile(path='sources/loc/source/dir/a.txt')], locations=[DatasetLocation(location_id='loc',
        node_id=LOCAL_DEFAULT_NODE_ID, storage_type=DatasetStorageType.HOST_PATH, source_path='/data/private/source',
        mount_name='source', verified=True)], metadata={}, nc_view_url=None)
    assert resolve_file(dataset, 'dir/a.txt') == ('bind','/data/private/source','dir/a.txt')
    for bad in ['/data/private/source/dir/a.txt','../a.txt','dir/../a.txt','dir//a.txt','b.txt']:
        with pytest.raises(s3.S3Error): resolve_file(dataset, bad)


def test_backend_no_longer_hosts_s3_protocol():
    from app.interfaces.api.dataset_s3_routes import router
    assert [r.path for r in router.routes] == ['/datasets/{dataset_id}/files/s3-download']
