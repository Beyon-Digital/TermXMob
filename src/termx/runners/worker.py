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

def capabilities():
    registry=default_registry()
    return {'protocol':PROTOCOL,'engine':'internal','credential_transport':'host-stdio-broker',
            'review':'host-bound-fingerprint-v1','tools':sorted(TOOLS.intersection(registry.names())),
            'root':'/workspace','network':'none','version':'0.3.0'}

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
    def __init__(self):self.pending={};self.task_id=None;self.manager=None;self.closed=asyncio.Event()
    def send(self,value):sys.stdout.buffer.write(frame(value));sys.stdout.buffer.flush()
    async def rpc(self,method,arguments):
        identifier=uuid.uuid4().hex;future=asyncio.get_running_loop().create_future();self.pending[identifier]=future
        self.send({'type':'rpc','id':identifier,'method':method,'arguments':arguments})
        try:return await asyncio.wait_for(future,600)
        finally:self.pending.pop(identifier,None)
    async def listen(self):
        while True:
            raw=await asyncio.to_thread(sys.stdin.buffer.readline,MAX_FRAME+2)
            if not raw:self.closed.set();return
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
    def _fail(self,task_id,exc):
        # Installed-code diagnostics are scoped to the canonical task. Never
        # include tracebacks, arguments, environment or provider credentials.
        from termx.agent.policy import redact
        detail=redact(str(exc))[:500] if isinstance(exc,(TypeError,AttributeError)) else ''
        self._emit(task_id,'runner.failure',{'exception_type':type(exc).__name__,'detail':detail})
        super()._fail(task_id,exc)
    async def _invoke_tool(self,entry,ctx):
        call=entry.call
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

async def run():
    raw=await asyncio.to_thread(sys.stdin.buffer.readline,MAX_FRAME+2)
    if len(raw)>MAX_FRAME+1 or not raw.endswith(b'\n'):raise ValueError('Invalid startup frame')
    start=json.loads(raw)
    if start.get('type')!='start' or start.get('protocol')!=PROTOCOL:raise ValueError('Unsupported engine port')
    root=Path('/workspace')
    # Test-only isolated path is never accepted in production container commands.
    if os.environ.get('TERMX_RUNNER_TEST_ROOT'):root=Path(os.environ['TERMX_RUNNER_TEST_ROOT'])
    root.mkdir(parents=True,exist_ok=True)
    data=Path(tempfile.mkdtemp(prefix='termx-runner-'))
    os.environ['TERMX_CONFIG_DIR']=str(data/'config')
    os.environ['TERMX_AGENTS_DIR']=str(data/'agents')
    from termx.project_files import ProjectFiles
    projects=ProjectFiles();projects.register(str(root))
    store=AgentStore(data/'ledger')
    provider=start['provider'];store.put_provider(provider['id'],kind='openai-compatible',name='Host broker',base_url='https://broker.invalid',model=provider['model'],capabilities=['shell'],secret_configured=False)
    channel=Channel();manager=ReviewedManager(channel,store,CredentialStore(memory={}),SimpleNamespace(),adapter_factory=lambda *_:BrokerAdapter(channel),runner_for=lambda profile,**kwargs:HostSandboxRunner(profile=profile),project_files=projects)
    channel.manager=manager
    def event(task_id,event):
        channel.task_id=task_id
        channel.send({'type':'event','event':event,'task':store.get_task(task_id)})
    manager._listeners.add(event)
    listener=asyncio.create_task(channel.listen())
    try:
        task=await manager.create_task(prompt=start['prompt'],cwd=str(root),provider_id=provider['id'],model=provider['model'],mode=start.get('mode','agent'),limits=start.get('limits'),attachments=start.get('attachments'))
        channel.task_id=task['id'];channel.send({'type':'ready','remote_task_id':task['id']})
        while not channel.closed.is_set():
            task=store.get_task(channel.task_id)
            if task['status'] in {'completed','failed','cancelled'}:
                channel.send({'type':'finished','task':task});break
            await asyncio.sleep(.05)
    finally:
        await manager.close();listener.cancel();await asyncio.gather(listener,return_exceptions=True);store.close();projects.close()

if __name__=='__main__':
    if '--capabilities' in sys.argv:print(json.dumps(capabilities()))
    else:asyncio.run(run())
