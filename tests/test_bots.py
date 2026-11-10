"""Bots: a durable background agent that runs as its owner under a ceiling.

A bot is a workspace record joining an owner-private custom agent (its `.agent.md`)
and a durable Internal Agent conversation. The ceiling bounds what the bot may ever
do; editing the prompt or permissions never widens it.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from test_agent import FakeAdapter, build_manager
from termx.agent.policies.engine import PolicyEngine
from termx.agent.providers import ProviderCall, ProviderTurn
from termx.agent.tools import default_registry
from termx.agent.tools.registry import ToolContext
from termx.agents.registry import AgentRegistry
from termx.identity import AuthenticationService
from termx.workspace.bots import MAX_BOTS, default_ceiling, normalize_ceiling
from termx.workspace.service import WorkspaceService
from termx.workspace.store import Conflict


class BotAdapter(FakeAdapter):
    """CoordinateCase holds the main agent's turn open; every other prompt finishes at once."""

    def __init__(self):
        super().__init__()
        self.prompts = []

    async def turn(self, **kwargs):
        prompt = kwargs.get('prompt') or ''
        self.prompts.append(prompt)
        self.turns += 1
        if 'HOLD' in prompt or 'CoordinateCase' in prompt:
            await asyncio.Event().wait()
        return ProviderTurn(response_id=f'r{self.turns}', text='done', calls=[],
                            usage={'input_tokens': 1, 'output_tokens': 1}, output_items=[])


def setup(tmp_path):
    adapter = BotAdapter()
    manager, ledger = build_manager(tmp_path, adapter)
    identity = AuthenticationService(tmp_path / 'identity.sqlite3')
    owner = identity.setup_owner('owner', 'strong synthetic password')
    state = SimpleNamespace(identity=identity, agent_store=ledger, agent=manager,
                            engines=SimpleNamespace(engines=lambda: []),
                            agent_registry=AgentRegistry(ledger, str(tmp_path / 'agents')))
    workspace = WorkspaceService(state, tmp_path / 'workspace.sqlite3')
    workspace.principal_lookup = identity.principal_by_id
    workspace.orchestrator.ensure(owner)
    return workspace, owner, manager, ledger, state.agent_registry, identity


async def close(workspace, manager, ledger):
    for worker in list(manager._workers.values()):
        worker.cancel()
    await manager.close()
    await workspace.close()
    ledger.close()


def make(workspace, owner, name='Triage', template='inbox_triager', **extra):
    return workspace.bots.create(owner, name=name, template=template, **extra)


def call(_tool, _call_id='c1', **arguments):
    return ProviderCall(type='function', call_id=_call_id, name=_tool, arguments=arguments)


async def main_turn(workspace, owner, manager):
    primary = workspace.orchestrator.binding(owner.id)['conversation_id']
    parent = await workspace.send(owner, primary, prompt='CoordinateCase', request_id='primary-request')
    await manager.resolve_approval(parent['id'], parent['approvals'][0]['id'], 'approved')
    await asyncio.sleep(0)
    return parent


# -- creation ------------------------------------------------------------------

def test_create_from_template_binds_agent_file_conversation_and_ceiling_rules(tmp_path):
    workspace, owner, manager, ledger, registry, _ = setup(tmp_path)
    try:
        bot = make(workspace, owner)
        assert bot['state'] == 'active' and bot['template'] == 'inbox_triager' and bot['project_id'] is None
        slug = bot['custom_agent_id'].removeprefix('agent.')
        agent = registry.load(slug)
        assert agent.tools and agent.tools_mode == 'explicit' and not agent.tools_omitted
        assert set(agent.tools) <= set(bot['ceiling']['tools'])
        assert not {'run_shell', 'git_push', 'computer', 'write_file'} & set(bot['ceiling']['tools'])
        session = workspace.session(owner, bot['conversation_id'], turns=False)
        assert session['custom_agent_id'] == bot['custom_agent_id'] and session['engine'] == 'internal'
        assert session['mode'] == 'ask'
        denied = ledger.list_policy_rules(scope_type='custom_agent', scope_id=bot['custom_agent_id'], effect='deny')
        caps = {cap for rule in denied for cap in rule['capabilities']}
        assert {'net.outbound:any', 'git.publish', 'computer.control'} <= caps
        assert bot['peers'] == []
        assert [row['id'] for row in workspace.bots.list(owner)] == [bot['id']]
    finally:
        asyncio.run(close(workspace, manager, ledger))


