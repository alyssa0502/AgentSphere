# -*- coding: utf-8 -*-
"""镜像加 500MB 随机大文件 → 新 tag 推 SWR（编排脚本，在本地跑）。
═══════════════════════════════════════════════════════════
机制：从模板拉起一个运行中沙箱 → 沙箱内 dd 写 500MB 数据文件 →
      上传 img_pusher_inner.py 到沙箱 → 沙箱内导出 rootfs 并直推 SWR。
凭据（SWR_USER/SWR_PASS 临时登录凭据）全部从环境变量注入，勿硬编码。

用法：
  IMG_TAG=20261105c SWR_USER=... SWR_PASS=... python img_push3.py
"""
import base64
import os
import sys
import time

for _k in ("E2B_API_URL", "E2B_SANDBOX_URL", "E2B_API_KEY"):
    if not os.environ.get(_k):
        sys.exit(f"缺少环境变量 {_k}")
SWR_USER = os.environ.get("SWR_USER", "")
SWR_PASS = os.environ.get("SWR_PASS", "")
if not SWR_USER or not SWR_PASS:
    sys.exit("缺少 SWR_USER / SWR_PASS（SWR 临时登录凭据，24h 有效）")

from e2b import Sandbox  # noqa: E402

TEMPLATE = "f6c0b17f-ba7a-49d2-b7e2-33854811b5c8"   # 源模板（watcher 版 chromium）
TAG = os.environ.get("IMG_TAG", "20261105c")
BIGFILE = "/opt/bigfile-500mb.bin"

t0 = time.monotonic()
sb = Sandbox.create(template=TEMPLATE, timeout=7200)
print(f"sandbox {sb.sandbox_id}  create {time.monotonic()-t0:.1f}s")

inner = open("img_pusher_inner.py", "rb").read()
b64 = base64.b64encode(inner).decode()
env_content = f"SWR_USER={SWR_USER}\nSWR_PASS={SWR_PASS}\nIMG_TAG={TAG}\n"
env_b64 = base64.b64encode(env_content.encode()).decode()

r = sb.commands.run(
    f"echo {b64} | base64 -d > /root/img_push.py && "
    f"echo {env_b64} | base64 -d > /root/.swr_env && chmod 600 /root/.swr_env && "
    f"pkill -f 'chromiu[m]'; sleep 1; "          # 杀浏览器防 tar 读变化；方括号防自匹配
    f"dd if=/dev/urandom of={BIGFILE} bs=1M count=500 2>&1 | tail -1; "
    f"ls -la {BIGFILE}",
    user="root", cwd="/root", timeout=120)
print("prep:", ((r.stdout or "") + (r.stderr or "")).strip()[-300:])

print(f"\n=== 导出 rootfs(含500MB) → 推 {TAG}（预计 5-8 分钟）===")
t1 = time.monotonic()
r = sb.commands.run(
    "cd /root && set -a && . /root/.swr_env && set +a && python3 /root/img_push.py",
    user="root", cwd="/root", timeout=2400)
print(((r.stdout or "") + (r.stderr or ""))[-3000:])
print(f"\nexit={r.exit_code}  wall={time.monotonic()-t1:.0f}s")
print(f"新镜像: swr.cn-southwest-2.myhuaweicloud.com/mcn-sandbox/pilotdeck-chromium:{TAG}")
print(f"大文件: {BIGFILE} (500MB urandom，只当数据，无进程会读它)")
