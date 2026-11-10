# -*- coding: utf-8 -*-
"""
快照准备工具：给 burst_create.py 造/收集快照引用
════════════════════════════════════════════════════════════════
三种用法：
  1) 现做快照   python make_snapshots.py --template <tpl> --count 5
     循环 N 轮：创建沙箱 ->（可选 --warm-cmd 预热）-> create_snapshot
     -> 记录 snapshot_id -> 销毁沙箱。结果写 --out（默认 snapshots.txt）。
  2) 盘点已有   python make_snapshots.py --list-only
     列出账号下全部快照引用，同样写 --out，可直接喂给 burst_create。
  3) 验证引用   python make_snapshots.py --verify @snapshots.txt
     用第一个引用真实拉起一台，读 uptime 判冷热，确认引用可用。

注意：create_snapshot 会先暂停沙箱再快照；快照持久化，沙箱删了也在。
"""
import argparse
import os
import sys
import time

os.environ.setdefault("E2B_API_URL",
                      "https://agentsandbox.cn-southwest-2.myhuaweicloud.com")
os.environ.setdefault("E2B_SANDBOX_URL",
                      "https://default-sandbox-gateway-mq4gcnrxgj.agentgateway"
                      ".cn-southwest-2.huaweicloud-agentnetwork.com")

from e2b import Sandbox  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE = os.path.dirname(os.path.abspath(__file__))


def load_refs(spec):
    if spec.startswith("@"):
        with open(spec[1:], encoding="utf-8") as f:
            return [l.strip() for l in f if l.strip() and not l.strip().startswith("#")]
    return [s.strip() for s in spec.split(",") if s.strip()]


def write_refs(path, refs):
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(refs) + "\n")
    print(f"\n{len(refs)} 个引用 -> {os.path.abspath(path)}")
    for r in refs:
        print(f"  {r}")


def do_create(args):
    refs = []
    for i in range(args.count):
        print(f"\n── [{i + 1}/{args.count}] 创建沙箱 ...", flush=True)
        t0 = time.monotonic()
        sb = Sandbox.create(template=args.template, timeout=600)
        print(f"    {sb.sandbox_id}  create={time.monotonic() - t0:.2f}s")
        try:
            if args.warm_cmd:
                r = sb.commands.run(args.warm_cmd, user="root", cwd="/root",
                                    timeout=args.warm_timeout)
                print(f"    warm: exit={r.exit_code} {(r.stdout or '')[:120]}")
            t1 = time.monotonic()
            info = sb.create_snapshot()
            refs.append(info.snapshot_id)
            print(f"    snapshot_id={info.snapshot_id}  耗时={time.monotonic() - t1:.1f}s")
        finally:
            try:
                sb.kill()
            except Exception as e:
                print(f"    kill 失败: {e!r}")
    return refs


def do_list():
    refs, seen = [], set()
    paginator = Sandbox.list_snapshots(limit=100)
    while True:
        for item in paginator.next_items():
            sid = getattr(item, "snapshot_id", None) or str(item)
            if sid and sid not in seen:
                seen.add(sid)
                refs.append(sid)
        if not paginator.has_next():
            break
        paginator.next_page()
    return refs


def do_verify(refs):
    ref = refs[0]
    print(f"\n验证引用 {ref} ...")
    t0 = time.monotonic()
    sb = Sandbox.create(template=ref, timeout=600)
    print(f"  create={time.monotonic() - t0:.2f}s  sandbox={sb.sandbox_id}")
    r = sb.commands.run("cut -d' ' -f1 /proc/uptime", user="root", cwd="/root", timeout=30)
    up = float((r.stdout or "0").strip() or 0)
    print(f"  uptime={up:.0f}s -> {'热池命中（快照已预热）' if up > 5 else '冷启动'}")
    try:
        sb.kill()
    except Exception:
        pass


def main():
    if not os.environ.get("E2B_API_KEY"):
        raise SystemExit("缺少 E2B_API_KEY 环境变量。"
                         "PowerShell 临时设置:  $env:E2B_API_KEY='<你的key>'；"
                         "永久:  setx E2B_API_KEY <你的key>（重开终端生效）")

    ap = argparse.ArgumentParser(description="快照准备工具")
    ap.add_argument("--template", default=os.environ.get(
        "TEMPLATE_ID", "92db8659-f980-4576-80ec-c7cf9b69348f"))
    ap.add_argument("--count", type=int, default=5, help="现做快照数量")
    ap.add_argument("--warm-cmd", default="", help="快照前在沙箱内执行的预热命令")
    ap.add_argument("--warm-timeout", type=int, default=300)
    ap.add_argument("--list-only", action="store_true", help="只盘点账号已有快照")
    ap.add_argument("--verify", default="", help="验证引用可用性（逗号分隔或 @file）")
    ap.add_argument("--out", default=os.path.join(BASE, "snapshots.txt"))
    args = ap.parse_args()

    if args.verify:
        do_verify(load_refs(args.verify))
    elif args.list_only:
        refs = do_list()
        print(f"账号共有 {len(refs)} 个快照引用")
        write_refs(args.out, refs)
    else:
        refs = do_create(args)
        write_refs(args.out, refs)
        print("\n下一步: python burst_create.py --snapshots @snapshots.txt --count 50")


if __name__ == "__main__":
    main()
