"""Authenticated browser workspace routes; human and task tools are distinct."""
from __future__ import annotations
import asyncio
import base64
import hashlib
from pathlib import Path
from time import time
from typing import Literal
from fastapi import APIRouter,Request,WebSocket,WebSocketDisconnect,HTTPException
from fastapi.routing import APIRoute
from fastapi.responses import Response,FileResponse,JSONResponse
from pydantic import BaseModel,ConfigDict,Field
from termx.auth import extract_passcode
from termx.auto_review import ReviewRequired,ActionBlocked,ResponsesReviewer,ActionEnvelope
from termx.browser.network import origin
from termx.browser.evaluation import evaluate_reviewer

class Route(APIRoute):
    def get_route_handler(self):
        handler=super().get_route_handler()
        async def safe(request):
            try:return await handler(request)
            except ActionBlocked as exc:return JSONResponse({'error':'blocked','review':exc.record},status_code=403)
            except ReviewRequired as exc:return JSONResponse({'error':'review_required','review':exc.record},status_code=409)
            except KeyError:return JSONResponse({'error':'resource not found'},status_code=404)
            except PermissionError as exc:return JSONResponse({'error':str(exc)},status_code=403)
            except ValueError as exc:return JSONResponse({'error':str(exc)},status_code=400)
        return safe
class Body(BaseModel):model_config=ConfigDict(extra='forbid')
class ProfileBody(Body):
    project_id:str='';name:str=Field(min_length=1,max_length=100);ephemeral:bool=False
class TabBody(Body):profile_id:str;url:str='about:blank'
class HandoffBody(Body):
    run_id:str;origins:list[str];actions:list[str];expires_in:int=600
class TakeoverBody(Body):private:bool=False
class HumanBody(Body):action:str;args:dict=Field(default_factory=dict)
class AgentBody(Body):
    action_id:str;grant_id:str;run_id:str;action:str;args:dict=Field(default_factory=dict);document_revision:int;lease_revision:int
class AnnotationBody(Body):
    revision:int;comment:str;selector:str|None=None;region:dict|None=None
class RecordingBody(Body):enabled:bool
class DraftBody(Body):name:str
class DraftEditBody(Body):content:str=Field(max_length=100_000)
class DraftTestBody(Body):target_tab_id:str;parameters:dict[str,str]=Field(default_factory=dict);safe_target:bool=False
class DraftPublishBody(Body):version:str
class UploadBody(Body):filename:str;mime_type:str='application/octet-stream';data_base64:str
class DecisionBody(Body):approve:bool
class ReviewConfigBody(Body):provider_id:str;model:str;version:str
class RuleBody(Body):action_id:str;decision:Literal['ALLOW','BLOCK'];expires_in:int=3600


