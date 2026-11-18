"""Remote-access matrix: every route, query, mutation and subscription added for CLI sessions, durability and the
orchestrator (slices A5 to A7) is gated by an EXISTING scope, with the managed-principal project grant where the surface
is project-scoped. No scope was added, so devices paired before these surfaces existed keep every read path they had.

Each row asserts 401 without credentials (and with a wrong one), 403 for a credential that holds every scope except the
required one, and success for a credential that holds only the required scope. Cross-principal checks follow: per-user
records (CLI sessions, task-graph nodes, push registrations) never reach another principal.
"""
from __future__ import annotations

import asyncio
import json
from collections import namedtuple
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from _gql import err_status, gql
from cli_fake import posix_only, write_fake_cli
from test_authorization import PASSWORD, client_fixture
from termx.app import AppState, create_app
from termx.authorization import ROLES
from termx.tokens import DEFAULT_DEVICE_SCOPES, SCOPES

pytestmark = posix_only
FAST = {'quiet_after': 0.5, 'idle_after': 3.0, 'sustain': 0.6}

# The scope list as it was before A5. If this changes, devices paired earlier lose or gain authority.
BASELINE_SCOPES = ('machine-view', 'terminal-view', 'terminal-control', 'files-read', 'files-write', 'git-read', 'git-write',
                   'desktop-view', 'desktop-control', 'network-manage', 'agent-view', 'agent-run', 'agent-control', 'ai-settings', 'host-admin')

Rest = namedtuple('Rest', 'name method path body scope setup ok')
REST_ROWS = [
    Rest('cli-agents', 'GET', '/api/cli-agents', None, 'terminal-view', None, 200),
    Rest('cli-sessions-list', 'GET', '/api/cli-sessions', None, 'terminal-view', None, 200),
    Rest('cli-session-get', 'GET', '/api/cli-sessions/{sid}', None, 'terminal-view', 'live', 200),
    Rest('cli-session-create', 'POST', '/api/cli-sessions', {'cli': 'claude', 'cwd': '{root}'}, 'terminal-control', None, 201),
    Rest('cli-session-input', 'POST', '/api/cli-sessions/{sid}/input', {'text': 'hi'}, 'terminal-control', 'live', 200),
    Rest('cli-session-interrupt', 'POST', '/api/cli-sessions/{sid}/interrupt', None, 'terminal-control', 'live', 200),
    Rest('cli-session-stop', 'POST', '/api/cli-sessions/{sid}/stop', None, 'terminal-control', 'live', 200),
    Rest('cli-session-resume', 'POST', '/api/cli-sessions/{sid}/resume', None, 'terminal-control', 'ended', 201),
]
# The one route with no credential at all: a loopback-only capability URL minted per session for the CLI's own hook.
HOOK_ROUTE = ('POST', '/api/cli-sessions/hook/{token}')

Gql = namedtuple('Gql', 'name query scope field')
GQL_DEVICE_ROWS = [
    Gql('host-status', '{ host_status { version pid } }', 'machine-view', 'host_status'),
    Gql('update-host-settings', 'mutation { update_host_settings(input: {keep_awake: false}) { version } }', 'host-admin', 'update_host_settings'),
]
Sub = namedtuple('Sub', 'name query scope')
SUBSCRIPTION_ROWS = [
    Sub('host-status-events', 'subscription { host_status_events { version pid } }', 'machine-view'),
    Sub('cli-session-events', 'subscription { cli_session_events { type session { id status } } }', 'terminal-view'),
]
# The task graph acts as the signed-in user, so these use managed principals. ``grant`` is the whole set the success
# credential holds (the gate scope plus what the operation reads or starts with); ``gate`` is the scope removed for 403.
Managed = namedtuple('Managed', 'name query gate grant ok')
VIEW = ['agent-view']
CONTROL = ['agent-view', 'agent-control', 'agent-run']
NODE = '{node}'
MANAGED_ROWS = [
    Managed('orchestrator-board', '{ orchestrator_board { rollup { total } nodes { id status executor session_id blocked_reason resumable } } }',
            'agent-view', VIEW, {200}),
    Managed('start-ready-nodes', 'mutation { start_ready_nodes { rollup { total } } }', 'agent-control', CONTROL, {200}),
    Managed('retry-node', f'mutation {{ retry_node(node_id: "{NODE}") {{ rollup {{ total }} }} }}', 'agent-control', CONTROL, {409}),
    Managed('reassign-node', f'mutation {{ reassign_node(node_id: "{NODE}", input: {{model: "x"}}) {{ rollup {{ total }} }} }}', 'agent-control', CONTROL, {400}),
    Managed('cancel-node', f'mutation {{ cancel_node(node_id: "{NODE}") {{ rollup {{ total }} }} }}', 'agent-control', CONTROL, {200}),
]


