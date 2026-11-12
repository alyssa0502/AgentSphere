# -*- coding: utf-8 -*-
"""
AgentSphere 沙箱生命周期管理操作批量计时
════════════════════════════════════════════════════════════════
精准统计每台沙箱的管理操作耗时（默认串行口径 = 纯执行耗时，不含下发队列）：

  create → print(ok) → pause → 状态核实(paused) → resume(connect)
        → print(ok)核实 → restart(平台无此接口则标 UNSUPPORTED) → update_timeout
        (set_timeout 把 TTL 延长 60s，覆盖平台 updated 事件) → kill(主动销毁参照)

外加 TTL 自动销毁观察：M 台 timeout=T 的沙箱，双确认轮询测
「计划到期 → 观察到死亡」的回收延迟（含 ±一个轮询周期的观察粒度）。

两种模式：
  serial（精准口径）任一时刻只有 1 个管理操作在途 → 服务端队列深度=1，无排队污染
  burst （独立流水并发对照）N 台各自跑完整链路，相位错开 → 墙钟含平台排队
  volley（并发生命周期）    N 个 sandbox 先全部 create 到位，然后每个操作一发令枪：
                          同一瞬间 N 台全 pause、同一瞬间全 resume、同一瞬间全 kill。
                          每个操作的读数 = 该操作在队列深度=N 时的单台耗时分布，
                          这是"n 个快照同时执行生命周期管理"的严格口径。

计时口径（全部 perf_counter 单调时钟）：
  - 每个操作：调用发出前最后一个打点 → 返回后第一个打点，窗口内零打印零日志零额外网络调用
  - print(ok)：只对成功那次调用计时；重试等待单列（*_verify 列的 off 起点可见）
  - resume_verify：该台 resume(connect) 返回后到首条命令真正跑通的耗时
    （锚点 = 该台 resume 返回时刻，含失败重试——回答"恢复后多久能真正执行代码/业务"）
  - 状态核实（get_info==paused）单列 verify_paused，不混入 pause 耗时

拉起来源：--template 模板直拉，或 --snapshots 快照引用（templateID:buildID，逗号分隔或 @refs.txt，
不足台数自动循环复用）。三种场景：
  1 个快照拉 N 台          python lifecycle_ops_bench.py --snapshots <ref> --count N
  N 个快照同时拉 N 台      python lifecycle_ops_bench.py --snapshots a,b,c --count N --mode volley
  N 个快照并发生命周期     python lifecycle_ops_bench.py --snapshots a,b,c --mode volley --skip-ttl

用法（agentsphere-demo venv）：
  python lifecycle_ops_bench.py                                    # 串行 5 台 + TTL 3台/180s
  python lifecycle_ops_bench.py --count 1 --ttl-count 1 --ttl 90   # 冒烟
  python lifecycle_ops_bench.py --mode volley --count 20 --skip-ttl  # 并发生命周期操作
  python lifecycle_ops_bench.py --mode burst --count 20            # 独立流水并发对照
  python lifecycle_ops_bench.py --skip-ttl                         # 只测管理操作

输出：屏幕明细 + create 相拉起报告（逐台延迟/分位数/达标率，volley）
     + 每操作分位数统计 + lifecycle_ops_<时间戳>.txt/.csv
"""
import argparse
import csv
from datetime import datetime
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
             "resume_verify", "restart", "restart_verify", "update_timeout", "kill"]


def percentile(vals, p):
    vals = sorted(vals)
    k = (len(vals) - 1) * p / 100.0
    f = int(k)
    c = min(f + 1, len(vals) - 1)
    return vals[f] if f == c else vals[f] + (vals[c] - vals[f]) * (k - f)


PERCENTILES = [50, 75, 90, 95, 99, 99.9]


def print_lat_stats(data, sla, rep):
    """create 相拉起报告的分位数统计块（与 burst_create 口径一致）。"""
    n = len(data)
    rep.emit(f"\n==================== 时延统计（n={n}，仅成功样本） ====================")
    rep.emit(f"  {'min':<8}{data[0]:.3f}s")
    for p in PERCENTILES:
        rep.emit(f"  {'p' + f'{p:g}':<8}{percentile(data, p):.3f}s")
    rep.emit(f"  {'max':<8}{data[-1]:.3f}s")
    std = st.stdev(data) if n >= 2 else 0.0
    rep.emit(f"  {'mean':<8}{st.mean(data):.3f}s   stddev {std:.3f}s")
    met = sum(1 for x in data if x <= sla)
    rep.emit(f"  达标率(<={sla:g}s): {met}/{n} ({met / n * 100:.1f}%)")


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


