"""Native/ACP coordination through the existing workspace and engine gateway.

The host owns admission, receipts, current authority and elapsed-time supervision.
Adapters retain session identity, provider credentials, native tools and approvals.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from time import monotonic, time
from uuid import uuid4

from fastapi import HTTPException

from termx.agent.limits import resolve_limits
from termx.agent.store import ACTIVE_STATUSES
from termx.workspace.store import Conflict


class EngineCoordination:
    def __init__(self, orchestrator):
        self.orchestrator=orchestrator
        self.workspace=orchestrator.workspace
        self.store=orchestrator.store
        self.agents=orchestrator.agents
        self.watchers={}
        self.cancel_workers=set()

    def engines(self):
        gateway=self.workspace.state.engines
        result=[]
        for identifier in gateway.engines():
            adapter=gateway.adapter(identifier)
            descriptor=adapter.descriptor()
            caps=adapter.capabilities()
            result.append({'id':identifier,'label':descriptor.label,'installed':descriptor.installed,
                'auth_state':descriptor.auth_state,'capabilities':{
                    'steer':caps.steer,'resume':caps.resume,'approvals':caps.approvals,
                    'tools_filter':caps.tools_filter,'streaming':caps.streaming},
                'budget_enforcement':self.capabilities(identifier)})
        return result

    def capabilities(self, engine, *, adopted=False):
        if engine=='internal':return {'time_budget':'shared-host','tool_steps':'shared-host','tokens':'unmeasured','cost':'unmeasured'}
        try:caps=self.workspace.state.engines.adapter(engine).capabilities()
        except KeyError:
            return {'time_budget':'host-monitoring','tool_steps':'unmeasured','tokens':'unmeasured','cost':'unmeasured',
                'steer':'unavailable','resume':'unavailable','approvals':'unavailable','tools_filter':'unavailable',
                'elapsed_deadline_seconds':86400,'filesystem_boundary':'unverified'}
        return {'time_budget':'host-monitoring' if adopted else 'shared-host',
            'tool_steps':'native-max-turns' if engine=='claude' else 'unmeasured',
            'tokens':'unmeasured','cost':'unmeasured','steer':caps.steer,
            'resume':caps.resume,'approvals':caps.approvals,'tools_filter':caps.tools_filter,
            'elapsed_deadline_seconds':86400,
            'filesystem_boundary':'native-policy' if engine=='codex' else 'tool-gateway' if engine=='claude' else 'advisory'}

    def admission(self, principal, parent_id):
        parent=self.agents.get_task(parent_id)
        if not parent:raise KeyError(parent_id)
        limits=resolve_limits(parent.get('limits'))
        active={task['id'] for task in self.agents.children(parent_id) if task['status'] in ACTIVE_STATUSES}
        for relation in self.store.list('delegated_session',principal.id):
            if relation['parent_session_id']!=(self.orchestrator.binding(principal.id) or {}).get('conversation_id'):
                continue
            if relation.get('origin')=='adopted' and relation['control_owner']=='user':continue
            active.update(task['id'] for task in self.workspace.active(relation['id']))
        if len(active)>=limits['max_parallel_subagents']:
            raise Conflict('The main agent has reached its active worker allowance; wait or stop an existing worker')
        bound_sessions={(self.store.get('task',task['id']) or {}).get('conversation_id') for task in self.agents.children(parent_id)}
        pending=[row for row in self.store.list('orchestrator_command',principal.id)
            if row.get('parent_task_id')==parent_id and row.get('operation')=='spawn_engine_agent'
            and row.get('result') is None and row.get('session_id') not in bound_sessions]
        if len(self.agents.children(parent_id))+len(pending)>=limits['max_subagents_total']:
            raise Conflict('The main turn has reached its total delegation allowance')
        budget=self.workspace.state.agent.tree_budget.snapshot(parent_id)
        if budget['reason']:raise Conflict('The shared main-agent execution budget is exhausted; review it before delegating')
        return limits

    def receipt(self, principal, task_id, call, *, create=False):
        identifier=hashlib.sha256((task_id+':'+call.call_id).encode()).hexdigest()
        digest=hashlib.sha256(json.dumps({'name':call.name,'arguments':call.arguments},sort_keys=True).encode()).hexdigest()
        previous=self.store.get('orchestrator_command',identifier)
        if previous:
            if previous['owner']!=principal.id or previous['digest']!=digest:
                raise Conflict('Coordination call ID was reused with changed content')
            if previous.get('result') is None:
                raise Conflict('Previous native dispatch outcome is uncertain. Inspect its session; it will not be replayed.')
            return identifier,previous['result']
        if create:self.store.create('orchestrator_command',principal.id,{
            'digest':digest,'result':None,'operation':call.name,'parent_task_id':task_id},identifier=identifier)
        return identifier,None

    def complete(self, identifier, result):
        row=self.store.get('orchestrator_command',identifier)
        self.store.update('orchestrator_command',identifier,{**row,'result':result},row['revision'])
        return result

    def review(self, principal, identifier):
        row=self.workspace.session(principal,identifier,turns=False)
        self.workspace.record(principal,'conversation',identifier,scope='agent-control')
        self.workspace.record(principal,'conversation',identifier,scope='agent-run')
        if self.orchestrator.is_primary(principal.id,identifier):raise ValueError('The main agent cannot adopt itself')
        if row.get('archived') or row.get('runner_id'):raise ValueError('Restore this local conversation before enrolling it')
        active=self.workspace.active(identifier)
        target={'session_id':identifier,'revision':row['revision'],'title':row['title'],'engine':row['engine'],
            'project_id':row.get('project_id'),'cwd':row.get('cwd'),'model':row.get('model'),
            'mode':row['mode'],'active_task_ids':[task['id'] for task in active],
            'execution_target':self.workspace.execution_target(row)}
        digest=hashlib.sha256(json.dumps(target,sort_keys=True).encode()).hexdigest()
        relation=self.orchestrator.relation(identifier)
        return {**{key:value for key,value in target.items() if key!='execution_target'},
            'review_digest':digest,'control_owner':relation['control_owner'] if relation else 'user',
            'enrollment':{'enrolled':bool(relation),'control_owner':relation['control_owner'] if relation else None},
            'effect':'Enroll status and reports. Existing history, engine, native IDs and folder stay unchanged. Control remains with the user until explicitly returned.'}

    def adopt(self, principal, task_id, call):
        identifier=str(call.arguments.get('session_id') or '')
        for scope in ('agent-view','agent-run','agent-control'):
            self.workspace.record(principal,'conversation',identifier,scope=scope)
        receipt,previous=self.receipt(principal,task_id,call)
        if previous is not None:return previous
        preview=self.review(principal,identifier)
        if preview['review_digest']!=call.arguments.get('review_digest') or preview['revision']!=call.arguments.get('expected_revision'):
            raise Conflict('The existing conversation changed; review its exact current target again')
        with self.store.lock:
            # Another browser can finish the same reviewed enrollment while
            # this request is reading its preview. Return its exact receipt.
            _,raced=self.receipt(principal,task_id,call)
            if raced is not None:return raced
            current_review=self.review(principal,identifier)
            if current_review['review_digest']!=preview['review_digest']:
                raise Conflict('The existing conversation changed during review; inspect its current target again')
            existing=self.orchestrator.relation(identifier)
            if existing:
                if existing['owner']!=principal.id or existing['parent_session_id']!=(self.orchestrator.binding(principal.id) or {})['conversation_id']:
                    raise PermissionError('This conversation is enrolled elsewhere')
            self.receipt(principal,task_id,call,create=True)
            if not existing:
                session=self.workspace.session(principal,identifier,turns=False)
                latest=self.agents.workspace_turns_page(identifier,descending=True,limit=1)
                existing=self.store.create('delegated_session',principal.id,{
                    'parent_session_id':self.orchestrator.binding(principal.id)['conversation_id'],
                    'parent_task_id':task_id,'task_id':latest[0]['task_id'] if latest else None,
                    'control_owner':'user','origin':'adopted','cwd':session.get('cwd'),
                    'source_cwd':session.get('cwd'),'engine':session['engine'],
                    'isolation':{'kind':'existing','path':session.get('cwd'),'automatic_apply':False},
                    'capability_limits':self.capabilities(session['engine'],adopted=True),
                    'review_digest':preview['review_digest']},session.get('project_id'),identifier)
                self.store.log(principal.id,'conversation',identifier,'orchestrator.adopted',{'engine':session['engine'],'control_owner':'user'})
            self.orchestrator._reconciled_owners.discard(principal.id)
            return self.complete(receipt,{'ok':True,'session_id':identifier,'engine':preview['engine'],
                'control_owner':existing['control_owner'],'requires_user_handoff':existing['control_owner']=='user'})

    async def spawn(self, principal, parent_id, call):
        manager=self.workspace.state.agent
        async with manager._spawn_locks[parent_id]:
            receipt,previous=self.receipt(principal,parent_id,call)
            if previous is not None:
                self.workspace.record(principal,'conversation',previous['session_id'])
                return previous
            parent=self.agents.get_task(parent_id)
            metadata=self.workspace.record(principal,'task',parent_id,scope='agent-control')
            if metadata.get('delegation_id') or self.agents.task_agent(parent):
                raise PermissionError('Automatic native delegation requires a directly controlled default main agent')
            engine=str(call.arguments.get('engine') or '')
            if engine=='internal':raise ValueError('Use spawn_subagent or spawn_project_agent for Internal work')
            gateway=self.workspace.state.engines
            if engine not in gateway.engines():raise ValueError('Choose a configured engine from list_agent_engines')
            descriptor=gateway.adapter(engine).descriptor()
            if not descriptor.installed:raise ValueError('This native engine is not installed on the host')
            if descriptor.auth_state in {'unauthenticated','expired'}:raise ValueError('Sign in to this engine in Settings before delegating work')
            mode=str(call.arguments.get('mode') or 'agent')
            if mode not in {'agent','ask'}:raise ValueError('Choose Agent or Ask mode')
            if mode=='ask' and engine not in {'codex','claude'}:
                raise ValueError('Read-only containment is not verified for generic ACP engines; choose an isolated Agent task or a verified read-only engine')
            prompt=str(call.arguments.get('task') or '').strip()
            if not prompt or len(prompt)>64000:raise ValueError('Provide one bounded task of 1 to 64000 characters')
            project_id=call.arguments.get('project_id') or None
            source=None
            if project_id:
                source=self.workspace.state.projects.project(project_id)
                for scope in ('agent-run','agent-control'):
                    self.workspace.require(principal,scope,project_id,source['path'])
            limits=self.admission(principal,parent_id)
            child_limits=resolve_limits({'max_steps':min(24,limits['max_steps']),
                'max_seconds':min(900,limits['max_seconds']), 'shell_timeout_s':limits['shell_timeout_s']})
            self.receipt(principal,parent_id,call,create=True)
            worktree=None;isolation=None;snapshot_path=None
            if source and mode=='agent':
                from termx.agent import worktrees
                if await asyncio.to_thread(worktrees.git_root,source['path']):
                    delivery=getattr(self.workspace.state,'delivery',None)
                    if not delivery:raise ValueError('An enrolled worktree service is required for native project edits')
                    for scope in ('git-read','git-write'):
                        self.workspace.require(principal,scope,project_id,source['path'])
                    worktree=await asyncio.to_thread(delivery.effect,project_id,source['path'],'worktree-create',
                        {'branch':'termx/agent-'+uuid4().hex[:12],'base':'HEAD'})
                    isolation={'kind':'worktree','path':worktree['path'],'base_path':source['path'],'automatic_apply':False}
                else:
                    from termx.agent.child_isolation import snapshot
                    snapshot_path=self.workspace.scratch_root(principal.id)/uuid4().hex/'workspace'
                    isolation=await asyncio.to_thread(snapshot,Path(source['path']),snapshot_path,
                        excluded=[self.agents.path,self.agents.artifact_dir])
                receipt_row=self.store.get('orchestrator_command',receipt)
                self.store.update('orchestrator_command',receipt,{**receipt_row,'prepared_target':{
                    **isolation,'worktree_id':worktree['id'] if worktree else None}},receipt_row['revision'])
            self.orchestrator.actor_for_task(parent_id)
            if source:
                for scope in ('agent-run','agent-control'):
                    self.workspace.require(principal,scope,project_id,source['path'])
            child=self.workspace.create_session(principal,title=str(call.arguments.get('agent') or descriptor.label)[:200],
                project_id=project_id,cwd=source['path'] if source and not worktree else None,
                worktree_id=worktree['id'] if worktree else None,engine=engine,model=call.arguments.get('model'),
                mode=mode,run_limits=child_limits)
            if snapshot_path:
                meta=self.store.get('conversation',child['id'])
                self.store.update('conversation',child['id'],{**meta,'cwd':str(snapshot_path)},meta['revision'])
                self.agents.update_conversation(child['id'],cwd=str(snapshot_path))
            cwd=str(snapshot_path) if snapshot_path else child['cwd']
            isolation=isolation or {'kind':'read-only' if source else 'scratch','path':cwd,
                'base_path':source['path'] if source else cwd,'automatic_apply':False}
            self.store.create('delegated_session',principal.id,{
                'parent_session_id':self.orchestrator.binding(principal.id)['conversation_id'],
                'parent_task_id':parent_id,'task_id':None,'control_owner':'orchestrator','origin':'native',
                'engine':engine,'cwd':cwd,'source_cwd':source['path'] if source else cwd,
                'target_project_id':project_id,'isolation':isolation,'dispatch_call_id':call.call_id,
                'capability_limits':self.capabilities(engine)},project_id,child['id'])
            receipt_row=self.store.get('orchestrator_command',receipt)
            self.store.update('orchestrator_command',receipt,{**receipt_row,'session_id':child['id']},receipt_row['revision'])
            task=await self.workspace.send(principal,child['id'],prompt=prompt,
                request_id='orchestrator:'+parent_id+':'+call.call_id,_coordinator_task_id=parent_id)
            return self.complete(receipt,{'ok':task['status'] not in {'failed','cancelled'},'session_id':child['id'],
                'task_id':task['id'],'status':task['status'],'engine':engine,'capability_limits':self.capabilities(engine)})

    def created(self, parent_id, task_id, relation):
        task=self.agents.get_task(task_id)
        if not task or task.get('engine','internal')=='internal':return
        if task.get('parent_id')!=parent_id:raise Conflict('Native child ancestry was not bound before dispatch')
        if not relation.get('task_id'):
            relation=self.store.update('delegated_session',relation['id'],{**relation,'task_id':task_id},relation['revision'])
        manager=self.workspace.state.agent
        reason=manager.tree_budget.reserve(task_id,['native-dispatch'])
        if reason:raise Conflict('The shared main-agent budget is exhausted before native dispatch')
        manager.tree_budget.start(task_id)
        metadata=self.store.get('task',task_id)
        self.store.update('task',task_id,{**metadata,'native_monitor_started_at':time(),'native_active_seconds':0},metadata['revision'])
        self.watch(parent_id,task_id)

    def watch(self, parent_id, task_id):
        if task_id in self.watchers and not self.watchers[task_id].done():return
        try:loop=asyncio.get_running_loop()
        except RuntimeError:return  # First async managed operation restores supervision.
        worker=loop.create_task(self._watch(parent_id,task_id),name='termx-native-child-'+task_id[:8])
        self.watchers[task_id]=worker
        def done(task):
            if self.watchers.get(task_id) is task:self.watchers.pop(task_id,None)
            if not task.cancelled():task.exception()
        worker.add_done_callback(done)

    def bind_supervisor(self, task_id, parent_id):
        with self.store.lock:
            metadata=self.store.get('task',task_id)
            if metadata:
                self.store.update('task',task_id,{**metadata,'native_supervisor_parent':parent_id,
                    'native_monitor_started_at':metadata.get('native_monitor_started_at') or time()},metadata['revision'])
        self.watch(parent_id,task_id)

    def schedule_cancel(self, task_id, reason):
        worker=asyncio.create_task(self.cancel_once(task_id,reason),name='termx-native-stop-'+task_id[:8])
        self.cancel_workers.add(worker)
        def done(task):
            self.cancel_workers.discard(task)
            if not task.cancelled():task.exception()
        worker.add_done_callback(done)

    def on_event(self, task_id, event, relation):
        task=self.agents.get_task(task_id)
        if not task or task.get('engine','internal')=='internal':return
        parent_id=task.get('parent_id')
        if not parent_id:return  # Adoption does not rewrite original task ancestry.
        manager=self.workspace.state.agent
        handle=manager._subagents.get(parent_id,{}).get(task_id)
        if handle:
            handle.status=task['status']
            if task['status'] not in ACTIVE_STATUSES:
                handle.result=str(task.get('result') or task.get('error') or '')
                handle.done.set()
        actor=self.workspace.state.identity.principal_by_id(relation['owner'])
        if not actor:return
        try:
            self.workspace.record(actor,'task',task_id)
            self.workspace.record(actor,'conversation',relation['id'])
        except (KeyError,PermissionError,HTTPException) as exc:
            if isinstance(exc,HTTPException) and exc.status_code not in {403,404}:raise
            return
        if event['type']=='task.delegation':return
        payload=event.get('payload') or {}
        if len(json.dumps(payload,default=str).encode())>16000:
            payload={'message':'Activity details are available in the child conversation','truncated':True}
        manager.emit_external(parent_id,'subagent.event',{'child_id':task_id,
            'call_id':relation.get('dispatch_call_id'),'agent':(self.agents.get_conversation(relation['id']) or {}).get('title','Agent'),
            'type':event['type'],'payload':payload})
        if event['type'] in {'task.completed','task.failed','task.cancelled'}:
            manager.emit_external(parent_id,'subagent.finished',{'child_id':task_id,
                'call_id':relation.get('dispatch_call_id'),'agent':(self.agents.get_conversation(relation['id']) or {}).get('title','Agent'),
                'status':task['status'],'result':str(task.get('result') or task.get('error') or '')[:4000]})

    async def cancel_once(self, task_id, reason):
        with self.store.lock:
            metadata=self.store.get('task',task_id)
            if not metadata:raise KeyError(task_id)
            pending=metadata.get('native_cancel_request')
            if pending:return self.agents.get_task(task_id)
            self.store.update('task',task_id,{**metadata,'native_cancel_request':{'reason':reason,'outcome':'pending','created_at':time()}},metadata['revision'])
        try:
            async def owned_cancel():
                with self.store.lock:
                    current=self.store.get('task',task_id)
                    relation=self.orchestrator.relation((current or {}).get('conversation_id') or '')
                    if relation and relation['control_owner']=='user':
                        # wait_for schedules this coroutine separately. Recheck
                        # ownership where cancellation actually starts, not
                        # only when the receipt was prepared.
                        self.store.update('task',task_id,{**current,'native_cancel_request':None},current['revision'])
                        raise Conflict('The user took direct control before native cancellation began')
                return await self.workspace.state.engines.cancel(task_id)
            result=await asyncio.wait_for(owned_cancel(),timeout=5)
        except Conflict:
            self.workspace.state.agent.emit_external(task_id,'coordination.control.blocked',{
                'message':'Native cancellation was not dispatched because the user took direct control.'})
            raise
        except (Exception,asyncio.CancelledError):
            self.workspace.state.agent.emit_external(task_id,'task.recovery.blocked',{
                'message':'Native cancellation could not be confirmed. Inspect the engine session before retrying.'})
            raise
        current=self.agents.get_task(task_id)
        if current and current['status'] not in ACTIVE_STATUSES:
            metadata=self.store.get('task',task_id)
            self.store.update('task',task_id,{**metadata,'native_cancel_request':{
                **metadata['native_cancel_request'],'outcome':'confirmed'}},metadata['revision'])
        return result

    async def _watch(self, parent_id, task_id):
        manager=self.workspace.state.agent
        task=self.agents.get_task(task_id)
        if not task:return
        shared=bool(task.get('parent_id'))
        metadata=self.store.get('task',task_id) or {}
        elapsed=float(metadata.get('native_active_seconds') or 0);last=monotonic();saved=last;metered=False
        try:
            while True:
                task=self.agents.get_task(task_id)
                if not task or task['status'] not in ACTIVE_STATUSES:return
                meta=self.store.get('task',task_id) or {}
                if meta.get('native_cancel_request'):return  # Never replay an uncertain cancellation.
                if task['status']=='cancelling':return  # Another explicit Stop already owns this attempt.
                relation=self.orchestrator.relation(meta.get('conversation_id') or '')
                if relation and relation['control_owner']=='user':return
                live_parent_id=meta.get('native_supervisor_parent',parent_id)
                parent=self.agents.get_task(live_parent_id) if live_parent_id else None
                now=monotonic()
                waiting=any(row['status']=='pending' for row in self.agents.approvals(task_id))
                if not waiting:elapsed+=now-last
                last=now
                if now-saved>=10:
                    meta=self.store.get('task',task_id)
                    self.store.update('task',task_id,{**meta,'native_active_seconds':elapsed},meta['revision'])
                    saved=now
                if shared and waiting and metered:manager.tree_budget.stop(task_id);metered=False
                elif shared and not waiting and not metered:manager.tree_budget.start(task_id);metered=True
                reason=None
                if live_parent_id and (not parent or parent['status'] in {'cancelled','cancelling'}):reason='Main agent stopped'
                elif elapsed>=min(900,resolve_limits(task.get('limits'))['max_seconds']):reason='Native child host time allowance exhausted'
                elif time()-float(meta.get('native_monitor_started_at') or meta.get('created_at') or time())>=86400:reason='Native child elapsed-time safety ceiling reached'
                elif shared and manager.tree_budget.snapshot(task_id)['reason']:reason='Shared main-agent execution allowance exhausted'
                try:
                    self.workspace.validate_task_execution(task_id)
                    actor=self.workspace.state.identity.principal_by_id(meta.get('owner') or '')
                    if not actor:raise PermissionError('The conversation owner is no longer active')
                    self.workspace.record(actor,'task',task_id,scope='agent-run')
                    self.workspace.record(actor,'conversation',meta['conversation_id'],scope='agent-run')
                except (KeyError,PermissionError,HTTPException) as exc:
                    if isinstance(exc,HTTPException) and exc.status_code not in {403,404}:raise
                    reason='Delegated execution authority changed'
                if reason:
                    manager.emit_external(task_id,'task.recovery.blocked',{'message':reason+'; requesting native cancellation'})
                    await self.cancel_once(task_id,reason)
                    return
                await asyncio.sleep(.5)
        except asyncio.CancelledError:
            raise  # Host shutdown/takeover is not a new cancellation request.
        except Exception:
            manager.emit_external(task_id,'task.recovery.blocked',{
                'message':'Native supervision could not verify its execution boundary; requesting a single cancellation. Inspect the engine session before continuing.'})
            with self.store.lock:
                current=self.store.get('task',task_id) or {}
                relation=self.orchestrator.relation(current.get('conversation_id') or '')
                human_owned=bool(relation and relation['control_owner']=='user')
            if not human_owned:
                await self.cancel_once(task_id,'Native supervision failed closed')
        finally:
            if shared:manager.tree_budget.stop(task_id)
            with self.store.lock:
                meta=self.store.get('task',task_id)
                if meta:self.store.update('task',task_id,{**meta,'native_active_seconds':elapsed},meta['revision'])

    async def close(self):
        workers=list(self.watchers.values())+list(self.cancel_workers)
        for worker in workers:worker.cancel()
        await asyncio.gather(*workers,return_exceptions=True)
