import asyncio
from dataclasses import replace
from time import time
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from termx.agent.policies.engine import PolicyEngine
from termx.agent.policies.models import PolicyIntent
from termx.agent.policy import PolicyDecision
from termx.agent.store import AgentStore
from termx.workspace.coding_policies import mount_coding_policies
from test_durable_workspace import workspace


def binding(principal='alice',version=1,conversation='c-a',session='s-a'):
    return dict(principal_id=principal,policy_version=version,conversation_id=conversation,session_id=session)

@pytest.fixture
def ledger(tmp_path):
    store=AgentStore(tmp_path/'consent.sqlite3',tmp_path/'artifacts');yield store;store.close()

def intent(bound,task='task-a'):
    return PolicyIntent('tool','run_shell','exact-fp','exact','exact fixture command','/fixture',project_id='p',task_id=task,consent_binding=bound)

def decision(engine,i):
    return engine.evaluate(i,PolicyDecision(False,True,'Human review required','Exact operation'),SimpleNamespace(project_id='p',task={}))

@pytest.mark.parametrize('scope',['project','custom_agent','conversation'])
def test_managed_consent_matches_owner_policy_and_exact_conversation_device(ledger,scope):
    active=binding();engine=PolicyEngine(ledger,binding_lookup=lambda _:active)
    i=replace(intent(active),custom_agent_id='preset-a');rule=engine.record_resolution(i,decision='approved',remember=scope,project_id='p',source_approval_id='approval-a')
    assert rule['expires_at'] and rule['consent_binding']==active
    assert decision(engine,i).auto_resolved=='allow'
    assert decision(engine,replace(i,consent_binding=binding('bob'))).approval_required
    assert decision(engine,replace(i,consent_binding=binding(version=2))).approval_required
    assert decision(engine,replace(i,fingerprint='different-effect')).approval_required
    other=replace(i,task_id='task-b',consent_binding=binding(conversation='c-b',session='s-b'))
    assert decision(engine,other).approval_required if scope=='conversation' else decision(engine,other).auto_resolved=='allow'

def test_denies_and_sandbox_win_over_owned_allow_and_capability_consent(ledger):
    active=binding();engine=PolicyEngine(ledger,binding_lookup=lambda _:active,envelope=lambda _:frozenset(),grantable=lambda _:frozenset({'net.outbound:any'}))
    i=intent(active)
    allowed=engine.record_resolution(i,decision='approved',remember='project',project_id='p',source_approval_id='allow')
    ledger.create_policy_rule(effect='deny',scope_type='host',scope_id=None,action_type='tool',tool=i.tool,fingerprint=i.fingerprint)
    assert decision(engine,i).auto_resolved=='deny'
    needing=replace(i,required_capabilities=('net.outbound:any',))
    assert decision(engine,needing).approval_kind=='capability'
    for effect in ('allow','deny'):
        ledger.create_policy_rule(effect=effect,scope_type='project',scope_id='p',action_type='capability',tool='run_shell',fingerprint='cap',capabilities=['net.outbound:any'],consent_binding=active)
    assert not engine.capability_grant_set('agent',project_id='p',task_id='task-a')
    changed=ledger.edit_policy_consent(allowed['id'],version=1,effect='deny',expires_at=time()+60,validate=lambda row:None)
    assert changed['version']==2 and changed['fingerprint']==allowed['fingerprint'] and changed['consent_binding']==active
    with pytest.raises(ValueError,match='changed'):ledger.edit_policy_consent(allowed['id'],version=1,effect='allow',expires_at=time()+60,validate=lambda row:None)

def test_stale_or_unbound_origin_cannot_create_member_consent(ledger):
    active=binding();engine=PolicyEngine(ledger,binding_lookup=lambda _:binding(version=2))
    with pytest.raises(ValueError,match='authority changed'):engine.record_resolution(intent(active),decision='approved',remember='project',project_id='p',source_approval_id='a')
    with pytest.raises(ValueError,match='originating authority'):engine.record_resolution(intent({'unbound':True}),decision='approved',remember='project',project_id='p',source_approval_id='a')
    assert not ledger.list_policy_rules()

