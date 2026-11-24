"""List all cn-north-4 ECS and flag any that live in the SFS Turbo's VPC."""
import json
import os
import urllib.request

from huaweicloudsdkcore.auth.credentials import BasicCredentials
from huaweicloudsdkcore.sdk_request import SdkRequest
from huaweicloudsdkcore.signer.signer import Signer

REGION = "cn-north-4"
SFS_VPC = "f3378f57-83d8-4234-b22d-4ade0019ac9e"   # vpc-sandbox-test
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
    SdkRequest("GET", "https", f"ecs.{REGION}.myhuaweicloud.com",
               f"/v1/{pid}/cloudservers/detail", query_params=[("limit", "200")],
               header_params={"X-Project-Id": pid}))
with urllib.request.urlopen(urllib.request.Request(
        f"https://{signed.host}{signed.uri}",
        headers=dict(signed.header_params)), timeout=20) as resp:
    servers = json.loads(resp.read()).get("servers", [])

print(f"ECS total: {len(servers)}")
for s in servers:
    for vpc_name, addrs in (s.get("addresses") or {}).items():
        fixed = [a["addr"] for a in addrs if a.get("OS-EXT-IPS:type") == "fixed"]
        fips = [a["addr"] for a in addrs if a.get("OS-EXT-IPS:type") == "floating"]
        in_vpc = " <== IN vpc-sandbox-test" if vpc_name == SFS_VPC else ""
        print(f"  {s.get('name')}  status={s.get('status')}  vpc={vpc_name}  "
              f"fixed={fixed}  eip={fips}{in_vpc}")
