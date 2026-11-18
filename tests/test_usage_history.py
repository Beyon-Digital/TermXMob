"""Usage history: redacted events in the workspace store, fed by finished tasks, read by the usage_query tool."""
from __future__ import annotations

import asyncio
import json
import sqlite3
from time import time

import pytest

from test_durable_workspace import workspace  # noqa: F401  (fixture)
from test_memory_scopes import agent_profile, conversation, run_tool, task_for, tool_context
from termx.agent.providers import ProviderCall
from termx.agent.tools import default_registry
from termx.workspace.store import WorkspaceStore
from termx.workspace.usage import UsageHistory, clean_text, parse_since

DAY = 86400


@pytest.fixture
def history(tmp_path):
    store = WorkspaceStore(tmp_path / 'usage.sqlite3')
    yield UsageHistory(store)
    store.close()


# -- record / top / recent / search ----------------------------------------------------------------------------

def test_record_then_recent_returns_newest_first(history):
    now = time()
    for index, name in enumerate(('first', 'second', 'third')):
        history.record('o', 'task', name, at=now - 100 + index)
    recent = history.recent('o')
    assert [e['subject'] for e in recent] == ['third', 'second', 'first']
    assert set(recent[0]) == {'id', 'kind', 'subject', 'at'}  # no detail key when there is none
    assert [e['subject'] for e in history.recent('o', limit=2)] == ['third', 'second']


def test_top_groups_by_subject_and_orders_by_count_then_recency(history):
    now = time()
    for _ in range(3):
        history.record('o', 'site', 'docs.python.org', at=now - 50)
    for _ in range(3):
        history.record('o', 'site', 'github.com', at=now - 10)  # same count, more recent
    history.record('o', 'site', 'news.example', at=now)
    history.record('o', 'cli_session', 'docs.python.org', at=now)  # a different kind is a different row
    top = history.top('o')
    assert [(r['kind'], r['subject'], r['count']) for r in top] == [
        ('site', 'github.com', 3), ('site', 'docs.python.org', 3), ('cli_session', 'docs.python.org', 1), ('site', 'news.example', 1)]
    assert history.top('o', kind='site', limit=1)[0]['subject'] == 'github.com'
    assert [r['subject'] for r in history.top('o', kind='cli_session')] == ['docs.python.org']


def test_since_filters_every_mode(history):
    now = time()
    history.record('o', 'task', 'old build', at=now - 10 * DAY)
    history.record('o', 'task', 'new build', at=now - 3600)
    assert [e['subject'] for e in history.recent('o', since='7d')] == ['new build']
    assert [r['subject'] for r in history.top('o', since='24h')] == ['new build']
    assert [e['subject'] for e in history.search('o', 'build', since='7d')] == ['new build']
    assert len(history.recent('o', since=now - 11 * DAY)) == 2
    assert parse_since('2026-01-01') < parse_since('2026-01-02T00:00:00Z') < parse_since('30m')
    for bad in ('yesterday-ish', '7 parsecs', '2026-13-45'):
        with pytest.raises(ValueError, match='since'):
            parse_since(bad)


def test_search_ranks_by_subject_with_stemming_prefix_and_substring_fallback(history):
    history.record('o', 'task', 'Refactor the authentication middleware')
    history.record('o', 'task', 'Fix flaky auth tests')
    history.record('o', 'task', 'Update the billing page')
    assert {e['subject'] for e in history.search('o', 'authentication')} == {'Refactor the authentication middleware'}
    assert {e['subject'] for e in history.search('o', 'auth')} == {'Refactor the authentication middleware', 'Fix flaky auth tests'}
    assert [e['subject'] for e in history.search('o', 'refactoring middleware')] == ['Refactor the authentication middleware']
    assert {e['subject'] for e in history.search('o', 'billing nonsense')} == {'Update the billing page'}  # any-word fallback
    assert history.search('o', 'zzz') == []
    assert [e['subject'] for e in history.search('o', 'task', kind='task')] == []
    assert len(history.search('o', '')) == 3  # nothing to match on lists the newest
    history.fts = False  # no FTS5: substring match
    assert [e['subject'] for e in history.search('o', 'billing')] == ['Update the billing page']
    assert history.search('o', '100%_') == []  # LIKE wildcards are literal


