# -*- coding: utf-8 -*-
"""
AgentSphere 沙箱生命周期管理操作批量计时
════════════════════════════════════════════════════════════════
精准统计每台沙箱的管理操作耗时（默认串行口径 = 纯执行耗时，不含下发队列）：

  create → print(ok) → pause → 状态核实(paused) → resume(connect)
        → print(ok)核实 → restart(平台无此接口则标 UNSUPPORTED) → kill(主动销毁参照)

外加 TTL 自动销毁观察：M 台 timeout=T 的沙箱，双确认轮询测
「计划到期 → 观察到死亡」的回收延迟（含 ±一个轮询周期的观察粒度）。

两种模式：
  serial（默认，精准口径）任一时刻只有 1 个管理操作在途 → 服务端队列深度=1，无排队污染
  burst （对照口径）      N 台齐跑 → 墙钟含平台排队；排队分量 ≈ 并发p50 − 串行p50

计时口径（全部 perf_counter 单调时钟）：
  - 每个操作：调用发出前最后一个打点 → 返回后第一个打点，窗口内零打印零日志零额外网络调用
  - print(ok)：只对成功那次调用计时；重试等待单列（*_verify 列的 off 起点可见）
  - 状态核实（get_info==paused）单列 verify_paused，不混入 pause 耗时

拉起来源：--template 模板直拉，或 --snapshots 快照引用（templateID:buildID，逗号分隔或 @refs.txt，
不足台数自动循环复用）。三种场景：
  1 个快照拉 N 台          python lifecycle_ops_bench.py --snapshots <ref> --count N
  N 个快照同时拉 N 台      python lifecycle_ops_bench.py --snapshots a,b,c --count N --mode burst
  N 个快照并发生命周期     python lifecycle_ops_bench.py --snapshots a,b,c --mode burst --skip-ttl

用法（agentsphere-demo venv）：
  python lifecycle_ops_bench.py                                    # 串行 5 台 + TTL 3台/180s
  python lifecycle_ops_bench.py --count 1 --ttl-count 1 --ttl 90   # 冒烟
  python lifecycle_ops_bench.py --mode burst --count 20            # 并发对照（看排队分量）
  python lifecycle_ops_bench.py --skip-ttl                         # 只测管理操作

输出：屏幕明细 + 每操作分位数统计 + lifecycle_ops_<时间戳>.txt/.csv
"""
import argparse
import csv
import os
import statistics as st
import sys
import threading
import time

os.environ.setdefault("E2B_API_URL",
                      "https://agentsandbox.cn-southwest-2.myhuaweicloud.com")
os.environ.setdefault("E2B_SANDBOX_URL",
                      "https://default-sandbox-gateway-mq4gcnrxgj.agentgateway"
                      ".cn-southwest-2.huaweicloud-agentnetwork.com")

from e2b import Sandbox, SandboxState  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE = os.path.dirname(os.path.abspath(__file__))
TS = time.strftime("%Y%m%d_%H%M%S")

OPS_ORDER = ["create", "print_ok", "pause", "verify_paused", "resume",
             "resume_verify", "restart", "restart_verify", "kill"]


def percentile(vals, p):
    vals = sorted(vals)
    k = (len(vals) - 1) * p / 100.0
    f = int(k)
    c = min(f + 1, len(vals) - 1)
    return vals[f] if f == c else vals[f] + (vals[c] - vals[f]) * (k - f)


def R(phase, idx, sid, op, elapsed, status="ok", off=0.0, detail=""):
    return {"phase": phase, "idx": idx, "sid": sid, "op": op,
            "elapsed": round(elapsed, 3), "status": status,
            "off": round(off, 3), "detail": detail}


def load_refs(spec, count, fallback):
    """快照引用/模板 → 长度 count 的引用列表（循环复用）。返回 (refs, 去重源数)。"""
    if not spec:
        return [fallback] * count, 1
    if spec.startswith("@"):
        with open(spec[1:], encoding="utf-8") as f:
            ids = [l.strip() for l in f if l.strip() and not l.strip().startswith("#")]
    else:
        ids = [s.strip() for s in spec.split(",") if s.strip()]
    if not ids:
        raise SystemExit("快照引用列表为空")
    return [ids[i % len(ids)] for i in range(count)], len(ids)


class Report:
    def __init__(self, txt_path):
        self.txt_path = txt_path
        self._txt = open(txt_path, "w", encoding="utf-8")
        self.lock = threading.Lock()

    def emit(self, msg=""):
        with self.lock:
            print(msg, flush=True)
            self._txt.write(msg + "\n")
            self._txt.flush()

    def close(self):
        self._txt.close()


