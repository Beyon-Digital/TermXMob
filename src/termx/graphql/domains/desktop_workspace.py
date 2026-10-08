"""Relay Node identity and bounded connections over canonical workspace records."""
from __future__ import annotations
import asyncio
import threading
import base64
import secrets
from time import time
from collections.abc import Iterable
import strawberry
from strawberry import relay
from strawberry.scalars import JSON
from strawberry.types import Info
from termx.graphql.errors import fail

def principal(info):
    current=info.context.state.identity.resolve(info.context.secret)
    if not current:fail(401,'Managed sign-in required')
    return current.principal

def session_node(row):
    return WorkspaceSessionNode(id=row['id'],canonical_id=row['id'],title=row['title'],
        engine=row['engine'],revision=row['revision'],project_id=row.get('project_id'),snapshot=row)

@strawberry.type
class WorkspaceSessionNode(relay.Node):
    id:relay.NodeID[str]
    canonical_id:str
    title:str
    engine:str
    revision:int
    project_id:str|None
    snapshot:JSON

    @classmethod
    def resolve_nodes(cls,*,info:Info,node_ids:Iterable[str],required=False):
        owner=principal(info);service=info.context.state.workspace;result=[]
        for identifier in node_ids:
            try:result.append(session_node(service.session(owner,identifier,turns=False)))
            except (KeyError,PermissionError):
                if required:fail(404,'Session not found')
                result.append(None)
        return result

@strawberry.type
class WorkspaceTurnNode(relay.Node):
    id:relay.NodeID[str]
    canonical_id:str
    session_id:str
    sequence:int
    prompt:str
    task_id:str|None

    @strawberry.field
    def task(self,info:Info)->JSON|None:
        if not self.task_id:return None
        service=info.context.state.workspace
        service.record(principal(info),'conversation',self.session_id,scope='agent-view')
        task=service.agents.get_task(self.task_id)
        if not task:return None
        fields={'id','prompt','cwd','provider_id','model','status','limits','mode','parent_id','plan','result','error','engine','created_at','updated_at'}
        result={key:value for key,value in task.items() if key in fields}
        metadata=service.store.get('task',self.task_id) or {}
        for key in ('worktree_id','worktree_digest','worktree_branch','execution_location'):
            if key in metadata:result[key]=metadata[key]
        if task['status'] in {'running','awaiting_approval','paused','cancelling','recovering','recovery_confirmation_required','planning'}:
            result['events']=service.agents.events(self.task_id,tail_limit=200)
            result['approvals']=service.agents.approvals(self.task_id)
        return result

    @classmethod
    def resolve_nodes(cls,*,info:Info,node_ids:Iterable[str],required=False):
        owner=principal(info);service=info.context.state.workspace;result=[]
        for identifier in node_ids:
            row=service.agents.workspace_turn(identifier)
            try:
                if not row:raise KeyError(identifier)
                service.session(owner,row['conversation_id'],turns=False)
                result.append(turn_node(row))
            except (KeyError,PermissionError):
                if required:fail(404,'Turn not found')
                result.append(None)
        return result

def turn_node(row):
    return WorkspaceTurnNode(id=row['id'],canonical_id=row['id'],session_id=row['conversation_id'],
        sequence=row['sequence'],prompt=row['prompt'],task_id=row['task_id'])

def cursor(session,sequence):return base64.b64encode(f'{session}:{sequence}'.encode()).decode()
def read_cursor(value,session):
    if not value:return 0
    try:
        identifier,sequence=base64.b64decode(value,validate=True).decode().rsplit(':',1)
        if identifier!=session or int(sequence)<0:raise ValueError()
        return int(sequence)
    except (ValueError,UnicodeError):fail(400,'Cursor does not belong to this session')

@strawberry.type
class WorkspacePageInfo:
    has_next_page:bool=strawberry.field(name='hasNextPage')
    has_previous_page:bool=strawberry.field(name='hasPreviousPage')
    start_cursor:str|None=strawberry.field(name='startCursor')
    end_cursor:str|None=strawberry.field(name='endCursor')

@strawberry.type
class WorkspaceSessionConnection(relay.ListConnection[WorkspaceSessionNode]):
    page_info:WorkspacePageInfo=strawberry.field(name='pageInfo')

@strawberry.type
class WorkspaceTurnEdge:
    cursor:str
    node:WorkspaceTurnNode

@strawberry.type
class WorkspaceTurnConnection:
    edges:list[WorkspaceTurnEdge]
    page_info:WorkspacePageInfo=strawberry.field(name='pageInfo')

