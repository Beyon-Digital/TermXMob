"""Saved sites: tables, frecency, isolation, profile cascade, app-mode reopen, suggestions and notes.

No Chromium: pages are fakes, which is all `BrowserService` needs to record navigations and open tabs.
"""
import asyncio
import sqlite3
import pytest
from termx.browser import sites as sites_module
from termx.browser.service import BrowserService
from termx.browser.storage import Records

DAY = 86400.0
T0 = 1_800_000_000.0


class FakePage:
    def __init__(self, url, title='Example'):
        self.url = url; self.page_title = title; self.closed = False
    def is_closed(self): return self.closed
    async def close(self): self.closed = True
    async def title(self): return self.page_title


def make(tmp_path, **kwargs):
    service = BrowserService(tmp_path, **kwargs)
    service.profile = service.create_profile('owner', 'project', 'Work')
    return service


async def navigate(service, tab, url, title='Example'):
    page = service._pages[tab['id']]; page.url = url; page.page_title = title
    await service._navigation(tab['id'])


def fake_tabs(service):
    """Replace Chromium tab creation with a record plus a fake page; returns the list of created ids."""
    created = []
    async def create_tab(principal, session, profile_id, url='about:blank', *, scheduled_scope=None):
        profile = service.records.get('profile', profile_id)
        if not profile or profile['principal_id'] != principal: raise KeyError('profile not found')
        tab = service._new_tab_record(profile, session)
        service._pages[tab['id']] = FakePage(url)
        created.append(tab['id'])
        tab['url'] = url; service.records.put('tab', tab['id'], tab)
        return service.get(tab['id'], principal)
    service.create_tab = create_tab
    return created


def visit(service, url, *, now, principal='owner', profile=None, title='Page'):
    profile = profile or service.profile
    return service.touch_site({'principal_id': principal, 'profile_id': profile['id'], 'project_id': 'project', 'url': url}, title, now=now)


def test_schema_has_real_tables_and_indexes_and_reopening_is_idempotent(tmp_path):
    Records(tmp_path); records = Records(tmp_path)
    db = sqlite3.connect(records.path)
    try:
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        indexes = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='index'")}
        columns = {r[1] for r in db.execute('PRAGMA table_info(sites)')}
        notes = {r[1] for r in db.execute('PRAGMA table_info(site_notes)')}
    finally:
        db.close()
    assert {'records', 'events', 'sites', 'site_notes', 'site_visits'} <= tables
    assert {'sites_order', 'site_notes_site', 'site_visits_rank'} <= indexes
    assert {'id', 'principal_id', 'project_id', 'origin', 'start_url', 'title', 'profile_id', 'open_mode', 'pinned', 'position',
            'visit_count', 'last_visited', 'frecency', 'tab_id', 'created', 'updated'} <= columns
    assert {'id', 'site_id', 'author', 'content', 'created', 'updated'} == notes
    db = sqlite3.connect(records.path)
    try:
        with pytest.raises(sqlite3.IntegrityError):  # open_mode and author are constrained in the schema itself
            db.execute("INSERT INTO sites(id,principal_id,origin,start_url,profile_id,open_mode,created,updated) VALUES('x','p','o','u','f','window',0,0)")
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("INSERT INTO site_notes(id,site_id,author,content,created,updated) VALUES('n','x','robot','c',0,0)")
    finally:
        db.close()


def test_save_normalises_the_origin_drops_queries_and_is_unique_per_user_profile_and_origin(tmp_path):
    service = make(tmp_path)
    first = service.save_site('owner', service.profile['id'], 'https://Example.com:443/app/home?token=SECRET#frag', title='  Example   app ')
    assert first['origin'] == 'https://example.com' and first['start_url'] == 'https://example.com/app/home'
    assert 'SECRET' not in str(first) and first['title'] == 'Example app' and first['pinned'] is True and first['open_mode'] == 'tab'
    again = service.save_site('owner', service.profile['id'], 'https://example.com/other', open_mode='app')
    assert again['id'] == first['id'] and again['open_mode'] == 'app' and again['title'] == 'Example app'
    assert len(service.sites('owner')) == 1
    other_profile = service.create_profile('owner', 'project', 'Other')
    assert service.save_site('owner', other_profile['id'], 'https://example.com')['id'] != first['id']
    for bad in ('file:///etc/passwd', 'ftp://example.com', 'https://user:pw@example.com', 'javascript:alert(1)', '', 'https://' + 'a' * 600 + '.com'):
        with pytest.raises(ValueError):
            service.save_site('owner', service.profile['id'], bad)
    with pytest.raises(ValueError):
        service.save_site('owner', service.profile['id'], 'https://example.com', open_mode='window')
    with pytest.raises(ValueError):
        service.save_site('owner', service.profile['id'], 'https://example.com', title='t' * 201)
    with pytest.raises(KeyError):
        service.save_site('owner', 'missing-profile', 'https://example.com')


