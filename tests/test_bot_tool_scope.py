"""A bot is only offered, and only runs, the tools it was given."""
from __future__ import annotations

import asyncio

from test_bots import BotAdapter, call, close, setup
from termx.agent.providers import ProviderTurn
from termx.agent.tools import default_registry


def test_provider_tools_can_be_narrowed_to_an_allowed_set():
    registry = default_registry()
    names = {tool['name'] for tool in registry.provider_tools(allowed_tools=['read_file', 'memory_read'])}
    assert names <= {'read_file', 'memory_read'} and 'read_file' in names
    assert 'run_shell' in {tool['name'] for tool in registry.provider_tools()}


def test_a_bot_run_advertises_only_its_tools_and_a_call_outside_them_is_refused_not_fatal(tmp_path, monkeypatch):
    seen = []

    async def scripted(self, **kwargs):
        seen.append(kwargs.get('allowed_tools'))
        self.turns += 1
        if self.turns == 1:
            return ProviderTurn(response_id='r1', text='', calls=[call('run_shell', command='echo hi')],
                                usage={'input_tokens': 1, 'output_tokens': 1}, output_items=[])
        return ProviderTurn(response_id=f'r{self.turns}', text='done', calls=[],
                            usage={'input_tokens': 1, 'output_tokens': 1}, output_items=[])

    monkeypatch.setattr(BotAdapter, 'turn', scripted)

    async def run():
        workspace, owner, manager, ledger, registry, _ = setup(tmp_path)
        try:
            bot = workspace.bots.create(owner, name='Custom one', instructions='x', tools=['read_file', 'memory_read'])
            task = await workspace.bots.assign_task(owner, bot['id'], prompt='Try a shell', request_id='narrow-1')
            for _ in range(100):
                if ledger.get_task(task['id'])['status'] in ('completed', 'failed'):
                    break
                await asyncio.sleep(0.05)
            row = ledger.get_task(task['id'])
            assert row['status'] == 'completed', row.get('error')
            assert seen[0] and set(seen[0]) <= {'read_file', 'memory_read'} and 'run_shell' not in seen[0]
            finished = [e for e in ledger.events(task['id']) if e['type'] == 'tool.finished']
            assert finished and finished[0]['payload']['result']['refused'] is True
        finally:
            await close(workspace, manager, ledger)
    asyncio.run(run())
