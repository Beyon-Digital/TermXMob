"""Bounded durable follow-ups. A queue is explicit user consent, never a new job grant.

The originating managed session must remain live. A changed execution binding,
policy or dependency requires a fresh review; a lost dispatch receipt is never
replayed automatically. Actual task creation/idempotency remains Workspace.send.
"""
from __future__ import annotations
import asyncio
import hashlib
import json
from time import time
from uuid import uuid4

from termx.agent.limits import resolve_limits
from termx.agent.store import ACTIVE_STATUSES
from termx.agent.manager import _decode_images
from termx.workspace.store import Conflict

PENDING = {'queued','blocked','dispatching'}
MAX_PENDING = 10
MAX_WAIT = 1800


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,allow_nan=False,separators=(',',':')).encode()).hexdigest()


class PromptQueue:
    def __init__(self,workspace,*,clock=time):
        self.workspace=workspace;self.state=workspace.state;self.store=workspace.store;self.clock=clock
        self._worker=None;self._lock=asyncio.Lock();self._closing=False
        self.reconcile()

    def pending(self):
        with self.store.lock:
            rows=self.store.db.execute("SELECT * FROM records WHERE kind='prompt_queue' AND json_extract(body,'$.status') IN ('queued','blocked','dispatching') ORDER BY created,id LIMIT 1000").fetchall()
            return [self.store.unpack(row) for row in rows]

    def binding(self,actor,conversation):
        row=self.workspace.session(actor,conversation,turns=False)
        self.workspace.record(actor,'conversation',conversation,scope='agent-run')
        value=self.workspace.execution_target(row)
        for key in ('reasoning_config','config_options','native_mode'):
            if key in row:value[key]=row[key]
        extensions=getattr(self.state,'extensions',None)
        if row.get('extension_ids') and extensions:
            selected=extensions.execution_context(actor,row['extension_ids'],row.get('project_id'),row['cwd'],resource_id=conversation)
            value['extensions_digest']=digest(selected)
        value['memory_revision']=digest([(item['id'],item['revision']) for item in self.workspace.memories(actor,project_id=row.get('project_id'),include_excluded=False)])
        value['hook_revision']=digest([(item['id'],item['revision']) for item in self.store.list('hook',actor.id) if item.get('enabled') and item.get('project_id') in {None,row.get('project_id')}])
        return value

    def origin(self,actor,sid):
        current=self.state.identity.session_by_id(sid)
        if not current or current.principal.id!=actor.id or current.principal.policy_version!=actor.policy_version:
            raise PermissionError('The originating managed session is expired, locked, revoked or changed')
        return current

    def _row(self,actor,identifier,scope='agent-view'):
        row=self.store.get('prompt_queue',identifier)
        if not row or row['owner']!=actor.id:raise KeyError(identifier)
        self.workspace.record(actor,'conversation',row['conversation_id'],scope=scope)
        return row

    @staticmethod
    def public(row):
        result={key:row.get(key) for key in ('id','revision','conversation_id','prompt','attachments','context','status','reason','target','target_digest','expires_at','task_id','created_at','updated_at','interrupt_task_id')}
        result['attachments']=[{'name':item.get('name','Image'),'mime':item.get('mime')} for item in row.get('attachments',[])]
        return result

    def list(self,actor,conversation):
        self.workspace.record(actor,'conversation',conversation)
        with self.store.lock:
            records=self.store.db.execute("SELECT * FROM records WHERE kind='prompt_queue' AND owner=? AND json_extract(body,'$.conversation_id')=? ORDER BY created DESC,id DESC LIMIT 100",(actor.id,conversation)).fetchall()
            rows=[self.store.unpack(row) for row in records]
        return [self.public(row) for row in sorted(rows,key=lambda item:(item['created_at'],item['id']))[-100:]]

    def enqueue(self,actor,conversation,*,sid,prompt,request_id,attachments=None,context=None,limits=None,interrupt_task_id=None):
        origin=self.origin(actor,sid);actor=origin.principal
        if not isinstance(prompt,str) or not prompt.strip() or len(prompt)>64000:raise ValueError('Queue a nonempty prompt of at most 64000 characters')
        if not isinstance(request_id,str) or not 8<=len(request_id)<=128:raise ValueError('Use a bounded unique request ID')
        attachments=attachments or [];context=context or []
        if not isinstance(attachments,list) or len(json.dumps(attachments).encode())>11_000_000:raise ValueError('Queued images exceed the turn limit')
        _decode_images(attachments);self.workspace.validate_context(context)
        submitted={'conversation_id':conversation,'prompt':prompt,'attachments':attachments,'context':context,'limits':limits,'interrupt_task_id':interrupt_task_id}
        request_digest=digest(submitted)
        with self.store.lock:
            existing=next((row for row in self.store.list('prompt_queue',actor.id) if row.get('request_id')==request_id),None)
            if existing:
                if existing['submitted_digest']!=request_digest:raise Conflict('Queue request ID was already used with different content')
                self.workspace.record(actor,'conversation',existing['conversation_id'])
                return self.public(existing)
            if interrupt_task_id:
                metadata=self.workspace.record(actor,'task',interrupt_task_id,scope='agent-control')
                if metadata.get('conversation_id')!=conversation:raise PermissionError('Interrupt targets a different conversation')
                task=self.workspace.agents.get_task(interrupt_task_id)
                if not task or task['status'] not in ACTIVE_STATUSES:raise Conflict('The named current task has already finished; queue without interrupting it')
            target=self.binding(actor,conversation)
            if not target.get('model') or (target.get('engine')=='internal' and not target.get('provider_id')):raise ValueError('Choose an explicit session account and model before queueing; background work cannot choose a billing default')
            pending=[row for row in self.store.list('prompt_queue',actor.id) if row['status'] in PENDING]
            if len(pending)>=MAX_PENDING:raise Conflict('Keep at most ten pending follow-ups per account')
            if sum(len(json.dumps(item.get('attachments',[])))+len(json.dumps(item.get('context',[]))) for item in pending)+len(json.dumps(attachments))+len(json.dumps(context))>24_000_000:raise Conflict('Pending follow-up context exceeds the 24 MB account queue limit')
            resolved=resolve_limits(limits,target.get('run_limits'))
            if target.get('run_limits') and any(resolved[key]>target['run_limits'][key] for key in resolved):raise ValueError('Queue budget cannot exceed the selected session budget')
            identifier=uuid4().hex
            row=self.store.create('prompt_queue',actor.id,{**submitted,'limits':resolved,'request_id':request_id,'submitted_digest':request_digest,
                'dispatch_key':'queue-'+identifier,'conversation_id':conversation,'status':'queued','reason':None,'target':target,'target_digest':digest(target),
                'sid':sid,'policy_version':actor.policy_version,'scopes':sorted(actor.scopes),'expires_at':min(origin.expires_at,self.clock()+MAX_WAIT),'task_id':None},target.get('project_id'),identifier)
            saved=self.store.get('conversation',conversation)
            if saved and saved.get('draft_text','')==prompt and saved.get('draft_context',[])==context:
                self.store.update('conversation',conversation,{**saved,'draft_text':'','draft_context':[]},saved['revision'])
            self.store.log(actor.id,'prompt_queue',identifier,'queued',{'conversation_id':conversation,'target_digest':row['target_digest']})
            return self.public(row)

    def cancel(self,actor,identifier,revision):
        with self.store.lock:
            row=self._row(actor,identifier,'agent-control')
            if row['status'] not in {'queued','blocked'}:raise Conflict('This follow-up was already dispatched; use the task Stop control')
            return self.public(self.store.update('prompt_queue',identifier,{**row,'status':'cancelled','reason':None},revision))

    def current(self,actor,identifier):
        row=self._row(actor,identifier,'agent-control');target=self.binding(actor,row['conversation_id'])
        return {'target':target,'target_digest':digest(target)}

    def renew(self,actor,identifier,*,sid,revision,target_digest):
        origin=self.origin(actor,sid);actor=origin.principal
        with self.store.lock:
            row=self._row(actor,identifier,'agent-control')
            if row['status']!='blocked' or row.get('ambiguous'):raise Conflict('Only an undispatched blocked follow-up can be reviewed and renewed')
            target=self.binding(actor,row['conversation_id'])
            if not target.get('model') or (target.get('engine')=='internal' and not target.get('provider_id')):raise ValueError('Choose an explicit session account and model before renewing queue consent')
            if digest(target)!=target_digest:raise Conflict('Execution settings changed again; review the current target')
            if target.get('run_limits') and any(row['limits'][key]>target['run_limits'][key] for key in row['limits']):raise Conflict('The original queued budget exceeds the new session limit; cancel and queue with a smaller budget')
            self.store.log(actor.id,'prompt_queue',identifier,'renewed',{'target_digest':target_digest})
            return self.public(self.store.update('prompt_queue',identifier,{**row,'status':'queued','reason':None,'sid':sid,'policy_version':actor.policy_version,
                'scopes':sorted(actor.scopes),'target':target,'target_digest':target_digest,'expires_at':min(origin.expires_at,self.clock()+MAX_WAIT)},revision))

    def reconcile(self):
        """Restart only heals known receipts; an unobserved effect is never repeated."""
        for row in self.store.list('prompt_queue'):
            if row['status']!='dispatching':continue
            self._heal(row)

    def _heal(self,row):
        with self.store.lock:
            receipt=self.store.db.execute('SELECT result,state FROM requests WHERE owner=? AND key=?',(row['owner'],row['dispatch_key'])).fetchone()
            if receipt and receipt['result']:
                task=self.workspace.agents.get_task(receipt['result'])
                metadata=self.store.get('task',receipt['result'])
                if task and metadata and metadata.get('conversation_id')==row['conversation_id'] and metadata['owner']==row['owner']:
                    self.store.update('prompt_queue',row['id'],{**row,'status':'dispatched','task_id':task['id'],'reason':None});return
            self.store.update('prompt_queue',row['id'],{**row,'status':'blocked','ambiguous':True,'reason':'Host stopped during dispatch. Inspect the conversation before cancelling and submitting a new follow-up; it will not be replayed.'})

    def _ready(self,row):
        origin=self.state.identity.session_by_id(row['sid'])
        if not origin or origin.principal.id!=row['owner']:raise PermissionError('Originating session expired, locked or revoked. Review the queue after signing in or unlocking.')
        actor=origin.principal
        if row['expires_at']<=self.clock():raise PermissionError('Queue consent expired. Review and renew before continuing.')
        if actor.policy_version!=row['policy_version'] or sorted(actor.scopes)!=row['scopes']:raise PermissionError('Effective permission policy changed. Review and renew the follow-up.')
        if digest(self.binding(actor,row['conversation_id']))!=row['target_digest']:raise Conflict('Session, preset, skills, memory or hooks changed. Review the current execution target before continuing.')
        return actor

    def _dispatch_guard(self,queued,resolved):
        actual=self.workspace.execution_target(resolved)
        for key in ('reasoning_config','config_options','native_mode'):
            if key in resolved:actual[key]=resolved[key]
        if any(actual.get(key)!=queued['target'].get(key) for key in actual):raise Conflict('The resolved execution settings differ from the queued consent')
        self._ready(queued)

    async def tick(self):
        async with self._lock:
            rows=await asyncio.to_thread(self.pending);heads={}
            for row in sorted(rows,key=lambda item:(item['created_at'],item['id'])):
                if row['status'] in PENDING:heads.setdefault(row['conversation_id'],row)
            for row in heads.values():
                if self._closing:return
                if row['status']!='queued':continue
                try:actor=await asyncio.to_thread(self._ready,row)
                except (PermissionError,ValueError,KeyError) as exc:
                    await asyncio.to_thread(self.store.update,'prompt_queue',row['id'],{**row,'status':'blocked','reason':str(exc)[:500]},row['revision']);continue
                if await asyncio.to_thread(self.workspace.active,row['conversation_id']):continue
                with self.store.lock:
                    current=self.store.get('prompt_queue',row['id'])
                    if not current or current['revision']!=row['revision'] or current['status']!='queued':continue
                    self.store.update('prompt_queue',row['id'],{**current,'status':'dispatching'},current['revision'])
                try:
                    # Revalidate after admission and directly before the existing
                    # exact-key send. One canonical service owns actual workers.
                    actor=await asyncio.to_thread(self._ready,row)
                    task=await self.workspace.send(actor,row['conversation_id'],prompt=row['prompt'],request_id=row['dispatch_key'],attachments=row['attachments'],
                        context=row['context'],limits=row['limits'],managed_session_id=row['sid'],preserve_draft=True,dispatch_guard=lambda resolved:self._dispatch_guard(row,resolved))
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    current=self.store.get('prompt_queue',row['id'])
                    with self.store.lock:
                        receipt=self.store.db.execute('SELECT result,state FROM requests WHERE owner=? AND key=?',(row['owner'],row['dispatch_key'])).fetchone()
                    if receipt and receipt['result']:self._heal(current)
                    else:self.store.update('prompt_queue',row['id'],{**current,'status':'blocked','ambiguous':bool(receipt),'reason':str(exc)[:500]})
                else:
                    current=self.store.get('prompt_queue',row['id'])
                    self.store.update('prompt_queue',row['id'],{**current,'status':'dispatched','task_id':task['id'],'reason':None})
                    self.store.log(row['owner'],'prompt_queue',row['id'],'dispatched',{'task_id':task['id']})

    def start(self):
        if self._worker is None:self._worker=asyncio.create_task(self._run())

    async def _run(self):
        while not self._closing:
            try:await self.tick()
            except asyncio.CancelledError:raise
            except Exception:
                # An unavailable authority store never dispatches. Next tick can
                # reconcile while the selected item remains durable/inspectable.
                pass
            await asyncio.sleep(.5)

    async def close(self):
        self._closing=True
        if self._worker:
            self._worker.cancel();await asyncio.gather(self._worker,return_exceptions=True)
