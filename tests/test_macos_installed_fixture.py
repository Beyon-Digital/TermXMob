"""Artifact-binding and OS-input fixture checks, not installed GUI evidence."""
import importlib.util
from pathlib import Path
import sys

import pytest


def load(name):
    directory = Path(__file__).resolve().parents[1]/'desktop/scripts'
    sys.path.insert(0, str(directory))
    try:
        spec = importlib.util.spec_from_file_location(name, directory/(name+'.py'))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(directory))


def test_installer_requires_exact_commit_and_desktop_dispatch():
    module = load('fetch_macos_installer')
    commit = 'a'*40
    run = {'head_sha': commit, 'event': 'workflow_dispatch', 'path': '.github/workflows/desktop.yml'}
    module.validate_source(run, commit)
    for row, expected in [(dict(run, head_sha='b'*40), commit),
                          (dict(run, event='push'), commit),
                          (dict(run, path='.github/workflows/other.yml'), commit),
                          (run, 'main')]:
        with pytest.raises(ValueError):
            module.validate_source(row, expected)


def test_installer_selects_exact_unexpired_platform_artifact():
    module = load('fetch_macos_installer')
    target = {'name': 'termx-macos-arm64-77', 'expired': False}
    rows = [target, {'name': 'termx-macos-x86_64-77', 'expired': False}]
    assert module.select_artifact(rows, 'macos-arm64', 77) is target
    for values in [[], [dict(target, expired=True)], [target, dict(target)]]:
        with pytest.raises(ValueError):
            module.select_artifact(values, 'macos-arm64', 77)


def test_installer_refuses_other_signed_bundle_or_version():
    module = load('fetch_macos_installer')
    config = {'identifier': 'com.jaexxxy.termx', 'version': '0.2.6'}
    info = {'CFBundleIdentifier': config['identifier'], 'CFBundleShortVersionString': config['version']}
    module.validate_bundle(info, config)
    for row in [dict(info, CFBundleIdentifier='other.signed.app'),
                dict(info, CFBundleShortVersionString='0.2.5')]:
        with pytest.raises(ValueError):
            module.validate_bundle(row, config)


def test_native_keyboard_targets_owned_process_and_quotes_fixture_text():
    module = load('macos_installed_gui')
    script = module.keyboard_script(12345, 'fixture "quoted" value')
    assert 'whose unix id is 12345' in script
    assert r'keystroke "fixture \"quoted\" value"' in script
    for invalid in [True, 0, -1, '12345']:
        with pytest.raises(ValueError):
            module.keyboard_script(invalid, 'fixture')


def test_native_recovery_requires_entire_real_work_area():
    module = load('macos_installed_gui')
    area = {'x': 0, 'y': 24, 'width': 1024, 'height': 700}
    assert module.reachable_window([0, 24], [1024, 700], area)
    assert not module.reachable_window([-500, 24], [760, 520], area)
    assert not module.reachable_window([1023, 24], [760, 520], area)
    assert not module.reachable_window([0, 0], [1024, 724], area)
