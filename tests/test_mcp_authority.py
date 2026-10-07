"""Actual MCP stdio transport and current project/caller boundaries."""
import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from termx.agent.secrets import CredentialStore
from termx.mcp.client import McpPool,McpConnectionError
from termx.mcp.defs import validate_connection,ConnectionError_
from termx.mcp.registry import ConnectionRegistry
from termx.mcp.scope import authorize,definition_digest
from termx.engines.gateway import EngineGateway
from termx.engines.types import EffectiveRunConfiguration
from test_authorization import client_fixture,PASSWORD
from termx.authorization import ROLES

SERVER=str(Path(__file__).parent/'fixtures'/'fake_mcp_server.py')
def definition(**changes):
    return validate_connection({'id':'scoped','command':[sys.executable,SERVER],'transport':'stdio','trust':'trusted',**changes})


def test_definition_project_owner_validation_roundtrip_and_compare_swap(tmp_path):
    for changes in ({'owner':'arbitrary'},{'allowed_projects':'one'},{'allowed_projects':[None]},{'owner':'project:one','allowed_projects':['two']}):
        with pytest.raises(ConnectionError_):definition(**changes)
    conn=definition(owner='project:one');assert conn.allowed_projects==['one']
    reg=ConnectionRegistry([str(tmp_path)]);reg.save(conn);digest=definition_digest(conn)
    assert reg.get(conn.id).allowed_projects==['one']
    conn.allowed_projects=['one'];conn.label='new';reg.save(conn,expected_digest=digest)
    with pytest.raises(ConnectionError_,match='changed'):reg.save(conn,expected_digest=digest)


def test_actual_pool_rechecks_project_trust_approved_tools_and_live_definition(tmp_path):
    async def run():
        reg=ConnectionRegistry([str(tmp_path)]);conn=definition(allowed_projects=['one'],approved_tools=['add']);reg.save(conn)
        pool=McpPool(CredentialStore(memory={}));pool.definition_resolver=reg.get
        try:
            with pytest.raises(PermissionError):await pool.connect(conn)
            with pytest.raises(PermissionError):await pool.connect(conn,project_id='two')
            await pool.connect(conn,project_id='one')
            from termx.mcp.gateway import McpGateway,GatewayAuthError
            gateway=McpGateway(pool)
            with pytest.raises(GatewayAuthError):gateway.mint('session',{conn.id:['add']},project_id='one')
            out=await pool.call_tool(conn.id,'add',{'a':2,'b':3},project_id='one')
            assert out['structured_content']['result']==5
            with pytest.raises(PermissionError):await pool.call_namespaced('scoped.add',{'a':2,'b':3},project_id='two')
            with pytest.raises(PermissionError):await pool.call_tool(conn.id,'echo',{'text':'forbidden'},project_id='one')
            changed=reg.get(conn.id);changed.allowed_projects=['two'];reg.save(changed)
            with pytest.raises(PermissionError):await pool.call_tool(conn.id,'add',{},project_id='one')
            with pytest.raises(McpConnectionError,match='changed'):await pool.call_tool(conn.id,'add',{},project_id='two')
            changed.trust='untrusted';reg.save(changed)
            with pytest.raises(McpConnectionError,match='untrusted'):await pool.call_tool(conn.id,'add',{},project_id='two')
        finally:await pool.shutdown()
    asyncio.run(run())


def test_pool_connection_lifetime_owned_across_request_tasks(tmp_path):
    async def run():
        pool=McpPool(CredentialStore(memory={}));conn=definition()
        await asyncio.create_task(pool.connect(conn))
        owner=pool._connections[conn.id]._owner
        assert owner and not owner.done()
        result=await asyncio.create_task(pool.call_tool(conn.id,'add',{'a':4,'b':2}))
        assert result['structured_content']['result']==6
        await asyncio.create_task(pool.disconnect(conn.id))
        assert owner.done() and owner.exception() is None
        assert pool.status()=={}
    asyncio.run(run())


