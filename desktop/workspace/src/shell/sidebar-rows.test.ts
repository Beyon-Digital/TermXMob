import {describe, expect, it} from 'vitest';
import type {Session} from '../lib/api';
import {buildSidebarRows, sessionListPreview} from './sidebar-rows';

const chat = (id: string, extra: Partial<Session> = {}): Session =>
  ({
    id,
    title: id,
    engine: 'internal',
    mode: 'agent',
    pinned: false,
    archived: false,
    draft_text: '',
    revision: 1,
    updated_at: 0,
    scroll: 0,
    turns: [],
    ...extra,
  }) as Session;

const projects = [
  {id: 'api', name: 'API server', path: '/api'},
  {id: 'web', name: 'Website', path: '/web'},
];

const base = {
  sessions: [] as Session[],
  projects,
  primaryId: '',
  groupedChats: false,
  query: '',
  sessionFilter: 'all',
  session: null,
  collapsed: [] as string[],
  pinnedProjects: [] as string[],
  scopeProjectId: '',
};

const keys = (rows: ReturnType<typeof buildSidebarRows>) => rows.map(row => row.key);

describe('buildSidebarRows, by recent activity', () => {
  const sessions = [
    chat('old', {updated_at: 1}),
    chat('new', {updated_at: 9}),
    chat('pinned-old', {updated_at: 0, pinned: true}),
    chat('main', {updated_at: 20}),
  ];

  it('puts pinned conversations first, then the most recent, and leaves out the main agent', () => {
    expect(keys(buildSidebarRows({...base, sessions, primaryId: 'main'}))).toEqual([
      'pinned-old',
      'new',
      'old',
    ]);
  });

  it('searches titles and project names, ignoring case', () => {
    const rows = buildSidebarRows({
      ...base,
      sessions: [chat('a', {project_id: 'api', title: 'Fix login'}), chat('b', {title: 'Lunch'})],
      query: 'API SERVER',
    });
    expect(keys(rows)).toEqual(['a']);
    expect(
      keys(buildSidebarRows({...base, sessions: [chat('a', {title: 'Fix login'})], query: 'LOGIN'})),
    ).toEqual(['a']);
  });

  it('filters by running and needs-attention state, preferring the open conversation’s newer status', () => {
    const list = [
      chat('idle'),
      chat('working', {latest_status: 'running'}),
      chat('blocked', {latest_status: 'awaiting_approval'}),
      chat('stale', {latest_status: 'completed'}),
    ];
    expect(keys(buildSidebarRows({...base, sessions: list, sessionFilter: 'running'}))).toEqual(['working']);
    expect(keys(buildSidebarRows({...base, sessions: list, sessionFilter: 'attention'}))).toEqual([
      'blocked',
    ]);
    expect(
      keys(
        buildSidebarRows({
          ...base,
          sessions: list,
          sessionFilter: 'running',
          session: {id: 'stale', latest_status: 'running'},
        }),
      ),
    ).toEqual(
      ['working', 'stale'].sort((a, b) => list.findIndex(c => c.id === a) - list.findIndex(c => c.id === b)),
    );
  });
});

describe('buildSidebarRows, grouped by project', () => {
  const sessions = [
    chat('a1', {project_id: 'api', updated_at: 2}),
    chat('a2', {project_id: 'api', updated_at: 5}),
    chat('w1', {project_id: 'web', updated_at: 4}),
    chat('loose', {updated_at: 1}),
  ];
  const grouped = {...base, sessions, groupedChats: true};

  it('lists each project then its conversations, with unassigned chats under their own heading', () => {
    const rows = buildSidebarRows(grouped);
    expect(rows.filter(row => row.kind === 'project').map(row => (row as {name: string}).name)).toEqual([
      'API server',
      'Website',
      'Unassigned',
    ]);
    expect(keys(rows)).toEqual(['project:api', 'a2', 'a1', 'project:web', 'w1', 'project:', 'loose']);
  });

  it('puts pinned projects first', () => {
    expect(keys(buildSidebarRows({...grouped, pinnedProjects: ['web']})).slice(0, 2)).toEqual([
      'project:web',
      'w1',
    ]);
  });

  it('hides a collapsed group’s conversations unless searching', () => {
    expect(keys(buildSidebarRows({...grouped, collapsed: ['api']}))).toEqual([
      'project:api',
      'project:web',
      'w1',
      'project:',
      'loose',
    ]);
    expect(keys(buildSidebarRows({...grouped, collapsed: ['api'], query: 'a'}))).toContain('a1');
  });

  it('shows the scoped project even when it has no conversations, but only with no filter or search', () => {
    const rows = buildSidebarRows({...grouped, sessions: [chat('loose')], scopeProjectId: 'web'});
    expect(keys(rows)).toContain('project:web');
    expect(
      keys(buildSidebarRows({...grouped, sessions: [chat('loose')], scopeProjectId: 'web', query: 'l'})),
    ).not.toContain('project:web');
    expect(
      keys(
        buildSidebarRows({
          ...grouped,
          sessions: [chat('loose')],
          scopeProjectId: 'web',
          sessionFilter: 'running',
        }),
      ),
    ).not.toContain('project:web');
  });

  it('groups by an explicit group id when the host sends one', () => {
    const rows = buildSidebarRows({
      ...grouped,
      sessions: [chat('g', {project_id: 'api', group_id: 'web'} as Partial<Session>)],
    });
    expect(keys(rows)).toEqual(['project:web', 'g', 'project:api']);
  });

  it('searches titles only when grouped', () => {
    expect(keys(buildSidebarRows({...grouped, query: 'website'}))).toEqual([]);
    expect(keys(buildSidebarRows({...grouped, query: 'w1'}))).toEqual(['project:web', 'w1']);
  });
});

describe('sessionListPreview', () => {
  it('prefers an unsent draft', () => {
    expect(sessionListPreview({draft_text: '  fix   the\nbuild ', turns: []})).toBe('Draft: fix the build');
  });

  it('shows the latest turn’s result, stripped of markdown, and never more than 200 characters', () => {
    const turns = [
      {sequence: 1, prompt: 'first'},
      {
        sequence: 2,
        prompt: 'second',
        task: {result: '## Done\n- **bold** item\n```ts\ncode\n```\nand `inline` text'},
      },
    ] as unknown as Session['turns'];
    expect(sessionListPreview({draft_text: '', turns})).toBe('Done **bold** item code and inline text');
    const long = [{sequence: 1, prompt: 'x'.repeat(500)}] as unknown as Session['turns'];
    expect(sessionListPreview({draft_text: '', turns: long})).toHaveLength(200);
  });

  it('falls back to the prompt, then to nothing', () => {
    expect(sessionListPreview({draft_text: '', turns: [{sequence: 1, prompt: 'hello'}] as never})).toBe(
      'hello',
    );
    expect(sessionListPreview({draft_text: '', turns: []})).toBe('');
  });
});
