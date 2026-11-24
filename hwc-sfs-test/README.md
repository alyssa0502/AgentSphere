# hwc-sfs-test

华为云 SFS Turbo（高性能弹性文件服务）管理面 API 直连工具集。AK/SK 签名用官方
`huaweicloudsdkcore` 的通用 Signer（不需要服务专属 SDK 包——`huaweicloudsdksfs`
在 PyPI 上不存在），密钥只从环境变量读，不落盘。

## 前置

```bash
pip install huaweicloudsdkcore

# 用户级环境变量（Windows: setx / Linux: export）
HWC_AK=<AccessKey>
HWC_SK=<SecretKey>
```

## 脚本

| 脚本 | 用途 |
|---|---|
| `create_haier_dirs.py` | 批量创建目录（幂等，409 已存在=跳过） |
| `probe_dirs.py` | 按名字规律探测目录是否存在（ShowFsDir） |
| `discover2.py` | 盘点某区域的全部 SFS Turbo（名称/share_id/容量/VPC） |
| `show_share.py` | 查单个盘的详情（挂载点/VPC/子网/安全组） |
| `e2e_fsdir.py` | 单目录 建→重复建验 409→删 全流程自测 |
| `list_ecs.py` | 列区域全部 ECS 及所在 VPC（排查挂载网络问题时用） |

换盘/换区：改脚本头部的 `REGION` 和 `TARGET_NAME` 两个常量，share_id 自动查。

## 常用命令

```bash
# 批量创建：<前缀><起>..<前缀><止>，如 /deeptest/aaa/bbb/ccc/haier1..haier10001
python -I create_haier_dirs.py /deeptest/aaa/bbb/ccc/haier 1 10001

# 批量探测（限流内自动 2.6s 节流）
python -I probe_dirs.py /haier/test 1 100
python -I probe_dirs.py /haier/test5     # 单个
```

## 踩坑速查

- **列表 API 是 `GET /v1/{pid}/sfs-turbo/shares/detail`**：不带 `/detail` 会 404
- **嵌套路径一步建**：CreateFsDir 对 `/a/b/c` 会自动递归建父目录；重复建返回
  `409 SFS.TURBO.0114 path already exist`
- **删除是异步**：DeleteFsDir 返回 202（任务受理），不是立即删完
- **ShowFsDir 限流 25 次/分钟**（429）：批量探测必须节流；CreateFsDir 配额宽松得多
- **mode 传数字**（如 755），uid/gid 默认 0；STANDARD 型盘的 ShowFsDir 响应不含
  mode/uid/gid（仅 20MB/s/TiB 及以上规格返回）
- **手写 SDK-HMAC-SHA256 签名会被 IAM 401**（canonical 尾斜杠行为与服务端回显
  不一致），务必用官方 `huaweicloudsdkcore` 的 `Signer`；`SdkRequest` 的
  `query_params`/`header_params` 必须显式传（`None` 会炸），带 body 时签名与
  发送要用同一份 bytes
- **挂载网络**：SFS Turbo 只能 VPC 内挂载，`export_location` 的内网 IP/域名跨
  VPC 不可达（同网段≠同 VPC）；安全组放通救不了二层隔离；VPC 网段重叠时
  "添加 VPC"和对等连接都不可行
