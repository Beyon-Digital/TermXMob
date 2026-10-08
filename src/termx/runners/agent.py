"""Dedicated runner engine adapter and credential-free provider/review broker.

Every admitted job is canonical before dispatch. Immutable container inspection,
current host authority, explicit account/model and network=none precede provider
calls. Remote completion reports are untrusted claims, never approval authority.
"""
from __future__ import annotations
import asyncio
import hashlib
import json
import re
import uuid
from dataclasses import asdict
from pathlib import PurePosixPath
from time import time
from fastapi import HTTPException
from termx.agent.manager import _resolve_model,_decode_images
from termx.agent.providers import ProviderCall
from termx.agent.policy import is_sensitive_path,redact
from termx.agent.store import configured_models
from termx.auto_review import ActionEnvelope, ActionBlocked, ReviewRequired, canonical_hash
from termx.runners.worker import PROTOCOL, MAX_FRAME, TOOLS, frame

class RunnerAgentService:
    def __init__(self,state):
        self.state=state;self.runners=state.runners;self.store=state.agent.store
        self.qualified={};self.workers={};self.channels={};self.approvals={};self.rpc_tasks=set();self.rpc_owners={};self.control_tasks=set();self.review_waiters={}
        with self.runners.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS agent_jobs (task TEXT PRIMARY KEY,runner TEXT,owner TEXT,request_id TEXT,digest TEXT,provider TEXT,credential_ref TEXT,policy_version INTEGER,provider_fingerprint TEXT,status TEXT,UNIQUE(runner,request_id))')
    def owns(self,task):
        if isinstance(task,str):task=self.store.get_task(task)
        return bool(task and (task.get('runtime') or {}).get('runner_protocol')==PROTOCOL and (task.get('runtime') or {}).get('runner_id'))
    def _account(self,provider_id,credential_ref,model=None):
        provider=self.store.get_provider(provider_id)
        if not provider or provider['kind'] not in {'openai','openai-compatible'} or not provider.get('secret_configured') or credential_ref!=provider_id or not self.state.credentials.get(credential_ref):
            raise HTTPException(409,'Select an explicitly configured API account; subscription credentials are not delegated')
        provider=dict(provider);provider['model']=_resolve_model(provider,model)
        return provider
    def _fingerprint(self,provider,reference):
        value=self.state.credentials.get(reference)
        return canonical_hash({'provider':provider,'credential_digest':hashlib.sha256((value or '').encode()).hexdigest()})
    def accounts(self,principal):
        self.state.authorization.require_principal(principal,'host-admin')
        return [{'provider_id':p['id'],'credential_ref':p['id'],'name':p['name'],'models':configured_models(p['model'])} for p in self.store.list_providers() if p['kind'] in {'openai','openai-compatible'} and p.get('secret_configured') and self.state.credentials.get(p['id'])]
    async def preflight(self,principal,runner_id,provider_id,credential_ref,model=None):
        self.state.authorization.require_principal(principal,'host-admin')
        runner=self.runners.row(principal.id,runner_id)
        self.state.authorization.require_principal(principal,'agent-run',project_id=runner['project'])
        provider=self._account(provider_id,credential_ref,model)
        if runner['status']!='ready' or runner['expires']<=time() or not self.runners.authority(runner):raise HTTPException(403,'Runner lease is unavailable or revoked')
        config=runner['configuration']
        if config.get('network','none')!='none':raise HTTPException(409,'AI runner requires isolated networking and host provider brokerage')
        details=json.loads(await self.runners.docker(config,'inspect',runner['container']))[0]
        host=details['HostConfig']
        if (details.get('Image')!=config['image_id'] or not details.get('State',{}).get('Running') or details.get('Config',{}).get('User')!='65534:65534' or not host.get('ReadonlyRootfs') or host.get('NetworkMode')!='none' or (details.get('Mounts') or []) or host.get('Binds') or set(host.get('CapDrop') or [])!={'ALL'} or 'no-new-privileges=true' not in (host.get('SecurityOpt') or []) or not 0<host.get('PidsLimit',0)<=128 or not 0<host.get('Memory',0)<=int(config['memory_mb'])*1024*1024 or not 0<host.get('NanoCpus',0)<=int(float(config['cpu'])*1e9)):
            raise HTTPException(409,'Actual container isolation does not match the admitted runner boundary')
        if set(host.get('Tmpfs') or {})!={'/workspace','/tmp','/run/termx'}:raise HTTPException(409,'Unexpected writable runner mounts')
        probe=self.qualified.get(config['image_id'])
        if probe is None:
            probe=json.loads(await self.runners.docker(config,'exec',runner['container'],'python','-I','-m','termx.runners.worker','--capabilities',timeout=60))
        if any(probe.get(k)!=v for k,v in {'protocol':PROTOCOL,'engine':'internal','root':'/workspace','network':'none','credential_transport':'host-stdio-broker','review':'host-bound-fingerprint-v1'}.items()) or not set(probe.get('tools') or []).issubset(TOOLS):raise HTTPException(409,'Install the qualified TermX runner image before selecting this target')
        self.qualified[config['image_id']]=probe
        return {'runner':runner,'provider':provider,'capabilities':probe}
    def _job(self,task_id):
        with self.runners.db() as db:row=db.execute('SELECT * FROM agent_jobs WHERE task=?',(task_id,)).fetchone()
        if not row:raise HTTPException(404,'Runner task not found')
        return dict(row)
    def _valid(self,job):
        try:
            task=self.store.get_task(job['task'])
            if not task or task['status'] in {'completed','failed','cancelled'}:return False
            principal=self.state.identity.principal_by_id(job['owner'])
            if not principal or principal.policy_version!=job['policy_version']:return False
            self.state.authorization.require_principal(principal,'host-admin')
            runner=self.runners.row(principal.id,job['runner'])
            self.state.authorization.require_principal(principal,'agent-run',project_id=runner['project'])
            workspace=getattr(self.state,'workspace',None)
            if workspace:
                metadata=workspace.store.get('task',job['task'])
                if metadata and metadata.get('delegation_id'):
                    conversation=workspace.store.get('conversation',metadata['conversation_id'])
                    if not conversation:return False
                    workspace.validate_delegation(principal,conversation,metadata['delegation_id'])
            records=getattr(self.state.browser,'records',None)
            authority=records.get('agent-task-authority',job['task']) if records else None
            if authority:
                live=self.state.identity.execution_session(authority['session_id'])
                if not live or live.principal.id!=principal.id or live.principal.policy_version!=job['policy_version']:return False
                self.state.authorization.require_principal(live.principal,'host-admin')
                self.state.authorization.require_principal(live.principal,'agent-run',project_id=runner['project'])
            provider=self._account(job['provider'],job['credential_ref'],self.store.get_task(job['task'])['model'])
            return runner['expires']>time() and self.runners.authority(runner) and self._fingerprint(provider,job['credential_ref'])==job['provider_fingerprint'] and runner['status']=='ready'
        except Exception:return False
    async def create_task(self,principal,runner_id,credential_ref,prompt,model=None,mode='agent',limits=None,conversation_id=None,request_id=None,on_created=None,attachments=None,provider_id=None,custom_agent=None):
        if not request_id or not re.fullmatch(r'[A-Za-z0-9._-]{8,128}',request_id):raise HTTPException(400,'A stable request ID is required')
        if not prompt or len(prompt)>100000:raise HTTPException(400,'Provide a bounded prompt')
        if mode not in {'ask','agent'}:raise HTTPException(400,'Choose Ask or Agent mode')
        images=_decode_images(attachments)
        frame({'attachments':attachments,'prompt':prompt}) # bound before admission/effects
        provider_id=provider_id or credential_ref
        admission=await self.preflight(principal,runner_id,provider_id,credential_ref,model)
        runner,provider=admission['runner'],admission['provider']
        preset=None
        if custom_agent:
            if admission['capabilities'].get('agent_presets')!='snapshot-v1':raise HTTPException(409,'Install a runner image with immutable agent preset support')
            workspace=getattr(self.state,'workspace',None)
            if not workspace:raise HTTPException(409,'Managed preset authority is unavailable')
            from termx.workspace.presets import resolve_preset
            preset=resolve_preset(workspace,principal,custom_agent['id'],engine='internal',project_id=runner['project'],runner_id=runner_id)
            if preset['workspace_revision']!=custom_agent.get('workspace_revision'):raise HTTPException(409,'Agent preset changed before runner admission')
            if not set(preset.get('tools') or []).issubset(set(admission['capabilities']['tools'])):raise HTTPException(409,'Runner image does not support this preset tool set')
        bounded=self.state.agent._limits({**(preset.get('limits') or {}),**(limits or {})} if preset else limits)
        bounded['max_seconds']=min(bounded['max_seconds'],int(runner['expires']-time()),3600)
        bounded['shell_timeout_s']=min(bounded['shell_timeout_s'],bounded['max_seconds'])
        if bounded['max_seconds']<1:raise HTTPException(409,'Runner lease expired')
        original_limits=self.state.agent._limits({**(preset.get('limits') or {}),**(limits or {})} if preset else limits)
        request={'prompt':prompt,'model':provider['model'],'mode':mode,'limits':bounded,'attachments':attachments,'conversation_id':conversation_id,'provider_id':provider_id,'credential_ref':credential_ref,'custom_agent':preset}
        digest=canonical_hash({**request,'limits':original_limits})
        with self.runners.db() as db:
            db.execute('BEGIN IMMEDIATE')
            old=db.execute('SELECT * FROM agent_jobs WHERE runner=? AND request_id=?',(runner_id,request_id)).fetchone()
            if old:
                if old['digest']!=digest:raise HTTPException(409,'Request ID belongs to a different task')
                return self.store.get_task(old['task'],include_events=True)
            if db.execute("SELECT 1 FROM jobs WHERE runner=? AND status='running'",(runner_id,)).fetchone():raise HTTPException(409,'Dedicated runner already has an active job')
            task=self.store.create_task(prompt=prompt,cwd='/workspace',provider_id=provider_id,model=provider['model'],limits=bounded,mode=mode,engine='runner',custom_agent_id=preset['id'] if preset else None,custom_agent_snapshot=preset)
            runtime={'runner_id':runner_id,'runner_protocol':PROTOCOL,'runner_request_id':request_id,'conversation_id':conversation_id,'remote_root':'/workspace','owner':principal.id}
            self.store.update_task(task['id'],runtime=runtime)
            for name,mime,data in images:self.store.save_artifact(task['id'],'upload',mime,data)
            try:
                if on_created:on_created(task['id'])
            except BaseException:
                self.store.update_task(task['id'],status='failed',error='Resource admission failed before dispatch');raise
            db.execute('INSERT INTO agent_jobs VALUES(?,?,?,?,?,?,?,?,?,?)',(task['id'],runner_id,principal.id,request_id,digest,provider_id,credential_ref,principal.policy_version,self._fingerprint(provider,credential_ref),'running'))
            db.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?)',(task['id'],runner_id,'agent:'+request_id,digest,json.dumps({'engine':'internal','task_id':task['id']}),'running',time(),None,None))
        self.state.agent._emit(task['id'],'task.created',{'task':self.store.get_task(task['id']),'execution_location':{'kind':'runner','runner_id':runner_id,'root':'/workspace'}})
        self.workers[task['id']]=asyncio.create_task(self._drive(task['id'],request,runner))
        return self.store.get_task(task['id'],include_events=True)
    def _emit(self,task_id,kind,payload):return self.state.agent._emit(task_id,kind,payload)
    async def _send(self,task_id,value):
        process=self.channels.get(task_id)
        if not process or process.returncode is not None:raise HTTPException(409,'Remote task channel is unavailable; inspect outcome before retrying')
        process.stdin.write(frame(value));await process.stdin.drain()
    async def _drive(self,task_id,request,runner):
        job=self._job(task_id);config=runner['configuration'];process=None
        async def monitor():
            while True:
                await asyncio.sleep(.25)
                if not self._valid(job):raise PermissionError('Runner authority was revoked')
        async def read():
            total=0
            while raw:=await process.stdout.readline():
                total+=len(raw)
                if len(raw)>MAX_FRAME+1 or not raw.endswith(b'\n') or total>64*1024*1024:raise ValueError('Runner output exceeds protocol budget')
                value=json.loads(raw)
                if value.get('type')=='rpc':
                    work=asyncio.create_task(self._rpc(job,value));self.rpc_tasks.add(work);self.rpc_owners[work]=task_id
                    def rpc_done(done):self.rpc_tasks.discard(done);self.rpc_owners.pop(done,None)
                    work.add_done_callback(rpc_done)
                elif value.get('type')=='event':self._event(task_id,value)
                elif value.get('type')=='ready':
                    task=self.store.get_task(task_id);self.store.update_task(task_id,runtime={**task['runtime'],'remote_task_id':value['remote_task_id']})
                elif value.get('type')=='finished':self._snapshot(task_id,value['task']);return
                else:raise ValueError('Unexpected worker frame')
            raise RuntimeError('Runner channel closed before a confirmed result')
        try:
            command=['docker']+(['--host',config['endpoint']] if config.get('endpoint') else ['--context',config['context']])
            process=await asyncio.create_subprocess_exec(*command,'exec','-i',runner['container'],'python','-I','-m','termx.runners.worker',stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE,limit=MAX_FRAME+2)
            self.channels[task_id]=process
            await self._send(task_id,{'type':'start','protocol':PROTOCOL,'provider':{'id':job['provider'],'model':request['model']},'prompt':request['prompt'],'mode':request['mode'],'limits':request['limits'],'attachments':request['attachments'],'custom_agent':request.get('custom_agent')})
            reader=asyncio.create_task(read());watcher=asyncio.create_task(monitor())
            try:
                done,_=await asyncio.wait({reader,watcher},timeout=request['limits']['max_seconds'],return_when=asyncio.FIRST_COMPLETED)
                if not done:raise TimeoutError('Runner time budget exhausted')
                await (reader if reader in done else watcher)
            finally:
                reader.cancel();watcher.cancel();await asyncio.gather(reader,watcher,return_exceptions=True)
        except BaseException as exc:
            current=self.store.get_task(task_id)
            if current['status'] not in {'completed','failed','cancelled'}:
                status='cancelled' if isinstance(exc,asyncio.CancelledError) else 'failed'
                self.store.update_task(task_id,status=status,error='Runner stopped; execution outcome must be verified before retrying')
                self._emit(task_id,'task.status',{'status':status,'outcome':'unknown'})
            try:await self.runners.stop(job['owner'],job['runner'])
            except Exception:pass
        finally:
            pending=[rpc for rpc in self.rpc_tasks if self.rpc_owners.get(rpc)==task_id]
            for rpc in pending:rpc.cancel()
            await asyncio.gather(*pending,return_exceptions=True)
            if process and process.returncode is None:process.kill();await process.wait()
            self.channels.pop(task_id,None);self.workers.pop(task_id,None)
            self.state.browser.review.invalidate(grant_id='runner-task:'+task_id)
            for approval_id,record in list(self.approvals.items()):
                if record['task_id']==task_id:
                    future=record.get('future')
                    if future and not future.done():future.cancel()
                    self.approvals.pop(approval_id,None)
            status=self.store.get_task(task_id)['status']
            with self.runners.db() as db:
                db.execute('UPDATE agent_jobs SET status=? WHERE task=?',(status,task_id))
                db.execute('UPDATE jobs SET status=?,ended=?,result=? WHERE id=?',(status,time(),json.dumps({'task_id':task_id,'status':status}),task_id))
    def _snapshot(self,task_id,remote):
        allowed={k:remote[k] for k in ('status','plan','result','error','metrics','previous_response_id') if k in remote}
        if allowed.get('status') not in {'planning','awaiting_approval','running','paused','completed','failed','cancelled'}:allowed.pop('status',None)
        self.store.update_task(task_id,**allowed)
    def _event(self,task_id,value):
        event=value['event'];kind=event['type'];payload=event.get('payload') or {}
        self._snapshot(task_id,value.get('task') or {})
        if kind=='task.created':payload={'task':self.store.get_task(task_id),'execution_location':{'kind':'runner','runner_id':self._job(task_id)['runner'],'root':'/workspace'}}
        if kind=='user.media':payload={'artifacts':[a for a in self.store.artifacts(task_id) if a['kind']=='upload']}
        if kind=='approval.requested':
            remote=payload['approval'];local=self.store.create_approval(task_id,remote['kind'],{**remote['payload'],'execution_target':'Dedicated runner'})
            self.approvals[local['id']]={'task_id':task_id,'remote_id':remote['id']}
            payload={'approval':local}
        if kind=='approval.resolved':return # local control persists canonical approval
        self._emit(task_id,kind,payload)
    def _adapter(self,provider,qualified_tools=None):
        adapter=self.state.agent._adapter(provider)
        from termx.agent.providers import OpenAIResponsesAdapter
        if isinstance(adapter,OpenAIResponsesAdapter):
            from termx.agent.tools import default_registry
            tools=set(TOOLS if qualified_tools is None else qualified_tools)
            from copy import copy
            adapter=copy(adapter)
            # This adapter is a fresh task-local instance, so filtering cannot
            # alter another conversation's native/local registry.
            adapter._function_tools=lambda read_only,allow_subagents=False:[schema for schema in default_registry().provider_tools(read_only=read_only,allow_subagents=False) if schema['name'] in tools]
        return adapter
    async def _rpc(self,job,value):
        identifier=value['id'];method=value.get('method');args=value.get('arguments') or {};task_id=job['task']
        try:
            if not self._valid(job):raise PermissionError('Authority changed')
            if method in {'plan','turn'}:
                if args.get('cwd')!='/workspace':raise ValueError('Provider context must be the admitted remote workspace')
                provider=self._account(job['provider'],job['credential_ref'],self.store.get_task(task_id)['model'])
                runner=self.runners.row(job['owner'],job['runner'])
                qualified=self.qualified.get(runner['configuration']['image_id'],{}).get('tools') or TOOLS
                preset=self.store.task_agent(self.store.get_task(task_id))
                permitted=set(qualified).intersection(preset['tools']) if preset and preset.get('tools') else set(qualified)
                adapter=self._adapter(provider,permitted)
                if method=='plan':result=await adapter.plan(str(args.get('prompt','')), '/workspace', args.get('manifest') or {})
                else:
                    result=asdict(await adapter.turn(prompt=str(args.get('prompt','')),cwd='/workspace',manifest=args.get('manifest') or {},previous_response_id=args.get('previous_response_id'),input_items=args.get('input_items'),allow_computer=False,read_only=self.store.get_task(task_id)['mode']=='ask'))
                if not self._valid(job):raise PermissionError('Authority changed during provider request')
            elif method=='review':result=await self._review(job,identifier,args)
            elif method=='effect-result':
                review_id=str(args.get('review_id',''))
                if not review_id.startswith(task_id+':'):raise ValueError('Review belongs to another task')
                self.state.browser.review.complete_external(review_id,success=args.get('ok') is True);result={'recorded':True}
            else:raise ValueError('Unsupported runner broker operation')
            if not self._valid(job):raise PermissionError('Authority changed before broker release')
            await self._send(task_id,{'type':'rpc-result','id':identifier,'result':result})
        except asyncio.CancelledError:raise
        except Exception:
            try:await self._send(task_id,{'type':'rpc-result','id':identifier,'error':'Broker operation denied'})
            except Exception:pass
    async def _review(self,job,identifier,args):
        task_id=job['task'];task=self.store.get_task(task_id);call=args.get('call') or {};tool=call.get('name');arguments=call.get('arguments') or {};state=args.get('state') or []
        if tool not in TOOLS or call.get('type')!='function' or not isinstance(arguments,dict) or not isinstance(state,list):raise ValueError('Unqualified remote tool')
        preset=self.store.task_agent(task)
        if preset and preset.get('tools') and tool not in preset['tools']:raise ValueError('Tool is outside the frozen runner preset')
        effect='observe'  if tool in {'read_file','list_files','search_project','git_status','git_diff'} else 'edit' if tool in {'write_file','apply_patch'} else 'unknown'
        hard=None
        if task['mode']=='ask' and effect!='observe':hard='Ask mode cannot mutate the runner workspace'
        if len(state)>200:hard='Too many reviewed file targets'
        if tool in {'write_file','read_file','apply_patch'} and not state:hard='Missing installed-worker file state proof'
        for path,digest in state:
            p=PurePosixPath(path)
            if not p.is_relative_to('/workspace') or '..' in p.parts or is_sensitive_path(path) or (digest is not None and not re.fullmatch('[a-f0-9]{64}',digest)):hard='Sensitive or invalid remote file target'
        if tool=='apply_patch' and '+++ /dev/null' in str(arguments.get('patch','')):effect='delete'
        envelope=ActionEnvelope(task_id+':'+identifier,job['owner'],'runner:'+job['runner'],self.runners.row(job['owner'],job['runner'])['project'],task_id,'runner.'+tool,canonical_hash({'arguments':arguments,'state':state}),'/workspace',effect,'runner-task:'+task_id,job['policy_version'],data_labels=('secret',) if redact(json.dumps(arguments))!=json.dumps(arguments) else ())
        review=self.state.browser.review;validate=lambda:self._valid(job)
        try:permit=await review.authorize(envelope,validate=validate,hard_deny=hard,context={'task_summary':task['prompt'],'effect_summary':effect})
        except ActionBlocked:return {'allowed':False}
        except ReviewRequired as exc:
            if exc.record['status']!='needs_user':return {'allowed':False}
            local=self.store.create_approval(task_id,'tool',{'title':'Dedicated runner action','call':ProviderCall(**call).public(),'browser_review':exc.record,'remote_targets':state,'remember_options':[]})
            future=asyncio.get_running_loop().create_future();self.approvals[local['id']]={'task_id':task_id,'future':future,'review_id':envelope.action_id}
            self.store.update_task(task_id,status='awaiting_approval');self._emit(task_id,'approval.requested',{'approval':local})
            await asyncio.wait_for(future,300)
            try:permit=await review.authorize(envelope,validate=validate,hard_deny=hard)
            except ReviewRequired:return {'allowed':False}
        await review.consume_external(envelope,permit['permit'],validate=validate)
        self.store.update_task(task_id,status='running');return {'allowed':True,'review_id':envelope.action_id}
    def cancel(self,task_id):
        self._job(task_id)
        worker=self.workers.get(task_id)
        if not worker:raise HTTPException(409,'Task channel is unavailable; no automatic replay is permitted')
        worker.cancel()
        self.store.update_task(task_id,status='cancelled',error='Cancelled by user; dedicated container stopping')
        self._emit(task_id,'task.status',{'status':'cancelled','execution_target':'runner'})
        async def stop_cancelled():
            await asyncio.gather(worker,return_exceptions=True)
            job=self._job(task_id)
            try:await self.runners.stop(job['owner'],job['runner'])
            finally:
                with self.runners.db() as db:
                    db.execute("UPDATE agent_jobs SET status='cancelled' WHERE task=?",(task_id,))
                    db.execute("UPDATE jobs SET status='cancelled',ended=? WHERE id=?",(time(),task_id))
        control=asyncio.create_task(stop_cancelled());self.control_tasks.add(control);control.add_done_callback(self.control_tasks.discard)
        self.workers[task_id]=control
        return self.store.get_task(task_id,include_events=True)
    def steer(self,task_id,message):
        self._job(task_id)
        if task_id not in self.channels:raise HTTPException(409,'Task channel is unavailable')
        if not message.strip() or len(message)>20000:raise HTTPException(400,'Provide a bounded steering message')
        asyncio.create_task(self._send(task_id,{'type':'steer','message':message}));return self.store.get_task(task_id,include_events=True)
    async def resolve_approval(self,task_id,approval_id,decision,remember=None,limits=None):
        record=self.approvals.get(approval_id)
        if not record or record['task_id']!=task_id:raise HTTPException(409,'Approval channel is unavailable or already consumed')
        if decision not in {'approved','denied'}:raise HTTPException(400,'Choose approved or denied')
        if not self._valid(self._job(task_id)):raise HTTPException(403,'Authority was revoked')
        if remember:raise HTTPException(400,'Runner approvals are exact one-use actions')
        if record.get('future'):
            self.state.browser.review.decide(record['review_id'],principal_id=self._job(task_id)['owner'],approve=decision=='approved')
            if record['future'].done():raise HTTPException(409,'Review approval expired or was invalidated')
            record['future'].set_result(decision)
        else:await self._send(task_id,{'type':'approval','approval_id':record['remote_id'],'decision':decision,'limits':limits})
        approval=self.store.resolve_approval(approval_id,decision);self.approvals.pop(approval_id,None)
        self._emit(task_id,'approval.resolved',{'approval':approval});return self.store.get_task(task_id,include_events=True)
    async def startup_reconcile(self):
        with self.runners.db() as db:rows=db.execute("SELECT * FROM agent_jobs WHERE status='running'").fetchall()
        for row in rows:
            try:await self.runners.stop(row['owner'],row['runner'])
            except Exception:pass
            self.store.update_task(row['task'],status='failed',error='Host restarted; remote outcome unknown. Verify runner before creating a new task.')
            self._emit(row['task'],'task.recovery.confirmation_required',{'execution_target':'runner','outcome':'unknown','automatic_replay':False})
            with self.runners.db() as db:db.execute("UPDATE agent_jobs SET status='outcome-unknown' WHERE task=?",(row['task'],))
    async def close(self):
        workers=list(self.workers.values())
        for worker in workers:worker.cancel()
        await asyncio.gather(*workers,return_exceptions=True)
        await asyncio.gather(*list(self.control_tasks),return_exceptions=True)
        pending=list(self.rpc_tasks)
        for task in pending:task.cancel()
        await asyncio.gather(*pending,return_exceptions=True)