def echo_timed(sb, label, idx, sid, t0, rows, ready_timeout):
    """echo ok 直到成功；只对成功那次调用计时。返回 (成功时刻, 成功调用耗时)。"""
    t_start = time.perf_counter()
    attempt = 0
    while True:
        attempt += 1
        a0 = time.perf_counter()
        try:
            r = sb.commands.run("echo ok", user="root", cwd="/root", timeout=15)
            ok = (r.stdout or "").strip() == "ok"
        except Exception:
            ok = False
        a1 = time.perf_counter()
        if ok:
            rows.append(R("ops", idx, sid, label, a1 - a0, "ok", a1 - t0,
                          f"attempt{attempt}"))
            return a1, a1 - a0
        if time.perf_counter() - t_start > ready_timeout:
            rows.append(R("ops", idx, sid, label, -1, "timeout", a1 - t0,
                          f"{attempt}次尝试未通"))
            return None, None
        time.sleep(0.5)


def run_ops_one(idx, ref, args, rep):
    """单台全流程；逐操作即时打印。返回 rows。"""
    rows = []
    t0 = time.perf_counter()

    def off():
        return time.perf_counter() - t0

    a0 = time.perf_counter()
    try:
        sb = Sandbox.create(template=ref, timeout=args.ops_timeout)
    except Exception as e:
        rows.append(R("ops", idx, "-", "create", -1, f"fail({type(e).__name__})",
                      off(), f"ref={ref}"))
        rep.emit(f"[{idx + 1}] ref={ref} create 失败: {str(e)[:120]}")
        return rows
    a1 = time.perf_counter()
    sid = sb.sandbox_id
    rows.append(R("ops", idx, sid, "create", a1 - a0, "ok", off(), f"ref={ref}"))
    rep.emit(f"[{idx + 1}] {sid[:8]} ref={ref[:28]} create={a1 - a0:.2f}s")

    t_ok, ok_s = echo_timed(sb, "print_ok", idx, sid, t0, rows, args.ready_timeout)
    if ok_s is not None:
        rep.emit(f"    print_ok={ok_s:.3f}s (通道就绪累计 {t_ok - t0:.2f}s)")

    a0 = time.perf_counter()
    sb.pause()
    a1 = time.perf_counter()
    rows.append(R("ops", idx, sid, "pause", a1 - a0, "ok", off()))
    rep.emit(f"    pause={a1 - a0:.2f}s")

    v0 = time.perf_counter()
    try:
        info = sb.get_info()
        state_ok = info.state == SandboxState.PAUSED
        v1 = time.perf_counter()
        rows.append(R("ops", idx, sid, "verify_paused", v1 - v0,
                      "ok" if state_ok else "state_wrong", off(),
                      f"state={info.state}"))
        rep.emit(f"    verify_paused={v1 - v0:.2f}s state={info.state}")
    except Exception as e:
        v1 = time.perf_counter()
        rows.append(R("ops", idx, sid, "verify_paused", v1 - v0,
                      f"fail({type(e).__name__})", off()))
        rep.emit(f"    verify_paused 失败: {str(e)[:80]}")

    c0 = time.perf_counter()
    sb2 = Sandbox.connect(sid)
    c1 = time.perf_counter()
    rows.append(R("ops", idx, sid, "resume", c1 - c0, "ok", off(),
                  "connect(paused)"))
    rep.emit(f"    resume(connect)={c1 - c0:.2f}s")

    _, ok_s = echo_timed(sb2, "resume_verify", idx, sid, t0, rows, args.ready_timeout)
    if ok_s is not None:
        rep.emit(f"    resume_verify={ok_s:.3f}s")

    fn = getattr(sb2, "restart", None)
    if fn is None:
        rows.append(R("ops", idx, sid, "restart", -1, "unsupported", off(),
                      "SDK/平台无 restart 接口"))
        rep.emit("    restart=UNSUPPORTED（平台未提供）")
    else:
        a0 = time.perf_counter()
        fn()
        a1 = time.perf_counter()
        rows.append(R("ops", idx, sid, "restart", a1 - a0, "ok", off()))
        rep.emit(f"    restart={a1 - a0:.2f}s")
        _, ok_s = echo_timed(sb2, "restart_verify", idx, sid, t0, rows,
                             args.ready_timeout)
        if ok_s is not None:
            rep.emit(f"    restart_verify={ok_s:.3f}s")

    if args.keep:
        rows.append(R("ops", idx, sid, "kill", -1, "skipped(--keep)", off()))
        rep.emit("    kill=skipped(--keep)")
    else:
        k0 = time.perf_counter()
        try:
            sb.kill()
            k1 = time.perf_counter()
            rows.append(R("ops", idx, sid, "kill", k1 - k0, "ok", off()))
            rep.emit(f"    kill={k1 - k0:.2f}s")
        except Exception as e:
            rows.append(R("ops", idx, sid, "kill", -1,
                          f"fail({type(e).__name__})", off()))
            rep.emit(f"    kill 失败: {str(e)[:80]}")
    return rows


