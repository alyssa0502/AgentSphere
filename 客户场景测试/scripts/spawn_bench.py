# -*- coding: utf-8 -*-
"""
AgentSphere 沙箱拉起性能测试（严格顺序模式）
═══════════════════════════════════════════════════════════════
PyCharm 里直接右键 Run（解释器选项目 venv）。

输入：
  1) 模板 ID          （回车 = f6c0b17f-ba7a-49d2-b7e2-33854811b5c8）
  2) 沙箱数量         （回车 = 5）
  3) 测完是否销毁     （回车 = 销毁，仅保留最后一个）

每个沙箱打印（严格上一个完全就绪才创建下一个）：
  - create API 耗时 / envd 可执行耗时
  - UI 3001 就绪（相对 / 累计）、chromium 9222 就绪（相对 / 累计）
  - 端到端（发令→全就绪）+ 冷热判定（/proc/uptime，>5s=热池）

输出：屏幕全量明细 + 汇总统计，同时存两份文件：
  spawn_bench_月日_时分秒.txt（完整报告）  /  .csv（明细数据）
"""
import csv
import os
import statistics as st
import sys
import time

# ── AgentSphere 三件套：从环境变量注入（控制台提供，勿提交密钥）──
for _k in ("E2B_API_URL", "E2B_SANDBOX_URL", "E2B_API_KEY"):
    if not os.environ.get(_k):
        sys.exit(f"缺少环境变量 {_k}（见 docs/04-脚本使用指南.md 的环境变量说明）")

from e2b import Sandbox  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

DEFAULT_TEMPLATE = "f6c0b17f-ba7a-49d2-b7e2-33854811b5c8"

REPORT = []          # 全程输出镜像，最后写报告文件
TS = time.strftime("%m%d_%H%M%S")
TXT_NAME = f"spawn_bench_{TS}.txt"
CSV_NAME = f"spawn_bench_{TS}.csv"


def emit(line=""):
    print(line)
    REPORT.append(line)


# 沙箱内探测脚本：读 uptime + 轮询 UI + 轮询 chromium
POLL = r'''
UP=$(cut -d' ' -f1 /proc/uptime | cut -d. -f1)
T1=$(date +%s%3N); U=-1
for i in $(seq 1 900); do
  [ "$(curl -s -o /dev/null -w '%{http_code}' --max-time 2 http://localhost:3001/health)" = "200" ] && { U=$(( $(date +%s%3N) - T1 )); break; }
  sleep 0.2
done
T2=$(date +%s%3N); C=-1
for i in $(seq 1 300); do
  curl -s --max-time 1 http://127.0.0.1:9222/json/version 2>/dev/null | grep -q Browser && { C=$(( $(date +%s%3N) - T2 )); break; }
  sleep 0.2
done
echo "uptime_s=$UP ui_ms=$U cr_ms=$C"
'''


def ask(prompt, default):
    v = input(f"{prompt}（回车={default}）: ").strip().lstrip("﻿").strip()
    return v or default


def measure_one(idx, template, kill_after, cr_expected):
    """严格顺序：create→envd→UI→chromium 全就绪才返回。返回 (行dict, Sandbox)。"""
    row = {"idx": idx, "id": "-", "create": 0.0, "envd": 0.0, "ui": -1.0,
           "cr": -1.0, "e2e": 0.0, "uptime": -1.0, "result": "FAIL"}

    emit(f"── [{idx + 1}] 创建沙箱（上一台已完全就绪）──")
    t0 = time.monotonic()
    try:
        sb = Sandbox.create(template=template, timeout=600)
    except Exception as e:
        emit(f"    ✗ create 失败: {type(e).__name__}: {str(e)[:120]}")
        row["e2e"] = time.monotonic() - t0
        return row, None
    row["create"] = time.monotonic() - t0
    row["id"] = sb.sandbox_id[:8]
    t_create_done = time.monotonic()

    # envd 可执行：首个命令重试直到成功
    envd_ok = False
    while time.monotonic() - t_create_done < 180:
        try:
            sb.commands.run("echo ok", user="root", cwd="/root", timeout=10)
            envd_ok = True
            break
        except Exception:
            time.sleep(0.5)
    row["envd"] = time.monotonic() - t0
    if not envd_ok:
        emit("    ✗ envd 180s 内不可执行")
        if kill_after:
            try:
                sb.kill()
            except Exception:
                pass
        return row, None

    # UI + chromium 探测
    try:
        r = sb.commands.run(POLL, user="root", cwd="/root", timeout=300)
        kv = dict(p.split("=", 1)
                  for p in ((r.stdout or "") + (r.stderr or "")).split() if "=" in p)
        row["uptime"] = float(kv.get("uptime_s", -1))
        row["ui"] = float(kv.get("ui_ms", -1)) / 1000
        row["cr"] = float(kv.get("cr_ms", -1)) / 1000
    except Exception as e:
        emit(f"    ✗ 探测失败: {type(e).__name__}: {str(e)[:120]}")

    row["e2e"] = time.monotonic() - t0

    # ── 单沙箱完整明细 ──
    temp = "热池" if row["uptime"] > 5 else "冷启动"
    emit(f"    沙箱ID:   {sb.sandbox_id}")
    emit(f"    create API:        {row['create']:.2f}s")
    emit(f"    envd 可执行:        {row['envd']:.2f}s（发令起算，含 create）")
    emit(f"    UI 3001:           相对探测 {row['ui']:.2f}s    累计自发令 {row['envd'] + max(row['ui'], 0):.2f}s")
    if cr_expected:
        emit(f"    chromium 9222:     相对探测 {row['cr']:.2f}s    累计自发令 {row['envd'] + max(row['cr'], 0):.2f}s")
    emit(f"    端到端(发令→全就绪): {row['e2e']:.2f}s    [{temp} uptime={row['uptime']:.0f}s]")
    ok_ui = row["ui"] >= 0
    ok_cr = (row["cr"] >= 0) if cr_expected else True
    row["result"] = "PASS" if (ok_ui and ok_cr) else "PARTIAL"

    if kill_after:
        try:
            sb.kill()
            emit("    （已销毁）")
        except Exception:
            emit("    （销毁失败，沙箱留存活）")
            return row, sb
    return row, sb


