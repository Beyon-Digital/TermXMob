"""Round-2 review fixes: moving a view between sources, and unique pivot column labels."""
from __future__ import annotations

from test_data_views import call, env, spec, table_view  # noqa: F401


def test_a_view_can_move_to_a_shared_source_and_the_move_is_recorded(env):
    """A source-only edit is a real change (new revision, history entry), and a shared source is fine."""
    other = env.runtime.create_connection(env.actors['alice'], env.wid, {
        'name': 'Sales copy', 'engine': 'sqlite', 'config': {'path': str(env.sales_path)}, 'shared_read': True})['id']
    view = table_view(env, 'Movable')
    moved = call(env, 'PATCH', '/views/' + view['id'], 'alice', {'revision': view['revision'], 'spec': spec(other)})
    assert moved.status_code == 200, moved.text
    assert moved.json()['connection_id'] == other and moved.json()['revision'] == view['revision'] + 1
    history = call(env, 'GET', '/views/' + view['id'] + '/history', 'alice')
    assert history.status_code == 200, history.text
    assert any(c['field'] == 'source' for row in history.json()['revisions'] for c in row['changes'])


def test_a_private_source_is_refused_with_a_reason(env):
    view = table_view(env, 'Alices view two')
    moved = call(env, 'PATCH', '/views/' + view['id'], 'carol', {'revision': view['revision'], 'spec': spec(env.carol_cid)})
    assert moved.status_code == 403 and moved.json()['detail']['code'] == 'source_not_shared'
    assert call(env, 'GET', '/views/' + view['id'], 'alice').json()['connection_id'] == env.cid


def test_unique_labels_never_repeat_for_different_values():
    from termx.data.pivot import PivotColumnValue as V, unique_labels
    assert unique_labels([V('a'), V(None, True)]) == ['a', '(empty)']
    assert unique_labels([V('(empty)'), V(None, True)]) == ['(empty)', '(no value)']
    assert unique_labels([V('(blank)'), V('')]) == ['(blank)', '(empty text)']
    assert unique_labels([V('a'), V('a ')]) == ['a', '"a "']
