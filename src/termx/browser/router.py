"""Authenticated browser workspace routes; human and task tools are distinct."""
from __future__ import annotations
import asyncio
import base64
import hashlib
import secrets
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
from termx.browser.pricing import PricingSnapshot
from termx.browser.reviewer_binding import account_revision

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
    revision:int;comment:str=Field(min_length=1,max_length=2000);selector:str|None=None;region:dict|None=None;context_hash:str|None=None;include_screenshot:bool=False
class AnnotationCompareBody(Body):revision:int;include_screenshot:bool=False
class AnnotationResolveBody(Body):revision:int;resolved:bool
class RecordingBody(Body):enabled:bool
class DraftBody(Body):name:str
class DraftEditBody(Body):content:str=Field(max_length=100_000)
class DraftTestBody(Body):target_tab_id:str;parameters:dict[str,str]=Field(default_factory=dict);safe_target:bool=False
class DraftPublishBody(Body):version:str
class UploadBody(Body):filename:str;mime_type:str='application/octet-stream';data_base64:str
class DecisionBody(Body):approve:bool
class ReviewConfigBody(Body):
    provider_id:str=Field(min_length=1,max_length=200)
    model:str=Field(min_length=1,max_length=200)
    version:str=Field(min_length=1,max_length=120,pattern=r'\S')
class ReviewEvaluationBody(ReviewConfigBody):pricing:PricingSnapshot|None=None
class RuleBody(Body):action_id:str;decision:Literal['ALLOW','BLOCK'];expires_in:int=Field(default=3600,ge=1,le=30*86400)
class RuleEditBody(Body):decision:Literal['ALLOW','BLOCK'];expires_in:int=Field(ge=1,le=30*86400);revision:int=Field(ge=1)


