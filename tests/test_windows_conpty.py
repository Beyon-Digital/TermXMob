"""Actual Windows console/token/job integration; no terminal API mocks."""
import os
from pathlib import Path
import sys
import time

import pytest
from termx.sandbox import SpawnSpec, SandboxFailure, runner_for, windows_backend_available

pytestmark = pytest.mark.skipif(sys.platform != 'win32', reason='Actual Windows ConPTY integration')


def runner(tmp_path):
    if not windows_backend_available():
        if os.environ.get('TERMX_RUNTIME_QUALIFICATION') == '1':
            pytest.fail('Required restricted-token/Job Object terminal launch is unavailable')
        pytest.skip('Restricted-token/Job Object primitive unavailable; no host fallback')
    return runner_for('workspace', backend='windows', state_dir=tmp_path/'state')


def capture(terminal, marker, timeout=15):
    data = bytearray(); deadline = time.monotonic()+timeout
    while time.monotonic()<deadline:
        chunk = terminal.read(.1)
        if chunk: data.extend(chunk)
        if marker.encode() in data: return bytes(data)
        if chunk == b'': break
    raise AssertionError(f'Actual restricted console did not emit {marker!r}: {bytes(data)!r}; {terminal.diagnostics()}')


def spec(workspace, script, **changes):
    values = dict(profile='workspace', argv=(sys.executable,'-u',str(script)),
        cwd=str(workspace), workspace_root=str(workspace), writable_roots=(str(workspace),),
        pty=True, purpose='terminal', task_id='synthetic-conpty-fixture', network='outbound',
        granted_capabilities=('net.outbound:any',))
    values.update(changes)
    return SpawnSpec(**values)


def test_actual_conpty_input_resize_interrupt_and_write_boundary(tmp_path, monkeypatch):
    workspace=tmp_path/'project'; workspace.mkdir()
    outside=tmp_path/'outside.txt'
    script=workspace/'console_fixture.py'
    script.write_text('''open('fixture-entered.txt','w').write('first instruction')
import ctypes, ctypes.wintypes as wt, os, time
from pathlib import Path
k=ctypes.WinDLL('kernel32',use_last_error=True)
k.GetStdHandle.argtypes=[wt.DWORD];k.GetStdHandle.restype=wt.HANDLE
k.GetConsoleMode.argtypes=[wt.HANDLE,ctypes.POINTER(wt.DWORD)];k.GetConsoleMode.restype=wt.BOOL
handle=k.GetStdHandle(-11);mode=wt.DWORD()
print('CONSOLE_MODE='+str(int(bool(k.GetConsoleMode(handle,ctypes.byref(mode))))),flush=True)
class Coord(ctypes.Structure):_fields_=[('x',ctypes.c_short),('y',ctypes.c_short)]
class Rect(ctypes.Structure):_fields_=[('l',ctypes.c_short),('t',ctypes.c_short),('r',ctypes.c_short),('b',ctypes.c_short)]
class Info(ctypes.Structure):_fields_=[('size',Coord),('cursor',Coord),('attr',wt.WORD),('window',Rect),('maximum',Coord)]
k.GetConsoleScreenBufferInfo.argtypes=[wt.HANDLE,ctypes.POINTER(Info)];k.GetConsoleScreenBufferInfo.restype=wt.BOOL
print('HOST_SECRET='+str('TERMX_SYNTHETIC_HOST_SECRET' in os.environ),flush=True)
Path('allowed.txt').write_text('approved write')
try:Path(%r).write_text('forbidden write');print('OUTSIDE_WRITE_ALLOWED',flush=True)
except PermissionError:print('OUTSIDE_WRITE_DENIED',flush=True)
while True:
 try:
  command=input('READY>')
  if command=='size':
   info=Info();assert k.GetConsoleScreenBufferInfo(handle,ctypes.byref(info));print('SIZE='+str(info.size.x)+','+str(info.size.y),flush=True)
  elif command=='sleep':print('SLEEPING',flush=True);time.sleep(60)
  elif command=='exit':break
 except KeyboardInterrupt:print('INTERRUPTED',flush=True)
''' % str(outside))
    monkeypatch.setenv('TERMX_SYNTHETIC_HOST_SECRET','synthetic-value-must-not-enter-terminal')
    backend=runner(tmp_path)
    terminal=backend.spawn_terminal(spec(workspace,script),31,96)
    try:
        try:initial=capture(terminal,'READY>')
        except AssertionError as error:
            raise AssertionError(f'{error}; fixture_entered={(workspace/"fixture-entered.txt").exists()}') from error
        assert b'CONSOLE_MODE=1' in initial and b'HOST_SECRET=False' in initial
        assert b'OUTSIDE_WRITE_DENIED' in initial and b'OUTSIDE_WRITE_ALLOWED' not in initial
        assert (workspace/'allowed.txt').read_text()=='approved write' and not outside.exists()
        terminal.resize(45,120); terminal.write(b'size\r')
        assert b'SIZE=120,45' in capture(terminal,'SIZE=120,45')
        terminal.write(b'sleep\r');capture(terminal,'SLEEPING')
        assert terminal.send_signal('int')
        capture(terminal,'INTERRUPTED'); assert terminal.alive()
        terminal.write(b'exit\r'); assert terminal.wait(15)==0
        assert not terminal.alive()
    finally:terminal.kill()


