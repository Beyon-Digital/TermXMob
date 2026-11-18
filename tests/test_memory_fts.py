"""Memory recall: bm25-ranked FTS5 search that never widens what an agent may read.

The index (`memory_fts`) is only an ordering over notes `WorkspaceService.memories()` already allowed, so every scope
rule of test_memory_scopes holds here too. These tests cover ranking, scope isolation, index upkeep on edit, delete,
discard and expiry, backfilling of rows older than the index, and the substring fallback.
"""
from __future__ import annotations

import re
import sqlite3
from time import time

import pytest

from test_durable_workspace import workspace  # noqa: F401  (fixture)
from test_memory_scopes import agent_profile, conversation, delegated_task, run_tool, task_for, tool_context, write_call
from termx.agent.providers import ProviderCall
from termx.workspace.store import WorkspaceStore, fts_match


def read(service, owner, path, query=None, *, profile=None, limit=None, project_id='project-a'):
    row = conversation(service, owner, path, profile, project_id=project_id)
    arguments = {k: v for k, v in {'query': query, 'limit': limit}.items() if v is not None}
    result = run_tool('memory_read', ProviderCall('function', 'r', 'memory_read', arguments),
                      tool_context(service, task_for(service, owner, row, profile))).result
    assert result['ok'], result
    return [m['content'] for m in result['memories']]


def note(service, owner, content, **kwargs):
    return service.save_memory(owner, content=content, provenance='user', **kwargs)


def indexed(service):
    return {r['memory_id']: r['content'] for r in service.store.db.execute('SELECT memory_id,content FROM memory_fts')}


# -- ranking ---------------------------------------------------------------------------------------------------

def test_best_match_comes_first_by_bm25(workspace):
    service, owner, path = workspace
    note(service, owner, 'We discussed the cache once, among a long list of unrelated planning topics and lunch plans')
    note(service, owner, 'cache cache cache invalidation: the cache key includes the tenant')
    note(service, owner, 'Nothing relevant here')
    note(service, owner, 'The cache key')
    found = read(service, owner, path, 'cache key')
    assert found[0] == 'The cache key'  # short note with both words beats a long one with a passing mention
    assert found == ['The cache key', 'cache cache cache invalidation: the cache key includes the tenant']  # AND: both words
    assert read(service, owner, path, 'cache')[0].startswith('cache cache cache')
    assert len(read(service, owner, path, 'cache')) == 3


def test_words_match_by_prefix_and_stem(workspace):
    service, owner, path = workspace
    note(service, owner, 'Authentication uses short-lived tokens; deployment runs on Fridays')
    note(service, owner, 'Billing is monthly')
    assert read(service, owner, path, 'auth') == ['Authentication uses short-lived tokens; deployment runs on Fridays']
    assert read(service, owner, path, 'token deployments running') == ['Authentication uses short-lived tokens; deployment runs on Fridays']  # stems: tokens, deployment, runs
    assert read(service, owner, path, 'AUTHENTICATION deployment') != []


def test_all_words_must_match_but_any_word_is_the_fallback(workspace):
    service, owner, path = workspace
    note(service, owner, 'Redis stores sessions')
    note(service, owner, 'Postgres stores invoices')
    assert read(service, owner, path, 'redis sessions') == ['Redis stores sessions']
    assert sorted(read(service, owner, path, 'redis invoices')) == ['Postgres stores invoices', 'Redis stores sessions']
    assert read(service, owner, path, 'kafka') == []


def test_limit_and_empty_query_list_recent_notes(workspace):
    service, owner, path = workspace
    for index in range(4):
        note(service, owner, f'note number {index}')
    assert len(read(service, owner, path)) == 4
    assert len(read(service, owner, path, '', limit=2)) == 2
    assert len(read(service, owner, path, 'note', limit=3)) == 3


# -- scope isolation -------------------------------------------------------------------------------------------

def test_ranking_never_surfaces_notes_the_agent_may_not_read(workspace):
    service, owner, path = workspace
    first, second = agent_profile(service, 'First'), agent_profile(service, 'Second')
    note(service, owner, 'shared caching decision')
    note(service, owner, 'first agent caching caching caching', scope='agent', agent_id=first['id'])
    note(service, owner, 'second agent caching caching caching caching', scope='agent', agent_id=second['id'])
    note(service, owner, 'excluded caching caching caching caching caching', excluded=True)
    note(service, owner, 'other project caching caching caching caching', project_id='project-b', scope='project')
    note(service, owner, 'this project caching', project_id='project-a', scope='project')
    _row, _grant, task = delegated_task(service, owner, path, first)
    run_tool('memory_write', write_call(content='proposed caching caching caching caching caching caching'), tool_context(service, task))
    assert all('proposed' in m['content'] for m in service.memories(owner, state='proposed', all_agents=True))
    assert sorted(read(service, owner, path, 'caching', profile=first)) == [
        'first agent caching caching caching', 'shared caching decision', 'this project caching']
    assert sorted(read(service, owner, path, 'caching', profile=second)) == [
        'second agent caching caching caching caching', 'shared caching decision', 'this project caching']
    assert sorted(read(service, owner, path, 'caching')) == ['shared caching decision', 'this project caching']