def test_native_resolver_denies_before_secrets_and_redacts_canonical_snapshot(tmp_path):
    conn=definition(allowed_projects=['one'],secret_refs=['mcp.scoped.env.TOKEN'])
    gateway=EngineGateway(None,None);gateway.mcp_resolver=lambda _:conn
    lookups=[];gateway.credential_lookup=lambda ref:lookups.append(ref) or 'fixture-secret'
    links=[{'connection':conn.id,'tools':['add']}]
    with pytest.raises(PermissionError):gateway._resolve_mcp_bindings(links,project_id='two',authorize=lambda _:None)
    with pytest.raises(ValueError,match='Internal broker'):gateway._resolve_mcp_bindings(links,project_id='one',authorize=lambda _:None)
    assert not lookups
    conn.allowed_projects=[]
    bindings=gateway._resolve_mcp_bindings(links,project_id='one',authorize=lambda _:None)
    assert bindings[0]['env']=={'TOKEN':'fixture-secret'}
    assert 'fixture-secret' not in json.dumps(EffectiveRunConfiguration(mcp_bindings=bindings).as_dict())
    conn.approved_tools=['echo']
    with pytest.raises(PermissionError):gateway._resolve_mcp_bindings(links,project_id='one',authorize=lambda _:None)


def test_current_caller_enrolled_project_and_cwd_authority(tmp_path):
    identity,state,client,owner=client_fixture(tmp_path)
    one=tmp_path/'one';two=tmp_path/'two';one.mkdir();two.mkdir()
    p=state.projects.register(str(one));q=state.projects.register(str(two))
    alice=identity.create_local_user('mcp-alice',PASSWORD,list(ROLES['operator']));state.authorization.set_role(alice.id,'operator',trusted_execution=True)
    state.authorization.grant_project(alice.id,p['id'],['agent-view','agent-control','agent-run'])
    def token():return asyncio.run(identity.login('local-password',{'username':'mcp-alice','password':PASSWORD},peer='local')).access_token
    conn=definition(allowed_projects=[p['id']]);secret=token()
    authorize(state,conn,'agent-run',credential=secret,project_id=p['id'],cwd=str(one))
    for kwargs in ({'project_id':q['id'],'cwd':str(two)},{'project_id':p['id'],'cwd':str(two)},{'project_id':None}):
        with pytest.raises(HTTPException):authorize(state,conn,'agent-run',credential=secret,**kwargs)
    with pytest.raises(HTTPException):authorize(state,definition(),'agent-run',credential=secret,project_id=p['id'],cwd=str(one))
    state.authorization.revoke_project(alice.id,p['id'])
    with pytest.raises(HTTPException):authorize(state,conn,'agent-run',credential=token(),project_id=p['id'],cwd=str(one))