def main():
    emit("═" * 62)
    emit(" AgentSphere 沙箱拉起性能测试（严格顺序模式）")
    emit(f" 时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    emit("═" * 62)
    template = ask("模板 ID", DEFAULT_TEMPLATE)
    n = int(ask("沙箱数量", "5"))
    destroy = ask("测完销毁? y=销毁(留最后1台)/n=全保留", "y").lower() != "n"
    emit(f" 模板: {template}   数量: {n}   测完{'销毁(留最后1台)' if destroy else '全保留'}")
    emit("═" * 62)

    rows = []
    cr_expected = True
    last_sb = None
    for i in range(n):
        row, sb = measure_one(i, template, destroy, cr_expected)
        if row["result"] == "FAIL":
            emit("!! 失败即停止后续创建")
            break
        if row["cr"] < 0 and cr_expected:
            cr_expected = False
            emit("    ⚠ 此模板无 chromium 9222，后续不再检测浏览器")
        rows.append(row)
        last_sb = sb

    # ── 汇总 ──────────────────────────────────────────────
    ok = [r for r in rows if r["result"] in ("PASS", "PARTIAL")]
    emit()
    emit("═" * 62)
    emit(f" 汇总：成功 {len(ok)}/{n}   模板 {template}")
    emit("═" * 62)
    emit(f"  {'#':<3}{'id':<10}{'create':>7}{'envd':>7}{'ui':>7}"
         f"{'chromium':>9}{'端到端':>8}  冷热")
    for r in rows:
        if r["result"] == "FAIL":
            emit(f"  {r['idx']:<3}{r['id']:<10}  FAIL")
            continue
        emit(f"  {r['idx']:<3}{r['id']:<10}{r['create']:>7.2f}{r['envd']:>7.2f}"
             f"{r['ui']:>7.2f}{r['cr']:>9.2f}{r['e2e']:>8.2f}"
             f"  {'热池' if r['uptime'] > 5 else '冷启动'}")

    def stat(label, key):
        vals = [r[key] for r in ok if r[key] >= 0]
        if not vals:
            return
        emit(f"  {label:<14} min={min(vals):.2f}  p50={st.median(vals):.2f}"
             f"  mean={st.mean(vals):.2f}  max={max(vals):.2f}")

    emit()
    emit("  阶段统计（秒）:")
    stat("create API", "create")
    stat("envd 可执行", "envd")
    stat("UI 相对", "ui")
    if cr_expected:
        stat("chromium 相对", "cr")
    stat("端到端", "e2e")
    cold = sum(1 for r in ok if r["uptime"] > 0 and r["uptime"] <= 5)
    emit(f"\n  冷启动 {cold} 台 / 热池 {len(ok) - cold} 台")

    if last_sb is not None and not destroy:
        emit(f"\n  保留存活沙箱: {last_sb.sandbox_id}")

    # ── 存文件 ────────────────────────────────────────────
    with open(TXT_NAME, "w", encoding="utf-8") as f:
        f.write("\n".join(REPORT) + "\n")
    with open(CSV_NAME, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["idx", "id", "create", "envd", "ui", "chromium",
                    "e2e", "uptime_s", "result"])
        for r in rows:
            w.writerow([r["idx"], r["id"], f"{r['create']:.2f}", f"{r['envd']:.2f}",
                        f"{r['ui']:.2f}", f"{r['cr']:.2f}", f"{r['e2e']:.2f}",
                        f"{r['uptime']:.0f}", r["result"]])
    emit(f"\n  报告 -> {os.path.abspath(TXT_NAME)}")
    emit(f"  明细 -> {os.path.abspath(CSV_NAME)}")


if __name__ == "__main__":
    main()
