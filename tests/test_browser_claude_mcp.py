import asyncio
import json
import os
import pytest
from termx.browser.service import BrowserService
from termx.browser.claude_mcp import controlled_server
from termx.engines.claude import ClaudeEngine
from termx.engines.types import EffectiveRunConfiguration,EngineSessionBinding

def test_claude_browser_cli_availability_removes_builtins_settings_other_mcp(tmp_path):
    from claude_agent_sdk._internal.transport.subprocess_cli import SubprocessCLITransport
    engine=ClaudeEngine();engine.browser_service=BrowserService(tmp_path)
    engine.descriptor()
    binding=EngineSessionBinding.new('claude','native')
    cfg=EffectiveRunConfiguration(workflow='browser',tools={'browser_task_id':'trusted-task'},mcp_bindings=[{'connection_id':'unsafe','command':['sh','-c','echo unsafe']}],skills=[{'id':'unsafe'}])
    options=engine._options(cfg,binding)
    assert options.tools==[] and options.setting_sources==[] and options.skills==[]
    assert options.strict_mcp_config and set(options.mcp_servers)=={'termx-browser'}
    # This tests command assembly only. Pin a synthetic executable rather than
    # relying on an installed CLI or transport.connect() to resolve its path.
    options.cli_path='/fixture/claude'
    command=SubprocessCLITransport(prompt='No query is sent',options=options)._build_command()
    assert command[command.index('--tools')+1]==''
    assert '--strict-mcp-config' in command
    assert '--setting-sources=' in command
    assert 'unsafe' not in ' '.join(command)
    async def run():
        from claude_agent_sdk import PermissionResultDeny
        assert isinstance(await options.can_use_tool('Bash',{'command':'curl bad'},None),PermissionResultDeny)
    asyncio.run(run())

def test_native_coding_hooks_consume_before_write_and_complete_after_actual_effect(tmp_path):
    from termx.auto_review import ReviewVerdict
    from time import time
    async def run():
        engine=ClaudeEngine();service=BrowserService(tmp_path/'broker',session_valid=lambda p,s,v:p=='owner' and s=='sid' and v==1);engine.browser_service=service
        service.records.put('agent-task-authority','task',{'id':'task','principal_id':'owner','session_id':'sid','project_id':'project','policy_version':1})
        class Reviewer:
            version='qualified-v1'
            async def evaluate(self,a,c):return ReviewVerdict('ALLOW','aligned','Typed edit',self.version,time()+15)
        service.review.reviewer=Reviewer()
        cfg=EffectiveRunConfiguration(cwd=str(tmp_path),tools={'managed_task_id':'task'})
        hooks=engine._coding_review_hooks(cfg,EngineSessionBinding.new('claude','native'))
        before=hooks['PreToolUse'][0].hooks[0];after=hooks['PostToolUse'][0].hooks[0]
        request={'hook_event_name':'PreToolUse','tool_name':'Write','tool_input':{'file_path':str(tmp_path/'code.py'),'content':'VALUE=1'}}
        answer=await before(request,'call',{})
        assert answer['hookSpecificOutput']['permissionDecision']=='allow'
        assert service.records.get('review','task:native:call')['status']=='executing' and not (tmp_path/'code.py').exists()
        (tmp_path/'code.py').write_text('VALUE=1')
        await after({'hook_event_name':'PostToolUse'},'call',{})
        assert service.records.get('review','task:native:call')['status']=='completed'
        assert (await before(request,'call',{}))['hookSpecificOutput']['permissionDecision']=='deny'
        secret={'hook_event_name':'PreToolUse','tool_name':'Read','tool_input':{'file_path':str(tmp_path/'.env')}}
        assert (await before(secret,'secret',{}))['hookSpecificOutput']['permissionDecision']=='deny'
    asyncio.run(run())

@pytest.mark.skipif(not os.environ.get('TERMX_TEST_CLAUDE_INITIALIZE'),reason='explicit no-query native CLI handshake test')
def test_real_claude_cli_initialize_mcp_without_model_query(tmp_path):
    from claude_agent_sdk import ClaudeSDKClient
    async def run():
        engine=ClaudeEngine(spawn_env={'PATH':os.environ.get('PATH',''),'HOME':str(tmp_path/'isolated-native-home')})
        engine.descriptor();engine.browser_service=BrowserService(tmp_path/'browser')
        options=engine._options(EffectiveRunConfiguration(cwd=str(tmp_path),workflow='browser',tools={'browser_task_id':'trusted-task'}),EngineSessionBinding.new('claude','n'))
        options.env['ANTHROPIC_API_KEY']='';options.stderr=lambda value:None
        client=ClaudeSDKClient(options=options)
        try:
            await asyncio.wait_for(client.connect(),45)
            for _ in range(40):
                status=await asyncio.wait_for(client.get_mcp_status(),10)
                servers=status.get('mcpServers',status.get('servers',[]))
                if any(s.get('name')=='termx-browser' and s.get('status')=='connected' for s in servers):break
                await asyncio.sleep(.25)
            assert any(s.get('name')=='termx-browser' and s.get('status')=='connected' for s in servers),status
        finally:await client.disconnect()
    asyncio.run(run())

@pytest.mark.skipif(not os.environ.get('PLAYWRIGHT_BROWSERS_PATH'),reason='managed Chromium artifact required')
def test_real_mcp_protocol_browser_review_resume_once_and_read_only(tmp_path):
    import anyio
    from mcp import ClientSession
    from mcp.shared.memory import create_client_server_memory_streams
    from test_managed_browser import website
    async def run():
        server,site=await website();service=BrowserService(tmp_path,development_origins=[site])
        async def review(record,identity):
            assert record['status']=='needs_user' and identity['principal_id']=='owner'
            service.review.decide(record['id'],principal_id='owner',approve=True)
            return True
        try:
            profile=service.create_profile('owner','project','MCP isolation')
            tab=await service.create_tab('owner','sid',profile['id'],site)
            await service.handoff(tab['id'],'owner','sid',run_id='native-task',origins=[site],actions=['observe','click','type'])
            sdk=controlled_server(service,'native-task',review)['instance']
            async with create_client_server_memory_streams() as (client_streams,server_streams):
                async with anyio.create_task_group() as group:
                    group.start_soon(sdk.run,*server_streams,sdk.create_initialization_options())
                    async with ClientSession(*client_streams) as client:
                        await client.initialize()
                        assert {t.name for t in (await client.list_tools()).tools}=={'browser_tabs','browser_observe','browser_action','browser_open_tab','browser_close_tab','browser_wait_for_handoff'}
                        snapshot=json.loads((await client.call_tool('browser_observe',{'tab_id':tab['id']})).content[0].text)
                        result=await client.call_tool('browser_action',{'tab_id':tab['id'],'action':'click','args':{'selector':'#send'},'document_revision':snapshot['document_revision'],'lease_revision':snapshot['lease_revision']})
                        assert not result.is_error
                        assert await service._pages[tab['id']].locator('h1').inner_text()=='SENT'
                        assert len([r for r in service.records.list('review') if r['status']=='completed'])==1
                        assert (await client.call_tool('Bash',{'command':'curl bad'})).is_error
                    group.cancel_scope.cancel()
        finally:
            await service.close();server.close();await server.wait_closed()
    asyncio.run(run())
