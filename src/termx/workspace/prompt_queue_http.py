"""Managed-session prompt queue routes; guarded by the same HTTP/CSRF facade."""
from fastapi import APIRouter,HTTPException,Request
from pydantic import BaseModel,ConfigDict,Field
from termx.auth import extract_passcode
from termx.identity_http import ACCESS_COOKIE
from termx.workspace.prompt_queue import PromptQueue
from termx.workspace.store import Conflict

class QueueInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    prompt:str=Field(min_length=1,max_length=64000)
    request_id:str=Field(min_length=8,max_length=128)
    attachments:list[dict]|None=None
    context:list[dict]|None=None
    limits:dict|None=None
    interrupt_task_id:str|None=Field(default=None,max_length=128)

class QueueRevision(BaseModel):
    model_config=ConfigDict(extra='forbid')
    revision:int=Field(ge=1)

class QueueRenew(QueueRevision):
    target_digest:str=Field(pattern='^[0-9a-f]{64}$')


def mount_prompt_queue(app,state):
    if not getattr(state,'prompt_queue',None):state.prompt_queue=PromptQueue(state.workspace)
    service=state.prompt_queue
    router=APIRouter(prefix='/api/workspace')
    def identity(request):
        raw=extract_passcode(authorization=request.headers.get('authorization')) or request.cookies.get(ACCESS_COOKIE)
        current=state.identity.resolve(raw)
        if not current:raise HTTPException(401,'Managed sign-in required')
        return current
    def call(fn,*args,**kwargs):
        try:return fn(*args,**kwargs)
        except Conflict as exc:raise HTTPException(409,str(exc)) from None
        except KeyError:raise HTTPException(404,'Queued follow-up is unavailable') from None
        except PermissionError as exc:raise HTTPException(403,str(exc)) from None
        except ValueError as exc:raise HTTPException(400,str(exc)) from None
    @router.get('/sessions/{identifier}/queue')
    def queued(request:Request,identifier:str):
        return {'items':call(service.list,identity(request).principal,identifier)}
    @router.post('/sessions/{identifier}/queue')
    async def enqueue(request:Request,identifier:str,body:QueueInput):
        actor=identity(request)
        result=call(service.enqueue,actor.principal,identifier,sid=actor.session_id,**body.model_dump())
        if body.interrupt_task_id and result['status']=='queued':
            try:await state.automation.cancel_tree(actor.principal,body.interrupt_task_id)
            except Exception:
                row=service.store.get('prompt_queue',result['id'])
                if row and row['status']=='queued':
                    result=service.public(service.store.update('prompt_queue',row['id'],{**row,'status':'blocked','reason':'Current task could not be stopped. Inspect it, then explicitly review this queued follow-up.'},row['revision']))
        return result
    @router.delete('/queue/{identifier}')
    def cancel(request:Request,identifier:str,body:QueueRevision):
        return call(service.cancel,identity(request).principal,identifier,body.revision)
    @router.get('/queue/{identifier}/review')
    def review(request:Request,identifier:str):
        return call(service.current,identity(request).principal,identifier)
    @router.post('/queue/{identifier}/renew')
    def renew(request:Request,identifier:str,body:QueueRenew):
        actor=identity(request)
        return call(service.renew,actor.principal,identifier,sid=actor.session_id,**body.model_dump())
    app.include_router(router)