def test_conpty_rejects_unsupported_network_denial_instead_of_host_fallback(tmp_path):
    workspace=tmp_path/'project';workspace.mkdir();script=workspace/'unused.py'
    script.write_text('raise AssertionError("must never execute")')
    with pytest.raises(SandboxFailure,match='network-denied'):
        runner(tmp_path).spawn_terminal(spec(workspace,script,network='none',granted_capabilities=()),24,80)


def test_console_broker_refuses_normal_host_token_before_shell_effect(tmp_path):
    import subprocess
    from termx.sandbox._win_conpty_protocol import encode
    target=tmp_path/'must-not-exist.txt'
    script=tmp_path/'must-not-run.py'
    script.write_text(f'from pathlib import Path;Path({str(target)!r}).write_text("unsafe")')
    request={'type':'start','command':subprocess.list2cmdline([sys.executable,str(script)]),
        'cwd':str(tmp_path),'env':{},'job':{},'rows':24,'cols':80}
    result=subprocess.run([sys.executable,'-I','-m','termx.sandbox._win_conpty_worker'],
        input=encode(request),stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=20)
    assert result.returncode!=0 and not target.exists()
    assert b'Console child' in result.stderr


def test_actual_conpty_job_kill_reaps_descendants_before_root_release(tmp_path):
    workspace=tmp_path/'project';workspace.mkdir();script=workspace/'descendants.py'
    script.write_text('''open('fixture-entered.txt','w').write('first instruction')
import subprocess,sys,time
from pathlib import Path
child=subprocess.Popen([sys.executable,'-u','-c',"from pathlib import Path;import time;time.sleep(8);Path('escaped-child.txt').write_text('not reaped')"])
Path('child.pid').write_text(str(child.pid));print('TREE_READY',flush=True);time.sleep(60)
''')
    backend=runner(tmp_path);terminal=backend.spawn_terminal(spec(workspace,script),24,80)
    try:
        try:capture(terminal,'TREE_READY')
        except AssertionError as error:
            raise AssertionError(f'{error}; fixture_entered={(workspace/"fixture-entered.txt").exists()}') from error
        terminal.kill(15);terminal.wait(15)
        assert terminal._done.is_set() and not terminal.alive()
        from termx.sandbox import windows_runner
        assert not any(Path(path)==workspace for path in windows_runner._LABEL_HELD)
        # Real process query: the descendant is absent/terminated immediately.
        import ctypes,ctypes.wintypes as wt
        api=ctypes.WinDLL('kernel32',use_last_error=True)
        api.OpenProcess.argtypes=[wt.DWORD,wt.BOOL,wt.DWORD];api.OpenProcess.restype=wt.HANDLE
        api.GetExitCodeProcess.argtypes=[wt.HANDLE,ctypes.POINTER(wt.DWORD)];api.GetExitCodeProcess.restype=wt.BOOL
        api.CloseHandle.argtypes=[wt.HANDLE];api.CloseHandle.restype=wt.BOOL
        handle=api.OpenProcess(0x1000,False,int((workspace/'child.pid').read_text()))
        if handle:
            try:
                code=wt.DWORD();assert api.GetExitCodeProcess(handle,ctypes.byref(code));assert code.value!=259
            finally:api.CloseHandle(handle)
        assert not (workspace/'escaped-child.txt').exists()
    finally:terminal.kill()


def test_actual_restricted_builtin_console_output(tmp_path):
    workspace=tmp_path/'project';workspace.mkdir()
    cmd=str(Path(os.environ.get('SystemRoot',r'C:\Windows'))/'System32'/'cmd.exe')
    request=SpawnSpec(profile='workspace',argv=(cmd,'/d','/c','echo TERMX_BUILTIN_CONSOLE_OK'),
        cwd=str(workspace),workspace_root=str(workspace),writable_roots=(str(workspace),),
        pty=True,purpose='terminal',task_id='builtin-console-fixture',network='outbound',
        granted_capabilities=('net.outbound:any',))
    terminal=runner(tmp_path).spawn_terminal(request,24,80)
    try:
        assert b'TERMX_BUILTIN_CONSOLE_OK' in capture(terminal,'TERMX_BUILTIN_CONSOLE_OK')
        assert terminal.wait(15)==0
    finally:terminal.kill()


def test_diagnostic_host_conpty_baseline_for_owned_builtin_only(tmp_path,monkeypatch):
    """ABI comparison only. This deliberately has NO sandbox qualification claim.

    The only effect is the fixed echo fixture; production never selects this
    path and the separate normal-token refusal test remains enforced.
    """
    import subprocess
    from termx.sandbox import _win_conpty_security
    from termx.sandbox._win_conpty import LocalConPTY
    monkeypatch.setattr(_win_conpty_security,'verify_child_token',lambda process,expected:None)
    cmd=str(Path(os.environ.get('SystemRoot',r'C:\Windows'))/'System32'/'cmd.exe')
    environment={'SystemRoot':os.environ.get('SystemRoot',r'C:\Windows'),
        'TMP':str(tmp_path),'TEMP':str(tmp_path),'USERPROFILE':str(tmp_path)}
    terminal=LocalConPTY(command=subprocess.list2cmdline([cmd,'/d','/c','echo TERMX_HOST_ABI_BASELINE_OK']),
        cwd=str(tmp_path),env=environment,job_limits={'active_process_limit':8},
        user_mode=False,rows=24,cols=80,release=lambda:None)
    try:
        assert b'TERMX_HOST_ABI_BASELINE_OK' in capture(terminal,'TERMX_HOST_ABI_BASELINE_OK')
        assert terminal.wait(15)==0
    finally:terminal.kill()