def test_sites_cannot_be_saved_on_temporary_profiles_and_their_visits_leave_no_trace(tmp_path):
    service = make(tmp_path)
    temporary = service.create_profile('owner', 'project', 'Throwaway', ephemeral=True)
    with pytest.raises(ValueError):
        service.save_site('owner', temporary['id'], 'https://example.com')
    for step in range(4):
        assert visit(service, 'https://example.com/', now=T0 + step * DAY, profile=temporary) is None
    assert service.suggestions('owner') == []


def test_ordering_is_pinned_by_position_then_decayed_frecency(tmp_path):
    service = make(tmp_path)
    profile = service.profile['id']
    pin_b = service.save_site('owner', profile, 'https://b.example')
    pin_a = service.save_site('owner', profile, 'https://a.example')
    old = service.save_site('owner', profile, 'https://old.example', pinned=False)
    fresh = service.save_site('owner', profile, 'https://fresh.example', pinned=False)
    never = service.save_site('owner', profile, 'https://never.example', pinned=False)
    now = T0 + 80 * DAY
    for step in range(10):  # ten visits long ago ...
        visit(service, 'https://old.example/', now=T0 + step * 600)
    for step in range(3):  # ... lose to three visits today (14 day half-life)
        visit(service, 'https://fresh.example/', now=now - (2 - step) * 3600)
    order = [row['origin'] for row in service.site_store.list('owner', now=now)]
    assert order == ['https://b.example', 'https://a.example', 'https://fresh.example', 'https://old.example', 'https://never.example']
    listed = {row['id']: row for row in service.site_store.list('owner', now=now)}
    assert listed[fresh['id']]['frecency'] > listed[old['id']]['frecency'] > listed[never['id']]['frecency'] == 0.0
    assert listed[fresh['id']]['visit_count'] == 3 and listed[old['id']]['visit_count'] == 10
    # Position decides among pinned sites, and re-pinning appends rather than reshuffling.
    service.update_site(pin_a['id'], 'owner', position=0)
    service.update_site(pin_b['id'], 'owner', position=1)
    assert [row['origin'] for row in service.sites('owner', pinned=True)] == ['https://a.example', 'https://b.example']
    service.pin_site(pin_a['id'], 'owner', False)
    service.pin_site(pin_a['id'], 'owner', True)
    assert [row['origin'] for row in service.sites('owner', pinned=True)][-1] == 'https://a.example'


def test_decay_is_a_14_day_half_life(tmp_path):
    assert sites_module.current_frecency(sites_module.rank_of(8.0, T0), T0 + 14 * DAY) == pytest.approx(4.0)
    assert sites_module.current_frecency(sites_module.rank_of(8.0, T0), T0) == pytest.approx(8.0)


def test_one_browsing_session_counts_as_one_visit(tmp_path):
    service = make(tmp_path)
    site = service.save_site('owner', service.profile['id'], 'https://example.com')
    assert visit(service, 'https://example.com/a', now=T0)['counted'] is True
    assert visit(service, 'https://example.com/b?x=1', now=T0 + 60)['counted'] is False
    assert visit(service, 'https://example.com/c', now=T0 + sites_module.VISIT_GAP + 61)['counted'] is True
    assert service.site(site['id'], 'owner')['visit_count'] == 2


