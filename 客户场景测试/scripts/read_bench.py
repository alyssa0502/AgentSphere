# -*- coding: utf-8 -*-
"""
沙箱大文件读对照测试 · 交互版（PyCharm 直接运行）
═══════════════════════════════════════════════════════════════
用法：运行后按提示输入——
  1) 模板 ID（必输，或回车用上次默认）
  2) 沙箱台数（回车 = 2）
  3) 要读的文件路径（回车 = /opt/bigfile-500mb.bin）

流程（与 50 台批量版一致）：
  顺序执行 N 台：create → envd → UI+浏览器就绪 → 首读/二读/三读 → 端到端
  测试期间一律不销毁；全部测完统一 kill，最后打印分布汇总。
  明细写入 readbench_<时间戳>.txt 和 .csv（脚本同目录）。

依赖：仅 e2b SDK。三件套环境变量从系统环境注入（勿硬编码密钥）。
"""
import csv
import os
import sys
import time

# ── AgentSphere 三件套：从环境变量注入（控制台提供，勿提交密钥）──
for _k in ("E2B_API_URL", "E2B_SANDBOX_URL", "E2B_API_KEY"):
    if not os.environ.get(_k):
        sys.exit(f"缺少环境变量 {_k}（见 docs/04-脚本使用指南.md 的环境变量说明）")

from e2b import Sandbox  # noqa: E402

LAST_TPL = "53319a80-cb18-48aa-993b-1c6f1634d0e6"   # 上次测过的模板
DEFAULT_N = 2
DEFAULT_FILE = "/opt/bigfile-500mb.bin"

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def ask(prompt, default):
    v = input(prompt).strip().lstrip("﻿")
    return v or default


# ── 就绪探测（UI 3001 + chromium 9222，相对探测开始的毫秒）──
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


