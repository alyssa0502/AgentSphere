# -*- coding: utf-8 -*-
"""
50 台大文件读场景批量测试（创→测 全部完成后再统一 kill）
═══════════════════════════════════════════════════════════
流程：顺序执行 N 台（create → 就绪探测 → 读文件 → 打印），
      测试期间一律不销毁；全部测完后统一 kill 并打印汇总。
每台打印：create / envd / UI / chromium / 首读(MB/s) / 端到端。
明细同时写入 bench50_<时间戳>.txt。

用法：
  TPL=<模板ID> RD_FILE=/opt/bigfile-500mb.bin python bigfile_bench50.py 50
"""
import os
import sys
import time

# ── AgentSphere 三件套：从环境变量注入（控制台提供，勿提交密钥）──
for _k in ("E2B_API_URL", "E2B_SANDBOX_URL", "E2B_API_KEY"):
    if not os.environ.get(_k):
        sys.exit(f"缺少环境变量 {_k}（见 docs/04-脚本使用指南.md 的环境变量说明）")

from e2b import Sandbox  # noqa: E402

TEMPLATE = os.environ.get("TPL", "53319a80-cb18-48aa-993b-1c6f1634d0e6")
FILE = os.environ.get("RD_FILE", "/opt/bigfile-500mb.bin")
N = int(sys.argv[1]) if len(sys.argv) > 1 else 50

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

REPORT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           f"bench50_{time.strftime('%H%M%S')}.txt")
_rf = open(REPORT_PATH, "w", encoding="utf-8")


def emit(msg=""):
    print(msg)
    _rf.write(msg + "\n")
    _rf.flush()


