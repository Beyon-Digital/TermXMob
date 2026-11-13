"""Actual owned daemon shutdown; never signals an existing user host."""
import json
import os
from pathlib import Path
from secrets import token_hex
import socket
import subprocess
import sys
from time import monotonic, sleep
from types import ModuleType, SimpleNamespace

import httpx
import pytest


_IDENTITY_FIELDS = ('nonce', 'owner_pid', 'pid', 'ppid', 'root', 'entrypoint')


def _owned_identity(record, *, nonce, owner_pid, launcher_pid, platform, root, started=None):
    """Bind a guarded runtime to this invocation, including Windows venv redirectors."""
    assert all(name in record for name in _IDENTITY_FIELDS)
    identity = {name: record[name] for name in _IDENTITY_FIELDS}
    assert record['guards_installed'] is True
    assert identity['nonce'] == nonce and identity['owner_pid'] == owner_pid
    assert all(type(identity[name]) is int and identity[name] > 0 for name in ('owner_pid', 'pid', 'ppid'))
    assert identity['pid'] not in (owner_pid, identity['ppid'])
    assert identity['root'] == str(root.resolve())
    assert identity['entrypoint'] == str(Path(__file__).resolve())
    if identity['pid'] == launcher_pid:
        assert identity['ppid'] == owner_pid
    else:
        # CPython's Windows venv redirector starts Python, waits for that child,
        # and returns its status. A different PID must be exactly that child.
        assert platform == 'win32' and identity['ppid'] == launcher_pid
    if started is not None:
        assert identity == started
    return identity


def _owned_environment(root, source=None):
    source = os.environ if source is None else source
    names = ['PATH', 'LANG', 'LC_ALL']
    if sys.platform == 'win32':
        names += ['SystemRoot', 'windir', 'ComSpec', 'PATHEXT', 'OS', 'PROCESSOR_ARCHITECTURE']
    environment = {name: source[name] for name in names if name in source}
    if sys.platform == 'win32':
        environment['SystemRoot'] = source['SystemRoot']
        environment['USERPROFILE'] = str(root/'home')
    environment.update(
        TERMX_CONFIG_DIR=str(root/'config'), TERMX_AGENTS_DIR=str(root/'agents'),
        TERMX_ACP_HOME=str(root/'acp'), TERMX_ENGINE_STARTUP_REFRESH='0',
        TERMX_PROVIDER_STARTUP_REFRESH='0', TERMX_AGENT_DESKTOP_STARTUP_REFRESH='0',
        TERMX_DESKTOP='0', PYTHONUNBUFFERED='1', PYTHONIOENCODING='cp1252',
        PYTHONDONTWRITEBYTECODE='1', PYTHONPATH=str(Path(__file__).resolve().parents[1]/'src'),
    )
    return environment


