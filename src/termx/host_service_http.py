"""Authenticated service controls and private challenge-bound local attachment."""
from __future__ import annotations

import asyncio
import secrets

from fastapi import BackgroundTasks, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from termx.auth import extract_passcode
from termx.host_service import HostService
from termx.identity_http import ACCESS_COOKIE, loopback


class ServiceOptions(BaseModel):
    model_config=ConfigDict(extra='forbid')
    port:int=Field(default=8787,ge=1,le=65535)
    host:str=Field(default='127.0.0.1',pattern=r'^(127\.0\.0\.1|::1)$')
    tunnel_profile_id:str|None=Field(default=None,max_length=200)


def mount_host_service(app, state):
    def authorize(request):
        raw=extract_passcode(request.headers.get('x-termx-passcode'),request.headers.get('authorization')) or request.cookies.get(ACCESS_COOKIE)
        session=state.identity.resolve(raw)
        if not session:raise HTTPException(401,'Managed sign-in required')
        try:state.workspace.require(session.principal,'host-admin')
        except PermissionError as exc:raise HTTPException(403,str(exc)) from None
        return session.principal

    async def control(fn, *args, **kwargs):
        try:return await asyncio.to_thread(fn,*args,**kwargs)
        except (ValueError,OSError,RuntimeError) as exc:raise HTTPException(409,str(exc)) from None

    @app.get('/api/host/runtime')
    async def runtime(request:Request):
        instance=getattr(state,'host_instance',None)
        token=request.headers.get('x-termx-host-proof','')
        if not request.client or not loopback(request.client.host) or not instance or not secrets.compare_digest(token,instance.proof_token):
            raise HTTPException(403,'Private local host proof required')
        value=getattr(state,'host_runtime',None)
        if not value:raise HTTPException(503,'Host is not ready')
        return value

    @app.get('/api/host/service')
    async def status(request:Request):
        authorize(request)
        return await control(HostService().status)

    @app.post('/api/host/service/install')
    async def install(request:Request,body:ServiceOptions):
        actor=authorize(request)
        from termx.cli import default_web_dir
        if body.tunnel_profile_id:
            try:state.workspace.require(actor,'network-manage')
            except PermissionError as exc:raise HTTPException(403,str(exc)) from None
        return await control(HostService().install,host=body.host,port=body.port,web_dir=str(getattr(state,'host_web_dir',None) or default_web_dir()),
            bootstrap_passcode=state.auth.passcode,tunnel_profile_id=body.tunnel_profile_id,
            tunnel_authority={'principal_id':actor.id,'policy_version':actor.policy_version} if body.tunnel_profile_id else None)

    @app.post('/api/host/service/start')
    async def start(request:Request):
        authorize(request)
        return await control(HostService().start)

    @app.post('/api/host/service/stop')
    async def stop(request:Request,background:BackgroundTasks):
        authorize(request)
        background.add_task(asyncio.to_thread,HostService().stop,wait=False)
        return {'accepted':True,'state':'stopping'}

    @app.post('/api/host/service/uninstall')
    async def uninstall(request:Request,background:BackgroundTasks):
        authorize(request)
        background.add_task(asyncio.to_thread,HostService().uninstall,wait=False)
        return {'accepted':True,'state':'uninstalling'}
