#!/usr/bin/env python3
"""Check the actual frozen sidecar before signing/publishing a desktop installer."""
from __future__ import annotations
import json
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]

def execute(binary: Path, flag: str, timeout: int = 60):
    result = subprocess.run([str(binary), flag], capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise SystemExit(f'Frozen runtime {flag} failed ({result.returncode}):\n{result.stderr[-8000:]}')
    return result

def main() -> None:
    name = 'termx-backend.exe' if os.name == 'nt' else 'termx-backend'
    binary = ROOT / 'desktop/src-tauri/resources/backend' / name
    execute(binary, '--help')
    result = execute(binary, '--runtime-probe')
    snapshot = json.loads(result.stdout)
    required = ('frozen', 'node', 'javascript_debug', 'python_debug_adapter', 'chromium', 'ffmpeg', 'web')
    missing = [key for key in required if not snapshot.get(key)]
    missing += [f'language:{key}' for key, available in snapshot.get('languages', {}).items() if not available]
    if snapshot.get('ui_contract') != 3: missing.append('UI contract')
    if missing: raise SystemExit('Packaged runtime incomplete: ' + ', '.join(missing))
    executed = execute(binary, '--runtime-smoke', timeout=240)
    qualification = json.loads(executed.stdout)
    if not qualification.get('frozen') or qualification.get('ui_contract') != 3:
        raise SystemExit('Runtime qualification did not execute inside the frozen sidecar')
    report = ROOT / 'desktop/build/runtime-smoke.json'
    report.write_text(json.dumps({'assets':snapshot,'qualification':qualification}, indent=2))
    print('Frozen sidecar executed Node, five language servers, both debug adapters, Chromium rendering and FFmpeg successfully')

if __name__ == '__main__': main()