def test_navigation_records_only_non_private_http_pages_and_never_query_strings(tmp_path):
    async def run():
        service = make(tmp_path)
        events = []
        service.on_visit = events.append
        saved = service.save_site('owner', service.profile['id'], 'https://example.com')
        tab = service._new_tab_record(service.profile, 'session')
        service._pages[tab['id']] = FakePage('about:blank')
        await service._navigation(tab['id'])
        assert service.site(saved['id'], 'owner')['visit_count'] == 0 and events == []
        await navigate(service, tab, 'https://example.com/inbox?session=SECRET', 'Inbox')
        assert service.site(saved['id'], 'owner')['visit_count'] == 1
        assert events[0]['origin'] == 'https://example.com' and events[0]['saved'] is True and events[0]['site_id'] == saved['id']
        assert events[0]['url'] == 'https://example.com/inbox' and 'SECRET' not in str(events)
        assert events[0]['principal_id'] == 'owner' and events[0]['visit_count'] == 1 and isinstance(events[0]['at'], float)
        private = service._new_tab_record(service.profile, 'session'); private['state'] = 'private'
        service.records.put('tab', private['id'], private); service._pages[private['id']] = FakePage('https://example.com/')
        await service._navigation(private['id'])
        assert service.site(saved['id'], 'owner')['visit_count'] == 1
        db = sqlite3.connect(service.records.path)
        try:
            dump = str([row for table in ('sites', 'site_notes', 'site_visits') for row in db.execute(f'SELECT * FROM {table}')])
        finally:
            db.close()
        assert 'SECRET' not in dump  # the generic tab record keeps its own URL; the site tables never see a query
    asyncio.run(run())


def test_on_visit_hook_is_optional_and_its_failures_never_break_navigation(tmp_path):
    service = make(tmp_path)
    saved = service.save_site('owner', service.profile['id'], 'https://example.com')
    assert visit(service, 'https://example.com/', now=T0)['counted'] is True  # no hook installed

    def boom(event): raise RuntimeError('usage recorder down')
    service.on_visit = boom
    assert visit(service, 'https://example.com/', now=T0 + DAY)['counted'] is True
    assert service.site(saved['id'], 'owner')['visit_count'] == 2


def test_principal_isolation_for_every_site_operation(tmp_path):
    service = make(tmp_path)
    mine = service.save_site('owner', service.profile['id'], 'https://example.com')
    service.write_site_note(mine['id'], 'owner', 'my note')
    theirs_profile = service.create_profile('guest', 'project', 'Guest')
    assert service.sites('guest') == []
    for call in (lambda: service.site(mine['id'], 'guest'), lambda: service.pin_site(mine['id'], 'guest', False),
                 lambda: service.update_site(mine['id'], 'guest', title='x'), lambda: service.remove_site(mine['id'], 'guest'),
                 lambda: service.site_notes(mine['id'], 'guest'), lambda: service.write_site_note(mine['id'], 'guest', 'x'),
                 lambda: service.delete_site_note(mine['id'], 'guest', 'any')):
        with pytest.raises(KeyError):
            call()
    with pytest.raises(KeyError):
        service.save_site('guest', service.profile['id'], 'https://example.com')  # someone else's profile
    with pytest.raises(KeyError):
        asyncio.run(service.open_site(mine['id'], 'guest', 'session'))
    # The same origin is a separate saved site for each user, with its own counters and suggestions.
    other = service.save_site('guest', theirs_profile['id'], 'https://example.com')
    assert other['id'] != mine['id']
    for step in range(3):
        visit(service, 'https://seen.example/', now=T0 + step * DAY, principal='guest', profile=theirs_profile)
    assert [row['origin'] for row in service.suggestions('guest')] == ['https://seen.example'] and service.suggestions('owner') == []
    assert service.site(mine['id'], 'owner')['note_count'] == 1 and service.site(other['id'], 'guest')['note_count'] == 0


