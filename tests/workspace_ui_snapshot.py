"""Immutable UI fixture assets; a coordinated rebuild cannot alter an active proof."""
from pathlib import Path
import hashlib
import os
import shutil

CLASSIC_SHELL='<script>try{localStorage.setItem("termx-new-workspace-layout","off")}catch(e){}</script>'

def snapshot_ui(root:Path, source:Path, classic:bool=False)->Path:
    """Copy the built UI. The IDE layout is the default; a proof written for the deprecated classic dock passes
    `classic=True`, which pins it (the same stored choice as Settings > General) in the copy only; `TERMX_E2E_SHELL=ide`
    lifts the pin to try such a proof against the default layout."""
    source=source.resolve();entry=source/'index.html'
    if not entry.is_file():raise RuntimeError(f'Build the workspace before its rendered proof: {entry}')
    destination=root/'fixture-ui-build'
    shutil.copytree(source,destination)
    if classic and os.environ.get('TERMX_E2E_SHELL')!='ide':
        page=destination/'index.html'
        page.write_text(page.read_text().replace('<head>','<head>'+CLASSIC_SHELL,1))
    # Include all lazy chunks and fonts rather than pretending an entry hash
    # alone establishes which bytes were loaded during a long-running proof.
    manifest={str(path.relative_to(destination)):hashlib.sha256(path.read_bytes()).hexdigest()
              for path in sorted(destination.rglob('*')) if path.is_file()}
    import json
    (root/'fixture-ui-snapshot.json').write_text(json.dumps(manifest,indent=2)+'\n')
    return destination
