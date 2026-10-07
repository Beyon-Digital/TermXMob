"""Resolve installed tools and CI-packaged workspace runtimes without PATH guessing."""
from __future__ import annotations
import importlib.util
import os
from pathlib import Path
import shutil
import sys
from termx.desktop.paths import bundle_root

def assets_root() -> Path | None:
    bundled = bundle_root()
    if bundled: return bundled / 'runtime'
    configured = os.environ.get('TERMX_RUNTIME_ASSETS')
    return Path(configured).resolve() if configured else None

def node_binary() -> str | None:
    bundled = bundle_root()
    if bundled:
        candidate = bundled / 'nodejs_wheel' / ('node.exe' if os.name == 'nt' else 'bin/node')
        if candidate.is_file(): return str(candidate)
    # The development extra supplies the same platform Node wheel packaged by CI.
    spec = importlib.util.find_spec('nodejs_wheel')
    if spec and spec.origin:
        candidate = Path(spec.origin).parent / ('node.exe' if os.name == 'nt' else 'bin/node')
        if candidate.is_file(): return str(candidate)
    return shutil.which('node')

def python_interpreter() -> str | None:
    if not getattr(sys, 'frozen', False): return sys.executable
    configured = os.environ.get('TERMX_PYTHON_INTERPRETER')
    if configured:
        candidate = shutil.which(configured)
        if candidate and Path(candidate).resolve() != Path(sys.executable).resolve(): return candidate
    for name in ('python3', 'python'):
        candidate = shutil.which(name)
        if candidate and Path(candidate).resolve() != Path(sys.executable).resolve(): return candidate
    return None

def python_adapter() -> list[str] | None:
    if not importlib.util.find_spec('debugpy') or not python_interpreter(): return None
    if getattr(sys, 'frozen', False): return [sys.executable, '--runtime-module', 'debugpy.adapter']
    return [sys.executable, '-m', 'debugpy.adapter']

def debug_server() -> str | None:
    configured = os.environ.get('TERMX_JS_DEBUG_SERVER')
    root = assets_root()
    candidate = Path(configured) if configured else root / 'js-debug/src/dapDebugServer.js' if root else None
    return str(candidate) if candidate and candidate.is_file() else None

def language_command(language: str) -> tuple[str, ...] | None:
    root, node = assets_root(), node_binary()
    if not root or not node: return None
    modules = root / 'language/node_modules'
    paths = {
        'javascript': 'typescript-language-server/lib/cli.mjs',
        'typescript': 'typescript-language-server/lib/cli.mjs',
        'jsx': 'typescript-language-server/lib/cli.mjs', 'tsx': 'typescript-language-server/lib/cli.mjs',
        'json': 'vscode-langservers-extracted/bin/vscode-json-language-server',
        'html': 'vscode-langservers-extracted/bin/vscode-html-language-server',
        'css': 'vscode-langservers-extracted/bin/vscode-css-language-server',
    }
    if language == 'python':
        bundled = bundle_root()
        if bundled:
            candidate = bundled / 'basedpyright/langserver.index.js'
        else:
            spec = importlib.util.find_spec('basedpyright')
            candidate = Path(spec.origin).parent / 'langserver.index.js' if spec and spec.origin else None
    else:
        path = paths.get(language)
        candidate = modules / path if path else None
    return (node, str(candidate), '--stdio') if candidate and candidate.is_file() else None

def configure_bundle() -> None:
    root = assets_root()
    if root and (root / 'browsers').is_dir():
        # Use exactly the Chromium revision included in this package.
        os.environ['PLAYWRIGHT_BROWSERS_PATH'] = str(root / 'browsers')

def probe() -> dict[str, object]:
    """Local CI smoke contract: report assets, never session or provider secrets."""
    configure_bundle()
    root = assets_root()
    import imageio_ffmpeg
    return {
        'ui_contract': 3,
        'frozen': bool(getattr(sys, 'frozen', False)),
        'node': bool(node_binary()),
        'javascript_debug': bool(debug_server()),
        'python_debug_adapter': bool(importlib.util.find_spec('debugpy')),
        'languages': {name: bool(language_command(name)) for name in ('python', 'typescript', 'html', 'css', 'json')},
        'chromium': bool(root and (root / 'browsers').is_dir() and any((root / 'browsers').rglob('chrome*'))),
        'ffmpeg': Path(imageio_ffmpeg.get_ffmpeg_exe()).is_file(),
        'web': bool(bundle_root() and (bundle_root() / 'web/index.html').is_file()),
    }
