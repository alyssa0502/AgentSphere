# AgentSphere × E2B SDK 兼容性测试（2026-11-24）

用官方 e2b Python SDK（AgentSphere 定制包，版本号 2.42.0/含新面）直连 cn-southwest-2（API `agentsandbox.…`，模板 92db8659/pilotdeck），两轮 46 个断言。

## 结果速览

| 分类 | 数量 |
|---|---|
| ✅ 兼容 | 22（生命周期管理面 + 命令执行面高度兼容；connect TTL 只延长 / lifecycle 回显 / kill 即时 404 三处旧差异平台已修复） |
| ⚠️ 可用但语义不一致 | 6（Sandbox.list 必填 limit、get_host 拼默认 e2b.app 域、默认用户缺失、files.list 空表不报错、get_info.name=None、PTY 回环未验证） |
| ❌ 不兼容 / 未实现 | 15（文件系统全域 6 条=模板无 user 账户；Volume/Template 元数据/tags/alias/snapshot/fork/metrics/update_network=端点未发布；预签名直传网关 400；auto_resume 无增量行为） |
| ⏳ SDK 未覆盖 | 2（Secret CRUD、sandbox.logs） |

## 文件

- `SDK兼容性测试报告-20261124.md` — 完整报告（三清单明细、横向缺陷、平台近期改进、P0~P2 差距清单）
- `sdk_compat_test.py` — 第一轮全域矩阵（凭据运行时从 `create_snapshot_latest50_stats.py` 提取，不落明文）
- `sdk_compat_round2.py` — 第二轮五项复核（list 带 limit / PTY 读法 / 预签名 URL 真伪 / update_network TypedDict 形态）
- `sdk_compat_result.json` / `sdk_compat_round2.json` — 原始断言数据

## 跑法

```powershell
cd C:\Users\Administrator\agentsphere-demo
.\venv\Scripts\python.exe -u sdk_compat_test.py     # 全域矩阵 ~4min
.\venv\Scripts\python.exe -u sdk_compat_round2.py   # 复核 ~1min
```

依赖：venv 内 e2b SDK + agentsphere-e2b-patch（traffic-token 注入）；三件套环境变量同 stats 脚本。