def test_real_managed_typed_tool_consent_resumes_exact_call_and_second_call_without_prompt(tmp_path):
    from test_agent import _FanOutAdapter,_fn,build_manager,wait_for_pending_approval,wait_for_status
    from termx.browser.service import BrowserService
    async def run():
        adapter=_FanOutAdapter({'Edit code':([[_fn('write-a','write_file',path='owned.py',content='VALUE = 1\n')],[_fn('write-b','write_file',path='owned.py',content='VALUE = 1\n')]],0.)})
        manager,store=build_manager(tmp_path,adapter)
        service=BrowserService(tmp_path/'browser',session_valid=lambda p,s,v:v==1);manager.browser=service
        try:
            task=await manager.create_task(prompt='Edit code',cwd=str(tmp_path),provider_id='fake',on_created=lambda tid:service.records.put('agent-task-authority',tid,{'id':tid,'project_id':'p',**binding()}))
            await manager.resolve_approval(task['id'],task['approvals'][0]['id'],'approved')
            _,approval=await wait_for_pending_approval(store,task['id'],'tool')
            assert 'conversation' in approval['payload']['remember_options'] and not (tmp_path/'owned.py').exists()
            with pytest.raises(ValueError,match='requested remembered scope'):
                await manager.resolve_approval(task['id'],approval['id'],'approved',remember='host')
            assert store.get_approval(approval['id'])['status']=='pending'
            await manager.resolve_approval(task['id'],approval['id'],'approved',remember='conversation')
            finished=await wait_for_status(store,task['id'],'completed')
            assert (tmp_path/'owned.py').read_text()=='VALUE = 1\n'
            assert len([row for row in finished['approvals'] if row['kind']=='tool'])==1
            assert len([row for row in store.events(task['id']) if row['type']=='tool.finished'])==2
            review=service.records.list('review');assert len(review)==2
            assert any(row['decision_source']=='coding-policy' and row['status']=='completed' for row in review)
            stored=store.list_policy_rules()[0];assert stored['scope_type']=='conversation' and stored['consent_binding']['principal_id']=='alice'
            # An equivalent call under Bob's live task cannot inherit Alice's consent.
            manager._adapter_factory=lambda *_:_FanOutAdapter({'Other edit':([[_fn('bob-write','write_file',path='owned.py',content='VALUE = 1\n')]],0.)})
            other=await manager.create_task(prompt='Other edit',cwd=str(tmp_path),provider_id='fake',on_created=lambda tid:service.records.put('agent-task-authority',tid,{'id':tid,'project_id':'p',**binding('bob')}))
            await manager.resolve_approval(other['id'],other['approvals'][0]['id'],'approved')
            _,pending=await wait_for_pending_approval(store,other['id'],'tool')
            assert pending['status']=='pending' and pending['payload']['browser_review']['principal_id']=='bob'
        finally:
            await manager.close();await service.close();store.close()
    asyncio.run(run())

def test_coding_policy_api_cas_ownership_policy_revocation_and_immutable_scope(workspace):
    service,owner,path=workspace;state=service.state;state.workspace=service
    login=asyncio.run(state.identity.login('local-password',{'username':'owner','password':'strong fixture password'},peer='fixture'));session=state.identity.resolve(login.access_token)
    bound=binding(owner.id,owner.policy_version,session=session.session_id)
    def rule(bound=bound,**kw):
        return service.agents.create_policy_rule(effect='allow',scope_type='project',scope_id='project-a',action_type='tool',tool='run_shell',fingerprint='fp',project_id='project-a',consent_binding=bound,expires_at=time()+1000,**kw)
    ours=rule();foreign=rule(binding('foreign'));stale=rule(binding(owner.id,owner.policy_version+1));app=FastAPI();mount_coding_policies(app,state)
    with TestClient(app) as client:
        headers={'Authorization':'Bearer '+login.access_token}
        rows=client.get('/api/workspace/coding-policies',headers=headers).json()['rules'];assert foreign['id'] not in [row['id'] for row in rows]
        assert next(row for row in rows if row['id']==stale['id'])['editable'] is False
        endpoint='/api/workspace/coding-policies/'+ours['id'];body={'version':1,'effect':'deny','expires_in':120}
        assert client.patch(endpoint,headers=headers,json={**body,'scope_id':'outside'}).status_code==422
        changed=client.patch(endpoint,headers=headers,json=body);assert changed.status_code==200 and changed.json()['version']==2
        assert client.patch(endpoint,headers=headers,json=body).status_code==409
        assert client.patch('/api/workspace/coding-policies/'+foreign['id'],headers=headers,json=body).status_code==404
        assert client.patch('/api/workspace/coding-policies/'+stale['id'],headers=headers,json=body).status_code==403
        assert client.request('DELETE',endpoint,headers=headers,json={'version':2}).status_code==200
        assert service.agents.get_policy_rule(ours['id'])['revoked_at'] is not None
        state.identity.revoke(session.session_id,owner.id)
        assert client.get('/api/workspace/coding-policies',headers=headers).status_code==401

