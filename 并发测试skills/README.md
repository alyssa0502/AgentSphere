# 并发测试 skills

两个可分发的 Claude Code skill（整个文件夹拷进 `~/.claude/skills/` 即生效），固化自 agentsphere-demo 的实测脚本。脚本一律从 `E2B_API_KEY` 环境变量取 key，不内嵌、不落盘。

## 两个 skill

| 目录 | 测什么 | 核心口径 |
|---|---|---|
| `agentsphere-burst-create/` | **并发拉起测试**：同一瞬间批量创建，平台容量、批量交付、快照恢复速度（附 `snapshot_probe.py` 按模板盘点快照——快照=build，走 GET /templates/{id}） | 单请求 = 发出→返回；端到端 = 最早发出→最晚返回 |
| `agentsphere-lifecycle-ops/` | **并发生命周期操作**：create_verify / pause / resume(connect) / resume_verify / restart / update_timeout(TTL 更新，覆盖 updated 事件) / kill 计时 + TTL 到期销毁延迟 | serial=精准口径（队列深度=1）；**volley=并发（N 个 sandbox 同一瞬间执行同一操作——并发生命周期的严格口径）**；burst=独立流水对照；**每轮跑完自动产出双 sheet Excel**（`make_excel.py` 自动发现数据，Sheet1=场景一/场景二对比，Sheet2=逐台明细；`LIFECYCLE_EXCEL_OUT` 可重定向落盘位置） |

两个 verify 统一「锚点返回→真正可用」口径：探针同为 exec `echo ok`，只换锚点——**create_verify 锚点=create 返回**（拉起后多久真正可用），**resume_verify 锚点=resume(connect) 返回**（恢复后多久真正可用）；读数含失败尝试、重试间隔与被平台 hold 的等待，首条命令成功那一刻=可用时刻。（`print_ok` 为旧名，11-23 前历史 CSV 兼容，语义同 create_verify）

详细参数、坑表、新模板接入清单见各自 `SKILL.md`。

## 快速开始（agentsphere-demo venv）

```powershell
$py = "C:\Users\Administrator\agentsphere-demo\venv\Scripts\python.exe"
$env:E2B_API_KEY = "<你的key>"   # setx E2B_API_KEY <key> 可永久

# 并发拉起 50 台
& $py agentsphere-burst-create\burst_create.py --count 50

# 并发生命周期操作（N 个 sandbox 同一瞬间执行同一操作）
& $py agentsphere-lifecycle-ops\lifecycle_ops_bench.py --mode volley --count 20 --skip-ttl

# 独立流水对照（每台各自完整链路、相位错开）
& $py agentsphere-lifecycle-ops\lifecycle_ops_bench.py --mode burst --count 20 --skip-ttl

# 生命周期计时（串行 5 台 + TTL 3 台/180s）
& $py agentsphere-lifecycle-ops\lifecycle_ops_bench.py
```

环境默认 cn-southwest-2（agentsandbox 域名），模板默认 92db8659（pilotdeck）；换区域三件套整体换，模板 ID 不跨区。

## 实测基线（2026-11-23，cn-southwest-2，模板 bb468965 / template-swe-50snapshot，52 ready 快照）

- **并发拉起 50 台**：模板直拉 p50 0.585s / stddev 0.143 / 吞吐 43.8 个/s（尾部 3 台 1.0–1.1s，同源）；**50 快照各拉 1 台 p50 0.390s / stddev 0.021 / 吞吐 109.8 个/s——多快照分散全面优于同源**（p50 快 1.5×、离散 1/7、吞吐 2.5×）
- **生命周期 serial（队列深度=1）**：create_verify p50 0.16s、pause 0.58s、verify_paused 0.08s、resume(connect) 0.65s、resume_verify 0.24s、update_timeout 0.09s、kill 0.09s；restart 平台无接口（UNSUPPORTED）
- **volley 同相齐发 50 台（均 50/50 全成功）**：场景一（1 快照×50）create_verify p50 2.49s/p90 7.22/max 8.43，resume_verify p50 4.73/p90 9.19/max 10.19——**同快照楼梯还原的直接量化**（echo 被平台 hold 到逐台就绪）；场景二（50 快照各 1）create_verify p50 0.43s、resume_verify p50 0.93s、墙钟 6s——分散快照零互相拖拽
- **burst 独立流水对照（50 台/轮）**：场景一 resume_verify p50 8.69s/max 18.7s，**比 volley 更长——同源楼梯是平台还原带宽固有的串行，与全场对齐测法无关**；场景二 resume_verify p50 0.36s，比 volley 更顺——错开还原无瞬时争抢。两个口径共同证明 resume_verify/create_verify 的差距来自「还原本身」（connect 返回≠恢复完成），不是测法 artifact
- **create_verify vs resume_verify 差约 2 倍**（同探针只差锚点）：分散快照 0.43 vs 0.93s（2.2×）——「暂停挂起→继续使用」的体验按 resume_verify 口径评估
- 快照引用铁律：`模板ID:快照ID` 复合串（裸快照 UUID 404）；枚举走 `GET /templates/{模板ID}` 的 builds[]（`/snapshots`、`/templates` 团队级接口未发布）
- **TTL 到期回收滞后 ~60s，周期性批量清扫**（跨模板确认）：本轮 2 台 TTL=120s 均在到期 +61.3s 判死（+60s 清扫周期 + 巡检余量）。报 TTL 行为按「设 T 实际活 ~T+60s」表述
- 同快照并发 pause 偶发 500 NA.95600（间歇性平台缺陷）：历史 0/50、2/50，11-23 上午 3/50、下午两轮 0/50；pause 500 后该台 resume 409 卡过渡态，kill 正常

样例数据：`results/burst/` 与 `results/lifecycle/` 内按时间戳+模式命名的 `*.csv/.txt` 为 **2026-11-23 最新口径全量留档**（冒烟 + 直拉/快照 50 台 + serial + volley×2 + burst×2 + TTL），可作为输出格式参照；`agentsphere-lifecycle-ops/make_excel.py` 把 lifecycle CSV 汇成双 sheet 对照 Excel（数据源自动发现，无需改文件名）；skill 目录内只保留 SKILL.md + 脚本 + 输入快照清单。
