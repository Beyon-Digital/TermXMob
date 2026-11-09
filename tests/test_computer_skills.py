"""Computer-use skills: application lists, the permission to run unattended, and what reaches the desktop.

A skill that drives the desktop names the applications it may use. The list is checked in front of every
batch and again after it. A check after the batch cannot undo input that was already sent, so the tests
assert that the event says so and that the screenshot is withheld.
"""
from __future__ import annotations

import asyncio
import copy
from time import time
from typing import Any

import pytest

from termx.desktop import apps as apps_module
from termx.desktop.apps import AppBlocked, combine, matches, normalize_apps, switching_hotkey
from termx.identity import Principal
from termx.workspace.extensions import ExtensionService
from test_computer_policy import (  # noqa: F401  (hosted and anyio_backend are fixtures)
    CLICK, Desktop, _approve_plan, _eventually, _events, _finished, _manager, anyio_backend, hosted,
)
from test_durable_workspace import bundle, session, workspace  # noqa: F401  (workspace is a fixture)
from test_agent import wait_for_status

# Application lists and schedule permissions are enforced only where the host can name the front application
# (macOS). Model that host explicitly, so these tests give the same answer on Linux and Windows runners.
pytestmark = pytest.mark.usefixtures('host_names_the_front_application')

NOTES_SKILL = {'format': 'termx-bundle/v1', 'id': 'notes-helper', 'version': '1',
               'permissions': ['desktop-control'], 'computer': {'apps': ['Notes', 'com.apple.Notes']},
               'files': {'skills/notes-helper/SKILL.md': 'Open Notes and type the text the user asked for.'}}


def skill(**changes: Any) -> dict[str, Any]:
    data = copy.deepcopy(NOTES_SKILL)
    data.update(changes)
    return data


# ---------------------------------------------------------------------------
# Application lists
# ---------------------------------------------------------------------------


def test_a_list_names_one_to_twenty_exact_applications():
    assert normalize_apps(['Notes', 'com.apple.Notes', 'notepad.exe', 'Notes']) == ['Notes', 'com.apple.Notes', 'notepad.exe']
    assert normalize_apps(['Google Chrome']) == ['Google Chrome']
    assert len(normalize_apps([f'app{i}' for i in range(20)])) == 20


@pytest.mark.parametrize('bad', [
    [], 'Notes', None, ['*'], ['Note*'], ['com.apple.*'], [''], [' Notes'], ['Notes\n'], [1], [['Notes']], ['a' * 201],
    ['../x'.replace('.', '․')], [f'app{i}' for i in range(21)],
])
def test_a_list_refuses_patterns_and_oversize_input(bad):
    with pytest.raises(ValueError):
        normalize_apps(bad)


def test_the_front_application_matches_by_name_executable_or_bundle_id():
    assert matches(['Notes', '/System/Applications/Notes.app/Contents/MacOS/Notes', 'com.apple.Notes'], ['com.apple.Notes'])
    assert matches(['notes'], ['Notes'])
    assert not matches(['Notes Helper'], ['Notes'])
    assert not matches(None, ['Notes'])
    assert not matches([], ['Notes'])
    assert not matches(['Notes'], [])


def test_lists_from_several_skills_leave_only_what_every_list_allows():
    assert combine([None, None]) is None
    assert combine([['Notes', 'Safari'], None]) == ['Notes', 'Safari']
    assert combine([['Notes', 'Safari'], ['safari', 'Mail']]) == ['Safari']
    assert combine([['Notes'], ['Mail']]) == []


@pytest.mark.parametrize('keys', [['meta', 'tab'], ['cmd', 'tab'], ['alt', 'tab'], ['option', 'tab'], ['command', 'space'],
                                  ['meta', 'grave'], ['meta', 'q'], ['meta', 'h'], ['alt', 'escape'], 'meta'])
def test_hotkeys_that_change_application_are_recognized(keys):
    assert switching_hotkey([{'type': 'keypress', 'keys': keys}]) is not None


@pytest.mark.usefixtures('macos_shortcuts')
def test_a_chord_split_over_actions_is_recognized():
    assert switching_hotkey([{'type': 'key_down', 'keys': ['meta']}, {'type': 'key_down', 'keys': ['tab']}]) is not None
    assert switching_hotkey([{'type': 'key_down', 'keys': ['meta']}, {'type': 'key_up', 'keys': ['meta']},
                             {'type': 'keypress', 'keys': ['tab']}]) is None