def test_legacy_database_upgrade_preserves_explicit_shared_policy_and_allows_bound_conversation(tmp_path):
    import sqlite3
    path=tmp_path/'legacy.sqlite3';store=AgentStore(path,tmp_path/'artifacts')
    old=store.create_policy_rule(effect='deny',scope_type='project',scope_id='p',action_type='tool',tool='run_shell',fingerprint='legacy')
    ddl=store._db.execute("SELECT sql FROM sqlite_master WHERE name='policy_rules'").fetchone()[0];store.close()
    with sqlite3.connect(path) as database:
        database.execute('ALTER TABLE policy_rules RENAME TO fixture_original_rules')
        database.execute(ddl.replace("'conversation',",''))
        database.execute('INSERT INTO policy_rules SELECT * FROM fixture_original_rules')
        database.execute('DROP TABLE fixture_original_rules')
    reopened=AgentStore(path,tmp_path/'artifacts')
    try:
        assert reopened.get_policy_rule(old['id'])['effect']=='deny' and reopened.get_policy_rule(old['id'])['consent_binding']=={}
        new=reopened.create_policy_rule(effect='allow',scope_type='conversation',scope_id='c-a',action_type='tool',tool='write_file',fingerprint='exact',consent_binding=binding(),expires_at=time()+100)
        assert new['scope_type']=='conversation' and new['consent_binding']['principal_id']=='alice'
    finally:reopened.close()

def test_managed_consent_cannot_be_read_or_widened_through_legacy_graphql(tmp_path,monkeypatch):
    from termx.app import AppState,create_app
    from _gql import data,err_status
    monkeypatch.setenv('TERMX_CONFIG_DIR',str(tmp_path/'config'));monkeypatch.setenv('TERMX_AGENTS_DIR',str(tmp_path/'agents'));monkeypatch.setenv('TERMX_ENGINE_STARTUP_REFRESH','0')
    state=AppState(passcode=None);owner=state.identity.setup_owner('gql-owner','graphql-fixture-password-123')
    project=state.projects.register(str(tmp_path),name='GraphQL policy fixture')
    login=asyncio.run(state.identity.login('local-password',{'username':'gql-owner','password':'graphql-fixture-password-123'},peer='fixture'))
    own=state.agent_store.create_policy_rule(effect='allow',scope_type='project',scope_id=project['id'],action_type='tool',tool='write_file',fingerprint='exact',project_id=project['id'],consent_binding=binding(owner.id,owner.policy_version),expires_at=time()+300)
    foreign=state.agent_store.create_policy_rule(effect='deny',scope_type='project',scope_id=project['id'],action_type='tool',tool='write_file',fingerprint='foreign',project_id=project['id'],consent_binding=binding('other-user'),expires_at=time()+300)
    with TestClient(create_app(state,web_dir=None)) as client:
        headers={'Authorization':'Bearer '+login.access_token}
        listed=data(client,'{ agent_policies { id } }','agent_policies',{},headers)
        assert own['id'] in [r['id'] for r in listed] and foreign['id'] not in [r['id'] for r in listed]
        patch='mutation($id:String!){patch_agent_policy(rule_id:$id,input:{effect:"deny",capabilities:["net.outbound:any"]}){id}}'
        assert err_status(client,patch,{'id':own['id']},headers)==403
        revoke='mutation($id:String!){revoke_agent_policy(rule_id:$id){ok}}'
        assert err_status(client,revoke,{'id':own['id']},headers)==403
        assert state.agent_store.get_policy_rule(own['id'])['version']==1 and not state.agent_store.get_policy_rule(own['id'])['revoked_at']


