"""Saved-sites REST routes and the `browser_site_events` subscription: scopes, statuses, isolation, event contract."""
import time
import pytest
from fastapi.testclient import TestClient
from termx.app import AppState, create_app
from termx.browser.router import principal_for
from test_browser_sites import fake_tabs

BASE = '/api/browser'


@pytest.fixture
def api():
    state = AppState(passcode='secret')
    with TestClient(create_app(state, web_dir=None), base_url='https://localhost') as client:
        yield state, client


def bearer(token):
    return {'Authorization': 'Bearer ' + token}


def users(state):
    """Distinct tokens are distinct browser principals on a host without managed accounts."""
    return {'ctl': bearer(state.tokens.issue(['desktop-view', 'desktop-control'])),
            'view': bearer(state.tokens.issue(['desktop-view'])),
            'other': bearer(state.tokens.issue(['desktop-view', 'desktop-control'])),
            'noview': bearer(state.tokens.issue(['agent-view']))}


def profile_of(client, headers, name='Work'):
    response = client.post(f'{BASE}/profiles', headers=headers, json={'name': name})
    assert response.status_code == 200, response.text
    return response.json()


def test_scopes_gate_reads_and_writes_and_missing_credentials_are_refused(api):
    state, client = api; who = users(state)
    profile = profile_of(client, who['ctl'])
    created = client.post(f'{BASE}/sites', headers=who['ctl'], json={'profile_id': profile['id'], 'url': 'https://example.com/app?x=1', 'title': 'Example', 'open_mode': 'app'})
    assert created.status_code == 200, created.text
    site = created.json()
    assert site['origin'] == 'https://example.com' and site['start_url'] == 'https://example.com/app' and site['open_mode'] == 'app' and site['pinned'] is True
    assert set(site) == {'id', 'project_id', 'origin', 'start_url', 'title', 'profile_id', 'open_mode', 'pinned', 'position', 'visit_count',
                         'last_visited', 'frecency', 'tab_id', 'note_count', 'created', 'updated'}
    assert 'principal_id' not in site
    # Reads: the view scope; no token or no view scope is refused.
    assert client.get(f'{BASE}/sites').status_code in (401, 403)
    assert client.get(f'{BASE}/sites', headers=who['noview']).status_code == 403
    assert client.get(f'{BASE}/sites/suggestions', headers=who['noview']).status_code == 403
    assert client.get(f'{BASE}/sites', headers=who['ctl']).json() == [site]
    # Writes: the control scope. A view-only token can read its own (empty) list but never write.
    assert client.get(f'{BASE}/sites', headers=who['view']).json() == []
    for method, path, body in (('post', '/sites', {'profile_id': profile['id'], 'url': 'https://example.com'}),
                               ('patch', f"/sites/{site['id']}", {'pinned': False}), ('delete', f"/sites/{site['id']}", None),
                               ('post', f"/sites/{site['id']}/open", None), ('post', f"/sites/{site['id']}/notes", {'content': 'x'}),
                               ('delete', f"/sites/{site['id']}/notes?note_id=n", None)):
        kwargs = {'json': body} if body is not None else {}
        assert getattr(client, method)(BASE + path, headers=who['view'], **kwargs).status_code == 403, (method, path)
        assert getattr(client, method)(BASE + path, headers=who['noview'], **kwargs).status_code == 403, (method, path)
    assert client.get(f"{BASE}/sites/{site['id']}/notes", headers=who['noview']).status_code == 403