def test_create_is_all_or_nothing(tmp_path, monkeypatch):
    workspace, owner, manager, ledger, registry, _ = setup(tmp_path)
    try:
        def explode(*args, **kwargs):
            raise RuntimeError('conversation store unavailable')
        monkeypatch.setattr(workspace, 'create_session', explode)
        with pytest.raises(RuntimeError):
            make(workspace, owner)
        assert registry.list() == [] and ledger.list_custom_agents() == []
        assert ledger.list_policy_rules(scope_type='custom_agent') == []
        assert workspace.store.list('bot') == []
        assert not list((tmp_path / 'agents').glob('*.bak'))
    finally:
        asyncio.run(close(workspace, manager, ledger))


def test_create_validates_input_and_caps_the_number_of_bots(tmp_path):
    workspace, owner, manager, ledger, registry, _ = setup(tmp_path)
    try:
        with pytest.raises(ValueError, match='name'):
            workspace.bots.create(owner, name='  ', instructions='x')
        with pytest.raises(ValueError, match='template'):
            workspace.bots.create(owner, name='A', template='nope')
        with pytest.raises(ValueError, match='instructions'):
            workspace.bots.create(owner, name='A')
        with pytest.raises(ValueError, match='project'):
            workspace.bots.create(owner, name='Watcher', template='repo_watcher')
        with pytest.raises(ValueError, match='tool'):
            workspace.bots.create(owner, name='A', instructions='x', tools=['not_a_tool'])
        with pytest.raises(ValueError, match='tool'):
            workspace.bots.create(owner, name='A', instructions='x', tools=['spawn_subagent'])
        for index in range(MAX_BOTS):
            workspace.bots.create(owner, name=f'Bot {index}', instructions='Do the thing')
        with pytest.raises(Conflict, match='20'):
            workspace.bots.create(owner, name='One too many', instructions='Do the thing')
        assert len(registry.list()) == MAX_BOTS
    finally:
        asyncio.run(close(workspace, manager, ledger))


def test_ceiling_normalization_never_accepts_all_tools():
    assert normalize_ceiling(None)['tools'] == default_ceiling()['tools']
    for bad in ({'tools': 'all'}, {'tools': ['all']}, {'tools': []}, {'tools': ['*']},
                {'tools': ['read_file'], 'approval_mode': 'yolo'}, {'tools': ['read_file'], 'sandbox_profile': 'root'},
                {'tools': ['read_file'], 'extra': 1}, {'tools': ['plan_nodes']}):
        with pytest.raises(ValueError):
            normalize_ceiling(bad)


# -- editing, state and archive ----------------------------------------------------

def test_two_live_bots_cannot_share_a_name_and_a_bot_may_keep_its_own(tmp_path):
    workspace, owner, manager, ledger, registry, _ = setup(tmp_path)
    try:
        first = make(workspace, owner, name='Beta')
        with pytest.raises(Conflict, match='already have a bot named'):
            make(workspace, owner, name='  beta ')
        other = make(workspace, owner, name='Gamma')
        with pytest.raises(Conflict, match='already have a bot named'):
            workspace.bots.patch(owner, other['id'], revision=other['revision'], name='BETA')
        same = workspace.bots.patch(owner, first['id'], revision=first['revision'], name='Beta', instructions='Be brief.')
        assert same['name'] == 'Beta'
    finally:
        asyncio.run(close(workspace, manager, ledger))


def test_patch_is_revision_checked_and_cannot_exceed_the_ceiling(tmp_path):
    workspace, owner, manager, ledger, registry, _ = setup(tmp_path)
    try:
        bot = make(workspace, owner)
        before = workspace.store.get('conversation', bot['conversation_id'])['custom_agent_revision']
        patched = workspace.bots.patch(owner, bot['id'], revision=bot['revision'], instructions='Be brief.', name='Quiet triage')
        assert patched['revision'] == bot['revision'] + 1 and patched['instructions'] == 'Be brief.'
        assert registry.load(bot['custom_agent_id'].removeprefix('agent.')).instructions.strip() == 'Be brief.'
        assert workspace.store.get('conversation', bot['conversation_id'])['custom_agent_revision'] != before
        with pytest.raises(Conflict):
            workspace.bots.patch(owner, bot['id'], revision=bot['revision'], instructions='stale')
        with pytest.raises(PermissionError, match='ceiling'):
            workspace.bots.patch(owner, bot['id'], revision=patched['revision'], tools=['read_file', 'run_shell'])
        with pytest.raises(PermissionError, match='ceiling'):
            workspace.bots.patch(owner, bot['id'], revision=patched['revision'], approval_mode='autonomous')
        with pytest.raises(ValueError, match='peer'):
            workspace.bots.patch(owner, bot['id'], revision=patched['revision'], peers=[bot['id']])
        other = make(workspace, owner, name='Other')
        peered = workspace.bots.patch(owner, bot['id'], revision=patched['revision'], peers=[other['id']])
        assert peered['peers'] == [other['id']]
    finally:
        asyncio.run(close(workspace, manager, ledger))


