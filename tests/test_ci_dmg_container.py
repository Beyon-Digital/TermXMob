"""Injected-command evidence; real signed DMG qualification remains macOS CI."""
from desktop.scripts import create_ci_dmg as module
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess

import pytest

SHA = '1094609da6b390ec46438f9cd32f5cb3dfa1acad'
IDENTITY = 'synthetic-private-signing-port'
CONFIG = {'productName': 'Termx', 'version': '0.2.6', 'identifier': 'com.jaexxxy.termx'}


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    apps = tmp_path / 'macos'
    app = apps / 'Termx.app'
    binary = app / 'Contents/MacOS/termx-desktop'
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b'exact-signed-native-binary')
    binary.chmod(0o755)
    (app / 'Contents/Resources').mkdir()
    (app / 'Contents/Resources/owned-assets').write_bytes(b'exact-original-assets')
    (app / 'Contents/Info.plist').write_bytes(plistlib.dumps({'CFBundleIdentifier': CONFIG['identifier'], 'CFBundleShortVersionString': CONFIG['version'], 'CFBundleExecutable': 'termx-desktop'}))
    if os.name == 'nt':
        # Model the Mac-only staging link in orchestration fixtures. This does
        # not claim actual Windows symlink or macOS file-system qualification.
        def synthetic_link(path, target, target_is_directory=False):
            assert path.name == 'Applications' and str(target) == '/Applications'
            path.write_text('synthetic Applications link model')
        monkeypatch.setattr(Path, 'symlink_to', synthetic_link)
    return {'apps': apps, 'app': app, 'output': tmp_path / 'dmg', 'receipt': tmp_path / 'evidence/container.json', 'binary': binary}


class Commands:
    def __init__(self, fixture, *, fail=None, architecture='arm64', alter_copy=False, alter_create=False):
        self.fixture, self.fail, self.architecture = fixture, fail, architecture
        self.alter_copy, self.alter_create = alter_copy, alter_create
        self.calls = []
        self.staging_link = None

    def __call__(self, argv, timeout):
        assert 0 < timeout <= 900
        self.calls.append(argv)
        if self.fail and argv[:len(self.fail)] == self.fail:
            return subprocess.CompletedProcess(argv, 1, IDENTITY, IDENTITY)
        if argv[0] == 'ditto':
            shutil.copytree(argv[-2], argv[-1], symlinks=True)
            if self.alter_copy:
                (Path(argv[-1]) / 'Contents/Resources/owned-assets').write_bytes(b'changed during copy')
        if argv[:2] == ['hdiutil', 'create']:
            stage = Path(argv[argv.index('-srcfolder') + 1])
            self.staging_link = os.readlink(stage / 'Applications') if os.name != 'nt' else (stage / 'Applications').read_text()
            assert len(list(stage.glob('*.app'))) == 1
            Path(argv[-1]).write_bytes(b'synthetic-complete-compressed-container')
            if self.alter_create:
                (stage / 'Termx.app/Contents/Resources/owned-assets').write_bytes(b'changed during creation')
        output = self.architecture if argv[:2] == ['lipo', '-archs'] else ''
        return subprocess.CompletedProcess(argv, 0, output, '')


def execute(fixture, runner, **kwargs):
    return module.create_container(fixture['apps'], fixture['output'], fixture['receipt'], config=CONFIG, platform='macos-arm64', source_sha=SHA, run_id='37693649699', signing_identity=IDENTITY, runner=runner, **kwargs)


def test_exact_signed_stapled_app_copied_unchanged_into_unmounted_container(fixture):
    runner = Commands(fixture)
    original = module.bundle_digest(fixture['app'])
    result = execute(fixture, runner)
    assert module.bundle_digest(fixture['app']) == original == result['unchanged_application_tree_sha256']
    assert result['executable_sha256'] == module.digest(fixture['binary'])
    assert result['qualified'] is False and result['final_dmg_notarization_required'] is True
    assert result['package'] == 'Termx_0.2.6_aarch64.dmg'
    assert result == json.loads(fixture['receipt'].read_text())
    assert IDENTITY not in fixture['receipt'].read_text()
    assert result['explicit_layout_mount_performed'] is False
    assert not any('attach' in argv or 'detach' in argv or 'xattr' in argv or 'osascript' in argv for argv in runner.calls)
    create = next(argv for argv in runner.calls if argv[:2] == ['hdiutil', 'create'])
    assert create[create.index('-format')+1] == 'UDZO' and '-noskipunreadable' in create
    assert '-attach' not in create and '--force' not in create
    signatures = [argv for argv in runner.calls if argv[:2] == ['codesign', '--force']]
    assert len(signatures) == 1 and signatures[0][-1].endswith('.dmg')
    assert not list(fixture['output'].glob('.termx-dmg-staging-*'))


@pytest.mark.parametrize('fail', [
    ['codesign', '--verify', '--deep'], ['xcrun', 'stapler', 'validate'],
    ['spctl', '--assess'], ['ditto'], ['hdiutil', 'create'],
    ['codesign', '--force'], ['codesign', '--verify', '--strict'], ['hdiutil', 'verify'],
])
def test_no_signature_ticket_staging_or_container_failure_leaves_upload_input(fixture, fail):
    runner = Commands(fixture, fail=fail)
    with pytest.raises(module.TicketError) as error:
        execute(fixture, runner)
    assert IDENTITY not in str(error.value)
    assert not fixture['receipt'].exists()
    assert not list(fixture['output'].glob('*.dmg'))


@pytest.mark.parametrize('phase', ['copy', 'create'])
def test_app_bytes_changed_during_copy_or_creation_are_refused(fixture, phase):
    original = module.bundle_digest(fixture['app'])
    with pytest.raises(module.TicketError, match='changed'):
        execute(fixture, Commands(fixture, alter_copy=phase=='copy', alter_create=phase=='create'))
    assert module.bundle_digest(fixture['app']) == original
    assert not list(fixture['output'].glob('*.dmg')) and not fixture['receipt'].exists()


def test_architecture_mismatch_cannot_be_labelled_correct_installer(fixture):
    runner = Commands(fixture, architecture='x86_64')
    with pytest.raises(module.TicketError, match='architecture'):
        execute(fixture, runner)
    assert not any(argv[0] == 'ditto' for argv in runner.calls)


def test_existing_container_is_not_overwritten_or_replayed(fixture):
    fixture['output'].mkdir()
    old = fixture['output'] / 'old.dmg'
    old.write_bytes(b'old exact artifact')
    runner = Commands(fixture)
    with pytest.raises(module.TicketError, match='existing'):
        execute(fixture, runner)
    assert old.read_bytes() == b'old exact artifact' and not runner.calls


def test_source_version_identity_mismatch_is_refused_before_commands(fixture):
    info = fixture['app'] / 'Contents/Info.plist'
    data = plistlib.loads(info.read_bytes())
    data['CFBundleIdentifier'] = 'untrusted.other.app'
    info.write_bytes(plistlib.dumps(data))
    runner = Commands(fixture)
    with pytest.raises(module.TicketError, match='differs from source'):
        execute(fixture, runner)
    assert not runner.calls


def test_external_app_resource_link_is_refused(fixture):
    if os.name == 'nt':
        pytest.skip('Actual symbolic-link boundary qualification is POSIX; orchestration is modelled separately on Windows')
    (fixture['app'] / 'Contents/Resources/outside').symlink_to('/tmp')
    with pytest.raises(module.TicketError, match='external symbolic link'):
        execute(fixture, Commands(fixture))
    assert not fixture['receipt'].exists()
