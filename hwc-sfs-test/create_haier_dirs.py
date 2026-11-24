"""cn-north-4: find share 'sfs-turbo-sandbox-test', create dirs by pattern.
Usage: python -I create_haier_dirs.py <prefix> <lo> <hi>
       (creates <prefix><lo>..<prefix><hi>; 409 = already exist = idempotent ok)
AK/SK from env only."""
import concurrent.futures
import json
import os
import sys
import urllib.request

from huaweicloudsdkcore.auth.credentials import BasicCredentials
from huaweicloudsdkcore.sdk_request import SdkRequest
from huaweicloudsdkcore.signer.signer import Signer

REGION = "cn-north-4"
TARGET_NAME = "sfs-turbo-sandbox-test"
HOST = f"sfs-turbo.{REGION}.myhuaweicloud.com"
AK, SK = os.environ["HWC_AK"], os.environ["HWC_SK"]

if len(sys.argv) != 4:
    print(__doc__)
    sys.exit(2)
PREFIX, LO, HI = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])


def send(pid, method, path, body_dict=None, timeout=20):
    body = json.dumps(body_dict).encode() if body_dict is not None else None
    hdrs = {"X-Project-Id": pid}
    if body:
        hdrs["Content-Type"] = "application/json;charset=UTF-8"
    req = SdkRequest(method, "https", HOST, path, query_params=[],
                     header_params=hdrs, body=body)
    signed = Signer(BasicCredentials(AK, SK, pid)).sign(req)
    r = urllib.request.Request(f"https://{signed.host}{signed.uri}",
                               data=body if body else None,
                               headers=dict(signed.header_params), method=method)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


# 1) project_id for cn-north-4
iam_req = SdkRequest("GET", "https", "iam.myhuaweicloud.com", "/v3/auth/projects",
                     query_params=[], header_params={})
signed = Signer(BasicCredentials(AK, SK)).sign(iam_req)
with urllib.request.urlopen(urllib.request.Request(
        f"https://{signed.host}{signed.uri}",
        headers=dict(signed.header_params)), timeout=20) as resp:
    projects = json.loads(resp.read())
pid = next(p["id"] for p in projects["projects"] if p["name"] == REGION)
print(f"project_id = {pid}")

# 2) find the share by name
st, raw = send(pid, "GET", f"/v1/{pid}/sfs-turbo/shares/detail")
assert st == 200, f"list shares failed: {st} {raw[:200]}"
shares = json.loads(raw).get("shares", [])
hit = [s for s in shares if s.get("name") == TARGET_NAME]
assert hit, f"share '{TARGET_NAME}' not found in {REGION}; existing: " + \
    ", ".join(s.get("name", "?") for s in shares)
s = hit[0]
share_id = s["id"]
print(f"share = {s.get('name')}  id={share_id}  status={s.get('status')}  "
      f"size={s.get('size')}GB  type={s.get('share_type')}  az={s.get('az_name')}")

# 3) create <prefix><lo>..<prefix><hi>
created, existed, failed = 0, 0, []


def mk(i):
    path = f"{PREFIX}{i}"
    st, raw = send(pid, "POST", f"/v1/{pid}/sfs-turbo/shares/{share_id}/fs/dir",
                   {"path": path, "mode": 755})
    return i, path, st, raw


with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
    for i, path, st, raw in ex.map(mk, range(LO, HI + 1)):
        if st == 204:
            created += 1
        elif st == 409:
            existed += 1
        else:
            failed.append((path, st, raw[:120]))
        if (created + existed + len(failed)) % 500 == 0:
            print(f"  progress: {created + existed + len(failed)}/{HI - LO + 1}")

print(f"done: created(204)={created}  already_exist(409)={existed}  failed={len(failed)}")
for path, st, raw in failed:
    print(f"  FAIL {path}: {st} {raw}")
