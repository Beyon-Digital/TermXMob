# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH).parent

datas = [
    (str(ROOT / "src" / "termx" / "static"), "termx/static"),
    (str(ROOT / "desktop" / "web"), "web"),
]

if ROOT.joinpath("helpers", "macos", "bin").is_dir():
    datas.append((str(ROOT / "helpers" / "macos" / "bin"), "helpers/macos/bin"))

hiddenimports = [
    *collect_submodules("uvicorn"),
    *collect_submodules("websockets"),
    *collect_submodules("qrcode"),
    *collect_submodules("anyio"),
]

for optional in ("aiortc", "av", "numpy"):
    try:
        __import__(optional)
    except ImportError:
        continue
    hiddenimports.extend(collect_submodules(optional))

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
    binaries=[],
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
