# 并发测试 skills

两个可分发的 Claude Code skill（整个文件夹拷进 `~/.claude/skills/` 即生效），固化自 agentsphere-demo 的实测脚本。脚本一律从 `E2B_API_KEY` 环境变量取 key，不内嵌、不落盘。

## 两个 skill

| 目录 | 测什么 | 核心口径 |
|---|---|---|
| `agentsphere-burst-create/` | 齐射并发拉起：平台容量、批量交付、快照恢复速度（含 `make_snapshots.py` 快照现做/盘点/验证） | 单请求 = 发出→返回；端到端 = 最早发出→最晚返回 |
| `agentsphere-lifecycle-ops/` | 生命周期管理操作计时：pause / resume(connect) / print(ok) / restart / kill + TTL 到期销毁延迟 | serial=精准口径（队列深度=1，纯执行耗时不含下发排队）；burst=齐射对照，排队分量 ≈ 并发p50 − 串行p50 |

详细参数、坑表、新模板接入清单见各自 `SKILL.md`。

## 快速开始（agentsphere-demo venv）

```powershell
$py = "C:\Users\Administrator\agentsphere-demo\venv\Scripts\python.exe"
$env:E2B_API_KEY = "<你的key>"   # setx E2B_API_KEY <key> 可永久

# 并发拉起 50 台
& $py agentsphere-burst-create\burst_create.py --count 50

# 生命周期计时（串行 5 台 + TTL 3 台/180s）
& $py agentsphere-lifecycle-ops\lifecycle_ops_bench.py
```

环境默认 cn-southwest-2（agentsandbox 域名），模板默认 92db8659（pilotdeck）；换区域三件套整体换，模板 ID 不跨区。

## 实测基线（2026-11-10，cn-southwest-2）

- 并发拉起：热池交付 p50 ≈ 0.86s（30 并发打不空热池），热池单台 create ≈ 0.11–0.48s
- 生命周期（模板 bb468965，串行 n=5）：pause p50 0.60s、resume(connect) p50 0.64s、kill 0.09s、print_ok p50 0.15s
- **TTL 到期回收滞后 ~60s（跨模板确认）**：92db8659 单台 +60.8s；bb468965 三台 +60.1/60.2/60.3s——平台疑似固定加 60s 宽限，报 TTL 行为时按"设 T 实际活 ~T+60s"表述

样例数据：各 skill 目录内的 `*_20261110_*.txt/.csv` 为真实运行留档（92db8659 冒烟、bb468965 冒烟 + 全量），可作为输出格式参照。
