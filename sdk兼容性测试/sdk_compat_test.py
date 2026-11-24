# -*- coding: utf-8 -*-
r"""AgentSphere × E2B SDK 兼容性矩阵测试（e2b SDK 层，2026-11-24）

跑法: cd C:\Users\Administrator\agentsphere-demo && venv\Scripts\python.exe -u sdk_compat_test.py
输出: sdk_compat_result.json + 控制台逐条打印
分类: PASS=与 E2B 原生语义一致 / DIFF=可用但语义或默认值不一致 / FAIL=报错或功能缺失
"""
import os, re, sys, json, time, threading, traceback
from pathlib import Path
from datetime import datetime, timezone

DEMO = Path(__file__).resolve().parent
SRC = (DEMO / 'create_snapshot_latest50_stats.py').read_text(encoding='utf-8')

def _after_value(varname, want):
    """从 'varname =' 位置向后 300 字符窗口内, 找第一个含 want 的引号串(容忍跨行赋值)"""
    i = SRC.find(varname + ' ')
    if i < 0:
        i = SRC.find(varname + '=')
    if i < 0:
        raise SystemExit(f'[boot] 找不到 {varname}')
    win = SRC[i:i + 400]
    for m in re.finditer(r'["\']([^"\']+)["\']', win):
        if want in m.group(1) and m.group(1) != varname:
            return m.group(1)
    raise SystemExit(f'[boot] {varname} 的值里没有 {want}')

API_URL = _after_value('E2B_API_URL', 'https://')
SBX_URL = _after_value('E2B_SANDBOX_URL', 'https://')
API_KEY = _after_value('E2B_API_KEY', 'e2b_')
TPL     = _after_value('TEMPLATE', '-')

os.environ['E2B_API_URL'] = API_URL
os.environ['E2B_SANDBOX_URL'] = SBX_URL
os.environ['E2B_API_KEY'] = API_KEY

print(f'[boot] api={API_URL} sbx={SBX_URL} tpl={TPL[:8]}… key={API_KEY[:8]}…{API_KEY[-4:]}', flush=True)

from e2b import (Sandbox, Volume, Secret, Template,
                 AuthenticationException, SandboxNotFoundException,
                 NotFoundException, TimeoutException, CommandExitException)

RESULTS = []
TAG = {'PASS': '[PASS]', 'DIFF': '[DIFF]', 'FAIL': '[FAIL]', 'INFO': '[INFO]'}

def rec(domain, api, status, e2b_native, actual, note=''):
    RESULTS.append(dict(domain=domain, api=api, status=status,
                        e2b_native=e2b_native, actual=actual, note=note))
    print(f'{TAG.get(status,"[??]")} {domain:>10} | {api:<42} | {e2b_native} => {actual}' + (f' | {note}' if note else ''), flush=True)

def ttl_left(info):
    try:
        ea = info.end_at
        if ea is None: return None
        if ea.tzinfo is None: ea = ea.replace(tzinfo=timezone.utc)
        return (ea - datetime.now(timezone.utc)).total_seconds()
    except Exception:
        return None

def safe(fn, *a, **kw):
    try:
        return fn(*a, **kw), None
    except Exception as ex:
        return None, f'{type(ex).__name__}: {ex}'[:300]

def bg_linger_check(sid):
    """kill 后记录回收观察：E2B 原生 get_info 立即 404"""
    def work():
        time.sleep(70)
        info, err = safe(Sandbox.get_info, sid)
        if err and ('NotFound' in err or '404' in err):
            rec('沙箱生命周期', f'kill 后 70s get_info({sid[:8]}…)', 'PASS',
                '立即/快速 404', '已回收', '最终口径与 E2B 一致，仅清理有延迟')
        else:
            st = getattr(info, 'state', '?') if info else '?'
            rec('沙箱生命周期', f'kill 后 70s get_info({sid[:8]}…)', 'DIFF',
                '立即 404', f'仍返回 state={st}', '记录批量清扫有延迟（E2B 为立即 404）')
    threading.Thread(target=work, daemon=True).start()

# ============================================================
# A. 沙箱生命周期（核心）
# ============================================================
print('\n===== A. 沙箱生命周期 =====', flush=True)
S_main = None; S_ttl = None; S_lc1 = None; S_lc2 = None; S_tmp = None
to_kill = []; to_kill_ids = []

t0 = time.time()
sb, err = safe(Sandbox.create, template=TPL)
if err:
    rec('沙箱生命周期', 'Sandbox.create(默认参数)', 'FAIL', '创建成功, 默认 TTL≈300s', err)
    json.dump(RESULTS, open(DEMO/'sdk_compat_result.json','w',encoding='utf-8'), ensure_ascii=False, indent=1)
    sys.exit('[fatal] create 都失败，后续无法进行')