@pytest.mark.usefixtures('macos_shortcuts')
@pytest.mark.parametrize('keys', [['enter'], ['meta', 'c'], ['meta', 'v'], ['control', 'a'], ['shift', 'tab'], ['tab'], ['meta', 'n']])
def test_ordinary_shortcuts_are_not_blocked(keys):
    assert switching_hotkey([{'type': 'keypress', 'keys': keys}, {'type': 'type', 'text': 'meta tab'}]) is None


# ---------------------------------------------------------------------------
# Bundles: the desktop-control permission and the optional list
# ---------------------------------------------------------------------------


def test_a_bundle_may_ask_for_desktop_control_and_name_its_applications(workspace):
    service, owner, _ = workspace
    normalized = ExtensionService(service).normalize(skill(), 'termx-bundle/v1')
    assert normalized['permissions'] == ['desktop-control']
    assert normalized['computer'] == {'apps': ['Notes', 'com.apple.Notes']}


def test_a_bundle_without_a_list_keeps_working_and_carries_no_computer_key(workspace):
    service, _, _ = workspace
    data = skill()
    del data['computer']
    assert 'computer' not in ExtensionService(service).normalize(data, 'termx-bundle/v1')


@pytest.mark.parametrize('mutation', [
    lambda b: b.pop('permissions') and None,
    lambda b: b.update(permissions=['files-read']),
    lambda b: b.update(computer={'apps': []}),
    lambda b: b.update(computer={'apps': ['*']}),
    lambda b: b.update(computer={'apps': [f'app{i}' for i in range(21)]}),
    lambda b: b.update(computer={'apps': ['Notes'], 'any': True}),
    lambda b: b.update(computer=['Notes']),
    lambda b: b.update(computer={}),
    lambda b: b.update(permissions=['desktop-control', 'host-admin']),
    lambda b: b.update(permissions=['desktop-view']),
])
def test_a_bundle_with_a_bad_computer_declaration_is_refused(workspace, mutation):
    service, _, _ = workspace
    data = skill()
    mutation(data)
    with pytest.raises(ValueError):
        ExtensionService(service).normalize(data, 'termx-bundle/v1')


def test_the_list_is_part_of_the_reviewed_digest_and_the_permission_diff(workspace):
    service, owner, _ = workspace
    extensions = ExtensionService(service)
    first = extensions.preview(owner, skill())
    second = extensions.preview(owner, skill(computer={'apps': ['Notes', 'Mail']}))
    assert first['digest'] != second['digest']
    assert first['permission_diff']['added'] == ['desktop-control']
    assert first['bundle']['computer']['apps'] == ['Notes', 'com.apple.Notes']


def test_a_dispatch_needs_desktop_control_every_time(workspace, monkeypatch):
    service, owner, path = workspace
    extensions = ExtensionService(service, path / 'installed')
    preview = extensions.preview(owner, skill())
    extensions.install(owner, preview['id'], preview['digest'])

    context = extensions.execution_context(owner, ['notes-helper'], 'project-a', str(path))
    assert context[0]['computer_apps'] == ['Notes', 'com.apple.Notes']

    live = service.state.identity.current_principal
    without = lambda principal: Principal(  # noqa: E731
        principal.id, principal.display_name, tuple(s for s in principal.scopes if s != 'desktop-control'), principal.policy_version)
    monkeypatch.setattr(service.state.identity, 'current_principal', lambda principal: without(live(principal)))
    with pytest.raises(PermissionError):
        extensions.execution_context(owner, ['notes-helper'], 'project-a', str(path))


def test_the_installed_row_lists_the_applications_for_the_ui(workspace):
    service, owner, path = workspace
    extensions = ExtensionService(service, path / 'installed')
    preview = extensions.preview(owner, skill())
    row = extensions.install(owner, preview['id'], preview['digest'])
    assert row['computer_apps'] == ['Notes', 'com.apple.Notes']
    assert 'desktop-control' in row['permissions']


