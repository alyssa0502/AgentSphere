---
name: agentsphere-burst-create
description: AgentSphere 沙箱并发拉起压测（齐射模式）。N 个沙箱同一瞬间批量创建，统计 create 时延分位数（p50/p95/p99）、成功率、吞吐、SLA 达标率与直方图；支持模板直拉和快照引用拉起（templateID:buildID 循环复用），附带快照现做/盘点/验证工具。当用户要压测沙箱并发创建、批量拉起、快照恢复并发、统计创建延迟分位数、验证平台容量/热池承压时使用此技能。
---

# AgentSphere 并发拉起压测

对应旧脚本：`create_snapshot50.py` / `create_snapshot50_new.py` / `create_snapshot_latest50.py` / `create_snapshot_latest50_stats.py`（已合并为下面两个脚本）。

## 客户场景

- **容量压测**：平台高峰期能否承受 N 个用户同时开沙箱（齐射 = 最恶劣瞬间并发）
- **批量交付**：活动/开学季一次性交付 N 个同规格环境
- **快照恢复速度**：从快照拉起的交付时延分布（快照 = 带状态镜像，比裸模板重）

## 环境准备

```powershell
# venv（本机已知可用）
$py = "C:\Users\Administrator\agentsphere-demo\venv\Scripts\python.exe"

# 三件套（key 不落盘，走环境变量；换区域改 URL 即可）
$env:E2B_API_URL    = "https://agentsandbox.cn-southwest-2.myhuaweicloud.com"
$env:E2B_SANDBOX_URL = "https://default-sandbox-gateway-mq4gcnrxgj.agentgateway.cn-southwest-2.huaweicloud-agentnetwork.com"
$env:E2B_API_KEY    = "<你的key>"     # setx E2B_API_KEY <key> 可永久
```

依赖：`pip install e2b e2b-code-interpreter`（venv 已装）。

## 快速开始

```powershell
# 0) 冒烟：3 台确认环境通
& $py burst_create.py --count 3

# 1) 快照路线：先造快照引用（也可 --list-only 盘点账号已有快照）
& $py make_snapshots.py --template 92db8659-f980-4576-80ec-c7cf9b69348f --count 5
& $py make_snapshots.py --verify @snapshots.txt     # 拉一台验证引用可用
& $py burst_create.py --snapshots @snapshots.txt --count 50 --sla 2.0

# 2) 模板直拉路线
& $py burst_create.py --template 92db8659-f980-4576-80ec-c7cf9b69348f --count 50
```

## 参数

| 参数 | 默认 | 说明 |
|---|---|---|
| `--template` | 92db8659…（env `TEMPLATE_ID`） | 模板直拉模式 |
| `--snapshots` | 无 | 快照引用，逗号分隔或 `@snapshots.txt`；不足 count 自动循环复用 |
| `--count` | 50（env `COUNT`） | 齐射数量 |
| `--sla` | 2.0 | 达标阈值（秒），统计达标率 |
| `--bin` | 0.5 | 直方图桶宽（秒） |
| `--keep` | 关 | 测完不销毁（默认统一 kill） |
| `--csv` | 时间戳文件 | 明细落盘路径 |

## 输出解读

- **成功率 / 吞吐**：齐射 N 台成功几台、每秒交付几台
- **时延分位数**（仅成功样本）：p50 = 中位交付体验，p95/p99 = 长尾，达标率 = SLA 视角
- **端到端** = 最早发出 → 最晚返回 ≈ 最慢一台
- **直方图**：双峰 = 热池台 + 冷启动台混合；右尾拖长 = 后端排队
- CSV 列：`idx, ref, ok, send_s, ret_s, latency_s, sandbox_id, error`

## 参考基线（cn-southwest-2，2026-11-05 实测）

- 平台热池机制：create 前 20s 恒定预热，**30 并发打不空热池**，交付 p50 ≈ 0.86s
- 热池单台：create API ≈ 0.11–0.48s（首台偏高属正常）
- 冷启动（新模板首发）明显更高，直方图右尾可见

## 坑

1. **域名必须是 `agentsandbox.`**：`agentsphere.` 是平行后端，鉴权能过但模板 404
2. **模板 c4bf4922 已不可建**：现行正典 92db8659（pilotdeck）、f6c0b17f（chromium watcher 版）
3. **快照引用格式**：`create_snapshot` 返回的 `snapshot_id` 就是 `templateID:buildID`，直接当 `template=` 用；不要只填模板 ID 前半段
4. **并发规模**：单进程 asyncio 实测 50 正常；要更大规模分波跑多次，先 `--count 3` 冒烟
5. **测完必清理**：默认统一 kill；`--keep` 留的台靠平台 TTL 回收，压测前确认账号配额
6. **换区域**：三件套整体换（如 cn-north-4 的 URL + 网关 + 该区域 key），模板 ID 不跨区通用

## 新模板/新环境接入清单（执行前逐项确认并声明）

| 必传项 | 传法 | 不传时的默认（执行时标注【默认】） |
|---|---|---|
| 模板 ID | `--template <ID>` 或 env `TEMPLATE_ID` | 92db8659…（pilotdeck） |
| 区域三件套 | env `E2B_API_URL` / `E2B_SANDBOX_URL` / `E2B_API_KEY` | cn-southwest-2 + 用户级 E2B_API_KEY |
| SLA 阈值 | `--sla <秒>` | 2.0 |

硬规则：
- **模板 ID 不跨区**——换区域必须连三件套整体换，只换 ID 必 404
- **新模板首发无热池**——首轮 create 是冷启动，不能拿热池基线（p50≈0.86s）评判；先 `--count 3` 冒烟看量级
- 快照路线换新模板：先用 `make_snapshots.py --template <新模板>` 造引用，再 `--verify` 验一下，然后齐射

## 测试人员速查

| 你要测什么 | 命令 |
|---|---|
| 平台瞬时并发容量 | `burst_create.py --count 50`（模板直拉） |
| 快照恢复交付速度 | `make_snapshots.py` → `burst_create.py --snapshots @snapshots.txt --count 50` |
| SLA 达标率报告 | 加 `--sla 2.0`，看"达标率"行 + CSV |
