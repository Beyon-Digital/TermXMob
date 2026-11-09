"""Desktop-control skills: application lists, the permission to run unattended, and what reaches the desktop.

A skill that drives the desktop names the applications it may use. The list is checked from the application a
ref belongs to, before any agent-desktop process starts, so there is no input that was already sent.
"""
from __future__ import annotations

import asyncio
import copy
from time import time
from typing import Any

import pytest

from termx.desktop import apps as apps_module
from termx.desktop.apps import combine, matches, matches_app, normalize_apps
from termx.identity import Principal
from termx.workspace.extensions import ExtensionService
from test_computer_policy import (  # noqa: F401  (hosted and anyio_backend are fixtures)
    DesktopAdapter, _eventually, _finished, anyio_backend, approve_plan, events, hosted, notes_app,
)
from test_durable_workspace import bundle, session, workspace  # noqa: F401  (workspace is a fixture)
from test_agent import build_manager, wait_for_status

# A schedule's permission needs a platform with a pinned agent-desktop build. Model that host explicitly, so these
# tests give the same answer on Linux and Windows runners.
pytestmark = pytest.mark.usefixtures('ready_desktop')

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


def test_an_application_matches_by_name_executable_or_bundle_id():
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


def test_list_entries_are_typed_so_an_application_cannot_satisfy_an_identifier_by_its_name():
    assert matches_app('Notes', 'com.apple.Notes', '', ['com.apple.Notes'])
    assert matches_app('Notes', 'com.apple.Notes', '', ['notes'])
    assert not matches_app('com.apple.Notes', 'com.evil.x', '', ['com.apple.Notes'])  # a display name equal to a bundle id
    assert not matches_app('Notes', 'com.apple.Notes', '', ['Com.Apple.Notes.Helper'])
    assert matches_app('Notepad', '', 'notepad.exe', ['Notepad.exe'])
    assert not matches_app('Notepad.exe', '', '', ['Notepad.exe'])


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
# The list is enforced from the application a ref belongs to
# ---------------------------------------------------------------------------


async def _run(tmp_path, desk, *, apps, setup=None, expect='completed'):
    notes_app(desk)
    manager, store_ = build_manager(tmp_path, DesktopAdapter())
    if setup:
        setup(manager)
    task = await manager.create_task(prompt='Use the desktop', cwd=str(tmp_path), provider_id='fake', computer_apps=apps)
    await approve_plan(manager, store_, task['id'])
    done = await wait_for_status(store_, task['id'], expect)
    return manager, store_, done


async def _close(manager, store_):
    await manager.close()
    store_.close()


def test_a_task_in_a_listed_application_acts_normally(tmp_path, ready_desktop):
    async def run():
        manager, store_, done = await _run(tmp_path, ready_desktop, apps=['Notes'])
        assert 'click' in ready_desktop.commands()
        assert not events(done, 'desktop.app_blocked')
        assert (done['runtime'] or {})['computer_apps'] == ['Notes']
        await _close(manager, store_)

    asyncio.run(run())


def test_a_task_without_a_list_is_unchanged(tmp_path, ready_desktop):
    async def run():
        manager, store_, done = await _run(tmp_path, ready_desktop, apps=None)
        assert 'click' in ready_desktop.commands()
        assert 'computer_apps' not in (done['runtime'] or {})
        await _close(manager, store_)

    asyncio.run(run())


def test_an_application_outside_the_list_is_refused_before_any_process(tmp_path, ready_desktop):
    async def run():
        manager, store_, done = await _run(tmp_path, ready_desktop, apps=['Mail'])
        assert {call['argv'][0] for call in ready_desktop.spawns()} == {'list-apps'}  # only the filtered listing ran
        assert 'snapshot' not in ready_desktop.commands() and 'click' not in ready_desktop.commands()
        listed = [event for event in events(done, 'tool.finished')][0]['payload']['result']
        assert listed['count'] == 0  # the model never sees applications it cannot touch
        await _close(manager, store_)

    asyncio.run(run())


def test_a_bundle_id_in_the_list_matches_the_application(tmp_path, ready_desktop):
    async def run():
        manager, store_, done = await _run(tmp_path, ready_desktop, apps=['com.apple.Notes'])
        assert 'click' in ready_desktop.commands()
        await _close(manager, store_)

    asyncio.run(run())


def test_skills_whose_lists_do_not_overlap_allow_no_application(tmp_path, ready_desktop):
    async def run():
        manager, store_, done = await _run(tmp_path, ready_desktop, apps=[])
        assert 'snapshot' not in ready_desktop.commands() and 'click' not in ready_desktop.commands()
        await _close(manager, store_)

    asyncio.run(run())


def test_children_of_a_listed_task_stay_inside_the_list(tmp_path):
    async def run():
        manager, store_ = build_manager(tmp_path, DesktopAdapter())
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


def test_an_unattended_run_with_a_permission_acts_inside_its_applications(tmp_path, ready_desktop):
    async def run():
        manager, store_, done = await _run(tmp_path, ready_desktop, apps=None, setup=_unattended(_permission()))
        assert 'click' in ready_desktop.commands()
        await _close(manager, store_)

    asyncio.run(run())