# 就绪探测（UI + chromium，相对探测开始的毫秒）
READY = r'''
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

# 读文件 3 遍（F 注入；缺失也输出可解析行）
READ3 = r'''
F=''' + "'" + FILE + "'" + r'''
if [ ! -f "$F" ]; then echo "MISSING bytes=0 r1=-1 r2=-1 r3=-1"; else
SZ=$(stat -c %s "$F")
T0=$(date +%s%3N); cat "$F" > /dev/null 2>&1; T1=$(date +%s%3N)
T2=$(date +%s%3N); cat "$F" > /dev/null 2>&1; T3=$(date +%s%3N)
T4=$(date +%s%3N); cat "$F" > /dev/null 2>&1; T5=$(date +%s%3N)
echo "bytes=$SZ r1=$((T1-T0)) r2=$((T3-T2)) r3=$((T5-T4))"
fi
'''


def parse_kv(r):
    txt = (getattr(r, "stdout", "") or "") + (getattr(r, "stderr", "") or "")
    return dict(p.split("=", 1) for p in txt.split() if "=" in p), txt


def test_one(i):
    """create → 探测 → 读；不销毁。返回 (sb, rows)。"""
    rows = {"sid": None}
    t0 = time.monotonic()
    try:
        sb = Sandbox.create(template=TEMPLATE, timeout=3600)
    except Exception as e:
        emit(f"[{i}] ✗ create 失败: {str(e)[:120]}")
        rows["err"] = "create"
        return None, rows
    rows["sid"] = sb.sandbox_id
    rows["create"] = time.monotonic() - t0

    # envd 可执行
    t_c = time.monotonic()
    while time.monotonic() - t_c < 180:
        try:
            sb.commands.run("echo ok", user="root", cwd="/root", timeout=10)
            break
        except Exception:
            time.sleep(0.5)
    rows["envd"] = time.monotonic() - t0

    # 就绪探测
    try:
        kv, _ = parse_kv(sb.commands.run(READY, user="root", cwd="/root", timeout=300))
        rows["uptime"] = float(kv.get("uptime_s", -1))
        rows["ui"] = float(kv.get("ui_ms", -1)) / 1000
        rows["cr"] = float(kv.get("cr_ms", -1)) / 1000
    except Exception as e:
        rows["uptime"], rows["ui"], rows["cr"] = -1, -1, -1
        emit(f"    ✗ 就绪探测失败: {str(e)[:100]}")
    rows["ready"] = time.monotonic() - t0

    # 读文件
    try:
        kv2, txt2 = parse_kv(sb.commands.run(READ3, user="root", cwd="/root", timeout=600))
        rows["bytes"] = int(kv2.get("bytes", 0))
        rows["r1"] = int(kv2.get("r1", -1)) / 1000
        rows["r2"] = int(kv2.get("r2", -1)) / 1000
        rows["r3"] = int(kv2.get("r3", -1)) / 1000
        rows["missing"] = "MISSING" in txt2
    except Exception as e:
        rows["r1"] = -1
        emit(f"    ✗ 读文件失败: {str(e)[:100]}")

    rows["e2e"] = time.monotonic() - t0

    temp = "热池" if rows.get("uptime", -1) > 5 else "冷启"
    regime = ("缺文件" if rows.get("missing")
              else ("真冷读" if rows.get("r1", -1) > 20
                    else ("盘读" if rows.get("r1", -1) > 2
                          else ("页缓存" if rows.get("r1", -1) >= 0 else "读失败"))))
    emit(f"[{i}] {rows['sid']}  [{temp} uptime={rows.get('uptime', -1):.0f}s]")
    emit(f"    create {rows['create']:.2f}s  envd {rows['envd']:.2f}s  就绪 {rows['ready']:.2f}s")
    if rows.get("r1", -1) >= 0 and not rows.get("missing"):
        mb = rows["bytes"] / 1e6
        bw = mb / rows["r1"] if rows["r1"] > 0 else 0
        emit(f"    首读 {mb:.0f}MB/{rows['r1']:.2f}s={bw:.0f}MB/s  二读 {rows['r2']:.2f}s 三读 {rows['r3']:.2f}s  [{regime}]")
    emit(f"    ★ 端到端 {rows['e2e']:.2f}s")
    return sb, rows


if __name__ == "__main__":
    emit("═" * 64)
    emit(f" 50台大文件读批量  模板 {TEMPLATE[:8]}  文件 {FILE}")
    emit(f" 时间 {time.strftime('%Y-%m-%d %H:%M:%S')}  台数 {N}  测完统一kill")
    emit("═" * 64)

    t_start = time.monotonic()
    keep, results = [], []
    for i in range(N):
        sb, rows = test_one(i)
        if sb is not None:
            keep.append(sb)
        results.append(rows)

    t_tested = time.monotonic()
    emit("")
    emit("═" * 64)
    emit(f" 全部 {N} 台测完（耗时 {t_tested - t_start:.0f}s），开始统一销毁 {len(keep)} 台...")
    killed = 0
    for j, sb in enumerate(keep):
        try:
            sb.kill()
            killed += 1
        except Exception as e:
            emit(f"    ✗ kill {sb.sandbox_id[:8]} 失败: {str(e)[:80]}")
        if (j + 1) % 10 == 0:
            emit(f"    已销毁 {j + 1}/{len(keep)}")

    # 汇总
    emit("")
    emit("═" * 64)
    emit(" 汇总（按台序）")
    cold = disk = cache = other = 0
    for i, r in enumerate(results):
        if r.get("err"):
            emit(f"  [{i}] create失败")
            other += 1
            continue
        r1 = r.get("r1", -1)
        bw = (r["bytes"] / 1e6) / r1 if r1 and r1 > 0 else 0
        tag = ""
        if r.get("missing"):
            tag = "缺文件"
            other += 1
        elif r1 > 20:
            tag = "真冷读"
            cold += 1
        elif r1 > 2:
            tag = "盘读"
            disk += 1
        elif r1 >= 0:
            tag = "页缓存"
            cache += 1
        else:
            tag = "读失败"
            other += 1
        emit(f"  [{i:2d}] {r.get('sid', '?')[:8]}  首读 {r1:6.2f}s ({bw:4.0f}MB/s)"
             f"  端到端 {r.get('e2e', -1):6.2f}s  {tag}")
    emit("")
    emit(f" 分布: 真冷读(>20s) {cold} | 盘读(2-20s) {disk} | 页缓存(<2s) {cache} | 其他 {other}")
    r1s = [r["r1"] for r in results if r.get("r1", -1) >= 0]
    if r1s:
        r1s.sort()
        emit(f" 首读: min {min(r1s):.2f}s  p50 {r1s[len(r1s)//2]:.2f}s  max {max(r1s):.2f}s")
    e2es = [r["e2e"] for r in results if r.get("e2e")]
    if e2es:
        e2es.sort()
        emit(f" 端到端: min {min(e2es):.2f}s  p50 {e2es[len(e2es)//2]:.2f}s  max {max(e2es):.2f}s")
    emit(f" 总墙钟 {time.monotonic() - t_start:.0f}s  销毁 {killed}/{len(keep)}")
    emit(f" 报告: {REPORT_PATH}")
    _rf.close()