def test_set_ceiling_narrows_the_preset_and_never_widens_it(tmp_path):
    workspace, owner, manager, ledger, registry, _ = setup(tmp_path)
    try:
        bot = make(workspace, owner)
        wider = {**bot['ceiling'], 'tools': [*bot['ceiling']['tools'], 'run_shell'], 'approval_mode': 'remember'}
        widened = workspace.bots.set_ceiling(owner, bot['id'], revision=bot['revision'], ceiling=wider)
        assert 'run_shell' in widened['ceiling']['tools']
        assert 'run_shell' not in widened['tools']  # the preset stays as it was until the owner opts in
        assert widened['effective']['approval_mode'] == 'standard'
        narrow = {**wider, 'tools': ['read_file']}
        narrowed = workspace.bots.set_ceiling(owner, bot['id'], revision=widened['revision'], ceiling=narrow)
        assert narrowed['tools'] == ['read_file'] and narrowed['effective']['tools'] == ['read_file']
        with pytest.raises(ValueError):
            workspace.bots.set_ceiling(owner, bot['id'], revision=narrowed['revision'], ceiling={**narrow, 'tools': []})
        no_net = {**narrow, 'deny_capabilities': []}
        cleared = workspace.bots.set_ceiling(owner, bot['id'], revision=narrowed['revision'], ceiling=no_net)
        assert ledger.list_policy_rules(scope_type='custom_agent', scope_id=bot['custom_agent_id'], effect='deny') == []
        assert cleared['ceiling']['deny_capabilities'] == []
    finally:
        asyncio.run(close(workspace, manager, ledger))


def test_pause_blocks_runs_and_resume_restores_them(tmp_path):
    async def run():
        workspace, owner, manager, ledger, registry, _ = setup(tmp_path)
        try:
            bot = make(workspace, owner)
            paused = workspace.bots.set_state(owner, bot['id'], 'paused')
            assert paused['state'] == 'paused'
            with pytest.raises(Conflict, match='paused'):
                await workspace.bots.run_now(owner, bot['id'])
            with pytest.raises(Conflict, match='paused'):
                await workspace.send(owner, bot['conversation_id'], prompt='hello', request_id='direct-1')
            resumed = workspace.bots.set_state(owner, bot['id'], 'active')
            assert resumed['state'] == 'active'
            task = await workspace.bots.run_now(owner, bot['id'])
            assert task['id']
        finally:
            await close(workspace, manager, ledger)
    asyncio.run(run())


def test_archive_removes_file_rules_and_schedule_grants(tmp_path):
    workspace, owner, manager, ledger, registry, _ = setup(tmp_path)
    try:
        bot = make(workspace, owner)
        goal = workspace.store.create('goal', owner.id, {'conversation_id': bot['conversation_id'], 'status': 'active', 'runs_started': 0, 'max_runs': 5})
        grant = workspace.store.create('delegation', owner.id, {'goal_id': goal['id'], 'revoked': False, 'expires_at': 9e12, 'used_runs': 0, 'max_runs': 5})
        schedule = workspace.store.create('schedule', owner.id, {'goal_id': goal['id'], 'grant_id': grant['id'], 'enabled': True, 'state': 'active'})
        ledger.create_policy_rule(effect='allow', scope_type='custom_agent', scope_id=bot['custom_agent_id'], action_type='tool',
                                  tool='read_file', fingerprint='fp', display='read_file')
        archived = workspace.bots.archive(owner, bot['id'])
        assert archived['state'] == 'archived'
        assert registry.load(bot['custom_agent_id'].removeprefix('agent.')) is None
        assert ledger.get_custom_agent(bot['custom_agent_id']) is None
        assert ledger.list_policy_rules(scope_type='custom_agent', scope_id=bot['custom_agent_id']) == []
        assert workspace.store.get('delegation', grant['id'])['revoked'] is True
        stored = workspace.store.get('schedule', schedule['id'])
        assert stored['enabled'] is False and stored['archived'] is True
        assert workspace.store.get('goal', goal['id'])['status'] == 'cancelled'
        assert workspace.bots.list(owner) == []
        assert [row['id'] for row in workspace.bots.list(owner, include_archived=True)] == [bot['id']]
        with pytest.raises(Conflict):
            workspace.bots.set_state(owner, bot['id'], 'active')
        # An archived bot frees its slot under the cap.
        make(workspace, owner, name='Replacement')
    finally:
        asyncio.run(close(workspace, manager, ledger))


