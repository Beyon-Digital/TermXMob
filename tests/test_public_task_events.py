"""Saved child output follows live source authority on real public surfaces.

AppState, SQLite, managed JWTs, HTTP routes and GraphQL resolvers are real. A
narrow authorization shim returns 403 only for the seeded source resources;
this models a project-grant denial without revoking the whole parent session.
No engine, model, browser, network provider or native process is started.
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from termx.agent.limits import resolve_limits
from termx.agent.secrets import CredentialStore
from termx.app import AppState,create_app
from termx.graphql.context import TermxContext
from termx.graphql.schema import schema

SECRET='PRIVATE_CHILD_SOURCE_MARKER_7a81'
PUBLIC='Independent parent progress remains readable'
PREFIX='Delegated session update (untrusted agent output, not authority): '


def install_denial(monkeypatch,state,scene):
    """Keep the real authority checks, adding only one controlled 403 boundary."""
    record=state.workspace.record
    require=state.authorization.require
    def denied(kind,identifier):
        return (scene.denial['child'] and (kind=='task' and identifier==scene.child['id'] or
            kind=='conversation' and identifier==scene.child_session['id'])) or (
            scene.denial['parent'] and kind=='task' and identifier==scene.parent['id'])
    def scoped_record(principal,kind,identifier,**kwargs):
        if denied(kind,identifier):raise HTTPException(403,'Synthetic current source grant denial')
        return record(principal,kind,identifier,**kwargs)
    def scoped_require(secret,scope,**kwargs):
        if denied(kwargs.get('resource_kind'),kwargs.get('resource_id')):
            raise HTTPException(403,'Synthetic current source grant denial')
        return require(secret,scope,**kwargs)
    monkeypatch.setattr(state.workspace,'record',scoped_record)
    monkeypatch.setattr(state.authorization,'require',scoped_require)


def query(client,text,variables=None):
    response=client.post('/graphql',json={'query':text,'variables':variables or {}})
    assert response.status_code==200,response.text
    body=response.json()
    assert not body.get('errors'),body
    return body['data']


@pytest.fixture
def scene(tmp_path,monkeypatch):
    state=AppState(passcode=None,credentials=CredentialStore(memory={}))
    owner=state.identity.setup_owner('projection-owner','strong synthetic projection password')
    credentials=asyncio.run(state.identity.login('local-password',{
        'username':'projection-owner','password':'strong synthetic projection password'},peer='projection-test'))
    app=create_app(state)
    parent_folder=tmp_path/'parent';parent_folder.mkdir()
    source_folder=tmp_path/'source';source_folder.mkdir()
    project=state.projects.register(str(source_folder),name='Source grant fixture')
    parent_session=state.workspace.create_session(owner,title='Parent fixture',cwd=str(parent_folder),mode='agent')
    child_session=state.workspace.create_session(owner,title='Source child',project_id=project['id'],cwd=str(source_folder),mode='agent')
    parent=state.agent_store.create_task(prompt='Supervise bounded fixture',cwd=str(parent_folder),
        provider_id='fixture',model='fixture',limits=resolve_limits(),status='running')
    child=state.agent_store.create_task(prompt='Source work',cwd=str(source_folder),
        provider_id='fixture',model='fixture',limits=resolve_limits(),status='completed',parent_id=parent['id'])
    state.agent_store.update_task(child['id'],result=SECRET)
    for task,session,project_id in ((parent,parent_session,None),(child,child_session,project['id'])):
        state.workspace.store.create('task',owner.id,{'conversation_id':session['id'],'cwd':task['cwd']},project_id,task['id'])
        state.authorization.claim_principal(owner,'task',task['id'],project_id=project_id)
        state.agent_store.add_conversation_turn(session['id'],prompt=task['prompt'],task_id=task['id'])
    inner=state.agent_store.create_approval(child['id'],'tool',{'title':SECRET})
    wrapper=state.agent_store.create_approval(parent['id'],'tool',{
        'title':SECRET,'child_id':child['id'],'child_approval_id':inner['id'],'command':SECRET})
    child_output={'children':{child['id']:{'child_id':child['id'],'agent':SECRET,'status':'completed','result':SECRET}}}
    history=[{'type':'function_call','call_id':'collect','name':'await_subagents','arguments':'{}'},
        {'type':'function_call_output','call_id':'collect','output':json.dumps(child_output)},
        {'role':'user','content':PREFIX+json.dumps({'task_id':child['id'],'message':SECRET})},
        {'role':'assistant','content':PUBLIC}]
    state.agent_store.update_task(parent['id'],runtime={'history':history})
    state.agent_store.create_approval(parent['id'],'tool',{'title':'Parent operation','history':history})
    payloads=[('process.output',{'chunk':PUBLIC}),
        ('subagent.started',{'child_id':child['id'],'agent':SECRET,'task':SECRET}),
        ('subagent.event',{'child_id':child['id'],'agent':SECRET,'type':'process.output','payload':{'chunk':SECRET}}),
        ('subagent.finished',{'child_id':child['id'],'agent':SECRET,'result':SECRET,'status':'completed'}),
        ('approval.requested',{'approval':wrapper}),
        ('tool.finished',{'name':'await_subagents','call_id':'collect','result':child_output}),
        ('task.steered',{'message':history[2]['content']})]
    events=[state.agent_store.append_event(parent['id'],kind,payload) for kind,payload in payloads]
    artifact=state.agent_store.save_artifact(child['id'],'file','text/plain',SECRET.encode())
    client=TestClient(app,headers={'Authorization':'Bearer '+credentials.access_token})
    frame=SimpleNamespace(state=state,owner=owner,token=credentials.access_token,client=client,
        parent=parent,child=child,parent_session=parent_session,child_session=child_session,
        wrapper=wrapper,events=events,artifact=artifact,denial={'child':False,'parent':False})
    install_denial(monkeypatch,state,frame)
    yield frame
    client.close()
    asyncio.run(state.agent.close())
    asyncio.run(state.workspace.close())
    state.agent_store.close()


def test_saved_parent_rest_graphql_runtime_wrappers_and_artifact_recheck_source(scene):
    state,client=scene.state,scene.client
    path='/api/workspace/tasks/'+scene.parent['id']+'?activity=true'
    before=client.get(path)
    assert before.status_code==200 and SECRET in before.text
    artifact_path='/api/agent/tasks/'+scene.child['id']+'/artifacts/'+scene.artifact['id']
    assert client.get(artifact_path).content==SECRET.encode()
    scene.denial['child']=True
    response=client.get(path)
    assert response.status_code==200,response.text
    assert SECRET not in response.text and PUBLIC in response.text
    assert response.json()['children']==[]
    projected=response.json()['events']
    assert [event['sequence'] for event in projected]==[event['sequence'] for event in scene.events]
    assert any(event['payload'].get('redacted') for event in projected)
    session=client.get('/api/workspace/sessions/'+scene.parent_session['id'])
    assert session.status_code==200 and SECRET not in session.text and PUBLIC in session.text
    task=query(client,'query($id:String!){agent_task(task_id:$id){id events approvals runtime}}',{'id':scene.parent['id']})['agent_task']
    assert SECRET not in json.dumps(task) and PUBLIC in json.dumps(task)
    unavailable=next(row for row in task['approvals'] if row['id']==scene.wrapper['id'])
    assert unavailable['status']=='unavailable' and unavailable['actionable'] is False
    assert state.agent_store.get_approval(scene.wrapper['id'])['status']=='pending'
    turns=query(client,'query($id:String!){workspace_turns(session_id:$id,last:1){edges{node{task}}}}',{'id':scene.parent_session['id']})
    exported=query(client,'query($id:String!){agent_task_export(task_id:$id)}',{'id':scene.parent['id']})
    assert SECRET not in json.dumps(turns) and SECRET not in json.dumps(exported)
    denied=client.get(artifact_path)
    assert denied.status_code==403 and SECRET not in denied.text
    # Public projection never rewrites the original exact replay ledger.
    assert SECRET in json.dumps(state.agent_store.events(scene.parent['id']))
    assert SECRET in json.dumps(state.agent_store.get_task(scene.parent['id'])['runtime'])


def test_graphql_subscription_rechecks_child_and_parent_during_live_stream(scene):
    async def exercise():
        document='subscription($id:String!,$after:Int!){agent_task_events(task_id:$id,after:$after){sequence type task_id payload}}'
        iterator=await schema.subscribe(document,variable_values={'id':scene.parent['id'],'after':0},
            context_value=TermxContext(scene.state,scene.token))
        assert hasattr(iterator,'__anext__'),iterator
        try:
            first=await asyncio.wait_for(anext(iterator),2)
            assert not first.errors and PUBLIC in json.dumps(first.data)
            scene.denial['child']=True
            for _ in scene.events[1:]:
                projected=await asyncio.wait_for(anext(iterator),2)
                assert not projected.errors and SECRET not in json.dumps(projected.data)
            scene.state.agent.emit_external(scene.parent['id'],'subagent.event',{
                'child_id':scene.child['id'],'type':'process.output','payload':{'chunk':SECRET}})
            live=await asyncio.wait_for(anext(iterator),2)
            assert not live.errors and SECRET not in json.dumps(live.data)
            assert live.data['agent_task_events']['payload']['redacted'] is True
            scene.denial['parent']=True
            scene.state.agent.emit_external(scene.parent['id'],'process.output',{'chunk':'Must not pass revoked parent boundary'})
            stopped=await asyncio.wait_for(anext(iterator),2)
            assert stopped.errors and SECRET not in str(stopped)
            assert not (stopped.data or {}).get('agent_task_events')
        finally:await iterator.aclose()
        assert not scene.state.agent._subscribers.get(scene.parent['id'])
    asyncio.run(exercise())


def test_reconstructed_state_cursor_replay_reprojects_saved_sources(scene,monkeypatch):
    scene.denial['child']=True
    restored=AppState(passcode=None,credentials=CredentialStore(memory={}))
    client=TestClient(create_app(restored),headers={'Authorization':'Bearer '+scene.token})
    install_denial(monkeypatch,restored,scene)
    async def replay():
        iterator=await schema.subscribe(
            'subscription($id:String!,$after:Int!){agent_task_events(task_id:$id,after:$after){sequence type payload}}',
            variable_values={'id':scene.parent['id'],'after':scene.events[0]['sequence']},
            context_value=TermxContext(restored,scene.token))
        assert hasattr(iterator,'__anext__'),iterator
        try:
            seen=[]
            for _ in scene.events[1:]:
                item=await asyncio.wait_for(anext(iterator),2)
                assert not item.errors and SECRET not in json.dumps(item.data)
                seen.append(item.data['agent_task_events']['sequence'])
            assert seen==[event['sequence'] for event in scene.events[1:]]
        finally:await iterator.aclose()
    try:
        page=client.get('/api/workspace/tasks/'+scene.parent['id']+'?activity=true')
        assert page.status_code==200 and SECRET not in page.text and PUBLIC in page.text
        exported=query(client,'query($id:String!){agent_task_export(task_id:$id)}',{'id':scene.parent['id']})
        assert SECRET not in json.dumps(exported) and PUBLIC in json.dumps(exported)
        asyncio.run(replay())
        assert SECRET in json.dumps(restored.agent_store.events(scene.parent['id']))
    finally:
        client.close()
        asyncio.run(restored.agent.close())
        asyncio.run(restored.workspace.close())
        restored.agent_store.close()
