---
name: agentsphere-burst-create
description: AgentSphere 沙箱并发拉起压测（齐射模式）。N 个沙箱同一瞬间批量创建，统计 create 时延分位数（p50/p95/p99）、成功率、吞吐、SLA 达标率与直方图；支持模板直拉和快照引用拉起（templateID:buildID 循环复用），附带快照盘点工具（按模板枚举全部快照 ID）。当用户要压测沙箱并发创建、批量拉起、快照恢复并发、统计创建延迟分位数、验证平台容量/热池承压时使用此技能。
---

# AgentSphere 并发拉起压测

主脚本 `burst_create.py`（并发拉起）+ 辅助工具 `snapshot_probe.py`（按模板盘点快照、生成引用文件）。

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

# 0.5) 盘点模板下有多少快照（快照=模板的 build），列出全部快照 ID 并生成引用文件
& $py snapshot_probe.py --template <模板ID>          # --all-builds 连 error 的也列出

# 1) 快照路线：用盘点出的引用齐射（引用=模板ID:快照ID 复合串，见"坑"3/7）
& $py burst_create.py --snapshots @snapshots_found.txt --count 52 --sla 2.0
& $py burst_create.py --snapshots <模板ID>:<快照ID> --count 50 --sla 2.0   # 1 个快照拉 50 台

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
3. **快照引用格式**：要拼成 `模板ID:快照ID` 复合串再传（如 `bb468965-…:40ad37f4-…`）——SDK `create_snapshot` 返回的 snapshot_id 天然就是这个格式；**平台侧发的裸快照 UUID 直接传会 404 template not found**（11-11 实测：裸 ID 3/3 失败，复合格式 50 台全通）
4. **并发规模**：单进程 asyncio 实测 50 正常；要更大规模分波跑多次，先 `--count 3` 冒烟
5. **测完必清理**：默认统一 kill；`--keep` 留的台靠平台 TTL 回收，压测前确认账号配额
6. **换区域**：三件套整体换（如 cn-north-4 的 URL + 网关 + 该区域 key），模板 ID 不跨区通用
7. **快照管理 API 现状（11-11 实测）**：SDK 的 `list_snapshots`/`create_snapshot`（GET/POST /snapshots…）在本网关**未发布**——现做/盘点团队级快照不可用；但 **`GET /templates/{模板ID}` 已发布**，响应体 `builds[]` 就是该模板全部快照（buildID+status+规格），`snapshot_probe.py` 用它盘点并把 `模板ID:buildID` 引用写到 `snapshots_found.txt`。**引用拉起已真机验证**：1 引用×50 台 50/50（p50 0.584s）、52 引用×各 1 台 52/52（p50 0.552s，stddev 仅 0.017s）
8. **沙箱清单接口已发布（11-11 探明）**：`GET /v2/sandboxes` 必须**带必填参数 `?limit=N`**（不带则 400 `AgentSphere.0208` Limit required），返回运行中沙箱清单（sandboxID/templateID/规格/state/endAt/onTimeout）——盘点 `--keep` 残留台、配额审计用。三接口鉴权头统一 `X-API-KEY`；POST /sandboxes payload 用 camelCase `{"templateID":…,"timeout":…}`

## 新模板/新环境接入清单（执行前逐项确认并声明）

| 必传项 | 传法 | 不传时的默认（执行时标注【默认】） |
|---|---|---|
| 模板 ID | `--template <ID>` 或 env `TEMPLATE_ID` | 92db8659…（pilotdeck） |
| 区域三件套 | env `E2B_API_URL` / `E2B_SANDBOX_URL` / `E2B_API_KEY` | cn-southwest-2 + 用户级 E2B_API_KEY |
| SLA 阈值 | `--sla <秒>` | 2.0 |

硬规则：
- **模板 ID 不跨区**——换区域必须连三件套整体换，只换 ID 必 404
- **新模板首发无热池**——首轮 create 是冷启动，不能拿热池基线（p50≈0.86s）评判；先 `--count 3` 冒烟看量级
- 快照路线换新模板：先用 `snapshot_probe.py --template <新模板>` 盘点快照拿到引用（现做快照的 API 未发布，快照 ID 由平台侧提供），再齐射

## 测试人员速查

| 你要测什么 | 命令 |
|---|---|
| 模板下有多少快照、ID 是什么 | `snapshot_probe.py --template <ID>`（产出 snapshots_found.txt） |
| 平台瞬时并发容量 | `burst_create.py --count 50`（模板直拉） |
| 快照恢复交付速度 | `burst_create.py --snapshots @snapshots_found.txt --count 50` |
| 1 个快照拉多台 / N 个快照齐射 | `burst_create.py --snapshots 模板ID:快照ID --count 50` / `--snapshots 模板ID:快照ID1,模板ID:快照ID2 --count 50`（引用必须复合格式，见"坑"3；不足自动循环复用） |
| SLA 达标率报告 | 加 `--sla 2.0`，看"达标率"行 + CSV |