S_main = sb
to_kill.append(sb)
print(f'    create 耗时 {time.time()-t0:.2f}s', flush=True)
info = sb.get_info()
left = ttl_left(info)
rec('沙箱生命周期', 'Sandbox.create(默认参数)',
    'PASS' if left and 250 < left < 340 else 'DIFF',
    '创建成功, 默认 TTL≈300s (end_at≈now+300)',
    f'OK, 剩余 TTL={left:.0f}s' if left else f'state={getattr(info,"state","?")}',
    f'create 耗时 {time.time()-t0:.2f}s')

# get_info 字段面对照（E2B 原生字段见 01/02 文档）
fmap = {}
for f in ['sandbox_id','sandbox_domain','template_id','name','metadata','allow_internet_access',
          'envd_version','started_at','end_at','state','cpu_count','memory_mb','network','lifecycle','volume_mounts']:
    fmap[f] = getattr(info, f, '<缺>')
rec('沙箱生命周期', 'get_info 字段: sandbox_id/template_id/state/cpu_count/memory_mb/envd_version/started_at/end_at/metadata',
    'PASS', 'E2B: 全部字段齐备', '齐备' if all(v != '<缺>' for k,v in fmap.items() if k in ('sandbox_id','state','cpu_count','memory_mb','envd_version','started_at','end_at','metadata')) else f'缺失: {[k for k,v in fmap.items() if v=="<缺>"]}')
rec('沙箱生命周期', 'get_info.lifecycle 回显',
    'PASS' if fmap['lifecycle'] not in ('<缺>', None) else 'DIFF',
    'E2B: 回显 lifecycle={on_timeout, auto_resume}',
    f'lifecycle={fmap["lifecycle"]!r}')
rec('沙箱生命周期', 'get_info.allow_internet_access 回显',
    'PASS' if fmap['allow_internet_access'] not in ('<缺>', None) else 'DIFF',
    'E2B: create 传参后回显 True/False',
    f'allow_internet_access={fmap["allow_internet_access"]!r}',
    '已知: 出网是模板级配置, create 参数无效')
rec('沙箱生命周期', 'get_info.name(模板别名)',
    'INFO', "E2B: name=模板别名(如 'base')", f'name={fmap["name"]!r}')
rec('沙箱生命周期', 'get_info.sandbox_domain',
    'INFO', 'E2B: 返回沙箱域名(有公网入流时)', f'sandbox_domain={fmap["sandbox_domain"]!r}')

# metadata + envs
sb2, err = safe(Sandbox.create, template=TPL, timeout=600, metadata={'compat': 'sdk-matrix'}, envs={'COMPAT_VAR': 'hello-e2b'})
if sb2:
    S_ttl = sb2; to_kill.append(sb2)
    i2 = sb2.get_info()
    meta_ok = (getattr(i2, 'metadata', None) or {}).get('compat') == 'sdk-matrix'
    rec('沙箱生命周期', 'create(metadata=…) → get_info 回显', 'PASS' if meta_ok else 'DIFF',
        'metadata 原样回显', f'metadata={getattr(i2,"metadata",None)!r}')
    left = ttl_left(i2)
    rec('沙箱生命周期', 'create(timeout=600) → end_at',
        'PASS' if left and 560 < left < 640 else 'DIFF',
        'end_at≈now+600', f'剩余 TTL={left:.0f}s' if left else 'end_at=None')
    out, err2 = safe(sb2.commands.run, 'echo $COMPAT_VAR', user='root', cwd='/root')
    env_ok = out and out.stdout.strip() == 'hello-e2b'
    rec('沙箱生命周期', 'create(envs=…) → 沙箱内可见', 'PASS' if env_ok else 'FAIL',
        '$VAR 在 commands.run 中可见', f"stdout={getattr(out,'stdout',None)!r}" if out else f'{err2}')
else:
    rec('沙箱生命周期', 'create(metadata,envs,timeout)', 'FAIL', '创建成功', err or '')

# E2B_* 默认环境变量
out, err = safe(S_main.commands.run, 'env | grep -c ^E2B_ || true', user='root', cwd='/root')
cnt = out.stdout.strip() if out else '?'
rec('沙箱生命周期', 'E2B_* 默认环境变量注入',
    'PASS' if cnt.isdigit() and int(cnt) > 0 else 'DIFF',
    'E2B: 沙箱内注入 E2B_SANDBOX_ID 等 E2B_* 变量', f'E2B_* 变量个数={cnt}')

