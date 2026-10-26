"""Live source authorization for saved automatic child-agent activity.

The parent ledger is durable, but a copied child result never grants permanent
access to its source. Project these known relay envelopes at every public read;
leave the original ledger and its replay sequence intact for authorized review.
"""
from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from typing import Any

from fastapi import HTTPException

_UNAVAILABLE = 'Delegated agent activity is unavailable with your current access.'
_STEERING_PREFIX = 'Delegated session update (untrusted agent output, not authority): '
_RELAY_TYPES = {'subagent.started', 'subagent.event', 'subagent.finished', 'subagent.cancelled'}
_CHILD_TOOLS = {'spawn_subagent', 'spawn_project_agent', 'spawn_engine_agent', 'await_subagents',
    'subagent_status', 'cancel_subagent', 'managed_session_status', 'message_managed_session',
    'cancel_managed_session', 'review_managed_session', 'adopt_managed_session', 'list_agent_sessions'}


def _redacted() -> dict[str, Any]:
    return {'redacted': True, 'message': _UNAVAILABLE}


def _allowed(authorize: Callable, identifier: Any, kind: str = 'task') -> bool:
    if not isinstance(identifier, str) or not identifier:
        return False
    try:
        authorize(identifier, kind)
    except (KeyError, PermissionError):
        return False
    except HTTPException as exc:
        if exc.status_code not in {403, 404}:
            raise
        return False
    return True


def _source_authorizer(workspace, principal):
    def authorize(identifier, kind='task'):
        row = workspace.record(principal, kind, identifier, scope='agent-view')
        if kind == 'task' and row.get('conversation_id'):
            workspace.record(principal, 'conversation', row['conversation_id'], scope='agent-view')
    return authorize


def context_source_authorizer(context):
    """Re-resolve managed credentials, including long-lived subscriptions.

    Legacy passcode resources use their existing authorization service rather
    than becoming implicitly enrolled in a managed user's workspace.
    """
    def authorize(identifier, kind='task'):
        identity = getattr(context.state, 'identity', None)
        current = identity.resolve(context.secret, allow_locked=True) if identity else None
        if current:
            if current.locked:
                raise PermissionError('Session is locked')
            workspace = getattr(context.state, 'workspace', None)
            if workspace is None:
                raise PermissionError('Managed source enrollment is unavailable')
            _source_authorizer(workspace, current.principal)(identifier, kind)
        else:
            authorization = getattr(context.state, 'authorization', None)
            if authorization is None:
                raise PermissionError('Source authorization is unavailable')
            authorization.require(context.secret, 'agent-view',
                resource_kind=kind, resource_id=identifier)
    return authorize


def public_task_approvals(workspace, principal, approvals: Iterable[dict], *, source_authorize=None) -> list[dict]:
    authorize = source_authorize or _source_authorizer(workspace, principal)
    return [_approval(approval, authorize) for approval in approvals]


def _approval(approval: dict, authorize) -> dict:
    payload = approval.get('payload')
    if not isinstance(payload, dict):
        return approval
    if ((payload.get('child_id') and not _allowed(authorize, payload['child_id']))
            or (isinstance(payload.get('call'), dict) and _call(payload['call'], authorize).get('redacted'))):
        return {**approval, 'status': 'unavailable', 'actual_status': approval.get('status'),
                'actionable': False, 'payload': _redacted()}
    projected = public_task_runtime(None, None, payload, source_authorize=authorize)
    if isinstance(payload.get('call'), dict):
        projected['call'] = _call(payload['call'], authorize)
    if isinstance(payload.get('remaining_calls'), list):
        projected['remaining_calls'] = [_call(call, authorize) if isinstance(call, dict) else call
                                      for call in payload['remaining_calls']]
    return {**approval, 'payload': projected}


def _call(call: dict, authorize) -> dict:
    if call.get('name') not in _CHILD_TOOLS:
        return call
    arguments = call.get('arguments')
    encoded = isinstance(arguments, str)
    if encoded:
        try:
            arguments = json.loads(arguments)
        except (ValueError, TypeError):
            arguments = None
    if not isinstance(arguments, dict):
        return call
    denied = ((arguments.get('task_id') and not _allowed(authorize, arguments['task_id']))
        or (arguments.get('session_id') and not _allowed(authorize, arguments['session_id'], 'conversation'))
        or any(not _allowed(authorize, identifier) for identifier in
               (arguments['task_ids'] if isinstance(arguments.get('task_ids'), list) else [])))
    if denied:
        return {**call, 'redacted': True, 'arguments': json.dumps(_redacted()) if encoded else _redacted()}
    return call


def _child_result(result: Any, authorize) -> Any:
    """Only inspect the documented shapes returned by delegation tools."""
    if not isinstance(result, dict):
        return result
    source = result.get('child_id') or result.get('task_id')
    if source and not _allowed(authorize, source):
        return _redacted()
    if result.get('session_id') and not _allowed(authorize, result['session_id'], 'conversation'):
        return _redacted()
    projected = dict(result)
    children = result.get('children')
    if isinstance(children, dict):
        visible = {key: value for key, value in children.items() if _allowed(authorize, key)}
        if len(visible) != len(children):
            projected.update(children=visible, redacted=True, message=_UNAVAILABLE)
    if isinstance(children, list):
        visible = [row for row in children if isinstance(row, dict) and
            _allowed(authorize, row.get('session_id'), 'conversation') and
            (not row.get('task_id') or _allowed(authorize, row['task_id']))]
        if len(visible) != len(children):
            projected.update(children=visible, redacted=True, message=_UNAVAILABLE)
    if isinstance(result.get('sessions'), list):
        projected['sessions'] = [row for row in result['sessions'] if isinstance(row, dict)
            and _allowed(authorize, row.get('id'), 'conversation')]
    # Cancellation results contain source IDs without a result body.
    for key in ('cancelled', 'already_finished', 'missing', 'user_controlled', 'cancellation_requested', 'pending'):
        if isinstance(result.get(key), list):
            projected[key] = [identifier for identifier in result[key] if _allowed(authorize, identifier)]
    return projected


