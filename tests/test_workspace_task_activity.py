"""Saved chat activity is opt-in, bounded, and authorized after projection."""
from __future__ import annotations

import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from test_durable_workspace import workspace
from termx.workspace.http import mount_workspace


@pytest.fixture
def activity_client(workspace):
    service, _, _ = workspace
    service.state.workspace = service
    credentials = asyncio.run(service.state.identity.login('local-password',
        {'username': 'owner', 'password': 'strong fixture password'}, peer='activity-test'))
    app = FastAPI()
    mount_workspace(app, service.state)
    with TestClient(app, headers={'Authorization': 'Bearer ' + credentials.access_token}) as client:
        yield client, credentials


def enrolled_task(workspace, *, parent=None, owner=None):
    service, account, path = workspace
    task = service.agents.create_task(prompt='Saved task', cwd=str(path), provider_id='fixture',
        model='fixture', limits={}, parent_id=parent, status='completed')
    service.store.create('task', owner or account.id, {'cwd': str(path)},
        project='project-a', identifier=task['id'])
    return task


def flatten(tree):
    return [tree, *(node for child in tree['children'] for node in flatten(child))]


def test_task_activity_is_lazy_and_returns_real_parent_and_child_events(workspace, activity_client, monkeypatch):
    service, _, _ = workspace
    client, _ = activity_client
    parent = enrolled_task(workspace)
    child = enrolled_task(workspace, parent=parent['id'])
    events = {}
    for task in (parent, child):
        events[task['id']] = [
            service.agents.append_event(task['id'], 'tool.started', {'call_id': 'read', 'name': 'read_file'}),
            service.agents.append_event(task['id'], 'tool.finished', {'call_id': 'read', 'result': 'Saved file output'}),
        ]
    reads = []
    original = service.agents.events

    def read(task_id, **kwargs):
        reads.append((task_id, kwargs))
        return original(task_id, **kwargs)

    monkeypatch.setattr(service.agents, 'events', read)
    default = client.get('/api/workspace/tasks/' + parent['id'])
    assert default.status_code == 200, default.text
    assert reads == []
    assert all('events' not in node and 'activity_truncated' not in node for node in flatten(default.json()))

    response = client.get('/api/workspace/tasks/' + parent['id'] + '?activity=true')
    assert response.status_code == 200, response.text
    for node in flatten(response.json()):
        assert node['events'] == events[node['id']]
        assert node['activity_truncated'] is False
    assert reads == [(parent['id'], {'tail_limit': 301}), (child['id'], {'tail_limit': 301})]


def test_task_activity_never_reads_a_foreign_root_or_child(workspace, activity_client, monkeypatch):
    service, owner, _ = workspace
    client, _ = activity_client
    stranger = service.state.identity.create_local_user('stranger', 'strong fixture password', list(owner.scopes))
    parent = enrolled_task(workspace)
    child = enrolled_task(workspace, parent=parent['id'])
    foreign = enrolled_task(workspace, parent=parent['id'], owner=stranger.id)
    service.agents.append_event(foreign['id'], 'tool.finished', {'result': 'Another user private output'})
    reads = []
    original = service.agents.events

    def read(task_id, **kwargs):
        reads.append(task_id)
        return original(task_id, **kwargs)

    monkeypatch.setattr(service.agents, 'events', read)
    denied = client.get('/api/workspace/tasks/' + foreign['id'] + '?activity=true')
    assert denied.status_code == 404, denied.text
    assert reads == []
    response = client.get('/api/workspace/tasks/' + parent['id'] + '?activity=true')
    assert response.status_code == 200, response.text
    assert [node['id'] for node in flatten(response.json())] == [parent['id'], child['id']]
    assert reads == [parent['id'], child['id']]
    assert 'Another user private output' not in response.text


def test_task_activity_keeps_latest_events_with_per_node_and_total_limits(workspace, activity_client, monkeypatch):
    service, _, _ = workspace
    client, _ = activity_client
    parent = enrolled_task(workspace)
    tasks = [parent, *(enrolled_task(workspace, parent=parent['id']) for _ in range(4))]
    for task in tasks:
        for index in range(305):
            service.agents.append_event(task['id'], 'process.output', {'chunk': str(index)})
    limits = []
    original = service.agents.events

    def read(task_id, **kwargs):
        limits.append(kwargs['tail_limit'])
        return original(task_id, **kwargs)

    monkeypatch.setattr(service.agents, 'events', read)
    response = client.get('/api/workspace/tasks/' + parent['id'] + '?activity=true')
    assert response.status_code == 200, response.text
    nodes = flatten(response.json())
    assert [len(node['events']) for node in nodes] == [300, 300, 300, 100, 0]
    assert sum(len(node['events']) for node in nodes) == 1000
    assert all(node['activity_truncated'] for node in nodes)
    for node in nodes:
        if node['events']:
            assert [event['sequence'] for event in node['events']] == list(range(306 - len(node['events']), 306))
            assert node['events'][-1]['payload']['chunk'] == '304'
    assert limits == [301, 301, 301, 101, 1]


@pytest.mark.parametrize('shape,expected', [('wide', 100), ('deep', 9)])
def test_task_activity_preserves_tree_size_and_depth_guards(workspace, activity_client, shape, expected):
    service, _, _ = workspace
    client, _ = activity_client
    parent = enrolled_task(workspace)
    previous = parent
    for _ in range(102 if shape == 'wide' else 10):
        previous = enrolled_task(workspace, parent=(parent if shape == 'wide' else previous)['id'])
        service.agents.append_event(previous['id'], 'tool.finished', {'result': 'Bounded child output'})
    response = client.get('/api/workspace/tasks/' + parent['id'] + '?activity=true')
    assert response.status_code == 200, response.text
    nodes = flatten(response.json())
    assert len(nodes) == expected
    assert any(node['truncated'] for node in nodes)
    assert all('events' in node and not node['activity_truncated'] for node in nodes)


@pytest.mark.parametrize('revocation,expected', [('session', 401), ('scope', 403), ('child', 404)])
def test_task_activity_rechecks_session_and_each_task_after_read(workspace, activity_client, monkeypatch, revocation, expected):
    service, owner, _ = workspace
    client, credentials = activity_client
    parent = enrolled_task(workspace)
    child = enrolled_task(workspace, parent=parent['id'])
    service.agents.append_event(child['id'], 'tool.finished', {'result': 'Output read before authorization changed'})
    original = service.agents.events

    def read(task_id, **kwargs):
        result = original(task_id, **kwargs)
        if task_id == child['id']:
            if revocation == 'session':
                service.state.identity.revoke(credentials.session_id, owner.id)
            elif revocation == 'scope':
                service.state.identity.set_scopes(owner.id, [scope for scope in owner.scopes if scope != 'agent-view'])
            else:
                service.store.delete('task', child['id'])
        return result

    monkeypatch.setattr(service.agents, 'events', read)
    response = client.get('/api/workspace/tasks/' + parent['id'] + '?activity=true')
    assert response.status_code == expected, response.text
    assert 'Output read before authorization changed' not in response.text