def test_clearing_a_profile_forgets_usage_and_removing_it_deletes_sites_and_notes(tmp_path):
    async def run():
        service = make(tmp_path)
        profile = service.profile; keep = service.create_profile('owner', 'project', 'Keep')
        site = service.save_site('owner', profile['id'], 'https://example.com', open_mode='app')
        kept = service.save_site('owner', keep['id'], 'https://example.com')
        service.write_site_note(site['id'], 'owner', 'login via SSO')
        service.write_site_note(kept['id'], 'owner', 'untouched')
        for step in range(3):
            visit(service, 'https://example.com/', now=T0 + step * DAY)
            visit(service, 'https://seen.example/', now=T0 + step * DAY)
            visit(service, 'https://example.com/', now=T0 + step * DAY, profile=keep)
        assert service.suggestions('owner')
        service.site_store.set_tab(site['id'], 'owner', 'tab-1')
        await service.clear_profile(profile['id'], 'owner')
        cleared = service.site(site['id'], 'owner')
        assert cleared['visit_count'] == 0 and cleared['last_visited'] is None and cleared['frecency'] == 0 and cleared['tab_id'] is None
        assert cleared['note_count'] == 1 and cleared['pinned'] is True  # what the user saved is not browsing data
        assert service.suggestions('owner') == []
        assert service.site(kept['id'], 'owner')['visit_count'] == 3  # other profiles are untouched
        await service.clear_profile(profile['id'], 'owner', remove=True)
        assert [row['id'] for row in service.sites('owner')] == [kept['id']]
        db = sqlite3.connect(service.records.path)
        try:
            assert [r[0] for r in db.execute('SELECT content FROM site_notes')] == ['untouched']
        finally:
            db.close()
    asyncio.run(run())


def test_temporary_profile_cleanup_after_an_abrupt_stop_also_drops_site_rows(tmp_path):
    service = make(tmp_path)
    temporary = service.create_profile('owner', 'project', 'Throwaway', ephemeral=True)
    service.site_store.save('owner', temporary['id'], 'project', 'https://example.com')  # as if saved before it became temporary
    site = service.sites('owner')[0]
    service.write_site_note(site['id'], 'owner', 'note')
    restarted = BrowserService(tmp_path)
    assert restarted.sites('owner') == []


def test_app_mode_tab_persists_across_restart_and_reopens_lazily_at_its_last_page(tmp_path):
    async def run():
        service = make(tmp_path); created = fake_tabs(service)
        site = service.save_site('owner', service.profile['id'], 'https://mail.example/inbox', open_mode='app')
        opened = await service.open_site(site['id'], 'owner', 'session')
        assert opened['reused'] is False and opened['tab']['url'] == 'https://mail.example/inbox' and opened['tab']['state'] == 'human'
        assert opened['site']['tab_id'] == opened['tab']['id'] and len(created) == 1
        tab = opened['tab']
        await navigate(service, tab, 'https://mail.example/thread/42?auth=SECRET')
        again = await service.open_site(site['id'], 'owner', 'session')
        assert again['reused'] is True and again['tab']['id'] == tab['id'] and len(created) == 1  # live tab is reused
        # Host restart: Chromium is gone, the tab record stays and is closed; nothing is launched until the user opens it.
        restarted = BrowserService(tmp_path); created = fake_tabs(restarted)
        assert created == [] and restarted.get(tab['id'], 'owner')['state'] == 'closed'
        assert restarted.site(site['id'], 'owner')['tab_id'] == tab['id']
        reopened = await restarted.open_site(site['id'], 'owner', 'session2')
        assert len(created) == 1 and reopened['reused'] is False and reopened['tab']['id'] != tab['id']
        assert reopened['tab']['url'] == 'https://mail.example/thread/42?auth=SECRET'
        assert restarted.get(tab['id'], 'owner')['recovered_tab_id'] == reopened['tab']['id']
        assert restarted.site(site['id'], 'owner')['tab_id'] == reopened['tab']['id']
        assert (await restarted.open_site(site['id'], 'owner', 'session2'))['reused'] is True and len(created) == 1
    asyncio.run(run())