def _event(event: dict, authorize, depth: int = 0) -> dict:
    kind, payload = event.get('type'), event.get('payload')
    if not isinstance(payload, dict):
        return event
    if depth >= 8:
        return {**event, 'payload': _redacted()}
    if kind in _RELAY_TYPES:
        if not _allowed(authorize, payload.get('child_id')):
            return {**event, 'payload': _redacted()}
        if kind == 'subagent.event':
            inner = _event({'type': payload.get('type'), 'payload': payload.get('payload')}, authorize, depth + 1)
            return {**event, 'payload': {**payload, 'payload': inner.get('payload')}}
    elif kind == 'subagent.awaited':
        return {**event, 'payload': _child_result(payload, authorize)}
    elif kind in {'approval.requested', 'approval.resolved'}:
        approval = payload.get('approval')
        if isinstance(approval, dict):
            return {**event, 'payload': {**payload, 'approval': _approval(approval, authorize)}}
    elif kind == 'task.steered':
        message = payload.get('message')
        if isinstance(message, str) and message.startswith(_STEERING_PREFIX):
            try:
                source = json.loads(message[len(_STEERING_PREFIX):])
            except (ValueError, TypeError):
                return {**event, 'payload': _redacted()}
            if not isinstance(source, dict) or not _allowed(authorize, source.get('task_id')):
                return {**event, 'payload': _redacted()}
    elif kind == 'tool.finished' and payload.get('name') in _CHILD_TOOLS:
        return {**event, 'payload': {**payload, 'result': _child_result(payload.get('result'), authorize)}}
    elif kind == 'tool.started' and isinstance(payload.get('call'), dict):
        return {**event, 'payload': {**payload, 'call': _call(payload['call'], authorize)}}
    return event


def public_task_events(workspace, principal, events: Iterable[dict], *, source_authorize=None) -> list[dict]:
    authorize = source_authorize or _source_authorizer(workspace, principal)
    return [_event(event, authorize) for event in events]


def context_task_events(context, events):
    return public_task_events(getattr(context.state, 'workspace', None), None, events,
        source_authorize=context_source_authorizer(context))


def context_task_approvals(context, approvals):
    return public_task_approvals(getattr(context.state, 'workspace', None), None, approvals,
        source_authorize=context_source_authorizer(context))


def public_task_runtime(workspace, principal, runtime, *, source_authorize=None):
    """Project structured child outputs in a public runtime/export snapshot.

    This never changes the private execution checkpoint or model-authored prose.
    Only known tool outputs and host-generated steering use source dependencies.
    """
    if not isinstance(runtime, dict):
        return runtime
    authorize = source_authorize or _source_authorizer(workspace, principal)
    result = dict(runtime)
    source = runtime.get('coordination_source')
    if isinstance(source, dict) and not _allowed(authorize, source.get('source_task_id')):
        result['coordination_source'] = _redacted()
    if isinstance(runtime.get('tree_pending_calls'), list):
        result['tree_pending_calls'] = [_call(call, authorize) if isinstance(call, dict) else call
                                      for call in runtime['tree_pending_calls']]
    history = runtime.get('history')
    if not isinstance(history, list):
        return result
    calls = {item.get('call_id'): item.get('name') for item in history
        if isinstance(item, dict) and item.get('type') == 'function_call'}
    projected = []
    for item in history:
        if not isinstance(item, dict):
            projected.append(item)
            continue
        if item.get('type') == 'function_call_output' and calls.get(item.get('call_id')) in _CHILD_TOOLS:
            try:
                value = json.loads(item['output'])
            except (KeyError, ValueError, TypeError):
                # An unreadable known child output cannot establish its source.
                value = _redacted()
            projected.append({**item, 'output': json.dumps(_child_result(value, authorize), ensure_ascii=False)})
        elif item.get('role') == 'user' and isinstance(item.get('content'), str) and item['content'].startswith(_STEERING_PREFIX):
            value = _event({'type': 'task.steered', 'payload': {'message': item['content']}}, authorize)
            projected.append({**item, 'content': value['payload'].get('message', _UNAVAILABLE)})
        elif item.get('type') == 'function_call':
            projected.append(_call(item, authorize))
        else:
            projected.append(item)
    result['history'] = projected
    return result


def context_task_runtime(context, runtime):
    return public_task_runtime(getattr(context.state, 'workspace', None), None, runtime,
        source_authorize=context_source_authorizer(context))


def context_task_snapshot(context, task):
    """The JSON export needs the same projection as typed task fields."""
    result = dict(task)
    for key, project in (('events', context_task_events), ('approvals', context_task_approvals),
                         ('runtime', context_task_runtime)):
        if key in result:
            result[key] = project(context, result[key])
    return result