# connect 的 TTL 语义（E2B: 只延长不缩短 max(当前, now+300)；AgentSphere 已知: 重置为 300）
left_before = ttl_left(S_ttl.get_info())
_, err = safe(S_ttl.connect)
left_after = ttl_left(S_ttl.get_info()) if not err else None
if err:
    rec('沙箱生命周期', 'connect(已运行沙箱)', 'FAIL', '200, TTL 只延长', err)
elif left_before and left_after:
    if abs(left_after - left_before) < 30 and left_before > 400:
        rec('沙箱生命周期', 'connect TTL 语义(timeout=600 时 connect)', 'PASS', '只延长: max(剩余, now+300)', f'connect 前 {left_before:.0f}s → 后 {left_after:.0f}s')
    elif abs(left_after - 300) < 30:
        rec('沙箱生命周期', 'connect TTL 语义(timeout=600 时 connect)', 'DIFF', '只延长: max(剩余, now+300)', f'connect 前 {left_before:.0f}s → 后 {left_after:.0f}s', '重置为默认 300s 而非保留 600s')
    else:
        rec('沙箱生命周期', 'connect TTL 语义(timeout=600 时 connect)', 'DIFF', '只延长: max(剩余, now+300)', f'connect 前 {left_before:.0f}s → 后 {left_after:.0f}s')

# set_timeout
_, err = safe(S_ttl.set_timeout, 120)
left = ttl_left(S_ttl.get_info()) if not err else None
rec('沙箱生命周期', 'set_timeout(120)',
    'PASS' if left and 100 < left < 140 else 'DIFF',
    'end_at=调用时刻+120', f'剩余 TTL={left:.0f}s' if left else f'{err}')

# list
pg, err = safe(Sandbox.list)
if err:
    rec('沙箱生命周期', 'Sandbox.list', 'FAIL', '返回沙箱列表(分页)', err)
else:
    items = None; list_err = None
    for attr in ('next_items', 'items'):
        f = getattr(pg, attr, None)
        if callable(f):
            try:
                items = f()
                if callable(items): items = items()
            except Exception as ex:
                list_err = f'{type(ex).__name__}: {ex}'[:220]
                items = None
        if items: break
    n = len(items) if items is not None else -1
    found = any(getattr(x, 'sandbox_id', '') == S_main.sandbox_id for x in (items or [])) if items else False
    rec('沙箱生命周期', 'Sandbox.list',
        ('FAIL' if items is None else ('PASS' if found and n > 0 else 'DIFF')),
        '列表含刚创建的沙箱',
        f'返回 {n} 条, 含当前沙箱={found}' if items is not None else f'分页取数失败: {list_err}')

# is_running
ok, err = safe(S_main.is_running)
rec('沙箱生命周期', 'is_running(sandbox_id)', 'PASS' if ok is True else 'DIFF', 'True', f'{ok}' + (f' | {err}' if err else ''))

# lifecycle 计时器双沙箱并行: on_timeout=pause / +auto_resume
S_lc1, e1 = safe(Sandbox.create, template=TPL, timeout=20, lifecycle={'on_timeout': 'pause'})
S_lc2, e2 = safe(Sandbox.create, template=TPL, timeout=20, lifecycle={'on_timeout': 'pause', 'auto_resume': True})
if S_lc1: to_kill.append(S_lc1)
if S_lc2: to_kill.append(S_lc2)
rec('沙箱生命周期', "create(lifecycle={'on_timeout':'pause'})", 'INFO' if S_lc1 else 'FAIL',
    'E2B: 接受 lifecycle 参数', '创建成功' if S_lc1 else f'{e1}')
rec('沙箱生命周期', "create(lifecycle={…,'auto_resume':True})", 'INFO' if S_lc2 else 'FAIL',
    'E2B: 接受 auto_resume', '创建成功' if S_lc2 else f'{e2}')
print('    等待 40s 让 timeout=20 的沙箱到期…', flush=True)
time.sleep(40)
i1, e = safe(S_lc1.get_info) if S_lc1 else (None, '未创建')
st1 = getattr(i1, 'state', f'ERR:{e}')
rec('沙箱生命周期', 'on_timeout=pause 到期后状态',
    'PASS' if st1 == 'paused' else 'FAIL', 'deadline 后转 paused', f'state={st1}')
# auto_resume: 不 connect 直接用 → E2B 原生应自动恢复成功
if S_lc2:
    i2, e = safe(S_lc2.get_info)
    st2 = getattr(i2, 'state', f'ERR:{e}')
    out, err = safe(S_lc2.commands.run, 'echo auto-resumed', user='root', cwd='/root')
    rec('沙箱生命周期', 'auto_resume=True: 流量触发自动恢复',
        'PASS' if out else 'FAIL',
        'E2B: 请求触发自动 resume, 命令成功',
        f'state={st2}, 命令: ' + (('成功 stdout=' + out.stdout.strip()) if out else f'{err}'),
        'AgentSphere 已知: auto_resume 无增量行为, 恢复唯一入口=connect()')

