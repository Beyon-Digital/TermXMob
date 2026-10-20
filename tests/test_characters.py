"""Exercise real persistence, managed HTTP authority and presentation transitions."""
import asyncio
from copy import deepcopy
from time import time

from fastapi.testclient import TestClient
import pytest

from termx.app import AppState, create_app
from termx.characters.service import CharacterService


@pytest.fixture
def host():
    state = AppState(passcode='characters-test-bootstrap')
    app = create_app(state, web_dir=None)
    owner = state.identity.setup_owner('characters-owner', 'characters-fixture-password-123')
    login = asyncio.run(state.identity.login('local-password', {
        'username': 'characters-owner', 'password': 'characters-fixture-password-123'}, peer='local'))
    client = TestClient(app)
    headers = {'Authorization': 'Bearer ' + login.access_token}
    yield state, owner, client, headers
    asyncio.run(state.characters.close())


def task_for(state, owner, title='Agent', status='running', parent=None, conversation=None):
    conversation = conversation or state.workspace.create_session(owner, title=title, mode='agent')
    task = state.agent_store.create_task(prompt='Fixture', cwd=conversation['cwd'], provider_id='fixture',
                                         model='fixture', limits={}, status=status, parent_id=parent)
    state.workspace.store.create('task', owner.id, {'conversation_id': conversation['id'],
        'cwd': conversation['cwd'], 'parent_id': parent}, identifier=task['id'])
    if not parent:
        state.agent_store.add_conversation_turn(conversation['id'], prompt='Fixture', task_id=task['id'])
    return conversation, task


def test_runtime_requires_managed_login_and_defaults_disabled(host):
    state, owner, client, headers = host
    assert client.get('/api/companions/runtime').status_code == 401
    response = client.get('/api/companions/runtime', headers=headers)
    assert response.status_code == 200
    assert response.json() == {'enabled': False, 'preferences': state.characters.preferences(owner.id), 'pets': []}
    library = client.get('/api/characters', headers=headers).json()
    assert library['packs'][0]['builtin'] and library['packs'][0]['accepted']
    assert client.get('/api/characters/packs/termx-robot/manifest', headers=headers).status_code == 200


def test_authoritative_states_actions_children_and_four_pet_limit(host):
    state, owner, _, _ = host
    service = state.characters
    entries = [task_for(state, owner, title='Agent ' + str(index)) for index in range(5)]
    conversation, task = entries[0]
    _, child = task_for(state, owner, status='planning', parent=task['id'], conversation=conversation)
    service.update_preferences(owner.id, {'enabled': True, 'pinned': [conversation['id']]})
    pets = service.snapshot(owner)['pets']
    assert len(pets) == 6 and sum(pet['roaming'] for pet in pets) == 4
    assert pets[0]['id'] == conversation['id']
    assert any(pet['id'] == child['id'] for pet in pets)
    service.on_event(task['id'], {'type': 'tool.started', 'payload': {}})
    assert service.snapshot(owner)['pets'][0]['state'] == 'working'
    service.act(task['id'], {'name': 'wave', 'seconds': 3})
    assert service.snapshot(owner)['pets'][0]['action']['name'] == 'wave'
    state.agent_store.update_task(task['id'], status='awaiting_approval')
    pet = service.snapshot(owner)['pets'][0]
    assert pet['state'] == 'waiting' and pet['action'] is None
    state.agent_store.update_task(task['id'], status='completed')
    assert service.snapshot(owner)['pets'][0]['state'] == 'success'
    state.agent_store.update_task(task['id'], updated_at=time() - 20)
    assert service.snapshot(owner)['pets'][0]['state'] == 'idle'
    with pytest.raises(ValueError):
        service.act(task['id'], {'name': 'click-other-app'})


def test_drafts_do_not_replace_accepted_pack_and_owner_isolation(host):
    state, owner, _, _ = host
    service = state.characters
    manifest, assets = deepcopy(service.starter)
    manifest['name'] = 'My robot'
    draft = service.save_pack(owner.id, manifest, assets)
    assert not draft['accepted'] and draft['accepted_revision'] is None
    with pytest.raises(ValueError, match='accept'):
        service.update_preferences(owner.id, {'assignments': {'conversation': draft['id']}})
    service.accept(owner.id, draft['id'], 1)
    next_draft = service.edit_clip(owner.id, draft['id'], 'walk', 190)
    assert next_draft['revision'] == 2 and next_draft['accepted_revision'] == 1
    assert service.pack(owner.id, draft['id'], 1)[0]['clips']['walk']['frames'][0]['duration_ms'] != 190
    with pytest.raises(KeyError):
        service.pack('someone-else', draft['id'])
    with pytest.raises(Exception) as stale:
        service.accept(owner.id, draft['id'], 1)
    assert stale.value.status_code == 409
    conversation, _ = task_for(state, owner)
    service.update_preferences(owner.id, {'assignments': {conversation['id']: draft['id']}})
    assert service.snapshot(owner)['pets'][0]['pack_revision'] == 1


