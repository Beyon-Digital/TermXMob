"""Source checks for native fixture isolation; these do not prove a native GUI."""
import importlib.util
import json
import os
from pathlib import Path
import re


def harness():
    path = Path(__file__).resolve().parents[1]/'desktop/scripts/native_gui_smoke.py'
    spec = importlib.util.spec_from_file_location('native_gui_fixture',path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_native_fixture_keeps_home_and_accounts_outside_its_profile(tmp_path):
    module = harness()
    original = dict(os.environ)
    environment = module.fixture_environment(tmp_path)
    assert dict(os.environ) == original
    assert environment.get('HOME') == original.get('HOME')
    for name in ['XDG_DATA_HOME','XDG_CONFIG_HOME','XDG_CACHE_HOME','APPDATA',
                 'LOCALAPPDATA','TERMX_DESKTOP_DATA_DIR']:
        assert Path(environment[name]).is_absolute()
        assert Path(environment[name]).is_relative_to(tmp_path.resolve())
    # An override hides msedgedriver's DevToolsActivePort file from the driver.
    assert environment.get('WEBVIEW2_USER_DATA_FOLDER') == original.get('WEBVIEW2_USER_DATA_FOLDER')
    assert environment['TERMX_DESKTOP_DATA_DIR'] != original.get('TERMX_DESKTOP_DATA_DIR')


def test_native_fixture_installer_digest_streams_without_reading_whole_asset(tmp_path):
    module = harness()
    package = tmp_path/'fixture.msi'
    package.write_bytes(b'installed-platform-asset'*100000)
    import hashlib
    assert module.file_digest(package) == hashlib.sha256(package.read_bytes()).hexdigest()


def test_native_fixture_rejects_changed_installed_asset(tmp_path):
    import pytest
    module = harness()
    binary, package = tmp_path/'fixture.exe', tmp_path/'fixture.msi'
    binary.write_bytes(b'unchanged installed app')
    package.write_bytes(b'unchanged installer')
    report = {'binary_sha256': module.file_digest(binary), 'package_sha256': module.file_digest(package)}
    module.verify_unchanged_assets(report, binary, package)
    assert report['asset_verification']['unchanged']
    binary.write_bytes(b'modified app')
    with pytest.raises(AssertionError, match='assets changed'):
        module.verify_unchanged_assets(report, binary, package)
    assert report['asset_verification']['unchanged'] is False


def test_native_artifact_canvas_is_allowed_only_through_existing_window_guards():
    source=(Path(__file__).resolve().parents[1]/'desktop/src-tauri/src/workspace.rs').read_text()
    for name in ('workspace_redock','workspace_detach'):
        body=source.split('fn '+name,1)[1].split('#[tauri::command]',1)[0]
        assert 'trusted(&app, &window)?' in body
        assert '["chat", "workbench", "browser", "computer", "artifacts"]' in body
        assert 'valid_session_id(&session_id)' in body


def test_native_pairing_sponsor_has_exact_routes_without_authentication_retargeting():
    source=(Path(__file__).resolve().parents[1]/'desktop/src-tauri/src/workspace.rs').read_text()
    gate=source.split('pub async fn workspace_request',1)[1].split('spawn_blocking',1)[0]
    assert 'trusted(&app, &window)?' in gate
    assert '"/auth/pair/issue"' in gate and '"/auth/pair/revoke"' in gate
    assert '"/auth/pair/exchange"' not in gate and 'path.starts_with("/auth/pair/")' not in gate


def test_every_native_workspace_handler_has_a_scoped_explicit_app_acl():
    """Source contract only: actual compiled ACL and GUI are CI-qualified."""
    root = Path(__file__).resolve().parents[1]/'desktop/src-tauri'
    main = (root/'src/main.rs').read_text()
    handlers = set(re.findall(r'workspace::(workspace_\w+)', main.split('.invoke_handler(', 1)[1].split('.plugin(', 1)[0]))
    build = (root/'build.rs').read_text()
    manifest = set(re.findall(r'"(workspace_\w+)"', build.split('AppManifest::new().commands(', 1)[1]))
    capability = json.loads((root/'capabilities/default.json').read_text())
    allowed = set(capability['permissions'])-{'core:default'}
    assert len(handlers) == 13 and manifest == handlers
    assert allowed == {'allow-'+command.replace('_','-') for command in handlers}
    assert '.app_manifest(manifest)' in build and 'tauri_build::try_build(' in build
    assert capability['windows'] == ['main','connect','workspace-*']
    assert capability['remote']['urls'] == ['http://127.0.0.1:*']
    source = (root/'src/workspace.rs').read_text()
    for command in handlers:
        command_body = source.split('fn '+command+'(',1)[1].split('#[tauri::command]',1)[0]
        assert 'trusted(&app, &window)?' in command_body, command
    gate = source.split('fn trusted(',1)[1].split('fn safe_host_path(',1)[0]
    for boundary in ['!workspace_label(window.label())', 'url.scheme() != "http"',
                     'url.host_str() != Some("127.0.0.1")',
                     'url.port_or_known_default() != Some(port)', 'url.path() != "/"']:
        assert boundary in gate


def test_native_signin_detects_actual_acl_alert_before_entering_credentials():
    import pytest
    module = harness()
    class Driver:
        def element(self,selector):return 'signin' if selector=='.sign-in' else None
        def script(self,script):
            assert '[role="alert"]' in script
            return 'Command workspace_request not allowed by ACL'
        def type(self,*args):raise AssertionError('Rejected form must not receive credentials')
        def click(self,*args):raise AssertionError('Rejected form must not be clicked')
    with pytest.raises(AssertionError,match='Native sign-in screen reports: Command workspace_request not allowed by ACL'):
        module.password_signin(Driver(),'synthetic-owner','synthetic-password',setup=True)


def test_native_workspace_wait_reports_rendered_denial_without_auth_fallback():
    import pytest
    module = harness()
    class Driver:
        def element(self,selector):return None
        def script(self,script):return 'Native actions require the trusted local workspace'
        def api(self,*args):raise AssertionError('Denied sign-in must not bypass its screen')
    with pytest.raises(AssertionError,match='Native actions require the trusted local workspace'):
        module.assert_workspace(Driver())


def test_native_workspace_ready_still_verifies_managed_session_and_url():
    module = harness()
    class Driver:
        def element(self,selector):return 'actual-workspace'
        def script(self,script):
            assert '/[?&]k=/' in script
            return False
        def api(self,path):
            assert path == '/auth/me'
            return {'status':200,'body':{'session_id':'synthetic-managed-session'}}
    assert module.assert_workspace(Driver()) == {'session_id':'synthetic-managed-session'}


def test_native_terminal_cleanup_mutation_validates_against_actual_schema():
    from graphql import parse, validate
    from termx.graphql.schema import schema
    module = harness()
    calls = []
    class Driver:
        def api(self,path,method='GET',body=None):
            calls.append((path,method,body))
            if path.endswith('/terminal'):
                return {'status':200,'body':{'id':'synthetic-terminal'}}
            return {'status':200,'body':{'data':{'delete_session':{'ok':True}}}}
        def call(self,method,path,data=None,session=True):
            return {'ok':True,'connected':True,'bytes':1}
    module.terminal_transport(Driver(),'synthetic-session')
    cleanup = [body for path,method,body in calls if path == '/graphql']
    assert len(cleanup) == 1 and cleanup[0]['variables'] == {'id':'synthetic-terminal'}
    errors = validate(schema._schema, parse(cleanup[0]['query']))
    assert not errors, [error.message for error in errors]


def test_native_scenario_selectors_exist_in_the_shipped_workspace_source():
    root = Path(__file__).resolve().parents[1]
    smoke = (root/'desktop/scripts/native_gui_smoke.py').read_text()
    source = ''.join(path.read_text() for path in (root/'desktop/workspace/src').rglob('*')
                     if path.suffix in {'.tsx','.ts','.css'} and '.test.' not in path.name)
    labels = set(re.findall(r'aria-label=\\?"?([A-Za-z][A-Za-z ]*[A-Za-z])',smoke))
    texts = set(re.findall(r'normalize-space\(\)="([^"]+)"',smoke))
    # password_signin builds this one from its setup flag.
    texts |= {'Create owner account','Continue with password'}
    classes = set(re.findall(r'(?<=[\'",(])\.([a-z]+(?:-[a-z]+)+)',smoke))|set(re.findall(r'@class,"([^"]+)"',smoke))
    assert {'Sign out','Message','Search commands'} <= labels
    texts = {text for text in texts if '+' not in text}
    assert {'Return to main workspace','Unlock workspace','Stop host now','Lock workspace','Commands','Stop this host'} <= texts
    assert {'sign-in','workspace-root','lock-screen','host-stop-effects','command-row'} <= classes
    missing = ([f'aria-label {label}' for label in labels if f'aria-label="{label}"' not in source]
               + [f'text {text}' for text in texts if text not in source]
               + [f'class {name}' for name in classes if name not in source])
    assert not missing, missing


WMCTRL = ('0x04000003  0 host Termx\n'
          '0x04200003  0 host TermX workspace\n'
          '0x04400003  0 host TermX workspace notes\n')


def test_wmctrl_selection_matches_only_the_exact_detached_window_title():
    module = harness()
    assert module.wmctrl_window_ids(WMCTRL,'TermX workspace') == ['0x04200003']
    assert module.wmctrl_window_ids(WMCTRL,'Missing') == []
    assert module.wmctrl_window_ids('malformed\n\n','Termx') == []


def test_linux_close_sends_window_manager_close_to_one_window_and_rejects_ambiguity():
    import pytest, subprocess
    module = harness()
    calls = []
    def run(argv,**kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv,0,stdout=WMCTRL,stderr='')
    module.close_window_linux('TermX workspace',run=run)
    assert calls == [['wmctrl','-l'],['wmctrl','-i','-c','0x04200003']]
    calls.clear()
    with pytest.raises(RuntimeError,match='found 0'):
        module.close_window_linux('Missing',run=run)
    assert calls == [['wmctrl','-l']]
    def duplicate(argv,**kwargs):
        return subprocess.CompletedProcess(argv,0,stdout=WMCTRL+'0x04600003  0 host TermX workspace\n',stderr='')
    with pytest.raises(RuntimeError,match='found 2'):
        module.close_window_linux('TermX workspace',run=duplicate)


class FakeUser32:
    def __init__(self,windows):
        self.windows = windows
        self.posted = []
    def EnumWindows(self,callback,parameter):
        for handle in self.windows:
            callback(handle,parameter)
    def IsWindowVisible(self,handle):
        return self.windows[handle][1]
    def GetWindowTextLengthW(self,handle):
        return len(self.windows[handle][0])
    def GetWindowTextW(self,handle,buffer,size):
        buffer.value = self.windows[handle][0]
    def PostMessageW(self,handle,message,wparam,lparam):
        self.posted.append((handle,message,wparam,lparam))


def test_windows_close_posts_wm_close_only_to_the_single_visible_titled_window():
    import pytest
    module = harness()
    user32 = FakeUser32({1:('Termx',True),2:('TermX workspace',True),3:('TermX workspace',False)})
    module.close_window_windows('TermX workspace',user32)
    assert user32.posted == [(2,0x0010,0,0)]
    with pytest.raises(RuntimeError,match='found 0'):
        module.close_window_windows('Missing',FakeUser32({1:('Termx',True)}))
    both = FakeUser32({1:('TermX workspace',True),2:('TermX workspace',True)})
    with pytest.raises(RuntimeError,match='found 2'):
        module.close_window_windows('TermX workspace',both)
    assert both.posted == []


def test_failure_diagnostics_are_bounded_and_redact_secret_bearing_lines(tmp_path):
    module = harness()
    root = tmp_path/'fixture'
    (root/'native').mkdir(parents=True)
    text = 'setup: start\nrefresh token abc123\nwindow created\n'
    (root/'native'/'desktop.log').write_text(text)
    output = tmp_path/'out'
    output.mkdir()
    module.collect_diagnostics(root,output)
    data = json.loads((output/'diagnostics.json').read_text())
    assert data['log_tails']['native/desktop.log'] == ['setup: start','[redacted line]','window created']
    assert {'path':'native/desktop.log','bytes':len(text)} in data['fixture_tree']
    assert 'abc123' not in json.dumps(data)
    assert isinstance(data['processes'],list)
