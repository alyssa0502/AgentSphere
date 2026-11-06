#!/usr/bin/env python3
"""沙箱内执行：rootfs 导出 -> 单层 OCI(docker schema2) 镜像 -> 推 SWR 贵阳一。
凭据从环境变量 SWR_USER/SWR_PASS 读取。产物 /root/rootfs.tar.gz。"""
import gzip
import hashlib
import io
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone

import requests

REG = "https://swr.cn-southwest-2.myhuaweicloud.com"
REPO = "mcn-sandbox/pilotdeck-chromium"
TAG = os.environ.get("IMG_TAG", "20261105")
USER = os.environ.get("SWR_USER", "")
PASS = os.environ.get("SWR_PASS", "")
TAR = "/root/rootfs.tar"
GZ = "/root/rootfs.tar.gz"

if not USER or not PASS:
    sys.exit("SWR_USER/SWR_PASS not set")


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def sh(cmd):
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if r.returncode not in (0, 1):  # 1=tar 告警(文件读取中变化等)，可容忍
        log(f"cmd failed: {cmd}\n{r.stdout[-1500:]}\n{r.stderr[-1500:]}")
        sys.exit(1)
    if r.returncode == 1:
        log(f"   cmd rc=1 容忍: {r.stderr.strip()[-400:]}")
    return r.stdout.strip()


# ── 1) 打 rootfs tar ─────────────────────────────────────────────
log("1/7 tar rootfs (--one-file-system 自动排除虚拟挂载)...")
t0 = time.monotonic()
sh(f"tar -C / --numeric-owner --xattrs --acls --one-file-system "
   f"--exclude=./root/rootfs.tar --exclude=./root/rootfs.tar.gz "
   f"--exclude=./root/img_push.py --exclude=./root/.swr_env "
   f"-cpf {TAR} .")
sz = os.path.getsize(TAR)
log(f"   tar 完成 {sz/1e9:.2f} GB，{time.monotonic()-t0:.0f}s")

# ── 2) diff_id / gzip / digest ───────────────────────────────────
log("2/7 计算 diff_id (uncompressed sha256)...")
t0 = time.monotonic()
h = hashlib.sha256()
with open(TAR, "rb") as f:
    for chunk in iter(lambda: f.read(1 << 22), b""):
        h.update(chunk)
diff_id = "sha256:" + h.hexdigest()
log(f"   diff_id={diff_id[:30]}... {time.monotonic()-t0:.0f}s")

log("3/7 gzip -1 压缩...")
t0 = time.monotonic()
with open(TAR, "rb") as fi, open(GZ, "wb") as fo:
    with gzip.GzipFile(filename="", mode="wb", fileobj=fo, compresslevel=1, mtime=0) as gz:
        for chunk in iter(lambda: fi.read(1 << 22), b""):
            gz.write(chunk)
gz_size = os.path.getsize(GZ)
h = hashlib.sha256()
with open(GZ, "rb") as f:
    for chunk in iter(lambda: f.read(1 << 22), b""):
        h.update(chunk)
layer_digest = "sha256:" + h.hexdigest()
os.remove(TAR)
log(f"   layer={layer_digest[:30]}... size={gz_size/1e9:.2f} GB，{time.monotonic()-t0:.0f}s")

# ── 3) config + manifest ─────────────────────────────────────────
now = datetime.now(timezone.utc).isoformat()
config = {
    "architecture": "amd64",
    "os": "linux",
    "created": now,
    "config": {
        "Env": ["PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
                "HOME=/root", "LANG=C.UTF-8"],
        "Cmd": ["/sbin/init"],
        "WorkingDir": "/root",
        "Labels": {
            "org.opencontainers.image.title": "pilotdeck-chromium",
            "org.opencontainers.image.description": "AgentSphere 0db87f02(pilotdeck) rootfs + chromium",
            "org.opencontainers.image.created": now,
        },
    },
    "rootfs": {"type": "layers", "diff_ids": [diff_id]},
    "history": [{"created": now,
                 "created_by": "rootfs export: 0db87f02 + apt chromium fonts-liberation"}],
}
config_bytes = json.dumps(config, separators=(",", ":")).encode()
config_digest = "sha256:" + hashlib.sha256(config_bytes).hexdigest()

