"""Browser schedule retry retains the exact durable consent and run identity."""
from time import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from test_durable_workspace import session, workspace
from termx.workspace.automation import AutomationService
from termx.workspace.store import Conflict


@pytest.mark.anyio
async def test_retry_does_not_reauthorize_or_duplicate_existing_browser_schedule(workspace):
    service, owner, _ = workspace
    conversation = session(workspace)
    now = time()
    selection = {'tabs':[{'tab_id':'selected','origins':['https://example.com'],
                         'actions':['observe']}], 'expires_at':now+600, 'max_seconds':60}
    snapshot = {**selection, 'tabs':[{**selection['tabs'][0], 'profile_id':'approved-profile',
                                    'start_url':'https://example.com/start','lease_revision':3}]}
    prepare = AsyncMock(return_value=snapshot)
    service.state.scheduled_browser = SimpleNamespace(prepare=prepare)
    automation = AutomationService(service, principal_lookup=service.principal_lookup)
    arguments = dict(request_id='browser-creation-once', conversation_id=conversation['id'],
        success_criteria='Inspect the approved page', max_runs=2, limits=None,
        prompt='Inspect the selected page', spec={'kind':'once','at':now+60}, grant_days=1,
        browser_permission=selection)
    created = await automation.create_with_browser(owner, **arguments)
    prepare.side_effect = PermissionError('The original browser lease has changed')
    assert await automation.create_with_browser(owner, **arguments) == created
    assert prepare.await_count == 1
    assert len(service.store.list('schedule')) == len(service.store.list('delegation')) == 1
    assert service.store.get('delegation',created['grant_id'])['browser_permission'] == snapshot
    with pytest.raises(Conflict, match='changed'):
        await automation.create_with_browser(owner, **{**arguments, 'prompt':'Changed intent'})


@pytest.mark.anyio
async def test_browser_expiry_rejected_before_durable_authority_is_created(workspace):
    service, owner, _ = workspace
    conversation = session(workspace)
    now = time()
    permission = {'tabs':[{'tab_id':'selected','origins':['https://example.com'],
                          'actions':['observe']}], 'expires_at':now+2*86400,'max_seconds':60}
    service.state.scheduled_browser = SimpleNamespace(prepare=AsyncMock(return_value=permission))
    automation = AutomationService(service, principal_lookup=service.principal_lookup)
    with pytest.raises(ValueError, match='schedule grant'):
        await automation.create_with_browser(owner,request_id='browser-expiry-invalid',
            conversation_id=conversation['id'],success_criteria='Observe',max_runs=1,limits=None,
            prompt='Observe',spec={'kind':'once','at':now+60},grant_days=1,browser_permission=permission)
    assert not service.store.list('schedule')
    assert not service.store.list('delegation')
    assert not service.store.list('goal')
