#!/usr/bin/env python3
"""Check the actual frozen sidecar before signing/publishing a desktop installer."""
from __future__ import annotations
import json
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]

def main() -> None:
    name = 'termx-backend.exe' if os.name == 'nt' else 'termx-backend'
    binary = ROOT / 'desktop/src-tauri/resources/backend' / name
    subprocess.run([str(binary), '--help'], capture_output=True, text=True, timeout=60, check=True)
    result = subprocess.run([str(binary), '--runtime-probe'], capture_output=True, text=True, timeout=60, check=True)
    snapshot = json.loads(result.stdout)
    required = ('frozen', 'node', 'javascript_debug', 'python_debug_adapter', 'chromium', 'ffmpeg', 'web')
    missing = [key for key in required if not snapshot.get(key)]
    missing += [f'language:{key}' for key, available in snapshot.get('languages', {}).items() if not available]
    if snapshot.get('ui_contract') != 3: missing.append('UI contract')
    if missing: raise SystemExit('Packaged runtime incomplete: ' + ', '.join(missing))
    print('Frozen sidecar UI/browser/Node/debug/language/FFmpeg smoke passed')

if __name__ == '__main__': main()
