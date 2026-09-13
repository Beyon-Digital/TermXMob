#!/usr/bin/env python3
"""Build the PyInstaller onedir sidecar and stage it for the Tauri bundle.

The web UI ships prebuilt in desktop/web (see desktop/scripts/update_web_ui.sh).

Usage (from the repository root):
    uv run --group packaging python desktop/scripts/build_sidecar.py
    uv run --group packaging python desktop/scripts/build_sidecar.py --skip-build
    TERMX_MACOS_SIGN_IDENTITY="Developer ID Application: ..." python desktop/scripts/build_sidecar.py
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DESKTOP = ROOT / "desktop"
WEB = DESKTOP / "web"
STAGE = DESKTOP / "src-tauri" / "resources" / "backend"
BUILD = DESKTOP / "build"
EXE_NAME = "termx-backend.exe" if os.name == "nt" else "termx-backend"


def run(argv: list[str], cwd: Path | None = None) -> None:
    print(f"+ {' '.join(argv)}", flush=True)
    subprocess.run(argv, cwd=str(cwd or ROOT), check=True)


def verify_web(web_dir: Path) -> None:
    if not (web_dir / "index.html").is_file():
        raise SystemExit(
            f"web UI bundle not found at {web_dir}."
            " Run desktop/scripts/update_web_ui.sh to refresh it from the client repository."
        )


def build_sidecar(stage_only: bool = False) -> None:
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
    shutil.rmtree(STAGE, ignore_errors=True)
    STAGE.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(built, STAGE)
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
        print("signtool not found; skipping Windows sidecar signing")
        return
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
    build_sidecar(stage_only=args.skip_build)
    sign_macos()
    sign_windows()
    print("sidecar ready")


if __name__ == "__main__":
    main()
