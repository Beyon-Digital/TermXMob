from __future__ import annotations
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal
from termx.auth import extract_passcode
from termx.runners import RunnerService

class Provision(BaseModel):
    model_config=ConfigDict(extra='forbid')
    project_id:str
    image:str
    endpoint:str=''
    network:str='none'
    cpu:float=Field(default=1,ge=.1,le=8)
    memory_mb:int=Field(default=512,ge=64,le=8192)
    lease_seconds:int=Field(default=3600,ge=10,le=86400)

class Run(BaseModel):
    model_config=ConfigDict(extra='forbid')
    request_id:str=Field(min_length=8,max_length=128)
    argv:list[str]
    seconds:int=Field(default=60,ge=1,le=3600)
    secrets:dict[str,str]=Field(default_factory=dict)

class Machine(BaseModel):
    model_config=ConfigDict(extra='forbid')
    project_id:str
    name:str=Field(min_length=1,max_length=100)
    provider:Literal['ssh','ec2','lightsail']='ssh'
    host:str
    user:str
    port:int=Field(default=22,ge=1,le=65535)
    root:str
    identity_file:str=''
    known_hosts_file:str=''
    python_bootstrap:str='python3.14'
    region:str=''
    profile:str=''
    instance_id:str=''
    trust_machine:bool=False

class CloudAction(BaseModel):
    model_config=ConfigDict(extra='forbid')
    action:Literal['status','start','stop','reboot']

def mount_runners(app,state):
    def authority(row):
        principal=state.identity.principal_by_id(row['owner'])
        return bool(principal and principal.policy_version==row['configuration'].get('policy_version'))
    state.runners=RunnerService(state.delivery.directory.parent/'workspace-runners',state.credentials,authority)
    router=APIRouter(prefix='/api/runners')
    def actor(request,project=None):
        raw=extract_passcode(request.headers.get('x-termx-passcode'),request.headers.get('authorization'))
        current=state.identity.resolve(raw)
        if not current:raise HTTPException(401,'Managed sign-in required')
        # Docker administrative access is equivalent to host root: only owners/admins.
        state.authorization.require(raw,'host-admin')
        if project:state.authorization.require(raw,'agent-run',project_id=project)
        return current.principal
    @router.get('')
    def list_runners(request:Request):return state.runners.list(actor(request).id)
    @router.post('/machines')
    def register_machine(body:Machine,request:Request):
        principal=actor(request,body.project_id);state.projects.project(body.project_id)
        config=body.model_dump(exclude={'project_id'});config['policy_version']=principal.policy_version
        return state.runners.machines.register(principal.id,body.project_id,config)
    @router.post('/machines/{identifier}/connect')
    async def connect_machine(identifier:str,request:Request):
        principal=actor(request);row=state.runners.machines.row(principal.id,identifier);actor(request,row['project'])
        return await state.runners.machines.connect(principal.id,identifier)
    @router.post('/machines/{identifier}/setup',status_code=202)
    async def setup_machine(identifier:str,request:Request):
        principal=actor(request);row=state.runners.machines.row(principal.id,identifier);actor(request,row['project'])
        return state.runners.machines.begin_setup(principal.id,identifier)
    @router.post('/machines/{identifier}/cloud')
    async def cloud_machine(identifier:str,body:CloudAction,request:Request):
        principal=actor(request);row=state.runners.machines.row(principal.id,identifier);actor(request,row['project'])
        return await state.runners.machines.cloud_action(principal.id,identifier,body.action)
    @router.post('')
    async def provision(body:Provision,request:Request):
        principal=actor(request,body.project_id)
        state.projects.project(body.project_id)
        config=body.model_dump(exclude={'project_id'});config['policy_version']=principal.policy_version
        return await state.runners.provision(principal.id,body.project_id,config)
    @router.get('/{identifier}/jobs')
    def jobs(identifier:str,request:Request):return state.runners.jobs(actor(request).id,identifier)
    @router.post('/{identifier}/jobs')
    async def run(identifier:str,body:Run,request:Request):return await state.runners.run(actor(request).id,identifier,body.model_dump())
    @router.post('/{identifier}/stop')
    async def stop(identifier:str,request:Request):return await state.runners.stop(actor(request).id,identifier)
    @router.delete('/{identifier}')
    async def remove(identifier:str,request:Request):return await state.runners.stop(actor(request).id,identifier,teardown=True)
    @router.post('/{identifier}/workspace')
    async def upload(identifier:str,request:Request):
        owner=actor(request).id;chunks=[];size=0
        async for chunk in request.stream():
            size+=len(chunk)
            if size>20*1024*1024:raise HTTPException(413,'Workspace archive exceeds 20 MiB')
            chunks.append(chunk)
        return await state.runners.upload(owner,identifier,b''.join(chunks))
    @router.get('/{identifier}/results')
    async def results(identifier:str,request:Request,path:str):
        data=await state.runners.results(actor(request).id,identifier,path)
        return Response(data,media_type='application/x-tar',headers={'Cache-Control':'no-store','Content-Disposition':'attachment; filename="runner-results.tar"'})
    app.include_router(router)