def test_preferences_and_import_http_validation(host):
    state, owner, client, headers = host
    conversation, _ = task_for(state, owner)
    assert client.patch('/api/companions/preferences', headers=headers, json={'size': 900}).status_code == 400
    assert client.patch('/api/companions/preferences', headers=headers, content='invalid').status_code == 400
    assert client.patch('/api/companions/preferences', headers=headers, json={'pinned': ['foreign']}).status_code == 404
    assert client.patch('/api/companions/preferences', headers=headers, json={'pinned': [conversation['id']], 'enabled': True}).status_code == 200
    assert client.post('/api/characters/import', headers=headers, content=b'not zip').status_code == 400
    archive = client.get('/api/characters/packs/termx-robot/export', headers=headers)
    draft = client.post('/api/characters/import', headers=headers, content=archive.content).json()
    assert draft['id'] != 'termx-robot' and not draft['accepted']
    assert client.post('/api/characters/packs/' + draft['id'] + '/accept', headers=headers, json={'revision': 1}).status_code == 200
    assert client.get('/api/characters/packs/' + draft['id'] + '/assets/atlas.png', headers=headers).headers['content-type'] == 'image/png'
    state.identity.revoke(state.identity.resolve(headers['Authorization'].removeprefix('Bearer ')).session_id, owner.id)
    assert client.get('/api/companions/runtime', headers=headers).status_code == 401


def test_generation_deduplicates_and_never_replays_interrupted_billing(host, tmp_path):
    state, owner, _, _ = host
    service = state.characters
    calls = []
    async def generate(owner_id, project, args):
        calls.append(args)
        await asyncio.sleep(0)
        return {'id': 'canonical-reference'}
    state.media.generate = generate
    args = {'request_id': 'creation-fixture', 'provider_id': 'fixture', 'model': 'image', 'name': 'Robot',
            'prompt': 'Blue robot', 'phase': 'reference', 'acknowledge_billing': True}
    async def run():
        service.generate(owner.id, args, authority=lambda: None)
        service.generate(owner.id, args, authority=lambda: None)
        await asyncio.gather(*service._jobs)
        operation = service.operation(owner.id, args['request_id'])
        assert operation['phase'] == 'reference_ready' and operation['status'] == 'completed'
        assert service.generate(owner.id, args, authority=lambda: None) == operation
        with pytest.raises(Exception) as conflict:
            service.generate(owner.id, {**args, 'prompt': 'Changed'}, authority=lambda: None)
        assert conflict.value.status_code == 409
    asyncio.run(run())
    assert len(calls) == 1 and calls[0]['operation'] == 'image-generate'
    assert 'screenshot' not in calls[0]['prompt']


def test_cancelled_generation_keeps_unknown_operation(host):
    state, owner, _, _ = host
    async def stalled(*args):
        await asyncio.sleep(30)
    state.media.generate = stalled
    args = {'request_id': 'cancelled-fixture', 'provider_id': 'fixture', 'model': 'image', 'name': 'Robot',
            'prompt': 'Robot', 'phase': 'reference', 'acknowledge_billing': True}
    async def run():
        state.characters.generate(owner.id, args, authority=lambda: None)
        await asyncio.sleep(0)
        state.characters.cancel(owner.id, args['request_id'])
        await asyncio.gather(*state.characters._jobs, return_exceptions=True)
        assert state.characters.operation(owner.id, args['request_id'])['status'] == 'unknown'
    asyncio.run(run())


def test_individual_frame_replacement_preserves_other_frames_and_active_revision(host):
    from io import BytesIO
    from PIL import Image, ImageDraw
    state, owner, client, headers = host
    service = state.characters
    manifest, assets = deepcopy(service.starter)
    draft = service.save_pack(owner.id, manifest, assets)
    service.accept(owner.id, draft['id'], 1)
    image = Image.new('RGBA', (80, 80))
    ImageDraw.Draw(image).ellipse((20, 10, 60, 70), fill='#bb4444')
    output = BytesIO()
    image.save(output, 'PNG')
    artifact = state.artifacts.create(owner.id, None, 'image', 'Replacement', blob=output.getvalue(), mime='image/png')
    before = service.pack(owner.id, draft['id'])[0]
    response = client.post('/api/characters/packs/' + draft['id'] + '/frames/2', headers=headers,
                           json={'clip': 'wave', 'artifact_id': artifact['id']})
    assert response.status_code == 200, response.json()
    updated = response.json()
    assert updated['revision'] == 2 and updated['accepted_revision'] == 1
    after = service.pack(owner.id, draft['id'])[0]
    assert after['clips']['idle'] == before['clips']['idle']
    assert after['clips']['wave']['frames'][1] == before['clips']['wave']['frames'][1]
    assert after['clips']['wave']['frames'][2]['atlas'] != before['clips']['wave']['frames'][2]['atlas']
    assert client.post('/api/characters/packs/' + draft['id'] + '/frames/999', headers=headers,
                        json={'clip': 'wave', 'artifact_id': artifact['id']}).status_code == 400
