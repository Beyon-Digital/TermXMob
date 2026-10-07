"""Source checks for native fixture isolation; these do not prove a native GUI."""
import importlib.util
import os
from pathlib import Path


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
