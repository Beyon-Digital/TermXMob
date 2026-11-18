"""Internal engine browser schemas and execution through the same task broker."""
import asyncio
import hashlib
from time import monotonic
from termx.agent.policy import PolicyDecision
from termx.agent.tools.registry import ToolSpec,ToolOutcome,decide_never
from termx.browser.tools import BROWSER_TOOL_SCHEMAS,SITE_TOOL_SCHEMAS,BrowserToolAdapter,execute_site_tool


def decide(call,ctx):
    # BrowserService performs async policy preflight and holds resumable human
    # approval before any effect. Generic shell approvals cannot authorize it.
    if call.name in {'browser_action','browser_open_tab','browser_close_tab'} and ctx.read_only:
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
            if binding and service.authority_valid(binding):
                tabs=await BrowserToolAdapter(service).execute('browser_tabs',{},task_id=ctx.task_id,principal_id=binding['principal_id'],session_id=binding['session_id'],call_id='wait')
                if tabs:return ToolOutcome({'tabs':tabs})
            await asyncio.sleep(.25)
        return ToolOutcome({'refused':True,'error':'No built-in tab handed off before wait expired'})
    binding=service.records.get('browser-task',ctx.task_id)
    requested_tab=call.arguments.get('tab_id')
    def granted():
        current=service.records.get('browser-task',ctx.task_id)
        if not current:return None
        for tab in service.tabs(current['principal_id']):
            grant=service.records.get('grant',tab['grant_id']) if tab.get('grant_id') else None
            if (not requested_tab or tab['id']==requested_tab) and grant and grant['run_id']==ctx.task_id and service._grant_valid(tab,grant):return current
        return None
    binding=granted()
    if not binding:
        # Park the existing call without granting observations or finishing the
        # turn. A human's task-scoped handoff resumes this same call identity.
        ctx.emit('task.handoff.required',{'call_id':call.call_id,'tab_id':requested_tab,'message':'Hand a built-in tab to this task to continue.'})
        previous=ctx.store.get_task(ctx.task_id)
        ctx.store.update_task(ctx.task_id,status='paused')
        deadline=monotonic()+300
        try:
            while monotonic()<deadline:
                if ctx.cancel.is_set():raise asyncio.CancelledError
                task=ctx.store.get_task(ctx.task_id)
                if not task or task['status'] in {'completed','cancelled','failed','cancelling'}:raise asyncio.CancelledError
                binding=granted()
                if binding:break
                await asyncio.sleep(.1)
            if not binding:return ToolOutcome({'refused':True,'error':'Browser handoff wait expired; no action executed'})
        finally:
            current=ctx.store.get_task(ctx.task_id)
            if current and current['status']=='paused':ctx.store.update_task(ctx.task_id,status=previous['status'])
        ctx.emit('task.handoff.resumed',{'call_id':call.call_id,'tab_id':requested_tab})
    if ctx.read_only and call.name in {'browser_action','browser_open_tab','browser_close_tab'}:return ToolOutcome({'refused':True,'error':'Ask mode cannot send browser input'})
    if not service.authority_valid(binding):return ToolOutcome({'refused':True,'error':'Browser task authority expired or was revoked'})
    result=await BrowserToolAdapter(service).execute(call.name,call.arguments,task_id=ctx.task_id,principal_id=binding['principal_id'],session_id=binding['session_id'],call_id=ctx.task_id+':'+call.call_id,authority=lambda:service.authority_valid(binding))
    return ToolOutcome(result if isinstance(result,dict) else {'tabs':result})

# Saved-site tools act for the signed-in user who started the task, never through a tab grant.
def _site_authority(service,ctx):
    row=service.records.get('agent-task-authority',ctx.task_id)
    if row and service.session_valid(row['principal_id'],row['session_id'],row['policy_version']):return row
    row=service.records.get('browser-task',ctx.task_id)
    if row and service.authority_valid(row):return row
    raise PermissionError('This task is not acting for a signed-in user, so it cannot use saved sites')

def _site_origin(ctx,call):
    # Display only: the approval names where the site lives. A lookup failure leaves the approval generic, never open.
    try:
        service=ctx.manager.browser;row=_site_authority(service,ctx)
        return service.site(str(call.arguments.get('site_id') or ''),row['principal_id'])['origin']
    except Exception:return ''

