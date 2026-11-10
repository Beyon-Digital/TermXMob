"""Desktop-control skills, review hardening: enforcement that does not depend on the host and cannot be walked around."""
from __future__ import annotations

import asyncio
from time import time

import pytest

from agent_desktop_fixtures import unpinned_client
from termx.desktop import apps as apps_module
from termx.desktop.apps import combine
from test_computer_policy import DesktopAdapter, anyio_backend, hosted  # noqa: F401  (fixtures)
from test_agent import build_manager
from test_computer_skills import _close, _create, _grant, _permission, _run, _unattended

# Same host model as test_computer_skills: one with a pinned, verifiable agent-desktop build. Tests of the refusal
# override it.
pytestmark = pytest.mark.usefixtures('ready_desktop')


def _permitted(manager, task_id, **kw):
    manager.is_unattended = lambda _task_id: True
    manager.computer_permission = lambda _task_id: {**_permission(**kw), 'run_id': task_id}


def test_the_same_application_under_two_names_in_two_lists_overlaps_nowhere():
    assert combine([['com.apple.Notes'], ['Notes']]) == []


def test_a_list_that_allows_nothing_is_enforced_without_any_process(tmp_path, ready_desktop):
    async def run():
        manager, store_, done = await _run(tmp_path, ready_desktop, apps=[])
        assert {call['argv'][0] for call in ready_desktop.spawns()} <= {'list-apps'}
        await _close(manager, store_)

    asyncio.run(run())


def test_the_shipped_pins_are_complete_for_macos_arm64(tmp_path):
    # The one platform the shipped pins hash per file. Everything below builds its own pins table, so it does not
    # change when more platforms are pinned.
    from termx.desktop import agent_desktop as ad
    client = ad.AgentDesktopClient(home_root=tmp_path / 'desktop-control', platform=ad.HostPlatform('macos', 'arm64', None))
    assert client.pin_complete() is True


@pytest.mark.parametrize('system, arch', [('linux', 'x86_64'), ('windows', 'x86_64'), ('macos', 'x86_64')])
def test_a_platform_without_a_complete_pin_is_not_verifiable(tmp_path, system, arch):
    unpinned_client(tmp_path, system, arch)
    assert apps_module.verifiable() is False


@pytest.mark.parametrize('system', ['macos', 'linux', 'windows'])
def test_a_platform_with_a_complete_pin_is_verifiable_whatever_the_os(fake_agent_desktop, system):
    fake_agent_desktop(system)
    assert apps_module.verifiable() is True


def test_the_policy_denies_a_scheduled_run_where_desktop_control_is_not_set_up(tmp_path, ready_desktop):
    async def run():
        ready_desktop.client._status.ready = False  # nothing is offered or run where desktop control is not set up
        manager, store_, done = await _run(tmp_path, ready_desktop, apps=None, setup=_unattended(_permission()))
        assert ready_desktop.spawns() == []
        await _close(manager, store_)

    asyncio.run(run())


@pytest.mark.anyio
async def test_a_schedule_cannot_carry_a_permission_on_a_platform_without_a_complete_pin(hosted, tmp_path):
    unpinned_client(tmp_path)
    with pytest.raises(ValueError, match='no verified build for this computer') as refused:
        _create(hosted)
    assert 'front' not in str(refused.value)


@pytest.mark.anyio
@pytest.mark.parametrize('system', ['macos', 'linux', 'windows'])
async def test_a_schedule_with_a_permission_is_accepted_on_every_os_with_a_complete_pin(hosted, fake_agent_desktop, system):
    # The request refused above, where a build has been pinned. The OS does not matter: no one asks what is in front.
    fake_agent_desktop(system)
    assert _grant(hosted, _create(hosted))['computer_permission']['apps'] == ['Notes']


def test_a_call_is_cut_off_when_the_run_has_no_desktop_time_left(tmp_path):
    async def run():
        manager, store_ = build_manager(tmp_path, DesktopAdapter())
        task = store_.create_task(prompt='p', cwd=str(tmp_path), provider_id='fake', model='m', limits={})
        _permitted(manager, task['id'], seconds=1)
        started = asyncio.get_running_loop().time()
        with pytest.raises(PermissionError, match='seconds'):
            async with manager.desktop_budget(task['id']):
                await asyncio.sleep(5)
        assert asyncio.get_running_loop().time() - started < 3
        await _close(manager, store_)

    asyncio.run(run())


def test_the_seconds_spent_survive_a_restart(tmp_path):
    async def run():
        manager, store_ = build_manager(tmp_path, DesktopAdapter())
        task = store_.create_task(prompt='p', cwd=str(tmp_path), provider_id='fake', model='m', limits={})
        store_.update_task(task['id'], runtime={})
        _permitted(manager, task['id'], seconds=10)
        async with manager.desktop_budget(task['id']):
            await asyncio.sleep(0.05)
        assert store_.get_task(task['id'])['runtime']['computer_spent'] > 0
        store_.update_task(task['id'], runtime={'computer_spent': 10.0})
        again, _ = build_manager(tmp_path / 'again', DesktopAdapter())
        again.store = store_
        _permitted(again, task['id'], seconds=10)
        with pytest.raises(PermissionError, match='seconds'):
            async with again.desktop_budget(task['id']):
                pass
        await again.close()
        await _close(manager, store_)

    asyncio.run(run())


def test_a_planning_rewrite_keeps_the_seconds_spent():
    from termx.agent.manager import AgentManager
    assert AgentManager._carry_runtime({'computer_spent': 3.5})['computer_spent'] == 3.5


@pytest.mark.anyio
async def test_moving_a_schedule_past_its_computer_permission_is_refused(hosted):
    schedule = _create(hosted)
    with pytest.raises(ValueError, match='cover'):
        hosted.state.automation.edit(hosted.owner, schedule['id'], revision=schedule['revision'],
                                     spec={'kind': 'once', 'at': time() + 7200})