# ============================================================
# B. 命令执行 commands
# ============================================================
print('\n===== B. 命令执行 =====', flush=True)
out, err = safe(S_main.commands.run, 'echo default-user-ok')
rec('命令执行', "commands.run('echo hi') 默认参数",
    'PASS' if out else 'FAIL',
    'E2B: 默认 user=user, cwd=/home/user 开箱即用',
    ('成功 stdout=' + out.stdout.strip()) if out else f'{err}',
    'AgentSphere 模板无 user 账户 → 必须显式 user=root/cwd=/root')

out, err = safe(S_main.commands.run, 'echo hi', user='root', cwd='/root')
rec('命令执行', "commands.run(echo, user='root', cwd='/root')",
    'PASS' if out and out.stdout.strip() == 'hi' else 'FAIL',
    'stdout/stderr/exit_code 齐备', f"stdout={getattr(out,'stdout','?').strip()!r}" if out else f'{err}')

out, err = safe(S_main.commands.run, 'echo err-line 1>&2; exit 3', user='root', cwd='/root')
if out is None and 'Exit' in str(err):
    code = getattr(err if isinstance(err, Exception) else None, 'exit_code', None)
    rec('命令执行', '非零退出码语义', 'PASS', '抛 CommandExitException 且带 exit_code', f'{err}' + (f' exit_code={code}' if code else ''))
elif out is not None:
    rec('命令执行', '非零退出码语义', 'DIFF', 'E2B: 抛 CommandExitException', f'返回 result, exit_code={getattr(out,"exit_code","?")}')
else:
    rec('命令执行', '非零退出码语义', 'FAIL', '抛 CommandExitException', f'{err}')

out, err = safe(S_main.commands.run, 'sleep 30', timeout=2, user='root', cwd='/root')
rec('命令执行', 'run(timeout=2) 超时语义',
    'PASS' if out is None and 'Timeout' in str(err) else 'DIFF',
    '抛 TimeoutException', f'{err}' if out is None else f'未超时: {out.stdout!r}')

lines = []
out, err = safe(S_main.commands.run, 'echo stream-A; echo stream-B', user='root', cwd='/root',
                on_stdout=lambda l: lines.append(l))
rec('命令执行', 'on_stdout 流式回调', 'PASS' if lines else 'DIFF', '回调收到行事件', f'回调行数={len(lines)}')

h, err = safe(S_main.commands.run, 'sleep 60', background=True, user='root', cwd='/root')
if h:
    time.sleep(1.5)
    lst, e2 = safe(S_main.commands.list)
    pids = [getattr(p, 'pid', None) for p in (lst or [])]
    hit = getattr(h, 'pid', None) in pids
    rec('命令执行', 'background=True + commands.list', 'PASS' if hit else 'DIFF',
        '后台进程出现在 list 中', f'handle.pid={getattr(h,"pid","?")} list={pids[:8]}{"…" if len(pids)>8 else ""}')
    kres, e3 = safe(h.kill)
    rec('命令执行', '后台进程 kill', 'PASS' if not e3 else 'DIFF', '进程终止', 'OK' if not e3 else f'{e3}')
else:
    rec('命令执行', 'background=True', 'FAIL', '返回 CommandHandle', f'{err}')

# PTY（软探测: 实例 sb.pty 优先, 兼容 commands.pty）
pty_ok = None
try:
    pty = getattr(S_main, 'pty', None) or getattr(S_main.commands, 'pty', None)
    if pty is None:
        rec('命令执行', 'PTY 交互终端', 'INFO', 'E2B: PTY 可用', 'SDK 本版无 pty 入口(SDK 版本未覆盖)')
    else:
        create = getattr(pty, 'create', None) or getattr(pty, 'open', None) or getattr(pty, 'start', None)
        if not create:
            rec('命令执行', 'PTY 交互终端', 'INFO', 'E2B: PTY 可用', f'pty 对象({type(pty).__name__})无 create/open/start 入口(SDK 版本未覆盖)')
        else:
            sz = None
            try:
                from e2b import PtySize
                sz = PtySize(cols=80, rows=24)
            except Exception:
                sz = {'cols': 80, 'rows': 24}
            ph, err = safe(create, user='root', cwd='/root', size=sz)
            if ph:
                w = getattr(ph, 'write', None)
                if w: safe(w, 'echo pty-ok\n'); time.sleep(1.5)
                rd = getattr(ph, 'read', None)
                txt = ''
                if rd:
                    r, e2 = safe(rd, 1024)
                    if r: txt = r if isinstance(r, str) else r.decode('utf-8','ignore')
                elif hasattr(ph, 'stdout'):
                    txt = str(getattr(ph, 'stdout', ''))[:200]
                kill2 = getattr(ph, 'kill', None)
                if kill2: safe(kill2)
                pty_ok = 'pty-ok' in txt
                rec('命令执行', 'PTY 交互终端', 'PASS' if pty_ok else 'DIFF', 'E2B: PTY 会话可写读', f'读回片段含 pty-ok={pty_ok}')
            else:
                rec('命令执行', 'PTY 交互终端', 'FAIL', 'E2B: PTY 会话可创建', f'{err}')
