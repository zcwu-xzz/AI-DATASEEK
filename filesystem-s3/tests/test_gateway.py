import hashlib
import os
import time
from urllib.parse import quote, urlsplit
from xml.etree import ElementTree as ET

import boto3
import pytest
from botocore.auth import S3SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.config import Config as SDKConfig
from botocore.credentials import Credentials
from fastapi.testclient import TestClient

from fs3.app import create_app
from fs3.config import Config
from fs3.errors import S3Error
from fs3.filesystem import byte_range


@pytest.fixture
def gateway(tmp_path):
    root = tmp_path / 'data'; root.mkdir()
    (root / '目录').mkdir()
    (root / '目录' / 'a +%#.txt').write_bytes(b'0123456789')
    (root / 'b.txt').write_bytes(b'other')
    (root / 'empty').touch()
    (root / 'link').symlink_to(tmp_path / 'outside')
    (tmp_path / 'outside').write_bytes(b'private')
    config = Config('https://files.test/proxy/s3', 'a' * 40, {'research':str(root)}, str(tmp_path / 'state.sqlite3'))
    app = create_app(config)
    client = TestClient(app)
    headers = {'Authorization':'Bearer ' + config.admin_key}
    assert client.put('/admin/v1/buckets/research-data',json={'root':'research'},headers=headers).status_code == 200
    def grant(**kwargs):
        response = client.post('/admin/v1/credentials',json={'bucket':'research-data',**kwargs},headers=headers)
        assert response.status_code == 200, response.text
        return response.json()
    return config, app, client, headers, root, grant


def signed(client, info, method='GET', key=None, query='', headers=None, bucket='research-data'):
    suffix = '/' + bucket if bucket is not None else ''
    if key is not None: suffix += '/' + quote(key, safe='/')
    url = info['endpoint'] + suffix + query
    request = AWSRequest(method=method, url=url, headers=headers or {})
    S3SigV4Auth(Credentials(info['access_key_id'],info['secret_access_key']), 's3',info['region']).add_auth(request)
    return client.request(method, '/s3' + suffix + query, headers=dict(request.headers))


def test_sdk_signed_unicode_proxy_ranges_and_conditionals(gateway):
    _, _, client, _, _, grant = gateway
    info = grant(key='目录/a +%#.txt')
    response = signed(client, info, key='目录/a +%#.txt')
    assert response.content == b'0123456789'
    assert response.headers['content-length'] == '10'
    head = signed(client, info, method='HEAD', key='目录/a +%#.txt')
    assert head.status_code == 200 and head.headers['content-length'] == '10'
    assert signed(client, info, key='目录/a +%#.txt', headers={'Range':'bytes=2-5'}).content == b'2345'
    assert signed(client, info, key='目录/a +%#.txt', headers={'Range':'bytes=-3'}).content == b'789'
    bad = signed(client, info, key='目录/a +%#.txt', headers={'Range':'bytes=999-1000'})
    assert bad.status_code == 416 and bad.headers['content-range'] == 'bytes */10'
    assert signed(client, info, key='目录/a +%#.txt', headers={'If-Match':'"old"'}).status_code == 412
    assert signed(client, info, key='目录/a +%#.txt', headers={'If-None-Match':response.headers['etag']}).status_code == 304


def test_our_and_boto3_presigned_urls(gateway):
    _, _, client, _, _, grant = gateway
    info = grant(key='目录/a +%#.txt')
    parsed = urlsplit(info['download_url'])
    assert client.get(parsed.path.removeprefix('/proxy')+'?'+parsed.query).content == b'0123456789'
    sdk = boto3.client('s3',endpoint_url=info['endpoint'],region_name='us-east-1',aws_access_key_id=info['access_key_id'],aws_secret_access_key=info['secret_access_key'],config=SDKConfig(signature_version='s3v4',s3={'addressing_style':'path'}))
    parsed = urlsplit(sdk.generate_presigned_url('get_object',Params={'Bucket':'research-data','Key':'目录/a +%#.txt'},ExpiresIn=60))
    assert client.get(parsed.path.removeprefix('/proxy')+'?'+parsed.query).content == b'0123456789'
    assert client.get(parsed.path.removeprefix('/proxy')+'?'+parsed.query+'&X-Amz-Expires=1').status_code == 403