def echo_timed(sb, label, idx, sid, t0, rows, ready_timeout, anchor=None):
    """echo ok 直到成功。返回 (成功时刻, 计时值)。
    anchor=None：只对成功那次调用计时（print_ok / restart_verify 口径）。
    anchor=时刻：成功时刻−anchor（resume_verify 口径 = resume 返回后到真正能执行
    命令的耗时，含失败尝试与重试间隔——首条命令成功那一刻才算真正可用）。"""
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
            elapsed = (a1 - anchor) if anchor is not None else (a1 - a0)
            rows.append(R("ops", idx, sid, label, elapsed, "ok", a1 - t0,
                          f"attempt{attempt}"))
            return a1, elapsed
        if time.perf_counter() - t_start > ready_timeout:
            rows.append(R("ops", idx, sid, label, -1, "timeout", a1 - t0,
                          f"{attempt}次尝试未通"))
            return None, None
        time.sleep(0.1)


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

    _, ok_s = echo_timed(sb2, "resume_verify", idx, sid, t0, rows,
                         args.ready_timeout, anchor=c1)
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

    a0 = time.perf_counter()
    try:
        sb2.set_timeout(args.ops_timeout + 60)
        a1 = time.perf_counter()
        rows.append(R("ops", idx, sid, "update_timeout", a1 - a0, "ok", off(),
                      "TTL+60s"))
        rep.emit(f"    update_timeout={a1 - a0:.2f}s (TTL+60s)")
    except Exception as e:
        rows.append(R("ops", idx, sid, "update_timeout", -1,
                      f"fail({type(e).__name__})", off(), str(e)[:80]))
        rep.emit(f"    update_timeout 失败: {str(e)[:80]}")

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