def test_graphql_accepts_canonical_conversation_consent_and_keeps_exact_host_validation(tmp_path, monkeypatch):
    from termx.app import AppState, create_app
    from termx.sandbox.host import HostSandboxRunner
    from test_agent import _FanOutAdapter, _fn, wait_for_pending_approval, wait_for_status
    from _gql import data, err_status

    monkeypatch.setenv('TERMX_CONFIG_DIR', str(tmp_path/'config'))
    monkeypatch.setenv('TERMX_AGENTS_DIR', str(tmp_path/'agents'))
    monkeypatch.setenv('TERMX_ENGINE_STARTUP_REFRESH', '0')
    state = AppState(passcode=None)
    state.identity.setup_owner('consent-owner', 'consent-fixture-password-123')
    login = asyncio.run(state.identity.login('local-password', {'username':'consent-owner','password':'consent-fixture-password-123'}, peer='fixture'))
    origin = state.identity.resolve(login.access_token)
    project = state.projects.register(str(tmp_path), name='Consent fixture')
    adapter = _FanOutAdapter({'Exact edit': ([[_fn('write', 'write_file', path='owned.py', content='VALUE = 1\n')]], 0.)})
    state.agent._adapter_factory = lambda *_: adapter
    state.agent._runner_for = lambda profile, **kw: HostSandboxRunner(profile=profile)
    from termx.agent.secrets import CredentialStore
    state.credentials = CredentialStore(memory={})
    state.agent.credentials = state.credentials
    state.agent.save_provider(provider_id='fixture', kind='openai-compatible', name='No-network fixture', base_url='http://127.0.0.1:9999/v1', model='fixture', capabilities=['shell'], api_key='fixture')
    mutation='mutation($task:String!,$approval:String!,$input:AgentApprovalInput!){resolve_agent_approval(task_id:$task,approval_id:$approval,input:$input)}'
    with TestClient(create_app(state, web_dir=None)) as client:
        conversation = state.workspace.create_session(origin.principal, title='Exact edit', project_id=project['id'], cwd=str(tmp_path), provider_id='fixture', model='fixture', mode='agent')
        async def start():
            return await state.workspace.send(origin.principal, conversation['id'], prompt='Exact edit', request_id='graphql-exact-consent', managed_session_id=origin.session_id)
        task = client.portal.call(start)
        headers={'Authorization':'Bearer '+login.access_token}
        variables={'task':task['id'],'approval':task['approvals'][0]['id'],'input':{'decision':'approved'}}
        data(client, mutation, 'resolve_agent_approval', variables, headers)
        _, approval = client.portal.call(wait_for_pending_approval, state.agent_store, task['id'], 'tool')
        assert 'conversation' in approval['payload']['remember_options']
        variables['approval']=approval['id'];variables['input']={'decision':'approved','remember':'always'}
        assert err_status(client, mutation, variables, headers)==409
        assert state.agent_store.get_approval(approval['id'])['status']=='pending' and not (tmp_path/'owned.py').exists()
        variables['input']={'decision':'approved','remember':'conversation'}
        data(client, mutation, 'resolve_agent_approval', variables, headers)
        client.portal.call(wait_for_status, state.agent_store, task['id'], 'completed')
        assert (tmp_path/'owned.py').read_text()=='VALUE = 1\n'
        rule=state.agent_store.list_policy_rules()[0]
        assert rule['scope_type']=='conversation' and rule['scope_id']==conversation['id']
        assert rule['consent_binding']['session_id']==origin.session_id
        assert len([event for event in state.agent_store.events(task['id']) if event['type']=='tool.finished'])==1


def test_public_policy_metadata_recursively_redacts_without_changing_matcher_authority(ledger):
    from termx.agent.policies.models import rule_public
    matcher={'nested':{'password':'plain-private-value','safe':'command token=private-token'}, 'items':[{'Authorization':'Bearer private-bearer','target':'owned.py'}], 'arguments_digest':'exact-digest'}
    row=ledger.create_policy_rule(effect='allow', scope_type='host', scope_id=None, action_type='tool', tool='write_file', fingerprint='exact', matcher=matcher)
    public=rule_public(row)
    public_intent=replace(intent(binding()), matcher=matcher).to_public()
    for view in (public, public_intent):
        assert 'plain-private-value' not in str(view) and 'private-token' not in str(view) and 'private-bearer' not in str(view)
        assert view['matcher']['arguments_digest']=='exact-digest' and view['matcher']['items'][0]['target']=='owned.py'
    assert ledger.get_policy_rule(row['id'])['matcher']==matcher
