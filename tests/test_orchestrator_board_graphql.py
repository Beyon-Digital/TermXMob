"""GraphQL task board: orchestrator_board query and retry/reassign/cancel mutations."""
from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from termx.app import AppState, create_app

BOARD = 'nodes{id title role status depends_on blocked_by attempts max_attempts task_id error result_summary provider_id model tokens cost_usd} rollup{total counts percent_complete tokens cost_usd}'
PASSWORD = 'strong-password-fixture'


def _login(state, name):
    return asyncio.run(state.identity.login('local-password', {'username': name, 'password': PASSWORD}, peer='local', device_name=name)).access_token


def _client(tmp_path, monkeypatch):
    monkeypatch.setenv('TERMX_CONFIG_DIR', str(tmp_path/'config'))
    state = AppState()
    owner = state.identity.setup_owner('owner', PASSWORD)
    client = TestClient(create_app(state))
    state.workspace.orchestrator.ensure(owner)
    return state, owner, client


def _post(client, token, query, variables=None):
    return client.post('/graphql', headers={'Authorization': 'Bearer ' + token}, json={'query': query, 'variables': variables or {}}).json()


def _spec(key, depends_on=()):
    return {'key': key, 'title': key.upper(), 'prompt': key + ' work', 'depends_on': list(depends_on), 'mode': 'ask'}


def test_board_lists_nodes_with_rollup(tmp_path, monkeypatch):
    state, owner, client = _client(tmp_path, monkeypatch)
    token = _login(state, 'owner')
    empty = _post(client, token, '{orchestrator_board{%s}}' % BOARD)
    assert not empty.get('errors'), empty
    assert empty['data']['orchestrator_board']['nodes'] == []
    assert empty['data']['orchestrator_board']['rollup']['total'] == 0
    nodes = state.workspace.orchestrator.graph.plan(owner, [_spec('a'), _spec('b', ['a'])])
    board = _post(client, token, '{orchestrator_board{%s}}' % BOARD)['data']['orchestrator_board']
    assert [n['title'] for n in board['nodes']] == ['A', 'B']
    assert board['nodes'][1]['depends_on'] == [nodes[0]['id']] and board['nodes'][1]['blocked_by'] == [nodes[0]['id']]
    assert board['nodes'][0]['max_attempts'] == 2
    assert board['rollup']['counts']['pending'] == 2 and board['rollup']['percent_complete'] == 0


def test_cancel_node_mutation_uses_the_cascade_and_returns_the_board(tmp_path, monkeypatch):
    state, owner, client = _client(tmp_path, monkeypatch)
    token = _login(state, 'owner')
    nodes = state.workspace.orchestrator.graph.plan(owner, [_spec('a'), _spec('b', ['a']), _spec('c')])
    result = _post(client, token, 'mutation($id:String!){cancel_node(node_id:$id){%s}}' % BOARD, {'id': nodes[0]['id']})
    assert not result.get('errors'), result
    statuses = {n['title']: n['status'] for n in result['data']['cancel_node']['nodes']}
    assert statuses == {'A': 'cancelled', 'B': 'cancelled', 'C': 'pending'}
    assert result['data']['cancel_node']['rollup']['counts']['cancelled'] == 2
    again = _post(client, token, 'mutation($id:String!){cancel_node(node_id:$id){rollup{total counts}}}', {'id': nodes[0]['id']})
    assert again['data']['cancel_node']['rollup']['counts']['cancelled'] == 2  # cancelling twice changes nothing


def test_retry_and_reassign_report_conflicts_and_bad_input(tmp_path, monkeypatch):
    state, owner, client = _client(tmp_path, monkeypatch)
    token = _login(state, 'owner')
    (node,) = state.workspace.orchestrator.graph.plan(owner, [_spec('a')])
    retry = _post(client, token, 'mutation($id:String!){retry_node(node_id:$id){rollup{total}}}', {'id': node['id']})
    assert retry['errors'][0]['extensions']['http_status'] == 409
    reassign = _post(client, token, 'mutation($id:String!){reassign_node(node_id:$id,input:{provider_id:"missing",model:"x"}){rollup{total}}}',
        {'id': node['id']})
    assert reassign['errors'][0]['extensions']['http_status'] == 400
    unknown = _post(client, token, 'mutation{retry_node(node_id:"nope"){rollup{total}}}')
    assert unknown['errors'][0]['extensions']['http_status'] == 404


def test_board_and_mutations_follow_agent_scopes_and_ownership(tmp_path, monkeypatch):
    state, owner, client = _client(tmp_path, monkeypatch)
    (node,) = state.workspace.orchestrator.graph.plan(owner, [_spec('a')])
    state.identity.create_local_user('watcher', PASSWORD, ['agent-view'])
    state.identity.create_local_user('stranger', PASSWORD, list(owner.scopes))
    watcher, stranger = _login(state, 'watcher'), _login(state, 'stranger')
    # A view-only user may not change the board.
    for mutation in ('cancel_node(node_id:"%s")' % node['id'], 'retry_node(node_id:"%s")' % node['id'],
            'reassign_node(node_id:"%s",input:{model:"x"})' % node['id']):
        denied = _post(client, watcher, 'mutation{%s{rollup{total}}}' % mutation)
        assert denied['errors'][0]['extensions']['http_status'] == 403, (mutation, denied)
    # Another user cannot read or change this graph.
    assert _post(client, stranger, '{orchestrator_board{rollup{total}}}')['errors'][0]['extensions']['http_status'] == 403
    other = _post(client, stranger, 'mutation{cancel_node(node_id:"%s"){rollup{total}}}' % node['id'])
    assert other['errors'][0]['extensions']['http_status'] == 403
    # Without a credential nothing is reachable.
    assert _post(client, 'wrong', '{orchestrator_board{rollup{total}}}').get('errors')