def test_a_skill_without_a_list_declares_none(workspace):
    service, owner, path = workspace
    extensions = ExtensionService(service, path / 'installed')
    data = bundle(permissions=['files-read'])
    extensions.install(owner, *(lambda p: (p['id'], p['digest']))(extensions.preview(owner, data)))
    assert extensions.execution_context(owner, ['fixture'], 'project-a', str(path))[0].get('computer_apps') is None


@pytest.mark.anyio
async def test_a_send_hands_the_combined_list_to_the_task(workspace):
    service, owner, path = workspace
    extensions = ExtensionService(service, path / 'installed')
    service.state.extensions = extensions
    for data in (skill(), skill(id='mail-helper', computer={'apps': ['Notes', 'Mail']},
                                files={'skills/mail-helper/SKILL.md': 'Copy a note into Mail.'})):
        preview = extensions.preview(owner, data)
        extensions.install(owner, preview['id'], preview['digest'])
    seen: dict[str, Any] = {}
    original = service.state.agent.create_task

    async def spy(**kwargs):
        seen.update(kwargs)
        return await original(**kwargs)

    service.state.agent.create_task = spy
    row = session(workspace)
    service.update_session(owner, row['id'], revision=1, changes={'extension_ids': ['notes-helper', 'mail-helper']})
    await service.send(owner, row['id'], prompt='Copy the note', request_id='computer-skill-send')
    assert seen['computer_apps'] == ['Notes']

    plain = session(workspace)
    seen.clear()
    await service.send(owner, plain['id'], prompt='Nothing special', request_id='computer-skill-plain')
    assert seen.get('computer_apps') is None


# ---------------------------------------------------------------------------
# The list is enforced around every batch
# ---------------------------------------------------------------------------


class Switching(Desktop):
    """A desktop where the batch itself moves input to another application."""

    def __init__(self, app: str = 'Notes', after: str = 'Safari') -> None:
        super().__init__(app)
        self.after = after

    async def execute(self, actions, *, cancel=None, guard=None, pointer_guard=None):
        frame = await super().execute(actions, cancel=cancel)
        self.app = self.after
        return frame


async def _run(tmp_path, actions, *, apps, desktop=None, native=False, expect='completed', setup=None):
    manager, store_, fake = _manager(tmp_path, actions, native=native)
    if desktop is not None:
        manager._computer = desktop  # type: ignore[assignment]
        fake = desktop
    if setup:
        setup(manager)
    task = await manager.create_task(prompt='Use the desktop', cwd=str(tmp_path), provider_id='fake', computer_apps=apps)
    await _approve_plan(manager, store_, task['id'])
    done = await wait_for_status(store_, task['id'], expect)
    return manager, store_, fake, done


async def _close(manager, store_):
    await manager.close()
    store_.close()


def test_a_task_in_a_listed_application_acts_normally(tmp_path):
    async def run():
        manager, store_, desktop, done = await _run(tmp_path, CLICK, apps=['Notes'])
        assert desktop.actions == [CLICK]
        assert not _events(done, 'computer.app_blocked')
        assert (done['runtime'] or {})['computer_apps'] == ['Notes']
        await _close(manager, store_)

    asyncio.run(run())


def test_a_task_without_a_list_is_unchanged(tmp_path):
    async def run():
        manager, store_, desktop, done = await _run(tmp_path, CLICK, apps=None, desktop=Desktop('Safari'))
        assert desktop.actions == [CLICK]
        assert 'computer_apps' not in (done['runtime'] or {})
        await _close(manager, store_)

    asyncio.run(run())


def test_input_is_refused_when_another_application_is_in_front(tmp_path):
    async def run():
        manager, store_, desktop, done = await _run(tmp_path, CLICK, apps=['Notes'], desktop=Desktop('Safari'))
        assert desktop.actions == []
        blocked = _events(done, 'computer.app_blocked')
        assert len(blocked) == 1
        payload = blocked[0]['payload']
        assert payload['phase'] == 'before' and payload['input_sent'] is False
        assert payload['front'] == 'Safari' and payload['apps'] == ['Notes']
        finished = _events(done, 'tool.finished')[0]['payload']
        assert finished['result']['refused'] is True
        assert 'Notes' in finished['result']['error']
        await _close(manager, store_)

    asyncio.run(run())


