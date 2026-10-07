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
                 'LOCALAPPDATA','WEBVIEW2_USER_DATA_FOLDER','TERMX_DESKTOP_DATA_DIR']:
        assert Path(environment[name]).is_absolute()
        assert Path(environment[name]).is_relative_to(tmp_path.resolve())
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
