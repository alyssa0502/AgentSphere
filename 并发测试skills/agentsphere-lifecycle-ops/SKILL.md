---
name: agentsphere-lifecycle-ops
description: AgentSphere 沙箱生命周期管理操作计时（含并发生命周期压测）。三种模式：serial 精准统计 pause、resume(connect)、print(ok)、restart（平台无接口则标注）、update_timeout（set_timeout 更新 TTL，覆盖 updated 事件）、kill 的纯执行耗时；volley 并发模式（N 个 sandbox 先全部到位，然后同一瞬间全员执行同一操作——50 个 sandbox 并发 pause / 并发 resume / 并发 kill）= "n 个快照同时执行生命周期管理"的严格口径；burst 独立流水并发对照。另含 TTL 到期自动销毁的回收延迟观察（双确认轮询，实测平台批量清扫 +60s）。当用户要测沙箱暂停/恢复/重连/命令执行/销毁耗时、并发生命周期管理、TTL 回收速度、管理面操作延迟分位数时使用此技能。
---

# AgentSphere 生命周期管理操作计时（串行精准 / 并发 / TTL 观察）

## 客户场景

- **管理面延迟**：用户点"暂停/恢复"后多久生效（pause / resume 单操作耗时分布）
- **并发生命周期管理**：N 个用户同时暂停/恢复/销毁各自的沙箱，管理面承压与单台耗时变化（volley 模式）
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

# ★ 并发生命周期操作：N 个 sandbox 先全部 create 到位，
#   然后同一瞬间全员执行同一操作（并发 pause / 并发 resume / 并发 kill）
& $py lifecycle_ops_bench.py --mode volley --count 20 --skip-ttl

# 独立流水并发对照（N 台各跑各的完整链路，相位错开）
& $py lifecycle_ops_bench.py --mode burst --count 20

# 只测管理操作 / 只测 TTL
& $py lifecycle_ops_bench.py --skip-ttl
& $py lifecycle_ops_bench.py --skip-ops --ttl 120 --ttl-count 5

# 场景：1 个快照引用拉 N 台跑并发生命周期（循环复用同一引用）
& $py lifecycle_ops_bench.py --snapshots <templateID:buildID> --mode volley --count 20 --skip-ttl

