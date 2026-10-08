"""Host-derived proposals for typed project writes and process tools."""
from __future__ import annotations
import hashlib
import json
import re
from pathlib import Path
from termx.auto_review import ActionEnvelope,canonical_hash
from termx.agent.policy import is_sensitive_path,redact


_CREDENTIAL_FIELDS = frozenset({
    'password', 'passwd', 'pwd', 'secret', 'token', 'cookie', 'cookies',
    'authorization', 'apikey', 'csrf', 'refreshtoken', 'accesstoken',
    'clientsecret', 'privatekey', 'assertion', 'evidence', 'credentials',
    'credential', 'connectionstring',
})
_CREDENTIAL_ASSIGNMENT = re.compile(
    r'''(?i)["']?(?:password|passwd|secret|token|cookie|authorization|api[_-]?key|refresh[_-]?token|access[_-]?token|client[_-]?secret|private[_-]?key)["']?\s*[:=]'''
)
_BEARER = re.compile(r'(?i)\bBearer\s+[A-Za-z0-9._~+/-]{8,}')


def contains_credentials(value):
    """Conservatively classify bounded structured content for exact human review.

    This is an eligibility filter, never a promise to identify arbitrary secrets.
    Oversized/deep/uninspectable metadata also remains ineligible for automatic
    review or remembered blanket consent. Values are never logged or exported.
    """
    remaining_bytes = 256 * 1024
    remaining_nodes = 4096

    def inspect(item, depth=0):
        nonlocal remaining_bytes, remaining_nodes
        remaining_nodes -= 1
        if depth > 16 or remaining_nodes < 0:
            return True
        if isinstance(item, dict):
            for key, child in item.items():
                name = str(key)
                if len(name) > remaining_bytes:
                    return True
                remaining_bytes -= len(name.encode('utf-8'))
                if remaining_bytes < 0 or re.sub(r'[-_\s]', '', name.lower()) in _CREDENTIAL_FIELDS:
                    return True
                if inspect(child, depth + 1):
                    return True
        elif isinstance(item, (list, tuple)):
            return any(inspect(child, depth + 1) for child in item)
        elif isinstance(item, str):
            if len(item) > remaining_bytes:
                return True
            remaining_bytes -= len(item.encode('utf-8'))
            if remaining_bytes < 0 or redact(item) != item or _CREDENTIAL_ASSIGNMENT.search(item) or _BEARER.search(item):
                return True
            stripped = item.strip()
            if stripped.startswith(('{', '[', '"')):
                try:
                    decoded = json.loads(stripped)
                except RecursionError:
                    return True
                except ValueError:
                    # Ordinary code and malformed JSON still have their explicit
                    # credential assignments checked above; parser exhaustion
                    # is already bounded by the payload byte limit.
                    return False
                return inspect(decoded, depth + 1)
        return False

    return inspect(value)

def proposal(service,task_id,binding,tool,args,cwd,*,call_id,decision=None,read_only=False):
    root=Path(cwd).resolve();effect='unknown';targets=[];hard_deny=None
    if tool=='write_file':targets=[str(args.get('path') or '')];effect='edit'
    elif tool=='read_file':targets=[str(args.get('path') or '')];effect='observe'
    elif tool=='apply_patch':
        from termx.agent.tools.filesystem import _split_unified_diff
        try:
            sections=_split_unified_diff(str(args.get('patch') or ''))
            targets=[p for before,after,_ in sections for p in (before,after) if p]
            effect='delete' if any(before and not after for before,after,_ in sections) else 'edit'
        except ValueError:hard_deny='Invalid structured patch'
    state=[]
    for name in targets:
        path=(root/name).resolve()
        if not name or not path.is_relative_to(root) or is_sensitive_path(str(path)):
            hard_deny='Sensitive or out-of-project file target';continue
        state.append((str(path),hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None))
    if read_only and effect!='observe':hard_deny='Ask mode cannot execute mutating tools'
    if decision and (decision.auto_resolved=='deny' or (not decision.allowed and not decision.approval_required)):
        hard_deny=decision.reason
    baseline=canonical_hash(state)
    privacy_revision = None
    if effect == 'unknown':
        from termx.desktop.recording import capture_privacy_revision
        try: privacy_revision = capture_privacy_revision()
        except PermissionError: hard_deny = 'Private window mode pauses process and computer control'
    envelope=ActionEnvelope(call_id,binding['principal_id'],binding['session_id'],binding['project_id'],task_id,'agent.'+tool,
        canonical_hash({'args':args,'cwd':str(root),'baseline':baseline}),str(root),effect,'task:'+task_id,binding['policy_version'],0,0,data_labels=('secret',) if contains_credentials(args) else ())
    def validate():
        check=service.session_valid if effect=='unknown' else getattr(service,'execution_session_valid',service.session_valid)
        if not check(binding['principal_id'],binding['session_id'],binding['policy_version']):return False
        if effect == 'unknown':
            from termx.desktop.recording import capture_privacy_revision
            try:
                if capture_privacy_revision() != privacy_revision:return False
            except PermissionError:return False
        current=[]
        try:
            for path,_ in state:
                p=Path(path)
                if not p.resolve().is_relative_to(root):return False
                current.append((path,hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None))
            return canonical_hash(current)==baseline
        except OSError:return False
    return envelope,validate,hard_deny
