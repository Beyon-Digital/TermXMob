"""MCP tools for the existing Internal loop; no alternate agent/runtime loop."""
import json
from termx.agent.policy import redact
from termx.agent.store import ACTIVE_STATUSES
from .client import McpConnectionError
from .scope import authorize,definition_digest,validate_bindings


class McpTaskBroker:
    def __init__(self,state):self.state=state

    def preflight(self,principal,project_id,cwd,conversation_id,preset):
        links=validate_bindings(self.state,((preset or {}).get('file') or {}).get('mcp_connections') or [])
        if not isinstance(links,list) or len(links)>32:raise ValueError('Select at most32 MCP connections')
        pinned={}
        for link in links:
            conn=self.state.mcp_registry().get(link.get('connection'))
            if not conn or not conn.enabled or not conn.trusted:raise PermissionError('Selected MCP connection is unavailable or untrusted')
            authorize(self.state,conn,'agent-run',principal=principal,project_id=project_id,cwd=cwd,resource_id=conversation_id)
            handle=self.state.mcp_pool._connections.get(conn.id)
            if not handle:raise McpConnectionError('Connect the selected MCP connection in Managers before dispatch')
            self.state.mcp_pool._current(handle.conn,project_id)
            selected=link.get('tools')
            if selected is not None and (not isinstance(selected,list) or len(selected)>512 or any(not isinstance(t,str) for t in selected)):
                raise ValueError('MCP preset tools must be a bounded list')
            tools={t['name'] for t in (handle.catalog_data or {}).get('tools',[])}
            if selected and '*' not in selected and not set(selected)<=tools:raise PermissionError('MCP selection is outside the inspected catalog')
            if '*' not in conn.approved_tools and selected and '*' not in selected and not set(selected)<=set(conn.approved_tools):raise PermissionError('MCP preset requests unapproved tools')
            pinned[conn.id]={'definition_digest':definition_digest(conn),'catalog_fingerprint':handle.fingerprint,'tools':sorted(tools)}
        return pinned

    def _binding(self,task_id,conn_id):
        state=self.state
        task=state.agent_store.get_task(task_id)
        metadata=state.workspace.store.get('task',task_id)
        origin=state.browser.records.get('agent-task-authority',task_id)
        if not task or task['status'] not in ACTIVE_STATUSES or task['status']=='cancelling' or not metadata or not origin:
            raise PermissionError('MCP requires an active owned workspace task and originating managed session')
        live=state.identity.execution_session(origin['session_id'])
        if not live or live.principal.id!=metadata['owner'] or origin['principal_id']!=live.principal.id or origin['policy_version']!=live.principal.policy_version or (origin.get('project_id') or None)!=metadata.get('project_id'):
            raise PermissionError('MCP originating authority was revoked or changed')
        if metadata.get('runner_id'):raise PermissionError('Host MCP is unavailable in a dedicated runner')
        if metadata.get('delegation_id'):
            conversation=state.workspace.store.get('conversation',metadata['conversation_id'])
            state.workspace.validate_delegation(live.principal,conversation,metadata['delegation_id'])
        preset=state.agent_store.task_agent(task) or {}
        links=(preset.get('file') or {}).get('mcp_connections') or []
        link=next((item for item in links if item.get('connection')==conn_id),None)
        if link is None:raise PermissionError('MCP connection is not selected in this task preset')
        conn=state.mcp_registry().get(conn_id)
        if not conn or not conn.enabled or not conn.trusted:raise PermissionError('MCP connection is disabled, untrusted or removed')
        snapshot=(metadata.get('mcp_snapshot') or {}).get(conn.id)
        handle=state.mcp_pool._connections.get(conn.id)
        if not snapshot or snapshot['definition_digest']!=definition_digest(conn) or not handle or snapshot['catalog_fingerprint']!=handle.fingerprint:
            raise PermissionError('MCP definition/catalog changed after task dispatch; start a new task with reviewed settings')
        authorize(state,conn,'agent-run',principal=live.principal,project_id=metadata.get('project_id'),cwd=task['cwd'],resource_id=metadata['conversation_id'])
        if metadata.get('cwd')!=task['cwd']:raise PermissionError('MCP task execution folder changed')
        return task,conn,link,metadata

    def catalog(self,task_id):
        task=self.state.agent_store.get_task(task_id)
        preset=self.state.agent_store.task_agent(task) if task else None
        links=((preset or {}).get('file') or {}).get('mcp_connections') or []
        out=[]
        for link in links:
            _,conn,selected,meta=self._binding(task_id,link.get('connection'))
            handle=self.state.mcp_pool._connections.get(conn.id)
            if not handle:raise McpConnectionError('Connect the selected MCP connection in Managers before using it')
            self.state.mcp_pool._current(handle.conn,meta.get('project_id'))
            tools=(self.state.mcp_pool.catalog(conn.id,project_id=meta.get('project_id')) or {}).get('tools',[])
            selected_tools=selected.get('tools')
            approved=conn.approved_tools
            tools=[tool for tool in tools if ('*' in approved or tool['name'] in approved) and (selected_tools is None or '*' in selected_tools or tool['name'] in selected_tools)]
            out.append({'connection':conn.id,'definition_digest':definition_digest(conn),'tools':tools})
        encoded=json.dumps(out)
        if len(encoded.encode())>256_000:raise ValueError('MCP catalog exceeds the task context budget')
        return out

    async def call(self,task_id,conn_id,tool,arguments):
        task,conn,link,meta=self._binding(task_id,conn_id)
        if task['mode']=='ask':raise PermissionError('Ask mode cannot invoke external MCP effects')
        if not isinstance(arguments,dict) or len(json.dumps(arguments).encode())>64_000 or not isinstance(tool,str) or not tool or len(tool)>200:
            raise ValueError('Provide bounded MCP arguments and a tool name')
        selected=link.get('tools')
        if selected is not None and '*' not in selected and tool not in selected:
            raise PermissionError('MCP tool is outside the frozen preset selection')
        def current(_):self._binding(task_id,conn_id)
        result=await self.state.mcp_pool.call_tool(conn.id,tool,arguments,project_id=meta.get('project_id'),authorize=current)
        # Recheck before releasing any observation to the task/model.
        self._binding(task_id,conn_id)
        encoded=json.dumps(result)
        if len(encoded.encode())>256_000:raise ValueError('MCP response exceeds the task context budget')
        return {'connection':conn.id,'tool':tool,'result':redact(encoded)}