def test_search_syntax_cannot_break_the_query(history):
    history.record('o', 'task', 'plain subject')
    for query in ('"', 'a OR', 'NEAR(', 'subject:x', '*', "'; DROP TABLE usage_events; --"):
        history.search('o', query)
    assert history.recent('o')


# -- isolation -------------------------------------------------------------------------------------------------

def test_owners_never_see_each_others_history(history):
    history.record('alice', 'task', 'alice private build')
    history.record('bob', 'task', 'bob private build')
    for owner, other in (('alice', 'bob'), ('bob', 'alice')):
        subjects = [e['subject'] for e in history.recent(owner)] + [r['subject'] for r in history.top(owner)] \
            + [e['subject'] for e in history.search(owner, 'build')]
        assert subjects and all(owner in s for s in subjects) and not any(other in s for s in subjects)
    assert history.clear('alice') == 1
    assert [e['subject'] for e in history.recent('bob')] == ['bob private build']
    assert history.search('alice', 'private') == []


def test_project_scope_shows_global_and_own_project_events_only(history):
    history.record('o', 'site', 'global site')  # no project
    history.record('o', 'task', 'in a', project_id='a')
    history.record('o', 'task', 'in b', project_id='b')
    assert {e['subject'] for e in history.recent('o', project_id='a')} == {'global site', 'in a'}
    assert {e['subject'] for e in history.recent('o', project_id=None)} == {'global site'}
    assert {e['subject'] for e in history.recent('o')} == {'global site', 'in a', 'in b'}  # the owner's own view
    assert {r['subject'] for r in history.top('o', project_id='b')} == {'global site', 'in b'}
    assert {e['subject'] for e in history.search('o', 'in', project_id='a')} == {'in a'}


# -- redaction and bounds --------------------------------------------------------------------------------------

def test_record_redacts_and_bounds_what_it_stores(history):
    history.record('o', 'task', 'deploy with api_key=SUPERSECRETVALUE and sk-abcdefghijklmnopqrstuvwxyz1234 now',
                   detail={'note': 'password: hunter2', 'count': 3, 'nested': {'x': 1}, 'ok': True})
    history.record('o', 'site', 'https://user:pass@example.com/path?token=abc123&page=2&session_id=zzz')
    history.record('o', 'site', 'https://example.com/p?page=2&session_id=zzz&sort=asc')
    history.record('o', 'task', 'line one\nline two\x00\x07\ttabbed ' + 'x' * 500)
    stored = history.store.db.execute('SELECT subject,detail FROM usage_events').fetchall()
    blob = json.dumps([tuple(r) for r in stored])
    for leaked in ('SUPERSECRETVALUE', 'sk-abcdefgh', 'hunter2', 'user:pass', 'abc123', 'zzz', '\x00', '\x07'):
        assert leaked not in blob
    subjects = {r['subject'] for r in stored}
    assert 'https://example.com/path?[redacted]' in subjects  # userinfo dropped; the shared redactor takes the rest of a token= query
    assert 'https://example.com/p?page=2&session_id=[redacted]&sort=asc' in subjects  # a secret-named parameter alone
    assert all('\n' not in s and len(s) <= 200 for s in subjects)
    detail = json.loads(next(r['detail'] for r in stored if r['detail']))
    assert detail == {'count': 3, 'note': '[redacted]', 'ok': True}  # flat values only; nested objects are dropped
    # redaction also reaches the index, so a secret cannot be searched for
    assert history.search('o', 'SUPERSECRETVALUE') == []


def test_clean_text_and_empty_subjects():
    assert clean_text('  a \n b\t c  ', 50) == 'a b c'
    assert clean_text('x' * 300, 40) == 'x' * 40
    assert clean_text(None, 10) == ''


