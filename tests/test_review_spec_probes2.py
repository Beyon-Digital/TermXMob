"""Reviewer probes, batch 2: segments, roles, history, idempotency, export."""
from __future__ import annotations

import pytest

from test_data_views import (AND, C, F, REF, call, drill, env, finished, make_view, pivot_spec, run,  # noqa: F401
                             segment, spec, wait)
from test_review_spec_probes import sweep


def J(alias, table):
    return {'kind': 'left', 'table': table, 'alias': alias, 'on': [{'left': C('s', 'customer_id'), 'right': C(alias, 'id')}]}


def count_of(env, vid, **kw):
    return finished(env, vid, **kw)['result']['row_count']


def test_negated_segment_keeps_null_rows_and_partitions_the_table(env):
    seg = segment(env, 'Big', F('amount', 'gt', value=100))
    everything = make_view(env, 'table', 'all', spec(env.cid))
    inside = make_view(env, 'table', 'in', spec(env.cid, filters=AND(REF(seg['id']))))
    outside = make_view(env, 'table', 'out', spec(env.cid, filters=AND(REF(seg['id'], True))))
    a, i, o = (count_of(env, v['id']) for v in (everything, inside, outside))
    assert i + o == a, (a, i, o)


def test_pivot_with_joined_row_dimension_drills_exactly(env):
    p = pivot_spec(env.cid, [C('c', 'segment')], 'qty', joins=[J('c', 'customers')])
    view = make_view(env, 'pivot', 'joined', p)
    bad, _ = sweep(env, view)
    assert not bad, bad[:5]


def test_changing_joins_of_a_referenced_segment_is_refused(env):
    seg = segment(env, 'Big', F('amount', 'gt', value=100))
    make_view(env, 'table', 'uses', spec(env.cid, filters=AND(REF(seg['id']))))
    body = {**seg['spec'], 'joins': [J('c', 'customers')]}
    r = call(env, 'PATCH', '/views/' + seg['id'], 'alice', {'revision': seg['revision'], 'spec': body})
    assert r.status_code == 409 and r.json()['detail']['code'] == 'segment_in_use', r.text


def test_archive_referenced_segment_is_refused_and_unreferenced_ok(env):
    seg = segment(env, 'Big')
    use = make_view(env, 'table', 'uses', spec(env.cid, filters=AND(REF(seg['id']))))
    r = call(env, 'DELETE', f"/views/{seg['id']}?revision=1")
    assert r.status_code == 409 and r.json()['detail']['code'] == 'segment_in_use', r.text
    assert call(env, 'DELETE', f"/views/{use['id']}?revision=1").status_code == 200
    assert call(env, 'DELETE', f"/views/{seg['id']}?revision=1").status_code == 200


def test_viewer_cannot_see_or_run_unshared_views_and_sees_only_own_runs(env):
    shared = make_view(env, 'table', 'shared', spec(env.cid))
    priv = make_view(env, 'table', 'priv', spec(env.private_cid))
    names = [v['name'] for v in call(env, 'GET', '/views', 'vera').json()['views']]
    assert names == ['shared'], names
    assert call(env, 'GET', '/views/' + priv['id'], 'vera').status_code == 404
    assert run(env, priv['id'], 'vera').status_code in (403, 404)
    finished(env, shared['id'], 'alice')
    finished(env, shared['id'], 'vera')
    mine = call(env, 'GET', f"/views/{shared['id']}/runs", 'vera').json()['runs']
    assert mine and all(r['mine'] for r in mine)
    allr = call(env, 'GET', f"/views/{shared['id']}/runs", 'alice').json()['runs']
    assert len(allr) > len(mine)
    assert call(env, 'GET', f"/views/{shared['id']}/history", 'vera').status_code == 403
    assert call(env, 'POST', f"/views/{shared['id']}/drill", 'vera', {'coordinates': {}}).status_code == 403
    assert call(env, 'PATCH', f"/views/{shared['id']}", 'vera', {'revision': 1, 'name': 'x'}).status_code == 403
    assert call(env, 'POST', '/views', 'vera', {'kind': 'table', 'name': 'v', 'spec': spec(env.cid)}).status_code == 403