def test_a_native_call_in_the_wrong_application_fails_the_task(tmp_path):
    async def run():
        manager, store_, desktop, done = await _run(
            tmp_path, CLICK, apps=['Notes'], desktop=Desktop('Safari'), native=True, expect='failed')
        assert desktop.actions == []
        assert _events(done, 'computer.app_blocked')
        assert not _events(done, 'computer.screenshot')
        await _close(manager, store_)

    asyncio.run(run())


def test_an_application_change_during_a_batch_is_reported_and_the_screenshot_withheld(tmp_path):
    async def run():
        manager, store_, desktop, done = await _run(tmp_path, CLICK, apps=['Notes'], desktop=Switching())
        assert desktop.actions == [CLICK]
        payload = _events(done, 'computer.app_blocked')[0]['payload']
        assert payload['phase'] == 'after'
        assert payload['input_sent'] is True
        assert payload['front'] == 'Safari'
        assert not _events(done, 'computer.screenshot')
        assert not _events(done, 'computer.observation')
        error = _events(done, 'tool.finished')[0]['payload']['result']['error']
        assert 'already sent' in error
        assert desktop.released >= 1
        await _close(manager, store_)

    asyncio.run(run())


@pytest.mark.parametrize('keys', [['meta', 'tab'], ['alt', 'tab'], ['meta', 'space']])
def test_hotkeys_that_change_application_are_refused_while_a_list_is_active(tmp_path, keys):
    async def run():
        action = [{'type': 'keypress', 'keys': keys}]
        manager, store_, desktop, done = await _run(tmp_path, action, apps=['Notes'])
        assert desktop.actions == []
        payload = _events(done, 'computer.app_blocked')[0]['payload']
        assert payload['phase'] == 'hotkey' and payload['input_sent'] is False
        await _close(manager, store_)

    asyncio.run(run())


def test_the_same_hotkey_is_allowed_without_a_list(tmp_path):
    async def run():
        action = [{'type': 'keypress', 'keys': ['meta', 'tab']}]
        manager, store_, desktop, done = await _run(tmp_path, action, apps=None)
        assert desktop.actions == [action]
        await _close(manager, store_)

    asyncio.run(run())


def test_a_hotkey_late_in_a_batch_stops_the_whole_batch_before_any_input(tmp_path):
    async def run():
        batch = [{'type': 'click', 'x': 1, 'y': 1}, {'type': 'keypress', 'keys': ['cmd', 'tab']}]
        manager, store_, desktop, done = await _run(tmp_path, batch, apps=['Notes'])
        assert desktop.actions == []
        await _close(manager, store_)

    asyncio.run(run())


def test_skills_whose_lists_do_not_overlap_allow_no_application(tmp_path):
    async def run():
        manager, store_, desktop, done = await _run(tmp_path, CLICK, apps=[])
        assert desktop.actions == []
        assert _events(done, 'computer.app_blocked')
        await _close(manager, store_)

    asyncio.run(run())


async def _direct(tmp_path, desktop, apps):
    """One batch sent straight to the manager, without the approval a task would first ask for an unknown target."""
    from termx.agent.providers import ProviderCall
    manager, store_, _ = _manager(tmp_path, CLICK)
    manager._computer = desktop  # type: ignore[assignment]
    task = store_.create_task(prompt='p', cwd=str(tmp_path), provider_id='fake', model='m', limits={})
    store_.update_task(task['id'], runtime={'computer_apps': apps})
    events: list[tuple[str, dict]] = []
    manager._emit = lambda _task, kind, payload: events.append((kind, payload))  # type: ignore[method-assign]
    call = ProviderCall(type='computer', call_id='c1', name='use_computer', actions=CLICK)
    return manager, store_, task, call, events


def test_an_application_that_cannot_be_named_is_refused_where_the_host_can_name_them(tmp_path, monkeypatch):
    async def run():
        monkeypatch.setattr(apps_module, 'verifiable', lambda: True)
        desktop = Desktop(None)
        manager, store_, task, call, events = await _direct(tmp_path, desktop, ['Notes'])
        with pytest.raises(AppBlocked):
            await manager._execute_computer(task['id'], call, asyncio.Event())
        assert desktop.actions == []
        assert [payload['front'] for kind, payload in events if kind == 'computer.app_blocked'] == [None]
        await _close(manager, store_)

    asyncio.run(run())


