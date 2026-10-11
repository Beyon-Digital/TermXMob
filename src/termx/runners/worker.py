"""Credential-free stdio engine port; the existing AgentManager owns execution.

Production requires Docker's network=none, immutable image, no host mounts,
nonroot UID, readonly root and fixed cgroup limits. The worker never receives a
provider credential and cannot contact the host API or provider over a network.
"""
from __future__ import annotations
import asyncio
import hashlib
import json
import os
import sys
import uuid
import tempfile
import threading
import argparse
import re
from time import monotonic
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace
from termx.agent.manager import AgentManager
from termx.agent.providers import ProviderCall, ProviderTurn
from termx.agent.secrets import CredentialStore
from termx.agent.store import AgentStore
from termx.agent.tools import default_registry
from termx.agent.tools.registry import ToolRegistry, ToolOutcome
from termx.agent.scheduler import CallScheduler
from termx.sandbox.host import HostSandboxRunner

PROTOCOL = 1
MAX_FRAME = 8 * 1024 * 1024
TOOLS = {'read_file','write_file','apply_patch','list_files','search_files','grep','glob','run_shell','run_check','search_project','git_status','git_diff','git_log','git_show','project_manifest'}

def capabilities(machine_root=None):
    registry=default_registry()
    return {'protocol':PROTOCOL,'engine':'internal','credential_transport':'host-stdio-broker',
            'review':'host-bound-fingerprint-v1','tools':sorted(TOOLS.intersection(registry.names())),
            'root':machine_root or '/workspace','network':'machine' if machine_root else 'none',
            'machine_control':'stop-file-v1' if machine_root else None,
            'version':'0.3.0','agent_presets':'snapshot-v1'}

def frame(value):
    raw=json.dumps(value,separators=(',',':'),allow_nan=False).encode()
    if len(raw)>MAX_FRAME:raise ValueError('Runner protocol frame exceeds 8 MiB')
    return raw+b'\n'

def file_state(call,cwd):
    """Taken by the installed worker, never from model-provided summaries."""
    names=[]
    if call.name in {'write_file','read_file'}:names=[str(call.arguments.get('path') or '')]
    elif call.name=='apply_patch':
        from termx.agent.tools.filesystem import _split_unified_diff
        names=[p for before,after,_ in _split_unified_diff(str(call.arguments.get('patch') or '')) for p in (before,after) if p]
    root=Path(cwd).resolve();state=[]
    for name in names:
        path=(root/name).resolve()
        if not name or not path.is_relative_to(root):raise ValueError('Remote tool path escapes its workspace')
        if path.is_file() and path.stat().st_size>20*1024*1024:raise ValueError('Reviewed target exceeds 20 MiB')
        state.append([str(path),hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None])
    return state

