from __future__ import annotations

import asyncio
import json

from fastapi.testclient import TestClient
from termx.app import AppState, create_app


def fixture(tmp_path):
    state = AppState(passcode=None)
    owner = state.identity.setup_owner('artifact-owner', 'artifact-fixture-password-123')
    credentials = asyncio.run(state.identity.login('local-password', {'username':'artifact-owner','password':'artifact-fixture-password-123'}, peer='fixture'))
    client = TestClient(create_app(state), headers={'Authorization':'Bearer '+credentials.access_token})
    session = state.workspace.create_session(owner, cwd=str(tmp_path))
    return state, owner, client, session


def saved_task(state, owner, session, index):
    task = state.agent_store.create_task(prompt='Generated artifact '+str(index), cwd=session['cwd'], provider_id='fixture', model='fixture', limits={}, mode='ask')
    state.agent_store.update_task(task['id'], status='completed')
    state.workspace.store.create('task', owner.id, {'conversation_id':session['id'], 'cwd':session['cwd']}, None, task['id'])
    state.authorization.claim_principal(owner, 'task', task['id'])
    state.agent_store.add_conversation_turn(session['id'], prompt=task['prompt'], task_id=task['id'])
    return task


def query(client, session):
    return client.post('/graphql', json={'query':'query($id:String!){workspace_turns(session_id:$id,last:200){edges{node{task}}}}', 'variables':{'id':session['id']}}).json()


def test_graphql_history_batches_bounded_public_artifacts_and_excludes_foreign_tasks(tmp_path, monkeypatch):
    state, owner, client, session = fixture(tmp_path)
    try:
        for index in range(9):
            task = saved_task(state, owner, session, index)
            for number in range(40):
                state.agent_store.save_artifact(task['id'], 'generated', 'image/png', str(number).encode())
        foreign = state.identity.create_local_user('artifact-stranger', 'artifact-fixture-password-123', list(owner.scopes))
        other = state.agent_store.create_task(prompt='Other account', cwd=session['cwd'], provider_id='fixture', model='fixture', limits={})
        state.workspace.store.create('task', foreign.id, {'conversation_id':session['id'], 'cwd':session['cwd']}, None, other['id'])
        state.authorization.claim_principal(foreign, 'task', other['id'])
        private = state.agent_store.save_artifact(other['id'], 'generated', 'image/png', b'foreign-private-artifact')
        state.agent_store.add_conversation_turn(session['id'], prompt='Forged cross-account turn', task_id=other['id'])
        original = state.agent_store.workspace_artifacts_snapshot
        calls = []
        def capture(task_ids, **kwargs):
            calls.append(list(task_ids))
            return original(task_ids, **kwargs)
        monkeypatch.setattr(state.agent_store, 'workspace_artifacts_snapshot', capture)
        response = query(client, session)
        assert not response.get('errors'), response
        tasks = [edge['node']['task'] for edge in response['data']['workspace_turns']['edges']]
        assert len(calls) == 1 and len(calls[0]) == 9
        assert sum(len(task['artifacts']) for task in tasks) == 256
        assert all(len(task['artifacts']) <= 32 for task in tasks)
        assert all(task['artifacts_truncated'] for task in tasks[:-1])
        assert tasks[-1]['artifacts'] == []
        for task in tasks:
            for artifact in task['artifacts']:
                assert set(artifact) == {'id','task_id','kind','mime','size'}
        assert private['id'] not in json.dumps(response)
        assert str(state.agent_store.artifact_dir) not in json.dumps(response)
    finally:
        client.close()
        asyncio.run(state.workspace.close())
        asyncio.run(state.agent.close())
        state.agent_store.close()


def test_graphql_artifact_batch_rechecks_revocation_before_publication(tmp_path, monkeypatch):
    state, owner, client, session = fixture(tmp_path)
    try:
        task = saved_task(state, owner, session, 0)
        state.agent_store.save_artifact(task['id'], 'generated', 'image/png', b'private-output')
        original = state.agent_store.workspace_artifacts_snapshot
        def revoke(task_ids, **kwargs):
            result = original(task_ids, **kwargs)
            state.identity.disable(owner.id)
            return result
        monkeypatch.setattr(state.agent_store, 'workspace_artifacts_snapshot', revoke)
        response = query(client, session)
        assert response.get('errors') and not (response.get('data') or {}).get('workspace_turns')
    finally:
        client.close()
        asyncio.run(state.workspace.close())
        asyncio.run(state.agent.close())
        state.agent_store.close()
