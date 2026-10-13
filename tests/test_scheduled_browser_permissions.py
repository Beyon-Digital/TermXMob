"""Unattended browser consent boundaries without browser/model substitutions."""
import asyncio
from copy import deepcopy
from time import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from termx.browser.service import BrowserService
from termx.workspace.scheduled_browser import ScheduledBrowserPermissions


def fixture_workspace():
    principal = SimpleNamespace(id='owner', policy_version=4)
    tabs = {name:{'id':name,'principal_id':'owner','profile_id':'profile',
            'project_id':'project','lease_revision':2,'state':'human'} for name in ('one','two')}
    browser = SimpleNamespace(records=SimpleNamespace(get=Mock(return_value={'id':'profile','principal_id':'owner','ephemeral':False})),get=lambda name, owner:dict(tabs[name]),
        _pages={name:SimpleNamespace(url='https://example.com/start') for name in tabs},
        network=SimpleNamespace(validate=AsyncMock(return_value='https://example.com')))
    conversation={'id':'conversation','project_id':'project','engine':'internal','mode':'agent'}
    workspace=SimpleNamespace(state=SimpleNamespace(browser=browser),session=Mock(return_value=conversation),
        record=Mock(), require=Mock(),validate_task_execution=Mock())
    return ScheduledBrowserPermissions(workspace),workspace,principal,tabs


def selection(*names, **changes):
    return {'tabs':[{'tab_id':name,'origins':['https://example.com'],'actions':['observe','click']} for name in names],
            'expires_at':time()+600,'max_seconds':60,**changes}


@pytest.mark.parametrize('actions',[['click'],['observe','open_tab'],['observe','upload']])
def test_unsupported_or_unobservable_scope_is_rejected(actions):
    permissions,_,principal,_=fixture_workspace()
    request=selection('one');request['tabs'][0]['actions']=actions
    with pytest.raises(ValueError):asyncio.run(permissions.prepare(principal,'conversation',request))


@pytest.mark.parametrize('site',['https://example.com/path','https://example.com/?secret=1','https://example.com/#fragment'])
def test_origin_permission_cannot_silently_expand_url(site):
    permissions,_,principal,_=fixture_workspace()
    request=selection('one');request['tabs'][0]['origins']=[site]
    with pytest.raises(ValueError,match='scheme, host and port'):
        asyncio.run(permissions.prepare(principal,'conversation',request))


def test_prepare_rechecks_first_tab_after_later_network_validation():
    permissions,workspace,principal,tabs=fixture_workspace()
    count=0
    async def network(site):
        nonlocal count
        count+=1
        if count==2:tabs['one']['lease_revision']+=1
    workspace.state.browser.network.validate=network
    with pytest.raises(PermissionError,match='selected browser tab changed'):
        asyncio.run(permissions.prepare(principal,'conversation',selection('one','two')))


def test_prepare_captures_exact_initial_profile_url_and_lease():
    permissions,workspace,principal,_=fixture_workspace()
    snapshot=asyncio.run(permissions.prepare(principal,'conversation',selection('one')))
    assert snapshot['tabs'][0]=={'tab_id':'one','profile_id':'profile','lease_revision':2,
        'origins':['https://example.com'],'actions':['click','observe'],'start_url':'https://example.com/start'}
    workspace.require.assert_called_with(principal,'desktop-control','project',resource_kind='browser-tab',resource_id='one')


def test_explicit_delegation_never_borrows_session_validator(tmp_path):
    session=Mock(return_value=True)
    browser=BrowserService(tmp_path,session_valid=session)
    delegation=Mock(return_value=True);browser.delegation_valid=delegation
    binding={'principal_id':'owner','session_id':None,'policy_version':4,'delegation_id':'grant','run_id':'task'}
    assert browser.authority_valid(binding)
    session.assert_not_called()
    delegation.assert_called_once_with(binding)
    assert not browser.authority_valid({**binding,'session_id':'forged-login'})
    session.assert_not_called()


def test_ephemeral_profile_cannot_be_authorized_for_durable_schedule():
    permissions,workspace,principal,_=fixture_workspace()
    workspace.state.browser.records.get.return_value={'id':'profile','principal_id':'owner','ephemeral':True}
    with pytest.raises(ValueError,match='persistent profile'):
        asyncio.run(permissions.prepare(principal,'conversation',selection('one')))


@pytest.mark.parametrize('state',['human','private','agent','closed'])
def test_graceful_service_restart_preserves_exact_original_source(tmp_path,state):
    async def run():
        old=BrowserService(tmp_path)
        profile=old.create_profile('owner','project','Persistent')
        tab=old._new_tab_record(profile,'real-login')
        tab.update(state=state,lease_revision=7)
        old.records.put('tab',tab['id'],tab)
        await old.close()
        restarted=BrowserService(tmp_path)
        restored=restarted.get(tab['id'],'owner')
        assert restored['state']=='closed'
        assert restored['restart_source_state']==state
        assert restored['restart_source_lease']==7
        assert (restored['restart_source_state']=='human') == (state=='human')
        await restarted.close()
    asyncio.run(run())


def test_completed_delegation_releases_before_graceful_shutdown_snapshot(tmp_path):
    async def run():
        old=BrowserService(tmp_path)
        profile=old.create_profile('owner','project','Persistent')
        tab=old._new_tab_record(profile,'initial-human-session')
        tab.update(state='agent',lease_revision=7,grant_id='browser-grant')
        grant={'id':'browser-grant','delegation_id':'explicit-permission','run_id':'completed-task',
               'lease_revision':7,'revoked':False}
        old.records.put('tab',tab['id'],tab)
        old.records.put('grant','browser-grant',grant)
        old.delegation_completed=Mock(return_value=True)
        observations=[]
        old.delegation_release=Mock(side_effect=lambda row, grant:observations.append((deepcopy(row),deepcopy(grant))))
        await old.close()
        old.delegation_release.assert_called_once()
        released,passed_grant=observations[0]
        assert released['state']=='human' and released['lease_revision']==8
        assert passed_grant['run_id']=='completed-task'
        restarted=BrowserService(tmp_path)
        restored=restarted.get(tab['id'],'owner')
        assert restored['restart_source_state']=='human'
        assert restored['restart_source_lease']==8
        await restarted.close()
    asyncio.run(run())
