"""Model-specific reasoning: real subprocess protocols, no provider generation."""
import asyncio
import json
from types import SimpleNamespace

import pytest

from termx.engines.gateway import EngineGateway
from termx.engines.types import EffectiveRunConfiguration
from termx.workspace.reasoning import validate
from test_engines import _codex,_store
from test_durable_workspace import workspace,session


def test_codex_model_effort_turn_dispatch_reuse_snapshot_and_read_only(tmp_path):
    async def run():
        store=_store(tmp_path);gateway=EngineGateway(store,store.append_event)
        log=tmp_path/'wire.jsonl';engine=_codex(tmp_path,[],env_extra={'FAKE_CODEX_LOG':str(log)})
        engine._event_sink=gateway.on_engine_event
        gateway.register(engine)
        try:
            await gateway.catalogue.refresh('codex',str(tmp_path))
            configuration=await gateway.configuration('codex')
            assert [v['value'] for v in configuration['model_configurations']['gpt-fake-2']['config_options'][0]['options']]==['low']
            async def turn(model,effort):
                task=await gateway.create_task(engine='codex',cwd=str(tmp_path),prompt='fixture',model=model,workspace_mode='ask',config_options={'reasoning_effort':effort},conversation_id='conversation')
                for _ in range(100):
                    if store.get_task(task['id'])['status'] not in {'running','queued'}:break
                    await asyncio.sleep(.01)
                return store.get_task(task['id'])
            first=await turn('gpt-fake-1','high')
            second=await turn('gpt-fake-2','low')
            assert first['engine_native_id']==second['engine_native_id']
            calls=[json.loads(line) for line in log.read_text().splitlines()]
            assert len([c for c in calls if c.get('method')=='thread/start'])==1
            turns=[c['params'] for c in calls if c.get('method')=='turn/start']
            assert [(t['model'],t['effort'],t['sandboxPolicy']) for t in turns]==[('gpt-fake-1','high',{'type':'readOnly'}),('gpt-fake-2','low',{'type':'readOnly'})]
            event=next(e for e in store.events(first['id']) if e['type']=='engine.configuration.resolved')
            assert event['payload']['config_options']=={'reasoning_effort':'high'}
            with pytest.raises(ValueError,match='advertised'):
                await turn('gpt-fake-2','high')
            assert len([json.loads(line) for line in log.read_text().splitlines() if json.loads(line).get('method')=='turn/start'])==2
            # Public native resume contract accepts cfg, retaining the same ID.
            binding=gateway._bindings[first['engine_session_id']]
            await engine.attach(binding,cfg=EffectiveRunConfiguration())
            assert binding.native_session_id==first['engine_native_id']
        finally:await gateway.shutdown();store.close()
    asyncio.run(run())


def test_session_reasoning_revision_model_validation_active_freeze_and_delegation(workspace):
    service,owner,path=workspace
    service.state.engines.catalogue=SimpleNamespace(entries={'fixture-acp':{'configuration':{'model_configurations':{'fixture':{'config_options':[{'id':'thought','category':'thought_level','type':'select','options':[{'value':'low'},{'value':'high'}]}]},'fast':{'config_options':[{'id':'thought','category':'thought_level','type':'select','options':[{'value':'low'}]}]}}},'stale':False}})
    row=session(workspace,'fixture-acp')
    original=service.execution_target(row)
    changed=service.update_session(owner,row['id'],revision=row['revision'],changes={'reasoning_config':{'thought':'high'}})
    assert changed['reasoning_config']=={'thought':'high'}
    assert service.execution_target(changed)!=original
    with pytest.raises(ValueError,match='advertised'):
        service.update_session(owner,row['id'],revision=changed['revision'],changes={'model':'fast'})
    changed=service.update_session(owner,row['id'],revision=changed['revision'],changes={'model':'fast','reasoning_config':{'thought':'low'}})
    assert changed['model']=='fast'
    task=service.agents.create_task(prompt='fixture',cwd=str(path),provider_id='fixture',model='fast',mode='ask',status='running',limits={})
    service.agents.add_conversation_turn(row['id'],prompt='fixture',task_id=task['id'])
    with pytest.raises(Exception,match='active turn'):
        service.update_session(owner,row['id'],revision=changed['revision'],changes={'reasoning_config':{}})
    assert service.session(owner,row['id'],turns=False)['reasoning_config']=={'thought':'low'}
    assert task['model']=='fast'
    with pytest.raises(ValueError):validate(service.state,'internal','fast',{'thought':'low'})


