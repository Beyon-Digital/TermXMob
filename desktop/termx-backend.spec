# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
import os
import sys

from PyInstaller.utils.hooks import collect_submodules, collect_all

ROOT = Path(SPECPATH).parent

datas = [
    (str(ROOT / "src" / "termx" / "static"), "termx/static"),
    (os.environ.get("TERMX_PACKAGE_WEB_DIR", str(ROOT / "desktop" / "workspace" / "dist")), "web"),
]
# Chromium's complete macOS .app/framework layout must survive unchanged.
# PyInstaller otherwise processes individual Mach-O files in a partial cache
# bundle and codesign rejects its missing framework components. Stage the full
# external runtime after COLLECT, before signing and frozen execution checks.
if sys.platform != "darwin":
    datas.append((str(ROOT / "desktop" / "build" / "runtime"), "runtime"))

if ROOT.joinpath("helpers", "macos", "bin").is_dir():
    datas.append((str(ROOT / "helpers" / "macos" / "bin"), "helpers/macos/bin"))

hiddenimports = [
    *collect_submodules("uvicorn"),
    *collect_submodules("websockets"),
    *collect_submodules("qrcode"),
    *collect_submodules("anyio"),
]

binaries = []
# Browser driver, Node, FFmpeg, office schemas and debugger data are runtime assets.
for optional in ("aiortc", "av", "numpy", "playwright", "basedpyright", "nodejs_wheel", "debugpy", "imageio_ffmpeg", "openpyxl", "PIL", "pypdf", "docx", "pptx", "tzdata"):

    try:
        __import__(optional)
    except ImportError:
        continue
    package_data, package_binaries, package_imports = collect_all(optional)
    datas.extend(package_data)
    binaries.extend(package_binaries)
    hiddenimports.extend(package_imports)

excludes = [
    "tkinter",
    "matplotlib",
    "IPython",
    "pytest",
    "_tkinter",
]

a = Analysis(
    [str(ROOT / "desktop" / "backend_entry.py")],
    pathex=[str(ROOT / "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="termx-backend",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="termx-backend",
)
