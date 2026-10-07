#!/usr/bin/env python3
"""Build the PyInstaller onedir sidecar and stage it for the Tauri bundle.

The dedicated workspace is built from desktop/workspace; runtime assets are staged in CI.

Usage (from the repository root):
    uv run --group packaging python desktop/scripts/build_sidecar.py
    uv run --group packaging python desktop/scripts/build_sidecar.py --skip-build
    TERMX_MACOS_SIGN_IDENTITY="Developer ID Application: ..." python desktop/scripts/build_sidecar.py
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DESKTOP = ROOT / "desktop"
WEB = DESKTOP / "workspace" / "dist"
STAGE = DESKTOP / "src-tauri" / "resources" / "backend"
BUILD = DESKTOP / "build"
EXE_NAME = "termx-backend.exe" if os.name == "nt" else "termx-backend"


def run(argv: list[str], cwd: Path | None = None) -> None:
    shown = argv.copy()
    if "/p" in shown:
        shown[shown.index("/p") + 1] = "<redacted>"
    print(f"+ {' '.join(shown)}", flush=True)
    subprocess.run(argv, cwd=str(cwd or ROOT), check=True)


def verify_web(web_dir: Path) -> None:
    if not (web_dir / "index.html").is_file():
        raise SystemExit(
            f"web UI bundle not found at {web_dir}."
            " Run pnpm --dir desktop/workspace build to build the dedicated workspace."
        )

    import re
    index = (web_dir / "index.html").read_text(encoding="utf-8")
    if not re.search(r'<meta\s+name=[\"\']termx-ui-contract[\"\']\s+content=[\"\']3[\"\']', index):
        raise SystemExit("Web UI contract 3 missing; rebuild desktop/workspace")


def stage_macos_runtime(built: Path, runtime: Path) -> None:
    """Copy complete external bundles without flattening framework symlinks."""
    target = built / "_internal" / "runtime"
    if not (runtime / "manifest.json").is_file():
        raise SystemExit("Prepared macOS runtime manifest is missing")
    shutil.rmtree(target, ignore_errors=True)
    shutil.copytree(runtime, target, symlinks=True)


def stage_linux_av_libraries(built: Path, source: Path | None = None) -> None:
    """Keep auditwheel's exact AV library closure usable outside the bootloader.

    PyInstaller discovers libraries lazily and normally supplies LD_LIBRARY_PATH.
    linuxdeploy also inspects each ELF independently, including AV's helper libs
    whose wheels have no RPATH. Preserve the complete installed av.libs siblings,
    then give only these private libraries a relative search path. Never replace
    their hashed dependencies with similarly named system/Pillow libraries.
    """
    if source is None:
        spec = importlib.util.find_spec('av')
        if spec is None or not spec.origin:
            return
        source = Path(spec.origin).parent.parent / 'av.libs'
    if not source.is_dir():
        return  # Source-built AV can use system libraries without an auditwheel dir.
    patcher = shutil.which('patchelf')
    if not patcher:
        raise SystemExit('patchelf is required to stage Linux AV wheel libraries')
    internal = built / '_internal'
    target = internal / 'av.libs'
    target.mkdir(parents=True, exist_ok=True)
    libraries = sorted(source.glob('lib*.so*'))
    for library in libraries:
        if not library.resolve().is_relative_to(source.resolve()):
            raise SystemExit(f'AV wheel library escapes its installed directory: {library.name}')
        with library.open('rb') as file:
            if file.read(4) != b'\x7fELF':
                raise SystemExit(f'AV wheel library is not ELF: {library.name}')
        destination = target / library.name
        # Do not accidentally follow an existing freezer symlink into the venv.
        destination.unlink(missing_ok=True)
        shutil.copy2(library, destination)
        alias = internal / library.name
        if alias.exists() or alias.is_symlink():
            if not alias.resolve().is_relative_to(internal.resolve()):
                raise SystemExit(f'Frozen AV library alias escapes the sidecar: {library.name}')
            # The current spec disables strip/UPX; PyInstaller leaves Linux ELF
            # input unchanged. A different alias is a collision, not permission
            # to substitute another wheel or a system library.
            if hashlib.sha256(alias.read_bytes()).digest() != hashlib.sha256(destination.read_bytes()).digest():
                raise SystemExit(f'Frozen AV library disagrees with its installed wheel: {library.name}')
            alias.unlink()
        alias.symlink_to(Path('av.libs') / library.name)
    for library in libraries:
        destination = target / library.name
        prior = subprocess.run([patcher, '--print-rpath', str(destination)],
                               check=True, capture_output=True, text=True).stdout.strip()
        entries = [value for value in prior.split(':') if value]
        if '$ORIGIN' not in entries:
            subprocess.run([patcher, '--set-rpath', ':'.join([*entries, '$ORIGIN']),
                            str(destination)], check=True)
    loader_environment = dict(os.environ)
    loader_environment.pop('LD_LIBRARY_PATH', None)
    loader_environment.pop('LD_PRELOAD', None)
    for library in libraries:
        closure = subprocess.run(['ldd', str(target / library.name)], check=True,
                                 capture_output=True, text=True, env=loader_environment)
        if 'not found' in closure.stdout or 'not found' in closure.stderr:
            raise SystemExit(f'Linux AV library dependency closure failed: {library.name}: '
                             f'{(closure.stdout + closure.stderr)[:8192]}')
    print(f'staged {len(libraries)} exact AV wheel libraries with relative dependency paths', flush=True)


def build_sidecar(stage_only: bool = False, web_dir: Path = WEB) -> None:
    os.environ["TERMX_PACKAGE_WEB_DIR"] = str(web_dir.resolve())
    if not stage_only:
        for path in (BUILD / "dist", BUILD / "work"):
            shutil.rmtree(path, ignore_errors=True)
        run(
            [
                sys.executable,
                "-m",
                "PyInstaller",
                "--clean",
                "--noconfirm",
                "--distpath",
                str(BUILD / "dist"),
                "--workpath",
                str(BUILD / "work"),
                str(DESKTOP / "termx-backend.spec"),
            ]
        )
    built = BUILD / "dist" / "termx-backend"
    binary = built / EXE_NAME
    if not binary.is_file():
        raise SystemExit(f"PyInstaller did not produce {binary}")
    if sys.platform == "darwin" and not stage_only:
        stage_macos_runtime(built, BUILD / "runtime")
    if sys.platform == 'linux':
        stage_linux_av_libraries(built)
    shutil.rmtree(STAGE, ignore_errors=True)
    STAGE.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(built, STAGE, symlinks=sys.platform == "darwin")
    print(f"staged sidecar at {STAGE}")


def sign_macos() -> None:
    identity = os.environ.get("TERMX_MACOS_SIGN_IDENTITY")
    if sys.platform != "darwin" or not identity:
        return
    run(["sh", str(DESKTOP / "scripts" / "sign_macos_sidecar.sh"), identity, str(STAGE)])


def sign_windows() -> None:
    if os.name != "nt":
        return
    certificate = os.environ.get("TERMX_WINDOWS_PFX") or os.environ.get("WINDOWS_CERTIFICATE")
    password = os.environ.get("TERMX_WINDOWS_PFX_PASSWORD") or os.environ.get("WINDOWS_CERTIFICATE_PASSWORD")
    if not certificate or not password:
        return
    signtool = os.environ.get("TERMX_SIGNTOOL") or shutil.which("signtool")
    if not signtool:
        raise SystemExit("Windows signing requested but signtool is unavailable")
    cert_path = Path(certificate)
    temp_cert = None
    if not cert_path.is_file():
        import base64
        import tempfile

        handle, name = tempfile.mkstemp(suffix=".p12")
        os.close(handle)
        temp_cert = Path(name)
        temp_cert.write_bytes(base64.b64decode(certificate))
        cert_path = temp_cert
    try:
        for target in sorted(STAGE.rglob("*.exe")):
            run(
                [
                    signtool,
                    "sign",
                    "/fd",
                    "sha256",
                    "/tr",
                    "http://timestamp.digicert.com",
                    "/td",
                    "sha256",
                    "/f",
                    str(cert_path),
                    "/p",
                    password,
                    str(target),
                ]
            )
    finally:
        if temp_cert is not None:
            temp_cert.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--web-dir", type=Path, default=WEB, help="web UI bundle to package")
    parser.add_argument("--skip-build", action="store_true", help="only stage/sign an existing build")
    args = parser.parse_args()
    verify_web(args.web_dir)
    if not args.skip_build and not (BUILD / "runtime" / "manifest.json").is_file():
        raise SystemExit("Runtime assets missing; run desktop/scripts/prepare_runtime.py first")
    build_sidecar(stage_only=args.skip_build, web_dir=args.web_dir)
    sign_macos()
    sign_windows()
    print("sidecar ready")


if __name__ == "__main__":
    main()
