"""The owner approves every character a bot will later read unattended, so long text is refused rather than cut."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from test_bots import call
from termx.agent.tools import default_registry
from termx.agent.tools.subagents import _review_node

CTX = SimpleNamespace(task_id='t', manager=SimpleNamespace(orchestrator=None))
LONG = 'x' * 2500 + 'HIDDEN-TAIL'


@pytest.mark.parametrize('name,arguments', [
    ('create_bot', {'name': 'A', 'instructions': LONG}),
    ('update_bot', {'bot_id': 'b1', 'instructions': LONG}),
    ('assign_bot_task', {'bot_id': 'b1', 'task': LONG}),
])
def test_text_a_bot_will_read_is_refused_when_the_owner_could_not_see_all_of_it(name, arguments):
    decision = default_registry().get(name).decide(call(name, **arguments), CTX)
    assert decision.allowed is False and not decision.approval_required and 'shorter' in decision.consequence


@pytest.mark.parametrize('name,arguments', [
    ('create_bot', {'name': 'A', 'instructions': 'Watch the repo. END-OF-TEXT'}),
    ('update_bot', {'bot_id': 'b1', 'instructions': 'Watch the repo. END-OF-TEXT'}),
    ('assign_bot_task', {'bot_id': 'b1', 'task': 'Watch the repo. END-OF-TEXT'}),
])
def test_short_text_is_shown_in_full_in_the_approval(name, arguments):
    decision = default_registry().get(name).decide(call(name, **arguments), CTX)
    assert decision.approval_required and 'END-OF-TEXT' in decision.consequence


def test_a_bot_node_in_a_plan_is_reviewed_in_full_or_refused():
    def context(prompt):
        nodes = [{'id': 'n1', 'title': 'Node', 'mode': 'agent', 'prompt': prompt, 'bot_id': 'b1'}]
        graph = SimpleNamespace(preview=lambda *_: nodes)
        service = SimpleNamespace(graph=graph, actor_for_task=lambda _id: None,
                                  workspace=SimpleNamespace(store=SimpleNamespace(get=lambda *_: {'name': 'Alpha'})))
        return SimpleNamespace(task_id='t', manager=SimpleNamespace(orchestrator=service), policy_engine=None)

    decide = _review_node('Start nodes', True)
    shown = decide(call('start_ready'), context('p' * 900 + 'NODE-END'))
    assert shown.approval_required and 'NODE-END' in shown.consequence
    refused = decide(call('start_ready'), context(LONG))
    assert refused.allowed is False and not refused.approval_required and 'shorter' in refused.consequence