def test_record_validates_kind_and_skips_empty_subjects(history):
    for bad in ('', 'Task', 'has space', '9lives', 'x' * 40, None):
        with pytest.raises(ValueError, match='kind'):
            history.record('o', bad, 'subject')
    with pytest.raises(ValueError):
        history.record('', 'task', 'subject')
    assert history.record('o', 'task', '   ') is None and history.recent('o') == []


def test_a_key_makes_recording_idempotent(history):
    first = history.record('o', 'task', 'once', key='task-1')
    assert first == 'task:task-1'
    assert history.record('o', 'task', 'once again', key='task-1') is None
    assert [e['subject'] for e in history.recent('o')] == ['once']
    assert history.record('o', 'site', 'other kind', key='task-1')  # the key is per kind


# -- retention, backfill and fallback --------------------------------------------------------------------------

def test_events_past_retention_are_pruned_with_their_index_rows(tmp_path):
    store = WorkspaceStore(tmp_path / 'prune.sqlite3')
    history = UsageHistory(store, retention_days=30)
    history.record('o', 'task', 'ancient build', at=time() - 45 * DAY)
    history.record('o', 'task', 'fresh build')
    assert history.prune() == 1
    assert [e['subject'] for e in history.recent('o')] == ['fresh build']
    assert store.db.execute('SELECT COUNT(*) FROM usage_fts').fetchone()[0] == 1
    history.record('o', 'task', 'ancient again', at=time() - 400 * DAY)
    UsageHistory(store, retention_days=30)  # opening prunes too
    assert [e['subject'] for e in history.recent('o')] == ['fresh build']
    store.close()


def test_the_index_is_backfilled_when_events_predate_it(tmp_path):
    path = tmp_path / 'legacy.sqlite3'
    store = WorkspaceStore(path)
    first = UsageHistory(store)
    first.record('o', 'task', 'indexed later')
    store.db.execute('DROP TABLE usage_fts')
    store.db.commit()
    store.close()
    store = WorkspaceStore(path)
    reopened = UsageHistory(store)
    assert [e['subject'] for e in reopened.search('o', 'later')] == ['indexed later']
    assert store.db.execute('SELECT COUNT(*) FROM usage_fts').fetchone()[0] == 1
    store.close()


def test_schema_matches_the_documented_columns(history):
    columns = [row[1] for row in history.store.db.execute('PRAGMA table_info(usage_events)')]
    assert columns == ['id', 'owner', 'project_id', 'kind', 'subject', 'detail', 'at']
    assert history.store.db.execute("SELECT sql FROM sqlite_master WHERE name='usage_fts'").fetchone()[0].count('subject') >= 1


# -- task completion feeds the history -------------------------------------------------------------------------

def finish(service, task_id, kind='task.completed'):
    async def go():
        service._on_event(task_id, {'type': kind, 'payload': {}})
        await asyncio.gather(*list(service._workers), return_exceptions=True)  # the after-turn hook run, if any
        await asyncio.sleep(0)

    asyncio.run(go())


def started_task(service, owner, path, *, prompt='work', title=None, profile=None, project_id='project-a'):
    row = conversation(service, owner, path, profile, project_id=project_id)
    if title:
        service.update_session(owner, row['id'], revision=row['revision'], changes={'title': title})
    task = service.agents.create_task(prompt=prompt, cwd=row['cwd'], provider_id='fixture', model='fixture-model', limits={})
    service.store.create('task', owner.id, {'conversation_id': row['id'], 'cwd': row['cwd']}, project_id, task['id'])
    return row, task