def browser_router(state):
    router=APIRouter(prefix='/api/browser',route_class=Route)
    service=state.browser
    from termx.browser.skills import BrowserSkills
    skills=BrowserSkills(service)
    def current_reviewer():
        config=service.records.get('reviewer-config','active')
        if not config:return None
        provider=state.agent_store.get_provider(config['provider_id'])
        report=service.records.get('reviewer-evaluation',config['evaluation_id'])
        digest=hashlib.sha256((provider['base_url']+config['model']+config['version']).encode()).hexdigest() if provider else ''
        if not provider or provider['model']!=config['model'] or not report or not report['qualified'] or report['configuration_hash']!=digest or time()-report['created_at']>30*86400:return None
        return provider,config
    service.review.reviewer_valid=lambda:current_reviewer() is not None
    persisted=current_reviewer()
    if persisted:
        provider,config=persisted
        service.review.reviewer=ResponsesReviewer(provider_id=config['provider_id'],base_url=provider['base_url'],model=config['model'],api_key=state.credentials.get(config['provider_id']) or '',version=config['version'])
    def identity(request,scope='desktop-view',kind=None,id=None,project=None):
        token=extract_passcode(request.headers.get('x-termx-passcode'),request.headers.get('authorization'))
        if not state.auth.allows(token,scope):raise HTTPException(403,'missing browser authority')
        session=state.identity.resolve(token)
        if session:
            principal=session.principal.id;session_id=session.session_id
        else:
            # Transitional paired users remain separated even before owner setup.
            principal='legacy-'+hashlib.sha256((token or 'trusted-loopback').encode()).hexdigest()[:24]
            session_id=principal
        if getattr(state,'authorization',None):
            state.authorization.require(token,scope,project_id=project or None,resource_kind=kind,resource_id=id)
        return token,principal,session_id
    def tab_identity(request,id,scope='desktop-view'):
        result=identity(request,scope)
        tab=service.get(id,result[1])
        identity(request,scope,'browser-tab',id,tab['project_id'])
        return result,tab
    def revision(token):
        authorization=getattr(state,'authorization',None)
        if authorization:return authorization.revision(token)
        resolved=state.identity.resolve(token);return resolved.principal.policy_version if resolved else 0
    def claim(token,kind,id,project):
        if getattr(state,'authorization',None):state.authorization.claim(token,kind,id,project_id=project or None)
    @router.get('/status')
    async def status(request:Request):
        identity(request)
        return {'renderer':'managed-chromium-stream','interactive':True,'profile_isolation':'separate Chromium profile directories; same trusted OS account','external_tabs':False,'raw_cdp_exposed':False,'reviewer_configured':service.review.reviewer is not None,'development_origins':sorted(service.network.development_origins)}
    @router.get('/profiles')
    async def profiles(request:Request):return service.profiles(identity(request)[1])
    @router.post('/profiles')
    async def create_profile(request:Request,body:ProfileBody):
        token,principal,_=identity(request,'desktop-control',project=body.project_id)
        value=service.create_profile(principal,body.project_id,body.name,ephemeral=body.ephemeral)
        claim(token,'browser-profile',value['id'],body.project_id);return value
    @router.delete('/profiles/{id}')
    async def delete_profile(id:str,request:Request):
        _,principal,_=identity(request,'desktop-control','browser-profile',id)
        return await service.clear_profile(id,principal,remove=True)
    @router.post('/profiles/{id}/clear')
    async def clear_profile(id:str,request:Request):
        _,principal,_=identity(request,'desktop-control','browser-profile',id)
        return await service.clear_profile(id,principal)
    @router.get('/tabs')
    async def tabs(request:Request):return service.tabs(identity(request)[1])
    @router.post('/tabs')
    async def create_tab(request:Request,body:TabBody):
        token,principal,session=identity(request,'desktop-control','browser-profile',body.profile_id)
        profile=service.records.get('profile',body.profile_id)
        if not profile or profile['principal_id']!=principal:raise KeyError('profile')
        identity(request,'desktop-control',project=profile['project_id'])
        value=await service.create_tab(principal,session,body.profile_id,body.url)
        claim(token,'browser-tab',value['id'],value['project_id']);return value
    @router.delete('/tabs/{id}')
    async def close_tab(id:str,request:Request):
        (_,principal,_),_=tab_identity(request,id,'desktop-control');await service.close_tab(id,principal);return {'ok':True}
    @router.post('/tabs/{id}/handoff')
    async def handoff(id:str,request:Request,body:HandoffBody):
        (token,principal,session),tab=tab_identity(request,id,'desktop-control')
        # A handoff is an explicit named existing task owned by the connected user.
        task=state.agent_store.get_task(body.run_id)
        if not task:raise ValueError('select an existing task for tab handoff')
        if task.get('engine','internal')!='internal':
            binding=state.engines._binding_for_task(body.run_id)
            if task.get('engine')!='claude' or not binding or binding.extensions_snapshot.get('workflow')!='browser':
                raise ValueError('This native session cannot enforce broker-only tools. Create a Claude browser workflow conversation, or choose Internal.')
        if getattr(state,'authorization',None):state.authorization.require(token,'agent-control',resource_kind='task',resource_id=body.run_id,project_id=tab['project_id'] or None)
        return await service.handoff(id,principal,session,run_id=body.run_id,origins=body.origins,actions=body.actions,expires_in=body.expires_in,policy_version=revision(token))
    @router.post('/tabs/{id}/takeover')
    async def takeover(id:str,request:Request,body:TakeoverBody):
        (_,principal,_),_=tab_identity(request,id,'desktop-control');return service.takeover(id,principal,private=body.private)
    @router.post('/tabs/{id}/human')
    async def human(id:str,request:Request,body:HumanBody):
        (_,principal,_),_=tab_identity(request,id,'desktop-control');return await service.human_action(id,principal,body.action,body.args)
    @router.get('/tabs/{id}/frame')
    async def frame(id:str,request:Request):
        (_,principal,_),_=tab_identity(request,id);return Response(await service.frame(id,principal,human=True),media_type='image/jpeg',headers={'Cache-Control':'no-store'})
    @router.get('/tabs/{id}/context')
    async def context(id:str,request:Request):
        (_,principal,_),tab=tab_identity(request,id)
        if tab['state']=='private':raise PermissionError('context attachment paused for private login')
        return await service.observe(id,principal,human=True)
    @router.post('/tabs/{id}/agent')
    async def agent(id:str,request:Request,body:AgentBody):
        (token,principal,session),tab=tab_identity(request,id,'desktop-control')
        def authority():
            return state.auth.allows(token,'desktop-control') and revision(token)==service.records.get('grant',body.grant_id)['policy_version']
        return await service.action(id,principal,session_id=session,policy_version=revision(token),authority=authority,**body.model_dump())
    @router.post('/tabs/{id}/annotations')
    async def annotations(id:str,request:Request,body:AnnotationBody):
        (_,principal,_),_=tab_identity(request,id);return service.annotate(id,principal,**body.model_dump())
    @router.get('/tabs/{id}/annotations')
    async def annotation_list(id:str,request:Request):
        (_,principal,_),tab=tab_identity(request,id)
        return [{**a,'stale':a['document_revision']!=tab['document_revision']} for a in service.records.list('annotation') if a['tab_id']==id and a['principal_id']==principal]
    @router.post('/tabs/{id}/recording')
    async def recording(id:str,request:Request,body:RecordingBody):
        (_,principal,_),_=tab_identity(request,id,'desktop-control');return service.recording(id,principal,body.enabled)
    @router.post('/tabs/{id}/skill-draft')
    async def draft(id:str,request:Request,body:DraftBody):
        (_,principal,_),_=tab_identity(request,id,'desktop-control');return service.skill_draft(id,principal,body.name)
    @router.patch('/skill-drafts/{id}')
    async def edit_draft(id:str,request:Request,body:DraftEditBody):
        _,principal,_=identity(request,'desktop-control');return skills.edit(id,principal,body.content)
    @router.post('/skill-drafts/{id}/test')
    async def test_draft(id:str,request:Request,body:DraftTestBody):
        (token,principal,_),_=tab_identity(request,body.target_tab_id,'desktop-control')
        policy=revision(token)
        return await skills.test(id,principal,**body.model_dump(),authority=lambda:state.auth.allows(token,'desktop-control') and revision(token)==policy)
    @router.post('/skill-drafts/{id}/publish')
    async def publish_draft(id:str,request:Request,body:DraftPublishBody):
        token,principal,_=identity(request,'host-admin')
        managed=state.identity.resolve(token)
        if not managed:raise PermissionError('Skill publishing requires a managed owner session')
        return skills.publish(id,principal,managed.principal,state.extensions,body.version)
    @router.post('/tabs/{id}/uploads')
    async def upload(id:str,request:Request,body:UploadBody):
        (_,principal,_),_=tab_identity(request,id,'desktop-control')
        if len(body.data_base64)>14*1024*1024:raise ValueError('upload exceeds limit')
        return service.approve_upload(id,principal,body.filename,body.mime_type,base64.b64decode(body.data_base64,validate=True))
    @router.get('/downloads')
    async def downloads(request:Request):
        _,principal,_=identity(request);return [r for r in service.records.list('download') if r['principal_id']==principal]
    @router.get('/downloads/{id}')
    async def file(id:str,request:Request):
        _,principal,_=identity(request)
        ref=service.records.get('download',id)
        if not ref or ref['principal_id']!=principal or not ref['approved']:raise PermissionError('download needs exact approval')
        return FileResponse(service.records.root/'downloads'/id,filename=ref['filename'],media_type='application/octet-stream')
    @router.get('/history')
    async def history(request:Request):
        _,principal,_=identity(request);return [r for r in service.records.list('history') if r['principal_id']==principal]
    @router.get('/reviews')
    async def reviews(request:Request):return service.review.history(identity(request)[1])
    @router.get('/reviewer')
    async def reviewer_status(request:Request):
        identity(request)
        return {'configuration':service.records.get('reviewer-config','active'),'evaluations':service.records.list('reviewer-evaluation'),'active':service.review.reviewer is not None}
    @router.post('/reviewer/evaluate')
    async def reviewer_evaluate(request:Request,body:ReviewConfigBody):
        identity(request,'host-admin')
        provider=state.agent_store.get_provider(body.provider_id)
        if not provider or provider['kind'] not in {'openai','openai-compatible'} or body.model!=provider['model']:
            raise ValueError('select an existing configured provider model; no billing fallback')
        key=state.credentials.get(body.provider_id) or ''
        if not key and not provider['base_url'].startswith(('http://localhost','http://127.0.0.1')):
            raise ValueError('configured provider account required')
        reviewer=ResponsesReviewer(provider_id=body.provider_id,base_url=provider['base_url'],model=body.model,api_key=key,version=body.version)
        report=await evaluate_reviewer(reviewer)
        ref=body.provider_id+':'+body.model+':'+body.version
        service.records.put('reviewer-evaluation',ref,{'id':ref,**report,'configuration_hash':hashlib.sha256((provider['base_url']+body.model+body.version).encode()).hexdigest()})
        return report
    @router.post('/reviewer')
    async def configure_reviewer(request:Request,body:ReviewConfigBody):
        identity(request,'host-admin')
        provider=state.agent_store.get_provider(body.provider_id)
        report=service.records.get('reviewer-evaluation',body.provider_id+':'+body.model+':'+body.version)
        digest=hashlib.sha256((provider['base_url']+body.model+body.version).encode()).hexdigest() if provider else ''
        if not report or not report['qualified'] or report['configuration_hash']!=digest or time()-report['created_at']>30*86400:
            raise ValueError('current provider/model/version must pass qualification first')
        key=state.credentials.get(body.provider_id) or ''
        service.review.reviewer=ResponsesReviewer(provider_id=body.provider_id,base_url=provider['base_url'],model=body.model,api_key=key,version=body.version)
        config={'id':'active',**body.model_dump(),'evaluation_id':report['id'],'configuration_hash':digest,'activated_at':time()}
        service.records.put('reviewer-config','active',config);return config
    @router.delete('/reviewer')
    async def disable_reviewer(request:Request):
        identity(request,'host-admin');service.review.reviewer=None;service.records.delete('reviewer-config','active');return {'ok':True}
    @router.post('/reviews/{id}/decision')
    async def decision(id:str,request:Request,body:DecisionBody):
        _,principal,_=identity(request,'desktop-control')
        record=service.records.get('review',id)
        if not record or record['principal_id']!=principal:raise KeyError('review')
        for approval in state.agent_store.approvals(record['run_id']):
            if approval['status']=='pending' and ((approval['payload'].get('request') or {}).get('browser_review') or {}).get('id')==id:
                return await state.engines.resolve_approval(record['run_id'],approval['id'],'approved' if body.approve else 'denied')
            if approval['status']=='pending' and (approval['payload'].get('browser_review') or {}).get('id')==id:
                return await state.agent.resolve_approval(record['run_id'],approval['id'],'approved' if body.approve else 'denied')
        return service.review.decide(id,principal_id=principal,approve=body.approve)
    @router.get('/rules')
    async def rules(request:Request):
        _,principal,_=identity(request);return [r for r in service.records.list('review-rule') if r['scope']['principal_id']==principal]
    @router.post('/rules')
    async def create_rule(request:Request,body:RuleBody):
        _,principal,_=identity(request,'desktop-control')
        record=service.records.get('review',body.action_id)
        if not record or record['principal_id']!=principal:raise KeyError('action')
        envelope=ActionEnvelope(**record['envelope'])
        return service.review.rule(envelope,body.decision,expires_at=time()+body.expires_in)
    @router.delete('/rules/{id}')
    async def revoke_rule(id:str,request:Request):
        _,principal,_=identity(request,'desktop-control');rule=service.records.get('review-rule',id)
        if not rule or rule['scope']['principal_id']!=principal:raise KeyError('rule')
        service.review.revoke_rule(id);return {'ok':True}
    @router.get('/audit')
    async def audit(request:Request):
        _,principal,_=identity(request);return [r for r in service.records.events() if r.get('principal_id')==principal]
    @router.websocket('/tabs/{id}/view')
    async def interactive(websocket:WebSocket,id:str):
        try:
            _,principal,_=identity(websocket,'desktop-control','browser-tab',id)
            service.get(id,principal)
            await websocket.accept()
            async def frames():
                while True:
                    identity(websocket,'desktop-control','browser-tab',id)
                    data=await service.frame(id,principal,human=True)
                    await websocket.send_bytes(data)
                    tab=service.get(id,principal)
                    await websocket.send_json({'type':'state','tab':tab})
                    await asyncio.sleep(.125)
            async def input():
                while True:
                    payload=await websocket.receive_json()
                    identity(websocket,'desktop-control','browser-tab',id)
                    body=HumanBody.model_validate(payload)
                    await service.human_action(id,principal,body.action,body.args)
            tasks=[asyncio.create_task(frames()),asyncio.create_task(input())]
            try:
                done,_=await asyncio.wait(tasks,return_when=asyncio.FIRST_COMPLETED)
                for task in done:task.result()
            finally:
                for task in tasks:task.cancel()
                await asyncio.gather(*tasks,return_exceptions=True)
        except (WebSocketDisconnect,asyncio.CancelledError):pass
        except Exception:
            try:await websocket.close(code=4403)
            except Exception:pass
    return router
