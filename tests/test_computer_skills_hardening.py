"""Computer-use skills, review hardening: enforcement that does not depend on the host and cannot be walked around."""
from __future__ import annotations

import asyncio
import threading
from time import time

import pytest

from termx.desktop import apps as apps_module
from termx.desktop.apps import AppBlocked, combine, matches, switching_hotkey
from test_computer_policy import CLICK, Desktop, _events, _manager, anyio_backend, hosted  # noqa: F401  (fixtures)
from test_computer_skills import _close, _create, _direct, _grant, _permission, _run, _unattended


# Same host model as test_computer_skills: one that can name the front application. Tests of the refusal override it.
pytestmark = pytest.mark.usefixtures('host_names_the_front_application')


def _on(monkeypatch, platform):
    monkeypatch.setattr(apps_module.sys, 'platform', platform)


def _permitted(manager, task_id, **kw):
    manager.is_unattended = lambda _task_id: True
    manager.computer_permission = lambda _task_id: {**_permission(**kw), 'run_id': task_id}


# ---------------------------------------------------------------------------
# Hotkeys
# ---------------------------------------------------------------------------


@pytest.mark.parametrize('keys', [['super', 'r'], ['win', 'e'], ['windows', 'd'], ['meta', 'r'], ['meta', 'e'], ['super']])
def test_super_chords_are_refused_off_macos(monkeypatch, keys):
    _on(monkeypatch, 'linux')
    assert switching_hotkey([{'type': 'keypress', 'keys': keys}]) is not None


@pytest.mark.parametrize('keys', [['meta', 'c'], ['meta', 'v'], ['meta', 'n']])
def test_command_shortcuts_are_refused_off_macos_and_allowed_on_it(monkeypatch, keys):
    action = [{'type': 'keypress', 'keys': keys}]
    _on(monkeypatch, 'linux')
    assert switching_hotkey(action) is not None
    _on(monkeypatch, 'darwin')
    assert switching_hotkey(action) is None


def test_the_controller_reads_super_as_meta_like_the_filter():
    from termx.agent.computer import _key_name
    assert _key_name('super') == 'meta' and _key_name('win') == 'meta' and _key_name('windows') == 'meta'


@pytest.mark.parametrize('keys', [['alt+Tab'], ['Super+r'], ['ctrl+alt+Tab'], ['cmd+space']])
def test_a_chord_in_one_key_name_is_split_and_checked(monkeypatch, keys):
    _on(monkeypatch, 'linux')
    assert switching_hotkey([{'type': 'keypress', 'keys': keys}]) is not None


def test_a_plus_sign_typed_as_a_key_is_not_a_chord():
    assert switching_hotkey([{'type': 'keypress', 'keys': ['+']}]) is None


def test_the_same_application_under_two_names_in_two_lists_overlaps_nowhere():
    assert combine([['com.apple.Notes'], ['Notes']]) == []


def test_keys_held_in_one_batch_are_released_before_the_next(tmp_path):
    async def run():
        manager, store_, desktop, done = await _run(tmp_path, [{'type': 'key_down', 'keys': ['shift']}], apps=['Notes'])
        assert desktop.actions == [[{'type': 'key_down', 'keys': ['shift']}]]
        assert desktop.released >= 1
        await _close(manager, store_)

    asyncio.run(run())


# ---------------------------------------------------------------------------
# Hosts that cannot name the front application
# ---------------------------------------------------------------------------


def test_a_list_that_allows_nothing_is_enforced_where_the_host_cannot_name_applications(tmp_path, monkeypatch):
    async def run():
        monkeypatch.setattr(apps_module, 'verifiable', lambda: False)
        desktop = Desktop(None)
        manager, store_, task, call, events = await _direct(tmp_path, desktop, [])
        with pytest.raises(AppBlocked):
            await manager._execute_computer(task['id'], call, asyncio.Event())
        assert desktop.actions == []
        assert 'computer.app_blocked' in [kind for kind, _ in events]
        await _close(manager, store_)

    asyncio.run(run())