class Channel:
    def __init__(self):self.pending={};self.task_id=None;self.manager=None;self.control_error=None;self.closed=asyncio.Event();self.phase='startup';self._reader_stopped=threading.Event();self._diagnostics_stopped=threading.Event();self._incoming=None
    def send(self,value):sys.stdout.buffer.write(frame(value));sys.stdout.buffer.flush()
    async def rpc(self,method,arguments):
        if self.closed.is_set():raise RuntimeError('Host runner control channel closed')
        self.phase='rpc:'+method
        identifier=uuid.uuid4().hex;future=asyncio.get_running_loop().create_future();self.pending[identifier]=future
        self.send({'type':'rpc','id':identifier,'method':method,'arguments':arguments})
        try:return await asyncio.wait_for(future,600)
        finally:self.pending.pop(identifier,None)
    async def listen(self):
        try:
            await self._receive()
        except Exception:
            # Invalid protocol and failed transport are failures even when the
            # cancellation guard wins the race against an outstanding RPC.
            # Never publish the untrusted frame or parser exception text.
            self.control_error=RuntimeError('Host runner control protocol failed')
        finally:
            self.closed.set();self._reader_stopped.set()
            for future in list(self.pending.values()):
                if not future.done():future.set_exception(RuntimeError('Host runner control channel closed'))
            if self.manager is not None and self.task_id is not None:self.manager.cancel(self.task_id)

    async def next_frame(self):
        if self._incoming is None:
            # os.read avoids BufferedReader's mutex during interpreter teardown.
            # Keep the sole stdin reader outside asyncio's non-daemon executor.
            loop=asyncio.get_running_loop();queue=asyncio.Queue(maxsize=32);self._incoming=queue
            self._input_slots=threading.BoundedSemaphore(32)
            def deliver(value):
                if self._reader_stopped.is_set():self._input_slots.release()
                else:
                    if queue.full():
                        while not queue.empty():queue.get_nowait();self._input_slots.release()
                        queue.put_nowait(ValueError('Runner control queue exceeded'));self._reader_stopped.set()
                    else:queue.put_nowait(value)
            def dispatch(value):
                while not self._reader_stopped.is_set():
                    if self._input_slots.acquire(timeout=.25):break
                else:return
                try:loop.call_soon_threadsafe(deliver,value)
                except RuntimeError:self._input_slots.release();self._reader_stopped.set()
            def read():
                buffered=b''
                try:
                    while not self._reader_stopped.is_set():
                        chunk=os.read(0,min(65536,MAX_FRAME+1-len(buffered)))
                        if self._reader_stopped.is_set():return
                        if not chunk:
                            dispatch(ValueError('Incomplete runner frame') if buffered else b'');return
                        buffered+=chunk
                        while b'\n' in buffered:
                            line,buffered=buffered.split(b'\n',1)
                            if len(line)>MAX_FRAME:dispatch(ValueError('Runner frame exceeds limit'));return
                            dispatch(line+b'\n')
                        if len(buffered)>MAX_FRAME:dispatch(ValueError('Runner frame exceeds limit'));return
                except BaseException as error:dispatch(error)
            threading.Thread(target=read,name='runner-stdio',daemon=True).start()
        value=await self._incoming.get()
        self._input_slots.release()
        if isinstance(value,BaseException):raise value
        return value

    async def _receive(self):
        while True:
            raw=await self.next_frame()
            if not raw:return
            if len(raw)>MAX_FRAME+1 or not raw.endswith(b'\n'):raise ValueError('Invalid runner frame')
            value=json.loads(raw);kind=value.get('type')
            if kind=='rpc-result':
                future=self.pending.get(value.get('id'))
                if future and not future.done():
                    if value.get('error'):future.set_exception(RuntimeError('Host broker refused runner operation'))
                    else:future.set_result(value.get('result'))
            elif kind=='cancel' and self.task_id:self.manager.cancel(self.task_id)
            elif kind=='steer' and self.task_id:self.manager.steer(self.task_id,str(value.get('message') or '')[:20000])
            elif kind=='approval' and self.task_id:
                asyncio.create_task(self.manager.resolve_approval(self.task_id,value['approval_id'],value['decision'],remember=None,limits=value.get('limits')))
            else:raise ValueError('Unknown runner control frame')

    def diagnostics(self):
        if os.environ.get('TERMX_RUNNER_DIAGNOSTICS')!='1':return
        def watch():
            # Test/qualification-only: no locals, source text, arguments, env or
            # credentials. At most four bounded stack-location samples.
            for _ in range(4):
                if self._diagnostics_stopped.wait(5):return
                stacks=[]
                for identifier,current in sys._current_frames().items():
                    if identifier==threading.get_ident():continue
                    locations=[]
                    for _ in range(12):
                        if current is None:break
                        locations.append(Path(current.f_code.co_filename).name+':'+current.f_code.co_name+':'+str(current.f_lineno));current=current.f_back
                    stacks.append(locations)
                sys.stderr.write(json.dumps({'runner_phase':self.phase,'thread_locations':stacks})[:12000]+'\n');sys.stderr.flush()
        threading.Thread(target=watch,name='runner-diagnostics',daemon=True).start()

    def stop(self):self._reader_stopped.set();self._diagnostics_stopped.set()

class BrokerAdapter:
    def __init__(self,channel):self.channel=channel
    async def test(self):return 'Host broker'
    async def plan(self,prompt,cwd,manifest):
        result=await self.channel.rpc('plan',{'prompt':prompt,'cwd':cwd,'manifest':manifest})
        return result[0],result[1]
    async def turn(self,**arguments):
        value=await self.channel.rpc('turn',arguments)
        value['calls']=[ProviderCall(**c) for c in value['calls']]
        return ProviderTurn(**value)

