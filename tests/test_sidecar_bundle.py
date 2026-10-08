"""Complete external app layouts must bypass PyInstaller's partial binary cache."""
import importlib.util
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import pytest


def sidecar_module():
    script = Path(__file__).resolve().parents[1] / 'desktop/scripts/build_sidecar.py'
    spec = importlib.util.spec_from_file_location('sidecar_linux_fixture', script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_linux_private_library_staging_refuses_escaped_or_conflicting_aliases(tmp_path, monkeypatch):
    module = sidecar_module()
    monkeypatch.setattr(module.shutil, 'which', lambda name: '/fixture/patchelf')
    source = tmp_path / 'wheel/av.libs';source.mkdir(parents=True)
    library = source / 'libxcb-5ddf6756.so.1.1.0';library.write_bytes(b'\x7fELFfixture')
    built = tmp_path / 'dist';internal = built / '_internal';internal.mkdir(parents=True)
    (internal / library.name).write_bytes(b'\x7fELFconflicting wheel')
    with pytest.raises(SystemExit, match='disagrees'):
        module.stage_linux_av_libraries(built, source)
    assert (internal / library.name).read_bytes() == b'\x7fELFconflicting wheel'
    if os.name != 'nt':
        (internal / library.name).unlink()
        outside = tmp_path / 'outside.so';outside.write_bytes(b'\x7fELFoutside')
        library.unlink();library.symlink_to(outside)
        with pytest.raises(SystemExit, match='escapes its installed'):
            module.stage_linux_av_libraries(built, source)


@pytest.mark.skipif(sys.platform != 'linux', reason='actual ELF loader closure requires Linux')
def test_linux_av_helpers_resolve_exact_hashed_dependencies_without_bootloader(tmp_path):
    compiler = shutil.which('cc');patcher = shutil.which('patchelf')
    if not compiler or not patcher:
        pytest.skip('Linux compiler and patchelf required for actual ELF closure')
    module = sidecar_module();source = tmp_path / 'wheel/av.libs';source.mkdir(parents=True)
    dependency = 'libxcb-5ddf6756.so.1.1.0';helper = 'libxcb-shm-0be6dfbf.so.0.0.0'
    dep_c = tmp_path / 'dependency.c';dep_c.write_text('int exact_dependency(void) {return 37;}\n')
    help_c = tmp_path / 'helper.c';help_c.write_text('extern int exact_dependency(void); int effect(void) {return exact_dependency();}\n')
    subprocess.run([compiler, '-shared', '-fPIC', str(dep_c), '-Wl,-soname,'+dependency,
                    '-o', str(source / dependency)], check=True, capture_output=True)
    subprocess.run([compiler, '-shared', '-fPIC', str(help_c), '-L'+str(source),
                    '-l:'+dependency, '-Wl,-soname,'+helper, '-o', str(source / helper)],
                   check=True, capture_output=True)
    subprocess.run([patcher, '--set-rpath', '$ORIGIN/other', str(source / helper)], check=True)
    env = dict(os.environ);env.pop('LD_LIBRARY_PATH', None)
    before = subprocess.run(['ldd', str(source / helper)], env=env, check=True,
                            capture_output=True, text=True).stdout
    assert dependency+' => not found' in before
    built = tmp_path / 'dist';internal = built / '_internal';internal.mkdir(parents=True)
    # A different media wheel's library must never replace the exact DT_NEEDED.
    other = internal / 'libxcb-ad31f5a3.so.1.1.0';other.write_bytes(b'different wheel untouched')
    module.stage_linux_av_libraries(built, source)
    module.stage_linux_av_libraries(built, source)  # stage-only retries remain safe.
    staged = internal / 'av.libs' / helper
    after = subprocess.run(['ldd', str(staged)], env=env, check=True,
                           capture_output=True, text=True).stdout
    assert 'not found' not in after and str(internal / 'av.libs' / dependency) in after
    needed = subprocess.run([patcher, '--print-needed', str(staged)], check=True,
                            capture_output=True, text=True).stdout.splitlines()
    assert dependency in needed and other.name not in needed
    paths = subprocess.run([patcher, '--print-rpath', str(staged)], check=True,
                           capture_output=True, text=True).stdout.strip().split(':')
    assert paths == ['$ORIGIN/other', '$ORIGIN']
    assert other.read_bytes() == b'different wheel untouched'
    assert (internal / dependency).resolve() == internal / 'av.libs' / dependency
    # Run in a new process with no freezer or global LD_LIBRARY_PATH assistance.
    loaded = subprocess.run([sys.executable, '-c',
        'import ctypes,sys; x=ctypes.CDLL(sys.argv[1]); assert x.effect()==37', str(staged)],
        env=env, check=True, capture_output=True)
    assert loaded.returncode == 0


@pytest.mark.skipif(os.name == 'nt', reason='macOS bundle staging is not used on Windows')
def test_macos_runtime_staging_preserves_complete_framework_and_relative_links(tmp_path):
    script = Path(__file__).resolve().parents[1] / 'desktop/scripts/build_sidecar.py'
    spec = importlib.util.spec_from_file_location('sidecar_bundle_fixture', script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    runtime = tmp_path / 'prepared'
    framework = runtime / 'browsers/Chrome.app/Contents/Frameworks/Chrome.framework'
    version = framework / 'Versions/A'
    (version / 'Resources').mkdir(parents=True)
    (version / 'Libraries').mkdir()
    (version / 'Chrome').write_bytes(b'fixture executable')
    (version / 'Resources/Info.plist').write_bytes(b'fixture complete metadata')
    (version / 'Libraries/helper.dylib').write_bytes(b'fixture dependency')
    (framework / 'Versions/Current').symlink_to('A', target_is_directory=True)
    (framework / 'Chrome').symlink_to('Versions/Current/Chrome')
    for name in ('Resources', 'Libraries'):
        (framework / name).symlink_to('Versions/Current/' + name, target_is_directory=True)
    (runtime / 'manifest.json').write_text('{}')
    built = tmp_path / 'dist'
    module.stage_macos_runtime(built, runtime)
    installed = built / '_internal/runtime' / framework.relative_to(runtime)
    assert (installed / 'Versions/Current').is_symlink()
    assert (installed / 'Chrome').is_symlink()
    assert (installed / 'Resources').is_symlink()
    assert (installed / 'Libraries').is_symlink()
    assert (installed / 'Resources/Info.plist').read_bytes() == b'fixture complete metadata'
    assert (installed / 'Libraries/helper.dylib').read_bytes() == b'fixture dependency'
    assert (installed / 'Chrome').resolve().is_relative_to(built.resolve())


@pytest.mark.skipif(sys.platform != 'darwin', reason='actual codesign needs macOS')
def test_actual_macho_framework_signing_keeps_jit_entitlements_and_complete_layout(tmp_path):
    compiler = shutil.which('cc')
    codesign = shutil.which('codesign')
    if not compiler or not codesign:
        pytest.skip('macOS compiler and codesign required for bundle regression')
    root = Path(__file__).resolve().parents[1]
    source = tmp_path / 'fixture.c'
    source.write_text('int main(void) { return 0; }\n')
    app = tmp_path / 'runtime/Google Chrome for Testing.app'
    framework = app / 'Contents/Frameworks/Google Chrome Framework.framework'
    version = framework / 'Versions/A'
    helper = version / 'Helpers/Google Chrome for Testing Helper.app'

    def executable_bundle(bundle, name, identifier):
        executable = bundle / 'Contents/MacOS' / name
        executable.parent.mkdir(parents=True)
        subprocess.run([compiler, str(source), '-o', str(executable)], check=True,
                       capture_output=True)
        (bundle / 'Contents/Info.plist').write_bytes(plistlib.dumps({
            'CFBundleExecutable': name, 'CFBundleIdentifier': identifier,
            'CFBundlePackageType': 'APPL', 'CFBundleVersion': '1'}))
        return executable

    executable = executable_bundle(app, 'Google Chrome for Testing', 'test.termx.browser')
    helper_executable = executable_bundle(helper, 'Google Chrome for Testing Helper',
                                         'test.termx.browser.helper')
    (version / 'Resources').mkdir(parents=True)
    (version / 'Resources/Info.plist').write_bytes(plistlib.dumps({
        'CFBundleExecutable': 'Google Chrome Framework',
        'CFBundleIdentifier': 'test.termx.browser.framework',
        'CFBundlePackageType': 'FMWK', 'CFBundleVersion': '1'}))
    subprocess.run([compiler, '-dynamiclib', str(source), '-o',
                    str(version / 'Google Chrome Framework')], check=True, capture_output=True)
    (framework / 'Versions/Current').symlink_to('A', target_is_directory=True)
    (framework / 'Google Chrome Framework').symlink_to('Versions/Current/Google Chrome Framework')
    for component in ('Resources', 'Helpers'):
        (framework / component).symlink_to('Versions/Current/' + component, target_is_directory=True)
    subprocess.run(['sh', str(root / 'desktop/scripts/sign_macos_sidecar.sh'), '-',
                    str(tmp_path / 'runtime')], check=True, capture_output=True)
    for bundle in (helper, framework, app):
        subprocess.run([codesign, '--verify', '--strict', str(bundle)], check=True,
                       capture_output=True)
    for binary in (executable, helper_executable):
        signed = subprocess.run([codesign, '-d', '--entitlements', ':-', str(binary)],
                                check=True, capture_output=True)
        entitlements = plistlib.loads(signed.stdout)
        assert entitlements['com.apple.security.cs.allow-jit'] is True
    assert (framework / 'Resources').is_symlink()
    assert (framework / 'Helpers').is_symlink()
    assert (framework / 'Helpers').resolve().is_relative_to(tmp_path.resolve())
