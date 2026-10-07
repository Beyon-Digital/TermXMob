"""Host-derived proposals for typed project writes and process tools."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
from termx.auto_review import ActionEnvelope,canonical_hash
from termx.agent.policy import is_sensitive_path,redact

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
    raw=json.dumps(args)
    envelope=ActionEnvelope(call_id,binding['principal_id'],binding['session_id'],binding['project_id'],task_id,'agent.'+tool,
        canonical_hash({'args':args,'cwd':str(root),'baseline':baseline}),str(root),effect,'task:'+task_id,binding['policy_version'],0,0,data_labels=('secret',) if redact(raw)!=raw else ())
    def validate():
        if not service.session_valid(binding['principal_id'],binding['session_id'],binding['policy_version']):return False
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