# 场景：N 个快照同时拉 N 台并发生命周期管理（引用不足自动循环）
& $py lifecycle_ops_bench.py --snapshots <ref1>,<ref2>,<ref3> --mode volley --skip-ttl
& $py lifecycle_ops_bench.py --snapshots @refs.txt --mode volley --count 30 --skip-ttl
```

## 参数

| 参数 | 默认 | 说明 |
|---|---|---|
| `--template` | 92db8659…（env `TEMPLATE_ID`） | 被测模板（直拉） |
| `--snapshots` | 无 | 快照引用（templateID:buildID），逗号分隔或 `@refs.txt`；给了它就不看 --template；不足台数自动循环复用；管理操作和 TTL 两个阶段吃同一份引用 |
| `--count` | 5 | 管理操作阶段台数 |
| `--sla` | 2.0 | create 相达标阈值（秒），volley 拉起报告的达标率用 |
| `--mode` | serial | serial=精准口径（队列深度=1）；**volley=并发（N 个 sandbox 同一瞬间执行同一操作）**；burst=独立流水并发对照（相位错开） |
| `--ttl` | 180 | TTL 观察时长（秒，≥60） |
| `--ttl-count` | 3 | TTL 观察台数 |
| `--poll` | 2.0 | TTL 轮询周期（秒）= 销毁读数的观察粒度 |
| `--grace` | 300 | 到期后继续观察上限，超过报 not_observed |
| `--ops-timeout` | 3600 | 管理操作阶段沙箱自身 TTL（秒） |
| `--keep` | 关 | 跳过 kill（默认每台流程末尾 kill 作为主动销毁参照） |

## 输出解读

- 每台逐操作即时明细（`pause=0.42s` 一目了然），末尾按操作汇总分位数表
- volley 的 create 相输出完整拉起报告：计划拉起/SLA、成功率 + 总墙钟（发令->全部返回）、端到端 + 吞吐、create 下发时间与结束时间、逐台拉起延迟（按耗时升序）、分位数统计块（p50~p99.9/mean/stddev）+ SLA 达标率——口径与 burst_create 一致
- `print_ok` / `resume_verify`：只对成功那次调用计时；次数写在 detail（attempt2 = 首次未通）
- `restart=UNSUPPORTED`：当前 SDK/平台无 restart 接口，脚本标注而非报错——接口上线后自动开始测量
- `update_timeout`：运行中调 `set_timeout` 把 TTL 延长 60s（覆盖平台 updated 事件，与 create/pause/resume/kill 事件并列凑齐）；SDK 有此接口，平台若未支持会记 fail 而非 UNSUPPORTED
- `ttl_destroy` 读数 = 计划到期 → 观察到死亡；**含 0~poll 秒轮询粒度**，负值 = 提前回收
- `not_observed` = grace 内没死，这本身就是要上报的发现
- CSV 列：`phase,idx,sid,op,elapsed,status,off,detail`（off = 距该台流程起点的偏移，可还原时间线）

## 统计原理（为什么这样测才"精准"）

**测什么**：每个管理操作一个独立计时窗口——`perf_counter` 在 SDK 调用发出前最后一个打点、返回后第一个打点。窗口内零打印、零日志、零额外网络调用，测到的就是这一次管理操作在控制面的端到端执行时间。

**怎么排除"下发队列"耗时**（关键设计）：
1. **串行默认模式**：任一时刻整个账号只有 1 个管理操作在途。平台管理面队列深度恒为 1，不存在排队——读数即纯执行耗时。
2. **volley 并发模式**（并发生命周期操作的严格口径）：N 个 sandbox 先全部 create 到位，然后**每个操作全员同时执行**——同一瞬间 50 个 sandbox 并发 pause、并发 verify、并发 resume、并发 kill。每个操作的读数 = "该操作在并发度=N 时的单台耗时分布"，操作间互不重叠、同一相内并发度恰好是 N。与 serial 相减得排队分量（11-11 冒烟 n=3：serial pause 0.60s → volley 0.89s，resume 0.64s → 1.13s）。
3. **burst 独立流水对照**：N 台各自跑完整链路、相位错开，测的是"N 个用户各自独立操作"的真实混合负载，墙钟含平台排队。
4. **状态核实与操作分离**：pause 的耗时只含 pause 调用本身；"状态真的变成 paused 了吗"用单独的 `verify_paused`（get_info）行记录，不污染 pause 读数。
5. **print(ok) 只计成功那次**：create 返回到命令通道真正可用之间可能要重试，重试等待属于"就绪时间"不是"命令执行时间"——脚本只对最终成功的那次 echo 计时，重试次数记在 detail。
6. **TTL 销毁用双确认**：SDK 状态枚举只有 PAUSED/RUNNING，没有"已销毁"。网络抖动也会抛异常，所以连续 2 次轮询失败才判死，死亡时刻取**第一次**失败的时间戳，粒度误差 = ±一个轮询周期（--poll 可调小到 1s）。

## 坑

1. **域名必须 `agentsandbox.`**（agentsphere. 是平行后端，鉴权过但模板 404）
2. **命令要 `user="root", cwd="/root"`**：模板无普通用户账户
3. **SDK 无独立 resume()**：对 paused 沙箱 `Sandbox.connect(sid)` 即恢复，这就是 resume 的实测路径
4. **TTL 判死别用单次异常**：网络抖动同样抛异常，必须连续 2 次；也别指望 get_info 返回 DEAD 状态（枚举里没有）
5. **--ttl 最小 60**：平台下限；TTL 观察期间那几台别去动它，动了会刷新存活判定
6. **TTL 与管理操作用不同沙箱**：测 TTL 的台从头到尾只许轮询，保证"无操作干扰的自然到期"
7. **快照引用先冒烟**：引用 = `模板ID:快照ID` 复合串（平台发的裸快照 UUID 要手动拼模板前缀，裸传 404）；本网关快照管理 API（create/list）未发布，枚举快照用 burst-create skill 的 `snapshot_probe.py`。引用拉起已真机验证（11-11：1 引用×50 台、52 引用×各 1 台全通）——新引用仍建议 `--count 1 --skip-ttl` 先冒烟

## 新模板/新环境接入清单（执行前逐项确认并声明）

| 必传项 | 传法 | 不传时的默认（执行时标注【默认】） |
|---|---|---|
| 模板 ID | `--template <ID>` 或 env `TEMPLATE_ID` | 92db8659…（pilotdeck） |
| 区域三件套 | env `E2B_API_URL` / `E2B_SANDBOX_URL` / `E2B_API_KEY` | cn-southwest-2 + 用户级 E2B_API_KEY |

硬规则：模板 ID 不跨区；换区域三件套整体换；新模板先 `--count 1 --ttl-count 0 --skip-ttl` 冒烟。

## 测试人员速查

| 你要测什么 | 命令 |
|---|---|
| pause/resume/kill 纯执行耗时 | `lifecycle_ops_bench.py --skip-ttl`（serial） |
| **N 台并发生命周期管理**（并发 pause/resume/kill） | `lifecycle_ops_bench.py --mode volley --count 20 --skip-ttl` |
| N 个快照并发生命周期 | `lifecycle_ops_bench.py --snapshots @refs.txt --mode volley --count 30 --skip-ttl` |
| TTL 到期多久真回收 | `lifecycle_ops_bench.py --skip-ops --ttl 120` |
| 排队分量差分 | 同参数跑一轮 volley/burst + 一轮 serial，p50 相减 |
| 精细销毁时刻 | 加 `--poll 1`（粒度 ±1s，轮询更密） |
