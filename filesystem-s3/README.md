# Filesystem S3 Gateway

将已有服务器目录以只读 S3 协议提供给 AWS CLI、boto3、rclone 等客户端。
直接读取原文件，不上传至对象存储、不复制完整文件、不建立文件内容索引。
此目录是完整独立项目，复制到其他机器即可构建；不需要 DataSeek、Redis、MongoDB、MinIO、Docker Socket 或分析沙箱。

## 运行

需要 Python 3.12+、可读取的源目录及一个可写的小型状态目录。依赖仅 FastAPI/Uvicorn；SQLite 使用 Python 标准库。

```bash
cd filesystem-s3
python -m venv .venv
. .venv/bin/activate
pip install .
export FS3_PUBLIC_URL='http://localhost:8080/s3'
export FS3_ADMIN_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')"
export FS3_ROOTS='{"research":"/srv/research"}'
export FS3_STATE_PATH='/srv/fs3-state/gateway.sqlite3'
uvicorn fs3.app:create_app --factory --host 0.0.0.0 --port 8080 --no-access-log
```

`FS3_PUBLIC_URL` 是客户端实际使用的公开 Endpoint（包括代理前缀）；它与内部监听地址不同。
如公开地址是 `https://files.example.org/storage/s3`，代理应将该前缀映射到服务的 `/s3`，保留原始 URL 编码、查询字符串及签名头。
网关根据配置的公开地址校验签名，不信任请求中的转发 Host。
必须为不同部署生成独立管理密钥。管理 API 只供可信服务器调用，不交给浏览器。

### 独立容器

```bash
docker build -t filesystem-s3:0.1.0 .
docker run -d --name filesystem-s3 \
  --read-only --cap-drop ALL --security-opt no-new-privileges \
  --mount type=bind,source=/srv/research,target=/roots/research,readonly \
  --mount type=volume,source=fs3-state,target=/state \
  -e FS3_ROOTS='{"research":"/roots/research"}' \
  -e FS3_PUBLIC_URL='http://localhost:8080/s3' \
  -e FS3_ADMIN_KEY \
  -p 127.0.0.1:8080:8080 filesystem-s3:0.1.0
```

镜像默认 UID/GID 为 10001，源目录须授予该用户读取文件及遍历目录的权限，状态目录须可写。
不要为了接入而整体放开源目录权限。挂载始终只读；挂载前不存在的目录不会由网关创建。
公开服务建议通过 HTTPS 反向代理，仅代理 `/s3`；不要将 `/admin` 直接开放至公网。
若基础镜像仓库无法访问，可通过 `--build-arg PYTHON_IMAGE=镜像地址` 替换。

## 管理 API

管理请求统一使用 `Authorization: Bearer <FS3_ADMIN_KEY>`。响应不会包含服务器绝对路径。

| 接口 | 用途 |
|---|---|
| GET `/healthz` | 健康检查 |
| GET `/admin/v1/roots` | 列出配置的目录别名 |
| PUT `/admin/v1/buckets/{bucket}` | 将已配置根目录下的相对目录注册为 Bucket |
| DELETE `/admin/v1/buckets/{bucket}` | 取消映射，同时撤销关联凭证；不删除文件 |
| POST `/admin/v1/credentials` | 签发只读临时凭证 |
| DELETE `/admin/v1/credentials/{access_key_id}` | 即时撤销凭证 |

注册目录映射：

```bash
curl -X PUT http://localhost:8080/admin/v1/buckets/research-data \
  -H "Authorization: Bearer $FS3_ADMIN_KEY" -H 'Content-Type: application/json' \
  -d '{"root":"research","directory":"climate"}'
```

`root` 必须是部署配置中的别名；`directory` 是相对目录，可为空。同名同目录注册幂等；同名不同目录返回 409，必须先删除旧映射，避免旧凭证被用于新目录。

申请目录前缀权限：

