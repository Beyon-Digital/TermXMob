"""Managed-session desktop contracts. Same-origin cookies use SessionGuard CSRF."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from termx.auth import extract_passcode
from termx.identity_http import ACCESS_COOKIE
from termx.workspace.automation import AutomationService
from termx.workspace.extensions import ExtensionService
from termx.workspace.service import WorkspaceService
from termx.workspace.store import Conflict


class Input(BaseModel):
    model_config=ConfigDict(extra='forbid')


class SessionInput(Input):
    title: str=Field(default='',max_length=200)
    project_id: str | None=None
    cwd: str | None=None
    engine: str='internal'
    provider_id: str | None=None
    model: str | None=None
    mode: str='ask'
    workflow: str | None=None
    runner_id: str | None=None
    runner_credential_ref: str | None=None
    run_limits: dict | None=None
    worktree_id: str | None=None


class SessionUpdate(Input):
    revision: int
    changes: dict


class FileInput(Input):
    path:str
    content:str=Field(max_length=2*1024*1024)
    revision:str
    worktree_id:str|None=None


class TurnInput(Input):
    prompt: str=Field(min_length=1,max_length=64000)
    request_id: str=Field(min_length=8,max_length=128)
    limits: dict | None=None
    attachments: list[dict] | None=None


class ForkInput(Input):
    engine: str
    turn_ids: list[str]=Field(default_factory=list,max_length=10000)
    files: list[str]=Field(default_factory=list,max_length=20)
    summary: str=Field(default='',max_length=64000)


class ForkCommit(Input):
    digest: str
    model: str | None=None
    provider_id: str | None=None


class MemoryInput(Input):
    content: str=Field(min_length=1,max_length=64000)
    provenance: str=Field(min_length=1,max_length=1000)
    project_id: str | None=None
    retention_days: int=Field(default=30,ge=1,le=3650)
    excluded: bool=False
    identifier: str | None=None
    revision: int | None=None


class HookInput(Input):
    project_id: str
    event: str
    argv: list[str]=Field(min_length=1,max_length=32)
    cwd: str
    timeout_s: int=Field(default=10,ge=1,le=60)
    capabilities: list[str]=Field(default_factory=list)
    enabled: bool=False


class GoalInput(Input):
    conversation_id: str
    success_criteria: str
    max_runs: int=Field(ge=1,le=10000)
    limits: dict


class GrantInput(Input):
    goal_id: str
    expires_at: float
    max_runs: int=Field(ge=1,le=10000)
    limits: dict


class ScheduleInput(Input):
    goal_id: str
    grant_id: str
    prompt: str=Field(min_length=1,max_length=64000)
    spec: dict
    missed: str='skip'
    overlap: str='skip'
    enabled: bool=False


class EnableInput(Input):
    enabled: bool
    revision: int


class BundleInput(Input):
    data: dict
    format: str='termx-bundle/v1'


class DigestInput(Input):
    digest: str


class RegistryInput(Input):
    name: str=Field(min_length=1,max_length=200)
    path: str


class ApprovalInput(Input):
    decision: str
    remember: str | None = None
    limits: dict | None = None


class RetryInput(Input):
    request_id:str=Field(min_length=8,max_length=128)


class TaskMessage(Input):
    message: str=Field(min_length=1,max_length=64000)


def mount_workspace(app,state):
    if not getattr(state,'workspace',None):
        state.workspace=WorkspaceService(state)
    workspace=state.workspace
    workspace.principal_lookup=state.identity.principal_by_id
    if not getattr(state,'automation',None):
        state.automation=AutomationService(workspace,principal_lookup=state.identity.principal_by_id)
    if not getattr(state,'extensions',None):
        state.extensions=ExtensionService(workspace)
    automation=state.automation; extensions=state.extensions
    router=APIRouter(prefix='/api/workspace')

    def principal(request):
        raw=extract_passcode(authorization=request.headers.get('authorization')) or request.cookies.get(ACCESS_COOKIE)
        session=state.identity.resolve(raw)
        if not session:
            raise HTTPException(401,'Managed sign-in required')
        return session.principal

    def call(fn,*args,**kwargs):
        try:
            return fn(*args,**kwargs)
        except KeyError:
            raise HTTPException(404,'Resource not found') from None
        except PermissionError as exc:
            raise HTTPException(403,str(exc)) from None
        except Conflict as exc:
            raise HTTPException(409,str(exc)) from None
        except (ValueError,OSError) as exc:
            raise HTTPException(400,str(exc)) from None

    async def await_call(fn,*args,**kwargs):
        try:
            return await fn(*args,**kwargs)
        except KeyError:
            raise HTTPException(404,'Resource not found') from None
        except PermissionError as exc:
            raise HTTPException(403,str(exc)) from None
        except Conflict as exc:
            raise HTTPException(409,str(exc)) from None
        except (ValueError,OSError) as exc:
            raise HTTPException(400,str(exc)) from None

    @router.get('/sessions')
    def sessions(request:Request,query:str='',archived:bool=False,project_id:str|None=None):
        return {'sessions':call(workspace.sessions,principal(request),query=query,archived=archived,project_id=project_id)}

    @router.post('/sessions')
    def create_session(request:Request,body:SessionInput):
        return call(workspace.create_session,principal(request),**body.model_dump())

    @router.get('/sessions/{identifier}')
    def session(request:Request,identifier:str,turns:bool=True):
        return call(workspace.session,principal(request),identifier,turns=turns)

    @router.patch('/sessions/{identifier}')
    def update(request:Request,identifier:str,body:SessionUpdate):
        return call(workspace.update_session,principal(request),identifier,**body.model_dump())

    @router.get('/sessions/{identifier}/files')
    def files(request:Request,identifier:str,path:str='',operation:str='tree',query:str='',worktree_id:str|None=None):
        bound,pid=call(workspace.session_files,principal(request),identifier,expected_worktree=worktree_id)
        if operation=='tree':return call(bound.listing,pid,path)
        if operation=='read':return call(bound.read,pid,path)
        if operation=='search':return call(bound.search,pid,query)
        raise HTTPException(400,'Unknown file operation')

    @router.put('/sessions/{identifier}/files')
    def save_file(request:Request,identifier:str,body:FileInput):
        bound,pid=call(workspace.session_files,principal(request),identifier,'files-write',expected_worktree=body.worktree_id)
        return call(bound.save,pid,body.path,body.content,body.revision)

    @router.post('/sessions/{identifier}/terminal')
    def create_terminal(request:Request,identifier:str):
        actor=principal(request);row=call(workspace.record,actor,'conversation',identifier,scope='terminal-control')
        if row.get('runner_id'):raise HTTPException(409,'The selected runner uses its own bounded execution port')
        from termx.sessions import default_argv
        from termx.config import validate_shell
        shell=validate_shell(state.store.get().terminal.shell)
        item=state.sessions.create(cwd=row['cwd'],shell=shell,argv=default_argv(shell),title='Workspace checkout',sandbox_profile='workspace')
        state.authorization.claim_principal(actor,'terminal',item.id,project_id=row.get('project_id'))
        return item.snapshot()

    @router.post('/sessions/{identifier}/turns')
    async def send(request:Request,identifier:str,body:TurnInput):
        raw=extract_passcode(authorization=request.headers.get('authorization')) or request.cookies.get(ACCESS_COOKIE)
        origin=state.identity.resolve(raw)
        return await await_call(workspace.send,principal(request),identifier,managed_session_id=origin.session_id,**body.model_dump())

    @router.post('/sessions/{identifier}/fork-preview')
    def fork_preview(request:Request,identifier:str,body:ForkInput):
        return call(workspace.fork_preview,principal(request),identifier,**body.model_dump())

    @router.post('/fork-previews/{identifier}/commit')
    def commit(request:Request,identifier:str,body:ForkCommit):
        return call(workspace.commit_fork,principal(request),identifier,**body.model_dump())

    @router.get('/memory')
    def memories(request:Request,project_id:str|None=None):
        return {'memory':call(workspace.memories,principal(request),project_id=project_id)}

    @router.post('/memory')
    def save_memory(request:Request,body:MemoryInput):
        return call(workspace.save_memory,principal(request),**body.model_dump())

    @router.delete('/memory/{identifier}')
    def delete_memory(request:Request,identifier:str):
        call(workspace.delete_memory,principal(request),identifier)
        return {'deleted':True}

    @router.get('/memory/export')
    def memory_export(request:Request,project_id:str|None=None):
        return {'format':'termx-memory/v1','memory':call(workspace.memories,principal(request),project_id=project_id)}

    @router.get('/hooks')
    def hooks(request:Request,project_id:str|None=None):
        actor=principal(request)
        call(workspace.require,actor,'agent-view',project_id)
        return {'hooks':[row for row in workspace.store.list('hook',actor.id,project_id)
                         if row.get('project_id')==project_id]}

    @router.post('/hooks')
    def hook(request:Request,body:HookInput):
        return call(workspace.save_hook,principal(request),**body.model_dump())

    @router.get('/hook-runs')
    def hook_runs(request:Request,project_id:str|None=None):
        actor=principal(request)
        call(workspace.require,actor,'agent-view',project_id)
        return {'runs':[row for row in workspace.store.list('hook_run',actor.id,project_id)
                        if row.get('project_id')==project_id]}

    @router.patch('/hooks/{identifier}')
    def hook_enable(request:Request,identifier:str,body:EnableInput):
        actor=principal(request)
        row=call(workspace.record,actor,'hook',identifier,scope='agent-control')
        return call(workspace.store.update,'hook',identifier,{**row,'enabled':body.enabled},body.revision)

    @router.delete('/hooks/{identifier}')
    def hook_delete(request:Request,identifier:str):
        call(workspace.record,principal(request),'hook',identifier,scope='agent-control')
        workspace.store.delete('hook',identifier)
        return {'deleted':True}

    @router.post('/goals')
    def goal(request:Request,body:GoalInput):
        return call(automation.goal,principal(request),**body.model_dump())

    @router.post('/delegations')
    def grant(request:Request,body:GrantInput):
        return call(automation.grant,principal(request),**body.model_dump())

    @router.delete('/delegations/{identifier}')
    async def revoke(request:Request,identifier:str):
        actor=principal(request)
        call(automation.revoke,actor,identifier)
        await await_call(automation.cancel_delegation_runs,actor,identifier)
        return {'revoked':True}

    @router.post('/schedules/preview')
    def schedule_preview(request:Request,body:dict):
        actor=principal(request);call(workspace.require,actor,'agent-view')
        return {'next_runs':call(automation.preview,body)}

    @router.post('/schedules')
    def schedule(request:Request,body:ScheduleInput):
        return call(automation.schedule,principal(request),**body.model_dump())

    @router.patch('/schedules/{identifier}')
    def schedule_enable(request:Request,identifier:str,body:EnableInput):
        return call(automation.enable,principal(request),identifier,body.enabled,body.revision)

    @router.get('/automations')
    def automations(request:Request,project_id:str|None=None):
        actor=principal(request);call(workspace.require,actor,'agent-view',project_id)
        return {kind:[row for row in workspace.store.list(kind,actor.id,project_id)
                      if row.get('project_id')==project_id] for kind in ('goal','schedule','schedule_run','delegation')}

    @router.post('/goals/{identifier}/cancel')
    async def cancel_goal(request:Request,identifier:str):
        await await_call(automation.cancel_goal,principal(request),identifier)
        return {'cancelled':True}

    @router.get('/tasks/{identifier}')
    def task(request:Request,identifier:str):
        actor=principal(request);call(workspace.record,actor,'task',identifier)
        fields={'id','prompt','cwd','provider_id','model','status','limits','mode','parent_id','plan','result','error','engine','created_at','updated_at','execution_location'}
        remaining=[100]
        def tree(tid,depth=0):
            call(workspace.record,actor,'task',tid)
            value=state.agent_store.get_task(tid)
            result={key:value[key] for key in fields if key in value}
            metadata=workspace.store.get('task',tid) or {}
            result['execution_location']=metadata.get('execution_location') or value.get('cwd')
            result['runner_id']=metadata.get('runner_id')
            result['worktree']=state.agent_store.task_worktree(tid) or ({'worktree_id':metadata['worktree_id'],'worktree_path':metadata.get('cwd'),'branch':metadata.get('worktree_branch'),'source':'session'} if metadata.get('worktree_id') else None)
            result['children']=[]
            if depth<8:
                for child in state.agent_store.children(tid):
                    if remaining[0]<=0:break
                    remaining[0]-=1
                    result['children'].append(tree(child['id'],depth+1))
            return result
        return tree(identifier)

    @router.post('/tasks/{identifier}/cancel')
    async def cancel_task(request:Request,identifier:str):
        await await_call(automation.cancel_tree,principal(request),identifier)
        return {'cancelled':True}

    @router.post('/tasks/{identifier}/retry')
    async def retry_task(request:Request,identifier:str,body:RetryInput):
        raw=extract_passcode(authorization=request.headers.get('authorization')) or request.cookies.get(ACCESS_COOKIE)
        origin=state.identity.resolve(raw)
        return await await_call(workspace.retry,principal(request),identifier,request_id=body.request_id,
                                managed_session_id=origin.session_id)

    @router.post('/tasks/{identifier}/steer')
    async def steer(request:Request,identifier:str,body:TaskMessage):
        actor=principal(request);call(workspace.record,actor,'task',identifier,scope='agent-control')
        task=state.agent_store.get_task(identifier)
        if getattr(state,'runner_agents',None) and state.runner_agents.owns(task):
            return call(state.runner_agents.steer,identifier,body.message)
        if task.get('engine','internal')!='internal':
            return await await_call(state.engines.steer,identifier,body.message)
        return call(state.agent.steer,identifier,body.message)

    @router.post('/tasks/{identifier}/approvals/{approval_id}/resolve')
    async def approve(request:Request,identifier:str,approval_id:str,body:ApprovalInput):
        actor=principal(request);call(workspace.record,actor,'task',identifier,scope='agent-control')
        task=state.agent_store.get_task(identifier)
        if getattr(state,'runner_agents',None) and state.runner_agents.owns(task):
            return await await_call(state.runner_agents.resolve_approval,identifier,approval_id,body.decision,remember=body.remember,limits=body.limits)
        if task.get('engine','internal')!='internal':
            return await await_call(state.engines.resolve_approval,identifier,approval_id,body.decision,remember=body.remember)
        return await await_call(state.agent.resolve_approval,identifier,approval_id,body.decision,remember=body.remember,limits=body.limits)

    @router.get('/available-extensions')
    def available_extensions(request:Request,project_id:str|None=None):
        actor=principal(request);call(workspace.require,actor,'agent-view',project_id)
        return {'extensions':[{k:row[k] for k in ('id','version','permissions','description')} for row in workspace.store.list('extension') if row['enabled']]}

    @router.get('/extensions')
    def extension_list(request:Request):
        actor=principal(request);call(workspace.require,actor,'host-admin')
        return {'extensions':workspace.store.list('extension'), 'formats':sorted(extensions.adapters),
                'registries':workspace.store.list('registry',actor.id)}

    @router.post('/extensions/preview')
    def extension_preview(request:Request,body:BundleInput):
        return call(extensions.preview,principal(request),**body.model_dump())

    @router.post('/extension-previews/{identifier}/install')
    def install(request:Request,identifier:str,body:DigestInput):
        return call(extensions.install,principal(request),identifier,body.digest)

    @router.post('/extensions/{identifier}/rollback-preview')
    def rollback(request:Request,identifier:str,body:DigestInput):
        return call(extensions.rollback_preview,principal(request),identifier,body.digest)

    @router.get('/extensions/{identifier}/export')
    def export(request:Request,identifier:str):
        return call(extensions.export,principal(request),identifier)

    @router.delete('/extensions/{identifier}')
    def remove(request:Request,identifier:str):
        call(extensions.remove,principal(request),identifier)
        return {'removed':True}

    @router.post('/registries')
    def registry(request:Request,body:RegistryInput):
        return call(extensions.registry,principal(request),**body.model_dump())

    @router.get('/registries/{identifier}/packages')
    def packages(request:Request,identifier:str):
        return {'packages':call(extensions.registry_packages,principal(request),identifier)}

    @router.post('/registries/{identifier}/preview')
    def registry_preview(request:Request,identifier:str,body:dict):
        return call(extensions.registry_preview,principal(request),identifier,body.get('filename',''))

    @router.get('/audit')
    def audit(request:Request):
        actor=principal(request)
        return {'events':workspace.store.logs(actor.id)}

    app.include_router(router)
    return workspace
