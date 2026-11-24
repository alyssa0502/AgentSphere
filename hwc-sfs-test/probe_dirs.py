"""Probe whether directories exist via ShowFsDir (there is NO list-dir API
in SFS Turbo; this probes by name pattern, 25 req/min rate limit respected).

Usage:
  python -I probe_dirs.py /haier/test 1 100      # probe /haier/test1..test100
  python -I probe_dirs.py /haier/test5           # probe a single path
Edit REGION / TARGET_NAME below to switch share."""
import json
import os
import sys
import time
import urllib.request

from huaweicloudsdkcore.auth.credentials import BasicCredentials
from huaweicloudsdkcore.sdk_request import SdkRequest
from huaweicloudsdkcore.signer.signer import Signer

REGION = "cn-north-4"
TARGET_NAME = "sfs-turbo-sandbox-test"
HOST = f"sfs-turbo.{REGION}.myhuaweicloud.com"
AK, SK = os.environ["HWC_AK"], os.environ["HWC_SK"]


def send(pid, method, path, query=None, timeout=20):
    signed = Signer(BasicCredentials(AK, SK, pid)).sign(
        SdkRequest(method, "https", HOST, path, query_params=query or [],
                   header_params={"X-Project-Id": pid}))
    uri = signed.uri
    if signed.query_params and "?" not in uri:
        from urllib.parse import urlencode
        uri += "?" + urlencode(signed.query_params)
    req = urllib.request.Request(f"https://{signed.host}{uri}",
                                 headers=dict(signed.header_params), method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


# project_id + share_id
signed = Signer(BasicCredentials(AK, SK)).sign(
    SdkRequest("GET", "https", "iam.myhuaweicloud.com", "/v3/auth/projects",
               query_params=[], header_params={}))
with urllib.request.urlopen(urllib.request.Request(
        f"https://{signed.host}{signed.uri}",
        headers=dict(signed.header_params)), timeout=20) as resp:
    projects = json.loads(resp.read())
pid = next(p["id"] for p in projects["projects"] if p["name"] == REGION)
_, raw = send(pid, "GET", f"/v1/{pid}/sfs-turbo/shares/detail")
hit = [s for s in json.loads(raw).get("shares", []) if s.get("name") == TARGET_NAME]
assert hit, f"share '{TARGET_NAME}' not found in {REGION}"
share_id = hit[0]["id"]

# build probe list from argv
args = sys.argv[1:]
if len(args) >= 3:
    prefix, lo, hi = args[0], int(args[1]), int(args[2])
    targets = [f"{prefix}{i}" for i in range(lo, hi + 1)]
elif len(args) == 1:
    targets = [args[0]]
else:
    print("usage: probe_dirs.py <prefix> <lo> <hi>  |  probe_dirs.py <single-path>")
    sys.exit(2)

exists, missing, other = [], [], []
for i, p in enumerate(targets):
    st, raw = send(pid, "GET", f"/v1/{pid}/sfs-turbo/shares/{share_id}/fs/dir",
                   query=[("path", p)])
    if st == 200:
        exists.append(p)
    elif st == 404:
        missing.append(p)
    else:
        other.append((p, st, raw[:100]))
    if i < len(targets) - 1:
        time.sleep(2.6)   # ShowFsDir: 25 req/min

print(f"share={TARGET_NAME}  probed={len(targets)}")
print(f"exists({len(exists)}): {', '.join(exists) if exists else '-'}")
if missing:
    print(f"missing({len(missing)}): {', '.join(missing[:20])}"
          + (" ..." if len(missing) > 20 else ""))
for p, st, raw in other:
    print(f"ERR {p}: {st} {raw}")