def test_other_editor_cannot_archive_and_history_records_changes(env):
    v = make_view(env, 'table', 'mine', spec(env.cid))
    assert call(env, 'DELETE', f"/views/{v['id']}?revision=1", 'carol').status_code == 403
    r = call(env, 'PATCH', '/views/' + v['id'], 'carol', {'revision': 1, 'spec': spec(env.cid, filters=AND(F('amount', 'gt', value=5)))})
    assert r.status_code == 200, r.text
    h = call(env, 'GET', f"/views/{v['id']}/history").json()['revisions']
    assert [x['revision'] for x in h] == [2, 1]
    assert h[0]['actor']['name'] == 'carol'
    assert any(c['field'] == 'filters' and c['op'] == 'added' for c in h[0]['changes']), h[0]
    stale = call(env, 'PATCH', '/views/' + v['id'], 'alice', {'revision': 1, 'name': 'renamed'})
    assert stale.status_code == 409 and stale.json()['detail']['code'] == 'revision_conflict'
    assert stale.json()['detail']['updated_by']['name'] == 'carol'


def test_names_unique_case_insensitively_within_kind_only(env):
    make_view(env, 'table', 'Sales Report', spec(env.cid))
    post = lambda kind, name, s: call(env, 'POST', '/views', 'alice', {'kind': kind, 'name': name, 'spec': s}).status_code  # noqa: E731
    assert post('table', ' sales report ', spec(env.cid)) == 409
    assert post('table', 'STRASSE', spec(env.cid)) == 201
    assert post('table', 'straße', spec(env.cid)) == 409
    assert post('segment', 'Sales Report', spec(env.cid, select=[], sort=[], filters=AND(F('amount', 'gt', value=1)))) == 201


def test_request_id_reuse_across_views_conflicts(env):
    a = make_view(env, 'table', 'a', spec(env.cid))
    b = make_view(env, 'table', 'b', spec(env.cid, limit=5))
    assert run(env, a['id'], request_id='same').status_code == 200
    assert run(env, a['id'], request_id='same').status_code == 200
    assert run(env, b['id'], request_id='same').status_code == 409


def test_pivot_csv_export_uses_labels_and_neutralises_formulas(env):
    p = pivot_spec(env.cid, ['region'], 'qty', fn='sum', measure='amount')
    p['pivot']['value']['label'] = '=HYPERLINK("x")'
    p['pivot']['rows'][0]['label'] = '+Area'
    view = make_view(env, 'pivot', 'csv', p)
    done = finished(env, view['id'])
    r = call(env, 'GET', f"/views/{view['id']}/runs/{done['id']}/export.csv")
    assert r.status_code == 200, r.text
    header = r.text.splitlines()[0]
    assert 'pv_' not in header
    first = header.split(',')[0].strip('"')
    assert not first.startswith(('+', '=', '-', '@')), header
    assert '=HYPERLINK' not in header.replace("'=HYPERLINK", ''), header


def test_segment_count_run_and_pivot_count_mode_rejected(env):
    seg = segment(env, 'Big')
    done = finished(env, seg['id'], mode='count')
    assert 'Members' in str(done['output']), done['output']
    pv = make_view(env, 'pivot', 'p', pivot_spec(env.cid, ['region'], None))
    assert run(env, pv['id'], mode='count').status_code == 422


def test_pivot_totals_false_without_columns_is_rejected(env):
    p = pivot_spec(env.cid, ['region'], None)
    p['pivot']['totals'] = False
    assert call(env, 'POST', '/views', 'alice', {'kind': 'pivot', 'name': 'x', 'spec': p}).status_code == 422


def test_bootstrap_advertises_views(env):
    r = env.client.get('/api/data/bootstrap', headers={'Authorization': 'Bearer ' + env.sessions['alice'].access_token})
    assert r.status_code == 200, r.text
    caps = r.json()['capabilities']
    assert caps.get('views') is True and caps.get('pivot') is True and caps.get('segments') is True, caps
