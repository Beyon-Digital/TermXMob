import json
import os
from pathlib import Path
import plistlib
import struct
import subprocess
import sys

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).parents[1] / 'desktop/scripts'))
from artifact_matrix import build_matrix
from build_macos_openssl import configure_arguments, unpack_source
from verify_macos_compatibility import INTEL, intel_minimums, verify

ROOT = Path(__file__).parents[1]
ARM = 0x0100000C


def binary(minimum=(13, 7, 8), *, cpu=INTEL, endian='<', legacy=False, platform=1):
    value = minimum[0] << 16 | minimum[1] << 8 | minimum[2]
    # Deliberately use a newer SDK: SDK != minimum supported runtime.
    command = (struct.pack(endian + '4I', 0x24, 16, value, 26 << 16) if legacy else
               struct.pack(endian + '6I', 0x32, 24, platform, value, 26 << 16, 0))
    return struct.pack(endian + '8I', 0xFEEDFACF, cpu, 3, 2, 1, len(command), 0, 0) + command


def fat(intel, arm, *, wide=False, endian='>'):
    offset = 8 + 2 * (32 if wide else 20)
    header = struct.pack(endian + 'II', 0xCAFEBABF if wide else 0xCAFEBABE, 2)
    for cpu, body in ((INTEL, intel), (ARM, arm)):
        header += (struct.pack(endian + 'IIQQII', cpu, 3, offset, len(body), 0, 0) if wide else
                   struct.pack(endian + '5I', cpu, 3, offset, len(body), 0))
        offset += len(body)
    return header + intel + arm


@pytest.mark.parametrize('legacy', [True, False])
@pytest.mark.parametrize('endian', ['<', '>'])
def test_reads_actual_deployment_minimum_not_newer_sdk(tmp_path, legacy, endian):
    target = tmp_path / 'runtime'
    target.write_bytes(binary(legacy=legacy, endian=endian))
    assert intel_minimums(target) == ['13.7.8']
    assert verify(tmp_path, '13.7.8', ['runtime'])['errors'] == []


@pytest.mark.parametrize('wide', [True, False])
@pytest.mark.parametrize('endian', ['<', '>'])
def test_fat_binaries_check_intel_slice_independently(tmp_path, wide, endian):
    target = tmp_path / 'helper'
    target.write_bytes(fat(binary(), binary((15, 0, 0), cpu=ARM), wide=wide, endian=endian))
    assert verify(tmp_path, '13.7.8', ['helper'])['errors'] == []
    target.write_bytes(fat(binary((14, 0, 0)), binary(cpu=ARM), wide=wide, endian=endian))
    assert 'requires macOS 14.0.0' in '\n'.join(verify(tmp_path, '13.7.8', [])['errors'])


@pytest.mark.parametrize('payload', [
    binary()[:10],
    binary()[:-1],
    binary(platform=2),
    struct.pack('<8I', 0xFEEDFACF, INTEL, 3, 2, 0, 0, 0, 0),
    struct.pack('<8I', 0xFEEDFACF, INTEL, 3, 2, 1, 8, 0, 0) + struct.pack('<II', 0x32, 0),
    struct.pack('>II5I', 0xCAFEBABE, 1, INTEL, 3, 9000, 100, 0),
])
def test_malformed_or_non_macos_binaries_fail_closed(tmp_path, payload):
    (tmp_path / 'bad').write_bytes(payload)
    assert verify(tmp_path, '13.7.8', ['bad'])['errors']


def test_missing_or_wrong_architecture_required_binary_fails(tmp_path):
    (tmp_path / 'arm').write_bytes(binary(cpu=ARM))
    (tmp_path / 'intel').write_bytes(binary())
    (tmp_path / 'text').write_text('not a binary')
    errors = verify(tmp_path, '13.7.8', ['arm', 'text', 'absent'])['errors']
    assert len(errors) == 3
    assert all('missing or unverified' in error for error in errors)


@pytest.mark.skipif(os.name == 'nt', reason='Framework symlink fixture requires Unix symlink permissions')
def test_nested_bundle_metadata_and_framework_links_are_checked(tmp_path):
    framework = tmp_path / 'Framework.framework'
    current = framework / 'Versions/A'
    current.mkdir(parents=True)
    (current / 'Framework').write_bytes(binary())
    (framework / 'Versions/Current').symlink_to('A', target_is_directory=True)
    (framework / 'Framework').symlink_to('Versions/Current/Framework')
    info = current / 'Info.plist'
    info.write_bytes(plistlib.dumps({'LSMinimumSystemVersion': '14.0'}))
    report = verify(tmp_path, '13.7.8', ['Framework.framework/Framework'])
    assert len(report['binaries']) == 1
    assert len(report['errors']) == 1 and 'Info.plist' in report['errors'][0]
    info.write_bytes(plistlib.dumps({'LSMinimumSystemVersion': '14.0',
                                   'LSMinimumSystemVersionByArchitecture': {'x86_64': '13.0'}}))
    assert verify(tmp_path, '13.7.8', [])['errors'] == []


@pytest.mark.skipif(os.name == 'nt', reason='Framework symlink fixture requires Unix symlink permissions')
def test_external_links_are_rejected(tmp_path):
    root = tmp_path / 'root'
    root.mkdir()
    (tmp_path / 'external').write_bytes(binary())
    (root / 'escape').symlink_to('../external')
    assert any('external symbolic link' in error for error in verify(root, '13.7.8', [])['errors'])


