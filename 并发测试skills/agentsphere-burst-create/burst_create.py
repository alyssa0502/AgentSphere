# -*- coding: utf-8 -*-
"""
AgentSphere 并发拉起压测（齐射模式）
════════════════════════════════════════════════════════════════
N 个沙箱同一瞬间齐射创建，统计单请求时延分布 / 成功率 / 吞吐 / 达标率。

拉起来源两种：
  1) 模板直拉   --template <模板ID>
  2) 快照引用   --snapshots id1,id2,... 或 --snapshots @snapshots.txt
     引用格式 = create_snapshot 返回的 snapshot_id（templateID:buildID）。
     引用数量 < COUNT 时循环复用（同一快照拉多台）。

用法（用 agentsphere-demo venv 的 python）：
  python burst_create.py --template 92db8659-f980-4576-80ec-c7cf9b69348f --count 50
  python burst_create.py --snapshots @snapshots.txt --count 50 --sla 2.0
  python burst_create.py --template <tpl> --count 3        # 冒烟

环境三件套（环境变量可覆盖，默认 cn-southwest-2 活跃区）：
  E2B_API_URL / E2B_SANDBOX_URL / E2B_API_KEY（必填，不落盘）

口径（与 create_snapshot50_new.py 一致）：
  - 单请求耗时 = 协程内 create 发出 -> 返回（perf_counter 打点）
  - 端到端 = max(所有返回) - min(所有发出)，约等于最慢请求
  - 失败请求不计入时延分布，单独打印明细
"""
import argparse
import asyncio
import csv
import math
import os
import statistics
import sys
import time

os.environ.setdefault("E2B_API_URL",
                      "https://agentsandbox.cn-southwest-2.myhuaweicloud.com")
os.environ.setdefault("E2B_SANDBOX_URL",
                      "https://default-sandbox-gateway-mq4gcnrxgj.agentgateway"
                      ".cn-southwest-2.huaweicloud-agentnetwork.com")

from e2b_code_interpreter import AsyncSandbox  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

PERCENTILES = [50, 75, 90, 95, 99, 99.9]
BASE = os.path.dirname(os.path.abspath(__file__))


def percentile(sorted_data, p):
    """线性插值分位数（与 numpy.percentile 默认一致），入参需已升序。"""
    k = (len(sorted_data) - 1) * (p / 100.0)
    f = math.floor(k)
    c = min(f + 1, len(sorted_data) - 1)
    if f == c:
        return sorted_data[f]
    return sorted_data[f] + (sorted_data[c] - sorted_data[f]) * (k - f)


def print_stats(data, sla):
    n = len(data)
    print(f"\n==================== 时延统计（n={n}，仅成功样本） ====================")
    print(f"  {'min':<8}{data[0]:.3f}s")
    for p in PERCENTILES:
        print(f"  {'p' + f'{p:g}':<8}{percentile(data, p):.3f}s")
    print(f"  {'max':<8}{data[-1]:.3f}s")
    mean = statistics.mean(data)
    std = statistics.stdev(data) if n >= 2 else 0.0
    print(f"  {'mean':<8}{mean:.3f}s   stddev {std:.3f}s")
    met = sum(1 for x in data if x <= sla)
    print(f"  达标率(<={sla:g}s): {met}/{n} ({met / n * 100:.1f}%)")


def print_histogram(data, bin_s):
    lo = math.floor(data[0] / bin_s) * bin_s
    nbins = int(math.ceil((data[-1] - lo) / bin_s)) + 1
    counts = [0] * nbins
    for x in data:
        counts[min(int((x - lo) / bin_s), nbins - 1)] += 1
    peak = max(counts)
    print(f"\n-------------------- 直方图（桶宽 {bin_s:g}s） --------------------")
    for i, c in enumerate(counts):
        bar = "#" * max(1, round(c / peak * 40)) if c else ""
        print(f"  [{lo + i * bin_s:5.1f}, {lo + (i + 1) * bin_s:5.1f})  {c:>3}  {bar}")


def load_refs(args):
    count = args.count
    if args.snapshots:
        spec = args.snapshots
        if spec.startswith("@"):
            with open(spec[1:], encoding="utf-8") as f:
                ids = [l.strip() for l in f if l.strip() and not l.strip().startswith("#")]
        else:
            ids = [s.strip() for s in spec.split(",") if s.strip()]
        if not ids:
            raise SystemExit("快照引用列表为空")
        refs = [ids[i % len(ids)] for i in range(count)]
        print(f"来源: 快照引用 x{len(ids)}（循环复用至 {count} 台）")
    else:
        refs = [args.template] * count
        print(f"来源: 模板 {args.template}")
    print(f"计划拉起 {len(refs)} 个沙箱，SLA {args.sla:g}s")
    return refs