def test_graphql_scopes_compare_swap_and_write_only_credential(tmp_path):
    identity,state,client,owner=client_fixture(tmp_path)
    state.credentials=CredentialStore(memory={});state.mcp_pool._credentials=state.credentials
    root=tmp_path/'project';root.mkdir();project=state.projects.register(str(root))
    def gql(document,variables=None,headers=owner):
        response=client.post('/graphql',headers=headers,json={'query':document,'variables':variables or {}})
        assert response.status_code==200,response.text
        return response.json()
    mutation='mutation($data:JSON!,$digest:String){create_mcp_connection(input:{data:$data,expected_digest:$digest})}'
    data=definition(allowed_projects=[project['id']]).as_dict()
    wrong=gql(mutation,{'data':{**data,'allowed_projects':['missing']}})
    assert wrong['errors'][0]['extensions']['http_status']==400
    created=gql(mutation,{'data':data});assert not created.get('errors'),created
    alice=identity.create_local_user('mcp-untrusted',PASSWORD,list(ROLES['operator']))
    state.authorization.set_role(alice.id,'operator',trusted_execution=False)
    state.authorization.grant_project(alice.id,project['id'],['agent-view','agent-control','agent-run'])
    token=asyncio.run(identity.login('local-password',{'username':'mcp-untrusted','password':PASSWORD},peer='local')).access_token
    untrusted={'Authorization':'Bearer '+token}
    listing=gql('query($p:String){mcp_connections(project_id:$p){id}}',{'p':project['id']},headers=untrusted)
    assert [r['id'] for r in listing['data']['mcp_connections']]==[data['id']]
    blocked=gql('mutation($p:String,$id:String!){connect_mcp_connection(conn_id:$id,project_id:$p){status}}',{'p':project['id'],'id':data['id']},headers=untrusted)
    assert blocked['errors'][0]['extensions']['http_status']==403
    assert not state.mcp_pool._connections
    inventory=gql('query($p:String){mcp_connections(project_id:$p){id allowed_projects definition definition_digest credential_configured}}',{'p':project['id']})
    row=next(r for r in inventory['data']['mcp_connections'] if r['id']==data['id']);digest=row['definition_digest']
    stale=gql(mutation,{'data':{**data,'label':'stale'},'digest':'wrong'})
    assert stale['errors'][0]['extensions']['http_status']==409
    credential='mutation($id:String!,$digest:String!,$value:String){set_mcp_credential(conn_id:$id,binding:"TOKEN",value:$value,expected_digest:$digest,kind:"env")}'
    result=gql(credential,{'id':data['id'],'digest':digest,'value':'fixture-only-secret'})
    assert not result.get('errors'),result
    assert 'fixture-only-secret' not in json.dumps(result)
    binding=result['data']['set_mcp_credential'];ref=binding['connection']['secret_refs'][0]
    assert state.credentials.get(ref)=='fixture-only-secret'
    assert binding['definition_digest']!=digest
    with pytest.raises(ConnectionError_):state.mcp_registry().save(definition(allowed_projects=[project['id']]),expected_digest=digest)
    result=gql(credential,{'id':data['id'],'digest':binding['definition_digest'],'value':None})
    assert result['data']['set_mcp_credential']['configured'] is False
    assert state.credentials.get(ref) is None
    preset='mutation($connections:[JSON!]){create_custom_agent(input:{name:"MCP configured",tools:["mcp_catalog","mcp_call"],mcp_connections:$connections}){id mcp_connections file_revision}}'
    links=[{'connection':data['id'],'tools':['add']}]
    bound=gql(preset,{'connections':links});assert not bound.get('errors'),bound
    agent=bound['data']['create_custom_agent'];assert agent['mcp_connections']==links
    invalid=gql('mutation($connections:[JSON!]){create_custom_agent(input:{name:"Invalid MCP",mcp_connections:$connections}){id}}',{'connections':[{'connection':data['id'],'env':{'TOKEN':'must-not-save'}}]})
    assert invalid['errors'][0]['extensions']['http_status']==400
    patched=gql('mutation($id:String!,$revision:String!){patch_custom_agent(agent_id:$id,input:{description:"Preserve bindings",revision:$revision}){mcp_connections description}}',{'id':agent['id'],'revision':agent['file_revision']})
    assert not patched.get('errors'),patched
    assert patched['data']['patch_custom_agent']['mcp_connections']==links