class ReviewedManager(AgentManager):
    def __init__(self,channel,*args,**kwargs):
        super().__init__(*args,**kwargs);self.channel=channel
        registry=ToolRegistry()
        for name in capabilities()['tools']:
            spec=default_registry().get(name)
            def delegated(call,ctx,spec=spec):
                decision=spec.decide(call,ctx)
                # Exact host review below replaces eligible action prompts, while
                # preserving hard denies, Ask restrictions and capability gates.
                if decision.approval_required and decision.approval_kind!='capability' and decision.auto_resolved!='deny':
                    return replace(decision,allowed=True,approval_required=False)
                return decision
            registry.register(replace(spec,decide=delegated))
        self._tools=registry;self._scheduler=CallScheduler(registry)
    def _mark_cancelled(self,task_id):
        if self.channel.control_error is not None:
            if self.store.get_task(task_id)['status']!='failed':
                self._fail(task_id,self.channel.control_error)
        else:
            super()._mark_cancelled(task_id)
    def _fail(self,task_id,exc):
        # Installed-code diagnostics are scoped to the canonical task. Never
        # include tracebacks, arguments, environment or provider credentials.
        from termx.agent.policy import redact
        detail=redact(str(exc))[:500] if isinstance(exc,(TypeError,AttributeError)) else ''
        self._emit(task_id,'runner.failure',{'exception_type':type(exc).__name__,'detail':detail})
        super()._fail(task_id,exc)
    async def _invoke_tool(self,entry,ctx):
        call=entry.call
        preset=self.store.task_agent(ctx.task)
        if preset and preset.get('tools') and call.name not in preset['tools']:return ToolOutcome({'refused':True,'error':'Tool is outside the frozen runner preset'})
        if entry.spec is None or call.name not in self._tools.names():return ToolOutcome({'refused':True,'error':'Tool is not qualified for this dedicated runner'})
        before=file_state(call,ctx.cwd)
        request={'call':asdict(call),'state':before,'read_only':ctx.read_only}
        authorization=await self.channel.rpc('review',request)
        if not authorization.get('allowed'):return ToolOutcome({'refused':True,'error':'Host review denied or invalidated action'})
        review_id=authorization['review_id']
        try:
            if before!=file_state(call,ctx.cwd):raise ValueError('Remote target changed after review')
            result=await super()._invoke_tool(entry,ctx)
            await self.channel.rpc('effect-result',{'review_id':review_id,'ok':True})
            return result
        except BaseException:
            await self.channel.rpc('effect-result',{'review_id':review_id,'ok':False})
            raise