def _owned_hardware_guards(replace=setattr, ledger=None):
    """Guard this fixture process before CLI startup, without replacing its lifecycle."""
    from termx import cli
    from termx.agent.secrets import CredentialStore
    from termx.desktop import broker, capabilities, capture, displays, input, permissions, virtual, windows

    if ledger is None:
        ledger = {'forbidden': [], 'cleanup': [], 'bindings': [], 'memory_credentials': 0}

    def bind(module, name, replacement):
        original = getattr(module, name)
        # Cover existing `from ... import ...` aliases as well as future imports
        # from the canonical module. Only identical function objects are changed.
        for module_name, loaded in tuple(sys.modules.items()):
            if not module_name.startswith('termx.') or not isinstance(loaded, ModuleType):
                continue
            for attribute, value in tuple(vars(loaded).items()):
                if value is original:
                    replace(loaded, attribute, replacement)
                    ledger['bindings'].append(f'{module_name}.{attribute}')

    def forbidden(operation):
        def reject(*args, **kwargs):
            ledger['forbidden'].append(operation)
            raise AssertionError(f'Owned host-stop fixture forbids {operation}')
        return reject

    def cleanup(operation):
        def record(*args, **kwargs):
            ledger['cleanup'].append(operation)
        return record

    def input_event(event, target=None):
        if event.get('type') != 'release_all':
            forbidden('input')()
        ledger['cleanup'].append('release_all')

    bind(input, 'apply_event', input_event)
    bind(input, '_apply_event', input_event)
    bind(input, '_release_all', cleanup('release_all'))
    for name in ('clipboard_get', 'clipboard_set', '_pointer', '_key', '_text', '_send_inputs'):
        bind(input, name, forbidden('input'))
    bind(capture, 'close_capture', cleanup('close_capture'))
    bind(capture, 'grab_jpeg', forbidden('capture'))
    bind(capture, 'list_displays', lambda: [])
    bind(displays, 'list_physical_displays', lambda: [])
    bind(capabilities, 'probe_desktop', lambda: capabilities.DesktopProbe(reason='Owned fixture: no hardware'))
    bind(permissions, 'permission_snapshot', lambda: {'screen_recording': 'denied', 'accessibility': 'denied'})
    bind(permissions, 'request_permissions', forbidden('permission_prompt'))
    bind(virtual, '_adopt_helper_displays', forbidden('display_adoption'))
    bind(virtual, 'active_adapter', lambda: None)
    bind(virtual, 'list_virtual_displays', lambda adopt=False: [])
    bind(virtual, 'create_virtual_display', forbidden('display_create'))
    bind(virtual, 'destroy_virtual_display', forbidden('display_destroy'))
    bind(virtual, 'destroy_all_virtual_displays', cleanup('empty_virtual_cleanup'))
    replace(virtual, '_active', {})
    replace(virtual, '_impls', {})
    for name in ('list', 'current', 'capture', 'input'):
        replace(windows.WindowCapture, name, forbidden('window_capture'))
    replace(windows.WindowCapture, 'capability', lambda self: {'supported': False, 'backend': None, 'reason': 'Owned fixture'})

    def configure_broker(path):
        if path is not None:
            forbidden('native_broker')()
        replace(broker, '_CLIENT', None)

    bind(broker, 'configure', configure_broker)
    bind(broker, 'available', lambda: False)
    bind(broker, '_call', forbidden('native_broker'))
    replace(broker._Client, '_connect', forbidden('native_broker'))
    for name in ('request_permission', 'set_autostart', 'capture_frame', 'stop_capture', 'send_input', 'send_relative_move'):
        bind(broker, name, forbidden('native_broker'))
    # No desktop shell is launched by this test; a permission event would still
    # be an unintended request and must be visible even if a caller catches it.
    from termx import notify
    bind(notify, 'permission', forbidden('permission_prompt'))
    # Use the inherited runtime PATH; don't search the user's shell/profile paths.
    bind(cli, 'augment_path', lambda: None)
    original_state = cli.AppState

    def owned_state(*args, **kwargs):
        kwargs['credentials'] = CredentialStore(memory={})
        ledger['memory_credentials'] += 1
        return original_state(*args, **kwargs)

    replace(cli, 'AppState', owned_state)
    return ledger


def _owned_daemon(root, owner_pid, nonce, arguments):
    root = root.resolve()
    for name, child in (('TERMX_CONFIG_DIR', 'config'), ('TERMX_AGENTS_DIR', 'agents'), ('TERMX_ACP_HOME', 'acp')):
        assert Path(os.environ[name]).resolve() == root/child
    if sys.platform == 'win32':
        home = root/'home'
        assert Path(os.environ['USERPROFILE']).resolve() == home
        home.mkdir(exist_ok=True)
    ledger = {'forbidden': [], 'cleanup': [], 'bindings': [], 'memory_credentials': 0,
              'nonce': nonce, 'owner_pid': owner_pid, 'pid': os.getpid(), 'ppid': os.getppid(),
              'root': str(root), 'entrypoint': str(Path(__file__).resolve()),
              'guards_installed': False, 'cli_entered': False, 'exit_code': None}
    try:
        _owned_hardware_guards(ledger=ledger)
        ledger['guards_installed'] = True
        # The parent validates this receipt while its Popen is alive, before
        # authentication or stop requests. Never replace a previous receipt.
        with (root/'started-identity.json').open('x', encoding='utf-8') as receipt:
            json.dump(ledger, receipt)
        from termx import cli
        sys.argv = ['termx', *arguments]
        ledger['cli_entered'] = True
        cli.main()
        ledger['exit_code'] = 0
    except SystemExit as exc:
        ledger['exit_code'] = exc.code
        raise
    finally:
        (root/'hardware-audit.json').write_text(json.dumps(ledger, indent=2)+'\n', encoding='utf-8')


