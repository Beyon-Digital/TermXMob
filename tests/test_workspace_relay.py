from fastapi.testclient import TestClient
import asyncio
from termx.app import AppState,create_app

def test_node_identity_cursor_connections_and_cross_user_isolation(tmp_path,monkeypatch):
    monkeypatch.setenv('TERMX_CONFIG_DIR',str(tmp_path/'config'))
    state=AppState(passcode=None);alice=state.identity.setup_owner('alice','strong-password-fixture')
    creds=asyncio.run(state.identity.login('local-password',{'username':'alice','password':'strong-password-fixture'},peer='127.0.0.1',device_name='test'))
    client=TestClient(create_app(state))
    session=state.workspace.create_session(alice,title='Node fixture',cwd=str(tmp_path))
    for index in range(5):state.agent_store.add_conversation_turn(session['id'],prompt=f'Prompt {index}')
    headers={'Authorization':'Bearer '+creds.access_token}
    def query(text,variables=None):return client.post('/graphql',headers=headers,json={'query':text,'variables':variables or {}}).json()
    result=query('{workspace_sessions(first:1){edges{node{id canonical_id title}}pageInfo{hasNextPage}}}')
    assert not result.get('errors'),result
    node=result['data']['workspace_sessions']['edges'][0]['node']
    assert node['canonical_id']==session['id'] and node['id']!=session['id']
    loaded=query('query($id:ID!){node(id:$id){id ... on WorkspaceSessionNode{canonical_id title}}}',{'id':node['id']})
    assert loaded['data']['node']['canonical_id']==session['id'],loaded
    page=query('query($id:String!){workspace_turns(session_id:$id,first:2){edges{cursor node{id canonical_id prompt sequence}}pageInfo{endCursor hasNextPage}}}',{'id':session['id']})
    assert not page.get('errors'),page
    info=page['data']['workspace_turns']['pageInfo'];assert info['hasNextPage']
    continuation=query('query($id:String!,$after:String){workspace_turns(session_id:$id,first:2,after:$after){edges{node{sequence}}pageInfo{hasNextPage}}}',{'id':session['id'],'after':info['endCursor']})
    assert [edge['node']['sequence'] for edge in continuation['data']['workspace_turns']['edges']]==[3,4]
    latest=query('query($id:String!){workspace_turns(session_id:$id,last:2){edges{node{sequence}}pageInfo{startCursor hasPreviousPage hasNextPage}}}',{'id':session['id']})
    assert [edge['node']['sequence'] for edge in latest['data']['workspace_turns']['edges']]==[4,5]
    older=query('query($id:String!,$before:String){workspace_turns(session_id:$id,last:2,before:$before){edges{node{sequence}}pageInfo{hasPreviousPage}}}',{'id':session['id'],'before':latest['data']['workspace_turns']['pageInfo']['startCursor']})
    assert [edge['node']['sequence'] for edge in older['data']['workspace_turns']['edges']]==[2,3]
    stranger=state.identity.create_local_user('bob','strong-password-fixture',list(alice.scopes))
    other=asyncio.run(state.identity.login('local-password',{'username':'bob','password':'strong-password-fixture'},peer='127.0.0.1',device_name='other'))
    denied=client.post('/graphql',headers={'Authorization':'Bearer '+other.access_token},json={'query':'query($id:ID!){node(id:$id){id}}','variables':{'id':node['id']}}).json()
    assert denied['data']['node'] is None

def test_session_cursor_snapshot_survives_recent_order_changes(tmp_path):
    state=AppState();owner=state.identity.setup_owner('owner','strong-password-fixture')
    token=asyncio.run(state.identity.login('local-password',{'username':'owner','password':'strong-password-fixture'},peer='local')).access_token
    client=TestClient(create_app(state));headers={'Authorization':'Bearer '+token}
    rows=[state.workspace.create_session(owner,title=str(index),cwd=str(tmp_path)) for index in range(4)]
    query='query($after:String){workspace_sessions(first:2,after:$after){edges{node{canonical_id}cursor}pageInfo{endCursor hasNextPage}}}'
    def page(after=None):return client.post('/graphql',headers=headers,json={'query':query,'variables':{'after':after}}).json()['data']['workspace_sessions']
    first=page()
    state.workspace.update_session(owner,rows[0]['id'],revision=1,changes={'pinned':True})
    second=page(first['pageInfo']['endCursor'])
    ids=[edge['node']['canonical_id'] for part in [first,second] for edge in part['edges']]
    assert len(set(ids))==4 and set(ids)=={row['id'] for row in rows}


def test_slow_projection_keeps_host_loop_responsive_and_rechecks_revocation(tmp_path,monkeypatch):
    import threading
    from termx.graphql.context import TermxContext
    from termx.graphql.schema import schema
    state=AppState();owner=state.identity.setup_owner('owner','strong-password-fixture')
    token=asyncio.run(state.identity.login('local-password',{'username':'owner','password':'strong-password-fixture'},peer='local')).access_token
    create_app(state)
    state.workspace.create_session(owner,title='Private fixture',cwd=str(tmp_path))
    entered,release=threading.Event(),threading.Event()
    original=state.workspace.sessions
    def slow(*args,**kwargs):
        rows=original(*args,**kwargs)
        entered.set()
        assert release.wait(2),'Projection worker was never released'
        return rows
    monkeypatch.setattr(state.workspace,'sessions',slow)
    async def run():
        task=asyncio.create_task(schema.execute('{workspace_sessions{edges{node{canonical_id}}}}',context_value=TermxContext(state,token)))
        try:
            for _ in range(100):
                if entered.is_set():break
                await asyncio.sleep(.01)
            assert entered.is_set() and not task.done(),'Session projection blocked the host event loop'
            state.identity.disable(owner.id)
        finally:
            release.set()
        result=await task
        assert result.errors and not (result.data or {}).get('workspace_sessions')
    asyncio.run(run())