def headers(token):
    return {'x-termx-passcode': token}


def bearer(token):
    return {'Authorization': f'Bearer {token}'}


@pytest.fixture
def host(tmp_path, monkeypatch):
    monkeypatch.setenv('TERMX_CONFIG_DIR', str(tmp_path / 'config'))
    state = AppState(passcode='host-secret')
    app = create_app(state)
    state.cli_sessions._executables = {'claude': write_fake_cli(tmp_path / 'bin')}
    state.cli_sessions.tick, state.cli_sessions.thresholds, state.cli_sessions.grace = 0.1, dict(FAST), (0.6, 0.6)
    root = tmp_path / 'w'
    root.mkdir()
    with TestClient(app, base_url='http://localhost') as client:
        yield SimpleNamespace(state=state, client=client, root=str(root), tokens=state.tokens)


def start_session(host, *, ended=False):
    made = host.client.post('/api/cli-sessions', headers=headers('host-secret'), json={'cli': 'claude', 'cwd': host.root})
    assert made.status_code == 201, made.text
    sid = made.json()['id']
    if ended:
        assert host.client.post(f'/api/cli-sessions/{sid}/stop', headers=headers('host-secret')).json()['status'] == 'done'
    return sid


def subscribe(client, query, token):
    """('ok' | status) for one subscription attempt: the first frame decides."""
    payload = {'k': token} if token else {}
    try:
        with client.websocket_connect('/graphql', subprotocols=['graphql-transport-ws']) as socket:
            socket.send_json({'type': 'connection_init', 'payload': payload})
            if socket.receive_json()['type'] != 'connection_ack':
                return 401
            socket.send_json({'id': '1', 'type': 'subscribe', 'payload': {'query': query}})
            message = socket.receive_json()
            body = json.dumps(message.get('payload'))
            if message['type'] == 'next' and not message['payload'].get('errors'):
                socket.send_json({'id': '1', 'type': 'complete'})
                return 200
            if 'missing scope' in body or 'host-admin' in body:
                return 403
            return 401 if 'invalid passcode' in body else 500
    except WebSocketDisconnect:
        return 401


# -- the scope list itself ------------------------------------------------------------------------------


def test_no_scope_was_added_and_every_matrix_scope_exists():
    assert SCOPES == BASELINE_SCOPES
    used = {row.scope for row in REST_ROWS} | {row.scope for row in GQL_DEVICE_ROWS} | {row.scope for row in SUBSCRIPTION_ROWS}
    used |= {row.gate for row in MANAGED_ROWS} | {scope for row in MANAGED_ROWS for scope in row.grant}
    assert used <= set(SCOPES) and 'host-admin' not in DEFAULT_DEVICE_SCOPES


def test_every_cli_route_the_app_registers_is_in_the_matrix(tmp_path):
    app = create_app(AppState(passcode='host-secret'))
    registered = {(method.upper(), path) for path, methods in app.openapi()['paths'].items()
                  if path.startswith(('/api/cli-agents', '/api/cli-sessions')) for method in methods}
    covered = {(row.method, row.path.replace('{sid}', '{session_id}')) for row in REST_ROWS} | {(HOOK_ROUTE[0], '/api/cli-sessions/hook/{token}')}
    assert registered == covered, (registered ^ covered)


def test_every_graphql_operation_in_the_matrix_exists_in_the_schema():
    from termx.graphql.schema import schema

    text = str(schema)
    for name in ('host_status', 'update_host_settings', 'host_status_events', 'cli_session_events', 'orchestrator_board', 'start_ready_nodes',
                 'retry_node', 'reassign_node', 'cancel_node', 'register_push_device', 'unregister_push_device'):
        assert f'  {name}' in text, name


