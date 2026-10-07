"""Bounded non-Git delegation snapshots, with no links to parent files."""
from __future__ import annotations
import os
import shutil
from pathlib import Path

SKIP={'.git','.venv','node_modules','__pycache__','.pytest_cache','.mypy_cache','.ruff_cache'}

def snapshot(source:Path,destination:Path,*,excluded=(),max_bytes=100*1024*1024,max_files=6000):
    source=source.resolve();destination=destination.resolve()
    excluded={Path(p).resolve() for p in (*excluded,destination)}
    destination.mkdir(parents=True,mode=0o700)
    total=0;count=0
    try:
        for directory,folders,files in os.walk(source,followlinks=False):
            parent=Path(directory)
            folders[:]=[f for f in folders if f not in SKIP and not (parent/f).is_symlink() and not any((parent/f).resolve()==p or p in (parent/f).resolve().parents for p in excluded)]
            target=destination/parent.relative_to(source);target.mkdir(exist_ok=True,mode=0o700)
            for name in files:
                item=parent/name
                if item.resolve() in excluded or name in {'.DS_Store'} or any(item.parent==p.parent and item.name.startswith(p.name+'-') for p in excluded):continue
                if item.is_symlink():
                    raise ValueError('Non-Git delegation snapshot contains links; use a Git worktree or read-only child')
                if not item.is_file():continue
                total+=item.stat().st_size;count+=1
                if total>max_bytes or count>max_files:
                    raise ValueError('Non-Git delegation snapshot exceeds 100 MiB or 6000 files; use a Git worktree or read-only child')
                shutil.copy2(item,target/name)
        return {'kind':'snapshot','path':str(destination),'base_path':str(source),'files':count,'bytes':total,'automatic_apply':False}
    except BaseException:
        shutil.rmtree(destination,ignore_errors=True)
        raise