def test_scope_revocation_bucket_deletion_and_restart(gateway):
    config, app, client, admin, _, grant = gateway
    info = grant(key='b.txt')
    assert signed(client, info, key='empty').status_code == 403
    assert signed(client, info, key='b.txt',bucket='another').status_code == 403
    restarted = TestClient(create_app(config))
    assert signed(restarted, info, key='b.txt').content == b'other'
    assert client.delete('/admin/v1/credentials/'+info['access_key_id'],headers=admin).status_code == 200
    assert signed(restarted, info, key='b.txt').status_code == 403
    info = grant()
    client.delete('/admin/v1/buckets/research-data',headers=admin)
    assert signed(restarted, info, key='b.txt').status_code == 403


def test_prefix_pagination_and_directory_listing(gateway):
    _, _, client, _, root, grant = gateway
    for i in range(8): (root / '目录' / f'{i}.txt').write_bytes(b'x')
    info = grant(prefix='目录/')
    assert signed(client,info,key='b.txt').status_code == 403
    token = None; found = []
    for _ in range(10):
        query = '?list-type=2&max-keys=2&encoding-type=url'
        if token: query += '&continuation-token=' + quote(token,safe='')
        response = signed(client,info,query=query)
        assert response.status_code == 200, response.text
        tree = ET.fromstring(response.content)
        found += [node.text for node in tree.findall('.//{*}Contents/{*}Key')]
        node = tree.find('{*}NextContinuationToken'); token = node.text if node is not None else None
        if token is None: break
    assert len(found) == 9 and len(set(found)) == 9
    assert found == sorted(found)
    tree = ET.fromstring(signed(client,info,query='?list-type=2&delimiter=%2F').content)
    assert tree.find('.//{*}CommonPrefixes/{*}Prefix').text == '目录/'
    assert signed(client,info,query='?list-type=2&continuation-token=bad').status_code == 400


def test_admin_boundaries_symlinks_and_no_source_paths(gateway):
    _, _, client, admin, root, grant = gateway
    assert client.get('/admin/v1/roots').status_code == 403
    assert client.get('/admin/v1/roots',headers=admin).json() == {'roots':['research']}
    for directory in ('../', '/etc', 'link'):
        assert client.put('/admin/v1/buckets/invalid-root',json={'root':'research','directory':directory},headers=admin).status_code in (400,404)
    info = grant()
    response = signed(client,info,key='link')
    assert response.status_code == 404 and str(root) not in response.text
    for method in ('PUT','POST','DELETE','PATCH'):
        assert signed(client,info,method=method,key='b.txt').status_code == 403
    assert signed(client,info,key='b.txt',query='?versionId=bad').status_code == 501
    assert signed(client,info,key='empty').content == b''
    assert signed(client,info,key='empty',headers={'Range':'bytes=0-'}).status_code == 416


def test_exact_public_key_alias(gateway):
    _, _, client, _, _, grant = gateway
    info = grant(key='public/renamed.txt',source_key='b.txt')
    assert info['size'] == 5 and '/roots' not in str(info)
    assert signed(client,info,key='public/renamed.txt').content == b'other'
    assert signed(client,info,key='b.txt').status_code == 403
    tree = ET.fromstring(signed(client,info,query='?list-type=2').content)
    assert tree.find('.//{*}Key').text == 'public/renamed.txt'


def test_source_changes_visible_without_sync(gateway):
    _, _, client, _, root, grant = gateway
    info = grant(key='b.txt')
    old = signed(client,info,key='b.txt').headers['etag']
    (root/'b.txt').write_bytes(b'updated original')
    response = signed(client,info,key='b.txt')
    assert response.content == b'updated original' and response.headers['etag'] != old