@pytest.mark.parametrize('platform', ['darwin', 'linux', 'win32'])
def test_owned_daemon_environment_is_allowlisted(tmp_path, monkeypatch, platform):
    monkeypatch.setattr(sys, 'platform', platform)
    source = {'PATH': 'fixture-bin', 'LANG': 'C.UTF-8', 'SystemRoot': r'C:\Windows',
              'UNRELATED_FIXTURE_VALUE': 'must-not-inherit', 'PYTHONPATH': 'parent-packages',
              'TERMX_AUTH_ADAPTERS_FILE': 'parent-account-settings',
              'USERPROFILE': 'parent-profile-must-not-be-inherited', 'HOME': 'parent-home',
              'HOMEDRIVE': 'Z:', 'HOMEPATH': r'\parent-profile'}
    environment = _owned_environment(tmp_path, source)
    assert environment['PATH'] == 'fixture-bin'
    assert environment['PYTHONPATH'] != source['PYTHONPATH']
    assert environment['PYTHONIOENCODING'] == 'cp1252'
    assert environment['TERMX_CONFIG_DIR'] == str(tmp_path/'config')
    assert environment['TERMX_AGENTS_DIR'] == str(tmp_path/'agents')
    assert environment['TERMX_ACP_HOME'] == str(tmp_path/'acp')
    assert all(environment[name] == '0' for name in (
        'TERMX_ENGINE_STARTUP_REFRESH', 'TERMX_PROVIDER_STARTUP_REFRESH', 'TERMX_AGENT_DESKTOP_STARTUP_REFRESH'))
    assert set(environment) == {
        'PATH', 'LANG', 'TERMX_CONFIG_DIR', 'TERMX_AGENTS_DIR', 'TERMX_ACP_HOME',
        'TERMX_ENGINE_STARTUP_REFRESH', 'TERMX_PROVIDER_STARTUP_REFRESH',
        'TERMX_AGENT_DESKTOP_STARTUP_REFRESH', 'TERMX_DESKTOP', 'PYTHONUNBUFFERED',
        'PYTHONIOENCODING', 'PYTHONDONTWRITEBYTECODE', 'PYTHONPATH',
    } | ({'SystemRoot', 'USERPROFILE'} if platform == 'win32' else set())
    assert 'UNRELATED_FIXTURE_VALUE' not in environment and 'TERMX_AUTH_ADAPTERS_FILE' not in environment
    assert ('SystemRoot' in environment) is (platform == 'win32')
    if platform == 'win32':
        assert environment['SystemRoot'] == source['SystemRoot']
        assert environment['USERPROFILE'] == str(tmp_path/'home')
        assert environment['USERPROFILE'] != source['USERPROFILE']


def test_owned_windows_profile_resolves_home_without_parent_environment(tmp_path, monkeypatch):
    import ntpath

    monkeypatch.setattr(sys, 'platform', 'win32')
    environment = _owned_environment(tmp_path, {'SystemRoot': r'C:\Windows'})
    # Use the actual Windows resolver even on a Mac, without clearing or
    # changing the process environment (and without its POSIX pwd fallback).
    proxy = SimpleNamespace(fspath=os.fspath, fsdecode=os.fsdecode, fsencode=os.fsencode,
                            environ=dict(environment))
    with monkeypatch.context() as patch:
        patch.setattr(ntpath, 'os', proxy)
        assert ntpath.expanduser('~') == environment['USERPROFILE']
        proxy.environ.pop('USERPROFILE')
        assert ntpath.expanduser('~') == '~'


