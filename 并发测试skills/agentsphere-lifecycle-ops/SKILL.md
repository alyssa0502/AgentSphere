---
name: agentsphere-lifecycle-ops
description: AgentSphere 沙箱生命周期管理操作批量计时。串行/并发两种模式精准统计 pause、resume(connect)、print(ok)、restart（平台无接口则标注）、kill 的纯执行耗时，以及 TTL 到期自动销毁的回收延迟（双确认轮询）。当用户要测沙箱暂停/恢复/重连/命令执行/销毁耗时、TTL 回收速度、管理面操作延迟分位数时使用此技能。
---

# AgentSphere 生命周期管理操作计时

## 客户场景

- **管理面延迟**：用户点"暂停/恢复"后多久生效（pause / resume 单操作耗时分布）
- **TTL 回收合规**：到期后平台多久真正回收资源（计费/配额视角的"到点即停"验证）
- **长会话保活成本**：resume(connect) 的恢复速度决定"暂停挂起→继续使用"的体验
- **销毁速度**：kill 的主动回收延迟（也是所有压测的清理成本参照）

## 环境准备

```powershell
$py = "C:\Users\Administrator\agentsphere-demo\venv\Scripts\python.exe"

# 三件套（key 不落盘；换区域整体换）
$env:E2B_API_URL    = "https://agentsandbox.cn-southwest-2.myhuaweicloud.com"
$env:E2B_SANDBOX_URL = "https://default-sandbox-gateway-mq4gcnrxgj.agentgateway.cn-southwest-2.huaweicloud-agentnetwork.com"
$env:E2B_API_KEY    = "<你的key>"     # setx E2B_API_KEY <key> 可永久
```

## 快速开始

```powershell
# 冒烟：1 台管理操作 + 1 台 TTL/90s（约 4 分钟出全量结果）
& $py lifecycle_ops_bench.py --count 1 --ttl-count 1 --ttl 90

# 标准测试：串行 5 台 + TTL 3 台/180s
& $py lifecycle_ops_bench.py

# 并发对照（含平台排队，用于和串行相减得排队分量）
& $py lifecycle_ops_bench.py --mode burst --count 20

# 只测管理操作 / 只测 TTL
& $py lifecycle_ops_bench.py --skip-ttl
& $py lifecycle_ops_bench.py --skip-ops --ttl 120 --ttl-count 5
```

## 参数

| 参数 | 默认 | 说明 |
|---|---|---|
| `--template` | 92db8659…（env `TEMPLATE_ID`） | 被测模板 |
| `--count` | 5 | 管理操作阶段台数 |
| `--mode` | serial | serial=精准口径（队列深度=1）；burst=并发对照 |
| `--ttl` | 180 | TTL 观察时长（秒，≥60） |
| `--ttl-count` | 3 | TTL 观察台数 |
| `--poll` | 2.0 | TTL 轮询周期（秒）= 销毁读数的观察粒度 |
| `--grace` | 300 | 到期后继续观察上限，超过报 not_observed |
| `--ops-timeout` | 3600 | 管理操作阶段沙箱自身 TTL（秒） |
| `--keep` | 关 | 跳过 kill（默认每台流程末尾 kill 作为主动销毁参照） |

## 输出解读

- 每台逐操作即时明细（`pause=0.42s` 一目了然），末尾按操作汇总分位数表
- `print_ok` / `resume_verify`：只对成功那次调用计时；次数写在 detail（attempt2 = 首次未通）
- `restart=UNSUPPORTED`：当前 SDK/平台无 restart 接口，脚本标注而非报错——接口上线后自动开始测量
- `ttl_destroy` 读数 = 计划到期 → 观察到死亡；**含 0~poll 秒轮询粒度**，负值 = 提前回收
- `not_observed` = grace 内没死，这本身就是要上报的发现
- CSV 列：`phase,idx,sid,op,elapsed,status,off,detail`（off = 距该台流程起点的偏移，可还原时间线）

## 统计原理（为什么这样测才"精准"）

**测什么**：每个管理操作一个独立计时窗口——`perf_counter` 在 SDK 调用发出前最后一个打点、返回后第一个打点。窗口内零打印、零日志、零额外网络调用，测到的就是这一次管理操作在控制面的端到端执行时间。

**怎么排除"下发队列"耗时**（关键设计）：
1. **串行默认模式**：任一时刻整个账号只有 1 个管理操作在途。平台管理面队列深度恒为 1，不存在排队——读数即纯执行耗时。
2. **burst 对照模式**：发令枪齐射 N 台，墙钟里混入了平台排队。单跑一轮 burst、再单跑一轮 serial，`排队分量 ≈ 并发p50 − 串行p50`。两个口径分开出数，不混报。
3. **状态核实与操作分离**：pause 的耗时只含 pause 调用本身；"状态真的变成 paused 了吗"用单独的 `verify_paused`（get_info）行记录，不污染 pause 读数。
4. **print(ok) 只计成功那次**：create 返回到命令通道真正可用之间可能要重试，重试等待属于"就绪时间"不是"命令执行时间"——脚本只对最终成功的那次 echo 计时，重试次数记在 detail。
5. **TTL 销毁用双确认**：SDK 状态枚举只有 PAUSED/RUNNING，没有"已销毁"。网络抖动也会抛异常，所以连续 2 次轮询失败才判死，死亡时刻取**第一次**失败的时间戳，粒度误差 = ±一个轮询周期（--poll 可调小到 1s）。

## 坑

1. **域名必须 `agentsandbox.`**（agentsphere. 是平行后端，鉴权过但模板 404）
2. **命令要 `user="root", cwd="/root"`**：模板无普通用户账户
3. **SDK 无独立 resume()**：对 paused 沙箱 `Sandbox.connect(sid)` 即恢复，这就是 resume 的实测路径
4. **TTL 判死别用单次异常**：网络抖动同样抛异常，必须连续 2 次；也别指望 get_info 返回 DEAD 状态（枚举里没有）
5. **--ttl 最小 60**：平台下限；TTL 观察期间那几台别去动它，动了会刷新存活判定
6. **TTL 与管理操作用不同沙箱**：测 TTL 的台从头到尾只许轮询，保证"无操作干扰的自然到期"

## 新模板/新环境接入清单（执行前逐项确认并声明）

| 必传项 | 传法 | 不传时的默认（执行时标注【默认】） |
|---|---|---|
| 模板 ID | `--template <ID>` 或 env `TEMPLATE_ID` | 92db8659…（pilotdeck） |
| 区域三件套 | env `E2B_API_URL` / `E2B_SANDBOX_URL` / `E2B_API_KEY` | cn-southwest-2 + 用户级 E2B_API_KEY |

硬规则：模板 ID 不跨区；换区域三件套整体换；新模板先 `--count 1 --ttl-count 0 --skip-ttl` 冒烟。

## 测试人员速查

| 你要测什么 | 命令 |
|---|---|
| pause/resume/kill 纯执行耗时 | `lifecycle_ops_bench.py --skip-ttl` |
| TTL 到期多久真回收 | `lifecycle_ops_bench.py --skip-ops --ttl 120` |
| 管理面并发承压（含排队） | `--mode burst --count 20`，再跑 serial 相减 |
| 精细销毁时刻 | 加 `--poll 1`（粒度 ±1s，轮询更密） |