def test_an_unnameable_application_is_noted_not_blocked_where_the_check_is_best_effort(tmp_path, monkeypatch):
    async def run():
        monkeypatch.setattr(apps_module, 'verifiable', lambda: False)
        desktop = Desktop(None)
        manager, store_, task, call, events = await _direct(tmp_path, desktop, ['Notes'])
        await manager._execute_computer(task['id'], call, asyncio.Event())
        assert desktop.actions == [CLICK]
        kinds = [kind for kind, _ in events]
        assert 'computer.app_unverified' in kinds and 'computer.app_blocked' not in kinds
        await _close(manager, store_)

    asyncio.run(run())


def test_a_bundle_id_in_the_list_matches_through_the_controllers_identity(tmp_path):
    class Named(Desktop):
        def target_identity(self):
            return [self.app, '/Applications/Notes.app/Contents/MacOS/Notes', 'com.apple.Notes']

    async def run():
        manager, store_, desktop, done = await _run(tmp_path, CLICK, apps=['com.apple.Notes'], desktop=Named('Notes'))
        assert desktop.actions == [CLICK]
        await _close(manager, store_)

    asyncio.run(run())


def test_children_of_a_listed_task_stay_inside_the_list(tmp_path):
    async def run():
        manager, store_, _ = _manager(tmp_path, CLICK)
        parent = store_.create_task(prompt='p', cwd=str(tmp_path), provider_id='fake', model='m', limits={})
        store_.update_task(parent['id'], runtime={'computer_apps': ['Notes', 'Mail']})
        child = store_.create_task(prompt='c', cwd=str(tmp_path), provider_id='fake', model='m', limits={}, parent_id=parent['id'])
        store_.update_task(child['id'], runtime={'computer_apps': ['Mail']})
        grandchild = store_.create_task(prompt='g', cwd=str(tmp_path), provider_id='fake', model='m', limits={}, parent_id=child['id'])
        assert manager.computer_limits(grandchild['id'])[0] == ['Mail']
        assert manager.computer_limits(parent['id'])[0] == ['Notes', 'Mail']
        await _close(manager, store_)

    asyncio.run(run())


# ---------------------------------------------------------------------------
# Unattended use needs a permission on the delegation
# ---------------------------------------------------------------------------


def _permission(apps=('Notes',), seconds=60, expires=None):
    return {'apps': list(apps), 'max_seconds': seconds, 'expires_at': expires or time() + 600, 'run_id': 'run-1'}


def _unattended(permission):
    def setup(manager):
        manager.is_unattended = lambda _task_id: True
        manager.computer_permission = lambda _task_id: permission
    return setup


def test_an_unattended_run_with_a_permission_acts_inside_its_applications(tmp_path):
    async def run():
        manager, store_, desktop, done = await _run(tmp_path, CLICK, apps=None, setup=_unattended(_permission()))
        assert desktop.actions == [CLICK]
        await _close(manager, store_)

    asyncio.run(run())


def test_an_unattended_run_stays_out_of_other_applications(tmp_path):
    async def run():
        manager, store_, desktop, done = await _run(
            tmp_path, CLICK, apps=None, desktop=Desktop('Safari'), setup=_unattended(_permission()))
        assert desktop.actions == []
        assert _events(done, 'tool.finished')[0]['payload']['result']['refused'] is True
        await _close(manager, store_)

    asyncio.run(run())


def test_an_unattended_run_with_no_permission_is_still_denied(tmp_path):
    async def run():
        manager, store_, desktop, done = await _run(tmp_path, CLICK, apps=None, setup=_unattended(None))
        assert desktop.actions == []
        assert 'unattended' in _events(done, 'tool.finished')[0]['payload']['result']['error'].lower()
        await _close(manager, store_)

    asyncio.run(run())


def test_the_permission_and_a_skill_list_must_both_allow_the_application(tmp_path):
    async def run():
        manager, store_, desktop, done = await _run(
            tmp_path, CLICK, apps=['Mail'], setup=_unattended(_permission(['Notes'])))
        assert desktop.actions == []
        assert _events(done, 'computer.app_blocked')
        await _close(manager, store_)

    asyncio.run(run())


