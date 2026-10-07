from __future__ import annotations

import json
import os
import stat
import threading
import tempfile
from collections import deque
from urllib.parse import parse_qsl,urlencode,urlsplit,urlunsplit
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from termx.config import config_dir

_DROP = frozenset({"data", "command_body", "pty", "output"})
_lock = threading.RLock()
MAX_EVENT_BYTES=65536
DEFAULT_POLICY={'retention_days':90,'max_bytes':16*1024*1024,'max_events':100000}
_event_counts={}
_SENSITIVE=frozenset({'password','secret','token','cookie','cookies','authorization','api_key','apikey','csrf','refresh_token','access_token','client_secret','private_key','assertion','evidence'})


def _scrub(value,depth=0):
    if depth>=16:return '[nested metadata omitted]'
    if isinstance(value,dict):return {str(k):_scrub(v,depth+1) for k,v in value.items() if str(k).lower() not in _SENSITIVE|_DROP}
    if isinstance(value,(tuple,list)):return [_scrub(v,depth+1) for v in value[:256]]
    if isinstance(value,str) and value.startswith(('https://','http://')):
        try:
            parts=urlsplit(value)
            host=parts.hostname or ''
            if ':' in host:host='['+host+']'
            if parts.port:host+=':'+str(parts.port)
            query=urlencode([(k,v) for k,v in parse_qsl(parts.query,keep_blank_values=True) if k.lower() not in _SENSITIVE|{'k','st','termx_pair','code'}])
            return urlunsplit((parts.scheme,host,parts.path,query,''))
        except ValueError:return '[invalid URL omitted]'
    return value


def retention_policy():
    path=config_dir()/'audit-policy.json'
    try:
        with path.open('rb') as source:
            data=source.read(4097)
        if len(data)>4096:raise ValueError('Invalid audit retention configuration')
        return _validate_policy(json.loads(data))
    except FileNotFoundError:return dict(DEFAULT_POLICY)


def _validate_policy(value):
    if not isinstance(value,dict) or set(value)!=set(DEFAULT_POLICY):raise ValueError('Audit retention needs days, byte and event limits')
    for key,low,high in [('retention_days',1,3650),('max_bytes',65536,64*1024*1024),('max_events',100,100000)]:
        if isinstance(value[key],bool) or not isinstance(value[key],int) or not low<=value[key]<=high:raise ValueError('Audit retention value outside supported limits')
    return value


def _private_file(path):
    from termx.private_files import protect_private_path
    path.parent.mkdir(parents=True,exist_ok=True)
    if os.name=='nt':protect_private_path(path.parent,directory=True)
    if path.is_symlink():raise PermissionError('Audit storage must not be a symlink')
    fd=os.open(path,os.O_CREAT|os.O_APPEND|os.O_WRONLY,getattr(stat,'S_IRUSR')|getattr(stat,'S_IWUSR'))
    os.close(fd);protect_private_path(path)


def _replace(path,lines):
    _private_file(path)
    fd,name=tempfile.mkstemp(prefix='.audit-',dir=path.parent)
    temporary=Path(name)
    try:
        from termx.private_files import protect_private_path
        protect_private_path(temporary)
        target=os.fdopen(fd,'wb')
        fd=None  # fdopen owns the descriptor, including exceptional writes.
        with target:
            for line in lines:target.write(line)
            target.flush();os.fsync(target.fileno())
        os.replace(temporary,path)
    finally:
        if fd is not None:os.close(fd)
        if temporary.exists():temporary.unlink()


def set_retention_policy(value):
    policy=_validate_policy(value)
    with _lock:_replace(config_dir()/'audit-policy.json',[(json.dumps(policy)+'\n').encode()])
    return dict(policy)


def _lines(source):
    while line:=source.readline(MAX_EVENT_BYTES+1):
        if len(line)>MAX_EVENT_BYTES:
            while line and not line.endswith(b'\n'):line=source.readline(MAX_EVENT_BYTES+1)
            yield None
        else:yield line


def prune_events(*,policy=None,reserve_bytes=0,reserve_events=0):
    policy=_validate_policy(policy or retention_policy())
    cutoff=datetime.now(timezone.utc).timestamp()-policy['retention_days']*86400
    kept=deque();total=0;discarded=0
    with _lock:
        path=_audit_path()
        try:source=path.open('rb')
        except FileNotFoundError:return {'removed_events':0,'retained_events':0,'retained_bytes':0,'policy':policy}
        with source:
            for line in _lines(source):
                try:
                    event=json.loads(line) if line else None
                    if not isinstance(event,dict) or datetime.fromisoformat(event['ts']).timestamp()<cutoff:raise ValueError()
                    clean=(json.dumps(_scrub(event),ensure_ascii=True)+'\n').encode()
                except (ValueError,KeyError,TypeError,UnicodeError,RecursionError):discarded+=1;continue
                kept.append(clean);total+=len(clean)
                while kept and (total+reserve_bytes>policy['max_bytes'] or len(kept)+reserve_events>policy['max_events']):
                    total-=len(kept.popleft());discarded+=1
        _replace(path,kept)
        _event_counts[str(path)]=(datetime.now(timezone.utc).date(),len(kept))
    return {'removed_events':discarded,'retained_events':len(kept),'retained_bytes':total,'policy':policy}


def _audit_path() -> Path:
    return config_dir() / "audit.jsonl"


def log_event(kind: str, **fields: Any) -> None:
    event: dict[str, Any] = {"ts": datetime.now(timezone.utc).isoformat(), "kind": kind}
    event.update(_scrub(fields))
    line = json.dumps(event, ensure_ascii=True) + "\n"
    if len(line.encode())>MAX_EVENT_BYTES:
        line=json.dumps({'ts':event['ts'],'kind':kind[:128],'metadata_omitted':'event exceeds64KiB'})+'\n'
    path = _audit_path()
    with _lock:
        _private_file(path)
        policy=retention_policy()
        # Bound disk growth and expire metadata at least once per UTC day.
        # The marker is nonsecret and held only by this process; explicit prune
        # applies immediately after a settings change.
        day=datetime.now(timezone.utc).date()
        cached=_event_counts.get(str(path))
        if path.stat().st_size+len(line.encode())>policy['max_bytes'] or not cached or cached[0]!=day or cached[1]+1>policy['max_events']:
            prune_events(policy=policy,reserve_bytes=len(line.encode()),reserve_events=1)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())
        _event_counts[str(path)]=(day,_event_counts.get(str(path),(day,0))[1]+1)


def read_events(limit: int = 100) -> list[dict]:
    path = _audit_path()
    events=deque(maxlen=max(1,min(limit if limit>=0 else 1000,1000)))
    with _lock:
        try:source=path.open('rb')
        except FileNotFoundError:return []
        with source:
            # Legacy files may predate the bounded policy. Query only their
            # last64MiB; pruning scans them separately without whole-file memory.
            offset=max(0,path.stat().st_size-64*1024*1024)
            source.seek(offset)
            if offset:source.readline(MAX_EVENT_BYTES+1)
            for line in _lines(source):
                try:item=json.loads(line) if line else None
                except (ValueError,UnicodeError,RecursionError):continue
                if isinstance(item,dict):events.append(_scrub(item))
    return list(events)