# -- REST ----------------------------------------------------------------------------------------------


@pytest.mark.parametrize('row', REST_ROWS, ids=lambda row: row.name)
def test_rest_matrix_401_without_credentials_403_for_the_wrong_scope_success_for_the_right_one(host, row):
    sid = start_session(host, ended=row.setup == 'ended') if row.setup else None
    url = row.path.format(sid=sid)
    body = {key: value.format(root=host.root) if isinstance(value, str) else value for key, value in (row.body or {}).items()} or None
    before = len(host.client.get('/api/cli-sessions', headers=headers('host-secret')).json()['sessions'])

    def send(creds):
        return host.client.request(row.method, url, headers=creds, json=body)

    assert send({}).status_code == 401
    assert send(headers('wrong-passcode')).status_code == 401
    assert send(bearer('wrong-token')).status_code == 401
    wrong = host.tokens.issue([scope for scope in SCOPES if scope != row.scope], device_name='everything-but-' + row.scope)
    denied = send(headers(wrong))
    assert denied.status_code == 403 and row.scope in denied.json()['detail'], denied.text
    assert len(host.client.get('/api/cli-sessions', headers=headers('host-secret')).json()['sessions']) == before  # nothing happened
    right = host.tokens.issue([row.scope], device_name='only-' + row.scope)
    allowed = send(bearer(right))
    assert allowed.status_code == row.ok, allowed.text


def test_the_hook_route_is_the_one_unauthenticated_route_and_is_loopback_only(host):
    sid = start_session(host)
    path = '/api/cli-sessions/hook/clihook_forged_token_value'
    # a credential changes nothing: only the per-session token, from loopback, is accepted
    for creds in ({}, headers('host-secret'), headers(host.tokens.issue(list(SCOPES), device_name='all'))):
        assert host.client.post(path, headers=creds, json={'hook_event_name': 'Stop'}).status_code == 403
    loopback = TestClient(host.client.app, base_url='http://localhost', client=('127.0.0.1', 4000))
    assert loopback.post(path, json={'hook_event_name': 'Stop'}).status_code == 403
    assert host.client.get(f'/api/cli-sessions/{sid}', headers=headers('host-secret')).json()['status_source'] == 'heuristic'
    host.client.post(f'/api/cli-sessions/{sid}/stop', headers=headers('host-secret'))


# -- GraphQL (device credentials) -----------------------------------------------------------------------


@pytest.mark.parametrize('row', GQL_DEVICE_ROWS, ids=lambda row: row.name)
def test_graphql_matrix_for_device_credentials(host, row):
    assert err_status(host.client, row.query) == 401
    assert err_status(host.client, row.query, headers=headers('wrong-passcode')) == 401
    wrong = host.tokens.issue([scope for scope in SCOPES if scope != row.scope], device_name='everything-but-' + row.scope)
    assert err_status(host.client, row.query, headers=headers(wrong)) == 403
    assert 'errors' in gql(host.client, row.query, headers=headers(wrong)).json()
    right = host.tokens.issue([row.scope], device_name='only-' + row.scope)
    body = gql(host.client, row.query, headers=headers(right)).json()
    assert not body.get('errors') and body['data'][row.field]['version'], body


@pytest.mark.parametrize('row', SUBSCRIPTION_ROWS, ids=lambda row: row.name)
def test_subscription_matrix(host, row):
    start_session(host)  # something for the session stream to report
    assert subscribe(host.client, row.query, None) == 401
    assert subscribe(host.client, row.query, 'wrong-passcode') == 401
    wrong = host.tokens.issue([scope for scope in SCOPES if scope != row.scope], device_name='everything-but-' + row.scope)
    assert subscribe(host.client, row.query, wrong) == 403
    right = host.tokens.issue([row.scope], device_name='only-' + row.scope)
    assert subscribe(host.client, row.query, right) == 200


def test_update_host_settings_never_follows_from_a_default_paired_device(host):
    paired = host.tokens.issue(None, device_name='default pairing')  # None = the default set a pairing gets
    assert set(host.state.tokens.check(paired)) == set(DEFAULT_DEVICE_SCOPES)
    mutation = GQL_DEVICE_ROWS[1].query
    assert err_status(host.client, mutation, headers=headers(paired)) == 403
    assert gql(host.client, GQL_DEVICE_ROWS[0].query, headers=headers(paired)).json()['data']['host_status']['pid']
    assert host.client.get('/api/cli-sessions', headers=headers(paired)).status_code == 200


