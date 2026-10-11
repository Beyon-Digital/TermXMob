from typing import Any, Literal
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from termx.auth import extract_passcode
from .base import CloudError
from .models import Launch
from .service import CloudService

class AccountInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    provider:Literal['aws','azure','gcp']
    name:str=Field(min_length=1,max_length=100)
    credentials:dict[str,Any]

class LaunchInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    preview_id:str

def mount_cloud(app,state):
    def actor(request,project=None):
        raw=extract_passcode(request.headers.get('x-termx-passcode'),request.headers.get('authorization'))
        identity=state.identity.resolve(raw)
        if not identity:raise HTTPException(401,'Managed sign-in required')
        state.authorization.require(raw,'host-admin')
        if project:state.authorization.require(raw,'agent-run',project_id=project)
        return identity.principal
    def authority(row):
        try:
            principal=state.identity.principal_by_id(row['owner'])
            if not principal or principal.policy_version!=row['configuration']['policy_version']:return False
            state.authorization.require_principal(principal,'host-admin')
            state.authorization.require_principal(principal,'agent-run',project_id=row['project'])
            return True
        except Exception:return False
    state.cloud=CloudService(state.runners,authority);state.runners.cloud=state.cloud
    router=APIRouter(prefix='/api/cloud')
    async def cloud_error(request,error):return JSONResponse({'detail':str(error)},status_code=400 if error.definitive else 502,headers={'Cache-Control':'no-store'})
    app.add_exception_handler(CloudError,cloud_error)
    @router.get('/accounts')
    def accounts(request:Request):return {'accounts':state.cloud.accounts(actor(request).id),'secure_storage':state.credentials.available()}
    @router.post('/accounts')
    async def create_account(body:AccountInput,request:Request):return await state.cloud.save_account(actor(request).id,body.provider,body.name,body.credentials)
    @router.put('/accounts/{identifier}')
    async def update_account(identifier:str,body:AccountInput,request:Request):return await state.cloud.save_account(actor(request).id,body.provider,body.name,body.credentials,identifier)
    @router.delete('/accounts/{identifier}')
    def delete_account(identifier:str,request:Request):return state.cloud.delete_account(actor(request).id,identifier)
    @router.get('/accounts/{identifier}/catalog')
    async def catalog(identifier:str,request:Request,region:str=Query(default='',pattern='^[a-z0-9-]{0,40}$'),zone:str=Query(default='',pattern='^[a-zA-Z0-9-]{0,80}$')):
        return await state.cloud.catalog(actor(request).id,identifier,region,zone)
    @router.get('/deployments')
    def deployments(request:Request):return state.cloud.list(actor(request).id)
    @router.post('/preview')
    async def preview(body:Launch,request:Request):
        principal=actor(request,body.project_id);state.projects.project(body.project_id)
        return await state.cloud.preview(principal,body.model_dump())
    @router.post('/deployments',status_code=202)
    async def launch(body:LaunchInput,request:Request):return state.cloud.launch(actor(request),body.preview_id)
    @router.post('/deployments/{identifier}/cleanup',status_code=202)
    async def cleanup(identifier:str,request:Request):return await state.cloud.request_cleanup(actor(request).id,identifier)
    app.include_router(router)
