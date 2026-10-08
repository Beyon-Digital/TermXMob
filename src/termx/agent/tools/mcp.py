"""Scoped MCP through the normal Internal scheduler and approval pipeline."""
import asyncio
from termx.agent.policy import PolicyDecision
from .registry import ToolSpec,ToolOutcome


def decide(call,ctx):
    if call.name=='mcp_call':
        if ctx.read_only:return PolicyDecision(False,False,'Ask mode is read-only','External MCP effects require Agent mode')
        return PolicyDecision(False,True,'External MCP tool','Runs the exact selected MCP tool with the reviewed arguments')
    return PolicyDecision(True,False,'Scoped MCP catalog','Inspect selected trusted connections without invoking tools')


async def execute(call,ctx):
    broker=getattr(ctx.manager,'mcp',None)
    if not broker:return ToolOutcome({'refused':True,'error':'Managed MCP broker is unavailable'})
    try:
        if call.name=='mcp_catalog':return ToolOutcome({'connections':await asyncio.to_thread(broker.catalog,ctx.task_id)})
        if ctx.read_only:return ToolOutcome({'refused':True,'error':'Ask mode cannot invoke external MCP tools'})
        result=await broker.call(ctx.task_id,call.arguments.get('connection'),call.arguments.get('tool'),call.arguments.get('arguments',{}))
        return ToolOutcome(result)
    except Exception:
        # MCP errors can contain endpoint/header/server text. Avoid exposing
        # credentials or unauthorized schemas from a rejected transport.
        return ToolOutcome({'refused':True,'error':'MCP authority, connection, tool selection or transport is unavailable; inspect the selected connection in Managers'})


def register(registry):
    registry.register(ToolSpec('mcp_catalog','Inspect the approved tool schemas of the trusted MCP connections selected in this task preset.',{'type':'object','properties':{},'additionalProperties':False},'read',False,'never',execute,decide))
    registry.register(ToolSpec('mcp_call','Invoke an exact approved external MCP tool. First inspect mcp_catalog; connection/tool/project authority is checked before every call.',{'type':'object','properties':{'connection':{'type':'string'},'tool':{'type':'string'},'arguments':{'type':'object'}},'required':['connection','tool','arguments'],'additionalProperties':False},'external',False,'always',execute,decide,expose_read_only=False))