def test_devices_paired_before_these_surfaces_existed_keep_every_read_path_they_had(host):
    """v1 scopes migrate to the same authority: ``terminal`` already meant terminal view and control."""
    legacy_terminal = host.tokens.issue(['terminal'], device_name='old phone')
    legacy_view_only = host.tokens.issue(['terminal-view'], device_name='old watch')
    status_only = host.tokens.issue(['machine-view'], device_name='old dashboard')
    assert host.client.get('/api/cli-sessions', headers=headers(legacy_terminal)).status_code == 200
    assert host.client.get('/api/cli-agents', headers=headers(legacy_view_only)).status_code == 200
    assert host.client.post('/api/cli-sessions', headers=headers(legacy_view_only), json={'cli': 'claude', 'cwd': host.root}).status_code == 403
    made = host.client.post('/api/cli-sessions', headers=headers(legacy_terminal), json={'cli': 'claude', 'cwd': host.root})
    assert made.status_code == 201, made.text
    assert gql(host.client, GQL_DEVICE_ROWS[0].query, headers=headers(status_only)).json()['data']['host_status']['version']
    assert host.client.get('/api/cli-sessions', headers=headers(status_only)).status_code == 403
    host.client.post(f"/api/cli-sessions/{made.json()['id']}/stop", headers=headers(legacy_terminal))


# -- managed principals ---------------------------------------------------------------------------------


def make_user(state, identity, name, *, role='admin', scopes=None, ensure=False, nodes=0):
    """A managed user; the main agent and nodes are created with full authority, then the scopes are narrowed."""
    principal = identity.create_local_user(name, PASSWORD, list(SCOPES))
    state.authorization.set_role(principal.id, role, trusted_execution=True)
    created = []
    if ensure:
        state.workspace.orchestrator.ensure(principal)
        spec = [{'key': f'n{index}', 'title': f'{name} node {index}', 'prompt': 'work', 'mode': 'ask'} for index in range(nodes)]
        created = state.workspace.orchestrator.graph.plan(principal, spec) if spec else []
    if scopes is not None:
        identity.set_scopes(principal.id, list(scopes))
    token = asyncio.run(identity.login('local-password', {'username': name, 'password': PASSWORD}, peer='local')).access_token
    return principal, bearer(token), created


@pytest.mark.parametrize('row', MANAGED_ROWS, ids=lambda row: row.name)
def test_task_graph_matrix_for_managed_principals(tmp_path, monkeypatch, row):
    monkeypatch.setenv('TERMX_CONFIG_DIR', str(tmp_path / 'config'))
    identity, state, client, owner = client_fixture(tmp_path)
    with client:
        everything_but = [scope for scope in SCOPES if scope != row.gate]
        _, wrong, nodes = make_user(state, identity, 'wrong', scopes=everything_but, ensure=True, nodes=1)
        _, right, right_nodes = make_user(state, identity, 'right', scopes=row.grant, ensure=True, nodes=1)

        def run(creds, nodes):
            query = row.query.replace(NODE, nodes[0]['id'])
            return gql(client, query, headers=creds).json(), err_status(client, query, headers=creds)

        assert run({}, nodes)[1] == 401
        assert run(bearer('not-a-session'), nodes)[1] == 401
        denied, status = run(wrong, nodes)
        assert status == 403 and 'missing scope: ' + row.gate in json.dumps(denied)
        body, status = run(right, right_nodes)
        if 200 in row.ok:
            assert status == 200 and not body.get('errors'), body
        else:
            assert status in row.ok  # authorised; the operation itself is refused for the node's state


