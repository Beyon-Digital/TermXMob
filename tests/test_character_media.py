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