async def run(machine_root=None,job_id=None,seconds=3600):
    if os.name=='nt':
        import msvcrt
        msvcrt.setmode(0,os.O_BINARY);msvcrt.setmode(1,os.O_BINARY)
    channel=Channel();deadline=monotonic()+seconds
    raw=await asyncio.wait_for(channel.next_frame(),min(30,seconds))
    if len(raw)>MAX_FRAME+1 or not raw.endswith(b'\n'):raise ValueError('Invalid startup frame')
    start=json.loads(raw)
    if start.get('type')!='start' or start.get('protocol')!=PROTOCOL:raise ValueError('Unsupported engine port')
    root=Path(machine_root or '/workspace')
    # Test-only isolated path is never accepted in production container commands.
    if not machine_root and os.environ.get('TERMX_RUNNER_TEST_ROOT'):root=Path(os.environ['TERMX_RUNNER_TEST_ROOT'])
    root.mkdir(parents=True,exist_ok=True)
    control=root/'.termx-control'
    if machine_root:
        control.mkdir(mode=0o700,exist_ok=True)
        if (control/(job_id+'.stop')).exists():
            (control/(job_id+'.done')).touch();raise ValueError('Machine task was cancelled before startup')
    data=Path(tempfile.mkdtemp(prefix='termx-runner-'))
    os.environ['TERMX_CONFIG_DIR']=str(data/'config')
    os.environ['TERMX_AGENTS_DIR']=str(data/'agents')
    from termx.project_files import ProjectFiles
    projects=ProjectFiles();projects.register(str(root))
    store=AgentStore(data/'ledger')
    provider=start['provider'];store.put_provider(provider['id'],kind='openai-compatible',name='Host broker',base_url='https://broker.invalid',model=provider['model'],capabilities=['shell'],secret_configured=False)
    manager=ReviewedManager(channel,store,CredentialStore(memory={}),SimpleNamespace(),adapter_factory=lambda *_:BrokerAdapter(channel),runner_for=lambda profile,**kwargs:HostSandboxRunner(profile=profile),project_files=projects)
    channel.diagnostics()
    channel.manager=manager
    def event(task_id,event):
        channel.task_id=task_id
        channel.phase='event:'+event['type']
        channel.send({'type':'event','event':event,'task':store.get_task(task_id)})
    manager._listeners.add(event)
    preset=start.get('custom_agent')
    if preset:
        if not isinstance(preset,dict) or not isinstance(preset.get('id'),str) or not set(preset.get('tools') or []).issubset(capabilities()['tools']):raise ValueError('Unqualified worker preset')
        # The container boundary supplies confinement. A host preset cannot
        # switch the worker to a host sandbox or carry host-native skills.
        preset={key:preset.get(key) for key in ('id','name','instructions','tools','limits','workspace_revision')}
        preset.update(sandbox_profile='agent',approval_mode='standard')
    listener=asyncio.create_task(channel.listen())
    async def watch_machine():
        while True:
            if monotonic()>=deadline or (control/(job_id+'.stop')).exists():
                if channel.task_id:manager.cancel(channel.task_id)
                channel.closed.set()
                for future in list(channel.pending.values()):
                    if not future.done():future.set_exception(RuntimeError('Machine execution stopped'))
                return
            await asyncio.sleep(.05)
    watcher=asyncio.create_task(watch_machine()) if machine_root else None
    try:
        task=await manager.create_task(prompt=start['prompt'],cwd=str(root),provider_id=provider['id'],model=provider['model'],mode=start.get('mode','agent'),limits=start.get('limits'),attachments=start.get('attachments'),custom_agent_snapshot=preset,custom_agent_id=preset['id'] if preset else None)
        channel.task_id=task['id'];channel.send({'type':'ready','remote_task_id':task['id']})
        while not channel.closed.is_set():
            task=store.get_task(channel.task_id)
            if task['status'] in {'completed','failed','cancelled'}:
                channel.send({'type':'finished','task':task});break
            await asyncio.sleep(.05)
    finally:
        if watcher:
            watcher.cancel();await asyncio.gather(watcher,return_exceptions=True)
        channel.stop();await manager.close();listener.cancel();await asyncio.gather(listener,return_exceptions=True)
        # A task may already have paused or reached a terminal state when its
        # listener rejects a frame. Keep the protocol failure explicit without
        # depending on another agent-loop cancellation callback being scheduled.
        if channel.control_error is not None and channel.task_id and store.get_task(channel.task_id)['status']!='failed':
            manager._fail(channel.task_id,channel.control_error)
        store.close();projects.close()
        if machine_root:(control/(job_id+'.done')).touch()

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--capabilities',action='store_true')
    parser.add_argument('--machine-root')
    parser.add_argument('--job-id')
    parser.add_argument('--stop-job')
    parser.add_argument('--seconds',type=int,default=3600)
    args=parser.parse_args()
    if args.machine_root:
        root=Path(args.machine_root)
        if not root.is_absolute() or str(root.resolve())!=args.machine_root:parser.error('Machine root must be an absolute canonical path')
        job=args.stop_job or args.job_id
        if not args.capabilities and (not job or not re.fullmatch('[a-zA-Z0-9_-]{1,128}',job)):parser.error('Machine job ID is required')
        if not 1<=args.seconds<=3600:parser.error('Machine budget must be 1–3600 seconds')
    elif args.stop_job or args.job_id:parser.error('Machine root is required')
    if args.capabilities:print(json.dumps(capabilities(args.machine_root)))
    elif args.stop_job:
        import time
        control=Path(args.machine_root)/'.termx-control';control.mkdir(mode=0o700,exist_ok=True)
        (control/(args.stop_job+'.stop')).touch()
        deadline=monotonic()+10
        while not (control/(args.stop_job+'.done')).exists():
            if monotonic()>=deadline:sys.exit(2)
            time.sleep(.1)
    else:asyncio.run(run(args.machine_root,args.job_id,args.seconds))