def test_other_owners_cannot_see_or_change_a_bot(tmp_path):
    workspace, owner, manager, ledger, registry, identity = setup(tmp_path)
    try:
        bot = make(workspace, owner)
        stranger = identity.create_local_user('mallory', 'another strong password', list(owner.scopes))
        assert workspace.bots.list(stranger) == []
        with pytest.raises(KeyError):
            workspace.bots.get(stranger, bot['id'])
        with pytest.raises(KeyError):
            workspace.bots.set_state(stranger, bot['id'], 'paused')
        with pytest.raises(KeyError):
            workspace.bots.archive(stranger, bot['id'])
    finally:
        asyncio.run(close(workspace, manager, ledger))


# -- ceiling at run time -------------------------------------------------------------

def test_send_refuses_a_hand_edited_agent_file_that_exceeds_the_ceiling(tmp_path):
    async def run():
        workspace, owner, manager, ledger, registry, _ = setup(tmp_path)
        try:
            bot = make(workspace, owner)
            slug = bot['custom_agent_id'].removeprefix('agent.')
            path = tmp_path / 'agents' / f'{slug}.agent.md'
            original = path.read_text()
            for edit, label in (
                (lambda text: text.replace('- read_file', '- read_file\n- run_shell', 1), 'run_shell'),
                (lambda text: text.replace('approval_mode: standard', 'approval_mode: autonomous'), 'approval'),
            ):
                edited = edit(original)
                assert edited != original, label
                path.write_text(edited)
                registry.sync()
                with pytest.raises(PermissionError, match='ceiling'):
                    await workspace.send(owner, bot['conversation_id'], prompt='go', request_id=f'edited-{label}')
                with pytest.raises(PermissionError, match='ceiling'):
                    await workspace.bots.run_now(owner, bot['id'])
            path.write_text(original)
            registry.sync()
            view = workspace.bots.get(owner, bot['id'])
            assert view['violations'] == []
        finally:
            await close(workspace, manager, ledger)
    asyncio.run(run())


def test_effective_authority_is_the_intersection_of_preset_and_ceiling(tmp_path):
    workspace, owner, manager, ledger, registry, _ = setup(tmp_path)
    try:
        bot = make(workspace, owner)
        slug = bot['custom_agent_id'].removeprefix('agent.')
        agent = registry.load(slug)
        agent.tools = ['read_file', 'run_shell']  # a hand edit past the ceiling
        agent.approval_mode = 'autonomous'
        registry.save(agent, source='bot')
        view = workspace.bots.get(owner, bot['id'])
        assert view['effective']['tools'] == ['read_file']
        assert view['effective']['approval_mode'] == 'standard'
        assert view['effective']['sandbox_profile'] == 'agent'
        assert any('run_shell' in problem for problem in view['violations'])
    finally:
        asyncio.run(close(workspace, manager, ledger))


def test_rules_list_excludes_ceiling_denies_and_can_be_revoked(tmp_path):
    workspace, owner, manager, ledger, registry, _ = setup(tmp_path)
    try:
        bot = make(workspace, owner)
        rule = ledger.create_policy_rule(effect='allow', scope_type='custom_agent', scope_id=bot['custom_agent_id'],
                                         action_type='tool', tool='read_file', fingerprint='fp', display='read_file')
        view = workspace.bots.get(owner, bot['id'])
        assert [item['id'] for item in view['rules']] == [rule['id']]
        managed = next(r for r in ledger.list_policy_rules(scope_type='custom_agent', scope_id=bot['custom_agent_id'])
                       if r['id'] != rule['id'])
        with pytest.raises(Conflict, match='ceiling'):
            workspace.bots.revoke_rule(owner, bot['id'], managed['id'])
        with pytest.raises(KeyError):
            workspace.bots.revoke_rule(owner, bot['id'], 'unknown-rule')
        foreign = ledger.create_policy_rule(effect='allow', scope_type='custom_agent', scope_id='someone-else',
                                            action_type='tool', tool='read_file', fingerprint='fp2', display='x')
        with pytest.raises(KeyError):
            workspace.bots.revoke_rule(owner, bot['id'], foreign['id'])
        workspace.bots.revoke_rule(owner, bot['id'], rule['id'])
        assert workspace.bots.get(owner, bot['id'])['rules'] == []
    finally:
        asyncio.run(close(workspace, manager, ledger))


# -- running -------------------------------------------------------------------------

def test_at_most_three_bot_tasks_run_at_once(tmp_path):
    async def run():
        workspace, owner, manager, ledger, registry, _ = setup(tmp_path)
        try:
            bots = [workspace.bots.create(owner, name=f'B{i}', instructions='Do the thing') for i in range(4)]
            for bot in bots[:3]:
                await workspace.bots.assign_task(owner, bot['id'], prompt='HOLD this', request_id=f'hold-{bot["id"]}')
            with pytest.raises(Conflict, match='3'):
                await workspace.bots.assign_task(owner, bots[3]['id'], prompt='HOLD this', request_id='hold-4')
        finally:
            await close(workspace, manager, ledger)
    asyncio.run(run())