def test_expiry_and_scan_limit(gateway, monkeypatch):
    config, _, client, _, _, grant = gateway
    info = grant(ttl_seconds=1)
    monkeypatch.setattr('fs3.store.time.time',lambda:info['expires_at']+1)
    assert signed(client,info,key='b.txt').status_code == 403
    monkeypatch.undo()
    info = grant(); config.scan_limit = 1
    response = signed(client,info,query='?list-type=2')
    assert response.status_code == 503 and '<Code>SlowDown</Code>' in response.text


@pytest.mark.parametrize('header', ['bytes=10-', 'bytes=-0','bytes=2-1','bytes=1-2,4-5','other=1-2'])
def test_bad_range(header):
    with pytest.raises(S3Error): byte_range(header,10)


def test_real_boto3_download_and_paginator_over_http(gateway, tmp_path):
    """A real SDK/HTTP round trip, including download_file's HeadObject/GET flow."""
    import socket
    import threading
    import uvicorn
    config, app, _, _, _, grant = gateway
    sock = socket.socket()
    sock.bind(('127.0.0.1',0))
    port = sock.getsockname()[1]
    config.public_url = f'http://127.0.0.1:{port}/s3'
    info = grant()
    server = uvicorn.Server(uvicorn.Config(app, log_level='error', access_log=False))
    thread = threading.Thread(target=server.run, kwargs={'sockets':[sock]}, daemon=True)
    thread.start()
    try:
        for _ in range(100):
            if server.started: break
            time.sleep(.01)
        assert server.started
        sdk = boto3.client('s3',endpoint_url=config.public_url,region_name='us-east-1',
            aws_access_key_id=info['access_key_id'],aws_secret_access_key=info['secret_access_key'],
            config=SDKConfig(signature_version='s3v4',s3={'addressing_style':'path'}))
        pages = list(sdk.get_paginator('list_objects_v2').paginate(Bucket='research-data',PaginationConfig={'PageSize':1}))
        names = [obj['Key'] for page in pages for obj in page.get('Contents',[])]
        assert names == ['b.txt','empty','目录/a +%#.txt']
        output = tmp_path/'downloaded.txt'
        sdk.download_file('research-data','目录/a +%#.txt',str(output))
        assert output.read_bytes() == b'0123456789'
        assert sdk.get_object(Bucket='research-data',Key='b.txt',Range='bytes=1-3')['Body'].read() == b'the'
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        sock.close()


def test_delimiter_pages_are_unique_and_tokens_bound_to_prefix(gateway):
    _, _, client, _, root, grant = gateway
    for directory in ('one','two','three'):
        (root/directory).mkdir()
        for i in range(5): (root/directory/str(i)).write_bytes(b'x')
    info = grant()
    token = None; names = []
    for _ in range(12):
        query = '?list-type=2&delimiter=%2F&max-keys=1'
        if token: query += '&continuation-token='+quote(token,safe='')
        tree = ET.fromstring(signed(client,info,query=query).content)
        names += [n.text for n in tree.findall('.//{*}Contents/{*}Key') + tree.findall('.//{*}CommonPrefixes/{*}Prefix')]
        more = tree.find('{*}NextContinuationToken')
        if more is None: break
        token = more.text
    assert names == sorted(['b.txt','empty','one/','two/','three/','目录/'])
    assert signed(client,info,query='?list-type=2&prefix=other&continuation-token='+quote(token,safe='')).status_code == 400


def test_remapping_requires_revocation_and_link_swap_is_denied(gateway):
    _, _, client, admin, root, grant = gateway
    info = grant(key='b.txt')
    response = client.put('/admin/v1/buckets/research-data',json={'root':'research','directory':'目录'},headers=admin)
    assert response.status_code == 409
    (root/'b.txt').unlink()
    (root/'b.txt').symlink_to(root.parent/'outside')
    assert signed(client,info,key='b.txt').status_code == 404