def test_a_finished_task_records_one_redacted_event(workspace):
    service, owner, path = workspace
    _row, task = started_task(service, owner, path, title='Ship the importer', prompt='Use api_key=PROMPTSECRET1234 to import')
    service.agents.update_task(task['id'], metrics={'input_tokens': 1200, 'output_tokens': 300, 'cost_usd': 0.01234,
                                                    'tool_calls': {'read_file': 3, 'run_shell': 2}})
    finish(service, task['id'])
    finish(service, task['id'])  # a repeated event adds nothing
    (event,) = service.usage.recent(owner.id)
    assert (event['kind'], event['subject']) == ('task', 'Ship the importer')
    detail = json.loads(event['detail'])
    assert detail['outcome'] == 'completed' and detail['model'] == 'fixture-model'
    assert (detail['tokens'], detail['tool_calls'], detail['cost_usd']) == (1500, 5, 0.0123)
    assert 'PROMPTSECRET' not in json.dumps(service.usage.recent(owner.id))


def test_without_a_title_the_subject_is_a_short_redacted_prompt_head_and_never_the_result(workspace):
    service, owner, path = workspace
    long_prompt = 'Investigate the flaky checkout test with token=LEAKME9999 ' + 'padding ' * 40
    _row, task = started_task(service, owner, path, prompt=long_prompt)
    service.agents.update_task(task['id'], result='RESULT-BODY should not be stored')
    finish(service, task['id'])
    (event,) = service.usage.recent(owner.id)
    assert event['subject'].startswith('Investigate the flaky checkout test') and len(event['subject']) <= 80
    stored = json.dumps(list(map(tuple, service.store.db.execute('SELECT * FROM usage_events'))))
    assert 'LEAKME9999' not in stored and 'RESULT-BODY' not in stored and 'padding padding padding padding padding' not in stored


def test_failed_and_cancelled_tasks_record_their_outcome(workspace):
    service, owner, path = workspace
    for kind, outcome in (('task.failed', 'failed'), ('task.cancelled', 'cancelled')):
        _row, task = started_task(service, owner, path, title=f'run {outcome}')
        finish(service, task['id'], kind)
    outcomes = {e['subject']: json.loads(e['detail'])['outcome'] for e in service.usage.recent(owner.id)}
    assert outcomes == {'run failed': 'failed', 'run cancelled': 'cancelled'}


def test_sub_agent_tasks_and_unenrolled_tasks_are_not_recorded(workspace):
    service, owner, path = workspace
    row, parent = started_task(service, owner, path, title='parent work')
    child = service.agents.create_task(prompt='child', cwd=row['cwd'], provider_id='fixture', model='fixture', limits={})
    service.store.create('task', owner.id, {'conversation_id': row['id'], 'cwd': row['cwd'], 'parent_id': parent['id']},
                         'project-a', child['id'])
    stray = service.agents.create_task(prompt='stray', cwd=str(path), provider_id='fixture', model='fixture', limits={})
    finish(service, child['id'])
    finish(service, stray['id'])
    assert service.usage.recent(owner.id) == []


def test_a_failure_to_record_never_disturbs_the_task_event(workspace, monkeypatch):
    service, owner, path = workspace
    _row, task = started_task(service, owner, path, title='survives')

    def boom(*args, **kwargs):
        raise sqlite3.OperationalError('disk full')

    monkeypatch.setattr(service.usage, 'record', boom)
    finish(service, task['id'])  # no exception


# -- the usage_query tool --------------------------------------------------------------------------------------

def query(service, owner, path, profile=None, project_id='project-a', **arguments):
    row = conversation(service, owner, path, profile, project_id=project_id)
    ctx = tool_context(service, task_for(service, owner, row, profile))
    return run_tool('usage_query', ProviderCall('function', 'u', 'usage_query', arguments), ctx).result


def test_usage_query_is_read_only_never_asks_and_is_offered_in_ask_mode(workspace):
    service, owner, path = workspace
    spec = default_registry().get('usage_query')
    assert spec.mutability == 'read' and spec.approval == 'never' and spec.expose_read_only and spec.parallel_safe
    assert spec.parameters['properties']['mode']['enum'] == ['top', 'recent', 'search']
    assert spec.parameters['additionalProperties'] is False
    row = conversation(service, owner, path)
    for read_only in (False, True):
        ctx = tool_context(service, task_for(service, owner, row), read_only=read_only)
        decision = spec.decide(ProviderCall('function', 'u', 'usage_query', {}), ctx)
        assert decision.allowed and not decision.approval_required
    assert 'usage_query' in {t['name'] for t in default_registry().provider_tools(read_only=True)}


