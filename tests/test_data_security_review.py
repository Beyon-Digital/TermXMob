"""Security review of views, segments and pivots (phase 2). Each test proves one confirmed issue and fails today."""
from __future__ import annotations

import asyncio
import threading
import time

from test_data_views import (AND, F, REF, call, env, month, pivot_spec, run, segment,  # noqa: F401
                             segment_spec, spec, table_view)

from termx.data.connectors import SQLiteConnector
from termx.data.query_spec import QuerySpec
from termx.data.runtime import MAX_RUNS_PER_ACTOR


def test_pivot_probes_respect_the_per_person_run_cap(env, monkeypatch):
    """Every pivot with columns runs a probe through `execute_read`, which never calls `_check_capacity`.
    Compile, view run and drill (without a request_id) therefore let one person hold up to READ_WORKERS source reads
    at once, past MAX_RUNS_PER_ACTOR, and starve every other person's runs."""
    original = SQLiteConnector.execute
    gate = threading.Lock()
    state = {'now': 0, 'peak': 0}

    def slow(self, *args, **kwargs):
        with gate:
            state['now'] += 1
            state['peak'] = max(state['peak'], state['now'])
        try:
            time.sleep(.4)
            return original(self, *args, **kwargs)
        finally:
            with gate:
                state['now'] -= 1
    monkeypatch.setattr(SQLiteConnector, 'execute', slow)
    body = QuerySpec.model_validate(pivot_spec(env.cid, ['region'], month()))
    call(env, 'POST', '/query-specs/compile', 'alice', {'spec': spec(env.cid)})   # warm the schema cache
    state['peak'] = 0

    async def burst():
        query = env.service.query
        return await asyncio.gather(*(query.compile(env.actors['alice'], env.wid, body) for _ in range(12)),
                                    return_exceptions=True)
    asyncio.run(burst())
    assert state['peak'] <= MAX_RUNS_PER_ACTOR, f"{state['peak']} concurrent source reads for one person (cap is {MAX_RUNS_PER_ACTOR})"


def test_editing_a_segment_cannot_push_a_referrer_past_the_depth_limit(env):
    """`check_references` walks only downwards from the segment being saved. Adding levels under the deepest
    segment saves fine and silently breaks every view above it (segment_depth at run time)."""
    leaf = segment(env, 'Leaf', F('amount', 'gt', value=1))
    mid = segment(env, 'Mid', REF(leaf['id']))
    top = segment(env, 'Top seg', REF(mid['id']))
    user = table_view(env, 'Uses top', filters=AND(REF(top['id'])))
    assert run(env, user['id']).status_code == 200            # view -> top -> mid -> leaf: the permitted depth
    extra = segment(env, 'Extra', F('amount', 'gt', value=2))
    patch = call(env, 'PATCH', '/views/' + leaf['id'], 'alice',
                 {'revision': leaf['revision'], 'spec': segment_spec(env.cid, REF(extra['id']))})
    still = run(env, user['id'])
    assert patch.status_code != 200 or still.status_code == 200, (
        'the edit was accepted and the existing view now fails: ' + still.text)


def test_an_editor_cannot_move_someone_elses_view_onto_a_private_connection(env):
    """`update` authorizes the old connection and the new one separately. Carol (editor) re-points Alice's view at
    her own unshared connection: Alice, the workspace owner, then gets 404 for it and can no longer delete it, which
    sidesteps the rule that only the owner or creator may delete."""
    view = table_view(env, 'Alices view')
    moved = call(env, 'PATCH', '/views/' + view['id'], 'carol', {'revision': view['revision'], 'spec': spec(env.carol_cid, limit=100)})
    owner_view = call(env, 'GET', '/views/' + view['id'], 'alice')
    assert moved.status_code != 200 or owner_view.status_code == 200, (
        f'carol moved it ({moved.status_code}); the owner now gets {owner_view.status_code}')