def run_ttl(args, refs, rep, all_rows):
    rep.emit(f"\n══ TTL 自动销毁观察：{args.ttl_count} 台 / TTL={args.ttl:.0f}s / "
             f"轮询 {args.poll:.1f}s ══")
    recs = []
    for i in range(args.ttl_count):
        t0 = time.perf_counter()
        sb = Sandbox.create(template=refs[i], timeout=args.ttl)
        t_done = time.perf_counter()
        recs.append({"idx": i, "sid": sb.sandbox_id, "sb": sb, "ref": refs[i],
                     "create_s": t_done - t0, "t_done": t_done,
                     "expiry": t_done + args.ttl,
                     "dead": None, "delta": None, "status": "watching"})
        rep.emit(f"  TTL[{i}] {sb.sandbox_id[:8]} ref={refs[i][:28]} "
                 f"create={t_done - t0:.2f}s，{args.ttl:.0f}s 后到期")

    deadline = max(r["expiry"] for r in recs) + args.grace
    fail_streak, first_fail = {}, {}
    while any(r["status"] == "watching" for r in recs):
        now = time.perf_counter()
        if now > deadline:
            break
        for r in recs:
            if r["status"] != "watching" or now < r["expiry"] - 5:
                continue
            try:
                r["sb"].get_info()
                fail_streak[r["sid"]] = 0
            except Exception:
                n = fail_streak.get(r["sid"], 0) + 1
                fail_streak[r["sid"]] = n
                if n == 1:
                    first_fail[r["sid"]] = now
                if n >= 2:
                    r["dead"] = first_fail[r["sid"]]
                    r["delta"] = r["dead"] - r["expiry"]
                    r["status"] = "dead"
                    rep.emit(f"  TTL[{r['idx']}] {r['sid'][:8]} 判死: "
                             f"到期 {r['delta']:+.1f}s（观察粒度 ±{args.poll:.0f}s）")
        time.sleep(args.poll)
    for r in recs:
        if r["status"] == "watching":
            r["status"] = "not_observed"
            rep.emit(f"  TTL[{r['idx']}] {r['sid'][:8]} grace 内未观察到销毁（本身即发现）")

    deltas = sorted(r["delta"] for r in recs if r["status"] == "dead")
    rep.emit(f"\n  TTL销毁延迟（计划到期→观察到死亡，n={len(deltas)}，"
             f"未观察到 {sum(1 for r in recs if r['status'] != 'dead')} 台）:")
    if deltas:
        rep.emit(f"    min {deltas[0]:+.1f}s   p50 {percentile(deltas, 50):+.1f}s   "
                 f"max {deltas[-1]:+.1f}s   mean {st.mean(deltas):+.1f}s")
        rep.emit(f"    注：读数含 0~{args.poll:.0f}s 轮询粒度；负值=提前于计划到期回收")
    for r in recs:
        all_rows.append(R("ttl", r["idx"], r["sid"], "ttl_destroy",
                          r["delta"] if r["delta"] is not None else -1,
                          r["status"], 0.0,
                          f"create={r['create_s']:.2f}s ref={r['ref']}"))


def print_op_stats(rows, rep):
    rep.emit(f"\n{'操作':<15}{'n':>4}{'min':>8}{'p50':>8}{'p90':>8}{'p95':>8}"
             f"{'max':>8}{'mean':>8}  失败/其他")
    for op in OPS_ORDER:
        sub = [r for r in rows if r["op"] == op]
        if not sub:
            continue
        ok = sorted(r["elapsed"] for r in sub
                    if r["status"] == "ok" and r["elapsed"] >= 0)
        others = {}
        for r in sub:
            if r["status"] != "ok":
                others[r["status"]] = others.get(r["status"], 0) + 1
        note = ", ".join(f"{k} x{v}" for k, v in others.items()) or "-"
        if not ok:
            rep.emit(f"{op:<15}{0:>4}{'-':>8}{'-':>8}{'-':>8}{'-':>8}{'-':>8}"
                     f"{'-':>8}  {note}")
            continue
        rep.emit(f"{op:<15}{len(ok):>4}{ok[0]:>8.2f}{percentile(ok, 50):>8.2f}"
                 f"{percentile(ok, 90):>8.2f}{percentile(ok, 95):>8.2f}"
                 f"{ok[-1]:>8.2f}{st.mean(ok):>8.2f}  {note}")