def test_owned_daemon_guards_aliases_and_records_cleanup(monkeypatch):
    from termx.desktop import broker, capture, input, permissions, session, virtual, windows

    ledger = _owned_hardware_guards(monkeypatch.setattr)
    assert session.apply_event is input.apply_event
    assert session.close_capture is capture.close_capture
    assert session.grab_jpeg is capture.grab_jpeg
    session.apply_event({'type': 'release_all'})
    session.close_capture()
    virtual.destroy_all_virtual_displays()
    assert ledger['cleanup'] == ['release_all', 'close_capture', 'empty_virtual_cleanup']
    calls = [
        lambda: session.apply_event({'type': 'key'}), lambda: session.grab_jpeg(),
        lambda: permissions.request_permissions(), lambda: virtual._adopt_helper_displays(),
        lambda: virtual.create_virtual_display('owned', 800, 600),
        lambda: virtual.destroy_virtual_display('owned'), lambda: broker.configure('forbidden-socket'),
        lambda: broker._call({'op': 'status'}), lambda: broker._Client('forbidden-socket')._connect(),
        lambda: windows.WindowCapture().capture({}),
    ]
    for call in calls:
        with pytest.raises(AssertionError, match='Owned host-stop fixture forbids'):
            call()
    assert ledger['forbidden'] == ['input', 'capture', 'permission_prompt', 'display_adoption',
                                   'display_create', 'display_destroy', 'native_broker', 'native_broker',
                                   'native_broker', 'window_capture']


@pytest.fixture
def owned_identity_record(tmp_path):
    return {'nonce': 'a'*64, 'owner_pid': 101, 'pid': 201, 'ppid': 101,
            'root': str(tmp_path.resolve()), 'entrypoint': str(Path(__file__).resolve()),
            'guards_installed': True}


@pytest.mark.parametrize('platform,pid,ppid', [
    ('darwin', 201, 101), ('linux', 201, 101), ('win32', 201, 101), ('win32', 301, 201),
])
def test_owned_identity_accepts_only_direct_or_windows_child(tmp_path, owned_identity_record, platform, pid, ppid):
    owned_identity_record.update(pid=pid, ppid=ppid)
    assert _owned_identity(owned_identity_record, nonce='a'*64, owner_pid=101, launcher_pid=201,
                           platform=platform, root=tmp_path) == {
        name: owned_identity_record[name] for name in _IDENTITY_FIELDS}


@pytest.mark.parametrize('change', [
    {'nonce': 'b'*64}, {'owner_pid': 999}, {'ppid': 999}, {'pid': False}, {'pid': 0},
    {'pid': 101, 'ppid': 201}, {'pid': 201, 'ppid': 201},
    {'root': 'foreign-root'}, {'entrypoint': 'foreign-bootstrap'}, {'guards_installed': False},
])
def test_owned_identity_rejects_stale_forged_or_foreign_records(tmp_path, owned_identity_record, change):
    owned_identity_record.update(change)
    with pytest.raises(AssertionError):
        _owned_identity(owned_identity_record, nonce='a'*64, owner_pid=101, launcher_pid=201,
                        platform='win32', root=tmp_path)


@pytest.mark.parametrize('missing', ['nonce', 'owner_pid', 'pid', 'ppid'])
def test_owned_identity_requires_launch_attestation(tmp_path, owned_identity_record, missing):
    owned_identity_record.pop(missing)
    with pytest.raises(AssertionError):
        _owned_identity(owned_identity_record, nonce='a'*64, owner_pid=101, launcher_pid=201,
                        platform='win32', root=tmp_path)


@pytest.mark.parametrize('platform', ['darwin', 'linux'])
def test_owned_identity_rejects_delegation_off_windows(tmp_path, owned_identity_record, platform):
    owned_identity_record.update(pid=301, ppid=201)
    with pytest.raises(AssertionError):
        _owned_identity(owned_identity_record, nonce='a'*64, owner_pid=101, launcher_pid=201,
                        platform=platform, root=tmp_path)


def test_owned_audit_cannot_substitute_another_child(tmp_path, owned_identity_record):
    owned_identity_record.update(pid=301, ppid=201)
    options = dict(nonce='a'*64, owner_pid=101, launcher_pid=201, platform='win32', root=tmp_path)
    started = _owned_identity(owned_identity_record, **options)
    forged = {**owned_identity_record, 'pid': 302}
    assert _owned_identity(forged, **options)['pid'] == 302
    with pytest.raises(AssertionError):
        _owned_identity(forged, **options, started=started)