def test_the_board_exposes_executor_fields_only_to_agent_view_and_only_for_the_owners_nodes(tmp_path, monkeypatch):
    monkeypatch.setenv('TERMX_CONFIG_DIR', str(tmp_path / 'config'))
    identity, state, client, owner = client_fixture(tmp_path)
    with client:
        alice, alice_headers, (node,) = make_user(state, identity, 'alice', ensure=True, nodes=1)
        _, bob_headers, bob_nodes = make_user(state, identity, 'bob', ensure=True, nodes=1)
        board = '{ orchestrator_board { nodes { id title executor blocked_reason session_id resumable } } }'
        mine = gql(client, board, headers=alice_headers).json()['data']['orchestrator_board']['nodes']
        theirs = gql(client, board, headers=bob_headers).json()['data']['orchestrator_board']['nodes']
        assert [n['id'] for n in mine] == [node['id']] and mine[0]['executor'] == {'kind': 'internal'}
        assert [n['id'] for n in theirs] == [bob_nodes[0]['id']] and node['id'] not in json.dumps(theirs)
        cancel = f'mutation {{ cancel_node(node_id: "{node["id"]}") {{ rollup {{ total }} }} }}'
        assert err_status(client, cancel, headers=bob_headers) == 404  # someone else's node does not exist for them
        assert [n['status'] for n in gql(client, board.replace('title', 'status'), headers=alice_headers).json()['data']['orchestrator_board']['nodes']] == ['pending']


def test_starting_a_cli_node_from_the_board_needs_terminal_control_not_just_agent_scopes(tmp_path, monkeypatch):
    monkeypatch.setenv('TERMX_CONFIG_DIR', str(tmp_path / 'config'))
    identity, state, client, owner = client_fixture(tmp_path)
    state.cli_sessions._executables = {'claude': write_fake_cli(tmp_path / 'bin')}
    state.cli_sessions.tick, state.cli_sessions.thresholds, state.cli_sessions.grace = 0.1, dict(FAST), (0.6, 0.6)
    spec = [{'key': 'c', 'title': 'CLI work', 'prompt': 'work', 'mode': 'agent', 'executor': {'kind': 'cli', 'cli': 'claude'}}]
    last = {}
    with client:
        for name, scopes, expected in (('agents-only', CONTROL, 'failed'), ('with-terminal', CONTROL + ['terminal-control'], 'running')):
            principal, creds, _ = make_user(state, identity, name, ensure=True)
            planned = state.workspace.orchestrator.graph.plan(principal, spec)
            assert planned[0]['executor'] == {'kind': 'cli', 'cli': 'claude', 'mode': 'default', 'isolation': 'none'} or planned[0]['executor']['kind'] == 'cli'
            identity.set_scopes(principal.id, scopes)
            token = asyncio.run(identity.login('local-password', {'username': name, 'password': PASSWORD}, peer='local')).access_token
            last = bearer(token)
            board = gql(client, 'mutation { start_ready_nodes { nodes { status error executor } } }', headers=last).json()
            node = board['data']['start_ready_nodes']['nodes'][0]
            assert node['status'] == expected, node
            if expected == 'failed':
                assert 'terminal-control' in node['error'] and state.cli_sessions.rows(principal.id) == []
        for row in state.cli_sessions.rows(principal.id):
            assert client.post(f"/api/cli-sessions/{row['id']}/stop", headers=last).status_code == 200


