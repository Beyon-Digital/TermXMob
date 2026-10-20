"""Request real alpha output only from models supporting that image option."""
import asyncio
import base64
from io import BytesIO
import json
from types import SimpleNamespace

import httpx
from PIL import Image
import pytest

from termx.media.service import ArtifactService, MediaProvider


@pytest.mark.parametrize('model', ['gpt-image-fixture', 'dall-e-fixture', 'compatible-image'])
def test_transparent_character_generation_and_reference_edit_options(tmp_path, model):
    image = BytesIO()
    Image.new('RGBA', (10, 10), (0, 80, 90, 120)).save(image, 'PNG')
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={'data': [{'b64_json': base64.b64encode(image.getvalue()).decode()}]})
    provider = {'id': 'images', 'kind': 'openai-compatible', 'base_url': 'https://image.fixture/v1', 'capabilities': ['image']}
    state = SimpleNamespace(agent_store=SimpleNamespace(get_provider=lambda _: provider),
                            credentials=SimpleNamespace(get=lambda _: 'fixture-only'))
    artifacts = ArtifactService(tmp_path)
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    service = MediaProvider(state, artifacts, client)
    args = {'request_id': 'character-create-fixture', 'provider_id': 'images', 'model': model,
            'operation': 'image-generate', 'prompt': 'Character reference', 'background': 'transparent',
            'acknowledge_billing': True}
    async def run():
        reference = await service.generate('owner', None, args)
        await service.generate('owner', None, {**args, 'request_id': 'character-edit-fixture',
            'operation': 'image-edit', 'input_id': reference['id']})
        await client.aclose()
    asyncio.run(run())
    generation = json.loads(calls[0].content)
    if model.startswith('gpt-image'):
        assert generation['background'] == 'transparent' and generation['output_format'] == 'png'
        assert b'name="background"\r\n\r\ntransparent' in calls[1].content
        assert b'name="output_format"\r\n\r\npng' in calls[1].content
    else:
        assert 'background' not in generation and 'output_format' not in generation
        assert b'name="background"' not in calls[1].content


def test_parallel_tool_completion_does_not_make_working_pet_think_early():
    from termx.characters.service import CharacterService
    service = object.__new__(CharacterService)
    service.activities, service._active_tools, service.actions = {}, {}, {}
    for call in ('first', 'second'):
        service.on_event('task', {'type': 'tool.started', 'payload': {'call': {'call_id': call}}})
    service.on_event('task', {'type': 'tool.finished', 'payload': {'call_id': 'first'}})
    service.on_event('task', {'type': 'assistant.delta', 'payload': {}})
    assert service.activities['task'] == 'working'
    service.on_event('task', {'type': 'tool.finished', 'payload': {'call_id': 'second'}})
    assert service.activities['task'] == 'thinking'
    service.on_event('task', {'type': 'task.completed', 'payload': {}})
    assert 'task' not in service.activities and 'task' not in service._active_tools


@pytest.mark.parametrize('engine,terminal', [
    ('acp', 'completed'), ('acp', 'failed'),
    ('claude', 'completed'), ('claude', 'failed'),
    ('codex', 'completed'), ('codex', 'failed'), ('codex', 'interrupted'),
])
def test_native_parallel_tools_drive_character_activity_until_last_completion(engine, terminal):
    from termx.characters.service import CharacterService

    service = object.__new__(CharacterService)
    service.activities, service._active_tools, service.actions = {}, {}, {'task': {'name': 'wave'}}

    def tool_event(identifier, finished=False):
        if engine == 'acp':
            return {'type': 'engine.tool.progress' if finished else 'engine.tool.started',
                    'payload': {'toolCallId': identifier, 'status': terminal if finished else 'in_progress'}}
        if engine == 'claude':
            return {'type': 'engine.tool.' + (terminal if finished else 'started'),
                    'payload': {'call_id': identifier, 'tool': 'Read',
                                'status': terminal if finished else 'running'}}
        return {'type': 'engine.item.completed' if finished else 'engine.item.started',
                'payload': {'item': {'id': identifier, 'type': 'commandExecution',
                                     'status': terminal if finished else 'inProgress'}}}

    for identifier in ('read-config', 'run-check'):
        service.on_event('task', tool_event(identifier))
    assert service.activities['task'] == 'working'
    service.on_event('task', tool_event('read-config', finished=True))
    service.on_event('task', {'type': 'engine.message.delta', 'payload': {'delta': 'Checking results'}})
    service.on_event('task', {'type': 'engine.reasoning.delta', 'payload': {'delta': 'Reviewing'}})
    # Codex emits regular message items alongside command execution items.
    service.on_event('task', {'type': 'engine.item.completed',
                              'payload': {'item': {'id': 'answer', 'type': 'agentMessage'}}})
    assert service.activities['task'] == 'working'
    service.on_event('task', tool_event('run-check', finished=True))
    assert service.activities['task'] == 'thinking'
    # A duplicate completion cannot clear a different call started afterward.
    service.on_event('task', tool_event('write-summary'))
    service.on_event('task', tool_event('run-check', finished=True))
    assert service.activities['task'] == 'working'
    service.on_event('task', {'type': 'task.failed', 'payload': {}})
    assert 'task' not in service.activities
    assert 'task' not in service._active_tools
    assert 'task' not in service.actions


@pytest.mark.parametrize('item_type', ['agentMessage', 'reasoning', 'plan'])
def test_codex_message_items_do_not_start_working_animation(item_type):
    from termx.characters.service import CharacterService

    service = object.__new__(CharacterService)
    service.activities, service._active_tools, service.actions = {}, {}, {}
    service.on_event('task', {'type': 'engine.item.started',
                              'payload': {'item': {'id': 'message', 'type': item_type}}})
    service.on_event('task', {'type': 'engine.message.delta', 'payload': {'delta': 'Hello'}})
    assert service.activities.get('task', 'thinking') == 'thinking'
    assert not service._active_tools.get('task')