```bash
curl -X POST http://localhost:8080/admin/v1/credentials \
  -H "Authorization: Bearer $FS3_ADMIN_KEY" -H 'Content-Type: application/json' \
  -d '{"bucket":"research-data","prefix":"1953/","ttl_seconds":900}'
```

权限可以是整个 Bucket（省略 key 和 prefix）、对象前缀（prefix）或单个文件（key）。
prefix 是 S3 字符串前缀；限定目录请保留末尾 `/`。
单文件请求例如 `{"bucket":"research-data","key":"1953/rain.nc","ttl_seconds":900}`。
可信集成还可指定 `source_key`，将公开的 key 映射到 Bucket 内另一相对文件路径，仅对该单文件凭证生效，不改变源文件名。

响应包含 `endpoint`、`region`、`bucket`、`s3_uri`、`access_key_id`、`secret_access_key`、`expires_at`。
单文件还包含预签名 `download_url`、文件名和字节数。默认有效期 15 分钟，最长 24 小时。
凭证只授予当前 Bucket 的选定范围，无法枚举或访问其他 Bucket。
原文件发生变化后下次读取立即可见；S3 路径不是版本快照。

## 客户端

将上一步返回的临时凭证配置为 AWS CLI profile（不要使用管理密钥作为 S3 Secret）：

```bash
aws configure --profile research
aws configure set s3.addressing_style path --profile research
aws --profile research --endpoint-url http://localhost:8080/s3 s3 ls s3://research-data/1953/
aws --profile research --endpoint-url http://localhost:8080/s3 s3 cp s3://research-data/1953/rain.nc ./rain.nc
```

boto3：

```python
import boto3
from botocore.config import Config

s3 = boto3.client('s3', endpoint_url=credentials['endpoint'],
    region_name=credentials['region'],
    aws_access_key_id=credentials['access_key_id'],
    aws_secret_access_key=credentials['secret_access_key'],
    config=Config(signature_version='s3v4', s3={'addressing_style': 'path'}))
for page in s3.get_paginator('list_objects_v2').paginate(Bucket='research-data', Prefix='1953/'):
    print([item['Key'] for item in page.get('Contents', [])])
s3.download_file('research-data', '1953/rain.nc', 'rain.nc')
```

## 协议范围与边界

- 支持 SigV4 请求头签名及预签名 URL，Path Style。
- 支持 GetObject、HeadObject、ListObjectsV2（Prefix、Delimiter、MaxKeys、ContinuationToken、StartAfter、EncodingType）、ListBuckets、HeadBucket、GetBucketLocation。
- 支持单段 Range、If-Match、If-None-Match；支持客户端以多个 Range 请求并发/续传。
- 所有 S3 写入和删除操作拒绝；不支持对象版本、ACL、多段上传、虚拟主机式 Bucket 地址。
- ETag 使用文件身份、长度及修改时间生成，**不是文件 MD5**；不将 ETag 当作内容校验和。
- 拒绝 `..`、绝对路径、软链接、管道和设备文件；读取通过文件描述符逐级打开，避免路径检查与实际打开之间的软链接替换。
- 状态数据库只保存目录映射与临时凭证，重启保持有效期/撤销状态，不保存文件内容。数据卷与状态卷分开，保护状态卷及其备份。
- 目录列举实时扫描，不建立数据副本。单次最多扫描 `FS3_SCAN_LIMIT`（默认 100000）个条目，内存仅保留当前页及一个候选项；超限返回明确的 SlowDown 错误，不伪装成完整结果。大型目录应缩小 Prefix 或配置更细的 Bucket。
- 分页是实时目录视图，不承诺源目录并发修改下的快照一致性；流式下载期间修改源文件同样不具有快照保证。
- 网关的应用管理者被视为可信主体；需要多个相互不信任的租户时，应分别部署实例或先补充管理 API 的租户授权体系。

## 验证

```bash
uv run --extra test pytest
```

测试使用真实 boto3/botocore 签名，覆盖中文特殊字符、公开代理前缀、下载、范围读取、目录分页、授权范围、过期和撤销、重启持久化、软链接拒绝及原文件变更。
