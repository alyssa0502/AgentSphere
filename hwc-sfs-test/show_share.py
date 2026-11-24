"""Show sfs-turbo-sandbox-test details (vpc_id etc.) in cn-north-4."""
import json
import os
import sys
import urllib.request

sys.path.insert(0, r"C:\Users\Administrator\hwc-sfs-test")
from huaweicloudsdkcore.auth.credentials import BasicCredentials
from huaweicloudsdkcore.sdk_request import SdkRequest
from huaweicloudsdkcore.signer.signer import Signer

REGION = "cn-north-4"
TARGET_NAME = "sfs-turbo-sandbox-test"
HOST = f"sfs-turbo.{REGION}.myhuaweicloud.com"
AK, SK = os.environ["HWC_AK"], os.environ["HWC_SK"]

signed = Signer(BasicCredentials(AK, SK)).sign(
    SdkRequest("GET", "https", "iam.myhuaweicloud.com", "/v3/auth/projects",
               query_params=[], header_params={}))
with urllib.request.urlopen(urllib.request.Request(
        f"https://{signed.host}{signed.uri}",
        headers=dict(signed.header_params)), timeout=20) as resp:
    projects = json.loads(resp.read())
pid = next(p["id"] for p in projects["projects"] if p["name"] == REGION)

signed = Signer(BasicCredentials(AK, SK, pid)).sign(
    SdkRequest("GET", "https", HOST, f"/v1/{pid}/sfs-turbo/shares/detail",
               query_params=[], header_params={"X-Project-Id": pid}))
with urllib.request.urlopen(urllib.request.Request(
        f"https://{signed.host}{signed.uri}",
        headers=dict(signed.header_params)), timeout=20) as resp:
    shares = json.loads(resp.read()).get("shares", [])
for s in shares:
    if s.get("name") == TARGET_NAME:
        print(json.dumps({k: s.get(k) for k in (
            "id", "name", "status", "size", "share_type", "az_name",
            "vpc_id", "subnet_id", "security_group_id", "export_location")},
            ensure_ascii=False, indent=2))