def save_csv(rows, path):
    cols = ["phase", "idx", "sid", "op", "elapsed", "status", "off", "detail"]
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def main():
    ap = argparse.ArgumentParser(description="沙箱生命周期管理操作批量计时")
    ap.add_argument("--template", default=os.environ.get(
        "TEMPLATE_ID", "92db8659-f980-4576-80ec-c7cf9b69348f"))
    ap.add_argument("--snapshots", default="",
                    help="快照引用（templateID:buildID），逗号分隔或 @refs.txt；"
                         "给了它就不看 --template；不足台数自动循环复用")
    ap.add_argument("--count", type=int, default=5, help="管理操作阶段的沙箱台数")
    ap.add_argument("--mode", choices=["serial", "burst"], default="serial",
                    help="serial=精准口径(默认)；burst=并发对照(含排队)")
    ap.add_argument("--skip-ttl", action="store_true", help="跳过 TTL 销毁观察")
    ap.add_argument("--skip-ops", action="store_true", help="跳过管理操作，只测 TTL")
    ap.add_argument("--ttl", type=int, default=180, help="TTL 时长（秒，≥60）")
    ap.add_argument("--ttl-count", type=int, default=3, help="TTL 观察沙箱台数")
    ap.add_argument("--poll", type=float, default=2.0, help="TTL 轮询周期（秒）")
    ap.add_argument("--grace", type=float, default=300,
                    help="到期后继续观察上限（秒），超过即报未观察到")
    ap.add_argument("--ops-timeout", type=int, default=3600,
                    help="管理操作阶段沙箱的 TTL（秒）")
    ap.add_argument("--ready-timeout", type=float, default=120,
                    help="print(ok) 重试上限（秒）")
    ap.add_argument("--keep", action="store_true",
                    help="不执行 kill（跳过主动销毁参照）")
    args = ap.parse_args()

    if not os.environ.get("E2B_API_KEY"):
        raise SystemExit("缺少 E2B_API_KEY 环境变量。"
                         "PowerShell:  $env:E2B_API_KEY='<你的key>'；"
                         "永久:  setx E2B_API_KEY <你的key>（重开终端生效）")

    refs_ops, n_src = load_refs(args.snapshots, args.count, args.template)
    src_desc = (f"快照引用 x{n_src}（循环至 {args.count} 台）" if args.snapshots
                else f"模板 {args.template}")

    rep = Report(os.path.join(BASE, f"lifecycle_ops_{TS}.txt"))
    csv_path = os.path.join(BASE, f"lifecycle_ops_{TS}.csv")
    all_rows = []
    t0 = time.monotonic()
    rep.emit("═" * 64)
    rep.emit(f" 生命周期管理操作计时  来源: {src_desc}")
    rep.emit(f" {time.strftime('%Y-%m-%d %H:%M:%S')}  台数 {args.count}  "
             f"模式 {args.mode}  TTL={'关' if args.skip_ttl else f'{args.ttl:.0f}s'}")
    rep.emit("═" * 64)
    try:
        if not args.skip_ops:
            if args.mode == "serial":
                for i in range(args.count):
                    all_rows.extend(run_ops_one(i, refs_ops[i], args, rep))
            else:
                rep.emit(f"[{time.strftime('%H:%M:%S')}] === 齐射 {args.count} 台"
                         f"（对照口径：墙钟含平台排队） ===")
                gun = threading.Barrier(args.count + 1)
                lock = threading.Lock()

                def worker(i):
                    try:
                        gun.wait(timeout=120)
                    except threading.BrokenBarrierError:
                        rep.emit(f"[{i + 1}] 发令前掉线，跳过")
                        return
                    rows = run_ops_one(i, refs_ops[i], args, rep)
                    with lock:
                        all_rows.extend(rows)

                from concurrent.futures import ThreadPoolExecutor
                with ThreadPoolExecutor(max_workers=args.count) as pool:
                    futs = [pool.submit(worker, i) for i in range(args.count)]
                    gun.wait(timeout=120)
                    for f in futs:
                        f.result()

            rep.emit("\n──────── 管理操作耗时统计（"
                     + ("串行=纯执行口径" if args.mode == "serial" else "并发=含平台排队")
                     + "）────────")
            print_op_stats(all_rows, rep)
            if args.mode == "burst":
                rep.emit("  排队分量 ≈ 并发p50 − 串行p50（先跑一轮 serial 再对比）")

        if not args.skip_ttl:
            refs_ttl, _ = load_refs(args.snapshots, args.ttl_count, args.template)
            run_ttl(args, refs_ttl, rep, all_rows)

        rep.emit(f"\n 总墙钟 {time.monotonic() - t0:.0f}s")
        rep.emit(f" 报告 -> {rep.txt_path}")
        rep.emit(f" CSV  -> {csv_path}")
    finally:
        save_csv(all_rows, csv_path)
        rep.close()


if __name__ == "__main__":
    main()
