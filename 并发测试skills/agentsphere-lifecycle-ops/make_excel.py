# -*- coding: utf-8 -*-
"""扫描目录里 lifecycle_ops_*.csv，自动取各模式×场景的最新一轮，生成双 sheet Excel：
Sheet1 场景对比（场景一/场景二 × volley/burst 四组 p50/p90/max）+ 场景说明 + 存在问题；
Sheet2 逐台明细（全量 sandbox_id/模板/快照/各操作）。
独立运行:  python make_excel.py [--base 目录] [--out 路径]
被 bench 调用: 每轮跑完自动刷新。
输出位置默认与 CSV 同目录；设环境变量 LIFECYCLE_EXCEL_OUT 可重定向（如桌面）。
"""
import argparse
import csv
import io
import os
import re
import sys

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

OPS = ["create", "create_verify", "pause", "verify_paused", "resume",
       "resume_verify", "update_timeout", "kill", "restart"]
TS_RE = re.compile(r"(2026\d{4}_\d{6})")

# ── 发现与分类 ──────────────────────────────────────────────
def load_csv(path):
    rows = []
    with io.open(path, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            if r.get("sid"):
                rows.append(r)
    return rows

def classify(name, rows):
    """返回 (ts, mode, scenario) 或 None。mode: serial/volley/burst/ttl；scenario: s1/s2/tpl"""
    m = TS_RE.search(name)
    if not m or not rows:
        return None
    ts = m.group(1)
    phases = {r.get("phase", "") for r in rows}
    sids = {r["sid"] for r in rows}
    if "ttl" in phases and "ops" not in phases:
        mode = "ttl"
    elif "_serial" in name:
        mode = "serial"
    elif "_volley" in name:
        mode = "volley"
    elif "_burst" in name:
        mode = "burst"
    else:
        # 旧无后缀文件按内容判别：
        # volley 文件按「操作」分块（create 一块→verify 一块→pause…，操作切换 ≈ 8 次）；
        # burst/serial 按「台」分块（每台 9 行连续，操作切换 ≈ 8×台数）。
        # burst 全员同时起跑 → create 的 off 贴 0 且台数多；serial 逐台串行 → create off 跨度大。
        op_sw = sum(1 for i in range(len(rows) - 1) if rows[i]["op"] != rows[i + 1]["op"])
        if op_sw <= 12 and len(sids) >= 10:
            mode = "volley"
        else:
            c_offs = [float(r.get("off") or 0) for r in rows if r["op"] == "create"]
            spread = (max(c_offs) - min(c_offs)) if c_offs else 0.0
            mode = "burst" if len(sids) >= 30 and spread < 5.0 else "serial"
    refs = set()
    for r in rows:
        if r["op"] == "create" and "ref=" in r.get("detail", ""):
            refs.add(r["detail"].split("ref=")[-1].strip())
    colons = {x for x in refs if ":" in x}
    if len(colons) == 1:
        scen = "s1"
    elif len(colons) > 1:
        scen = "s2"
    else:
        scen = "tpl"
    return ts, mode, scen

def discover(base):
    """→ {mode: {scen: {"ts","path","piv"}}} 取各组合最新一轮"""
    out = {}
    for fn in os.listdir(base):
        if not (fn.startswith("lifecycle_ops_") and fn.endswith(".csv")):
            continue
        info = classify(fn, load_csv(os.path.join(base, fn)))
        if not info:
            continue
        ts, mode, scen = info
        cur = out.setdefault(mode, {}).get(scen)
        if cur is None or ts > cur["ts"]:
            out[mode][scen] = {"ts": ts, "path": os.path.join(base, fn)}
    return out

def pivot(rows):
    out = {}
    for r in rows:
        d = out.setdefault(r["sid"], {"idx": int(r["idx"]), "ops": {}})
        op = "create_verify" if r["op"] == "print_ok" else r["op"]  # 旧 CSV 兼容
        if op == "create" and "ref=" in r.get("detail", ""):
            d["ref"] = r["detail"].split("ref=")[-1].strip()
        d["ops"][op] = (r["elapsed"], r["status"], r.get("detail", ""))
    return out

def percentile(vals, p):
    vals = sorted(vals)
    k = (len(vals) - 1) * p / 100.0
    f = int(k)
    c = min(f + 1, len(vals) - 1)
    return vals[f] if f == c else vals[f] + (vals[c] - vals[f]) * (k - f)

def op_series(piv, op):
    okv, bad = [], []
    for d in piv.values():
        t = d["ops"].get(op)
        if t is None:
            continue
        v, st, det = t
        if st == "ok":
            okv.append(float(v))
        elif st != "unsupported":
            bad.append((op, st, det))
    return okv, bad

def p50_of(piv, op):
    okv, _ = op_series(piv, op)
    return percentile(okv, 50) if okv else float("nan")

# ── 样式 ────────────────────────────────────────────────────
F_HEAD = Font(name="微软雅黑", size=10, bold=True, color="FFFFFF")
F_TXT = Font(name="微软雅黑", size=10)
F_TXT_B = Font(name="微软雅黑", size=10, bold=True)
FILL_HEAD = PatternFill("solid", fgColor="0D7D8C")
FILL_GRP = PatternFill("solid", fgColor="E3F1F3")
FILL_ALT = PatternFill("solid", fgColor="F4F7F9")
FILL_BAD = PatternFill("solid", fgColor="FDEEEE")
THIN = Side(style="thin", color="C9D4DB")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
CENTER = Alignment(horizontal="center", vertical="center", wrap_text=True)
LEFT = Alignment(horizontal="left", vertical="center", wrap_text=True)
NUMFMT = "0.000"

def cell(ws, r, c, v, font=F_TXT, fill=None, align=LEFT, fmt=None, border=True):
    x = ws.cell(row=r, column=c, value=v)
    x.font = font
    x.alignment = align
    if fill:
        x.fill = fill
    if fmt:
        x.number_format = fmt
    if border:
        x.border = BORDER
    return x

def set_widths(ws, widths):
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w

def band(ws, r, text, fill, last_col):
    cell(ws, r, 1, text, F_TXT_B, fill, LEFT)
    for c in range(2, last_col + 1):
        cell(ws, r, c, None, F_TXT_B, fill)
    ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=last_col)

