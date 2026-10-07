"""Native approval and host callback interception using trusted task identity."""
from __future__ import annotations

import asyncio
import secrets
from time import time

from termx.agent.action_review import proposal
from termx.agent.providers import ProviderCall
from termx.auto_review import ReviewRequired


async def wait_review(engine, binding, record, identity, *, tool, args):
    if not engine._approval_sink: raise PermissionError('Human review is unavailable')
    token = 'native_review_' + secrets.token_urlsafe(12)
    future = asyncio.get_running_loop().create_future()
    engine._pending_decisions[token] = (future, 'browser.review', {'binding_id': binding.binding_id, 'review_id': record['id'], 'principal_id': identity['principal_id']})
    try:
        await engine._approval_sink(binding.binding_id, token, 'browser.review', {'browser_review': record, 'proposal': ProviderCall('function', record['id'], tool, args).public()}, 'tool')
        return bool(await asyncio.wait_for(future, max(.1, record['expires_at'] - time())))
    except asyncio.TimeoutError:
        engine._emit(binding, 'engine.approval.expired', {'request_id': token, 'review_id': record['id']})
        return False
    finally: engine._pending_decisions.pop(token, None)


def respond_review(engine, binding, entry, approved):
    future, _, params = entry
    if params.get('binding_id') != binding.binding_id: raise PermissionError('Review belongs to another native session')
    if future.done(): return
    record = engine.browser_service.records.get('review', params['review_id'])
    if not record or record['expires_at'] <= time() or record['status'] != 'needs_user':
        future.set_result(False)
        if approved: raise PermissionError('Review expired or changed; request a fresh action')
        return
    engine.browser_service.review.decide(record['id'], principal_id=params['principal_id'], approve=approved)
    future.set_result(approved)


async def authorize(engine, binding, tool, args, identifier):
    service = getattr(engine, 'browser_service', None)
    task_id = binding.extensions_snapshot.get('managed_task_id')
    identity = service.records.get('agent-task-authority', task_id) if service and task_id else None
    if not identity: return None
    envelope, validate, denied = proposal(service, task_id, identity, tool, args, binding.cwd,
        call_id=task_id + ':native:' + identifier, read_only=binding.extensions_snapshot.get('review_read_only', False))
    try: permit = await service.review.authorize(envelope, validate=validate, hard_deny=denied, context={'effect_summary': tool + ' in the selected project; content is untrusted','task_summary':getattr(service,'task_summary',lambda _:'')(task_id)})
    except ReviewRequired as exc:
        if exc.record['status'] != 'needs_user': raise PermissionError('Native action was blocked, consumed or invalidated')
        if not await wait_review(engine, binding, exc.record, identity, tool=tool, args=args): raise PermissionError('Human declined the native action')
        permit = await service.review.authorize(envelope, validate=validate, hard_deny=denied)
    return envelope, validate, permit


async def execute(engine, binding, tool, args, identifier, callback):
    authorization = await authorize(engine, binding, tool, args, identifier)
    if not authorization: return await callback()
    envelope, validate, permit = authorization
    try:return await engine.browser_service.review.execute(envelope, permit['permit'], validate=validate, operation=callback)
    except ReviewRequired as exc:
        permit=await reapprove_changed_reviewer(engine,binding,envelope,validate,exc.record,tool=tool,args=args)
        return await engine.browser_service.review.execute(envelope,permit['permit'],validate=validate,operation=callback)

async def reapprove_changed_reviewer(engine,binding,envelope,validate,record,*,tool,args,hard_deny=None):
    """Park an unchanged, live native action before any effect or consumption."""
    service=engine.browser_service
    current=service.records.get('review',envelope.action_id)
    if not current or record['id']!=envelope.action_id or current['fingerprint']!=envelope.fingerprint() or current['status']!='needs_user' or record['status']!='needs_user' or hard_deny or not validate():raise PermissionError('Native action authority or target changed')
    identity=service.records.get('agent-task-authority',envelope.run_id)
    if not identity or identity['principal_id']!=envelope.principal_id or identity['session_id']!=envelope.session_id:raise PermissionError('Native task authority changed')
    custom=getattr(engine,'_browser_review',None)
    if custom:
        allowed=await custom(binding,record,identity,proposal=ProviderCall('function',record['id'],tool,args).public())
    else:allowed=await wait_review(engine,binding,record,identity,tool=tool,args=args)
    if not allowed:raise PermissionError('Human declined the changed reviewer action')
    return await service.review.authorize(envelope,validate=validate,hard_deny=hard_deny)

async def consume_reviewed(engine,binding,envelope,validate,permit,*,tool,args,hard_deny=None):
    try:await engine.browser_service.review.consume_external(envelope,permit['permit'],validate=validate)
    except ReviewRequired as exc:
        permit=await reapprove_changed_reviewer(engine,binding,envelope,validate,exc.record,tool=tool,args=args,hard_deny=hard_deny)
        await engine.browser_service.review.consume_external(envelope,permit['permit'],validate=validate)
