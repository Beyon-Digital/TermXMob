"""Managed GraphQL preset provenance and foreign/private selection boundary."""
import asyncio
import pytest
from test_authorization import client_fixture, PASSWORD
from termx.authorization import ROLES
from termx.agents.files import AgentFile


def query(client,headers,document,variables=None):
    response=client.post('/graphql',headers=headers,json={'query':document,'variables':variables or {}})
    assert response.status_code==200,response.text
    return response.json()

def denied(result,status=403):
    assert result.get('errors'),result
    assert result['errors'][0].get('extensions',{}).get('http_status')==status,result


def test_graphql_custom_agent_claims_and_live_private_lists(tmp_path):
    identity,state,client,owner=client_fixture(tmp_path)
    alice=identity.create_local_user('alice',PASSWORD,list(ROLES['operator']))
    bob=identity.create_local_user('bob',PASSWORD,list(ROLES['operator']))
    for principal in (alice,bob):state.authorization.set_role(principal.id,'operator')
    def headers(name):
        token=asyncio.run(identity.login('local-password',{'username':name,'password':PASSWORD},peer='local')).access_token
        return {'Authorization':'Bearer '+token}
    created=query(client,owner,'mutation { create_custom_agent(input:{name:"Owner private",instructions:"private text"}) { id } }')
    assert not created.get('errors'),created
    identifier=created['data']['create_custom_agent']['id']
    owning=state.authorization.resource_owner('custom_agent',identifier)
    assert owning and owning['principal_id']==identity.resolve(owner['Authorization'][7:]).principal.id
    private=state.agent_registry.save(AgentFile(slug='alice-private',name='Alice private',instructions='ALICE_ONLY'))
    state.authorization.claim_principal(alice,'custom_agent',private.qid_id)
    installed=state.agent_registry.save(AgentFile(slug='legacy-installed',name='Legacy installed',instructions='Shared'),source='device')
    scoped=state.agent_registry.save(AgentFile(slug='legacy-scoped',name='Legacy scoped',instructions='Unclaimed private'),source='device')
    # A file scope marker never turns into an invented legacy ownership claim.
    original=state.agent_store.get_custom_agent(scoped.qid_id)
    with state.agent_store._lock:
        import json
        source=original['file'];source['owner_id']=alice.id
        state.agent_store._db.execute('UPDATE custom_agents SET file_json=? WHERE id=?',(json.dumps(source),scoped.qid_id))
        state.agent_store._db.commit()
    document='query { custom_agents { id instructions } }'
    for name,expected in [('alice',{private.qid_id,installed.qid_id}),('bob',{installed.qid_id})]:
        result=query(client,headers(name),document)
        assert not result.get('errors'),result
        assert {row['id'] for row in result['data']['custom_agents']}==expected
    assert state.authorization.resource_owner('custom_agent',installed.qid_id) is None
    foreign=query(client,headers('bob'),'query($id:String!){custom_agent(agent_id:$id){id instructions}}',{'id':private.qid_id})
    denied(foreign)
    assert foreign['data'] is None
    forbidden_binding=query(client,headers('bob'),'mutation($id:String!){create_conversation(input:{custom_agent_id:$id}){id}}',{'id':private.qid_id})
    denied(forbidden_binding)
    root=tmp_path/'project';root.mkdir()
    project=state.projects.register(str(root))
    state.authorization.grant_project(bob.id,project['id'],['agent-view','agent-run','agent-control'])
    state.authorization.set_role(bob.id,'operator',trusted_execution=True)
    tasks_before=state.agent_store.list_tasks()
    forbidden_execution=query(client,headers('bob'),
        'mutation($id:String!,$cwd:String!){create_agent_task(input:{prompt:"must not dispatch",cwd:$cwd,custom_agent_id:$id}){id}}',
        {'id':private.qid_id,'cwd':str(root)})
    denied(forbidden_execution)
    assert state.agent_store.list_tasks()==tasks_before
    policies=query(client,headers('bob'),
        'query($id:String!,$project:String!){effective_agent_policies(project_id:$project,custom_agent_id:$id){approval_mode}}',
        {'id':private.qid_id,'project':project['id']})
    denied(policies)
    duplicated=query(client,owner,'mutation($id:String!){duplicate_custom_agent(agent_id:$id){id}}',{'id':identifier})
    assert not duplicated.get('errors'),duplicated
    assert state.authorization.resource_owner('custom_agent',duplicated['data']['duplicate_custom_agent']['id'])['principal_id']==owning['principal_id']
    imported=query(client,owner,'mutation($text:String!){import_custom_agent(input:{markdown:$text}){id}}',
        {'text':'---\nname: Imported private\n---\nImported instructions\n'})
    assert not imported.get('errors'),imported
    assert state.authorization.resource_owner('custom_agent',imported['data']['import_custom_agent']['id'])['principal_id']==owning['principal_id']
    forbidden_delete=query(client,headers('alice'),'mutation($id:String!){delete_custom_agent(agent_id:$id){deleted}}',{'id':identifier})
    denied(forbidden_delete)
    assert state.agent_store.get_custom_agent(identifier)
    collision=query(client,owner,'mutation { create_custom_agent(input:{name:"Owner private",instructions:"overwrite"}) { id } }')
    denied(collision,409)
    assert state.agent_store.get_custom_agent(identifier)['instructions']=='private text'
    alice_headers=headers('alice')
    identity.disable(alice.id)
    denied(query(client,alice_headers,document),401)
    denied(query(client,headers('bob'),'query($id:String!){custom_agent_export(agent_id:$id){markdown}}',{'id':private.qid_id}))