def test_assign_task_records_where_the_result_should_be_reported(tmp_path):
    async def run():
        workspace, owner, manager, ledger, registry, _ = setup(tmp_path)
        try:
            bot = make(workspace, owner)
            other = make(workspace, owner, name='Other')
            first = await workspace.bots.assign_task(owner, bot['id'], prompt='Summarise', request_id='a-1')
            record = workspace.store.get('bot_assignment', first['id'])
            assert record['bot_id'] == bot['id'] and record['report_to'] == {'kind': 'owner', 'id': None}
            second = await workspace.bots.assign_task(owner, other['id'], prompt='Summarise', request_id='a-2',
                                                     report_to='bot:' + bot['id'])
            assert workspace.store.get('bot_assignment', second['id'])['report_to'] == {'kind': 'bot', 'id': bot['id']}
            for bad in ('somebody', 'bot:missing', 'bot:'):
                with pytest.raises(ValueError, match='report'):
                    await workspace.bots.assign_task(owner, bot['id'], prompt='x', request_id='a-' + bad, report_to=bad)
            # Replaying the request returns the same task instead of starting another.
            await asyncio.sleep(0.05)
            again = await workspace.bots.assign_task(owner, bot['id'], prompt='Summarise', request_id='a-1')
            assert again['id'] == first['id']
        finally:
            await close(workspace, manager, ledger)
    asyncio.run(run())


# -- orchestrator tools --------------------------------------------------------------

NAMES = ('list_bots', 'bot_status', 'pause_bot', 'create_bot', 'update_bot', 'resume_bot', 'delete_bot',
         'assign_bot_task', 'schedule_bot')


def test_tools_are_registered_main_agent_only_with_the_right_approvals():
    registry = default_registry()
    assert set(NAMES) <= set(registry.names())
    hidden = {tool['name'] for tool in registry.provider_tools(allow_subagents=False)}
    assert not set(NAMES) & hidden
    read_only = {tool['name'] for tool in registry.provider_tools(read_only=True)}
    assert not set(NAMES) & read_only
    for name in ('list_bots', 'bot_status', 'pause_bot'):
        assert registry.get(name).approval == 'never'
    for name in ('create_bot', 'update_bot', 'resume_bot', 'delete_bot', 'assign_bot_task', 'schedule_bot'):
        assert registry.get(name).approval == 'always'


def test_create_and_assign_need_approval_that_auto_cannot_give(tmp_path):
    async def run():
        workspace, owner, manager, ledger, registry, _ = setup(tmp_path)
        try:
            parent = await main_turn(workspace, owner, manager)
            task = ledger.get_task(parent['id'])
            runtime = {'auto': {'enabled': True}}
            ctx = ToolContext(task_id=parent['id'], cwd=task['cwd'], task={**task, 'runtime': runtime}, read_only=False,
                              cancel=asyncio.Event(), emit=lambda *_: {}, store=ledger,
                              manager=SimpleNamespace(orchestrator=workspace.orchestrator), policy_engine=PolicyEngine(ledger))
            tools = default_registry()
            for name, args in (('create_bot', {'name': 'Triage', 'template': 'inbox_triager'}),
                               ('assign_bot_task', {'bot_id': 'x', 'task': 'do it'})):
                decision = tools.get(name).decide(call(name, **args), ctx)
                assert decision.approval_required and decision.auto_resolved is None, name
                assert decision.remember_options == (), name
            created = tools.get('create_bot').decide(call('create_bot', name='Triage', template='inbox_triager'), ctx)
            assert 'Triage' in created.consequence and 'read_file' in created.consequence
        finally:
            await close(workspace, manager, ledger)
    asyncio.run(run())


