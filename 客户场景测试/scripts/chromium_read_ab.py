# -*- coding: utf-8 -*-
"""
大文件冷读对照测试（含端到端拉起时延）· 环境变量参数版
═══════════════════════════════════════════════════════════════
严格顺序：第 1 台 拉起→就绪→读完文件 全部完成并计时后，才拉起第 2 台。

每台沙箱打印：
  create API / envd 可执行 / UI 3001 就绪 / chromium 9222 就绪（相对+累计）
  首读文件耗时（MB/s）+ 二三读（页缓存对照）
  端到端 = 发令 → UI+浏览器就绪 → 文件读完（客户端墙钟）

用法：
  TPL=<模板ID> RD_FILE=/opt/bigfile-500mb.bin python chromium_read_ab.py 2
环境变量：
  TPL      模板 ID（默认 f6c0b17f）
  RD_FILE  要读的文件（默认 /usr/lib/chromium/chromium）
  RD_N     沙箱台数（默认 2，或命令行第 1 参数）
"""
import os
import sys
import time

# ── AgentSphere 三件套：从环境变量注入（控制台提供，勿提交密钥）──
for _k in ("E2B_API_URL", "E2B_SANDBOX_URL", "E2B_API_KEY"):
    if not os.environ.get(_k):
        sys.exit(f"缺少环境变量 {_k}（见 docs/04-脚本使用指南.md 的环境变量说明）")

from e2b import Sandbox  # noqa: E402

TEMPLATE = os.environ.get("TPL", "f6c0b17f-ba7a-49d2-b7e2-33854811b5c8")
FILE = os.environ.get("RD_FILE", "/usr/lib/chromium/chromium")
N = int(sys.argv[1]) if len(sys.argv) > 1 else int(os.environ.get("RD_N", "2"))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# 就绪探测：uptime + UI + chromium（相对探测开始的毫秒）
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

# 读文件 3 遍（F 由客户端注入；文件缺失时也输出可解析行，不非零退出）
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


def one(i):
    rows = {}
    t0 = time.monotonic()
    sb = Sandbox.create(template=TEMPLATE, timeout=600)
    rows["create"] = time.monotonic() - t0
    t_c = time.monotonic()

    # envd 可执行（重试）
    while time.monotonic() - t_c < 180:
        try:
            sb.commands.run("echo ok", user="root", cwd="/root", timeout=10)
            break
        except Exception:
            time.sleep(0.5)
    rows["envd"] = time.monotonic() - t0

    # 就绪探测（UI + chromium）
    r = sb.commands.run(READY, user="root", cwd="/root", timeout=300)
    kv = dict(p.split("=", 1)
              for p in ((r.stdout or "") + (r.stderr or "")).split() if "=" in p)
    rows["uptime"] = float(kv.get("uptime_s", -1))
    rows["ui"] = float(kv.get("ui_ms", -1)) / 1000
    rows["cr"] = float(kv.get("cr_ms", -1)) / 1000
    t_ready = time.monotonic()          # 拉起就绪时刻（客户端）

    # 读文件
    try:
        r2 = sb.commands.run(READ3, user="root", cwd="/root", timeout=600)
        kv2 = dict(p.split("=", 1)
                   for p in ((r2.stdout or "") + (r2.stderr or "")).split() if "=" in p)
        rows["bytes"] = int(kv2.get("bytes", 0))
        rows["r1"] = int(kv2.get("r1", -1)) / 1000
        rows["r2"] = int(kv2.get("r2", -1)) / 1000
        rows["r3"] = int(kv2.get("r3", -1)) / 1000
        rows["missing"] = "MISSING" in ((r2.stdout or "") + (r2.stderr or ""))
    except Exception as e:
        rows["r1"] = -1
        print(f"    ✗ 读文件失败: {str(e)[:100]}")

    rows["t_read_done"] = time.monotonic()
    rows["e2e"] = rows["t_read_done"] - t0          # 发令→读完文件
    rows["up_to_ready"] = t_ready - t0              # 发令→拉起就绪

    mb = rows.get("bytes", 0) / 1e6
    temp = "热池" if rows["uptime"] > 5 else "冷启动"
    print(f"[{i}] 沙箱 {sb.sandbox_id}   [{temp} uptime={rows['uptime']:.0f}s]")
    print(f"    create API:      {rows['create']:.2f}s")
    print(f"    envd 可执行:      {rows['envd']:.2f}s（发令起算）")
    print(f"    UI 3001:         相对 {rows['ui']:.2f}s   累计自发令 {rows['envd'] + max(rows['ui'], 0):.2f}s")
    print(f"    chromium 9222:   相对 {rows['cr']:.2f}s   累计自发令 {rows['envd'] + max(rows['cr'], 0):.2f}s")
    print(f"    拉起就绪(发令→UI+浏览器): {rows['up_to_ready']:.2f}s")
    if rows.get("missing"):
        print(f"    ✗ 文件不存在: {FILE}")
    elif rows.get("r1", -1) >= 0:
        bw = mb / rows["r1"] if rows["r1"] > 0 else 0
        print(f"    首读 {mb:.0f}MB / {rows['r1']:.2f}s = {bw:.0f} MB/s   二读 {rows['r2']:.2f}s  三读 {rows['r3']:.2f}s")
    print(f"    ★ 端到端(发令→就绪→读完文件): {rows['e2e']:.2f}s")

    sb.kill()
    print("    （已销毁）")
    return rows


if __name__ == "__main__":
    print("═" * 64)
    print(f" 大文件读对照  模板 {TEMPLATE[:8]}  文件 {FILE}")
    print(f" 时间 {time.strftime('%Y-%m-%d %H:%M:%S')}   台数 {N}（严格顺序）")
    print("═" * 64)
    allrows = []
    for i in range(N):
        allrows.append(one(i))
    print("\n" + "═" * 64)
    print(" 汇总")
    for i, r in enumerate(allrows):
        mb = r.get("bytes", 0) / 1e6
        bw = mb / r["r1"] if r.get("r1", 0) > 0 else 0
        print(f"  [{i}] 就绪 {r['up_to_ready']:6.2f}s   首读 {r.get('r1', -1):6.2f}s"
              f" ({bw:5.0f} MB/s)   端到端 {r['e2e']:6.2f}s   "
              f"{'热池' if r['uptime'] > 5 else '冷启动'}")