def test_the_seconds_for_a_run_are_spent_by_the_batches_it_sends(tmp_path):
    async def run():
        manager, store_, _ = _manager(tmp_path, CLICK)
        manager.is_unattended = lambda _task_id: True
        task = store_.create_task(prompt='p', cwd=str(tmp_path), provider_id='fake', model='m', limits={})
        manager.computer_permission = lambda _task_id: {**_permission(seconds=10), 'run_id': task['id']}
        store_.update_task(task['id'], runtime={'computer_spent': 10.0})
        from termx.agent.providers import ProviderCall
        call = ProviderCall(type='computer', call_id='c1', name='use_computer', actions=CLICK)
        with pytest.raises(PermissionError, match='seconds'):
            await manager._execute_computer(task['id'], call, asyncio.Event())
        await _close(manager, store_)

    asyncio.run(run())


def test_a_permission_that_lapsed_before_execution_stops_the_batch(tmp_path):
    async def run():
        manager, store_, desktop = _manager(tmp_path, CLICK)
        manager.is_unattended = lambda _task_id: True
        manager.computer_permission = lambda _task_id: None
        task = store_.create_task(prompt='p', cwd=str(tmp_path), provider_id='fake', model='m', limits={})
        from termx.agent.providers import ProviderCall
        call = ProviderCall(type='computer', call_id='c1', name='use_computer', actions=CLICK)
        with pytest.raises(PermissionError, match='[Uu]nattended'):
            await manager._execute_computer(task['id'], call, asyncio.Event())
        assert desktop.actions == []
        await _close(manager, store_)

    asyncio.run(run())


# ---------------------------------------------------------------------------
# computer_permission on the delegation
# ---------------------------------------------------------------------------


def _create(hosted, request_id='computer-schedule-1', **overrides):
    values = dict(
        request_id=request_id, conversation_id=hosted.conversation['id'], success_criteria='Look at the desktop', max_runs=1,
        limits=None, prompt='Look at the desktop', spec={'kind': 'once', 'at': time() + 60}, grant_days=1,
        computer_permission={'apps': ['Notes'], 'expires_at': time() + 3600, 'max_seconds': 120},
    )
    values.update(overrides)
    return hosted.state.automation.create(hosted.owner, **values)


def _grant(hosted, schedule):
    return hosted.state.workspace.store.get('delegation', schedule['grant_id'])


@pytest.mark.anyio
async def test_a_schedule_can_carry_a_computer_permission(hosted):
    grant = _grant(hosted, _create(hosted))
    assert grant['computer_permission']['apps'] == ['Notes']
    assert grant['computer_permission']['max_seconds'] == 120
    assert grant['computer_permission']['expires_at'] <= grant['expires_at']


@pytest.mark.anyio
async def test_a_schedule_without_one_carries_none(hosted):
    assert 'computer_permission' not in _grant(hosted, _create(hosted, computer_permission=None))


@pytest.mark.anyio
@pytest.mark.parametrize('bad', [
    {'apps': ['Notes'], 'expires_at': time() + 3600},
    {'apps': ['Notes'], 'expires_at': time() + 3600, 'max_seconds': 5},
    {'apps': ['Notes'], 'expires_at': time() + 3600, 'max_seconds': 3601},
    {'apps': ['Notes'], 'expires_at': time() + 3600, 'max_seconds': True},
    {'apps': [], 'expires_at': time() + 3600, 'max_seconds': 60},
    {'apps': ['*'], 'expires_at': time() + 3600, 'max_seconds': 60},
    {'apps': ['Notes'], 'expires_at': time() - 5, 'max_seconds': 60},
    {'apps': ['Notes'], 'expires_at': time() + 3 * 86400, 'max_seconds': 60},
    {'apps': ['Notes'], 'expires_at': float('inf'), 'max_seconds': 60},
    {'apps': ['Notes'], 'expires_at': time() + 3600, 'max_seconds': 60, 'extra': 1},
    'Notes',
])
async def test_a_bad_computer_permission_creates_nothing(hosted, bad):
    store = hosted.state.workspace.store
    before = (len(store.list('goal')), len(store.list('delegation')), len(store.list('schedule')))
    with pytest.raises((ValueError, PermissionError)):
        _create(hosted, computer_permission=bad)
    assert (len(store.list('goal')), len(store.list('delegation')), len(store.list('schedule'))) == before


