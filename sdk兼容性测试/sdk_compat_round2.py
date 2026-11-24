# -*- coding: utf-8 -*-
r"""第二轮复核: list(limit)/PTY 读法/预签名URL真伪/update_network 形态"""
import os, re, sys, time, json
from pathlib import Path
from datetime import datetime, timezone

DEMO = Path(__file__).resolve().parent
SRC = (DEMO / 'create_snapshot_latest50_stats.py').read_text(encoding='utf-8')

def _after_value(varname, want):
    i = SRC.find(varname + ' ')
    win = SRC[i:i + 400]
    for m in re.finditer(r'["\']([^"\']+)["\']', win):
        if want in m.group(1) and m.group(1) != varname:
            return m.group(1)
    raise SystemExit(f'not found {varname}')

os.environ['E2B_API_URL'] = _after_value('E2B_API_URL', 'https://')
os.environ['E2B_SANDBOX_URL'] = _after_value('E2B_SANDBOX_URL', 'https://')
os.environ['E2B_API_KEY'] = _after_value('E2B_API_KEY', 'e2b_')
TPL = _after_value('TEMPLATE', '-')

from e2b import Sandbox, PtySize
import inspect, httpx

RES = {}
def out(k, v):
    RES[k] = v
    print(f'== {k}: {v}', flush=True)

sb = Sandbox.create(template=TPL, timeout=240)
try:
    # R1 list 带 limit
    try:
        pg = Sandbox.list(limit=20)
        items = None
        try:
            items = pg.next_items()
        except Exception as ex:
            out('R1_list_limit', f'next_items 异常 {type(ex).__name__}: {ex}'[:250])
        if items is not None:
            ids = [getattr(x, 'sandbox_id', '') for x in items]
            out('R1_list_limit', f'OK 共{len(items)}条 含当前={sb.sandbox_id in ids}')
    except Exception as ex:
        out('R1_list_limit', f'{type(ex).__name__}: {ex}'[:250])

    # R2 PTY 读法修正
    try:
        ph = sb.pty.create(user='root', cwd='/root', size=PtySize(cols=80, rows=24))
        try:
            w = getattr(ph, 'write', None)
            if w: w('echo pty-ok\r\n')
            txt = ''
            rd = getattr(ph, 'read', None)
            it = getattr(ph, '__iter__', None)
            t0 = time.time()
            if it is not None:
                try:
                    for chunk in ph:
                        txt += chunk if isinstance(chunk, str) else chunk.decode('utf-8', 'ignore')
                        if 'pty-ok' in txt or time.time() - t0 > 6: break
                except Exception as ex:
                    txt += f'<iter err {type(ex).__name__}>'
            if 'pty-ok' not in txt and rd is not None:
                while time.time() - t0 < 5 and 'pty-ok' not in txt:
                    r = rd(4096)
                    if not r: time.sleep(0.3); continue
                    txt += r if isinstance(r, str) else r.decode('utf-8', 'ignore')
            k2 = getattr(ph, 'kill', None)
            if k2: k2()
            out('R2_pty', f'含pty-ok={"pty-ok" in txt} 原始片段={txt[:160]!r}')
        except Exception as ex:
            out('R2_pty', f'{type(ex).__name__}: {ex}'[:250])
    except Exception as ex:
        out('R2_pty', f'create: {type(ex).__name__}: {ex}'[:250])

    # R3/R4 预签名 URL 真伪
    for name, meth in [('R3_download_url', 'download_url'), ('R4_upload_url', 'upload_url')]:
        try:
            u = getattr(sb, meth)('/root/x.txt')
            info = u if isinstance(u, str) else getattr(u, 'url', str(u))
            from urllib.parse import urlparse
            p = urlparse(info)
            host = p.netloc
            verb = 'HEAD'
            try:
                rr = httpx.request(verb, info if info.startswith('http') else f'https:{info}', timeout=8, follow_redirects=False)
                st = rr.status_code
            except Exception as ex:
                st = f'{type(ex).__name__}'
            out(name, f'host={host[:60]} 状态={st} (AgentSphere域={"agentsandbox" in host or "agentgateway" in host})')
        except Exception as ex:
            out(name, f'{type(ex).__name__}: {ex}'[:250])

    # R5 update_network 形态
    try:
        from e2b import SandboxNetworkOpts
        out('R5_opts_type', f'{type(SandboxNetworkOpts).__name__} ann={list(getattr(SandboxNetworkOpts, "__annotations__", {}).keys())}')
    except Exception as ex:
        out('R5_opts_type', f'import fail: {ex}')
    try:
        sig = inspect.signature(sb.update_network)
        out('R5_sig', str(sig))
    except Exception as ex:
        out('R5_sig', f'{ex}')
    for shape in [{'allow_internet_access': True}, {'allowInternetAccess': True}]:
        try:
            r = sb.update_network(shape)
            out('R5_update_network', f'dict 形态 OK: {r}')
            break
        except Exception as ex:
            out('R5_update_network', f'dict 形态: {type(ex).__name__}: {ex}'[:250])
finally:
    sb.kill()
    print('[done] cleaned', flush=True)

json.dump(RES, open(DEMO / 'sdk_compat_round2.json', 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