def test_a_schedule_permission_is_refused_where_the_host_cannot_name_applications(tmp_path, monkeypatch):
    async def run():
        monkeypatch.setattr(apps_module, 'verifiable', lambda: False)
        desktop = Desktop(None)
        manager, store_, task, call, events = await _direct(tmp_path, desktop, None)
        store_.update_task(task['id'], runtime={})
        _permitted(manager, task['id'])
        with pytest.raises(AppBlocked):
            await manager._execute_computer(task['id'], call, asyncio.Event())
        assert desktop.actions == []
        await _close(manager, store_)

    asyncio.run(run())


def test_the_policy_denies_a_schedule_permission_where_the_host_cannot_name_applications(tmp_path, monkeypatch):
    async def run():
        monkeypatch.setattr(apps_module, 'verifiable', lambda: False)
        manager, store_, desktop, done = await _run(
            tmp_path, CLICK, apps=None, desktop=Desktop(None), setup=_unattended(_permission()))
        assert desktop.actions == []
        assert _events(done, 'tool.finished')[0]['payload']['result']['refused'] is True
        await _close(manager, store_)

    asyncio.run(run())


@pytest.mark.anyio
async def test_a_schedule_cannot_carry_a_permission_the_host_cannot_enforce(hosted, monkeypatch):
    monkeypatch.setattr(apps_module, 'verifiable', lambda: False)
    with pytest.raises(ValueError, match='This host cannot tell which application is in front, so it cannot hold a computer permission for a schedule'):
        _create(hosted)


@pytest.mark.anyio
async def test_a_schedule_with_a_permission_is_accepted_where_the_host_can_name_applications(hosted):
    # The same request as the refusal above, on a host that names the front application (the module fixture).
    assert _grant(hosted, _create(hosted))['computer_permission']['apps'] == ['Notes']


# ---------------------------------------------------------------------------
# Lookup cost and per-action checks
# ---------------------------------------------------------------------------


def test_the_front_application_is_looked_up_off_the_event_loop(tmp_path):
    async def run():
        manager, store_, task, call, events = await _direct(tmp_path, Desktop('Notes'), ['Notes'])
        seen: list[bool] = []

        def names():
            seen.append(threading.current_thread() is threading.main_thread())
            return ['Notes']

        manager.front_names = names  # type: ignore[method-assign]
        await manager._execute_computer(task['id'], call, asyncio.Event())
        assert seen and not any(seen)
        await _close(manager, store_)

    asyncio.run(run())


class Guarded(Desktop):
    """Asks the manager's guard before its second action, as the real controller does, and moves to Safari on a click."""

    async def execute(self, actions, *, cancel=None, guard=None, pointer_guard=None):
        for index, action in enumerate(actions):
            if index and guard is not None:
                await guard(action)
            self.actions.append(action)
            if action.get('type') == 'click':
                self.app = 'Safari'
        return b'jpeg'


def test_input_that_follows_a_switch_inside_one_batch_is_stopped(tmp_path):
    async def run():
        batch = [{'type': 'click', 'x': 1, 'y': 1}, {'type': 'type', 'text': 'secret'}]
        manager, store_, desktop, done = await _run(tmp_path, batch, apps=['Notes'], desktop=Guarded('Notes'))
        assert desktop.actions == [batch[0]]
        payload = _events(done, 'computer.app_blocked')[0]['payload']
        assert payload['phase'] == 'during' and payload['input_sent'] is True and payload['front'] == 'Safari'
        assert not _events(done, 'computer.screenshot')
        await _close(manager, store_)

    asyncio.run(run())


def test_the_controller_asks_the_guard_before_each_input_after_the_first(monkeypatch):
    from termx.agent.computer import ComputerController

    async def run():
        controller = ComputerController()
        done: list[str] = []

        async def action(self, item, **_):
            done.append(item['type'])

        async def frame(self):
            return b'frame'

        async def release(self):
            done.append('release')

        monkeypatch.setattr(ComputerController, '_action', action)
        monkeypatch.setattr(ComputerController, 'screenshot', frame)
        monkeypatch.setattr(ComputerController, 'release_all', release)
        asked: list[str] = []

        async def guard(item):
            asked.append(item['type'])
            if item['type'] == 'keypress':
                raise AppBlocked('no', {})

        batch = [{'type': 'screenshot'}, {'type': 'click'}, {'type': 'wait'}, {'type': 'type'}, {'type': 'keypress'}, {'type': 'type'}]
        with pytest.raises(AppBlocked):
            await controller.execute(batch, guard=guard)
        assert asked == ['type', 'keypress']
        assert done == ['screenshot', 'click', 'wait', 'type', 'release']

    asyncio.run(run())


