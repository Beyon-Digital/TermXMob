"""MCP scopes use enrolled execution projects, never caller-supplied owner text."""
import hashlib
import json
from fastapi import HTTPException
from .defs import ConnectionError_


def definition_digest(conn):
    data=conn.as_dict();data.pop('path',None)
    return hashlib.sha256(json.dumps(data,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def projects(conn):
    allowed = list(getattr(conn,'allowed_projects',[]) or [])
    owner = getattr(conn,'owner','user')
    if owner.startswith('project:'):
        project = owner.removeprefix('project:')
        if allowed and allowed != [project]:
            raise ConnectionError_('connection owner/project scope conflicts')
        return [project]
    if owner != 'user':raise ConnectionError_('unknown connection owner')
    return allowed


def require_scope(conn, project_id=None):
    allowed=projects(conn)
    if allowed and (not project_id or project_id not in allowed):
        raise PermissionError('MCP connection is outside this execution project')


def validate_projects(state,conn):
    enrolled={p['id'] for p in state.projects.projects()}
    if not set(projects(conn)) <= enrolled:
        raise ConnectionError_('MCP scope contains an unenrolled project')


def validate_bindings(state,bindings):
    if not isinstance(bindings,list) or len(bindings)>32:raise ValueError('MCP bindings must contain at most32 connection selections')
    out=[];seen=set()
    for link in bindings:
        if not isinstance(link,dict) or set(link)-{'connection','tools'}:raise ValueError('MCP preset bindings contain only connection and tool selections')
        identifier=link.get('connection')
        if not isinstance(identifier,str) or len(identifier)>200:raise ValueError('Select a canonical MCP connection ID')
        conn=state.mcp_registry().get(identifier)
        if not conn or identifier!=conn.id:raise ValueError('MCP connection is not installed')
        validate_projects(state,conn)
        if identifier in seen:raise ValueError('Duplicate MCP connection selection')
        seen.add(identifier);selection={'connection':identifier}
        if 'tools' in link:
            tools=link['tools']
            if not isinstance(tools,list) or len(tools)>512 or any(not isinstance(t,str) or not t or len(t)>200 for t in tools):raise ValueError('MCP tools must be a bounded list of names')
            if '*' not in conn.approved_tools and tools and '*' not in tools and not set(tools)<=set(conn.approved_tools):raise ValueError('MCP selection requests unapproved tools')
            selection['tools']=list(dict.fromkeys(tools))
        out.append(selection)
    return out


def authorize(state,conn,action,*,credential=None,principal=None,project_id=None,cwd=None,resource_id=None):
    """Live authority; worktree paths are checked by the workspace boundary."""
    try:
        validate_projects(state,conn)
        require_scope(conn,project_id)
    except ConnectionError_ as exc:raise HTTPException(400,str(exc)) from exc
    except PermissionError as exc:raise HTTPException(403,str(exc)) from exc
    allowed=projects(conn)
    target=project_id if allowed else None
    if principal is not None:
        if allowed and cwd:
            state.workspace.require(principal,action,target,cwd,resource_kind='conversation' if resource_id else None,resource_id=resource_id)
        else:
            state.authorization.require_principal(principal,action,project_id=target)
    else:
        state.authorization.require(credential,action,project_id=target)
        if allowed and cwd:
            actual=state.authorization.require_path(credential,action,cwd,state.projects.projects())
            if actual != project_id:raise HTTPException(403,'MCP folder/project mismatch')
    return conn
