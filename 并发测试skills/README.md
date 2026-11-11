# 并发测试 skills

两个可分发的 Claude Code skill（整个文件夹拷进 `~/.claude/skills/` 即生效），固化自 agentsphere-demo 的实测脚本。脚本一律从 `E2B_API_KEY` 环境变量取 key，不内嵌、不落盘。

## 两个 skill

| 目录 | 测什么 | 核心口径 |
|---|---|---|
| `agentsphere-burst-create/` | **并发拉起测试**：齐发批量创建，平台容量、批量交付、快照恢复速度（附 `snapshot_probe.py` 按模板盘点快照——快照=build，走 GET /templates/{id}） | 单请求 = 发出→返回；端到端 = 最早发出→最晚返回 |
| `agentsphere-lifecycle-ops/` | **并发生命周期操作**：pause / resume(connect) / print(ok) / restart / kill 计时 + TTL 到期销毁延迟 | serial=精准口径（队列深度=1）；**volley=同相齐发并发（每操作一发令枪，N 台同相同时执行同一操作——并发生命周期的严格口径）**；burst=独立流水对照；排队分量 ≈ 并发p50 − 串行p50 |

详细参数、坑表、新模板接入清单见各自 `SKILL.md`。

## 快速开始（agentsphere-demo venv）

```powershell
$py = "C:\Users\Administrator\agentsphere-demo\venv\Scripts\python.exe"
$env:E2B_API_KEY = "<你的key>"   # setx E2B_API_KEY <key> 可永久

# 并发拉起 50 台
& $py agentsphere-burst-create\burst_create.py --count 50

# 并发生命周期操作（同相齐发：N 台先全部到位，每操作一发令枪同时执行）
& $py agentsphere-lifecycle-ops\lifecycle_ops_bench.py --mode volley --count 20 --skip-ttl

# 生命周期计时（串行 5 台 + TTL 3 台/180s）
& $py agentsphere-lifecycle-ops\lifecycle_ops_bench.py
```

环境默认 cn-southwest-2（agentsandbox 域名），模板默认 92db8659（pilotdeck）；换区域三件套整体换，模板 ID 不跨区。

## 实测基线（2026-11-10，cn-southwest-2）

- 并发拉起：热池交付 p50 ≈ 0.86s（30 并发打不空热池），热池单台 create ≈ 0.11–0.48s
- 快照恢复（2026-11-11，模板 bb468965，引用=`模板ID:快照ID` 复合串）：1 引用×50 台 50/50 全通（p50 0.584s，吞吐 52.3 个/s）；**52 快照×各 1 台 52/52 全通（p50 0.552s，stddev 0.017s）——多快照并发交付无互相争抢**
- 快照枚举：`GET /templates/{模板ID}` 已发布（builds[] 即快照库，bb468965 下 55 build/52 ready）；`GET /snapshots`、`GET /templates` 团队级接口未发布（APIGW.0101）；裸快照 UUID 直接当引用传会 404，必须拼 `模板ID:快照ID`
- 生命周期（模板 bb468965，串行 n=5）：pause p50 0.60s、resume(connect) p50 0.64s、kill 0.09s、print_ok p50 0.15s
- 并发生命周期（volley 同相齐发 n=3 冒烟，模板 92db8659）：pause p50 0.89s、resume p50 1.13s、kill 0.10s——对比串行可见排队分量；正式并发基线待更大规模实测
- **并发生命周期两场景实测（11-11，volley 50 台，bb468965，均 50/50 全成功，墙钟 21~23s）**：场景一（1 快照×50 台）create p50 0.85s、pause 1.22s、resume(connect) 0.28s 但 resume_verify p50 3.14s/p90 8.0s；场景二（52 快照×各 1 台）create p50 0.44s、pause 0.77s、resume_verify 0.21s。**结论：对同一快照的并发还原/恢复存在单源排队（resume_verify 最长 9.25s），多快照几乎无互相干扰；场景一 print_ok 齐刷刷 ~8s = 快照台从 create 返回到命令通道可用的平台就绪时间，一致性极高（min=p50=7.95, max 8.52）**
- **TTL 到期回收滞后 ~60s，且为周期性批量清扫**（跨模板 + 三场景确认）：92db8659 单台 +60.8s；bb468965 三台 +60.1/60.2/60.3s；10 台不同快照 TTL180 全部在 +60.6~+61.6s（p50 +61.1s）同一 2s 轮询窗内判死，死亡时刻随创建顺序递减（=创建时间差）——平台周期性批量清扫而非逐台定时器。报 TTL 行为按"设 T 实际活 ~T+60s"表述

样例数据：各 skill 目录内的 `*_20261110_*.txt/.csv` 为真实运行留档（92db8659 冒烟、bb468965 冒烟 + 全量），可作为输出格式参照。
