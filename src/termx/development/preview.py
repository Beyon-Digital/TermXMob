"""Project host previews rendered in an isolated exact-origin Chromium worker.

Remote pages never execute in the privileged application origin or native
WebView. The viewer's localhost is never used as the development server.
"""
from __future__ import annotations
import asyncio
import uuid
import shutil
from pathlib import Path
from time import time
from urllib.parse import urlsplit
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from termx.auth import extract_passcode
from termx.browser.network import NetworkPolicy, TargetDenied, origin
from termx.browser.service import BrowserService

class ExactPreviewNetwork(NetworkPolicy):
    def __init__(self, target):
        self.target=origin(target)
        super().__init__([self.target])
    async def resolve(self, host, port, scheme):
        candidate=origin(f'{scheme}://{host}:{port}')
        if candidate!=self.target:raise TargetDenied('Preview network is limited to its explicit project origin')
        return await super().resolve(host,port,scheme)

class PreviewService:
    def __init__(self,root,session_valid):
        self.root=Path(root);self.session_valid=session_valid;self.items={};self.monitor=None;self.cleanup_tasks=set();self.pending={}
    async def create(self,owner,session,policy,project,url):
        target=origin(url);identifier=uuid.uuid4().hex
        if sum(item['owner']==owner for item in self.items.values())+self.pending.get(owner,0)>=4:raise HTTPException(409,'Close an existing preview before opening another')
        self.pending[owner]=self.pending.get(owner,0)+1
        service=BrowserService(self.root/identifier,session_valid=self.session_valid)
        service.network=ExactPreviewNetwork(target)
        profile=service.create_profile(owner,project,'Project preview',ephemeral=True)
        try:tab=await service.create_tab(owner,session,profile['id'],url)
        except BaseException:
            await service.close();raise
        finally:
            self.pending[owner]-=1
        item={'id':identifier,'owner':owner,'session':session,'policy':policy,'project':project,'origin':target,'expires':time()+1800,'service':service,'tab':tab['id']}
        self.items[identifier]=item
        return {key:item[key] for key in ('id','project','origin','expires')}
    def get(self,owner,identifier):
        item=self.items.get(identifier)
        if not item or item['owner']!=owner:raise HTTPException(404,'Preview not found')
        if item['expires']<=time() or not self.session_valid(owner,item['session'],item['policy']):raise HTTPException(403,'Preview session expired or was revoked')
        return item
    async def remove(self,identifier):
        item=self.items.pop(identifier,None)
        if item:
            async def cleanup():
                try:await item['service'].close()
                finally:shutil.rmtree(self.root/identifier,ignore_errors=True)
            task=asyncio.create_task(cleanup());self.cleanup_tasks.add(task)
            task.add_done_callback(self.cleanup_tasks.discard)
            await asyncio.shield(task)
    def start(self):
        if not self.monitor:self.monitor=asyncio.create_task(self._monitor())
    async def _monitor(self):
        while True:
            await asyncio.sleep(1)
            for identifier,item in list(self.items.items()):
                if item['expires']<=time() or not self.session_valid(item['owner'],item['session'],item['policy']):await self.remove(identifier)
    async def close(self):
        if self.monitor:
            self.monitor.cancel();await asyncio.gather(self.monitor,return_exceptions=True);self.monitor=None
        for identifier in list(self.items):await self.remove(identifier)
        await asyncio.gather(*list(self.cleanup_tasks),return_exceptions=True)

class CreatePreview(BaseModel):
    model_config=ConfigDict(extra='forbid')
    url:str=Field(max_length=2000)
class PreviewInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    action:str
    args:dict=Field(default_factory=dict)

def mount_previews(app,state):
    state.previews=PreviewService(state.delivery.directory.parent/'project-previews',state.browser.session_valid)
    router=APIRouter(prefix='/api/development/projects/{project_id}/previews')
    def actor(request,project,scope='desktop-view'):
        raw=extract_passcode(request.headers.get('x-termx-passcode'),request.headers.get('authorization'))
        current=state.identity.resolve(raw)
        if not current:raise HTTPException(401,'Managed sign-in required')
        state.authorization.require(raw,scope,project_id=project)
        state.projects.project(project)
        return raw,current
    def owned(request,project,identifier,scope='desktop-view'):
        _,current=actor(request,project,scope)
        item=state.previews.get(current.principal.id,identifier)
        if item['project']!=project:raise HTTPException(404,'Preview not found')
        return item
    @router.post('')
    async def create(project_id:str,body:CreatePreview,request:Request):
        raw,current=actor(request,project_id,'host-admin')
        parsed=urlsplit(body.url)
        try:port=parsed.port
        except ValueError as exc:raise HTTPException(400,'Invalid preview port') from exc
        server=request.scope.get('server') or ('',0)
        if parsed.scheme!='http' or parsed.hostname!='127.0.0.1' or not port or not 1024<=port<=65535 or port in {server[1],request.url.port} or parsed.username or parsed.password or parsed.fragment:
            raise HTTPException(400,'Select a host development server at http://127.0.0.1:PORT, separate from the TermX control service')
        return await state.previews.create(current.principal.id,current.session_id,state.authorization.revision(raw),project_id,body.url)
    @router.get('/{identifier}/frame')
    async def frame(project_id:str,identifier:str,request:Request):
        item=owned(request,project_id,identifier)
        raw=await item['service'].frame(item['tab'],item['owner'],human=True)
        owned(request,project_id,identifier)
        return Response(raw,media_type='image/jpeg',headers={'Cache-Control':'no-store'})
    @router.get('/{identifier}/context')
    async def context(project_id:str,identifier:str,request:Request):
        item=owned(request,project_id,identifier)
        value=await item['service'].observe(item['tab'],item['owner'],human=True)
        owned(request,project_id,identifier);return value
    @router.post('/{identifier}/input')
    async def human(project_id:str,identifier:str,body:PreviewInput,request:Request):
        item=owned(request,project_id,identifier,'desktop-control')
        if body.action not in {'click','type','key','scroll','history','find','zoom'}:raise HTTPException(400,'Unsupported preview input')
        revision=body.args.pop('_document_revision',None)
        if revision is not None and revision!=item['service'].get(item['tab'],item['owner'])['document_revision']:
            raise HTTPException(409,'Preview page changed; refresh its controls before acting')
        try:return await item['service'].human_action(item['tab'],item['owner'],body.action,body.args)
        except (ValueError,KeyError) as exc:raise HTTPException(400,'Invalid preview input') from exc
    @router.delete('/{identifier}')
    async def close(project_id:str,identifier:str,request:Request):
        owned(request,project_id,identifier,'desktop-control');await state.previews.remove(identifier);return {'ok':True}
    app.include_router(router)