def test_internal_manager_actual_mcp_transport_and_revoked_effect(tmp_path):
    from termx.agent.providers import ProviderCall,ProviderTurn
    from termx.mcp.task_broker import McpTaskBroker
    from test_agent import FakeAdapter
    from termx.agents.files import AgentFile
    from termx.app import create_app
    import httpx
    identity,state,client,headers=client_fixture(tmp_path)
    state.credentials=CredentialStore(memory={});state.agent.credentials=state.credentials;state.mcp_pool._credentials=state.credentials
    live=identity.resolve(headers['Authorization'][7:]);owner=live.principal
    root=tmp_path/'project';root.mkdir();project=state.projects.register(str(root))
    conn=definition(allowed_projects=[project['id']],approved_tools=['add']);state.mcp_registry().save(conn)
    state.agent.mcp=McpTaskBroker(state)
    state.agent.save_provider(provider_id='mcp-fixture',kind='openai',name='Fixture',base_url='https://fixture.invalid',model='fixture',capabilities=['functions'],api_key='fixture-no-network')
    class Adapter(FakeAdapter):
        async def turn(self,**kwargs):
            self.turns+=1
            if self.turns==1:
                return ProviderTurn(response_id='fixture-catalog',text='',calls=[ProviderCall('function','catalog','mcp_catalog',{})],usage={})
            if self.turns==2:
                return ProviderTurn(response_id='fixture-call',text='',calls=[ProviderCall('function','add','mcp_call',{'connection':conn.id,'tool':'add','arguments':{'a':2,'b':3}})],usage={})
            return ProviderTurn(response_id='fixture-done',text='Verified',calls=[],usage={})
    profile={'id':'fixture-mcp','name':'Fixture MCP','tools':['mcp_catalog','mcp_call'],'file':{'mcp_connections':[{'connection':conn.id,'tools':['add']}]}}
    installed=state.agent_registry.save(AgentFile(slug='fixture-mcp',name='Fixture MCP',engine='internal',tools=['mcp_catalog','mcp_call'],mcp_connections=profile['file']['mcp_connections']))
    async def run():
        await state.mcp_pool.connect(conn,project_id=project['id'])
        handle=state.mcp_pool._connections[conn.id];original_call=handle.session.call_tool;effects=[]
        async def counted(tool,arguments):
            effects.append(tool);return await original_call(tool,arguments)
        handle.session.call_tool=counted
        try:
            for revoke in (False,True):
                adapter=Adapter();state.agent._adapter_factory=lambda *_:adapter
                conv=state.agent_store.create_conversation(title='Fixture MCP',project_id=project['id'],cwd=str(root))
                pinned=state.agent.mcp.preflight(owner,project['id'],str(root),conv['id'],profile)
                def created(tid):
                    state.workspace.store.create('task',owner.id,{'conversation_id':conv['id'],'cwd':str(root),'mcp_snapshot':pinned},project['id'],tid)
                    state.authorization.claim_principal(owner,'task',tid,project['id'])
                    state.browser.records.put('agent-task-authority',tid,{'id':tid,'principal_id':owner.id,'session_id':live.session_id,'project_id':project['id'],'policy_version':owner.policy_version})
                if not revoke:
                    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(state)),base_url='https://localhost') as http:
                        response=await http.post('/graphql',headers=headers,json={'query':'mutation($cwd:String!,$preset:String!){create_agent_task(input:{prompt:"Controlled MCP fixture",cwd:$cwd,provider_id:"mcp-fixture",mode:"agent",custom_agent_id:$preset}){id status}}','variables':{'cwd':str(root),'preset':installed.qid_id}})
                    assert response.status_code==200,response.text
                    result=response.json();assert not result.get('errors'),result
                    task=result['data']['create_agent_task']
                    bound=state.browser.records.get('agent-task-authority',task['id'])
                    assert bound['session_id']==live.session_id and bound['principal_id']==owner.id
                else:task=await state.agent.create_task(prompt='Controlled MCP fixture',cwd=str(root),provider_id='mcp-fixture',mode='agent',custom_agent_snapshot=profile,on_created=created)
                for _ in range(500):
                    current=state.agent_store.get_task(task['id'])
                    pending=next((a for a in state.agent_store.approvals(task['id']) if a['status']=='pending'),None)
                    if pending:
                        if revoke and pending['kind']=='tool':identity.revoke(live.session_id,owner.id)
                        await state.agent.resolve_approval(task['id'],pending['id'],'approved')
                    if current['status'] not in {'queued','running','planning','awaiting_approval','paused'}:break
                    await asyncio.sleep(.01)
                events=state.agent_store.events(task['id'])
                outputs=[e['payload'].get('result',{}) for e in events if e['type']=='tool.finished']
                if revoke:
                    assert any(o.get('refused') for o in outputs),outputs
                    assert effects==['add'],effects
                else:
                    assert any('5' in str(o.get('result','')) and o.get('tool')=='add' for o in outputs),outputs
                    assert any(a['kind']=='tool' and a['status']=='approved' for a in state.agent_store.approvals(task['id']))
                    assert effects==['add'],effects
        finally:await state.mcp_pool.shutdown();await state.agent.close()
    asyncio.run(run())