def test_another_owners_notes_are_invisible_to_the_index(workspace):
    service, owner, path = workspace
    service.store.create('memory', 'someone-else', {'content': 'rival caching secret', 'provenance': 'user', 'excluded': False,
                                                    'expires_at': time() + 999, 'retention_days': 30, 'scope': 'user'})
    note(service, owner, 'my caching note')
    assert service.store.search_memory('someone-else', 'caching') != service.store.search_memory(owner.id, 'caching')
    assert read(service, owner, path, 'caching') == ['my caching note']
    assert read(service, owner, path, 'secret') == []


def test_search_memory_only_returns_ids_for_the_asking_owner(workspace):
    service, owner, _ = workspace
    mine = note(service, owner, 'alpha beta')
    theirs = service.store.create('memory', 'someone-else', {'content': 'alpha beta gamma'})
    assert service.store.search_memory(owner.id, 'alpha') == [mine['id']]
    assert service.store.search_memory('someone-else', 'alpha') == [theirs['id']]


# -- the index follows every write -----------------------------------------------------------------------------

def test_editing_a_note_replaces_its_indexed_text(workspace):
    service, owner, path = workspace
    saved = note(service, owner, 'uses zebra mode')
    assert read(service, owner, path, 'zebra') == ['uses zebra mode']
    edited = service.save_memory(owner, content='uses giraffe mode', provenance='user', identifier=saved['id'], revision=saved['revision'])
    assert read(service, owner, path, 'zebra') == []
    assert read(service, owner, path, 'giraffe') == ['uses giraffe mode']
    assert indexed(service) == {saved['id']: 'uses giraffe mode'} and edited['id'] == saved['id']


def test_deleting_and_discarding_notes_removes_them_from_the_index(workspace):
    service, owner, path = workspace
    gone = note(service, owner, 'delete-me marker')
    keep = note(service, owner, 'keep-me marker')
    service.delete_memory(owner, gone['id'])
    assert indexed(service) == {keep['id']: 'keep-me marker'}
    assert read(service, owner, path, 'marker') == ['keep-me marker']
    profile = agent_profile(service)
    _row, _grant, task = delegated_task(service, owner, path, profile)
    proposal = run_tool('memory_write', write_call(content='proposal marker'), tool_context(service, task)).result['id']
    assert proposal in indexed(service)
    assert read(service, owner, path, 'proposal', profile=profile) == []  # indexed, but never read back while proposed
    service.resolve_memory_proposal(owner, proposal, keep=False)
    assert proposal not in indexed(service)


def test_keeping_a_proposal_makes_it_readable_and_expired_notes_leave_the_index(workspace):
    service, owner, path = workspace
    profile = agent_profile(service)
    _row, _grant, task = delegated_task(service, owner, path, profile)
    proposal = run_tool('memory_write', write_call(content='kept marker'), tool_context(service, task)).result['id']
    service.resolve_memory_proposal(owner, proposal, keep=True)
    assert read(service, owner, path, 'kept', profile=profile) == ['kept marker']
    stale = service.store.create('memory', owner.id, {'content': 'stale marker', 'provenance': 'user', 'excluded': False,
                                                      'expires_at': time() - 5, 'retention_days': 30})
    assert stale['id'] in indexed(service)
    service.memories(owner)  # listing sweeps expired notes
    assert stale['id'] not in indexed(service)


def test_index_and_records_stay_in_step(workspace):
    service, owner, _ = workspace
    ids = [note(service, owner, f'entry {i} about topic{i}')['id'] for i in range(5)]
    service.delete_memory(owner, ids[0])
    assert set(indexed(service)) == set(ids[1:])
    notes = service.store.db.execute("SELECT COUNT(*) FROM records WHERE kind='memory'").fetchone()[0]
    assert notes == len(indexed(service)) == 4


# -- backfill --------------------------------------------------------------------------------------------------