def make_read3(file_path):
    """读文件 3 遍；F 注入；缺失也输出可解析行。"""
    return r'''
F=''' + "'" + file_path + "'" + r'''
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


class Bench:
    def __init__(self, tpl, file_path, n):
        self.tpl, self.file_path, self.n = tpl, file_path, n
        self.read3 = make_read3(file_path)
        base = os.path.dirname(os.path.abspath(__file__))
        ts = time.strftime("%Y%m%d_%H%M%S")
        self.txt_path = os.path.join(base, f"readbench_{ts}.txt")
        self.csv_path = os.path.join(base, f"readbench_{ts}.csv")
        self._txt = open(self.txt_path, "w", encoding="utf-8")
        self.keep = []      # 存活沙箱，测完统一 kill
        self.rows = []

    def emit(self, msg=""):
        print(msg)
        self._txt.write(msg + "\n")
        self._txt.flush()

    def test_one(self, i):
        """create → 探测 → 读；不销毁。"""
        rows = {"idx": i, "sid": "", "err": ""}
        t0 = time.monotonic()
        try:
            sb = Sandbox.create(template=self.tpl, timeout=3600)
        except Exception as e:
            rows["err"] = f"create失败: {str(e)[:100]}"
            self.emit(f"[{i}] ✗ {rows['err']}")
            self.rows.append(rows)
            return
        rows["sid"] = sb.sandbox_id
        rows["create_s"] = round(time.monotonic() - t0, 2)

        t_c = time.monotonic()
        while time.monotonic() - t_c < 180:
            try:
                sb.commands.run("echo ok", user="root", cwd="/root", timeout=10)
                break
            except Exception:
                time.sleep(0.5)
        rows["envd_s"] = round(time.monotonic() - t0, 2)

        rows["uptime_s"] = rows["ui_s"] = rows["cr_s"] = -1
        try:
            kv, _ = parse_kv(sb.commands.run(READY, user="root", cwd="/root", timeout=300))
            rows["uptime_s"] = float(kv.get("uptime_s", -1))
            rows["ui_s"] = round(float(kv.get("ui_ms", -1)) / 1000, 2)
            rows["cr_s"] = round(float(kv.get("cr_ms", -1)) / 1000, 2)
        except Exception as e:
            self.emit(f"    ✗ 就绪探测失败: {str(e)[:100]}")
        rows["ready_s"] = round(time.monotonic() - t0, 2)

        rows["bytes"] = rows["r1_s"] = rows["r2_s"] = rows["r3_s"] = -1
        try:
            kv2, txt2 = parse_kv(sb.commands.run(self.read3, user="root", cwd="/root", timeout=600))
            rows["bytes"] = int(kv2.get("bytes", 0))
            rows["r1_s"] = round(int(kv2.get("r1", -1)) / 1000, 2)
            rows["r2_s"] = round(int(kv2.get("r2", -1)) / 1000, 2)
            rows["r3_s"] = round(int(kv2.get("r3", -1)) / 1000, 2)
            if "MISSING" in txt2:
                rows["err"] = "文件不存在"
        except Exception as e:
            self.emit(f"    ✗ 读文件失败: {str(e)[:100]}")

        rows["e2e_s"] = round(time.monotonic() - t0, 2)
        rows["temp"] = "热池" if rows["uptime_s"] > 5 else "冷启"
        r1 = rows["r1_s"]
        rows["regime"] = ("缺文件" if rows["err"] == "文件不存在" else
                          "真冷读" if r1 > 20 else
                          "盘读" if r1 > 2 else
                          "页缓存" if r1 >= 0 else "读失败")
        mb = rows["bytes"] / 1e6 if rows["bytes"] > 0 else 0
        rows["mbps"] = round(mb / r1, 0) if r1 and r1 > 0 else 0

        self.emit(f"[{i}] {rows['sid']}  [{rows['temp']} uptime={rows['uptime_s']:.0f}s]")
        self.emit(f"    create {rows['create_s']}s  envd {rows['envd_s']}s  就绪 {rows['ready_s']}s")
        if rows["regime"] in ("真冷读", "盘读", "页缓存"):
            self.emit(f"    首读 {mb:.0f}MB/{rows['r1_s']}s={rows['mbps']:.0f}MB/s"
                      f"  二读 {rows['r2_s']}s 三读 {rows['r3_s']}s  [{rows['regime']}]")
        else:
            self.emit(f"    ✗ {rows['err'] or '读失败'}")
        self.emit(f"    ★ 端到端 {rows['e2e_s']}s")

        self.keep.append(sb)
        self.rows.append(rows)

    def kill_all(self):
        self.emit("")
        self.emit(f" 全部测完（测试耗时 {time.monotonic() - self.t0:.0f}s），"
                  f"开始统一销毁 {len(self.keep)} 台...")
        killed = 0
        remaining = []
        for j, sb in enumerate(self.keep):
            try:
                sb.kill()
                killed += 1
            except Exception as e:
                remaining.append(sb)
                self.emit(f"    ✗ kill {sb.sandbox_id[:8]} 失败: {str(e)[:80]}")
            if (j + 1) % 10 == 0:
                self.emit(f"    已销毁 {j + 1}/{len(self.keep)}")
        self.emit(f" 已销毁 {killed}/{len(self.keep)}")
        self.keep = remaining  # 只留销毁失败的，兜底再试

    def summary(self):
        self.emit("")
        self.emit("═" * 64)
        self.emit(" 汇总（按台序）")
        for r in self.rows:
            if r["err"] and r["regime"] not in ("真冷读", "盘读", "页缓存"):
                self.emit(f"  [{r['idx']:2d}] {r['sid'][:8] or '—':8}  ✗ {r['err']}")
                continue
            self.emit(f"  [{r['idx']:2d}] {r['sid'][:8]}  首读 {r['r1_s']:6.2f}s"
                      f" ({r['mbps']:4.0f}MB/s)  端到端 {r['e2e_s']:6.2f}s  {r['regime']}")
        dist = {}
        for r in self.rows:
            dist[r["regime"]] = dist.get(r["regime"], 0) + 1
        self.emit("")
        self.emit(" 分布: " + " | ".join(f"{k} {v}" for k, v in dist.items()))
        for key, label in (("r1_s", "首读"), ("e2e_s", "端到端")):
            vals = sorted(r[key] for r in self.rows if r[key] >= 0)
            if vals:
                self.emit(f" {label}: min {vals[0]:.2f}s  p50 {vals[len(vals)//2]:.2f}s"
                          f"  max {vals[-1]:.2f}s")
        self.emit(f" 总墙钟 {time.monotonic() - self.t0:.0f}s")
        self.emit(f" 报告: {self.txt_path}")
        self.emit(f" CSV : {self.csv_path}")

    def save_csv(self):
        cols = ["idx", "sid", "temp", "uptime_s", "create_s", "envd_s", "ready_s",
                "bytes", "r1_s", "mbps", "r2_s", "r3_s", "e2e_s", "regime", "err"]
        with open(self.csv_path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
            w.writeheader()
            for r in self.rows:
                w.writerow(r)

    def run(self):
        self.t0 = time.monotonic()
        self.emit("═" * 64)
        self.emit(f" 大文件读对照  模板 {self.tpl[:8]}  文件 {self.file_path}")
        self.emit(f" 时间 {time.strftime('%Y-%m-%d %H:%M:%S')}"
                  f"  台数 {self.n}（顺序测，测完统一kill）")
        self.emit("═" * 64)
        try:
            for i in range(self.n):
                self.test_one(i)
            self.kill_all()
            self.summary()
        finally:
            # 异常/Ctrl+C 兜底：已创建的沙箱也要销毁
            alive = [sb for sb in self.keep if sb is not None]
            if alive and self._txt is not None:
                self.emit(" [兜底] 清理未销毁沙箱...")
            for sb in alive:
                try:
                    sb.kill()
                except Exception:
                    pass
            self.save_csv()
            self._txt.close()


if __name__ == "__main__":
    print("─" * 64)
    tpl = ask(f"模板 ID（回车 = {LAST_TPL[:8]}…）: ", os.environ.get("TPL", LAST_TPL))
    n = ask(f"沙箱台数（回车 = {DEFAULT_N}）: ", str(os.environ.get("RD_N", DEFAULT_N)))
    fp = ask(f"要读的文件（回车 = {DEFAULT_FILE}）: ",
             os.environ.get("RD_FILE", DEFAULT_FILE))
    print("─" * 64)
    Bench(tpl, fp, int(n)).run()
