"""In-process MCP transport; identity stays in the trusted host closure."""
from __future__ import annotations
import asyncio
import json
import secrets
from time import monotonic
from termx.browser.tools import BROWSER_TOOL_SCHEMAS,BrowserToolAdapter
from termx.auto_review import ReviewRequired,ActionBlocked

def controlled_server(service,task_id,pending_review,*,read_only=False):
    from claude_agent_sdk import create_sdk_mcp_server,tool
    adapter=BrowserToolAdapter(service)
    async def execute(name,args):
        if read_only and name=='browser_action':raise PermissionError('Ask mode does not allow browser input')
        binding=service.records.get('browser-task',task_id)
        if not binding or not service.session_valid(binding['principal_id'],binding['session_id'],binding['policy_version']):
            raise PermissionError('Hand a built-in tab to this task before using browser tools')
        # The SDK protocol handles call correlation, while this host-owned ID
        # remains stable through approval resume and is never model-supplied.
        call_id=task_id+':claude:'+secrets.token_hex(12)
        kwargs=dict(task_id=task_id,principal_id=binding['principal_id'],session_id=binding['session_id'],call_id=call_id,
                    authority=lambda:service.session_valid(binding['principal_id'],binding['session_id'],binding['policy_version']))
        try:return await adapter.execute(name,args,**kwargs)
        except ReviewRequired as exc:
            if exc.record['status']!='needs_user':raise PermissionError('This action cannot be replayed; obtain a fresh observation')
            allowed=await pending_review(exc.record,binding)
            if not allowed:raise PermissionError('Browser action denied')
            return await adapter.execute(name,args,**kwargs)
    definitions=[]
    for schema in BROWSER_TOOL_SCHEMAS:
        def handler(name):
            async def invoke(args):
                try:
                    result=await execute(name,args)
                    return {'content':[{'type':'text','text':json.dumps(result)}]}
                except (PermissionError,ValueError,ActionBlocked,KeyError):
                    # Protocol errors expose no input values, credentials or
                    # raw page exceptions to persistent native engine events.
                    return {'isError':True,'content':[{'type':'text','text':'Browser action refused or stale. Ask the user to hand off the tab, then observe it again.'}]}
            return invoke
        definitions.append(tool(schema['name'],schema['description'],schema['parameters'])(handler(schema['name'])))
    @tool('browser_wait_for_handoff','Wait for the user to explicitly hand a built-in browser tab to this task.',{'type':'object','properties':{'seconds':{'type':'integer','minimum':1,'maximum':300}},'additionalProperties':False})
    async def wait(args):
        deadline=monotonic()+max(1,min(300,int(args.get('seconds',300))))
        while monotonic()<deadline:
            binding=service.records.get('browser-task',task_id)
            if binding:
                try:
                    tabs=await adapter.execute('browser_tabs',{},task_id=task_id,principal_id=binding['principal_id'],session_id=binding['session_id'],call_id='wait')
                    if tabs:return {'content':[{'type':'text','text':json.dumps({'tabs':tabs})}]}
                except PermissionError:pass
            await asyncio.sleep(.25)
        return {'isError':True,'content':[{'type':'text','text':'No browser tab handed off before the wait expired.'}]}
    definitions.append(wait)
    return create_sdk_mcp_server(name='termx-browser',version='1.0.0',tools=definitions)