async def create_one(gun, ref, timeout):
    await gun.wait()
    send = time.perf_counter()
    try:
        sbx = await AsyncSandbox.create(
            template=ref,
            api_key=os.environ["E2B_API_KEY"],
            api_url=os.environ["E2B_API_URL"],
            sandbox_url=os.environ["E2B_SANDBOX_URL"],
            timeout=timeout,
        )
    except Exception as e:
        ret = time.perf_counter()
        return {"ok": False, "ref": ref, "send": send, "ret": ret,
                "sid": "", "lat": ret - send, "err": repr(e), "sbx": None}
    ret = time.perf_counter()
    return {"ok": True, "ref": ref, "send": send, "ret": ret,
            "sid": sbx.sandbox_id, "lat": ret - send, "err": "", "sbx": sbx}


async def kill_one(sbx):
    try:
        await sbx.kill()
    except Exception as e:
        print(f"kill {sbx.sandbox_id} 失败: {e}")


async def main():
    if not os.environ.get("E2B_API_KEY"):
        raise SystemExit("缺少 E2B_API_KEY 环境变量。"
                         "PowerShell 临时设置:  $env:E2B_API_KEY='<你的key>'；"
                         "永久:  setx E2B_API_KEY <你的key>（重开终端生效）")

    ap = argparse.ArgumentParser(description="AgentSphere 并发拉起压测")
    ap.add_argument("--template", default=os.environ.get(
        "TEMPLATE_ID", "92db8659-f980-4576-80ec-c7cf9b69348f"))
    ap.add_argument("--snapshots", default="",
                    help="快照引用列表，逗号分隔，或 @snapshots.txt 文件")
    ap.add_argument("--count", type=int, default=int(os.environ.get("COUNT", "50")))
    ap.add_argument("--sla", type=float, default=2.0, help="达标阈值（秒）")
    ap.add_argument("--bin", type=float, default=0.5, help="直方图桶宽（秒）")
    ap.add_argument("--timeout", type=int, default=60,
                    help="沙箱 TTL 存活时长（秒，到期平台自动回收；"
                         "--keep 也只能活这么久，要留活就调大）")
    ap.add_argument("--keep", action="store_true", help="测完保留沙箱不销毁")
    ap.add_argument("--csv", default="", help="CSV 明细路径（默认时间戳文件）")
    args = ap.parse_args()

    refs = load_refs(args)
    ts = time.strftime("%Y%m%d_%H%M%S")
    csv_path = args.csv or os.path.join(BASE, f"burst_create_{ts}.csv")

    gun = asyncio.Event()
    tasks = [asyncio.create_task(create_one(gun, r, args.timeout)) for r in refs]
    await asyncio.sleep(0)          # 让每个协程先就位

    t0 = time.perf_counter()
    gun.set()                       # 发令枪
    rows = await asyncio.gather(*tasks)
    t_total = time.perf_counter() - t0

    ok = [r for r in rows if r["ok"]]
    bad = [r for r in rows if not r["ok"]]
    print(f"\n成功 {len(ok)}/{len(rows)}（成功率 {len(ok) / len(rows) * 100:.1f}%），"
          f"总墙钟（发令->全部返回）{t_total:.3f}s")
    if ok:
        e2e = max(r["ret"] for r in ok) - min(r["send"] for r in ok)
        print(f"端到端（最早发出 -> 最晚返回）: {e2e:.3f}s，"
              f"吞吐 {len(ok) / t_total:.2f} 个/s")
    for r in bad:
        print(f"创建失败 [{r['ref'][:24]}]: {r['err'][:160]}")

    for r in sorted(ok, key=lambda x: x["lat"]):
        print(f"sandbox {r['sid']} [{r['ref'][:24]}] 拉起延迟: {r['lat']:.3f}s")

    lats = sorted(r["lat"] for r in ok)
    if lats:
        print_stats(lats, args.sla)
        print_histogram(lats, args.bin)

    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["idx", "ref", "ok", "send_s", "ret_s", "latency_s",
                    "sandbox_id", "error"])
        for i, r in enumerate(rows):
            w.writerow([i, r["ref"], r["ok"], round(r["send"] - t0, 3),
                        round(r["ret"] - t0, 3), round(r["lat"], 3),
                        r["sid"], r["err"][:120]])
    print(f"\n明细 -> {csv_path}")

    if not args.keep:
        live = [r["sbx"] for r in ok if r["sbx"] is not None]
        print(f"统一销毁 {len(live)} 台...")
        await asyncio.gather(*[kill_one(s) for s in live])
    else:
        print(f"保留 {len(ok)} 台存活（--keep，注意平台 TTL 会自动回收）")


if __name__ == "__main__":
    asyncio.run(main())
