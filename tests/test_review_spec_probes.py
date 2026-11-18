"""Reviewer probes: drill-down parity for every pivot row dimension kind, plus misc invariants."""
from __future__ import annotations

import time

import pytest

from test_data_views import (AND, C, F, REF, call, drill, drilled_count, env, finished, make_view,  # noqa: F401
                             pivot_spec, run, segment, spec, table_view, wait)


def sweep(env, view, rows_dim_count=1):
    done = finished(env, view['id'])
    meta = done['output']['columns']
    cells = [(i, c) for i, c in enumerate(meta) if c['role'] == 'pivot_cell']
    bad = []
    for row in done['result']['rows']:
        keys = list(row[:rows_dim_count])
        for i, c in cells:
            if row[i] in (0, None):
                continue
            body = drill(env, view['id'], {'rows': keys, 'column': {'value': c['column_value'], 'empty': c['column_empty']}},
                         request_id=f'd-{time.monotonic_ns()}')
            if body.status_code != 200:
                bad.append((keys, c['label'], body.status_code, body.text[:120]))
                continue
            job = wait(env, body.json()['job'])
            if job['status'] != 'completed' or job['result']['row_count'] != row[i]:
                bad.append((keys, c['label'], row[i], job['status'], job.get('result', {}).get('row_count')))
        if cells == []:
            continue
    return bad, done


@pytest.mark.parametrize('dim', ['region', 'tag', 'tier', 'flag', 'qty', 'amount', 'price', 'day', 'stamp', 'note'])
def test_row_dimension_drill_parity(env, dim):
    view = make_view(env, 'pivot', f'by {dim}', pivot_spec(env.cid, [dim], 'qty' if dim != 'qty' else 'flag'))
    bad, done = sweep(env, view)
    assert not bad, bad[:5]


@pytest.mark.parametrize('dim', ['tag', 'flag', 'price', 'stamp', 'amount'])
def test_total_column_drill_parity(env, dim):
    view = make_view(env, 'pivot', f'tot {dim}', pivot_spec(env.cid, [dim], None))
    done = finished(env, view['id'])
    bad = []
    for row in done['result']['rows']:
        r = drill(env, view['id'], {'rows': [row[0]], 'column': None}, request_id=f'd-{time.monotonic_ns()}')
        if r.status_code != 200:
            bad.append((row[0], r.status_code, r.text[:120])); continue
        job = wait(env, r.json()['job'])
        if job['result']['row_count'] != row[-1]:
            bad.append((row[0], row[-1], job['result']['row_count']))
    assert not bad, bad[:5]