def test_main_agent_manages_bots_through_command_with_receipts(tmp_path):
    async def run():
        workspace, owner, manager, ledger, registry, _ = setup(tmp_path)
        try:
            parent = await main_turn(workspace, owner, manager)
            command = workspace.orchestrator.command
            created = await command(parent['id'], call('create_bot', 'cb1', name='Triage', template='inbox_triager'))
            assert created['ok'] and created['bot']['state'] == 'active'
            bot_id = created['bot']['id']
            replay = await command(parent['id'], call('create_bot', 'cb1', name='Triage', template='inbox_triager'))
            assert replay == created and len(workspace.bots.list(owner)) == 1
            with pytest.raises(Conflict, match='reused'):
                await command(parent['id'], call('create_bot', 'cb1', name='Different', template='inbox_triager'))
            listed = await command(parent['id'], call('list_bots', 'lb'))
            assert [row['id'] for row in listed['bots']] == [bot_id]
            status = await command(parent['id'], call('bot_status', 'bs', bot_id=bot_id))
            assert status['bot']['id'] == bot_id and 'revision' in status['bot']
            updated = await command(parent['id'], call('update_bot', 'ub', bot_id=bot_id, instructions='Be terse.'))
            assert updated['bot']['instructions'] == 'Be terse.'
            refused = await command(parent['id'], call('update_bot', 'ub2', bot_id=bot_id, tools=['read_file', 'run_shell']))
            assert refused['ok'] is False and 'ceiling' in refused['error']
            paused = await command(parent['id'], call('pause_bot', 'pb', bot_id=bot_id))
            assert paused['bot']['state'] == 'paused'
            resumed = await command(parent['id'], call('resume_bot', 'rb', bot_id=bot_id))
            assert resumed['bot']['state'] == 'active'
            assigned = await command(parent['id'], call('assign_bot_task', 'at', bot_id=bot_id, task='Summarise', report_to='orchestrator'))
            assert assigned['ok'] and assigned['task_id']
            assert workspace.store.get('bot_assignment', assigned['task_id'])['report_to'] == {'kind': 'orchestrator', 'id': None}
            unknown = await command(parent['id'], call('bot_status', 'bs2', bot_id='nope'))
            assert unknown['ok'] is False
            deleted = await command(parent['id'], call('delete_bot', 'db', bot_id=bot_id))
            assert deleted['bot']['state'] == 'archived'
        finally:
            await close(workspace, manager, ledger)
    asyncio.run(run())


def test_schedule_bot_asks_the_owner_and_reports_when_schedules_are_unavailable(tmp_path):
    # Full scheduling behaviour is covered in test_bot_schedules.py; here the host has no scheduler.
    async def run():
        workspace, owner, manager, ledger, registry, _ = setup(tmp_path)
        try:
            parent = await main_turn(workspace, owner, manager)
            bot = make(workspace, owner)
            args = dict(bot_id=bot['id'], task='Check', spec={'kind': 'interval', 'seconds': 3600})
            result = await workspace.orchestrator.command(parent['id'], call('schedule_bot', 's1', **args))
            assert result['ok'] is False and 'unavailable' in result['error']
            ctx = SimpleNamespace(task_id=parent['id'], manager=SimpleNamespace(orchestrator=workspace.orchestrator))
            decision = default_registry().get('schedule_bot').decide(call('schedule_bot', **args), ctx)
            assert decision.approval_required and not decision.allowed
        finally:
            await close(workspace, manager, ledger)
    asyncio.run(run())


def test_scheduled_runs_and_bots_cannot_manage_bots(tmp_path):
    async def run():
        workspace, owner, manager, ledger, registry, _ = setup(tmp_path)
        try:
            bot = make(workspace, owner)
            # A bot's own task is not the main agent.
            task = await workspace.bots.assign_task(owner, bot['id'], prompt='HOLD', request_id='own')
            with pytest.raises(PermissionError):
                await workspace.orchestrator.command(task['id'], call('create_bot', 'x1', name='Spawn', template='inbox_triager'))
            with pytest.raises(PermissionError):
                await workspace.orchestrator.command(task['id'], call('update_bot', 'x2', bot_id=bot['id'], instructions='free'))
            # The main agent's turn started by a schedule carries a delegation and is refused too.
            parent = await main_turn(workspace, owner, manager)
            meta = workspace.store.get('task', parent['id'])
            workspace.store.update('task', parent['id'], {**meta, 'delegation_id': 'grant-1'})
            for name, args in (('create_bot', {'name': 'N', 'template': 'inbox_triager'}),
                               ('assign_bot_task', {'bot_id': bot['id'], 'task': 'x'}),
                               ('delete_bot', {'bot_id': bot['id']})):
                with pytest.raises(PermissionError):
                    await workspace.orchestrator.command(parent['id'], call(name, 'x3', **args))
            assert len(workspace.bots.list(owner)) == 1
        finally:
            await close(workspace, manager, ledger)
    asyncio.run(run())


def test_task_graph_node_can_run_as_a_bot(tmp_path):
    async def run():
        workspace, owner, manager, ledger, registry, _ = setup(tmp_path)
        try:
            bot = make(workspace, owner)
            graph = workspace.orchestrator.graph
            nodes = graph.plan(owner, [{'key': 'a', 'title': 'A', 'prompt': 'a work', 'mode': 'ask', 'bot_id': bot['id']}])
            assert nodes[0]['bot_id'] == bot['id']
            with pytest.raises(ValueError, match='bot'):
                graph.plan(owner, [{'key': 'b', 'title': 'B', 'prompt': 'b work', 'bot_id': 'missing'}])
            started = await graph.start_ready(owner)
            task = ledger.get_task(started[0]['task_id'])
            assert task['custom_agent_id'] == bot['custom_agent_id']
            # A bot that was paused after planning is not started.
            more = graph.plan(owner, [{'key': 'c', 'title': 'C', 'prompt': 'c work', 'mode': 'ask', 'bot_id': bot['id']}])
            workspace.bots.set_state(owner, bot['id'], 'paused')
            await graph.start_ready(owner)
            node = next(row for row in graph.board(owner)['nodes'] if row['id'] == more[0]['id'])
            assert node['status'] == 'failed' and 'paused' in node['error']
        finally:
            await close(workspace, manager, ledger)
    asyncio.run(run())