def browser_router(state):
    router=APIRouter(prefix='/api/browser',route_class=Route)
    service=state.browser
    def claim_agent_tab(principal,tab):
        live=state.identity.principal_by_id(principal)
        if not live:raise PermissionError('Principal disabled during tab creation')
        state.authorization.claim_principal(live,'browser-tab',tab['id'],tab['project_id'] or None)
    service.claim_tab=claim_agent_tab
    from termx.browser.skills import BrowserSkills
    skills=BrowserSkills(service)
    def binding(provider):
        return account_revision(service.records,provider,state.credentials.get(provider['id']) or '')
    def eligible(report,provider,model,version,revision=None):
        digest=hashlib.sha256((provider['base_url']+model+version).encode()).hexdigest() if provider else ''
        return bool(provider and provider['model']==model and report and report['qualified'] and report.get('model_identity',{}).get('pinned')
                    and report.get('configuration_hash')==digest and report.get('account_revision')==(revision or binding(provider))
                    and time()-report['created_at']<=30*86400)
    def current_reviewer():
        config=service.records.get('reviewer-config','active')
        if not config:return None
        provider=state.agent_store.get_provider(config['provider_id'])
        report=service.records.get('reviewer-evaluation',config['evaluation_id'])
        if not eligible(report,provider,config['model'],config['version']) or config.get('account_revision')!=report.get('account_revision'):return None
        return provider,config
    service.review.reviewer_valid=lambda:current_reviewer() is not None
    persisted=current_reviewer()
    if persisted:
        provider,config=persisted
        service.review.reviewer=ResponsesReviewer(provider_id=config['provider_id'],base_url=provider['base_url'],model=config['model'],api_key=state.credentials.get(config['provider_id']) or '',version=config['version'],expected_model=config['qualified_model'])
        service.review.reviewer.account_revision=config['account_revision']
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
    @router.post('/tabs/{id}/recover')
    async def recover_tab(id:str,request:Request):
        (token,principal,session),tab=tab_identity(request,id,'desktop-control')
        identity(request,'desktop-control','browser-profile',tab['profile_id'],tab['project_id'])
        value=await service.recover_tab(id,principal,session)
        claim(token,'browser-tab',value['id'],value['project_id']);return value
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
        (token,principal,session),_=tab_identity(request,id,'desktop-control');return service.takeover(id,principal,private=body.private,session_id=session,policy_version=revision(token))
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
        (token,principal,_),_=tab_identity(request,id,'desktop-control')
        policy_version=revision(token)
        def authority():
            try:
                tab_identity(request,id,'desktop-control')
                return revision(token)==policy_version
            except (HTTPException,PermissionError,KeyError):return False
        return await service.annotate_context(id,principal,authority=authority,**body.model_dump())
    @router.get('/tabs/{id}/annotations')
    async def annotation_list(id:str,request:Request):
        (_,principal,_),tab=tab_identity(request,id)
        return service.annotations(id,principal)
    def annotation_authority(request,id):
        (token,principal,_),_=tab_identity(request,id,'desktop-control');version=revision(token)
        def current():
            try:
                tab_identity(request,id,'desktop-control')
                return revision(token)==version
            except (HTTPException,PermissionError,KeyError):return False
        return principal,current
    @router.post('/tabs/{id}/annotations/{ref}/compare')
    async def annotation_compare(id:str,ref:str,request:Request,body:AnnotationCompareBody):
        principal,authority=annotation_authority(request,id)
        return await service.compare_annotation(id,principal,ref,authority=authority,**body.model_dump())
    @router.post('/tabs/{id}/annotations/{ref}/resolve')
    async def annotation_resolve(id:str,ref:str,request:Request,body:AnnotationResolveBody):
        principal,authority=annotation_authority(request,id)
        return service.resolve_annotation(id,principal,ref,authority=authority,**body.model_dump())
    @router.get('/tabs/{id}/annotation-frames/{ref}')
    async def annotation_frame(id:str,ref:str,request:Request):
        (_,principal,_),tab=tab_identity(request,id)
        if tab['state']=='private':raise PermissionError('Snapshot preview paused during private login')
        value=service.records.get('annotation-frame',ref)
        if not value or value['principal_id']!=principal or value['tab_id']!=id:raise KeyError('frame')
        return FileResponse(service.records.root/'annotation-frames'/value['id'],media_type='image/jpeg',headers={'Cache-Control':'no-store'})
    @router.post('/tabs/{id}/diagnostics')
    async def enable_diagnostics(id:str,request:Request,body:RecordingBody):
        (token,principal,session),_=tab_identity(request,id,'desktop-control')
        return service.diagnostic_consent(id,principal,session,revision(token),body.enabled)
    @router.get('/tabs/{id}/diagnostics/{view}')
    async def diagnostics(id:str,view:str,request:Request):
        (_,principal,_),_=tab_identity(request,id)
        return await service.human_diagnostics(id,principal,view)
    @router.delete('/profiles/{id}/history')
    async def clear_history(id:str,request:Request):
        _,principal,_=identity(request,'desktop-control','browser-profile',id)
        profile=service.records.get('profile',id)
        if not profile or profile['principal_id']!=principal:raise KeyError('profile')
        identity(request,'desktop-control',project=profile['project_id'])
        for item in service.records.list('history'):
            if item['profile_id']==id and item['principal_id']==principal:service.records.delete('history',item['id'])
        return {'ok':True}
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
        def status():
            reports=[];providers={};revisions={}
            for report in service.records.list('reviewer-evaluation'):
                identifier=report['provider_id']
                if identifier not in providers:
                    providers[identifier]=state.agent_store.get_provider(identifier)
                    revisions[identifier]=binding(providers[identifier]) if providers[identifier] else ''
                provider=providers[identifier]
                reports.append({**report,'activation_eligible':eligible(report,provider,report['model'],report['reviewer_version'],revisions[identifier])})
            return {'configuration':service.records.get('reviewer-config','active'),'evaluations':reports,'active':service.review.reviewer is not None and current_reviewer() is not None}
        value=await asyncio.to_thread(status)
        identity(request)
        return value
    @router.get('/reviewer/evaluations/{identifier}/export')
    async def reviewer_export(identifier:str,request:Request):
        identity(request)
        report=service.records.get('reviewer-evaluation',identifier)
        if not report:raise KeyError(identifier)
        provider=state.agent_store.get_provider(report['provider_id'])
        report={**report,'activation_eligible':eligible(report,provider,report['model'],report['reviewer_version'])}
        return JSONResponse(report,headers={'Content-Disposition':'attachment; filename="termx-reviewer-qualification.json"','Cache-Control':'no-store'})
    @router.post('/reviewer/evaluate')
    async def reviewer_evaluate(request:Request,body:ReviewEvaluationBody):
        identity(request,'host-admin')
        provider=state.agent_store.get_provider(body.provider_id)
        if not provider or provider['kind'] not in {'openai','openai-compatible'} or body.model!=provider['model']:
            raise ValueError('select an existing configured provider model; no billing fallback')
        key=state.credentials.get(body.provider_id) or ''
        if not key and not provider['base_url'].startswith(('http://localhost','http://127.0.0.1')):
            raise ValueError('configured provider account required')
        reviewer=ResponsesReviewer(provider_id=body.provider_id,base_url=provider['base_url'],model=body.model,api_key=key,version=body.version)
        revision=binding(provider)
        report=await evaluate_reviewer(reviewer,body.pricing)
        identity(request,'host-admin')
        ref=secrets.token_urlsafe(24)
        current=state.agent_store.get_provider(body.provider_id)
        report=service.records.put('reviewer-evaluation',ref,{'id':ref,**report,'account_revision':revision,'configuration_hash':hashlib.sha256((provider['base_url']+body.model+body.version).encode()).hexdigest(),
            'activation_eligible_at_evaluation':eligible({**report,'account_revision':revision,'configuration_hash':hashlib.sha256((provider['base_url']+body.model+body.version).encode()).hexdigest()},current,body.model,body.version)})
        return report
    @router.post('/reviewer')
    async def configure_reviewer(request:Request,body:ReviewConfigBody):
        identity(request,'host-admin')
        provider=state.agent_store.get_provider(body.provider_id)
        matches=[report for report in service.records.list('reviewer-evaluation') if report['provider_id']==body.provider_id and report['model']==body.model and report['reviewer_version']==body.version and eligible(report,provider,body.model,body.version)]
        report=max(matches,key=lambda row:row['created_at']) if matches else None
        if not report:
            raise ValueError('current provider/model/version must pass qualification first')
        key=state.credentials.get(body.provider_id) or ''
        service.review.reviewer=ResponsesReviewer(provider_id=body.provider_id,base_url=provider['base_url'],model=body.model,api_key=key,version=body.version,expected_model=report['model_identity']['qualified_model'])
        service.review.reviewer.account_revision=report['account_revision']
        config={'id':'active','qualified_model':report['model_identity']['qualified_model'],**body.model_dump(),'evaluation_id':report['id'],'configuration_hash':report['configuration_hash'],'account_revision':report['account_revision'],'activated_at':time()}
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
    @router.patch('/rules/{id}')
    async def edit_rule(id:str,request:Request,body:RuleEditBody):
        token,principal,session=identity(request,'desktop-control')
        row=service.records.get('review-rule',id)
        if not row or row['scope']['principal_id']!=principal:raise KeyError('rule')
        def current(scope):
            if scope['principal_id']!=principal or scope['session_id']!=session or revision(token)!=scope['policy_version'] or not service.session_valid(principal,session,scope['policy_version']) or not service.task_live(scope['run_id']):return False
            identity(request,'desktop-control',project=scope['project_id'])
            if scope['grant_id'].startswith('task:'):
                authority=service.records.get('agent-task-authority',scope['run_id'])
                return bool(scope['grant_id']=='task:'+scope['run_id'] and authority and all(authority[key]==scope[key] for key in ('principal_id','session_id','project_id','policy_version')))
            grant=service.records.get('grant',scope['grant_id'])
            tab=service.records.get('tab',grant['tab_id']) if grant else None
            if not tab or not service._grant_valid(tab,grant):return False
            identity(request,'desktop-control','browser-tab',tab['id'],scope['project_id'])
            return bool(all(grant[key]==scope[key] for key in ('principal_id','session_id','project_id','run_id','profile_id','policy_version')) and scope['target'] in grant['origins'] and scope['tool_id'].removeprefix('browser.') in grant['actions'])
        return service.review.edit_rule(id,body.decision,expires_at=time()+body.expires_in,revision=body.revision,validate=current)
    @router.delete('/rules/{id}')
    async def revoke_rule(id:str,request:Request):
        _,principal,_=identity(request,'desktop-control');rule=service.records.get('review-rule',id)
        if not rule or rule['scope']['principal_id']!=principal:raise KeyError('rule')
        service.review.revoke_rule(id);return {'ok':True}
    @router.get('/audit')
    async def audit(request:Request):
        _,principal,_=identity(request);return [r for r in service.records.events() if r.get('principal_id')==principal]
    @router.post('/tabs/{id}/view/stop')
    async def stop_view(id:str,request:Request):
        _,principal,_=identity(request,'desktop-view','browser-tab',id)
        return await service.stop_views(id,principal)
    @router.post('/tabs/{id}/capture/stop')
    async def stop_tab_capture(id:str,request:Request):
        _,principal,_=identity(request,'desktop-view','browser-tab',id)
        tab=service.get(id,principal)
        if tab['state']=='agent' or tab['recording'] or service._diagnostic_consent_valid(tab):
            identity(request,'desktop-control','browser-tab',id)
            if tab['state']=='agent':service.takeover(id,principal)
            tab=service.get(id,principal);tab['recording']=False;service.records.put('tab',id,tab)
            service._human_diagnostics.pop(id,None);service._diagnostics.pop(id,None)
        return await service.stop_views(id,principal)
    @router.websocket('/tabs/{id}/view')
    async def interactive(websocket:WebSocket,id:str):
        viewer=None
        try:
            token,principal,session_id=await asyncio.to_thread(identity,websocket,'desktop-view','browser-tab',id)
            service.get(id,principal)
            await websocket.accept();viewer=service.viewer_start(id,principal,session_id,websocket)
            async def frames():
                while True:
                    await asyncio.to_thread(identity,websocket,'desktop-view','browser-tab',id)
                    try:data=await service.frame(id,principal,human=True)
                    except PermissionError:
                        await asyncio.sleep(.01);continue
                    await asyncio.to_thread(identity,websocket,'desktop-view','browser-tab',id)
                    await websocket.send_bytes(data)
                    tab=service.get(id,principal)
                    control=await asyncio.to_thread(state.authorization.can,token,'desktop-control',project_id=tab['project_id'] or None,resource_kind='browser-tab',resource_id=id)
                    await websocket.send_json({'type':'state','tab':tab,'can_control':control})
                    await asyncio.sleep(.125)
            async def input():
                while True:
                    payload=await websocket.receive_json()
                    try:
                        await asyncio.to_thread(identity,websocket,'desktop-control','browser-tab',id)
                    except HTTPException:
                        await websocket.send_json({'type':'denied','message':'Browser control permission is denied or revoked. You may watch while view permission remains.'});continue
                    try:body=HumanBody.model_validate(payload)
                    except ValueError:
                        await websocket.send_json({'type':'error','message':'Invalid browser control request.'});continue
                    try:await service.human_action(id,principal,body.action,body.args)
                    except (ValueError,PermissionError) as error:
                        await websocket.send_json({'type':'error','message':str(error)[:500]})
            tasks=[asyncio.create_task(frames()),asyncio.create_task(input())]
            try:
                done,_=await asyncio.wait(tasks,return_when=asyncio.FIRST_COMPLETED)
                for task in done:task.result()
            finally:
                for task in tasks:task.cancel()
                await asyncio.gather(*tasks,return_exceptions=True)
        except (WebSocketDisconnect,asyncio.CancelledError):pass
        except HTTPException as error:
            try:await websocket.close(code=4401 if error.status_code==401 else 4403,reason='Sign-in expired' if error.status_code==401 else 'Browser view permission denied or revoked')
            except Exception:pass
        except (KeyError,ValueError):
            try:await websocket.close(code=4404,reason='Built-in tab closed or unavailable')
            except Exception:pass
        except Exception:
            try:await websocket.close(code=1011,reason='Browser worker unavailable; reconnect or reopen the tab')
            except Exception:pass
        finally:
            if viewer:service.viewer_finish(viewer)
    return router