def test_claude_native_plan_mode_and_unadvertised_reasoning_denied():
    from termx.engines.claude import ClaudeEngine
    from termx.engines.types import EngineSessionBinding
    engine=ClaudeEngine();binding=EngineSessionBinding.new('claude','fixture')
    assert engine._options(EffectiveRunConfiguration(mode='ask'),binding).permission_mode=='plan'
    with pytest.raises(ValueError,match='not advertised'):
        engine._options(EffectiveRunConfiguration(model='unknown',config_options={'effort':'high'}),binding)


def test_acp_workspace_agent_uses_native_default_and_ask_preserves_read_only(tmp_path):
    from test_engines_acp import _engine,_wait_turn
    async def run():
        store=_store(tmp_path);gateway=EngineGateway(store,store.append_event)
        engine=_engine([],[],{'FAKE_ACP_DEFAULT_MODE':'code'});engine._event_sink=gateway.on_engine_event;gateway.register(engine)
        try:
            first=await gateway.create_task(engine=engine.id,cwd=str(tmp_path),prompt='fixture',model='fast',mode='agent',workspace_mode='agent',conversation_id='conversation')
            binding=gateway._binding_for_task(first['id']);await _wait_turn(engine,binding)
            assert engine.session_configuration(binding)['modes']['currentModeId']=='code'
            second=await gateway.create_task(engine=engine.id,cwd=str(tmp_path),prompt='fixture read-only',model='slow',mode='ask',workspace_mode='ask',config_options={'reasoning':'high'},conversation_id='conversation')
            await _wait_turn(engine,binding)
            assert second['engine_session_id']==first['engine_session_id']
            assert engine.session_configuration(binding)['modes']['currentModeId']=='ask'
            assert binding.extensions_snapshot['review_read_only'] is True
        finally:await gateway.shutdown();store.close()
    asyncio.run(run())


def test_reasoning_catalogue_is_owned_session_scoped_for_project_operator(tmp_path,monkeypatch):
    from test_authorization import client_fixture,PASSWORD
    from termx.authorization import ROLES
    monkeypatch.setenv('TERMX_CONFIG_DIR',str(tmp_path/'config'))
    identity,state,client,admin=client_fixture(tmp_path)
    alice=identity.create_local_user('reasoning-alice',PASSWORD,list(ROLES['operator']))
    bob=identity.create_local_user('reasoning-bob',PASSWORD,list(ROLES['operator']))
    for principal in (alice,bob):state.authorization.set_role(principal.id,'operator')
    root=tmp_path/'project';root.mkdir();project=state.projects.register(str(root))
    state.authorization.grant_project(alice.id,project['id'],['agent-view','agent-control'])
    engine=_codex(tmp_path,[]);state.engines.register(engine)
    state.engines.catalogue.entries['codex']={'configuration':{'models':['fixture-model'],'model_configurations':{'fixture-model':{'config_options':[{'id':'reasoning_effort','category':'thought_level','type':'select','options':[{'value':'low'}]}]}},'config_options':[]},'descriptor':{'transport':'stdio-jsonl'},'stale':False,'refresh_error':None,'refreshed_at':1}
    row=state.workspace.create_session(alice,project_id=project['id'],cwd=str(root),engine='codex',model='fixture-model')
    def login(name):
        credentials=asyncio.run(identity.login('local-password',{'username':name,'password':PASSWORD},peer='local'))
        return {'Authorization':'Bearer '+credentials.access_token}
    alice_headers=login('reasoning-alice')
    assert not state.authorization.can(alice_headers['Authorization'][7:],'agent-view')
    route='/api/workspace/sessions/'+row['id']+'/reasoning'
    result=client.get(route,headers=alice_headers)
    assert result.status_code==200,result.text
    assert result.json()['model_configurations']['fixture-model']['config_options'][0]['options']==[{'value':'low'}]
    assert client.get(route,headers=login('reasoning-bob')).status_code==404
    assert engine._conn is None,'A catalogue read unexpectedly spawned native discovery'
    state.workspace.store.close();state.agent_store.close()