def test_tab_mode_site_starts_fresh_and_an_app_tab_that_left_its_origin_restarts_at_the_saved_url(tmp_path):
    async def run():
        service = make(tmp_path); created = fake_tabs(service)
        plain = service.save_site('owner', service.profile['id'], 'https://docs.example/start')
        first = await service.open_site(plain['id'], 'owner', 'session')
        await navigate(service, first['tab'], 'https://docs.example/deep/page')
        await service.close_tab(first['tab']['id'], 'owner')
        second = await service.open_site(plain['id'], 'owner', 'session')
        assert second['tab']['url'] == 'https://docs.example/start' and second['tab']['id'] != first['tab']['id']
        app = service.save_site('owner', service.profile['id'], 'https://app.example/', open_mode='app')
        opened = await service.open_site(app['id'], 'owner', 'session')
        await navigate(service, opened['tab'], 'https://elsewhere.example/')
        await service.close_tab(opened['tab']['id'], 'owner')
        reopened = await service.open_site(app['id'], 'owner', 'session')
        assert reopened['tab']['url'] == 'https://app.example/' and reopened['tab']['id'] != opened['tab']['id']
        # A site whose tab record vanished (temporary cleanup) simply opens a new tab.
        service.records.delete('tab', reopened['tab']['id'])
        assert (await service.open_site(app['id'], 'owner', 'session'))['reused'] is False
    asyncio.run(run())


def test_open_site_claims_new_tabs_only_when_asked_and_concurrent_opens_share_one_tab(tmp_path):
    async def run():
        service = make(tmp_path); created = fake_tabs(service); claimed = []
        service.claim_tab = lambda principal, tab: claimed.append((principal, tab['id']))
        site = service.save_site('owner', service.profile['id'], 'https://example.com/')
        first, second = await asyncio.gather(service.open_site(site['id'], 'owner', 's', claim=True), service.open_site(site['id'], 'owner', 's', claim=True))
        assert first['tab']['id'] == second['tab']['id'] and len(created) == 1 and sorted([first['reused'], second['reused']]) == [False, True]
        assert claimed and claimed[0] == ('owner', first['tab']['id'])
    asyncio.run(run())


def test_suggestions_need_repeat_visits_exclude_saved_sites_and_restart_after_removal(tmp_path):
    service = make(tmp_path)
    for step in range(2):
        visit(service, 'https://rare.example/', now=T0 + step * DAY)
    for step in range(5):
        visit(service, 'https://often.example/x?q=secret', now=T0 + step * DAY, title='Often')
    for step in range(3):
        visit(service, 'https://saved.example/', now=T0 + step * DAY)
    assert [row['origin'] for row in service.suggestions('owner')] == ['https://often.example', 'https://saved.example']
    saved = service.save_site('owner', service.profile['id'], 'https://saved.example')
    assert saved['visit_count'] == 3 and saved['title'] == 'Page'  # the counter and page title carry over
    suggestions = service.suggestions('owner')
    assert [row['origin'] for row in suggestions] == ['https://often.example']
    assert suggestions[0]['visit_count'] == 5 and suggestions[0]['title'] == 'Often' and 'secret' not in str(suggestions)
    assert set(suggestions[0]) == {'profile_id', 'origin', 'start_url', 'title', 'project_id', 'visit_count', 'last_visited', 'frecency'}
    service.remove_site(saved['id'], 'owner')
    assert 'https://saved.example' not in [row['origin'] for row in service.suggestions('owner')]  # no instant re-suggestion
    for step in range(3):
        visit(service, 'https://saved.example/', now=T0 + 10 * DAY + step * DAY)
    assert 'https://saved.example' in [row['origin'] for row in service.suggestions('owner')]


def test_unsaved_visit_counters_are_bounded(tmp_path, monkeypatch):
    monkeypatch.setattr(sites_module, 'MAX_CANDIDATES', 5)
    service = make(tmp_path)
    for index in range(12):
        visit(service, f'https://site{index}.example/', now=T0 + index * DAY)
    db = sqlite3.connect(service.records.path)
    try:
        kept = [r[0] for r in db.execute('SELECT origin FROM site_visits ORDER BY rank DESC')]
    finally:
        db.close()
    assert kept == [f'https://site{index}.example' for index in (11, 10, 9, 8, 7)]