def _site_decision(call,ctx,base,*,fingerprint,display,matcher,risk,ask_each_time):
    engine=ctx.policy_engine
    if engine is None:return base
    return engine.decide_tool(call,ctx,fingerprint=hashlib.sha256(fingerprint.encode()).hexdigest(),display=display,matcher=matcher,base=base,capabilities=('browser.site',),risk=risk,ask_each_time=ask_each_time)

def decide_site_open(call,ctx):
    if ctx.read_only:return PolicyDecision(False,False,'Ask mode is read-only','Opening a site requires Agent mode')
    origin=_site_origin(ctx,call)
    base=PolicyDecision(False,True,'Open saved site',f'Opens {origin or "a saved site"} in a new built-in tab. The tab stays under your control; the agent cannot act in it until you hand it off.')
    return _site_decision(call,ctx,base,fingerprint='browser_site_open|'+origin,display='browser_site_open · '+(origin or 'saved site'),matcher={'origin':origin},risk='external',ask_each_time=False)

def decide_site_pin(call,ctx):
    if ctx.read_only:return PolicyDecision(False,False,'Ask mode is read-only','Changing saved sites requires Agent mode')
    target=str(call.arguments.get('url') or call.arguments.get('site_id') or '')
    base=PolicyDecision(True,False,'Saved-site pin','Pins or unpins a site in your saved sites')
    return _site_decision(call,ctx,base,fingerprint='browser_site_pin|'+target,display='browser_site_pin · '+target[:80],matcher={},risk='safe',ask_each_time=False)

def decide_site_note_write(call,ctx):
    if ctx.read_only:return PolicyDecision(False,False,'Ask mode is read-only','Saving a site note requires Agent mode')
    text=str(call.arguments.get('content') or '');origin=_site_origin(ctx,call)
    base=PolicyDecision(False,True,'Save site note',f'Saves a note on {origin or "a saved site"}. It is read back later as untrusted data, never as instructions. Note ({len(text)} characters): {text[:2000]}')
    digest=hashlib.sha256(text.encode()).hexdigest()
    return _site_decision(call,ctx,base,fingerprint=f'browser_site_note_write|{call.arguments.get("site_id")}|{digest}',display='browser_site_note_write · '+(origin or 'saved site'),matcher={},risk='sensitive',ask_each_time=True)

async def execute_site(call,ctx):
    service=getattr(ctx.manager,'browser',None)
    if not service:return ToolOutcome({'refused':True,'error':'Built-in browser unavailable'})
    if ctx.read_only and call.name in {'browser_site_open','browser_site_pin','browser_site_note_write'}:return ToolOutcome({'ok':False,'refused':True,'error':'Ask mode cannot change saved sites'})
    try:
        binding=_site_authority(service,ctx)
        result=await execute_site_tool(service,call.name,call.arguments,principal_id=binding['principal_id'],session_id=binding.get('session_id') or binding['principal_id'])
    except KeyError:return ToolOutcome({'ok':False,'refused':True,'error':'Saved site or profile not found'})
    except (ValueError,PermissionError) as error:return ToolOutcome({'ok':False,'refused':True,'error':str(error)[:300]})
    return ToolOutcome(result,event={'name':call.name})

_SITE_TOOLS={'browser_sites_list':('read',True,'never',decide_never),'browser_site_note_read':('read',True,'never',decide_never),
             'browser_site_open':('external',False,'policy',decide_site_open),'browser_site_pin':('write',False,'policy',decide_site_pin),
             'browser_site_note_write':('write',False,'always',decide_site_note_write)}

def register(registry):
    for tool in BROWSER_TOOL_SCHEMAS:
        name=tool['name']
        registry.register(ToolSpec(name,tool['description'],tool['parameters'],'external' if name in {'browser_action','browser_open_tab','browser_close_tab'} else 'read',False,'never',execute,decide,expose_read_only=name not in {'browser_action','browser_open_tab','browser_close_tab'}))
    registry.register(ToolSpec('browser_wait_for_handoff','Wait for the user to hand a built-in tab to this task before starting browser work.',{'type':'object','properties':{'seconds':{'type':'integer','minimum':1,'maximum':300}},'additionalProperties':False},'read',False,'never',execute,decide,expose_read_only=False))
    for tool in SITE_TOOL_SCHEMAS:
        mutability,parallel,approval,decide_site=_SITE_TOOLS[tool['name']]
        registry.register(ToolSpec(tool['name'],tool['description'],tool['parameters'],mutability,parallel,approval,execute_site,decide_site,expose_read_only=mutability=='read'))
