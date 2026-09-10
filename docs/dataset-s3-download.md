# DataSeek 接入独立文件系统 S3 网关

S3 协议能力已移至独立项目 [`filesystem-s3`](../filesystem-s3/README.md)。DataSeek 不再处理 S3 签名、文件流、分页或临时凭证存储，不再为下载启动沙箱容器。

DataSeek 保留 `/api/v1/datasets/{id}/files/s3-download` 作为集成接口：检查数据集与公开文件路径，将已经验证的源位置映射为网关目录别名，再通过管理 API 签发 15 分钟单文件凭证。返回字段保持不变，下载按钮按当前产品要求继续隐藏。

## 配置

现有 `.env` 增加以下配置（不要将管理密钥提交到代码库）：

```dotenv
S3_GATEWAY_URL=http://filesystem-s3:8080
S3_GATEWAY_ADMIN_KEY=填写至少32字符的随机密钥
S3_GATEWAY_HOST_DIRECTORY=/data
S3_GATEWAY_ROOT_MAPPINGS=[{"host_path":"/data","root_id":"local"}]
S3_GATEWAY_MANAGED_ROOT=managed
```

`S3_GATEWAY_HOST_DIRECTORY` 是要只读挂载的宿主机目录；必须存在并属于 `DATASET_HOST_PATH_ALLOWLIST`。
`S3_GATEWAY_ROOT_MAPPINGS` 把 DataSeek 已验证的宿主机目录映射到网关的 root 别名。
默认 Compose 同时只读挂载 DataSeek 托管数据卷为 `managed`。独立网关不认识 DataSeek 的数据集 ID、用户系统或宿主机路径表示，目录别名是双方的契约。

同一栈默认公开 Endpoint 根据 `.env` 的 `SERVER_HOST` 生成：`{SERVER_HOST}/api/v1/s3`，没有固定域名。
如使用外部独立部署的网关，设置 `S3_GATEWAY_URL` 为其管理地址，网关自身设置 `FS3_PUBLIC_URL`；还可通过 `S3_GATEWAY_PUBLIC_URL` 覆盖同栈公开 Endpoint。
Nginx 将旧 `/api/v1/s3` 入口直接代理到独立网关 `/s3`，保留中文文件名的编码和签名查询参数；不会经过 Backend 或 SSO 中间件。
管理 API 不通过 DataSeek 的 Nginx 公开。

## 启用

仍然只使用项目现有 `docker-compose.yml` 和 `./run.sh`：

```bash
./run.sh --profile s3 build filesystem-s3 backend frontend
./run.sh --profile s3 up -d --no-deps filesystem-s3 backend frontend
./run.sh --profile s3 ps
```

网关是可选的 `s3` profile，不占用新增的前端端口。未启用或未配置管理密钥时，S3 准备接口明确返回不可用，不退回旧的内置实现。
镜像中的服务使用 UID/GID 10001，需授予其源目录只读/遍历权限。不要通过挂载整个宿主机或 Docker Socket 来解决目录权限问题。
如需多组宿主机目录，在同一 Compose 服务显式添加只读挂载，并扩充 `FS3_ROOTS` 和 DataSeek root mappings。

## 迁移注意

旧版 Redis 中签发的 S3 凭证不会迁移到独立网关，切换后需要重新申请；旧凭证本身也只有 15 分钟有效期。
网关在签发和读取时验证实际文件，但不再回调 DataSeek 查询数据集状态。数据集撤销访问时，应调用管理 API 删除对应 Bucket 或撤销凭证；未主动撤销的临时凭证在有效期内仍然有效。
源目录保持只读，文件不搬迁；新增状态卷 `ai-dataseek-s3-gateway-state` 仅存映射和临时授权。