def test_notes_crud_authorship_sanitising_and_size_limits(tmp_path):
    service = make(tmp_path)
    site = service.save_site('owner', service.profile['id'], 'https://example.com')
    user = service.write_site_note(site['id'], 'owner', '  Billing lives under Settings.\n\nUse the SSO button.  ')
    agent = service.write_site_note(site['id'], 'owner', 'Agent‮ found​ a shortcut\x00\x1b[31m', author='agent')
    assert user['author'] == 'user' and user['content'] == 'Billing lives under Settings.\n\nUse the SSO button.'
    assert agent['author'] == 'agent' and agent['content'] == 'Agent found a shortcut[31m'
    notes = service.site_notes(site['id'], 'owner')
    assert [n['id'] for n in notes] == [user['id'], agent['id']] and service.site(site['id'], 'owner')['note_count'] == 2
    assert service.delete_site_note(site['id'], 'owner', user['id']) == {'id': user['id']}
    with pytest.raises(KeyError):
        service.delete_site_note(site['id'], 'owner', user['id'])
    other = service.save_site('owner', service.profile['id'], 'https://other.example')
    with pytest.raises(KeyError):  # a note id from another site never deletes across sites
        service.delete_site_note(other['id'], 'owner', agent['id'])
    for bad in ('', '   ', '​', 'x' * (sites_module.NOTE_MAX + 1)):
        with pytest.raises(ValueError):
            service.write_site_note(site['id'], 'owner', bad)
    assert len(service.write_site_note(site['id'], 'owner', 'x' * sites_module.NOTE_MAX)['content']) == sites_module.NOTE_MAX
    with pytest.raises(ValueError):
        service.site_store.add_note(site['id'], 'owner', 'robot', 'content')
    for index in range(sites_module.MAX_NOTES - 2):
        service.write_site_note(site['id'], 'owner', f'note {index}')
    with pytest.raises(ValueError):
        service.write_site_note(site['id'], 'owner', 'one too many')
    service.remove_site(site['id'], 'owner')
    db = sqlite3.connect(service.records.path)
    try:
        assert db.execute('SELECT COUNT(*) FROM site_notes WHERE site_id=?', (site['id'],)).fetchone()[0] == 0
    finally:
        db.close()


def test_saved_site_count_is_bounded_and_edits_are_validated(tmp_path, monkeypatch):
    monkeypatch.setattr(sites_module, 'MAX_SITES', 3)
    service = make(tmp_path)
    rows = [service.save_site('owner', service.profile['id'], f'https://s{index}.example') for index in range(3)]
    with pytest.raises(ValueError):
        service.save_site('owner', service.profile['id'], 'https://s4.example')
    service.save_site('owner', service.profile['id'], 'https://s0.example', title='Renamed')  # an update is not growth
    with pytest.raises(ValueError):
        service.update_site(rows[0]['id'], 'owner', start_url='https://elsewhere.example/')
    assert service.update_site(rows[0]['id'], 'owner', start_url='https://s0.example/new?q=1')['start_url'] == 'https://s0.example/new'
    for bad in ({'title': ''}, {'open_mode': 'window'}, {'position': -1}, {'position': True}, {'colour': 'red'}):
        with pytest.raises(ValueError):
            service.update_site(rows[0]['id'], 'owner', **bad)


def test_diff_events_distinguish_upserts_visits_and_removals():
    base = {'id': 'a', 'title': 'A', 'pinned': True, 'visit_count': 0, 'last_visited': None, 'frecency': 0.0, 'note_count': 0}
    assert sites_module.diff_sites({}, {'a': base}) == [{'type': 'browser.site.upserted', 'id': 'a', 'site': base}]
    visited = {**base, 'visit_count': 1, 'last_visited': 5.0, 'frecency': 1.0}
    assert sites_module.diff_sites({'a': base}, {'a': visited}) == [{'type': 'browser.site.visited', 'id': 'a', 'site': visited}]
    assert sites_module.diff_sites({'a': visited}, {'a': {**visited, 'frecency': 0.5}}) == []  # decay alone is not an event
    renamed = {**visited, 'title': 'B', 'visit_count': 2}
    assert sites_module.diff_sites({'a': visited}, {'a': renamed})[0]['type'] == 'browser.site.upserted'
    assert sites_module.diff_sites({'a': visited}, {}) == [{'type': 'browser.site.removed', 'id': 'a'}]