def test_usage_query_modes_return_untrusted_data(workspace):
    service, owner, path = workspace
    now = time()
    for _ in range(3):
        service.usage.record(owner.id, 'site', 'docs.python.org', at=now - 60)
    service.usage.record(owner.id, 'site', 'example.org', at=now - 5)
    service.usage.record(owner.id, 'task', 'Fix the parser', project_id='project-a', at=now - 1)
    top = query(service, owner, path, mode='top')
    assert top['ok'] and top['untrusted'] is True and 'not instructions' in top['note'] and top['mode'] == 'top'
    assert top['results'][0] == {'kind': 'site', 'subject': 'docs.python.org', 'count': 3, 'last_at': pytest.approx(now - 60)}
    recent = query(service, owner, path)  # mode defaults to recent
    assert recent['mode'] == 'recent' and recent['results'][0]['subject'] == 'Fix the parser'
    assert [r['subject'] for r in query(service, owner, path, mode='recent', kind='site', limit=1)['results']] == ['example.org']
    assert [r['subject'] for r in query(service, owner, path, mode='search', query='parser')['results']] == ['Fix the parser']
    assert [r['subject'] for r in query(service, owner, path, mode='top', kind='site', since='1h')['results']] == ['docs.python.org', 'example.org']


def test_usage_query_rejects_bad_arguments_without_raising(workspace):
    service, owner, path = workspace
    assert 'mode' in query(service, owner, path, mode='bogus')['error']
    assert 'query' in query(service, owner, path, mode='search')['error']
    assert 'since' in query(service, owner, path, mode='recent', since='someday')['error']
    assert query(service, owner, path, mode='recent', limit=-3)['ok']


def test_usage_query_is_scoped_to_the_owner_and_the_tasks_project(workspace):
    service, owner, path = workspace
    service.usage.record('another-owner', 'task', 'rival build', project_id='project-a')
    service.usage.record(owner.id, 'task', 'my build in a', project_id='project-a')
    service.usage.record(owner.id, 'task', 'my build in b', project_id='project-b')
    service.usage.record(owner.id, 'site', 'my browsing')
    assert {r['subject'] for r in query(service, owner, path)['results']} == {'my build in a', 'my browsing'}
    assert {r['subject'] for r in query(service, owner, path, project_id='project-b')['results']} == {'my build in b', 'my browsing'}
    assert {r['subject'] for r in query(service, owner, path, project_id=None)['results']} == {'my browsing'}
    assert 'rival' not in json.dumps(query(service, owner, path, mode='search', query='rival build'))


def test_usage_query_stops_when_the_task_is_not_enrolled(workspace):
    service, owner, path = workspace
    task = service.agents.create_task(prompt='x', cwd=str(path), provider_id='fixture', model='fixture', limits={})
    result = run_tool('usage_query', ProviderCall('function', 'u', 'usage_query', {}), tool_context(service, task)).result
    assert result.get('refused')


def test_a_revoked_task_cannot_read_history(workspace):
    service, owner, path = workspace
    profile = agent_profile(service)
    row = conversation(service, owner, path, profile)
    task = task_for(service, owner, row, profile)
    service.usage.record(owner.id, 'task', 'anything')
    ctx = tool_context(service, task)
    assert run_tool('usage_query', ProviderCall('function', 'u', 'usage_query', {}), ctx).result['ok']
    original = service.validate_task_execution

    def revoked(task_id):
        raise PermissionError('revoked')

    service.validate_task_execution = revoked
    try:
        assert run_tool('usage_query', ProviderCall('function', 'u', 'usage_query', {}), ctx).result.get('refused')
    finally:
        service.validate_task_execution = original