@pytest.mark.anyio
async def test_the_permission_must_expire_before_the_first_run_is_over(hosted):
    with pytest.raises(ValueError):
        _create(hosted, spec={'kind': 'once', 'at': time() + 7200})


@pytest.mark.anyio
async def test_a_principal_without_desktop_control_cannot_grant_it(hosted, monkeypatch):
    live = hosted.state.identity.current_principal
    monkeypatch.setattr(hosted.state.identity, 'current_principal', lambda principal: (lambda p: Principal(
        p.id, p.display_name, tuple(s for s in p.scopes if s != 'desktop-control'), p.policy_version))(live(principal)))
    with pytest.raises(PermissionError):
        _create(hosted)


def _delegated_task(hosted, schedule, **kw):
    state = hosted.state
    task = state.agent_store.create_task(prompt='p', cwd='/tmp', provider_id='fixture', model='fixture', limits={}, **kw)
    state.workspace.store.create('task', hosted.owner.id, {'conversation_id': hosted.conversation['id'], 'delegation_id': schedule['grant_id']},
                                 None, task['id'])
    return task


@pytest.mark.anyio
async def test_a_delegated_task_and_its_children_resolve_the_permission(hosted):
    schedule = _create(hosted)
    root = _delegated_task(hosted, schedule)
    child = hosted.state.agent_store.create_task(prompt='c', cwd='/tmp', provider_id='fixture', model='fixture', limits={}, parent_id=root['id'])
    for task in (root, child):
        permission = hosted.state.agent.computer_permission(task['id'])
        assert permission['apps'] == ['Notes'] and permission['max_seconds'] == 120
        assert permission['run_id'] == root['id']


@pytest.mark.anyio
async def test_a_task_without_a_delegation_or_a_permission_resolves_none(hosted):
    plain = hosted.state.agent_store.create_task(prompt='p', cwd='/tmp', provider_id='fixture', model='fixture', limits={})
    assert hosted.state.agent.computer_permission(plain['id']) is None
    without = _delegated_task(hosted, _create(hosted, request_id='computer-schedule-none', computer_permission=None))
    assert hosted.state.agent.computer_permission(without['id']) is None


@pytest.mark.anyio
async def test_revoking_the_grant_withdraws_the_permission(hosted):
    schedule = _create(hosted)
    task = _delegated_task(hosted, schedule)
    assert hosted.state.agent.computer_permission(task['id'])
    store = hosted.state.workspace.store
    grant = store.get('delegation', schedule['grant_id'])
    store.update('delegation', grant['id'], {**grant, 'revoked': True})
    assert hosted.state.agent.computer_permission(task['id']) is None


@pytest.mark.anyio
async def test_the_permission_ends_at_its_own_expiry(hosted, monkeypatch):
    task = _delegated_task(hosted, _create(hosted))
    assert hosted.state.agent.computer_permission(task['id'])
    monkeypatch.setattr('termx.workspace.scheduled_computer.time', lambda: time() + 7200)
    assert hosted.state.agent.computer_permission(task['id']) is None


@pytest.mark.anyio
async def test_losing_desktop_control_withdraws_the_permission(hosted, monkeypatch):
    task = _delegated_task(hosted, _create(hosted))
    live = hosted.state.identity.current_principal
    monkeypatch.setattr(hosted.state.identity, 'current_principal', lambda principal: (lambda p: Principal(
        p.id, p.display_name, tuple(s for s in p.scopes if s != 'desktop-control'), p.policy_version))(live(principal)))
    assert hosted.state.agent.computer_permission(task['id']) is None


