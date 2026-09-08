# 数据集 S3 只读下载

左侧数据集文件行的下载按钮打开 S3 下载弹窗。弹窗展示 S3 URI、Endpoint、文件大小和有效期，同时提供浏览器下载以及 S3 客户端临时凭证。地址由 `SERVER_HOST` 生成，保留其路径前缀，不使用请求 Host 或固定域名。

Endpoint 为 `{SERVER_HOST}/api/v1/s3`。外部反向代理须将其转发到 DataSeek 的同名 API 路由；如果 SERVER_HOST 包含代理前缀，代理移除该前缀即可。无需新增端口或对象存储服务。

## 数据与凭证

数据留在原目录；读取时通过现有 Docker 节点将目录只读挂载，以固定大小的数据块返回，不上传到对象存储、不生成完整文件副本。路径仍须通过 `DATASET_HOST_PATH_ALLOWLIST` 和宿主机真实路径校验，读取时拒绝软链接、非普通文件与路径越界。当前支持本地默认执行节点和托管数据卷；其他节点明确返回不可用。

每次准备下载签发有效期为 15 分钟的临时 Access Key/Secret Key，Redis 保存访问范围和过期时间。权限只覆盖选中的文件；即使同一数据集中的其他文件也不能通过该凭证读取。每次读取重新检查数据集是否可用。凭证仅保存在弹窗内存，不写入浏览器持久化存储。关闭弹窗不会撤销已经复制的凭证，凭证到期后失效。

S3 客户端配置 Path Style、`us-east-1` 区域、自定义 Endpoint 和弹窗中的凭证。以 AWS CLI 为例，使用 `aws configure --profile dataseek` 配置临时凭证，再执行弹窗提供的命令。凭证过期后重新生成并更新 profile。

## 支持边界

- AWS Signature Version 4 请求头签名及查询参数预签名。
- GetObject、HeadObject、单段 HTTP Range、If-Match、If-None-Match。
- ListBuckets、HeadBucket、GetBucketLocation、ListObjectsV2；只列出当前凭证授权的文件和父前缀。
- 所有写入、删除操作均拒绝；对象版本、多段上传、对象 ACL 等不支持，返回 S3 XML 错误。
- ETag 是文件身份、长度及修改时间组成的版本标记，不是 MD5 校验和。
- 网关下载占用宿主机磁盘读取和网络带宽。只读辅助容器在下载结束时删除，并设有最长运行时间；不启动分析 Agent。

已有成果物对象存储配置与此功能独立；该下载流程不调用它。