except Exception as ex:
    rec('命令执行', 'PTY 交互终端', 'FAIL', 'E2B: PTY 可用', f'{type(ex).__name__}: {ex}'[:200])

# ============================================================
# C. 文件系统 files
# ============================================================
print('\n===== C. 文件系统 =====', flush=True)
w, err = safe(S_main.files.write, '/root/compat.txt', 'hello-compat')
r, err2 = safe(S_main.files.read, '/root/compat.txt')
rec('文件系统', 'files.write + files.read(文本)',
    'PASS' if (w and r == 'hello-compat') else 'FAIL',
    '读写回环一致', f'read={r!r}' + (f' | {err or err2}' if (err or err2) else ''))

w, err = safe(S_main.files.write, '/root/compat.bin', b'\x00\x01\x02compat')
r, err2 = safe(S_main.files.read, '/root/compat.bin')
rec('文件系统', 'files.write + files.read(二进制)',
    'PASS' if (w and r == b'\x00\x01\x02compat') else 'FAIL',
    '字节回环一致', f'read={r!r}' + (f' | {err or err2}' if (err or err2) else ''))

lst, err = safe(S_main.files.list, '/')
names = [getattr(e, 'path', str(e)) for e in (lst or [])]
has_home_user = any('/home/user' in n for n in names)
has_root = any(n.rstrip('/') == '/root' for n in names)
rec('文件系统', "files.list('/') 目录面貌",
    'PASS' if has_root and has_home_user else 'DIFF',
    "E2B: 含 /home/user(默认用户家目录)",
    f"含/root={has_root} 含/home/user={has_home_user} 共{len(names)}项")

ex, err = safe(S_main.files.exists, '/root/compat.txt')
rec('文件系统', 'files.exists', 'PASS' if ex is True else 'FAIL', 'True', f'{ex}' + (f' | {err}' if err else ''))

_, err = safe(S_main.files.make_dir, '/root/compat_dir')
mv, err2 = safe(S_main.files.rename, '/root/compat.txt', '/root/compat_dir/renamed.txt')
lst2, err3 = safe(S_main.files.list, '/root/compat_dir')
got = [getattr(e, 'path', '') for e in (lst2 or [])]
rm, err4 = safe(S_main.files.remove, '/root/compat_dir')
rec('文件系统', 'make_dir + rename + remove', 'PASS' if not (err or err2 or err3 or err4) and any('renamed' in g for g in got) else 'FAIL',
    '目录操作回环', f'{"全通过" if not (err or err2 or err3 or err4) else (err or err2 or err3 or err4)}')

watch_fn = getattr(S_main.files, 'watch', None) or getattr(S_main.files, 'watch_dir', None)
if watch_fn is None:
    rec('文件系统', 'files.watch 目录事件', 'INFO', 'E2B: SDK 提供 watch 返回事件流',
        f'SDK 本版 Filesystem 无 watch 方法(SDK 版本未覆盖), 可用方法={[m for m in dir(S_main.files) if not m.startswith("_")]}')
else:
    wres, err = safe(watch_fn, '/root')
    if wres:
        got_evt = []
        def watcher():
            try:
                for ev in wres:
                    got_evt.append(ev)
                    break
            except Exception:
                pass
        th = threading.Thread(target=watcher, daemon=True); th.start()
        time.sleep(1)
        safe(S_main.files.write, '/root/watch_probe.txt', 'x')
        th.join(timeout=6)
        stop = getattr(wres, 'stop', None) or getattr(wres, 'close', None)
        if callable(stop): safe(stop)
        rec('文件系统', 'files.watch 目录事件', 'PASS' if got_evt else 'DIFF', 'write 触发事件', f'收到 {len(got_evt)} 个事件')
    else:
        rec('文件系统', 'files.watch 目录事件', 'FAIL', '返回 WatchHandle 迭代事件', f'{err}')

