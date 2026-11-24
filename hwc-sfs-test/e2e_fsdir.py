"""E2E: CreateFsDir on sfs-turbo-sandbox (cn-southwest-2).
1) POST nested path  -> does it create parents in one call?
2) POST again        -> expect 409 path already exist (proves it exists)
3) DELETE both dirs  -> cleanup
AK/SK from env only."""
import json
import os
import sys
import urllib.request

from huaweicloudsdkcore.auth.credentials import BasicCredentials
from huaweicloudsdkcore.sdk_request import SdkRequest
from huaweicloudsdkcore.signer.signer import Signer

REGION = "cn-southwest-2"
PID = "b1cada1c234f4571a89274cced4861e0"
SHARE = "fea9ad8e-d1fe-11f1-b690-fa16446e05c1"   # sfs-turbo-sandbox
HOST = f"sfs-turbo.{REGION}.myhuaweicloud.com"
AK, SK = os.environ["HWC_AK"], os.environ["HWC_SK"]


def send(method, path, body_dict=None, timeout=20):
    body = json.dumps(body_dict).encode() if body_dict is not None else None
    hdrs = {"X-Project-Id": PID}
    if body:
        hdrs["Content-Type"] = "application/json;charset=UTF-8"
    req = SdkRequest(method, "https", HOST, path, query_params=[],
                     header_params=hdrs, body=body)
    signed = Signer(BasicCredentials(AK, SK, PID)).sign(req)
    url = f"https://{signed.host}{signed.uri}"
    r = urllib.request.Request(url, data=body if body else None,
                               headers=dict(signed.header_params), method=method)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def show(tag, st, raw):
    print(f"{tag}: HTTP {st}" + (f"  {raw[:200]}" if raw.strip() else ""))
    return st, raw


base = "/apitest_api_20261123"
sub = base + "/sub"

# 1) create (accept 204 new / 409 already exist from a previous run)
st, raw = send("POST", f"/v1/{PID}/sfs-turbo/shares/{SHARE}/fs/dir",
               {"path": sub, "mode": 755})
show("1) POST nested /apitest_api_20261123/sub", st, raw)
# 204 = created now; 409 = created by earlier run. Either way it exists.
# 2) duplicate create -> expect 409
st, raw = send("POST", f"/v1/{PID}/sfs-turbo/shares/{SHARE}/fs/dir",
               {"path": sub, "mode": 755})
show("2) POST same path again (expect 409)", st, raw)

# 3) cleanup: delete sub then base
for p in (sub, base):
    st, raw = send("DELETE", f"/v1/{PID}/sfs-turbo/shares/{SHARE}/fs/dir",
                   {"path": p})
    show(f"3) DELETE {p}", st, raw)