# -- review fixes -----------------------------------------------------------------------

def test_bot_tool_names_agree_across_the_service_the_registry_and_the_sub_agent_filter():
    from termx.agent.tools import bots as bot_tools
    from termx.agent.tools.registry import ToolRegistry
    from termx.workspace.bots import BOT_TOOLS
    scratch = ToolRegistry()
    bot_tools.register(scratch)
    assert set(scratch.names()) == set(BOT_TOOLS)
    assert BOT_TOOLS <= ToolRegistry._SUBAGENT_TOOLS


def test_clearing_the_agent_binding_does_not_skip_pause_or_the_ceiling(tmp_path):
    async def run():
        workspace, owner, manager, ledger, registry, _ = setup(tmp_path)
        try:
            bot = make(workspace, owner)
            workspace.bots.set_state(owner, bot['id'], 'paused')
            cid = bot['conversation_id']
            session = workspace.session(owner, cid, turns=False)
            for change in ({'custom_agent_id': None}, {'engine': 'codex'}, {'workflow': 'browser'}):
                with pytest.raises((Conflict, ValueError)):
                    workspace.update_session(owner, cid, revision=session['revision'], changes=change)
            # Even if the binding is cleared behind the service's back, the bot's own conversation is still guarded.
            row = workspace.store.get('conversation', cid)
            workspace.store.update('conversation', cid, {**row, 'custom_agent_id': None}, row['revision'])
            with pytest.raises((Conflict, PermissionError)):
                await workspace.send(owner, cid, prompt='go', request_id='cleared-1')
        finally:
            await close(workspace, manager, ledger)
    asyncio.run(run())


def test_the_main_agent_can_only_create_read_only_bots(tmp_path):
    async def run():
        workspace, owner, manager, ledger, registry, _ = setup(tmp_path)
        try:
            parent = await main_turn(workspace, owner, manager)
            refused = await workspace.orchestrator.command(
                parent['id'], call('create_bot', 'wide', name='Wide', instructions='x', tools=['run_shell', 'write_file']))
            assert refused['ok'] is False and 'read' in refused['error']
            assert workspace.store.list('bot') == [] and registry.list() == []
            ctx = SimpleNamespace(task_id=parent['id'], manager=SimpleNamespace(orchestrator=workspace.orchestrator))
            decision = default_registry().get('create_bot').decide(
                call('create_bot', name='Wide', instructions='x', tools=['run_shell']), ctx)
            assert not decision.approval_required and not decision.allowed
            # The owner may still choose wider tools directly.
            wide = workspace.bots.create(owner, name='Owner wide', instructions='x', tools=['read_file', 'write_file'])
            assert 'write_file' in wide['ceiling']['tools']
        finally:
            await close(workspace, manager, ledger)
    asyncio.run(run())


def test_a_paused_bots_agent_cannot_be_started_from_another_conversation(tmp_path):
    workspace, owner, manager, ledger, registry, _ = setup(tmp_path)
    try:
        bot = make(workspace, owner)
        workspace.bots.guard_agent(bot['custom_agent_id'])
        workspace.bots.set_state(owner, bot['id'], 'paused')
        with pytest.raises(Conflict, match='paused'):
            workspace.bots.guard_agent(bot['custom_agent_id'])
        row = {'id': 'elsewhere', 'custom_agent_id': bot['custom_agent_id']}
        with pytest.raises(Conflict, match='paused'):
            workspace.bots.guard(row, ledger.get_custom_agent(bot['custom_agent_id']))
    finally:
        asyncio.run(close(workspace, manager, ledger))


def test_archive_keeps_the_rules_when_the_agent_file_cannot_be_removed(tmp_path, monkeypatch):
    workspace, owner, manager, ledger, registry, _ = setup(tmp_path)
    try:
        bot = make(workspace, owner)
        monkeypatch.setattr(registry, 'delete', lambda slug: False)
        with pytest.raises(Conflict, match='agent file'):
            workspace.bots.archive(owner, bot['id'])
        assert workspace.store.get('bot', bot['id'])['state'] == 'active'
        denied = ledger.list_policy_rules(scope_type='custom_agent', scope_id=bot['custom_agent_id'], effect='deny')
        assert denied, 'the ceiling deny must outlive a failed archive'
    finally:
        asyncio.run(close(workspace, manager, ledger))