def test_project_grants_and_isolation_for_cli_sessions_between_managed_principals(tmp_path, monkeypatch):
    monkeypatch.setenv('TERMX_CONFIG_DIR', str(tmp_path / 'config'))
    identity, state, client, owner = client_fixture(tmp_path)
    state.cli_sessions._executables = {'claude': write_fake_cli(tmp_path / 'bin')}
    state.cli_sessions.tick, state.cli_sessions.thresholds, state.cli_sessions.grace = 0.1, dict(FAST), (0.6, 0.6)
    project = tmp_path / 'proj'
    project.mkdir()
    pid = state.projects.register(str(project))['id']
    with client:
        alice, a, _ = make_user(state, identity, 'alice', role='operator')
        bob, b, _ = make_user(state, identity, 'bob', role='operator')
        carol, c, _ = make_user(state, identity, 'carol', role='operator')
        state.authorization.grant_project(alice.id, pid, ['terminal-view', 'terminal-control', 'agent-view'])
        state.authorization.grant_project(carol.id, pid, ['terminal-view'])  # may look, may not start
        body = {'cli': 'claude', 'project_id': pid}
        assert client.post('/api/cli-sessions', headers=b, json=body).status_code == 403  # no grant on the project
        assert client.post('/api/cli-sessions', headers=c, json=body).status_code == 403  # a view-only grant is not control
        made = client.post('/api/cli-sessions', headers=a, json=body)
        assert made.status_code == 201, made.text
        sid = made.json()['id']
        for creds in (b, c):  # another principal's session does not exist for them, on any route
            assert client.get(f'/api/cli-sessions/{sid}', headers=creds).status_code == 404
            assert client.post(f'/api/cli-sessions/{sid}/input', headers=creds, json={'text': 'x'}).status_code in (403, 404)
            assert client.post(f'/api/cli-sessions/{sid}/interrupt', headers=creds).status_code in (403, 404)
            assert client.post(f'/api/cli-sessions/{sid}/stop', headers=creds).status_code in (403, 404)
            assert client.post(f'/api/cli-sessions/{sid}/resume', headers=creds).status_code in (403, 404)
            assert client.get('/api/cli-sessions', headers=creds).json() == {'sessions': []}
        assert [row['id'] for row in client.get('/api/cli-sessions', headers=a).json()['sessions']] == [sid]
        # a revoked project grant takes the session's routes away from its own creator immediately
        state.authorization.revoke_project(alice.id, pid)
        assert client.get('/api/cli-sessions', headers=a).json() == {'sessions': []}
        assert client.post(f'/api/cli-sessions/{sid}/input', headers=a, json={'text': 'x'}).status_code in (403, 404)
        state.authorization.grant_project(alice.id, pid, ['terminal-view', 'terminal-control'])
        assert client.post(f'/api/cli-sessions/{sid}/stop', headers=a).status_code == 200


def test_push_registrations_belong_to_one_principal(tmp_path, monkeypatch):
    monkeypatch.setenv('TERMX_CONFIG_DIR', str(tmp_path / 'config'))
    identity, state, client, owner = client_fixture(tmp_path)
    with client:
        alice, a, _ = make_user(state, identity, 'alice', role='operator')
        bob, b, _ = make_user(state, identity, 'bob', role='operator')
        token = 'ExpoPushToken[alicesdevice0001]'
        register = 'mutation($t:String!){ register_push_device(token:$t, host:"http://localhost:8787") }'
        unregister = 'mutation($t:String!){ unregister_push_device(token:$t) }'
        assert err_status(client, register, {'t': token}) == 401  # no credential
        assert not gql(client, register, {'t': token}, headers=a).json().get('errors')
        assert err_status(client, register, {'t': token}, headers=b) == 409  # a token bound to another active user is refused
        gql(client, unregister, {'t': token}, headers=b)
        assert state.push.db.execute('SELECT count(*) FROM push_devices').fetchone()[0] == 1  # bob cannot remove alice's device
        gql(client, unregister, {'t': token}, headers=a)
        assert state.push.db.execute('SELECT count(*) FROM push_devices').fetchone()[0] == 0


def test_a_viewer_role_sees_nothing_of_other_users_cli_sessions_and_cannot_start_one(tmp_path, monkeypatch):
    monkeypatch.setenv('TERMX_CONFIG_DIR', str(tmp_path / 'config'))
    identity, state, client, owner = client_fixture(tmp_path)
    state.cli_sessions._executables = {'claude': write_fake_cli(tmp_path / 'bin')}
    project = tmp_path / 'proj'
    project.mkdir()
    pid = state.projects.register(str(project))['id']
    with client:
        viewer = identity.create_local_user('viewer', PASSWORD, list(ROLES['viewer']))
        state.authorization.set_role(viewer.id, 'viewer', trusted_execution=False)
        token = asyncio.run(identity.login('local-password', {'username': 'viewer', 'password': PASSWORD}, peer='local')).access_token
        creds = bearer(token)
        sid = client.post('/api/cli-sessions', headers=owner, json={'cli': 'claude', 'project_id': pid}).json()['id']
        assert client.get('/api/cli-sessions', headers=creds).json() == {'sessions': []}
        assert client.post('/api/cli-sessions', headers=creds, json={'cli': 'claude', 'project_id': pid}).status_code == 403
        assert client.get(f'/api/cli-sessions/{sid}', headers=creds).status_code == 404
        assert err_status(client, GQL_DEVICE_ROWS[0].query, headers=creds) == 403  # host status is an administrator view for managed users
        client.post(f'/api/cli-sessions/{sid}/stop', headers=owner)