def test_application_executable_is_taken_from_metadata_and_floor_is_exact(tmp_path):
    root = tmp_path / 'Termx.app'
    executable = root / 'Contents/MacOS/Termx'
    executable.parent.mkdir(parents=True)
    executable.write_bytes(binary())
    info = root / 'Contents/Info.plist'
    info.write_bytes(plistlib.dumps({'CFBundleExecutable': 'Termx', 'LSMinimumSystemVersion': '13.7.8'}))
    assert verify(root, '13.7.8', [])['errors'] == []
    info.write_bytes(plistlib.dumps({'CFBundleExecutable': 'missing', 'LSMinimumSystemVersion': '15.0'}))
    errors = verify(root, '13.7.8', [])['errors']
    assert any('minimum does not match' in error for error in errors)
    assert any('missing or unverified' in error for error in errors)


def test_cli_writes_failure_report_and_exits_nonzero(tmp_path):
    (tmp_path / 'too-new').write_bytes(binary((15, 0, 0)))
    report = tmp_path / 'report.json'
    result = subprocess.run([sys.executable, str(ROOT / 'desktop/scripts/verify_macos_compatibility.py'),
                             str(tmp_path), '--minimum', '13.7.8', '--report', str(report)],
                            text=True, capture_output=True)
    assert result.returncode != 0
    assert 'requires macOS 15.0.0' in result.stderr
    assert json.loads(report.read_text())['errors']


def test_release_applies_intel_target_before_compilation_and_gates_packaging(tmp_path):
    rows = build_matrix(event='push', release_tag='v0.2.11')['include']
    intel = next(row for row in rows if row['name'] == 'macos-x86_64')
    assert intel['minimum_macos'] == '13.7.8'
    assert all('minimum_macos' not in row for row in rows if row != intel)
    workflow = yaml.safe_load((ROOT / '.github/workflows/desktop.yml').read_text())
    steps = workflow['jobs']['build']['steps']
    by_name = {step.get('name'): step for step in steps}
    configure = by_name['Configure Intel Ventura deployment target']
    config = tmp_path / 'desktop/src-tauri/tauri.conf.json'
    config.parent.mkdir(parents=True)
    original = json.loads((ROOT / 'desktop/src-tauri/tauri.conf.json').read_text())
    config.write_text(json.dumps(original))
    environment = tmp_path / 'github-env'
    configure_script = configure['run'].split("<<'PY'\n", 1)[1].rsplit('\nPY', 1)[0]
    subprocess.run([sys.executable, '-c', configure_script], cwd=tmp_path, check=True,
                   env={**os.environ, 'GITHUB_ENV': str(environment), 'MINIMUM_MACOS': intel['minimum_macos']})
    assert environment.read_text() == 'MACOSX_DEPLOYMENT_TARGET=13.7.8\n'
    updated = json.loads(config.read_text())
    assert updated['bundle']['macOS']['minimumSystemVersion'] == '13.7.8'
    updated['bundle']['macOS']['minimumSystemVersion'] = original['bundle']['macOS']['minimumSystemVersion']
    assert updated == original
    assert steps.index(configure) < next(i for i, step in enumerate(steps) if step.get('uses', '').startswith('dtolnay/'))
    for action in ('Swatinem/rust-cache@v2', 'astral-sh/setup-uv@v5'):
        cache = next(step for step in steps if step.get('uses') == action)
        assert 'matrix.minimum_macos' in str(cache['with'])
    install = by_name['Install complete backend and packaging dependencies']['run']
    assert '--python-platform {0} --no-binary-package debugpy' in install
    assert '--no-binary-package cryptography' in install
    tls = by_name['Build Ventura-compatible Intel TLS libraries']
    attach = by_name['Build Ventura-compatible debugger attach helper']
    assert tls['if'] == attach['if'] == "matrix.minimum_macos != ''"
    assert steps.index(tls) < steps.index(by_name['Install complete backend and packaging dependencies'])
    assert steps.index(attach) < steps.index(by_name['Build complete workspace runtime and Python sidecar'])
    assert 'compile_mac.sh' in attach['run'] and 'check=True' in attach['run']
    assert 'attach.dylib' in by_name['Verify Intel sidecar Ventura compatibility']['run']
    for name in ('Verify Intel sidecar Ventura compatibility', 'Verify Intel application Ventura compatibility'):
        gate = by_name[name]
        assert gate['if'] == "matrix.minimum_macos != ''"
        assert 'continue-on-error' not in gate
        assert 'verify_macos_compatibility.py' in gate['run']
        assert steps.index(gate) < steps.index(by_name['Create signed macOS container without layout mounting'])


def test_tls_source_checksum_is_checked_before_unpacking(tmp_path):
    archive = tmp_path / 'openssl.tar.gz'
    archive.write_bytes(b'Untrusted or truncated download')
    destination = tmp_path / 'source'
    with pytest.raises(ValueError, match='checksum mismatch'):
        unpack_source(archive, destination)
    assert not destination.exists()


def test_tls_build_targets_ventura_and_produces_static_libraries(tmp_path):
    arguments = configure_arguments(tmp_path / 'tls', '13.7.8')
    assert 'darwin64-x86_64-cc' in arguments
    assert 'no-shared' in arguments and 'no-module' in arguments
    assert '-mmacosx-version-min=13.7.8' in arguments
    assert '--libdir=lib' in arguments
    with pytest.raises(ValueError, match='absolute'):
        configure_arguments(Path('relative-prefix'), '13.7.8')
    with pytest.raises(ValueError, match='Invalid macOS'):
        configure_arguments(tmp_path, '13.7.8\nINVALID=1')
