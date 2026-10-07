"""Internal engine browser schemas and execution through the same task broker."""
import asyncio
from time import monotonic
from termx.agent.policy import PolicyDecision
from termx.agent.tools.registry import ToolSpec,ToolOutcome
from termx.browser.tools import BROWSER_TOOL_SCHEMAS,BrowserToolAdapter


def decide(call,ctx):
    # BrowserService performs async policy preflight and holds resumable human
    # approval before any effect. Generic shell approvals cannot authorize it.
    if call.name=='browser_action' and ctx.read_only:
        return PolicyDecision(False,False,'Ask mode is read-only','Browser input requires Agent mode')
    return PolicyDecision(True,False,'Browser grant broker','Task-scoped built-in browser only')

async def execute(call,ctx):
    service=getattr(ctx.manager,'browser',None)
    if not service:return ToolOutcome({'refused':True,'error':'Built-in browser unavailable'})
    if call.name=='browser_wait_for_handoff':
        deadline=monotonic()+max(1,min(300,int(call.arguments.get('seconds',300))))
        while monotonic()<deadline:
            if ctx.cancel.is_set():raise asyncio.CancelledError
            binding=service.records.get('browser-task',ctx.task_id)
            if binding and service.session_valid(binding['principal_id'],binding['session_id'],binding['policy_version']):
                tabs=await BrowserToolAdapter(service).execute('browser_tabs',{},task_id=ctx.task_id,principal_id=binding['principal_id'],session_id=binding['session_id'],call_id='wait')
                if tabs:return ToolOutcome({'tabs':tabs})
            await asyncio.sleep(.25)
        return ToolOutcome({'refused':True,'error':'No built-in tab handed off before wait expired'})
    binding=service.records.get('browser-task',ctx.task_id)
    if not binding:return ToolOutcome({'refused':True,'error':'User must hand a built-in tab to this task first'})
    if ctx.read_only and call.name=='browser_action':return ToolOutcome({'refused':True,'error':'Ask mode cannot send browser input'})
    if not service.session_valid(binding['principal_id'],binding['session_id'],binding['policy_version']):return ToolOutcome({'refused':True,'error':'Browser task authority expired or was revoked'})
    result=await BrowserToolAdapter(service).execute(call.name,call.arguments,task_id=ctx.task_id,principal_id=binding['principal_id'],session_id=binding['session_id'],call_id=ctx.task_id+':'+call.call_id,authority=lambda:service.session_valid(binding['principal_id'],binding['session_id'],binding['policy_version']))
    return ToolOutcome(result if isinstance(result,dict) else {'tabs':result})

def register(registry):
    for tool in BROWSER_TOOL_SCHEMAS:
        name=tool['name']
        registry.register(ToolSpec(name,tool['description'],tool['parameters'],'external' if name=='browser_action' else 'read',False,'never',execute,decide,expose_read_only=name!='browser_action'))
    registry.register(ToolSpec('browser_wait_for_handoff','Wait for the user to hand a built-in tab to this task before starting browser work.',{'type':'object','properties':{'seconds':{'type':'integer','minimum':1,'maximum':300}},'additionalProperties':False},'read',False,'never',execute,decide,expose_read_only=False))