def test_an_unattended_run_stays_out_of_other_applications(tmp_path, ready_desktop):
    async def run():
        manager, store_, done = await _run(tmp_path, ready_desktop, apps=None, setup=_unattended(_permission(['Mail'])))
        assert 'snapshot' not in ready_desktop.commands() and 'click' not in ready_desktop.commands()
        await _close(manager, store_)

    asyncio.run(run())


def test_an_unattended_run_with_no_permission_is_still_denied(tmp_path, ready_desktop):
    async def run():
        manager, store_, done = await _run(tmp_path, ready_desktop, apps=None, setup=_unattended(None))
        assert ready_desktop.spawns() == []
        assert 'unattended' in events(done, 'tool.finished')[0]['payload']['result']['error'].lower()
        await _close(manager, store_)

    asyncio.run(run())


def test_the_permission_and_a_skill_list_must_both_allow_the_application(tmp_path, ready_desktop):
    async def run():
        manager, store_, done = await _run(tmp_path, ready_desktop, apps=['Mail'], setup=_unattended(_permission(['Notes'])))
        assert 'snapshot' not in ready_desktop.commands() and 'click' not in ready_desktop.commands()
        await _close(manager, store_)

    asyncio.run(run())


def test_the_seconds_for_a_run_are_spent_by_the_calls_it_makes(tmp_path):
    async def run():
        manager, store_ = build_manager(tmp_path, DesktopAdapter())
        manager.is_unattended = lambda _task_id: True
        task = store_.create_task(prompt='p', cwd=str(tmp_path), provider_id='fake', model='m', limits={})
        manager.computer_permission = lambda _task_id: {**_permission(seconds=10), 'run_id': task['id']}
        store_.update_task(task['id'], runtime={'computer_spent': 10.0})
        with pytest.raises(PermissionError, match='seconds'):
            async with manager.desktop_budget(task['id']):
                pytest.fail('a spent budget must refuse the call')
        await _close(manager, store_)

    asyncio.run(run())


def test_a_call_is_cut_off_when_the_runs_time_ends_and_the_time_survives(tmp_path):
    async def run():
        manager, store_ = build_manager(tmp_path, DesktopAdapter())
        manager.is_unattended = lambda _task_id: True
        task = store_.create_task(prompt='p', cwd=str(tmp_path), provider_id='fake', model='m', limits={})
        manager.computer_permission = lambda _task_id: {**_permission(seconds=10), 'run_id': task['id']}
        store_.update_task(task['id'], runtime={'computer_spent': 9.5})
        with pytest.raises(PermissionError, match='seconds'):
            async with manager.desktop_budget(task['id']):
                await asyncio.sleep(5)
        assert manager._computer_spent(task['id']) >= 10.0  # charged in full, kept on the task row
        await _close(manager, store_)

    asyncio.run(run())


def test_a_permission_that_lapsed_before_execution_stops_the_call(tmp_path, ready_desktop):
    async def run():
        manager, store_ = build_manager(tmp_path, DesktopAdapter())
        manager.is_unattended = lambda _task_id: True
        manager.computer_permission = lambda _task_id: None
        task = store_.create_task(prompt='p', cwd=str(tmp_path), provider_id='fake', model='m', limits={})
        with pytest.raises(PermissionError, match='[Uu]nattended'):
            async with manager.desktop_budget(task['id']):
                pass
        assert ready_desktop.spawns() == []
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
    assert 'click' in hosted.desk.commands()
    assert schedule['grant_id']


@pytest.mark.anyio
async def test_a_scheduled_run_whose_permission_names_another_application_is_refused(hosted):
    state = hosted.state
    now = time()
    _create(hosted, spec={'kind': 'once', 'at': now + 1}, missed='once', enabled=True,
            computer_permission={'apps': ['Mail'], 'expires_at': time() + 3600, 'max_seconds': 120})
    state.automation.clock = lambda: now + 2
    await state.automation.tick()
    await _eventually(lambda: bool(state.workspace.store.list('schedule_run')) and bool(state.workspace.store.list('schedule_run')[0].get('task_id')))
    task_id = state.workspace.store.list('schedule_run')[0]['task_id']
    await _eventually(lambda: _finished(state, task_id))
    assert 'click' not in hosted.desk.commands() and 'snapshot' not in hosted.desk.commands()


# ---------------------------------------------------------------------------
# Recorded window workflows are retired; a skill is still how an application list is declared
# ---------------------------------------------------------------------------


def test_no_front_application_lookup_remains_so_no_os_is_special():
    """A list is enforced from the application a ref belongs to. Nothing asks which window is in front, so a schedule's
    computer permission does not depend on the OS being able to answer that."""
    from termx.desktop import windows
    for name in ('frontmost_app', 'frontmost_pid', 'frontmost_identity', 'pointer_in_front_app'):
        assert not hasattr(windows, name), name
    for name in ('switching_hotkey', 'KEY_ALIASES'):
        assert not hasattr(apps_module, name), name


def test_the_skill_adapter_carries_the_list_through_to_the_bundle(workspace):
    service, owner, _ = workspace
    normalized = ExtensionService(service).normalize(
        {'id': 'rec', 'version': '1', 'markdown': '---\nname: rec\ndescription: Recorded\n---\nSteps', 'permissions': ['desktop-control'],
         'computer': {'apps': ['Notes']}}, 'skill-markdown/v1')
    assert normalized['computer'] == {'apps': ['Notes']}
    assert normalized['permissions'] == ['desktop-control']
