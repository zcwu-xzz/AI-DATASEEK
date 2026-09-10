import base64
import hashlib
import hmac
import json
import secrets
import time
from datetime import datetime, timezone
from urllib.parse import quote, unquote, urlencode, urlsplit

from .errors import S3Error


def signature(method, path, query, headers, signed_headers, payload, secret, scope, stamp):
    encoded = sorted((quote(k, safe='-_.~'), quote(v, safe='-_.~')) for k, v in query)
    canonical_query = '&'.join(f'{k}={v}' for k, v in encoded)
    canonical_headers = ''.join(f"{name}:{' '.join(headers[name].split())}\n" for name in signed_headers.split(';'))
    canonical = '\n'.join((method, quote(path, safe='/-_.~'), canonical_query, canonical_headers, signed_headers, payload))
    to_sign = '\n'.join(('AWS4-HMAC-SHA256', stamp, scope, hashlib.sha256(canonical.encode()).hexdigest()))
    date, region, _, _ = scope.split('/')
    key = ('AWS4' + secret).encode()
    for part in (date, region, 's3', 'aws4_request'):
        key = hmac.new(key, part.encode(), hashlib.sha256).digest()
    return hmac.new(key, to_sign.encode(), hashlib.sha256).hexdigest()


def issue(config, store, bucket, key, prefix, source_key, ttl):
    access = 'FS' + secrets.token_hex(12).upper()
    secret = secrets.token_urlsafe(32)
    expires = int(time.time()) + ttl
    store.grant(access, dict(bucket=bucket, key=key, prefix=prefix, source_key=source_key, secret=secret, expires=expires))
    result = dict(endpoint=config.public_url, region=config.region, access_key_id=access,
                  secret_access_key=secret, expires_at=expires, bucket=bucket,
                  s3_uri=f's3://{bucket}/' + (key if key is not None else prefix))
    if key is not None:
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        scope = f'{stamp[:8]}/{config.region}/s3/aws4_request'
        base = urlsplit(config.public_url)
        query = [('X-Amz-Algorithm', 'AWS4-HMAC-SHA256'), ('X-Amz-Credential', access + '/' + scope),
                 ('X-Amz-Date', stamp), ('X-Amz-Expires', str(ttl)), ('X-Amz-SignedHeaders', 'host')]
        sig = signature('GET', unquote(base.path) + '/' + bucket + '/' + key, query, {'host': base.netloc},
                        'host', 'UNSIGNED-PAYLOAD', secret, scope, stamp)
        result['download_url'] = config.public_url + '/' + bucket + '/' + quote(key, safe='/-_.~') + '?' + urlencode(query) + '&X-Amz-Signature=' + sig
    return result


def authenticate(config, store, request, bucket, key):
    try:
        pairs = list(request.query_params.multi_items())
        if len(pairs) != len(dict(pairs)):
            raise ValueError()
        q = dict(pairs)
        presigned = 'X-Amz-Algorithm' in q
        if presigned:
            if q['X-Amz-Algorithm'] != 'AWS4-HMAC-SHA256': raise ValueError()
            credential, signed, supplied, stamp = q['X-Amz-Credential'], q['X-Amz-SignedHeaders'], q['X-Amz-Signature'], q['X-Amz-Date']
            pairs = [(k, v) for k, v in pairs if k != 'X-Amz-Signature']
            duration = int(q['X-Amz-Expires'])
            if not 0 < duration <= config.max_ttl: raise ValueError()
        else:
            auth = request.headers.get('authorization', '')
            if not auth.startswith('AWS4-HMAC-SHA256 '): raise ValueError()
            fields = dict(part.strip().split('=', 1) for part in auth.split(' ', 1)[1].split(','))
            credential, signed, supplied = fields['Credential'], fields['SignedHeaders'], fields['Signature']
            stamp, duration = request.headers['x-amz-date'], 300
        access, scope = credential.split('/', 1)
        date, region, service, terminator = scope.split('/')
        if service != 's3' or terminator != 'aws4_request' or date != stamp[:8] or region != config.region: raise ValueError()
        names = signed.split(';')
        if 'host' not in names or names != sorted(set(names)) or any(n != n.lower() for n in names): raise ValueError()
        if not presigned and 'x-amz-date' not in names: raise ValueError()
        then = datetime.strptime(stamp, '%Y%m%dT%H%M%SZ').replace(tzinfo=timezone.utc).timestamp()
        if then > time.time() + 60 or time.time() > then + duration: raise ValueError()
        record = store.credential(access)
        if bucket is not None and bucket != record['bucket']: raise ValueError()
        if key is not None:
            if record['key'] is not None and key != record['key']: raise ValueError()
            if record['key'] is None and not key.startswith(record['prefix']): raise ValueError()
        base = urlsplit(config.public_url)
        # ASGI path is already decoded. URL.path would reinterpret a literal '#'
        # in an object key as a URL fragment and invalidate a correct signature.
        suffix = request.scope['path'].removeprefix('/s3')
        headers = dict(request.headers)
        headers['host'] = base.netloc
        payload = 'UNSIGNED-PAYLOAD' if presigned else headers.get('x-amz-content-sha256', hashlib.sha256(b'').hexdigest())
        expected = signature(request.method, unquote(base.path) + suffix, pairs, headers, signed, payload, record['secret'], scope, stamp)
        if not hmac.compare_digest(supplied, expected): raise ValueError()
        store.bucket(record['bucket'])
        return record
    except (ValueError, KeyError, TypeError, OverflowError):
        raise S3Error() from None


def page_token(config, parameters, last):
    raw = json.dumps([parameters, last], ensure_ascii=True, separators=(',', ':')).encode()
    mac = hmac.new(config.admin_key.encode(), raw, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(mac + raw).decode()


def decode_token(config, token, parameters):
    try:
        if len(token) > 16000: raise ValueError()
        raw = base64.urlsafe_b64decode(token)
        mac, payload = raw[:32], raw[32:]
        if not hmac.compare_digest(mac, hmac.new(config.admin_key.encode(), payload, hashlib.sha256).digest()): raise ValueError()
        saved, last = json.loads(payload)
        if saved != parameters or not isinstance(last, str): raise ValueError()
        return last
    except (ValueError, TypeError, UnicodeError):
        raise S3Error('InvalidArgument', 'Invalid continuation token', 400) from None
