#!/usr/bin/env python3
"""Stage verified browser/debug/language assets before the CI-only native build."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / 'desktop' / 'build' / 'runtime'
DEBUG_VERSION = '1.140.0'
DEBUG_SHA256 = '27dab92937ec1ab35821ae955aac867544fe06a1b6307229049f2d789af10968'
DEBUG_URL = f'https://github.com/microsoft/vscode-js-debug/releases/download/v{DEBUG_VERSION}/js-debug-dap-v{DEBUG_VERSION}.tar.gz'

def unpack_debug(archive: Path, destination: Path) -> None:
    if hashlib.sha256(archive.read_bytes()).hexdigest() != DEBUG_SHA256:
        raise ValueError('Pinned Microsoft js-debug artifact checksum mismatch')
    with tarfile.open(archive, 'r:gz') as source:
        for entry in source.getmembers():
            target = (destination / entry.name).resolve()
            if not target.is_relative_to(destination.resolve()) or entry.issym() or entry.islnk():
                raise ValueError('Unsafe debugger archive entry')
        source.extractall(destination, filter='data')
    if not (destination / 'js-debug' / 'src' / 'dapDebugServer.js').is_file():
        raise ValueError('Debugger artifact entrypoint missing')

def main() -> None:
    ASSETS.mkdir(parents=True, exist_ok=True)
    browser_dir = ASSETS / 'browsers'
    env = {**os.environ, 'PLAYWRIGHT_BROWSERS_PATH': str(browser_dir)}
    subprocess.run([sys.executable, '-m', 'playwright', 'install', 'chromium'], env=env, check=True)
    # FFmpeg is supplied by the platform wheel and collected by PyInstaller.
    import imageio_ffmpeg
    ffmpeg = Path(imageio_ffmpeg.get_ffmpeg_exe())
    if not ffmpeg.is_file(): raise RuntimeError('FFmpeg wheel executable unavailable')
    with tempfile.TemporaryDirectory() as scratch:
        archive = Path(scratch) / 'debug.tar.gz'
        with urllib.request.urlopen(DEBUG_URL, timeout=60) as response:
            archive.write_bytes(response.read(64 * 1024 * 1024))
        unpack_debug(archive, ASSETS)
    npm = shutil.which('npm.cmd' if os.name == 'nt' else 'npm')
    if not npm: raise RuntimeError('Node/npm required for runtime language assets')
    source = ROOT / 'desktop' / 'runtime'
    target = ASSETS / 'language'
    target.mkdir(exist_ok=True)
    for name in ('package.json', 'package-lock.json'):
        shutil.copy2(source / name, target / name)
    subprocess.run([npm, 'ci', '--ignore-scripts', '--omit=dev', '--no-audit', '--no-fund'], cwd=target, check=True)
    manifest = {'js_debug': {'version': DEBUG_VERSION, 'sha256': DEBUG_SHA256}, 'browser': 'playwright-chromium/1.58.0', 'ffmpeg': ffmpeg.name}
    (ASSETS / 'manifest.json').write_text(json.dumps(manifest, indent=2))
    print('Pinned desktop runtime assets ready')

if __name__ == '__main__': main()