def run_volley(args, refs, rep):
    """并发生命周期操作：N 个 sandbox 先全部 create 到位，然后每个操作一发令枪，
    N 台在同一瞬间执行同一个操作（全 pause / 全 resume / 全 kill）。
    每个操作的读数 = 该操作在并发度=N 时的单台耗时分布。返回 rows。"""
    from concurrent.futures import ThreadPoolExecutor

    n = args.count
    rows = []
    lock = threading.Lock()
    recs = [None] * n
    fails = []              # (ref, err)
    times = [None] * n      # 每台 create 的 (send, ret) perf_counter 打点

    def phase(op, label, cur, worker_fn):
        if not cur:
            return
        rep.emit(f"── {label}：{len(cur)} 台并发 ──")
        g = threading.Barrier(len(cur) + 1)

        def guarded(i, rec):
            local = []
            try:
                g.wait(timeout=180)
                worker_fn(i, rec, local)
            except threading.BrokenBarrierError:
                pass
            except Exception as e:
                local.append(R("ops", i, rec["sid"], op, -1,
                               f"fail({type(e).__name__})", 0.0, str(e)[:80]))
            finally:
                with lock:
                    rows.extend(local)

        with ThreadPoolExecutor(max_workers=len(cur)) as pool:
            futs = [pool.submit(guarded, i, rec) for i, rec in cur]
            g.wait(timeout=180)
            t_gun_w = time.time()
            for f in futs:
                f.result()
            t_end_w = time.time()
        ok = sorted(r["elapsed"] for r in rows
                    if r["op"] == op and r["status"] == "ok" and r["elapsed"] >= 0)
        if ok:
            rep.emit(f"{op} 下发时间: {datetime.fromtimestamp(t_gun_w).strftime('%H:%M:%S.%f')[:-3]}"
                     f"  {op} 结束时间: {datetime.fromtimestamp(t_end_w).strftime('%H:%M:%S.%f')[:-3]}")
            ok_rows = [r for r in rows
                       if r["op"] == op and r["status"] == "ok" and r["elapsed"] >= 0]
            for r in sorted(ok_rows, key=lambda r: r["elapsed"]):
                ref = recs[r["idx"]]["ref"] if recs[r["idx"]] else ""
                rep.emit(f"sandbox {r['sid']} [{ref[:24]}] {op} 耗时: {r['elapsed']:.3f}s")
            rep.emit(f"   {op}: n={len(ok)} min={ok[0]:.2f}s "
                     f"p50={percentile(ok, 50):.2f}s max={ok[-1]:.2f}s")

    def living():
        return [(i, r) for i, r in enumerate(recs) if r and not r["dead"]]

    # ── 相 1：create 并发（发令枪统一放行）──
    rep.emit(f"计划拉起 {n} 个沙箱，SLA {args.sla:g}s")
    rep.emit(f"── create：{n} 台并发 ──")
    g = threading.Barrier(n + 1)

    def create_worker(i):
        local = []
        try:
            g.wait(timeout=180)
            a0 = time.perf_counter()
            try:
                sb = Sandbox.create(template=refs[i], timeout=args.ops_timeout)
            except Exception as e:
                local.append(R("ops", i, "-", "create", -1,
                               f"fail({type(e).__name__})", 0.0, f"ref={refs[i]}"))
                with lock:
                    fails.append((refs[i], str(e)))
                return
            a1 = time.perf_counter()
            times[i] = (a0, a1)
            recs[i] = {"sid": sb.sandbox_id, "sb": sb, "sb2": None,
                       "ref": refs[i], "dead": False}
            local.append(R("ops", i, recs[i]["sid"], "create", a1 - a0, "ok",
                           0.0, f"ref={refs[i]}"))
        except threading.BrokenBarrierError:
            pass
        finally:
            with lock:
                rows.extend(local)

    with ThreadPoolExecutor(max_workers=n) as pool:
        futs = [pool.submit(create_worker, i) for i in range(n)]
        g.wait(timeout=180)
        t_gun = time.perf_counter()
        t_gun_wall = time.time()
        for f in futs:
            f.result()

    ok_pairs = [(i, recs[i]) for i in range(n) if recs[i] is not None]
    if ok_pairs:
        wall = max(times[i][1] for i, _ in ok_pairs) - t_gun
        rep.emit(f"\n成功 {len(ok_pairs)}/{n}（成功率 {len(ok_pairs) / n * 100:.1f}%），"
                 f"总墙钟（发令->全部返回）{wall:.3f}s")
        min_send = min(times[i][0] for i, _ in ok_pairs)
        max_ret = max(times[i][1] for i, _ in ok_pairs)
        e2e = max_ret - min_send
        t_send = datetime.fromtimestamp(t_gun_wall + (min_send - t_gun))
        t_done = datetime.fromtimestamp(t_gun_wall + (max_ret - t_gun))
        rep.emit(f"端到端（最早发出 -> 最晚返回）: {e2e:.3f}s，"
                 f"吞吐 {len(ok_pairs) / wall:.2f} 个/s")
        rep.emit(f"create 下发时间: {t_send.strftime('%H:%M:%S.%f')[:-3]}  "
                 f"create 结束时间: {t_done.strftime('%H:%M:%S.%f')[:-3]}")
    else:
        rep.emit(f"\n成功 0/{n}（成功率 0.0%）")
    for ref, err in fails:
        rep.emit(f"创建失败 [{ref[:24]}]: {err[:160]}")
    for i, rec in sorted(ok_pairs, key=lambda p: times[p[0]][1] - times[p[0]][0]):
        rep.emit(f"sandbox {rec['sid']} [{rec['ref'][:24]}] "
                 f"拉起延迟: {times[i][1] - times[i][0]:.3f}s")
    lats = sorted(times[i][1] - times[i][0] for i, _ in ok_pairs)
    if lats:
        print_lat_stats(lats, args.sla, rep)

    def w_print_ok(i, rec, local):
        echo_timed(rec["sb"], "print_ok", i, rec["sid"],
                   time.perf_counter(), local, args.ready_timeout)

    phase("print_ok", "print(ok) 通道就绪", living(), w_print_ok)

    def w_pause(i, rec, local):
        a0 = time.perf_counter()
        rec["sb"].pause()
        local.append(R("ops", i, rec["sid"], "pause",
                       time.perf_counter() - a0, "ok", 0.0))

    phase("pause", "pause", living(), w_pause)

    def w_verify(i, rec, local):
        v0 = time.perf_counter()
        info = rec["sb"].get_info()
        v1 = time.perf_counter()
        local.append(R("ops", i, rec["sid"], "verify_paused", v1 - v0,
                       "ok" if info.state == SandboxState.PAUSED else "state_wrong",
                       0.0, f"state={info.state}"))

    phase("verify_paused", "状态核实 paused", living(), w_verify)

    def w_resume(i, rec, local):
        c0 = time.perf_counter()
        try:
            rec["sb2"] = Sandbox.connect(rec["sid"])
        except Exception as e:
            rec["dead"] = True
            local.append(R("ops", i, rec["sid"], "resume", -1,
                           f"fail({type(e).__name__})", 0.0, str(e)[:80]))
            return
        ret = time.perf_counter()
        rec["resume_ret"] = ret                    # 锚点：该台 resume 返回时刻
        local.append(R("ops", i, rec["sid"], "resume",
                       ret - c0, "ok", 0.0, "connect(paused)"))

    phase("resume", "resume(connect)", living(), w_resume)

    def w_resume_verify(i, rec, local):
        echo_timed(rec["sb2"], "resume_verify", i, rec["sid"],
                   time.perf_counter(), local, args.ready_timeout,
                   anchor=rec.get("resume_ret"))

    phase("resume_verify", "resume 后命令核实", living(), w_resume_verify)

    def w_restart(i, rec, local):
        fn = getattr(rec["sb2"], "restart", None)
        if fn is None:
            local.append(R("ops", i, rec["sid"], "restart", -1, "unsupported",
                           0.0, "SDK/平台无 restart 接口"))
            return
        a0 = time.perf_counter()
        fn()
        local.append(R("ops", i, rec["sid"], "restart",
                       time.perf_counter() - a0, "ok", 0.0))

    phase("restart", "restart", living(), w_restart)

    def w_restart_verify(i, rec, local):
        if getattr(rec["sb2"], "restart", None) is None:
            return
        echo_timed(rec["sb2"], "restart_verify", i, rec["sid"],
                   time.perf_counter(), local, args.ready_timeout)

    phase("restart_verify", "restart 后命令核实", living(), w_restart_verify)

    def w_update(i, rec, local):
        a0 = time.perf_counter()
        rec["sb2"].set_timeout(args.ops_timeout + 60)
        local.append(R("ops", i, rec["sid"], "update_timeout",
                       time.perf_counter() - a0, "ok", 0.0, "TTL+60s"))

    phase("update_timeout", "update_timeout(TTL+60s)", living(), w_update)

    cur = [(i, r) for i, r in enumerate(recs) if r]
    if args.keep:
        with lock:
            rows.extend(R("ops", i, r["sid"], "kill", -1, "skipped(--keep)", 0.0)
                        for i, r in cur)
        rep.emit("── kill：skipped(--keep) ──")
    else:
        def w_kill(i, rec, local):
            k0 = time.perf_counter()
            try:
                rec["sb"].kill()
            except Exception as e:
                local.append(R("ops", i, rec["sid"], "kill", -1,
                               f"fail({type(e).__name__})", 0.0, str(e)[:80]))
                return
            local.append(R("ops", i, rec["sid"], "kill",
                           time.perf_counter() - k0, "ok", 0.0))

        phase("kill", "kill", cur, w_kill)

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
                             f"到期 {r['delta']:+.1f}s")
        time.sleep(args.poll)
    for r in recs:
        if r["status"] == "watching":
            r["status"] = "not_observed"
            rep.emit(f"  TTL[{r['idx']}] {r['sid'][:8]} grace 内未观察到销毁")

    deltas = sorted(r["delta"] for r in recs if r["status"] == "dead")
    rep.emit(f"\n  TTL销毁延迟（计划到期→观察到死亡，n={len(deltas)}，"
             f"未观察到 {sum(1 for r in recs if r['status'] != 'dead')} 台）:")
    if deltas:
        rep.emit(f"    min {deltas[0]:+.1f}s   p50 {percentile(deltas, 50):+.1f}s   "
                 f"max {deltas[-1]:+.1f}s   mean {st.mean(deltas):+.1f}s")
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
    ap.add_argument("--sla", type=float, default=2.0,
                    help="create 相达标阈值（秒），volley 拉起报告的达标率用")
    ap.add_argument("--mode", choices=["serial", "burst", "volley"], default="serial",
                    help="serial=精准口径(默认,队列深度=1)；burst=独立流水并发对照；"
                         "volley=N 个 sandbox 并发执行同一操作(并发 pause/resume/kill))")
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

    display_n = args.ttl_count if args.skip_ops else args.count
    refs_ops, n_src = load_refs(args.snapshots, max(display_n, 1), args.template)
    src_desc = (f"快照引用 x{n_src}（循环复用至 {display_n} 台）" if args.snapshots
                else f"模板 {args.template}")

    rep = Report(os.path.join(BASE, f"lifecycle_ops_{TS}.txt"))
    csv_path = os.path.join(BASE, f"lifecycle_ops_{TS}.csv")
    all_rows = []
    t0 = time.monotonic()
    rep.emit("═" * 64)
    rep.emit(f" 生命周期管理操作计时  来源: {src_desc}")
    rep.emit(f" {time.strftime('%Y-%m-%d %H:%M:%S')}  操作台数 {args.count}  "
             f"TTL 台数 {args.ttl_count}  模式 {args.mode}  "
             f"TTL={'关' if args.skip_ttl else f'{args.ttl:.0f}s'}")
    rep.emit("═" * 64)
    try:
        if not args.skip_ops:
            if args.mode == "serial":
                for i in range(args.count):
                    all_rows.extend(run_ops_one(i, refs_ops[i], args, rep))
            elif args.mode == "volley":
                rep.emit(f"[{time.strftime('%H:%M:%S')}] === {args.count} 个 sandbox 并发生命周期操作 ===")
                all_rows.extend(run_volley(args, refs_ops, rep))
            else:
                rep.emit(f"[{time.strftime('%H:%M:%S')}] === {args.count} 个 sandbox 并发生命周期操作（独立流水） ===")
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

            if args.mode == "volley":
                rep.emit(f"\n管理操作耗时统计（并发口径：{args.count} 个 sandbox "
                         f"同一瞬间执行同一操作，队列深度={args.count}）────────")
            else:
                rep.emit("\n──────── 管理操作耗时统计 ────────")
            print_op_stats(all_rows, rep)

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