manifest = {
    "schemaVersion": 2,
    "mediaType": "application/vnd.docker.distribution.manifest.v2+json",
    "config": {"mediaType": "application/vnd.docker.container.image.v1+json",
               "size": len(config_bytes), "digest": config_digest},
    "layers": [{"mediaType": "application/vnd.docker.image.rootfs.diff.tar.gzip",
                "size": gz_size, "digest": layer_digest}],
}
manifest_bytes = json.dumps(manifest, separators=(",", ":")).encode()
manifest_digest = "sha256:" + hashlib.sha256(manifest_bytes).hexdigest()
log(f"4/7 manifest 构建完成 digest={manifest_digest[:30]}...")

# ── 4) SWR token ─────────────────────────────────────────────────
log("5/7 获取 SWR bearer token...")
scope = f"repository:{REPO}:pull,push"
tr = requests.get(f"{REG}/swr/auth/v2/registry/auth/",
                  params={"service": "dockyard", "scope": scope},
                  auth=(USER, PASS), timeout=60)
if tr.status_code != 200:
    sys.exit(f"token fetch {tr.status_code}: {tr.text[:300]}")
tok = tr.json().get("token") or tr.json().get("access_token")
if not tok:
    sys.exit(f"no token in response: {tr.text[:300]}")
H = {"Authorization": "Bearer " + tok}
log("   token OK")


def push_blob_bytes(data, digest):
    r = requests.post(f"{REG}/v2/{REPO}/blobs/uploads/", headers=H, timeout=120)
    if r.status_code != 202:
        sys.exit(f"upload init {r.status_code}: {r.text[:300]}")
    loc = r.headers.get("Location", "")
    if loc.startswith("/"):
        loc = REG + loc
    rr = requests.put(loc, params={"digest": digest}, data=data,
                      headers={**H, "Content-Type": "application/octet-stream"}, timeout=300)
    if rr.status_code not in (201, 202):
        sys.exit(f"blob put {rr.status_code}: {rr.text[:300]}")
    log(f"   blob {digest[:26]}... ({len(data)} B) 已上传")


def push_blob_file(path, digest, size):
    # 存在性检查：HEAD blob
    hr = requests.head(f"{REG}/v2/{REPO}/blobs/{digest}", headers=H, timeout=60)
    if hr.status_code == 200:
        log(f"   blob {digest[:26]}... 已存在，跳过")
        return
    r = requests.post(f"{REG}/v2/{REPO}/blobs/uploads/", headers=H, timeout=120)
    if r.status_code != 202:
        sys.exit(f"upload init {r.status_code}: {r.text[:300]}")
    loc = r.headers.get("Location", "")
    if loc.startswith("/"):
        loc = REG + loc
    t0 = time.monotonic()
    with open(path, "rb") as f:
        rr = requests.put(loc, params={"digest": digest}, data=f,
                          headers={**H, "Content-Type": "application/octet-stream"}, timeout=7200)
    if rr.status_code not in (201, 202):
        sys.exit(f"layer put {rr.status_code}: {rr.text[:500]}")
    wall = time.monotonic() - t0
    log(f"   layer {digest[:26]}... 上传 {size/1e9:.2f} GB / {wall:.0f}s = {size/1e6/max(wall,1):.1f} MB/s")


log("6/7 推送 blobs（config + layer）...")
push_blob_bytes(config_bytes, config_digest)
push_blob_file(GZ, layer_digest, gz_size)

log("7/7 PUT manifest ...")
mr = requests.put(f"{REG}/v2/{REPO}/manifests/{TAG}", data=manifest_bytes,
                  headers={**H, "Content-Type": "application/vnd.docker.distribution.manifest.v2+json"},
                  timeout=120)
if mr.status_code not in (201, 202):
    sys.exit(f"manifest put {mr.status_code}: {mr.text[:500]}")
log(f"   manifest {mr.status_code} OK")

# ── 5) 回读验证 ─────────────────────────────────────────────────
vr = requests.get(f"{REG}/v2/{REPO}/manifests/{TAG}",
                  headers={**H, "Accept": "application/vnd.docker.distribution.manifest.v2+json"},
                  timeout=60)
back = "sha256:" + hashlib.sha256(vr.content).hexdigest()
ok = back == manifest_digest and vr.status_code == 200
print(json.dumps({
    "image": f"{REG.replace('https://', '')}/{REPO}:{TAG}",
    "manifest_digest": manifest_digest,
    "layer": layer_digest, "layer_gb": round(gz_size / 1e9, 2),
    "verify": "PASS" if ok else f"FAIL({vr.status_code},{back[:20]})",
}, ensure_ascii=False))
os.remove(GZ)
log("完成，临时文件已清理")
