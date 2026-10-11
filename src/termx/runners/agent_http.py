from fastapi import APIRouter,HTTPException,Request
from termx.auth import extract_passcode
from termx.runners.agent import RunnerAgentService

def mount_runner_agents(app,state):
    state.runner_agents=RunnerAgentService(state)
    router=APIRouter(prefix='/api/runner-agents')
    def actor(request):
        raw=extract_passcode(request.headers.get('x-termx-passcode'),request.headers.get('authorization'))
        identity=state.identity.resolve(raw)
        if not identity:raise HTTPException(401,'Managed sign-in required')
        state.authorization.require(raw,'host-admin');return identity.principal
    @router.get('/capabilities')
    def capabilities(request:Request):
        return {'protocol':1,'engine':'internal','accounts':state.runner_agents.accounts(actor(request)),'network':'none','credential_transport':'host-broker','execution_modes':{'container':{'network':'none','root':'/workspace'},'machine':{'network':'machine','root':'configured','permissions':'ssh-account'}}}
    @router.post('/{runner_id}/preflight')
    async def preflight(runner_id:str,request:Request):
        data=await request.json()
        result=await state.runner_agents.preflight(actor(request),runner_id,data.get('provider_id'),data.get('credential_ref'),data.get('model'))
        return {'runner_id':runner_id,'capabilities':result['capabilities'],'provider_id':result['provider']['id'],'model':result['provider']['model']}
    app.include_router(router)