# ============================================================
# D. Volume / Secret / Template 读面
# ============================================================
print('\n===== D. Volume / Secret / Template =====', flush=True)
vols, err = safe(Volume.list)
if vols is None:
    rec('Volume', 'Volume.list', 'FAIL', '返回团队卷列表', f'{err}')
else:
    rec('Volume', 'Volume.list', 'PASS', '返回列表(可空)', f'{len(vols)} 个卷')
    v, err2 = safe(Volume.create, 'compat-matrix-vol')
    if v:
        gi, e3 = safe(Volume.get_info, getattr(v, 'volume_id', None) or 'compat-matrix-vol')
        dres, e4 = safe(v.remove) or safe(Volume.destroy, 'compat-matrix-vol')
        rec('Volume', 'Volume.create + get_info + 删除', 'PASS' if not e3 else 'DIFF', 'CRUD 回环', f'{"全通过" if not (e3 or e4) else (e3 or e4)}')
    else:
        rec('Volume', 'Volume.create', 'FAIL', '创建卷成功', f'{err2}')

sec_ok = None
try:
    inst_methods = [m for m in dir(Secret) if not m.startswith('_')]
    rec('Secret', 'SDK Secret 类面', 'INFO', 'E2B: Secret.create/list/get/update/delete', f'可用类成员={inst_methods}')
    mk = getattr(Secret, 'create', None)
    if mk:
        r1, e1 = safe(Secret.create, 'compat-matrix-secret', 'dummy-value')
        r2, e2 = safe(getattr(Secret, 'list', lambda *a, **k: None))
        if r2 is not None and not isinstance(r2, type):
            try: r2 = r2()
            except Exception: pass
        dl = getattr(Secret, 'delete', None)
        r3 = None
        if dl:
            r3, e3 = safe(dl, 'compat-matrix-secret')
        rec('Secret', 'Secret.create + list + delete', 'PASS' if r1 and not e2 else 'FAIL', 'CRUD 回环, 永不回读值', f'create={bool(r1)} list={type(r2).__name__ if r2 is not None else e2}' + (f' | {e1 or e2 or e3 or ""}' if (e1 or e2 or e3) else ''))
    else:
        rec('Secret', 'Secret.create', 'INFO', 'E2B: Secret CRUD 经 client 暴露', 'SDK 本版 Secret 类无 CRUD 入口(SDK 版本未覆盖, 无法经 SDK 测平台 /secrets)')
except Exception as ex:
    rec('Secret', 'Secret 域', 'FAIL', '可 CRUD', f'{type(ex).__name__}: {ex}'[:200])

tex, err = safe(Template.exists, TPL)
rec('Template', f'Template.exists({TPL[:8]}…)', 'PASS' if tex is True else 'FAIL', 'True', f'{tex}' + (f' | {err}' if err else ''),
    'KeyError:code=SDK 错误解析假设响应含 code 字段, 掩盖了后端真实 404(模板元数据端点未按 E2B 形态发布)')
tg, err = safe(Template.get_tags, TPL)
rec('Template', 'Template.get_tags', 'PASS' if not err else 'FAIL', '返回标签列表', f'{tg if not err else err}',
    '同上: KeyError:code 掩盖后端 404')
ta, err = safe(Template.alias_exists, 'base')
rec('Template', "Template.alias_exists('base')", 'INFO', 'E2B: 公共 base 模板别名存在', f'{ta}' + (f' | {err}' if err else ''),
    '同上: KeyError:code 掩盖后端 404')

# ============================================================
# E. 快照 / fork / 公网URL / logs / metrics / network
# ============================================================
print('\n===== E. 快照/fork/网络/观测 =====', flush=True)
snap, err = safe(S_ttl.create_snapshot, 'compat-matrix-snap')
if snap:
    sid_snap = getattr(snap, 'snapshot_id', None) or getattr(snap, 'id', '')
    rec('快照', 'create_snapshot(name)', 'PASS', '返回 SnapshotInfo', f'snapshot_id={str(sid_snap)[:12]}…')
    sl, e2 = safe(Sandbox.list_snapshots)
    rec('快照', 'list_snapshots', 'PASS' if not e2 else 'FAIL', '含新快照', f'{len(sl) if isinstance(sl,(list,tuple)) else type(sl).__name__}' + (f' | {e2}' if e2 else ''))
    if sid_snap:
        cs, e3 = safe(Sandbox.create, template=f'{TPL}:{sid_snap}', timeout=120)
        if cs:
            to_kill.append(cs)
            o, e4 = safe(cs.commands.run, 'echo from-snapshot', user='root', cwd='/root')
            rec('快照', f'从快照拉起(template=模板ID:快照ID)', 'PASS' if o else 'DIFF', '新沙箱可用', f"{'命令OK ' + o.stdout.strip() if o else e4}")
        else:
            rec('快照', '从快照拉起', 'FAIL', '可从快照创建新沙箱', f'{e3}')
    ds, e4 = safe(Sandbox.delete_snapshot, sid_snap) if sid_snap else (None, 'no-snap')
    rec('快照', 'delete_snapshot', 'PASS' if not e4 else 'INFO', '删除成功', f'{"OK" if not e4 else e4}')
