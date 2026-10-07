"""Bounded, incremental frames for restricted console data/control."""
from __future__ import annotations
import json
import base64
MAX_FRAME = 2 * 1024 * 1024

def encode(value):
    raw = json.dumps(value, separators=(',', ':'), allow_nan=False).encode('utf-8')
    if len(raw) > MAX_FRAME: raise ValueError('Restricted console frame exceeds limit')
    return raw + b'\n'

def _invalid_constant(value):
    raise ValueError('Restricted console frame contains an invalid number')

class Decoder:
    def __init__(self): self.buffer = bytearray()
    def feed(self, data):
        self.buffer.extend(data); values = []
        while (boundary := self.buffer.find(b'\n')) >= 0:
            if boundary > MAX_FRAME: raise ValueError('Restricted console frame exceeds limit')
            raw = bytes(self.buffer[:boundary]); del self.buffer[:boundary+1]
            value = json.loads(raw, parse_constant=_invalid_constant)
            if not isinstance(value, dict): raise ValueError('Restricted console frame must be an object')
            values.append(value)
        if len(self.buffer) > MAX_FRAME: raise ValueError('Restricted console frame exceeds limit')
        return values

def validate_start(value):
    if value.get('type') != 'start': raise ValueError('Console start required')
    for name in ('command', 'cwd'):
        item=value.get(name)
        if not isinstance(item,str) or not item or '\0' in item or len(item)>32767:
            raise ValueError('Invalid console launch string')
    env=value.get('env')
    if not isinstance(env,dict) or len(env)>1024: raise ValueError('Invalid console environment')
    for name,item in env.items():
        if not isinstance(name,str) or not name or '=' in name or '\0' in name or not isinstance(item,str) or '\0' in item:
            raise ValueError('Invalid console environment')
    job=value.get('job')
    allowed={'active_process_limit','process_memory_bytes','job_memory_bytes','job_time_100ns'}
    if not isinstance(job,dict) or set(job)-allowed: raise ValueError('Invalid console job limits')
    if any(type(item) is not int or not 0<=item<2**63 for item in job.values()):
        raise ValueError('Invalid console job limit')
    for name in ('rows','cols'):
        item=value.get(name)
        if type(item) is not int or not 1<=item<=32767: raise ValueError('Invalid console dimensions')
    return value

def validate_response(value, ready):
    kind=value.get('type')
    if kind=='ready':
        pid=value.get('pid')
        if ready or type(pid) is not int or not 1<=pid<2**32: raise ValueError('Invalid console ready response')
    elif kind=='data':
        raw=value.get('data')
        if not ready or not isinstance(raw,str) or len(raw)>10924: raise ValueError('Invalid console output')
        data=base64.b64decode(raw,validate=True)
        if len(data)>8192: raise ValueError('Console output exceeds chunk limit')
        return data
    elif kind=='exit':
        code=value.get('code');diagnostics=value.get('diagnostics')
        if not ready or type(code) is not int or not 0<=code<2**32: raise ValueError('Invalid console exit response')
        if not isinstance(diagnostics,dict) or len(encode(diagnostics))>4096: raise ValueError('Invalid console exit diagnostics')
    elif kind=='phase':
        if ready or value.get('stage') not in {'restricted-token-verified','creating-console','console-created','creating-client','client-resumed'}:
            raise ValueError('Invalid console startup phase')
    elif kind=='error':
        for name in ('error','category'):
            item=value.get(name)
            if not isinstance(item,str) or len(item)>256: raise ValueError('Invalid console failure response')
        number=value.get('winerror')
        if number is not None and (type(number) is not int or not -2**31<=number<2**32):
            raise ValueError('Invalid console failure status')
    else:raise ValueError('Unknown console response')
