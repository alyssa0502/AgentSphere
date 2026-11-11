# -*- coding: utf-8 -*-
"""
快照探测：通过 API 统计模板下有多少快照，并列出全部快照 ID
════════════════════════════════════════════════════════════════
原理（2026-11-11 探测实证）：
  平台的"快照" = 模板的 build。GET /templates/{模板ID} 已在网关发布，
  响应体里 builds[] 数组即该模板的全部快照：
    buildID + status(ready/error) + 创建/更新时间 + 规格(cpu/内存/盘)
  快照引用格式 = 模板ID:buildID（模板详情里的顶层 buildID 就是默认快照）。
  而 GET /snapshots、GET /templates、POST /sandboxes/{id}/snapshots 等
  在本网关未发布（APIGW.0101）——本脚本仍会顺带探测它们，一旦平台
  发布团队级列表接口会自动提示。

产出：
  - 模板元信息（names/description/spawnCount）
  - 快照总数 / 可用(ready)数 / 失败数
  - 全部快照 ID 清单 + 规格 + 创建时间
  - 可用快照的完整引用写进 --out 文件（直接 --snapshots @file 拉起）

用法（agentsphere-demo venv）：
  python snapshot_probe.py --template <模板ID>
  python snapshot_probe.py --template <模板ID> --all-builds     # 连 error 的也列出
  python snapshot_probe.py --template <模板ID> --out found.txt  # 引用写指定文件
"""
import argparse
import json
import os
import sys

os.environ.setdefault("E2B_API_URL",
                      "https://agentsandbox.cn-southwest-2.myhuaweicloud.com")
os.environ.setdefault("E2B_SANDBOX_URL",
                      "https://default-sandbox-gateway-mq4gcnrxgj.agentgateway"
                      ".cn-southwest-2.huaweicloud-agentnetwork.com")

import httpx  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE = os.path.dirname(os.path.abspath(__file__))


def side_probe(api, headers, tpl):
    """顺带探测 SDK 级列表接口是否已发布（团队级快照/模板列表）。"""
    print("── 旁路探测（团队级接口是否发布）──")
    for path, note in [("/snapshots", "SDK list_snapshots"),
                       ("/templates", "团队模板列表")]:
        try:
            r = httpx.get(api + path, headers=headers, timeout=15.0)
            if r.status_code == 200:
                print(f"  200  {path:<12} 已发布！{note} 可用了，可扩展本脚本做团队级盘点")
            else:
                tag = "未发布(APIGW.0101)" if b"APIGW" in r.content else f"HTTP {r.status_code}"
                print(f"  {r.status_code:>3}  {path:<12} {tag}")
        except Exception as e:
            print(f"  ERR  {path:<12} {type(e).__name__}")


def template_inventory(api, headers, tpl):
    r = httpx.get(f"{api}/templates/{tpl}", headers=headers, timeout=15.0)
    if r.status_code != 200:
        print(f"GET /templates/{tpl} -> HTTP {r.status_code}")
        print(r.text[:300])
        raise SystemExit("模板详情接口不可用（若为 APIGW.0101 表示该接口也被下线了）")
    d = json.loads(r.text)
    return d


def main():
    if not os.environ.get("E2B_API_KEY"):
        raise SystemExit("缺少 E2B_API_KEY 环境变量。"
                         "PowerShell:  $env:E2B_API_KEY='<你的key>'；"
                         "永久:  setx E2B_API_KEY <你的key>（重开终端生效）")

    ap = argparse.ArgumentParser(description="按模板统计快照数量并列出快照 ID")
    ap.add_argument("--template", default=os.environ.get(
        "TEMPLATE_ID", "92db8659-f980-4576-80ec-c7cf9b69348f"))
    ap.add_argument("--all-builds", action="store_true",
                    help="列出全部 build（含 error）；默认只列 ready")
    ap.add_argument("--out", default=os.path.join(BASE, "snapshots_found.txt"),
                    help="可用快照引用写出到此文件")
    args = ap.parse_args()

    api = os.environ["E2B_API_URL"].rstrip("/")
    headers = {"X-API-KEY": os.environ["E2B_API_KEY"]}
    tpl = args.template.strip()

    d = template_inventory(api, headers, tpl)
    builds = d.get("builds", []) or []
    ready = [b for b in builds if b.get("status") == "ready"]
    bad = [b for b in builds if b.get("status") != "ready"]

    names = ",".join(d.get("names") or [])
    print("══ 模板元信息 ══")
    print(f"  模板ID    {tpl}")
    print(f"  名称/描述 {names}  |  {d.get('description') or '-'}")
    print(f"  默认快照  {d.get('buildID')}（顶层 buildID = 平台发给你的那枚\"快照ID\"）")
    print(f"  创建      {d.get('createdAt')}   最近拉起 {d.get('lastSpawnedAt')}"
          f"   累计拉起 {d.get('spawnCount')} 次")

    print(f"\n══ 快照（=build）统计 ══")
    print(f"  总数 {len(builds)}   可用(ready) {len(ready)}   失败/其他 {len(bad)}")

    show = builds if args.all_builds else ready
    print(f"\n══ 快照清单（{'全部' if args.all_builds else '仅 ready'}，共 {len(show)} 条）══")
    for b in sorted(show, key=lambda x: x.get("createdAt") or ""):
        spec = f"{b.get('cpuCount')}C/{b.get('memoryMB') // 1024}G/{b.get('diskSizeMB') // 1024}G"
        print(f"  {b.get('buildID')}  {b.get('status'):<6} {b.get('createdAt')}"
              f"  {spec}  envd={b.get('envdVersion')}")

    out = os.path.abspath(args.out)
    with open(out, "w", encoding="utf-8") as f:
        for b in ready:
            f.write(f"{tpl}:{b['buildID']}\n")
    print(f"\n  可用引用 x{len(ready)} -> {out}")
    print(f"  下一步: python burst_create.py --snapshots @{os.path.basename(out)} --count 50")

    side_probe(api, headers, tpl)


if __name__ == "__main__":
    main()