def test_crud_statuses_validation_and_notes_round_trip(api):
    state, client = api; who = users(state); headers = who['ctl']
    profile = profile_of(client, headers)
    ok = client.post(f'{BASE}/sites', headers=headers, json={'profile_id': profile['id'], 'url': 'https://example.com'})
    site = ok.json()
    assert site['open_mode'] == 'tab' and site['title'] == 'example.com'
    again = client.post(f'{BASE}/sites', headers=headers, json={'profile_id': profile['id'], 'url': 'https://example.com/other', 'open_mode': 'app'})
    assert again.status_code == 200 and again.json()['id'] == site['id'] and again.json()['open_mode'] == 'app'
    assert client.post(f'{BASE}/sites', headers=headers, json={'profile_id': 'missing', 'url': 'https://example.com'}).status_code == 404
    assert client.post(f'{BASE}/sites', headers=headers, json={'profile_id': profile['id'], 'url': 'file:///etc/passwd'}).status_code == 400
    assert client.post(f'{BASE}/sites', headers=headers, json={'profile_id': profile['id'], 'url': 'https://example.com', 'open_mode': 'window'}).status_code == 422
    assert client.post(f'{BASE}/sites', headers=headers, json={'profile_id': profile['id'], 'url': 'https://example.com', 'principal_id': 'x'}).status_code == 422
    patched = client.patch(f"{BASE}/sites/{site['id']}", headers=headers, json={'pinned': False, 'title': 'Mail', 'position': 3})
    assert patched.status_code == 200 and patched.json()['pinned'] is False and patched.json()['title'] == 'Mail' and patched.json()['position'] == 3
    assert client.patch(f"{BASE}/sites/{site['id']}", headers=headers, json={'start_url': 'https://elsewhere.example/'}).status_code == 400
    assert client.patch(f"{BASE}/sites/{site['id']}", headers=headers, json={'position': -1}).status_code == 422
    assert client.patch(f"{BASE}/sites/missing", headers=headers, json={'pinned': True}).status_code == 404
    assert [row['id'] for row in client.get(f'{BASE}/sites', headers=headers, params={'pinned': 'false'}).json()] == [site['id']]
    assert client.get(f'{BASE}/sites', headers=headers, params={'pinned': 'true'}).json() == []
    note = client.post(f"{BASE}/sites/{site['id']}/notes", headers=headers, json={'content': 'SSO only'})
    assert note.status_code == 200 and note.json()['author'] == 'user'
    assert client.post(f"{BASE}/sites/{site['id']}/notes", headers=headers, json={'content': 'x' * 2001}).status_code == 422
    assert client.post(f"{BASE}/sites/{site['id']}/notes", headers=headers, json={'content': '​'}).status_code == 400
    assert client.get(f"{BASE}/sites/{site['id']}/notes", headers=who['view']).status_code == 404  # not the viewer's site
    assert [n['content'] for n in client.get(f"{BASE}/sites/{site['id']}/notes", headers=headers).json()] == ['SSO only']
    assert client.delete(f"{BASE}/sites/{site['id']}/notes", headers=headers, params={'note_id': note.json()['id']}).status_code == 200
    assert client.delete(f"{BASE}/sites/{site['id']}/notes", headers=headers, params={'note_id': note.json()['id']}).status_code == 404
    assert client.delete(f"{BASE}/sites/{site['id']}/notes", headers=headers).status_code == 422
    assert client.delete(f"{BASE}/sites/{site['id']}", headers=headers).json() == {'id': site['id']}
    assert client.delete(f"{BASE}/sites/{site['id']}", headers=headers).status_code == 404
    assert client.get(f'{BASE}/sites', headers=headers).json() == []


def test_other_users_see_and_touch_nothing_and_ids_do_not_leak_existence(api):
    state, client = api; who = users(state)
    profile = profile_of(client, who['ctl'])
    site = client.post(f'{BASE}/sites', headers=who['ctl'], json={'profile_id': profile['id'], 'url': 'https://example.com'}).json()
    client.post(f"{BASE}/sites/{site['id']}/notes", headers=who['ctl'], json={'content': 'private note'})
    other = who['other']
    assert client.get(f'{BASE}/sites', headers=other).json() == []
    for method, path, body in (('patch', f"/sites/{site['id']}", {'pinned': False}), ('delete', f"/sites/{site['id']}", None),
                               ('post', f"/sites/{site['id']}/open", None), ('get', f"/sites/{site['id']}/notes", None),
                               ('post', f"/sites/{site['id']}/notes", {'content': 'x'}), ('delete', f"/sites/{site['id']}/notes?note_id=n", None)):
        kwargs = {'json': body} if body is not None else {}
        assert getattr(client, method)(BASE + path, headers=other, **kwargs).status_code == 404, (method, path)
    assert client.post(f'{BASE}/sites', headers=other, json={'profile_id': profile['id'], 'url': 'https://example.com'}).status_code == 404
    assert client.get(f"{BASE}/sites/{site['id']}/notes", headers=who['ctl']).json()[0]['content'] == 'private note'


def test_suggestions_endpoint_returns_frequent_unsaved_origins(api):
    state, client = api; who = users(state)
    profile = profile_of(client, who['ctl']); principal = principal_for(state, who['ctl']['Authorization'].split()[1])
    for step in range(4):
        state.browser.touch_site({'principal_id': principal, 'profile_id': profile['id'], 'project_id': '', 'url': 'https://often.example/a?q=1'}, 'Often', now=1e9 + step * 86400)
    rows = client.get(f'{BASE}/sites/suggestions', headers=who['ctl']).json()
    assert [row['origin'] for row in rows] == ['https://often.example'] and rows[0]['visit_count'] == 4 and rows[0]['profile_id'] == profile['id']
    assert client.get(f'{BASE}/sites/suggestions', headers=who['ctl'], params={'limit': 0}).status_code == 422
    assert client.get(f'{BASE}/sites/suggestions', headers=who['other']).json() == []
    client.post(f'{BASE}/sites', headers=who['ctl'], json={'profile_id': profile['id'], 'url': 'https://often.example'})
    assert client.get(f'{BASE}/sites/suggestions', headers=who['ctl']).json() == []


