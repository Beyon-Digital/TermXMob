"""Explicit desktop consent for one bounded unattended execution delegation.

Modelled on the scheduled browser permission: default deny, an expiry inside the delegation's own, a time
budget per run, and exact application names. The permission lives on the delegation, so revoking the grant,
losing the ``desktop-control`` scope or changing the owner's policy withdraws it at the next computer action.
"""
from __future__ import annotations

from math import isfinite
from time import time

from termx.desktop.apps import normalize_apps

MAX_DAYS = 90
KEYS = {'apps', 'expires_at', 'max_seconds'}


def normalize(selection, *, now: float, grant_until: float, first_due: float | None = None) -> dict:
    """The stored form of a computer permission, or ValueError. ``grant_until`` is when the delegation expires."""
    if not isinstance(selection, dict) or set(selection) != KEYS:
        raise ValueError('Choose the applications, an expiry and a per-run computer time')
    expires, seconds = selection['expires_at'], selection['max_seconds']
    if isinstance(expires, bool) or not isinstance(expires, (int, float)) or not isfinite(expires) or not now + 10 <= expires <= now + MAX_DAYS * 86400:
        raise ValueError('Computer permission must expire within 90 days')
    if expires > grant_until:
        raise ValueError('Computer permission must stay within the execution delegation expiry')
    if first_due is not None and first_due >= expires:
        raise ValueError('Computer permission must cover the first run')
    if isinstance(seconds, bool) or not isinstance(seconds, int) or not 10 <= seconds <= 3600:
        raise ValueError('Computer time must be 10 to 3600 seconds per run')
    return {'apps': normalize_apps(selection['apps']), 'expires_at': float(expires), 'max_seconds': seconds}


def for_task(workspace, task_id: str) -> dict | None:
    """The permission a running task holds through its delegation, or None. Checked on every computer action.

    The delegation is found on the task or the nearest task above it (a sub-agent runs under its parent's grant).
    """
    seen: set[str] = set()
    current: str | None = task_id
    root = None
    while current:
        if current in seen or len(seen) >= 64:
            return None
        seen.add(current)
        row = workspace.store.get('task', current)
        if row and row.get('delegation_id'):
            root = row
            break
        current = (workspace.agents.get_task(current) or {}).get('parent_id')
    if root is None:
        return None
    grant = workspace.store.get('delegation', root['delegation_id'])
    permission = (grant or {}).get('computer_permission')
    now = time()
    if not permission or grant['revoked'] or grant['expires_at'] <= now or permission['expires_at'] <= now:
        return None
    principal = workspace.state.identity.principal_by_id(root['owner'])
    if not principal or principal.policy_version != grant['policy_version']:
        return None
    try:
        workspace.require(principal, 'desktop-control', grant.get('project_id'))
    except (PermissionError, KeyError):
        return None
    return {**permission, 'run_id': root['id']}