def test_resource_provenance_lookup_never_claims_or_grants(tmp_path):
    identity,state,client,owner=client_fixture(tmp_path)
    assert state.authorization.resource_owner('custom_agent','never-created') is None
    with pytest.raises(ValueError):state.authorization.resource_owner('custom_agent','x'*257)


def test_import_and_duplicate_refuse_foreign_tombstones_before_file_effect(tmp_path):
    identity,state,client,owner=client_fixture(tmp_path)
    bob=identity.create_local_user('bob',PASSWORD,list(ROLES['operator']))
    state.authorization.set_role(bob.id,'operator')
    state.authorization.claim_principal(bob,'custom_agent','agent.tombstone')
    imported=query(client,owner,
        'mutation($text:String!){import_custom_agent(input:{markdown:$text,source:"device"}){id}}',
        {'text':'---\nname: Tombstone\n---\nPRIVATE_IMPORT_MUST_NOT_PUBLISH\n'})
    denied(imported,409)
    assert state.agent_registry.read_raw('tombstone') is None
    assert state.agent_store.get_custom_agent('agent.tombstone') is None
    assert state.authorization.resource_owner('custom_agent','agent.tombstone')['principal_id']==bob.id
    created=query(client,owner,'mutation { create_custom_agent(input:{name:"Owned source",instructions:"private source"}) { id } }')
    assert not created.get('errors'),created
    state.authorization.claim_principal(bob,'custom_agent','agent.owned-source-copy')
    duplicate=query(client,owner,'mutation($id:String!){duplicate_custom_agent(agent_id:$id){id}}',
        {'id':created['data']['create_custom_agent']['id']})
    denied(duplicate,409)
    assert state.agent_registry.read_raw('owned-source-copy') is None
    assert state.agent_store.get_custom_agent('agent.owned-source-copy') is None


def test_managed_revocation_during_claim_cannot_publish_shared_source(tmp_path,monkeypatch):
    identity,state,client,owner=client_fixture(tmp_path)
    live=identity.resolve(owner['Authorization'][7:])
    original=state.authorization.claim
    def revoke_then_claim(*args,**kwargs):
        identity.revoke(live.session_id,live.principal.id)
        return original(*args,**kwargs)
    monkeypatch.setattr(state.authorization,'claim',revoke_then_claim)
    imported=query(client,owner,
        'mutation($text:String!){import_custom_agent(input:{markdown:$text,source:"device"}){id}}',
        {'text':'---\nname: Revoked import\n---\nMUST_NOT_PUBLISH\n'})
    denied(imported,401)
    assert state.agent_registry.read_raw('revoked-import') is None
    assert state.agent_store.get_custom_agent('agent.revoked-import') is None
