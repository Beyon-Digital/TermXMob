"""Bounded owner rule edits use real managed auth and canonical task/grant state."""
import asyncio
from dataclasses import replace
from time import time

import pytest
from fastapi.testclient import TestClient

from termx.auto_review import ActionEnvelope,ActionBlocked,canonical_hash

PASSWORD='isolated-remembered-rule-password-123'

@pytest.fixture
def setup(tmp_path,monkeypatch):
    from termx.app import AppState,create_app
    monkeypatch.setenv('TERMX_CONFIG_DIR',str(tmp_path/'config'))
    state=AppState(passcode=None)
    owner=state.identity.setup_owner('owner',PASSWORD)
    login=asyncio.run(state.identity.login('local-password',{'username':'owner','password':PASSWORD},peer='localhost'))
    project=state.projects.register(str(tmp_path),name='Rule fixture')
    client=TestClient(create_app(state),base_url='https://localhost')
    task=state.agent_store.create_task(prompt='Rule fixture',cwd=str(tmp_path),provider_id='fixture',model='fixture',limits={},status='running')
    authority={'id':task['id'],'principal_id':owner.id,'session_id':login.session_id,'project_id':project['id'],'policy_version':owner.policy_version}
    state.browser.records.put('agent-task-authority',task['id'],authority)
    envelope=ActionEnvelope('rule-source',owner.id,login.session_id,project['id'],task['id'],'agent.write_file',canonical_hash({'file':'safe.txt'}),str(tmp_path),'edit','task:'+task['id'],owner.policy_version)
    rule=state.browser.review.rule(envelope,'BLOCK',expires_at=time()+3600)
    yield state,client,{'Authorization':'Bearer '+login.access_token},login,envelope,rule
    asyncio.run(state.browser.close())
    state.agent_store.close()

def edit(client,headers,rule,**body):
    return client.patch('/api/browser/rules/'+rule['id'],headers=headers,json={'decision':'ALLOW','expires_in':600,'revision':rule.get('revision',1),**body})

def test_owner_edits_only_decision_expiry_and_inspects_exact_scope(setup):
    state,client,headers,_,envelope,rule=setup
    response=edit(client,headers,rule)
    assert response.status_code==200,response.text
    value=response.json();assert value['scope']==rule['scope'] and value['id']==rule['id'] and value['decision']=='ALLOW'
    assert time()+590<value['expires_at']<time()+610
    assert any(event.get('rule_id')==rule['id'] for event in state.browser.records.events())
    assert client.get('/api/browser/rules',headers=headers).json()[0]['scope']==rule['scope']
    assert edit(client,headers,rule).status_code==400
    assert state.browser.records.get('review-rule',rule['id'])['decision']=='ALLOW'
    assert edit(client,headers,rule,scope={**rule['scope'],'target':'https://foreign.example'}).status_code==422
    for seconds in [0,-1,2592001]:assert edit(client,headers,rule,expires_in=seconds).status_code==422
    assert client.delete('/api/browser/rules/'+rule['id'],headers=headers).status_code==200
    assert not state.browser.records.list('review-rule')

@pytest.mark.parametrize('mutation',['session','policy','task','authority','expired'])
def test_changed_live_authority_cannot_edit_existing_rule(setup,mutation):
    state,client,headers,login,envelope,rule=setup
    if mutation=='session':state.identity.revoke(login.session_id,envelope.principal_id)
    elif mutation=='policy':state.authorization.grant_project(envelope.principal_id,envelope.project_id,['desktop-control'])
    elif mutation=='task':state.agent_store.update_task(envelope.run_id,status='completed')
    elif mutation=='authority':state.browser.records.delete('agent-task-authority',envelope.run_id)
    else:state.browser.records.put('review-rule',rule['id'],{**rule,'expires_at':time()-1})
    assert edit(client,headers,rule).status_code==(401 if mutation=='session' else 403)
    assert state.browser.records.get('review-rule',rule['id'])['decision']=='BLOCK'

def test_foreign_admin_and_new_session_cannot_borrow_rule_scope(setup):
    state,client,headers,login,envelope,rule=setup
    other=state.identity.create_local_user('other',PASSWORD,['desktop-view','desktop-control','host-admin'])
    state.authorization.set_role(other.id,'admin')
    foreign=asyncio.run(state.identity.login('local-password',{'username':'other','password':PASSWORD},peer='localhost'))
    assert edit(client,{'Authorization':'Bearer '+foreign.access_token},rule).status_code==404
    fresh=asyncio.run(state.identity.login('local-password',{'username':'owner','password':PASSWORD},peer='localhost'))
    assert edit(client,{'Authorization':'Bearer '+fresh.access_token},rule).status_code==403
    assert edit(client,headers,rule).status_code==200

def test_edited_allow_never_overrides_another_deny_or_current_hard_rule(setup):
    state,client,headers,login,envelope,rule=setup
    other=state.browser.review.rule(envelope,'BLOCK',expires_at=time()+3600)
    assert edit(client,headers,rule).status_code==200
    async def proof():
        with pytest.raises(ActionBlocked,match='remembered deny'):
            await state.browser.review.authorize(replace(envelope,action_id='first-effect'),validate=lambda:True)
        state.browser.review.revoke_rule(other['id'])
        with pytest.raises(ActionBlocked,match='inherited host deny'):
            await state.browser.review.authorize(replace(envelope,action_id='second-effect'),validate=lambda:True,hard_deny='inherited host deny')
    asyncio.run(proof())
    assert all(row['status']=='blocked' for row in state.browser.records.list('review'))

def test_browser_rule_edit_requires_same_live_tab_grant_origin_and_action(setup):
    state,client,headers,login,envelope,rule=setup
    profile=state.browser.create_profile(envelope.principal_id,envelope.project_id,'Fixture profile')
    tab={'id':'rule-tab','principal_id':envelope.principal_id,'project_id':envelope.project_id,'profile_id':profile['id'],'state':'agent','grant_id':'rule-grant','lease_revision':1}
    grant={'id':'rule-grant','tab_id':tab['id'],'principal_id':envelope.principal_id,'session_id':login.session_id,'project_id':envelope.project_id,'profile_id':profile['id'],'run_id':envelope.run_id,'policy_version':envelope.policy_version,'lease_revision':1,'revoked':False,'expires_at':time()+600,'origins':['https://example.test'],'actions':['observe']}
    state.browser.records.put('tab',tab['id'],tab);state.browser.records.put('grant',grant['id'],grant)
    browser=replace(envelope,tool_id='browser.observe',target='https://example.test',intended_effect='observe',grant_id=grant['id'],profile_id=profile['id'])
    rule=state.browser.review.rule(browser,'BLOCK',expires_at=time()+600)
    assert edit(client,headers,rule).status_code==200
    state.browser.records.put('grant',grant['id'],{**grant,'revoked':True})
    assert edit(client,headers,{**rule,'revision':2},decision='BLOCK').status_code==403
    state.browser.records.put('grant',grant['id'],{**grant,'origins':['https://other.test']})
    assert edit(client,headers,{**rule,'revision':2}).status_code==403
    state.browser.records.put('grant',grant['id'],{**grant,'actions':['scroll']})
    assert edit(client,headers,{**rule,'revision':2}).status_code==403

def test_sensitive_deny_cannot_be_changed_to_blanket_allow(setup):
    state,client,headers,_,envelope,_=setup
    rule=state.browser.review.rule(replace(envelope,intended_effect='publish'),'BLOCK',expires_at=time()+600)
    assert edit(client,headers,rule).status_code==400
    assert state.browser.records.get('review-rule',rule['id'])['decision']=='BLOCK'
