# -*- coding: utf-8 -*-
"""生命周期计时 CSV -> 桌面 Excel（Sheet1 场景对比 volley/burst 四组 p50/p90/max + 场景说明 + 问题；
Sheet2 逐台明细全量 sandbox/模板/快照）。运行: python gen_excel.py"""
import csv
import io
import os
import sys

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

DIR = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(r"C:\Users\Administrator\Desktop", "场景对比_20261123.xlsx")

SERIAL_CSV = "lifecycle_ops_20261123_105823_serial.csv"
TTL_CSV = "lifecycle_ops_20261123_095549_ttl.csv"
RUNS = [
    ("场景一 volley：1快照×50同相齐发", "lifecycle_ops_20261123_105846_volley.csv"),
    ("场景二 volley：50快照各1台同相齐发", "lifecycle_ops_20261123_105916_volley.csv"),
    ("场景一 burst：1快照×50独立流水", "lifecycle_ops_20261123_162114_burst.csv"),
    ("场景二 burst：50快照各1台独立流水", "lifecycle_ops_20261123_162149_burst.csv"),
]
OPS = ["create", "create_verify", "pause", "verify_paused", "resume",
       "resume_verify", "update_timeout", "kill", "restart"]
WALL = [22, 6, 22, 5]

# ── 数据装载 ────────────────────────────────────────────────
def load_life(name):
    out = []
    with io.open(os.path.join(DIR, name), encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            if r.get("sid"):
                out.append(r)
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

piv_serial = pivot(load_life(SERIAL_CSV))
pivs = [pivot(load_life(name)) for _, name in RUNS]
ttl_rows = load_life(TTL_CSV)
piv_v1, piv_v2, piv_b1, piv_b2 = pivs

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

fails_by_run = {}
for name, piv in zip([n for n, _ in RUNS], pivs):
    fails_by_run[name] = {}
    for op in OPS:
        _, bad = op_series(piv, op)
        if bad:
            fails_by_run[name][op] = len(bad)

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
    """整行色带小节标题：先逐格上样式，再合并。"""
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

wb = Workbook()
ws = wb.active
ws.title = "场景对比"
ws.sheet_view.showGridLines = False
set_widths(ws, [26] + [10] * 12)

# 表头两行：A1:A2 操作；B..M 四组 p50/p90/max
cell(ws, 1, 1, "操作（秒）", F_HEAD, FILL_HEAD, CENTER)
cell(ws, 2, 1, None, F_HEAD, FILL_HEAD, CENTER)
ws.merge_cells("A1:A2")
for gi, (name, _) in enumerate(RUNS):
    c0 = 2 + gi * 3
    cell(ws, 1, c0, name, F_HEAD, FILL_HEAD, CENTER)
    for c in range(c0 + 1, c0 + 3):
        cell(ws, 1, c, None, F_HEAD, FILL_HEAD, CENTER)
    ws.merge_cells(start_row=1, start_column=c0, end_row=1, end_column=c0 + 2)
for c in range(2, 14):
    cell(ws, 2, c, ["p50", "p90", "max"][(c - 2) % 3], F_TXT_B, FILL_GRP, CENTER)
ws.freeze_panes = "A3"

r = 3
for i, op in enumerate(OPS):
    fill = FILL_ALT if i % 2 else None
    cell(ws, r, 1, op, F_TXT_B if op in ("create_verify", "resume_verify") else F_TXT, fill)
    for gi, piv in enumerate(pivs):
        okv, _ = op_series(piv, op)
        base = 2 + gi * 3
        if okv:
            for j, v in enumerate([percentile(okv, 50), percentile(okv, 90), max(okv)]):
                cell(ws, r, base + j, round(v, 3), F_TXT, fill, LEFT, NUMFMT)
        else:
            for j in range(3):
                cell(ws, r, base + j, "UNSUPPORTED" if op == "restart" else "—", F_TXT, fill, LEFT)
    r += 1

cell(ws, r, 1, "失败操作数（500/409 等）", F_TXT)
for gi, (name, _) in enumerate(RUNS):
    fails = fails_by_run[name]
    txt = "、".join(f"{op}×{n}" for op, n in fails.items()) if fails else "0（全部成功）"
    base = 2 + gi * 3
    cell(ws, r, base, txt, F_TXT)
    for c in range(base + 1, base + 3):
        cell(ws, r, c, None, F_TXT)
    ws.merge_cells(start_row=r, start_column=base, end_row=r, end_column=base + 2)
r += 1
cell(ws, r, 1, "总墙钟（发令→全流程收尾）", F_TXT)
for gi, sec in enumerate(WALL):
    base = 2 + gi * 3
    cell(ws, r, base, sec, F_TXT_B, None, LEFT, "0")
    cell(ws, r, base + 1, "s", F_TXT)
r += 2

notes = [
    "口径：create_verify = create 返回 → 首条 echo ok 跑通（含失败重试与等待）；resume_verify = resume(connect) 返回 → 首条 echo ok 跑通。首条命令成功那一刻 = 可用时刻。",
    "volley=每相一发令枪全场硬对齐（含等最慢台）；burst=每台独立流水相位错开（无全场等待，但平台排队如实计入）。",
    "数据源：_105846_volley/_105916_volley + _112907_burst/_112949_burst，2026-11-23；文件名已带模式后缀；分位数=线性插值，与 bench 控制台同法。",
]
for t in notes:
    cell(ws, r, 1, t, F_TXT, None, LEFT, border=False)
    r += 1

r += 1
band(ws, r, "测试场景说明", FILL_GRP, 13)
r += 1
scen_desc = [
    ("serial（队列深度=1）",
     "5 台逐台跑完整链路，任一时刻账号内仅 1 个管理操作在途——管理面纯执行耗时基线，无排队分量。volley 读数 − serial 读数 ≈ 排队/hold 分量。"),
    ("场景一（1快照×50，volley 与 burst 各一轮）",
     "同一快照（bb468965:40ad37f4）拉 50 台。volley=全员到位后一发令枪同相执行；burst=50 台各自独立跑完整链路、相位错开。两者对照分离「全场等待」与「平台还原排队」。"),
    ("场景二（50快照各1台，volley 与 burst 各一轮）",
     "50 个不同快照各拉 1 台，同上两种并发口径——分散源对照，验证「n 台用 n 个不同快照」的收益。"),
    ("TTL 回收观察",
     "另起 2 台 TTL=120s，全程只轮询不操作，双确认判死——到期→真回收 = +61.3s（平台批量清扫周期 +60s）。"),
]
for name, desc in scen_desc:
    cell(ws, r, 1, name, F_TXT_B, None)
    cell(ws, r, 2, desc, F_TXT, None)
    for c in range(3, 14):
        cell(ws, r, c, None, F_TXT)
    ws.merge_cells(start_row=r, start_column=2, end_row=r, end_column=13)
    ws.row_dimensions[r].height = 34
    r += 1

r += 1
band(ws, r, "存在问题", FILL_BAD, 13)
r += 1
cvv1, rvv1 = p50_of(piv_v1, "create_verify"), p50_of(piv_v1, "resume_verify")
cvv2, rvv2 = p50_of(piv_v2, "create_verify"), p50_of(piv_v2, "resume_verify")
cvb1, rvb1 = p50_of(piv_b1, "create_verify"), p50_of(piv_b1, "resume_verify")
cvb2, rvb2 = p50_of(piv_b2, "create_verify"), p50_of(piv_b2, "resume_verify")
problems = [
    f"现象：resume_verify 稳定为 create_verify 的约 2 倍（同探针 exec echo ok、只差锚点）。"
    f"volley p50：场景二 {rvv2:.2f} vs {cvv2:.2f}s（{rvv2 / cvv2:.1f}×）；场景一 {rvv1:.2f} vs {cvv1:.2f}s（{rvv1 / cvv1:.1f}×）。",
    "机理：create 返回时沙箱已 running，echo 只等 envd 命令通道；resume 的 connect 返回≠恢复完成，"
    "首条 echo 被平台 hold 到 paused 状态还原结束——恢复路径的「可用时刻」天然比 create 慢约一倍。",
    f"burst 对照（本轮新增，排除「全场对齐等待」后的结论）：同快照 burst 反而更长——resume_verify p50 {rvb1:.2f}s / max 18.7s，"
    f"高于 volley 的 {rvv1:.2f}s / 10.2s：同源楼梯是平台还原带宽固有的串行，独立流水持续占满还原通道；"
    f"异快照 burst 更顺——resume_verify p50 {rvb2:.2f}s，低于 volley 的 {rvv2:.2f}s：相位错开无瞬时还原争抢。"
    f"两个口径共同证明差距来自「还原本身」，不是测法 artifact。",
    "影响与建议：「暂停挂起→继续使用」的体验按 resume_verify 口径评估——分散快照约 0.4~0.9s、同快照 50 台 4.7~8.7s（长尾 10~19s）；"
    "平台侧可优化 paused 还原耗时或对 resume 做预热；批量交付继续按「n 台用 n 个不同快照」供给。",
]
for txt in problems:
    wide_text(ws, r, txt, 13)
    r += 1

# ── Sheet2 逐台明细 ─────────────────────────────────────────
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

r = 2
blocks = [("serial（队列深度=1）", piv_serial)] + [(n, p) for (n, _), p in zip(RUNS, pivs)]
for scen, piv in blocks:
    for i, (sid, d) in enumerate(sorted(piv.items(), key=lambda kv: kv[1]["idx"])):
        ref = d.get("ref", "")
        if ":" in ref:
            tpl, snap = ref.split(":", 1)
        else:
            tpl, snap = ref or "", "—（模板直拉）"
        fill = FILL_ALT if i % 2 else None
        cell(ws2, r, 1, scen, F_TXT, fill)
        cell(ws2, r, 2, i, F_TXT, fill, LEFT, "0")
        cell(ws2, r, 3, sid, F_TXT, fill)
        cell(ws2, r, 4, tpl, F_TXT, fill)
        cell(ws2, r, 5, snap, F_TXT, fill)
        for c, op in enumerate(OPS[:8], 6):
            v = op_val(d["ops"].get(op))
            f = FILL_BAD if isinstance(v, str) and v.startswith("fail") else fill
            cell(ws2, r, c, v, F_TXT, f, LEFT, NUMFMT if isinstance(v, float) else None)
        notes2 = []
        for op in OPS[:8]:
            t = d["ops"].get(op)
            if t and t[1] not in ("ok", "unsupported"):
                notes2.append(f"{op} {t[1]}: {t[2][:60]}")
            elif t and t[1] == "ok" and t[2] and t[2] != "attempt1":
                notes2.append(f"{op} {t[2]}")
        cell(ws2, r, 14, "", F_TXT, fill)
        cell(ws2, r, 15, "；".join(notes2), F_TXT, fill)
        r += 1

for i, t in enumerate(ttl_rows):
    det = t.get("detail", "")
    cs = det.split("create=")[1].split("s")[0] if "create=" in det else ""
    ref = det.split("ref=")[-1].strip() if "ref=" in det else ""
    tpl, snap = (ref.split(":", 1) + [""])[:2] if ":" in ref else (ref, "—")
    fill = FILL_ALT if i % 2 else None
    cell(ws2, r, 1, "TTL回收（TTL=120s 双确认）", F_TXT, fill)
    cell(ws2, r, 2, i, F_TXT, fill, LEFT, "0")
    cell(ws2, r, 3, t["sid"], F_TXT, fill)
    cell(ws2, r, 4, tpl, F_TXT, fill)
    cell(ws2, r, 5, snap, F_TXT, fill)
    cell(ws2, r, 6, round(float(cs), 3) if cs else "", F_TXT, fill, LEFT, NUMFMT)
    for c in range(7, 14):
        cell(ws2, r, c, "", F_TXT, fill)
    cell(ws2, r, 14, round(float(t["elapsed"]), 3), F_TXT_B, fill, LEFT, NUMFMT)
    cell(ws2, r, 15, f"status={t['status']}；到期→判死（清扫周期+60s）", F_TXT, fill)
    r += 1

wb.save(OUT)
print("OK detail rows =", r - 2)
print("OK fails:", {k: v for k, v in fails_by_run.items()})
print("OK written:", OUT)
print("OK size:", os.path.getsize(OUT))