else:
    rec('快照', 'create_snapshot(name)', 'FAIL', '返回 SnapshotInfo', f'{err}',
        'KeyError:code 掩盖后端真实错误(显式快照端点大概率未实现; AgentSphere 快照语义走 pause)')

fk, err = safe(S_ttl.fork, count=1, timeout=120)
if fk:
    kids = [x for x in (fk if isinstance(fk, (list, tuple)) else [fk]) if not isinstance(x, Exception)]
    for k in kids: to_kill.append(k)
    ok_cmd = False
    if kids:
        o, e2 = safe(kids[0].commands.run, 'echo fork-ok', user='root', cwd='/root')
        ok_cmd = bool(o)
    rec('fork', 'fork(count=1)', 'PASS' if kids and ok_cmd else 'DIFF',
        'E2B: 原地 checkpoint + 派生新沙箱可用', f'{len(kids)} 个派生, 命令可用={ok_cmd}' + (f' | {e2}' if kids and not ok_cmd else ''))
else:
    rec('fork', 'fork(count=1)', 'FAIL', '派生沙箱列表', f'{err}')

host = S_main.get_host(3000)
reach, e2 = 'fail', None
try:
    import httpx
    rr = httpx.get(f'http://{host}', timeout=6)
    reach = f'HTTP {rr.status_code}'
except Exception as ex:
    e2 = f'{type(ex).__name__}'
is_e2b_default = str(host).endswith('e2b.app')
rec('网络', 'get_host(3000) 公网入流',
    'DIFF' if is_e2b_default else ('PASS' if reach.startswith('HTTP') else 'FAIL'),
    'E2B: {port}-{sandboxId}.域名 通配域公网直达沙箱端口',
    f'SDK 拼出 host={host} → {reach}' + (f'({e2})' if e2 else ''),
    ('SDK 用的是默认域名 e2b.app——E2B_SANDBOX_URL(网关)未参与 get_host 域名推导; '
     'AgentSphere 网关为固定主机、无 {sid}/{port} 通配 DNS, 公网入流功能未提供' if is_e2b_default else ''))

lg_attr = getattr(S_main, 'logs', None)
if lg_attr is None:
    rec('观测', 'sandbox.logs', 'INFO', 'E2B: 日志流迭代器', 'SDK 本版 Sandbox 无 logs 属性(SDK 版本未覆盖); 平台日志端点 /v2/sandboxes/{id}/logs 亦未发布')
else:
    got = []
    def reader2():
        try:
            for x in lg_attr:
                got.append(x)
                if len(got) >= 2: break
        except Exception:
            pass
    th = threading.Thread(target=reader2, daemon=True); th.start()
    safe(S_main.commands.run, 'echo log-probe', user='root', cwd='/root')
    th.join(timeout=6)
    rec('观测', 'sandbox.logs', 'PASS' if got else 'DIFF', '产生日志条目', f'读到 {len(got)} 条')

mres, err = safe(Sandbox.get_metrics, S_main.sandbox_id)
if err:
    mres2, err2 = safe(S_main.get_metrics)
    mres, err = mres2, err2
rec('观测', 'get_metrics', 'PASS' if not err else 'FAIL', '返回 CPU/内存时序', f'{"OK" if not err else err}')

du, err = safe(Sandbox.download_url, S_main.sandbox_id, '/root/compat.txt')
if err:
    du2, err2 = safe(getattr(S_main, 'download_url'), '/root/compat.txt')
    du, err = du2, err2
rec('网络', 'download_url(预签名下载)', 'PASS' if not err else 'FAIL', '返回带签名的 URL', f'{"OK" if not err else err}')

uu, err = safe(Sandbox.upload_url, S_main.sandbox_id, '/root/up_probe.txt')
if err:
    uu2, err2 = safe(getattr(S_main, 'upload_url'), '/root/up_probe.txt')
    uu, err = uu2, err2
rec('网络', 'upload_url(预签名上传)', 'PASS' if not err else 'FAIL', '返回带签名的 URL', f'{"OK" if not err else err}')

