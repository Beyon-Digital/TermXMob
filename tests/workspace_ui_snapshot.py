"""Immutable UI fixture assets; a coordinated rebuild cannot alter an active proof."""
from pathlib import Path
import hashlib
import shutil

def snapshot_ui(root:Path, source:Path)->Path:
    source=source.resolve();entry=source/'index.html'
    if not entry.is_file():raise RuntimeError(f'Build the workspace before its rendered proof: {entry}')
    destination=root/'fixture-ui-build'
    shutil.copytree(source,destination)
    # Include all lazy chunks and fonts rather than pretending an entry hash
    # alone establishes which bytes were loaded during a long-running proof.
    manifest={str(path.relative_to(destination)):hashlib.sha256(path.read_bytes()).hexdigest()
              for path in sorted(destination.rglob('*')) if path.is_file()}
    import json
    (root/'fixture-ui-snapshot.json').write_text(json.dumps(manifest,indent=2)+'\n')
    return destination
