# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
import os
import sys

from PyInstaller.utils.hooks import collect_submodules, collect_all, copy_metadata

ROOT = Path(SPECPATH).parent

datas = [
    (str(ROOT / "src" / "termx" / "static"), "termx/static"),
    (str(ROOT / "src" / "termx" / "characters" / "assets"), "termx/characters/assets"),
    # The pinned agent-desktop version and sha256 hashes desktop control checks before it runs the program.
    (str(ROOT / "src" / "termx" / "desktop" / "agent_desktop_pins.json"), "termx/desktop"),
    (str(ROOT / "src" / "termx" / "media" / "studio_models.json"), "termx/media"),
    (str(ROOT / "src" / "termx" / "workspace" / "workflows" / "worker.py"), "termx/workspace/workflows"),
    (str(ROOT / "src" / "termx" / "workspace" / "workflows" / "bridge.js"), "termx/workspace/workflows"),
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
    'termx.workspace.workflows.worker',
    # SQLGlot loads dialects through importlib. Include each Data adapter's
    # parser explicitly; source imports alone omit SQLite/PostgreSQL in frozen apps.
    'sqlglot.dialects.sqlite',
    'sqlglot.dialects.postgres',
    'sqlglot.dialects.mysql',
    'termx.sandbox._win_shim',
    'termx.sandbox._win_conpty',
    'termx.sandbox._win_conpty_host',
    'termx.sandbox._win_conpty_worker',
    'termx.sandbox._win_conpty_protocol',
    'termx.sandbox._win_conpty_security',
    *collect_submodules("uvicorn"),
    *collect_submodules("websockets"),
    *collect_submodules("qrcode"),
    *collect_submodules("anyio"),
]

binaries = []
# Browser driver, Node, FFmpeg, office schemas and debugger data are runtime assets.
for optional in ("aiortc", "av", "numpy", "playwright", "basedpyright", "nodejs_wheel", "debugpy", "imageio_ffmpeg", "openpyxl", "PIL", "pypdf", "docx", "pptx", "tzdata", "quickjs"):

    try:
        __import__(optional)
    except ImportError:
        continue
    package_data, package_binaries, package_imports = collect_all(optional)
    datas.extend(package_data)
    binaries.extend(package_binaries)
    hiddenimports.extend(package_imports)
    if optional == "quickjs":
        # quickjs-ng exposes quickjs plus its native _quickjs extension. Keep
        # distribution metadata for the runtime's exact dependency probe.
        hiddenimports.extend(["quickjs", "_quickjs"])
        datas.extend(copy_metadata("quickjs-ng"))

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

# Static ctypes analysis sees Linux's optional X11 loader even on macOS.
# Do not ship unused build-host libraries with a newer deployment minimum.
sys.path.insert(0, str(ROOT / 'desktop' / 'scripts'))
from build_sidecar import platform_binaries
a.binaries = platform_binaries(a.binaries, sys.platform)

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