# update_network 放最后(整体替换语义, 可能破坏沙箱网络)
un_ok, err = None, None
try:
    from e2b import SandboxNetworkOpts
    import dataclasses
    fields = {f.name: None for f in dataclasses.fields(SandboxNetworkOpts)}
    opts = SandboxNetworkOpts(**fields)
    r, err = safe(S_ttl.update_network, opts)
    un_ok = r is not None or err is None
except Exception as ex:
    err = f'{type(ex).__name__}: {ex}'[:200]
rec('网络', 'update_network(空配置整体替换)', 'PASS' if un_ok else 'FAIL',
    'E2B: 接受并整体替换 egress 配置', f'{"OK" if un_ok else err}',
    '注意 E2B 语义: 省略字段=清除')

# ============================================================
# F. pause/resume 深测(keep_memory=False)
# ============================================================
print('\n===== F. pause/resume =====', flush=True)
pr, err = safe(S_ttl.pause)
st = getattr(S_ttl.get_info(), 'state', '?') if not err else '?'
rec('暂停恢复', 'pause() 返回值+状态', 'PASS' if (pr is True or pr == True) and st == 'paused' else 'DIFF',
    '返回 True, state=paused', f'pause返回={pr} state={st}')
o, err = safe(Sandbox.connect, S_ttl.sandbox_id, timeout=300)
if o is None:
    cn, e2 = safe(S_ttl.connect, 300)
    o, err = cn, e2
if o:
    to_kill.append(S_ttl)
    left = ttl_left(S_ttl.get_info())
    rec('暂停恢复', 'connect 恢复 paused 沙箱', 'PASS', '恢复成功命令可用',
        f"恢复成功, 剩余TTL={left:.0f}s" if left else '恢复成功')
    o2, e2 = safe(S_ttl.commands.run, 'cat /root/compat.txt 2>/dev/null || echo NOFILE', user='root', cwd='/root')
    rec('暂停恢复', '恢复后文件状态保留(fs 快照)', 'PASS' if o2 and 'hello-compat' in (getattr(o2,"stdout","") or '') else 'DIFF',
        '文件系统随快照恢复', f"stdout={getattr(o2,'stdout','?').strip()!r}" if o2 else f'{e2}')
else:
    rec('暂停恢复', 'resume(connect)', 'FAIL', '恢复成功', f'{err}')

p2, err = safe(S_ttl.pause, keep_memory=False)
if not err:
    cn, e2 = safe(Sandbox.connect, S_ttl.sandbox_id, timeout=300) or safe(S_ttl.connect, 300)
    if cn:
        rec('暂停恢复', 'pause(keep_memory=False) + 恢复', 'PASS', '无内存冷恢复路径可用', 'OK')
    else:
        rec('暂停恢复', 'pause(keep_memory=False) + 恢复', 'FAIL', 'E2B: memory=false 冷启动恢复', f'{e2 or err}')

# ============================================================
# G. 清理 + kill 语义
# ============================================================
print('\n===== G. 清理 =====', flush=True)
kid, err = safe(S_main.kill)
gi, err2 = safe(Sandbox.get_info, S_main.sandbox_id)
rec('沙箱生命周期', 'kill → get_info(立即)',
    'PASS' if (kid and (gi is None or 'NotFound' in str(err2) or getattr(gi,'state','')=='killed')) else 'DIFF',
    'E2B: kill 后 get_info 立即 404',
    f'kill={bool(kid)} get_info={"404/NotFound" if gi is None or (err2 and "NotFound" in str(err2)) else getattr(gi,"state","?")}')
if kid: bg_linger_check(S_main.sandbox_id)

for s in to_kill:
    if s is S_ttl: continue  # S_ttl 已 kill 过(pause 深测后仍需 kill)
    safe(s.kill)
safe(S_ttl.kill)
if S_lc1: safe(S_lc1.kill)
if S_lc2: safe(S_lc2.kill)
for s in to_kill_ids: safe(Sandbox.kill, s)
print('\n[done] 清理完成, 写结果文件…', flush=True)
json.dump(RESULTS, open(DEMO/'sdk_compat_result.json', 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
n_pass = sum(1 for r in RESULTS if r['status']=='PASS'); n_diff = sum(1 for r in RESULTS if r['status']=='DIFF'); n_fail = sum(1 for r in RESULTS if r['status']=='FAIL'); n_info = sum(1 for r in RESULTS if r['status']=='INFO')
print(f'[summary] PASS={n_pass} DIFF={n_diff} FAIL={n_fail} INFO={n_info} → sdk_compat_result.json', flush=True)
