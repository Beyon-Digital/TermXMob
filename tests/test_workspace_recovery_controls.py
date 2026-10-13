"""Recovery may resume execution only within the original host authority."""
import asyncio
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from test_durable_workspace import session, workspace
from termx.workspace.http import mount_workspace


def recovery_client(workspace, monkeypatch):
    service, owner, _ = workspace
    row = session(workspace)
    task = asyncio.run(service.send(owner, row['id'], prompt='Recover work', request_id='recover-original'))
    service.agents.update_task(task['id'], status='recovery_confirmation_required')
    recover = AsyncMock(return_value={'id': task['id'], 'status': 'running'})
    monkeypatch.setattr(service.state.agent, 'recover_task', recover, raising=False)
    service.state.workspace = service
    app = FastAPI()
    mount_workspace(app, service.state)
    return service, owner, task, recover, app


def login_client(service, app):
    credentials = asyncio.run(service.state.identity.login('local-password',
        {'username': 'owner', 'password': 'strong fixture password'}, peer='recovery-test'))
    return TestClient(app, headers={'Authorization': 'Bearer ' + credentials.access_token})


def test_recovery_forwards_explicit_confirmation(workspace, monkeypatch):
    service, _, task, recover, app = recovery_client(workspace, monkeypatch)
    with login_client(service, app) as client:
        response = client.post(f'/api/workspace/tasks/{task["id"]}/recover', json={'confirm': True})
    assert response.status_code == 200, response.text
    recover.assert_awaited_once_with(task['id'], confirm=True)


@pytest.mark.parametrize('missing', ['agent-control', 'agent-run'])
def test_recovery_requires_both_execution_and_control(workspace, monkeypatch, missing):
    service, owner, task, recover, app = recovery_client(workspace, monkeypatch)
    service.state.identity.set_scopes(owner.id, [scope for scope in owner.scopes if scope != missing])
    with login_client(service, app) as client:
        response = client.post(f'/api/workspace/tasks/{task["id"]}/recover', json={'confirm': True})
    assert response.status_code == 403, response.text
    recover.assert_not_awaited()


@pytest.mark.parametrize('target', ['runner', 'native'])
def test_recovery_cannot_replay_runner_or_native_task_on_host(workspace, monkeypatch, target):
    service, _, task, recover, app = recovery_client(workspace, monkeypatch)
    if target == 'native':
        service.agents.update_task(task['id'], engine='fixture-acp')
    else:
        metadata = service.store.get('task', task['id'])
        service.store.update('task', task['id'], {**metadata, 'runner_id': 'original-runner'})
    with login_client(service, app) as client:
        response = client.post(f'/api/workspace/tasks/{task["id"]}/recover', json={'confirm': True})
    assert response.status_code == 409, response.text
    recover.assert_not_awaited()