def test_opening_a_database_from_before_the_index_backfills_it(tmp_path):
    path = tmp_path / 'old.sqlite3'
    store = WorkspaceStore(path)
    for text in ('legacy one about caching', 'legacy two about queues'):
        store.create('memory', 'owner-1', {'content': text})
    store.create('conversation', 'owner-1', {'engine': 'internal'})  # other kinds are not indexed
    store.db.execute('DROP TABLE memory_fts')  # what a database written before this change looks like
    store.db.commit()
    store.close()
    reopened = WorkspaceStore(path)
    assert reopened.memory_fts
    rows = reopened.db.execute('SELECT content,owner FROM memory_fts ORDER BY content').fetchall()
    assert [(r['content'], r['owner']) for r in rows] == [('legacy one about caching', 'owner-1'), ('legacy two about queues', 'owner-1')]
    assert len(reopened.search_memory('owner-1', 'queue')) == 1
    reopened.close()


def test_a_drifted_index_is_rebuilt_on_open(tmp_path):
    path = tmp_path / 'drift.sqlite3'
    store = WorkspaceStore(path)
    first = store.create('memory', 'o', {'content': 'first note'})
    store.create('memory', 'o', {'content': 'second note'})
    store.db.execute('DELETE FROM memory_fts WHERE memory_id=?', (first['id'],))  # a write a build without FTS5 made
    store.db.commit()
    store.close()
    again = WorkspaceStore(path)
    assert len(again.search_memory('o', 'note')) == 2
    again.close()
    # a second open with nothing to repair changes nothing
    final = WorkspaceStore(path)
    assert final.db.execute('SELECT COUNT(*) FROM memory_fts').fetchone()[0] == 2
    final.close()


def test_raw_legacy_rows_without_any_fts_table_are_indexed(tmp_path):
    path = tmp_path / 'raw.sqlite3'
    db = sqlite3.connect(path)
    db.execute('CREATE TABLE records (kind TEXT NOT NULL, id TEXT NOT NULL, owner TEXT NOT NULL, project TEXT, '
               'revision INTEGER NOT NULL DEFAULT 1, body TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL, PRIMARY KEY(kind,id))')
    db.execute("INSERT INTO records VALUES('memory','m1','o',NULL,1,'{\"content\": \"ancient fact about sharding\"}',1,1)")
    db.commit()
    db.close()
    store = WorkspaceStore(path)
    assert store.search_memory('o', 'sharding') == ['m1']
    store.close()


# -- fallback --------------------------------------------------------------------------------------------------

def test_punctuation_only_queries_fall_back_to_substring(workspace):
    service, owner, path = workspace
    note(service, owner, 'Use C++ for the hot path')
    note(service, owner, 'Use Go for tooling')
    assert fts_match('++') is None and service.store.search_memory(owner.id, '++') is None
    assert read(service, owner, path, '++') == ['Use C++ for the hot path']


def test_provenance_still_matches_through_the_substring_fallback(workspace):
    service, owner, path = workspace
    service.save_memory(owner, content='plain content', provenance='from the quarterly review')
    assert read(service, owner, path, 'quarterly') == ['plain content']


def test_when_fts_is_unavailable_reads_still_work(workspace):
    service, owner, path = workspace
    note(service, owner, 'fallback works for substrings like cach')
    service.store.memory_fts = False
    assert service.store.search_memory(owner.id, 'cach') is None
    assert read(service, owner, path, 'substrings like cach') == ['fallback works for substrings like cach']
    note(service, owner, 'written while disabled')  # writes skip the index without failing
    assert read(service, owner, path, 'while disabled') == ['written while disabled']


def test_a_failing_fts_query_falls_back_instead_of_erroring(workspace, monkeypatch):
    service, owner, path = workspace
    note(service, owner, 'resilient substring note')

    class Broken:
        def __init__(self, db):
            self.db = db

        def execute(self, sql, *args):
            if 'memory_fts' in sql and sql.lstrip().upper().startswith('SELECT MEMORY_ID'):
                raise sqlite3.OperationalError('fts5: syntax error')
            return self.db.execute(sql, *args)

        def __getattr__(self, name):
            return getattr(self.db, name)

    monkeypatch.setattr(service.store, 'db', Broken(service.store.db))
    assert service.store.search_memory(owner.id, 'resilient') is None
    assert read(service, owner, path, 'resilient substring') == ['resilient substring note']


@pytest.mark.parametrize('query', ['foo" OR bar*', 'NEAR(a b)', 'content:secret', 'a AND NOT b', '"unclosed', "it's", '(((', '*', 'a*b'])
def test_query_text_cannot_inject_fts_syntax(workspace, query):
    service, owner, path = workspace
    note(service, owner, 'ordinary note about a and b and secret')
    read(service, owner, path, query)  # must not raise
    match = fts_match(query)
    # only quoted words and the operators the builder itself adds survive
    assert match is None or not set(re.sub(r'"\w+"', '', match)) - set('() *ORAND')