@strawberry.type
class ArtifactNode(relay.Node):
    id:relay.NodeID[str]
    canonical_id:str
    title:str
    kind:str
    version:int
    content:JSON|None
    @classmethod
    def resolve_nodes(cls,*,info:Info,node_ids:Iterable[str],required=False):
        owner=principal(info);result=[]
        for identifier in node_ids:
            try:
                row=info.context.state.artifacts.get(owner.id,identifier)
                info.context.require_project('agent-view',row['project']) if row['project'] else info.context.require('agent-view')
                result.append(cls(id=row['id'],canonical_id=row['id'],title=row['title'],kind=row['kind'],version=row['version'],content=row['content']))
            except Exception:
                if required:fail(404,'Artifact not found')
                result.append(None)
        return result

@strawberry.type
class DesktopWorkspaceQueries:
    node:relay.Node|None=relay.node()
    nodes:list[relay.Node]=relay.node()

    @strawberry.field
    async def workspace_sessions(self,info:Info,query:str='',archived:bool=False,project_id:str|None=None,first:int=50,after:str|None=None)->WorkspaceSessionConnection:
        state=info.context.state
        lock=getattr(state,'workspace_session_pages_lock',None)
        if lock is None:
            lock=threading.RLock();state.workspace_session_pages_lock=lock
        owner=principal(info)
        def project():
            with lock:
                return session_connection(info,owner,query,archived,project_id,first,after)
        result=await asyncio.to_thread(project)
        current=principal(info)
        if current.id!=owner.id or current.policy_version!=owner.policy_version:
            fail(403,'Session authorization changed; reload the list')
        return result

    @strawberry.field
    def workspace_turns(self,info:Info,session_id:str,first:int|None=None,after:str|None=None,last:int|None=None,before:str|None=None)->WorkspaceTurnConnection:
        service=info.context.state.workspace;service.session(principal(info),session_id,turns=False)
        if last is not None and (first is not None or after is not None):fail(400,'Choose forward or backward pagination')
        size=last if last is not None else first or 50
        if not 1<=size<=200:fail(400,'Page size must be between 1 and 200')
        sequence=read_cursor(after,session_id)
        backward=last is not None
        rows=service.agents.workspace_turns_page(session_id,after_sequence=sequence,
            before_sequence=read_cursor(before,session_id) if before else None,descending=backward,limit=size+1)
        visible=list(reversed(rows[:size])) if backward else rows[:size]
        edges=[WorkspaceTurnEdge(cursor=cursor(session_id,row['sequence']),node=turn_node(row)) for row in visible]
        return WorkspaceTurnConnection(edges=edges,page_info=WorkspacePageInfo(has_next_page=bool(before) if backward else len(rows)>size,
            has_previous_page=len(rows)>size if backward else sequence>0,start_cursor=edges[0].cursor if edges else None,end_cursor=edges[-1].cursor if edges else None))

def session_connection(info,owner,query,archived,project_id,first,after):
    state=info.context.state
    if not 1<=first<=100:fail(400,'Session page size must be between 1 and 100')
    pages=getattr(state,'workspace_session_pages',None)
    if pages is None:pages={};state.workspace_session_pages=pages
    for identifier in list(pages):
        if pages[identifier]['expires']<=time():pages.pop(identifier)
    scope=(owner.id,owner.policy_version,query,archived,project_id)
    offset=0
    if after:
        try:
            identifier,position=base64.b64decode(after,validate=True).decode().split(':')
            offset=int(position)+1
            page=pages[identifier]
            if page['scope']!=scope or offset<0:raise ValueError()
        except (ValueError,UnicodeError,KeyError):fail(409,'Session cursor expired or changed scope; reload the list')
    else:
        if len(pages)>=64:pages.pop(min(pages,key=lambda key:pages[key]['expires']))
        identifier=secrets.token_urlsafe(24)
        page={'scope':scope,'expires':time()+120,'rows':state.workspace.sessions(owner,query=query,archived=archived,project_id=project_id)}
        pages[identifier]=page
    rows=page['rows'];visible=rows[offset:offset+first]
    # Check current authority again: a cursor is never delegated consent.
    authority=state.authorization.resource_snapshot(owner,'agent-view','conversation',[row['id'] for row in visible])
    boundaries=dict(authority.resources)
    allowed=authority.allowed_ids
    edges=[]
    for position,row in enumerate(visible,offset):
        if row.get('owner')!=owner.id or row['id'] not in allowed or (not authority.administrator and row.get('project_id')!=boundaries.get(row['id'])):
            fail(403,'Session authorization changed; reload the list')
        edges.append(relay.Edge(node=session_node(row),cursor=base64.b64encode(f'{identifier}:{position}'.encode()).decode()))
    state.authorization.validate_resource_snapshot(authority)
    return WorkspaceSessionConnection(edges=edges,page_info=WorkspacePageInfo(has_next_page=offset+first<len(rows),
        has_previous_page=offset>0,start_cursor=edges[0].cursor if edges else None,end_cursor=edges[-1].cursor if edges else None))
