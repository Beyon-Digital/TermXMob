"""One host per private state profile, with challenge-bound local discovery."""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import uuid
from pathlib import Path

from termx.config import atomic_write
from termx.private_files import protect_private_path


def profile_id(root: Path) -> str:
    return hashlib.sha256(os.fsencode(root.expanduser().resolve())).hexdigest()


def private_owned(path: Path, *, directory=False):
    if path.is_symlink():
        raise PermissionError('Host service state must not be a symbolic link')
    info=path.stat()
    if os.name!='nt' and info.st_uid!=os.geteuid():
        raise PermissionError('Host service state belongs to another operating-system user')
    protect_private_path(path,directory=directory)


class HostInstance:
    def __init__(self, root: Path):
        self.root = root.expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        private_owned(self.root, directory=True)
        self.runtime_path = self.root / 'host-runtime.json'
        self.instance_id = uuid.uuid4().hex
        self.proof_token = secrets.token_hex(32)
        self._file = None

    def acquire(self):
        lock = self.root / 'host.lock'
        flags = os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0)
        fd = os.open(lock, flags, 0o600)
        handle = os.fdopen(fd, 'r+b')
        try:
            private_owned(lock)
            if os.name == 'nt':
                import msvcrt
                if os.fstat(fd).st_size == 0:
                    handle.write(b'0'); handle.flush()
                handle.seek(0)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, PermissionError) as exc:
            handle.close()
            raise RuntimeError('Another Termx host already owns this profile; attach to it instead of starting a duplicate') from exc
        self._file = handle
        return self

    def publish(self, *, host: str, port: int, managed: bool):
        if self.runtime_path.exists():private_owned(self.runtime_path)
        address = f'[{host}]' if ':' in host else host
        value = {'schema': 1, 'instance_id': self.instance_id, 'profile_id': profile_id(self.root),
                 'pid': os.getpid(), 'host': host, 'port': port, 'url': f'http://{address}:{port}',
                 'managed': managed, 'ready': True, 'proof_token': self.proof_token}
        atomic_write(self.runtime_path, json.dumps(value))
        return {key: value[key] for key in ('instance_id', 'profile_id', 'pid', 'host', 'port', 'url', 'managed', 'ready')}

    def close(self):
        try:
            current = json.loads(self.runtime_path.read_text())
            if current.get('instance_id') == self.instance_id:
                self.runtime_path.unlink()
        except (FileNotFoundError, ValueError, OSError):
            pass
        if self._file:
            self._file.close()
            self._file = None