def test_narrowing_the_ceiling_revokes_remembered_approvals(tmp_path):
    workspace, owner, manager, ledger, registry, _ = setup(tmp_path)
    try:
        bot = make(workspace, owner)
        wide = {**bot['ceiling'], 'approval_mode': 'remember'}
        bot = workspace.bots.set_ceiling(owner, bot['id'], revision=bot['revision'], ceiling=wide)
        ledger.create_policy_rule(effect='allow', scope_type='custom_agent', scope_id=bot['custom_agent_id'],
                                  action_type='tool', tool='read_file', fingerprint='fp', display='read_file')
        assert len(workspace.bots.get(owner, bot['id'])['rules']) == 1
        narrow = {**bot['ceiling'], 'approval_mode': 'standard'}
        bot = workspace.bots.set_ceiling(owner, bot['id'], revision=bot['revision'], ceiling=narrow)
        assert bot['rules'] == []
    finally:
        asyncio.run(close(workspace, manager, ledger))


def test_simultaneous_assignments_cannot_exceed_three_running_bot_tasks(tmp_path):
    async def run():
        workspace, owner, manager, ledger, registry, _ = setup(tmp_path)
        try:
            bots = [workspace.bots.create(owner, name=f'B{i}', instructions='Do the thing') for i in range(5)]
            results = await asyncio.gather(*[
                workspace.bots.assign_task(owner, bot['id'], prompt='HOLD this', request_id=f'race-{bot["id"]}')
                for bot in bots], return_exceptions=True)
            assert sum(isinstance(r, dict) for r in results) == 3
            assert sum(isinstance(r, Conflict) for r in results) == 2
        finally:
            await close(workspace, manager, ledger)
    asyncio.run(run())


def test_bot_nodes_ask_every_time_and_respect_the_bot_cap(tmp_path):
    async def run():
        workspace, owner, manager, ledger, registry, _ = setup(tmp_path)
        try:
            parent = await main_turn(workspace, owner, manager)
            task = ledger.get_task(parent['id'])
            bots = [workspace.bots.create(owner, name=f'B{i}', instructions='Do the thing') for i in range(4)]
            graph = workspace.orchestrator.graph
            graph.plan(owner, [{'key': f'n{i}', 'title': f'N{i}', 'prompt': 'HOLD', 'mode': 'ask', 'bot_id': b['id']}
                               for i, b in enumerate(bots)])
            runtime = {'auto': {'enabled': True}}
            ctx = ToolContext(task_id=parent['id'], cwd=task['cwd'], task={**task, 'runtime': runtime}, read_only=False,
                              cancel=asyncio.Event(), emit=lambda *_: {}, store=ledger,
                              manager=SimpleNamespace(orchestrator=workspace.orchestrator), policy_engine=PolicyEngine(ledger))
            decision = default_registry().get('start_ready').decide(call('start_ready'), ctx)
            assert decision.approval_required and decision.auto_resolved is None and 'B0' in decision.consequence
            started = await graph.start_ready(owner)
            statuses = sorted(node['status'] for node in graph.board(owner)['nodes'])
            assert statuses.count('running') <= 3, (statuses, started)
        finally:
            await close(workspace, manager, ledger)
    asyncio.run(run())


# -- progressive skill loading -------------------------------------------------

def test_new_bots_can_read_skills_on_demand_without_gaining_any_write_tool(tmp_path):
    from termx.workspace.bots import READ_TOOLS
    workspace, owner, manager, ledger, registry, _ = setup(tmp_path)
    try:
        spec = default_registry().get('read_skill')
        assert spec.mutability == 'read' and spec.approval == 'never'
        assert 'read_skill' in READ_TOOLS and 'read_skill' in default_ceiling()['tools']
        assert all(default_registry().get(name).mutability == 'read' for name in READ_TOOLS)
        for template in workspace.bots.templates():
            assert 'read_skill' in template['tools'], template['id']
        custom = workspace.bots.create(owner, name='Custom', instructions='x')
        assert 'read_skill' in custom['tools'] and 'read_skill' in custom['ceiling']['tools']
        assert not {'run_shell', 'write_file', 'git_push', 'mcp_call', 'computer'} & set(custom['ceiling']['tools'])
        # The owner can still narrow it away.
        narrowed = workspace.bots.set_ceiling(owner, custom['id'], revision=custom['revision'], ceiling={'tools': ['read_file']})
        assert 'read_skill' not in narrowed['tools']
    finally:
        asyncio.run(close(workspace, manager, ledger))
