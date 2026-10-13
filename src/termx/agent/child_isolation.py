"""Bounded non-Git delegation snapshots, with no links to parent files."""
from __future__ import annotations
import os
import shutil
import stat
from pathlib import Path

SKIP={'.git','.venv','node_modules','__pycache__','.pytest_cache','.mypy_cache','.ruff_cache'}

def snapshot(source:Path,destination:Path,*,excluded=(),max_bytes=100*1024*1024,max_files=6000):
    source=source.resolve();destination=destination.resolve()
    excluded={Path(p).resolve() for p in (*excluded,destination)}
    # A failed snapshot must never remove a pre-existing user directory.
    destination.mkdir(parents=True,mode=0o700,exist_ok=False)
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
                # Do not follow a symlink swapped in after the initial check.
                flags=os.O_RDONLY|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_NONBLOCK',0)
                with os.fdopen(os.open(item,flags),'rb') as incoming:
                    info=os.fstat(incoming.fileno())
                    if not stat.S_ISREG(info.st_mode):
                        raise ValueError('Non-Git delegation snapshot contains non-regular files')
                    count+=1
                    if count>max_files:
                        raise ValueError('Non-Git delegation snapshot exceeds 100 MiB or 6000 files; use a Git worktree or read-only child')
                    with (target/name).open('xb') as outgoing:
                        while chunk:=incoming.read(1024*1024):
                            total+=len(chunk)
                            if total>max_bytes or count>max_files:
                                raise ValueError('Non-Git delegation snapshot exceeds 100 MiB or 6000 files; use a Git worktree or read-only child')
                            outgoing.write(chunk)
                    os.chmod(target/name,stat.S_IMODE(info.st_mode))
                    os.utime(target/name,ns=(info.st_atime_ns,info.st_mtime_ns))
        return {'kind':'snapshot','path':str(destination),'base_path':str(source),'files':count,'bytes':total,'automatic_apply':False}
    except BaseException:
        shutil.rmtree(destination,ignore_errors=True)
        raise