def wide_text(ws, r, text, last_col, height=34):
    cell(ws, r, 1, text, F_TXT, None)
    for c in range(2, last_col + 1):
        cell(ws, r, c, None, F_TXT)
    ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=last_col)
    ws.row_dimensions[r].height = height

SCEN_NAME = {"s1": "场景一：1快照×N台", "s2": "场景二：N快照各1台", "tpl": "模板直拉"}

def build(base, out=None):
    found = discover(base)
    if not found:
        raise SystemExit("目录里没有 lifecycle_ops_*.csv 可用")
    pivs = {}
    for mode, by_scen in found.items():
        for scen, ent in by_scen.items():
            pivs[(mode, scen)] = pivot(load_csv(ent["path"]))

    # Sheet1 分组：volley/burst × s1/s2（有数据才出列）
    groups = [(m, s) for m in ("volley", "burst") for s in ("s1", "s2")
              if (m, s) in pivs]
    if not groups:
        groups = [(m, s) for (m, s) in pivs if s in ("s1", "s2")]
    last_col = 1 + 3 * len(groups)

    wb = Workbook()
    ws = wb.active
    ws.title = "场景对比"
    ws.sheet_view.showGridLines = False
    set_widths(ws, [26] + [10] * (3 * len(groups)))
    cell(ws, 1, 1, "操作（秒）", F_HEAD, FILL_HEAD, CENTER)
    cell(ws, 2, 1, None, F_HEAD, FILL_HEAD, CENTER)
    ws.merge_cells("A1:A2")
    for gi, (mode, scen) in enumerate(groups):
        label = {"volley": "volley 同相齐发", "burst": "burst 独立流水"}.get(mode, mode)
        c0 = 2 + gi * 3
        cell(ws, 1, c0, f"{SCEN_NAME[scen]}｜{label}", F_HEAD, FILL_HEAD, CENTER)
        for c in range(c0 + 1, c0 + 3):
            cell(ws, 1, c, None, F_HEAD, FILL_HEAD, CENTER)
        ws.merge_cells(start_row=1, start_column=c0, end_row=1, end_column=c0 + 2)
    for c in range(2, last_col + 1):
        cell(ws, 2, c, ["p50", "p90", "max"][(c - 2) % 3], F_TXT_B, FILL_GRP, CENTER)
    ws.freeze_panes = "A3"

    r = 3
    for i, op in enumerate(OPS):
        fill = FILL_ALT if i % 2 else None
        cell(ws, r, 1, op, F_TXT_B if op in ("create_verify", "resume_verify") else F_TXT, fill)
        for gi, (mode, scen) in enumerate(groups):
            okv, _ = op_series(pivs[(mode, scen)], op)
            c0 = 2 + gi * 3
            if okv:
                for j, v in enumerate([percentile(okv, 50), percentile(okv, 90), max(okv)]):
                    cell(ws, r, c0 + j, round(v, 3), F_TXT, fill, LEFT, NUMFMT)
            else:
                for j in range(3):
                    cell(ws, r, c0 + j, "UNSUPPORTED" if op == "restart" else "—",
                         F_TXT, fill, LEFT)
        r += 1

    cell(ws, r, 1, "失败操作数（500/409 等）", F_TXT)
    for gi, (mode, scen) in enumerate(groups):
        fails = {}
        for op in OPS:
            _, bad = op_series(pivs[(mode, scen)], op)
            if bad:
                fails[op] = len(bad)
        txt = "、".join(f"{op}×{n}" for op, n in fails.items()) if fails else "0（全部成功）"
        c0 = 2 + gi * 3
        cell(ws, r, c0, txt, F_TXT)
        for c in range(c0 + 1, c0 + 3):
            cell(ws, r, c, None, F_TXT)
        ws.merge_cells(start_row=r, start_column=c0, end_row=r, end_column=c0 + 2)
    r += 2

    notes = [
        "口径：create_verify = create 返回 → 首条 echo ok 跑通（含失败重试与等待）；resume_verify = resume(connect) 返回 → 首条 echo ok 跑通。首条命令成功那一刻 = 可用时刻。",
        "volley=每相一发令枪全场硬对齐（含等最慢台）；burst=每台独立流水相位错开（无全场等待，平台排队如实计入）。",
        "数据取目录中各「模式×场景」最新一轮；分位数=线性插值，与 bench 控制台同法。",
    ]
    for t in notes:
        cell(ws, r, 1, t, F_TXT, None, LEFT, border=False)
        r += 1

    r += 1
    band(ws, r, "测试场景说明", FILL_GRP, last_col)
    r += 1
    scen_desc = [
        ("serial（队列深度=1）",
         "逐台跑完整链路，任一时刻账号内仅 1 个管理操作在途——管理面纯执行耗时基线。volley − serial ≈ 对齐等待+hold 分量；burst − serial ≈ 平台自然排队分量。"),
        ("场景一（1快照×N台，volley 与 burst 各测）",
         "同一快照引用并发拉 N 台。volley=全员到位后一发令枪同相执行各操作；burst=各台独立跑完整链路、相位错开。对照分离「全场对齐等待」与「平台还原排队」。"),
        ("场景二（N快照各1台，volley 与 burst 各测）",
         "N 个不同快照各拉 1 台，同上两种并发口径——分散源对照，验证「N 台用 N 个不同快照」的收益。"),
        ("TTL 回收观察",
         "另起若干台设短 TTL 只轮询不操作，双确认判死——实测到期→真回收 ≈ +60s（平台批量清扫周期）。"),
    ]
    for name, desc in scen_desc:
        cell(ws, r, 1, name, F_TXT_B, None)
        cell(ws, r, 2, desc, F_TXT, None)
        for c in range(3, last_col + 1):
            cell(ws, r, c, None, F_TXT)
        ws.merge_cells(start_row=r, start_column=2, end_row=r, end_column=last_col)
        ws.row_dimensions[r].height = 34
        r += 1

    r += 1
    band(ws, r, "存在问题", FILL_BAD, last_col)
    r += 1
    def g(mode, scen, op):
        return p50_of(pivs[(mode, scen)], op) if (mode, scen) in pivs else float("nan")
    problems = [
        f"现象：resume_verify 稳定为 create_verify 的约 2 倍（同探针 exec echo ok、只差锚点）。"
        f"p50：场景二 volley {g('volley','s2','resume_verify'):.2f} vs {g('volley','s2','create_verify'):.2f}s；"
        f"场景一 volley {g('volley','s1','resume_verify'):.2f} vs {g('volley','s1','create_verify'):.2f}s。",
        "机理：create 返回时沙箱已 running，echo 只等 envd 命令通道；resume 的 connect 返回≠恢复完成，"
        "首条 echo 被平台 hold 到 paused 状态还原结束——恢复路径的「可用时刻」天然比 create 慢约一倍。",
        f"burst 对照（排除「全场对齐等待」）：同快照 burst resume_verify p50 {g('burst','s1','resume_verify'):.2f}s "
        f"高于 volley 的 {g('volley','s1','resume_verify'):.2f}s——同源楼梯是平台还原带宽固有的串行，流水持续占满还原通道；"
        f"异快照 burst p50 {g('burst','s2','resume_verify'):.2f}s 低于 volley 的 {g('volley','s2','resume_verify'):.2f}s——错开无瞬时争抢。",
        "影响与建议：「暂停挂起→继续使用」的体验按 resume_verify 口径评估；平台侧可优化 paused 还原耗时或对 resume 预热；"
        "批量交付按「N 台用 N 个不同快照」供给。",
    ]
    for txt in problems:
        wide_text(ws, r, txt, last_col)
        r += 1

    # Sheet2 逐台明细
    ws2 = wb.create_sheet("逐台明细")
    ws2.sheet_view.showGridLines = False
    set_widths(ws2, [26, 6, 38, 38, 40, 9, 12, 9, 11, 9, 12, 12, 9, 10, 30])
    heads = ["场景", "#", "sandbox_id", "模板ID", "快照引用"] + OPS[:8] + ["ttl_判死", "状态说明"]
    for c, t in enumerate(heads, 1):
        cell(ws2, 1, c, t, F_HEAD, FILL_HEAD, CENTER)
    ws2.freeze_panes = "C2"

    def op_val(t):
        if t is None:
            return ""
        v, st, det = t
        if st == "ok":
            return round(float(v), 3)
        if st == "unsupported":
            return "UNSUPPORTED"
        return f"fail {st}: {det[:60]}"

    MODE_LABEL = {"serial": "serial（队列深度=1）", "volley": "volley 同相齐发",
                  "burst": "burst 独立流水", "ttl": "TTL 回收"}
    r2 = 2
    blocks = []
    if "serial" in found:
        scen0 = "s1" if ("serial", "s1") in pivs else ("s2" if ("serial", "s2") in pivs else "tpl")
        blocks.append((MODE_LABEL["serial"], ("serial", scen0)))
    for mode, scen in groups:
        blocks.append((f"{SCEN_NAME[scen]}｜{MODE_LABEL[mode].split('（')[0]}", (mode, scen)))
    for label, key in blocks:
        piv = pivs[key]
        for i, (sid, d) in enumerate(sorted(piv.items(), key=lambda kv: kv[1]["idx"])):
            ref = d.get("ref", "")
            if ":" in ref:
                tpl, snap = ref.split(":", 1)
            else:
                tpl, snap = ref or "", "—（模板直拉）"
            fill = FILL_ALT if i % 2 else None
            cell(ws2, r2, 1, label, F_TXT, fill)
            cell(ws2, r2, 2, i, F_TXT, fill, LEFT, "0")
            cell(ws2, r2, 3, sid, F_TXT, fill)
            cell(ws2, r2, 4, tpl, F_TXT, fill)
            cell(ws2, r2, 5, snap, F_TXT, fill)
            for c, op in enumerate(OPS[:8], 6):
                v = op_val(d["ops"].get(op))
                f = FILL_BAD if isinstance(v, str) and v.startswith("fail") else fill
                cell(ws2, r2, c, v, F_TXT, f, LEFT, NUMFMT if isinstance(v, float) else None)
            notes2 = []
            for op in OPS[:8]:
                t = d["ops"].get(op)
                if t and t[1] not in ("ok", "unsupported"):
                    notes2.append(f"{op} {t[1]}: {t[2][:60]}")
                elif t and t[1] == "ok" and t[2] and t[2] != "attempt1":
                    notes2.append(f"{op} {t[2]}")
            cell(ws2, r2, 14, "", F_TXT, fill)
            cell(ws2, r2, 15, "；".join(notes2), F_TXT, fill)
            r2 += 1

    if "ttl" in found:
        scen0 = next(iter(found["ttl"]))
        trows = load_csv(found["ttl"][scen0]["path"])
        for i, t in enumerate(trows):
            det = t.get("detail", "")
            cs = det.split("create=")[1].split("s")[0] if "create=" in det else ""
            ref = det.split("ref=")[-1].strip() if "ref=" in det else ""
            tpl, snap = (ref.split(":", 1) + [""])[:2] if ":" in ref else (ref, "—")
            fill = FILL_ALT if i % 2 else None
            cell(ws2, r2, 1, "TTL 回收（双确认轮询）", F_TXT, fill)
            cell(ws2, r2, 2, i, F_TXT, fill, LEFT, "0")
            cell(ws2, r2, 3, t["sid"], F_TXT, fill)
            cell(ws2, r2, 4, tpl, F_TXT, fill)
            cell(ws2, r2, 5, snap, F_TXT, fill)
            cell(ws2, r2, 6, round(float(cs), 3) if cs else "", F_TXT, fill, LEFT, NUMFMT)
            for c in range(7, 14):
                cell(ws2, r2, c, "", F_TXT, fill)
            cell(ws2, r2, 14, round(float(t["elapsed"]), 3), F_TXT_B, fill, LEFT, NUMFMT)
            cell(ws2, r2, 15, f"status={t['status']}；到期→判死（清扫周期+60s）", F_TXT, fill)
            r2 += 1

    if out is None:
        out = os.environ.get("LIFECYCLE_EXCEL_OUT") or os.path.join(base, "生命周期场景对比.xlsx")
    if os.path.isdir(out):
        out = os.path.join(out, "生命周期场景对比.xlsx")
    wb.save(out)
    return out, r2 - 2

def main():
    ap = argparse.ArgumentParser(description="lifecycle CSV -> 双 sheet Excel")
    ap.add_argument("--base", default=os.path.dirname(os.path.abspath(__file__)))
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    out, rows = build(a.base, a.out)
    print(f"OK detail rows = {rows}")
    print(f"OK written: {out}")

if __name__ == "__main__":
    main()
