from __future__ import annotations
import asyncio
import base64
from types import SimpleNamespace
import pytest
from termx.engines.attachments import image_attachments
from termx.engines.codex import CodexEngine
from termx.engines.claude import ClaudeEngine
from termx.engines.acp import AcpEngine
from termx.engines.types import EngineSessionBinding

IMAGE={'name':'fixture.png','mime':'image/png','data':base64.b64encode(b'fixture image bytes').decode()}


def test_native_image_payload_is_bounded_and_has_no_filesystem_reference():
    assert image_attachments([IMAGE])==[IMAGE]
    with pytest.raises(ValueError):
        image_attachments([{**IMAGE,'data':'not base64'}])
    with pytest.raises(ValueError):
        image_attachments([{**IMAGE,'mime':'application/pdf'}])
    with pytest.raises(ValueError):
        image_attachments([IMAGE]*5)


def test_codex_native_turn_receives_exact_image_data_url():
    async def run():
        calls=[]
        async def request(method,params,**kwargs):
            calls.append((method,params));return {'turn':{'id':'image-turn'}}
        engine=CodexEngine(event_sink=lambda *a:None,approval_sink=lambda *a:None)
        async def ready():pass
        engine._ensure_conn=ready
        engine._conn=SimpleNamespace(request=request)
        binding=EngineSessionBinding.new('codex','native-thread',cwd='/tmp')
        await engine.send(binding,'inspect image',[IMAGE])
        assert calls[0][0]=='turn/start'
        assert calls[0][1]['input']==[{'type':'text','text':'inspect image'},
            {'type':'image','url':'data:image/png;base64,'+IMAGE['data']}]
    asyncio.run(run())


def test_claude_native_query_receives_streaming_image_content():
    async def run():
        messages=[]
        class Client:
            async def connect(self):pass
            async def query(self,stream):
                async for message in stream:messages.append(message)
            async def receive_response(self):
                if False:yield None
        engine=ClaudeEngine(event_sink=lambda *a:None,approval_sink=lambda *a:None)
        binding=EngineSessionBinding.new('claude','native-thread',cwd='/tmp')
        await engine._run_turn(binding,{'client':Client()},'inspect image',[IMAGE])
        image=messages[0]['message']['content'][1]
        assert image=={'type':'image','source':{'type':'base64','media_type':'image/png','data':IMAGE['data']}}
        assert messages[0]['session_id']=='native-thread'
    asyncio.run(run())


def test_acp_image_requires_advertised_capability_and_preserves_protocol_content():
    async def run():
        content=[]
        async def prompt(**kwargs):
            content.extend(kwargs['prompt']);return SimpleNamespace(stop_reason='end_turn',usage=None)
        engine=AcpEngine(event_sink=lambda *a:None,approval_sink=lambda *a:None)
        binding=EngineSessionBinding.new('fixture','native-session',cwd='/tmp')
        state={'conn':SimpleNamespace(prompt=prompt),'turn_task':None}
        engine._sessions[binding.binding_id]=state
        with pytest.raises(ValueError,match='does not advertise'):
            await engine.send(binding,'inspect image',[IMAGE])
        engine._agent_capabilities={'promptCapabilities':{'image':True}}
        await engine.send(binding,'inspect image',[IMAGE])
        await state['turn_task']
        assert content[1].type=='image' and content[1].data==IMAGE['data'] and content[1].mime_type=='image/png'
    asyncio.run(run())
