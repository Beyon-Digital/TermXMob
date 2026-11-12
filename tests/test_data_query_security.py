"""Security review findings for the QuerySpec endpoints. Each test fails on the reviewed commit.

Everything here goes through the real routes with the shared fixture from test_data_query_http.
"""
from __future__ import annotations

import json
import time

import pytest

from test_data_query_http import C, auth, env, get, post, spec, wait  # noqa: F401  (env is a fixture)


def raw(env, path, text, who='alice'):
    """POST a raw JSON text. A server-side exception is reported as status 500, as in production."""
    headers = {**auth(env, who), 'content-type': 'application/json'}
    try:
        return env.client.post(env.base + path, headers=headers, content=text).status_code
    except Exception:
        return 500


def self_join(cid, n=4):
    """Five-way self join on a four-valued column: accepted by the compiler, roughly 1305^5 / 4^4 rows."""
    joins = [{'kind': 'inner', 'table': 'orders', 'alias': f'b{i}',
              'on': [{'left': C('o', 'status'), 'right': C(f'b{i}', 'status')}]} for i in range(1, n + 1)]
    return spec(cid, joins=joins, select=[C('o', 'id')], limit=1)


# --- hostile request bodies must be rejected with 4xx, never an unhandled exception ---------------
@pytest.mark.parametrize('literal', ['NaN', 'Infinity', '-Infinity', '1e999'])
def test_non_finite_json_numbers_are_rejected_not_500(env, literal):
    body = json.dumps({'spec': spec(env.source['id'])}).replace('"offset": 0', f'"offset": {literal}')
    assert raw(env, '/query-specs/compile', body) in (400, 422)
    filtered = json.dumps({'spec': spec(env.source['id'], filters={'op': 'and', 'items': [
        {'field': C('o', 'amount'), 'op': 'eq', 'value': 1}]})}).replace('"value": 1}', f'"value": {literal}}}')
    assert raw(env, '/query-specs/compile', filtered) in (400, 422)


def test_lone_surrogate_in_label_is_rejected_not_500(env):
    body = json.dumps({'spec': spec(env.source['id'], select=[{'table_alias': 'o', 'column': 'id', 'label': '\ud800'}])})
    assert raw(env, '/query-specs/compile', body) in (400, 422)


def test_lone_surrogate_in_request_id_is_rejected_not_500(env):
    body = json.dumps({'request_id': '\ud800', 'spec': spec(env.source['id'])})
    assert raw(env, '/query-specs/run', body) in (400, 422)


def test_lone_surrogate_in_filter_value_is_rejected_not_500(env):
    f = {'field': C('o', 'status'), 'op': 'eq', 'value': '\ud800'}
    body = json.dumps({'spec': spec(env.source['id'], filters={'op': 'and', 'items': [f]})})
    assert raw(env, '/query-specs/compile', body) in (400, 422)


def test_deeply_nested_filters_are_rejected_not_500(env):
    leaf = json.dumps({'field': C('o', 'id'), 'op': 'is_null'})
    nested = '{"op":"and","items":[' * 3000 + leaf + ']}' * 3000
    body = json.dumps({'spec': spec(env.source['id'], filters={'op': 'and', 'items': []})}).replace(
        '{"op": "and", "items": []}', nested)
    assert raw(env, '/query-specs/compile', body) in (400, 422)


# --- value typing ----------------------------------------------------------------------------------
def test_integer_beyond_int64_in_number_filter_does_not_queue_a_doomed_job(env):
    """`integer` columns reject a value beyond int64; `number` columns bind it as a float (as the string form
    already is), so the run completes instead of failing after queueing."""
    f = {'field': C('o', 'amount'), 'op': 'gt', 'value': 10 ** 40}
    s = spec(env.source['id'], filters={'op': 'and', 'items': [f]})
    compiled = post(env, '/query-specs/compile', {'spec': s}).json()
    assert compiled['ok'] is True and compiled['parameters'] == {'p1': 1e40}
    job = wait(env, post(env, '/query-specs/run', {'request_id': 'huge', 'spec': s}).json())
    assert job['status'] == 'completed' and job['result']['rows'] == []


# --- resource exhaustion ---------------------------------------------------------------------------
def settle(env, job_id, who='alice', seconds=6):
    deadline, status = time.time() + seconds, 'running'
    job = {}
    while time.time() < deadline:
        job = get(env, '/jobs/' + job_id, who).json()
        status = job['status']
        if status not in ('queued', 'running'):
            break
        time.sleep(0.2)
    return status, job


def test_five_way_non_key_self_join_is_stopped_by_a_statement_deadline(env, monkeypatch):
    monkeypatch.setenv('TERMX_DATA_STATEMENT_TIMEOUT_SECONDS', '1')
    cid = env.source['id']
    job = post(env, '/query-specs/run', {'request_id': 'boom', 'spec': self_join(cid)}).json()
    try:
        status, body = settle(env, job['id'])
        assert status == 'failed', 'the run is still executing after 6 seconds with a 1 second source-side deadline'
        assert 'longer than 1 seconds and was stopped' in body['error'], body
    finally:
        env.runtime.cancel(env.actors['alice'], env.wid, job['id'])


def test_a_join_where_neither_side_is_a_key_is_warned_about(env):
    compiled = post(env, '/query-specs/compile', {'spec': self_join(env.source['id'], 1)}).json()
    assert compiled['ok'] is True
    assert [w['code'] for w in compiled['warnings']] == ['many_to_many']


def test_one_editor_cannot_starve_another_editors_queries(env, monkeypatch):
    monkeypatch.setenv('TERMX_DATA_STATEMENT_TIMEOUT_SECONDS', '3')
    cid = env.source['id']
    heavy = self_join(cid)
    answers = [post(env, '/query-specs/run', {'request_id': f'heavy-{i}', 'spec': heavy}) for i in range(40)]
    accepted = [a.json()['id'] for a in answers if a.status_code == 200]
    try:
        assert len(accepted) == 4, 'one person may have 4 runs in flight'
        refused = [a for a in answers if a.status_code == 429]
        assert len(refused) == 36 and 'already have 4 queries running' in refused[0].json()['detail']
        job = post(env, '/query-specs/run', {'request_id': 'simple', 'spec': spec(cid, limit=5)}, 'bob').json()
        status, _ = settle(env, job['id'], 'bob', 5)
        assert status == 'completed', "bob's 5-row query did not finish in 5 seconds while alice had 4 runs in flight"
    finally:
        for jid in accepted:
            env.runtime.cancel(env.actors['alice'], env.wid, jid)
        time.sleep(1)


def test_an_idempotent_replay_is_not_counted_against_the_cap(env, monkeypatch):
    monkeypatch.setenv('TERMX_DATA_STATEMENT_TIMEOUT_SECONDS', '3')
    cid = env.source['id']
    first = [post(env, '/query-specs/run', {'request_id': f'h{i}', 'spec': self_join(cid)}) for i in range(4)]
    try:
        again = post(env, '/query-specs/run', {'request_id': 'h0', 'spec': self_join(cid)})
        assert again.status_code == 200 and again.json()['id'] == first[0].json()['id']
    finally:
        for r in first:
            env.runtime.cancel(env.actors['alice'], env.wid, r.json()['id'])
        time.sleep(1)