# ---------------------------------------------------------------------------
# Computer time
# ---------------------------------------------------------------------------


class Slow(Desktop):
    async def execute(self, actions, *, cancel=None, guard=None, pointer_guard=None):
        try:
            await asyncio.sleep(5)
        except asyncio.CancelledError:
            await self.release_all()
            raise
        return await super().execute(actions, cancel=cancel)


def test_a_batch_is_cut_off_when_the_run_has_no_computer_time_left(tmp_path):
    async def run():
        desktop = Slow('Notes')
        manager, store_, task, call, events = await _direct(tmp_path, desktop, None)
        store_.update_task(task['id'], runtime={})
        _permitted(manager, task['id'], seconds=0.2)
        started = asyncio.get_running_loop().time()
        with pytest.raises(PermissionError, match='seconds'):
            await manager._execute_computer(task['id'], call, asyncio.Event())
        assert asyncio.get_running_loop().time() - started < 2
        assert desktop.released >= 1
        await _close(manager, store_)

    asyncio.run(run())


def test_the_seconds_spent_survive_a_restart(tmp_path):
    async def run():
        manager, store_, task, call, events = await _direct(tmp_path, Desktop('Notes'), None)
        store_.update_task(task['id'], runtime={})
        _permitted(manager, task['id'], seconds=10)
        await manager._execute_computer(task['id'], call, asyncio.Event())
        assert store_.get_task(task['id'])['runtime']['computer_spent'] > 0
        store_.update_task(task['id'], runtime={'computer_spent': 10.0})
        again, _, _ = _manager(tmp_path / 'again', CLICK)
        again.store = store_
        _permitted(again, task['id'], seconds=10)
        with pytest.raises(PermissionError, match='seconds'):
            await again._execute_computer(task['id'], call, asyncio.Event())
        await again.close()
        await _close(manager, store_)

    asyncio.run(run())


def test_a_planning_rewrite_keeps_the_seconds_spent():
    from termx.agent.manager import AgentManager
    assert AgentManager._carry_runtime({'computer_spent': 3.5})['computer_spent'] == 3.5


# ---------------------------------------------------------------------------
# Schedules
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_moving_a_schedule_past_its_computer_permission_is_refused(hosted):
    schedule = _create(hosted)
    with pytest.raises(ValueError, match='cover'):
        hosted.state.automation.edit(hosted.owner, schedule['id'], revision=schedule['revision'],
                                     spec={'kind': 'once', 'at': time() + 7200})


# ---------------------------------------------------------------------------
# The front application is the active one
# ---------------------------------------------------------------------------


def _fake_host(monkeypatch, *, osascript):
    from termx.desktop import windows

    class Top:
        def list(self):
            return [{'app': 'Notes', 'pid': 1}]

    def run(command, **_):
        if command[0].endswith('osascript'):
            return type('Done', (), {'stdout': osascript, 'returncode': 0 if osascript else 1})()
        return type('Done', (), {'stdout': '/Applications/Raycast.app/Contents/MacOS/Raycast\n', 'returncode': 0})()

    monkeypatch.setattr(windows.sys, 'platform', 'darwin')
    monkeypatch.setattr(windows, 'WindowCapture', Top)
    monkeypatch.setattr(windows.subprocess, 'run', run)
    return windows


def test_the_front_application_is_the_active_one_not_the_top_window(monkeypatch):
    windows = _fake_host(monkeypatch, osascript='Raycast\ncom.raycast.macos\n4242\n')
    names = windows.frontmost_identity()
    assert 'Raycast' in names and 'com.raycast.macos' in names and 'Notes' not in names
    assert not matches(names, ['Notes'])


def test_an_unreadable_active_application_is_not_replaced_by_the_top_window(monkeypatch):
    assert _fake_host(monkeypatch, osascript='').frontmost_identity() is None
