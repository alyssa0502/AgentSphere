"""Discover IAM projects + SFS Turbo shares, using the official SDK signer.
AK/SK come from env only."""
import os
import sys
import json
import urllib.request

from huaweicloudsdkcore.auth.credentials import BasicCredentials
from huaweicloudsdkcore.sdk_request import SdkRequest
from huaweicloudsdkcore.signer.signer import Signer

REGIONS = ["cn-southwest-2", "cn-east-3"]

AK = os.environ["HWC_AK"]
SK = os.environ["HWC_SK"]


def send(req: SdkRequest, timeout=20):
    signed = Signer(BasicCredentials(AK, SK)).sign(req)
    url = f"{signed.schema}://{signed.host}{signed.uri}"
    if signed.query_params:
        from urllib.parse import urlencode
        url += "?" + urlencode(signed.query_params)
    r = urllib.request.Request(url, headers=dict(signed.header_params or {}),
                               method=signed.method)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8", "replace") or "{}")
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, raw


# 1) IAM projects
st, body = send(SdkRequest("GET", "https", "iam.myhuaweicloud.com",
                           "/v3/auth/projects", query_params=[], header_params={}))
print(f"IAM projects: HTTP {st}")
if st != 200:
    print(json.dumps(body, ensure_ascii=False)[:500])
    sys.exit(1)
pmap = {p["name"]: p["id"] for p in body.get("projects", [])}
print("regions:", ", ".join(sorted(pmap)))

# 2) SFS Turbo list endpoint probing
for region in REGIONS:
    pid = pmap.get(region)
    if not pid:
        print(f"\n== {region}: no project -> skip")
        continue
    print(f"\n== {region} project_id={pid}")
    creds = BasicCredentials(AK, SK, pid)
    for host in (f"sfs-turbo.{region}.myhuaweicloud.com", f"sfs.{region}.myhuaweicloud.com"):
        for path in (f"/v1/{pid}/sfs-turbo/shares/detail",):
            req = SdkRequest("GET", "https", host, path, query_params=[],
                             header_params={"X-Project-Id": pid})
            try:
                st, body = send(req, timeout=15)
            except Exception as e:
                print(f"  {host}: conn fail ({type(e).__name__})")
                break
            print(f"  GET {path} -> {st}")
            if st != 200 and isinstance(body, (dict, str)):
                print("      body:", json.dumps(body, ensure_ascii=False)[:300])
            if st == 200:
                shares = body.get("shares", body) if isinstance(body, dict) else body
                if isinstance(shares, list):
                    for s in shares:
                        print("    - " + json.dumps(
                            {k: s.get(k) for k in ("id", "name", "status", "az_name",
                                                   "availability_zone", "size",
                                                   "share_type", "export_location",
                                                   "vpc_id")},
                            ensure_ascii=False))
                else:
                    print("   ", json.dumps(body, ensure_ascii=False)[:800])