@pytest.mark.anyio
async def test_a_scheduled_run_with_a_permission_controls_only_its_application(hosted):
    state, owner = hosted.state, hosted.owner
    now = time()
    schedule = _create(hosted, spec={'kind': 'once', 'at': now + 1}, missed='once', enabled=True)
    state.automation.clock = lambda: now + 2
    await state.automation.tick()
    await _eventually(lambda: bool(state.workspace.store.list('schedule_run')) and bool(state.workspace.store.list('schedule_run')[0].get('task_id')))
    task_id = state.workspace.store.list('schedule_run')[0]['task_id']
    await _eventually(lambda: _finished(state, task_id))
    assert state.agent_store.get_task(task_id)['status'] == 'completed'
    assert hosted.desktop.actions == [CLICK]
    assert schedule['grant_id']


@pytest.mark.anyio
async def test_a_scheduled_run_whose_application_is_not_in_front_is_refused(hosted):
    state = hosted.state
    hosted.desktop.app = 'Safari'
    now = time()
    _create(hosted, spec={'kind': 'once', 'at': now + 1}, missed='once', enabled=True)
    state.automation.clock = lambda: now + 2
    await state.automation.tick()
    await _eventually(lambda: bool(state.workspace.store.list('schedule_run')) and bool(state.workspace.store.list('schedule_run')[0].get('task_id')))
    task_id = state.workspace.store.list('schedule_run')[0]['task_id']
    await _eventually(lambda: _finished(state, task_id))
    assert hosted.desktop.actions == []
    finished = _events(state.agent_store.get_task(task_id, include_events=True), 'tool.finished')[0]['payload']
    assert finished['result']['refused'] is True


# ---------------------------------------------------------------------------
# Recorded window workflows publish as computer skills
# ---------------------------------------------------------------------------


class Window:
    def __init__(self, app):
        self.app = app

    def list(self):
        return [{'id': '1', 'pid': 1, 'app': self.app, 'title': 'Test', 'width': 80, 'height': 60},
                {'id': '2', 'pid': 2, 'app': 'Other', 'title': 'Test', 'width': 80, 'height': 60}]

    def capture(self, row):
        from PIL import Image
        import io
        output = io.BytesIO()
        Image.new('RGB', (80, 60), 'white').save(output, 'JPEG')
        return output.getvalue()

    def input(self, row, event):
        pass


async def _published(app, tmp_path):
    from termx.browser.storage import Records
    from termx.desktop.recording import WindowRecordingService
    seen = {}

    class Extensions:
        def preview(self, principal, data, format='termx-bundle/v1'):
            seen['data'], seen['format'] = data, format
            return {'id': 'preview', 'digest': 'digest'}

        def install(self, principal, preview, digest):
            return {'active_digest': digest}

    manager = WindowRecordingService(Records(tmp_path), port=Window(app), session_valid=lambda *_: True)
    capture = manager.start('owner', 's', 1, '1')
    manager.configure(capture['id'], 'owner', recording=True)
    await manager.input(capture['id'], 'owner', {'type': 'key', 'key': 'Tab', 'action': 'down'})
    draft = manager.draft(capture['id'], 'owner', 'tab-through')
    await manager.test(draft['id'], 'owner', manager.start('owner', 's', 1, '2')['id'], {}, True)
    manager.publish(draft['id'], 'owner', object(), Extensions(), '1.0.0')
    return seen


@pytest.mark.anyio
async def test_a_published_recording_asks_for_desktop_control_and_names_its_application(tmp_path):
    seen = await _published('Notes', tmp_path)
    assert seen['data']['permissions'] == ['desktop-control']
    assert seen['data']['computer'] == {'apps': ['Notes']}
    assert seen['format'] == 'skill-markdown/v1'


@pytest.mark.anyio
@pytest.mark.parametrize('app', ['', 'Process 4242'])
async def test_a_recording_of_an_application_the_host_cannot_name_is_not_published(app, tmp_path):
    with pytest.raises(ValueError, match='name'):
        await _published(app, tmp_path)


def test_the_skill_adapter_carries_the_list_through_to_the_bundle(workspace):
    service, owner, _ = workspace
    normalized = ExtensionService(service).normalize(
        {'id': 'rec', 'version': '1', 'markdown': '---\nname: rec\ndescription: Recorded\n---\nSteps', 'permissions': ['desktop-control'],
         'computer': {'apps': ['Notes']}}, 'skill-markdown/v1')
    assert normalized['computer'] == {'apps': ['Notes']}
    assert normalized['permissions'] == ['desktop-control']