def test_open_returns_the_site_and_a_human_owned_tab_and_reuses_it(api):
    state, client = api; who = users(state)
    created = fake_tabs(state.browser)
    profile = profile_of(client, who['ctl'])
    site = client.post(f'{BASE}/sites', headers=who['ctl'], json={'profile_id': profile['id'], 'url': 'https://example.com/start', 'open_mode': 'app'}).json()
    opened = client.post(f"{BASE}/sites/{site['id']}/open", headers=who['ctl'])
    assert opened.status_code == 200, opened.text
    body = opened.json()
    assert set(body) == {'site', 'tab', 'reused'} and body['reused'] is False
    assert body['tab']['state'] == 'human' and body['tab']['url'] == 'https://example.com/start' and body['site']['tab_id'] == body['tab']['id']
    assert client.post(f"{BASE}/sites/{site['id']}/open", headers=who['ctl']).json()['reused'] is True and len(created) == 1
    assert client.get(f'{BASE}/tabs', headers=who['ctl']).json()[0]['id'] == body['tab']['id']


class Subscription:
    def __init__(self, client, token, query='subscription { browser_site_events { type id site { id title pinned visit_count note_count } } }'):
        self.ws = client.websocket_connect('/graphql', subprotocols=['graphql-transport-ws'])
        self.token = token; self.query = query
    def __enter__(self):
        self.socket = self.ws.__enter__()
        self.socket.send_json({'type': 'connection_init', 'payload': {'authorization': 'Bearer ' + self.token}})
        assert self.socket.receive_json()['type'] == 'connection_ack'
        self.socket.send_json({'id': '1', 'type': 'subscribe', 'payload': {'query': self.query}})
        return self
    def __exit__(self, *exc): return self.ws.__exit__(*exc)
    def next(self, wanted=None, timeout=8):
        deadline = time.time() + timeout
        while time.time() < deadline:
            message = self.socket.receive_json()
            if message.get('type') == 'next' and not message['payload'].get('errors'):
                event = message['payload']['data']['browser_site_events']
                if wanted is None or wanted(event):
                    return event
            elif message.get('type') in {'error', 'complete'} or message.get('payload', {}).get('errors'):
                return message
        raise AssertionError('no matching browser site event')


def test_subscription_streams_upserts_visits_and_removals_for_the_caller_only(api, monkeypatch):
    from termx.graphql import subscriptions
    monkeypatch.setattr(subscriptions, 'BROWSER_SITE_POLL_S', 0.05)
    state, client = api
    ctl_token = state.tokens.issue(['desktop-view', 'desktop-control']); other_token = state.tokens.issue(['desktop-view', 'desktop-control'])
    ctl = bearer(ctl_token); other = bearer(other_token)
    profile = profile_of(client, ctl); other_profile = profile_of(client, other, 'Theirs')
    existing = client.post(f'{BASE}/sites', headers=ctl, json={'profile_id': profile['id'], 'url': 'https://example.com', 'title': 'Existing'}).json()
    principal = principal_for(state, ctl_token)
    with Subscription(client, ctl_token) as mine, Subscription(client, other_token) as theirs:
        first = mine.next()
        assert first['type'] == 'browser.site.upserted' and first['id'] == existing['id'] and first['site']['title'] == 'Existing'
        # The other user's stream starts empty: their first event is their own site, never the caller's.
        own = client.post(f'{BASE}/sites', headers=other, json={'profile_id': other_profile['id'], 'url': 'https://theirs.example'}).json()
        got = theirs.next()
        assert got['id'] == own['id'] and got['site']['id'] == own['id']
        # Edit -> upserted; note -> upserted (note_count); visit -> visited; delete -> removed.
        client.patch(f"{BASE}/sites/{existing['id']}", headers=ctl, json={'pinned': False})
        assert mine.next(lambda e: e['type'] == 'browser.site.upserted')['site']['pinned'] is False
        client.post(f"{BASE}/sites/{existing['id']}/notes", headers=ctl, json={'content': 'hello'})
        assert mine.next(lambda e: e['type'] == 'browser.site.upserted')['site']['note_count'] == 1
        state.browser.touch_site({'principal_id': principal, 'profile_id': profile['id'], 'project_id': '', 'url': 'https://example.com/x'}, 'T')
        visited = mine.next(lambda e: e['type'] == 'browser.site.visited')
        assert visited['id'] == existing['id'] and visited['site']['visit_count'] == 1
        client.delete(f"{BASE}/sites/{existing['id']}", headers=ctl)
        removed = mine.next(lambda e: e['type'] == 'browser.site.removed')
        assert removed['id'] == existing['id'] and removed['site'] is None


def test_subscription_requires_the_view_scope(api):
    state, client = api
    token = state.tokens.issue(['agent-view'])
    with Subscription(client, token) as denied:
        result = denied.next()
    assert 'missing scope: desktop-view' in str(result)