@pytest.mark.parametrize('desktop', [False, True])
def test_explicit_host_stop_has_intentional_desktop_exit_status(tmp_path, desktop):
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
    environment = _owned_environment(tmp_path)
    owner_pid, nonce = os.getpid(), token_hex(32)
    command = [sys.executable, '-B', str(Path(__file__).resolve()), '--owned-daemon', str(tmp_path),
               str(owner_pid), nonce,
               '--host', '127.0.0.1', '--port', str(port),
               '--passcode', 'owned-host-stop-fixture', '--web-dir', str(tmp_path/'web')]
    if desktop:
        command.append('--desktop')
    # Windows pipes have a small buffer: the non-desktop QR/banner can fill it
    # before the client request is served. Files retain all diagnostics without
    # making application readiness depend on an undrained test pipe.
    stdout_path, stderr_path = tmp_path/'stdout.log', tmp_path/'stderr.log'
    with stdout_path.open('w', encoding='utf-8') as stdout_log, stderr_path.open('w', encoding='utf-8') as stderr_log:
        process = subprocess.Popen(command, env=environment, cwd=Path(__file__).resolve().parents[1],
                                   stdout=stdout_log, stderr=stderr_log, text=True)
        try:
            identity_options = dict(nonce=nonce, owner_pid=owner_pid, launcher_pid=process.pid,
                                    platform=sys.platform, root=tmp_path)
            with httpx.Client(base_url=f'http://127.0.0.1:{port}', timeout=5, trust_env=False,
                              headers={'Origin':f'http://127.0.0.1:{port}'}) as client:
                deadline = monotonic()+35
                while True:
                    assert process.poll() is None, stderr_path.read_text(encoding='utf-8')
                    try:
                        if client.get('/auth/methods').status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    assert monotonic() < deadline, ('Owned fixture daemon did not become ready\n'
                                                   + stderr_path.read_text(encoding='utf-8'))
                    sleep(.1)
                assert process.poll() is None, stderr_path.read_text(encoding='utf-8')
                started = _owned_identity(json.loads((tmp_path/'started-identity.json').read_text(encoding='utf-8')),
                                          **identity_options)
                login = client.post('/auth/setup', headers={'X-Termx-Passcode':'owned-host-stop-fixture'},
                                    json={'username':'owned-stop-owner','password':'owned-stop-password-123',
                                          'transport':'bearer','device_name':'Owned test'})
                assert login.status_code == 200, login.text
                client.headers['Authorization'] = 'Bearer '+login.json()['access_token']
                host = client.get('/auth/me').json()['host_id']
                refused = client.post('/auth/host/stop', json={'host_id':'another-host','acknowledge':True})
                assert refused.status_code == 400
                assert process.poll() is None
                assert client.get('/auth/me').status_code == 200
                accepted = client.post('/auth/host/stop', json={'host_id':host,'acknowledge':True})
                assert accepted.status_code == 200 and accepted.json()['accepted']
            process.communicate(timeout=25)
            stdout = stdout_path.read_text(encoding='utf-8')
            stderr = stderr_path.read_text(encoding='utf-8')
            assert process.returncode == (79 if desktop else 0), stderr
            events = [json.loads(line) for line in stdout.splitlines() if line.startswith('{')]
            assert [row for row in events if row.get('termx') == 'stopping'] == (
                [{'termx':'stopping','reason':'host-stop'}] if desktop else [])
            audit = json.loads((tmp_path/'hardware-audit.json').read_text(encoding='utf-8'))
            _owned_identity(audit, **identity_options, started=started)
            assert audit['guards_installed'] and audit['cli_entered']
            assert audit['exit_code'] == process.returncode and audit['memory_credentials'] == 1
            assert audit['forbidden'] == []
            assert {'release_all', 'close_capture', 'empty_virtual_cleanup'} <= set(audit['cleanup'])
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.communicate(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.communicate(timeout=5)


if __name__ == '__main__':
    assert sys.argv[1] == '--owned-daemon'
    _owned_daemon(Path(sys.argv[2]), int(sys.argv[3]), sys.argv[4], sys.argv[5:])
