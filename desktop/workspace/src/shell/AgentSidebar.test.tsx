import {act, cleanup, fireEvent, render, screen, waitFor} from '@testing-library/react';
import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest';
import AgentSidebar, {useSidebarState, type SidebarState} from './AgentSidebar';

const mocks = vi.hoisted(() => ({
  request: vi.fn(),
  report: vi.fn(),
  refresh: vi.fn(async () => {}),
  setProjectId: vi.fn(),
  setArchived: vi.fn(),
  pinProject: vi.fn(),
  data: {} as Record<string, unknown>,
}));

vi.mock('@tanstack/react-virtual', () => ({
  useVirtualizer: ({count, estimateSize, getItemKey}: {count: number; estimateSize: (i: number) => number; getItemKey: (i: number) => string}) => ({
    getTotalSize: () => count * 40,
    getVirtualItems: () =>
      Array.from({length: count}, (_, index) => ({index, key: getItemKey(index), size: estimateSize(index), start: index * 40})),
    measure: () => {},
  }),
}));
vi.mock('../lib/api', () => ({request: mocks.request, json: (method: string, data: unknown) => ({method, body: JSON.stringify(data)})}));
vi.mock('./AuthGate', () => ({useAuth: () => ({auth: {principal: {id: 'owner'}}})}));
vi.mock('./errors', () => ({useReport: () => mocks.report}));
vi.mock('./WorkspaceDataProvider', () => ({useWorkspaceData: () => mocks.data}));

const chat = (id: string, extra: Record<string, unknown> = {}) => ({
  id,
  title: id,
  pinned: false,
  archived: false,
  draft_text: '',
  revision: 1,
  updated_at: 0,
  turns: [],
  ...extra,
});

let sidebar: SidebarState;

function Harness(props: {
  primaryId?: string;
  manager?: string;
  scopeProjectId?: string;
  onOpen?: (id: string) => void;
  onNewChat?: () => void;
  onMain?: () => void;
}) {
  sidebar = useSidebarState({primaryId: props.primaryId ?? 'main', scopeProjectId: props.scopeProjectId ?? ''});
  return (
    <AgentSidebar
      sidebar={sidebar}
      primaryId={props.primaryId ?? 'main'}
      manager={props.manager ?? ''}
      creating={false}
      onNewChat={props.onNewChat ?? (() => {})}
      onOpenMainAgent={props.onMain ?? (() => {})}
      onOpenConversation={props.onOpen ?? (() => {})}
      onFindConversation={() => sidebar.openSearch()}
    />
  );
}

beforeEach(() => {
  localStorage.clear();
  vi.stubGlobal('matchMedia', () => ({matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn()}));
  mocks.data = {
    sessions: [
      chat('main', {updated_at: 100}),
      chat('alpha', {updated_at: 3, draft_text: 'half-written plan', latest_status: 'running'}),
      chat('beta', {updated_at: 2, project_id: 'api', pinned: true}),
    ],
    projects: [{id: 'api', name: 'API server', path: '/api'}],
    session: null,
    selected: 'alpha',
    setProjectId: mocks.setProjectId,
    projectPins: {revision: 0, projects: []},
    pinsReady: true,
    pinBusy: '',
    pinProject: mocks.pinProject,
    archived: false,
    setArchived: mocks.setArchived,
    sessionsState: 'ready',
    refresh: mocks.refresh,
  };
  mocks.request.mockImplementation(async (path: string) => (path.includes('turns=false') ? chat('alpha', {revision: 7}) : {}));
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

describe('AgentSidebar', () => {
  it('lists conversations, pinned first, without the main agent, and marks the open one', () => {
    render(<Harness />);
    const rows = document.querySelectorAll('.session-row');
    expect([...rows].map(row => row.querySelector('.session-row-title')?.textContent)).toEqual(['beta', 'alpha']);
    expect(screen.getByRole('button', {name: /^alpha/})).toHaveAttribute('aria-current', 'page');
    expect(screen.getByRole('button', {name: /^beta/})).not.toHaveAttribute('aria-current');
    expect(screen.getByText('Draft: half-written plan')).toBeInTheDocument();
    expect(document.querySelector('.status-dot.running')).not.toBeNull();
  });

  it('marks the main agent, not a conversation, as current on its page', () => {
    mocks.data.selected = 'main';
    render(<Harness />);
    expect(screen.getByRole('button', {name: 'Main agent'})).toHaveAttribute('aria-current', 'page');
    expect(screen.getByRole('button', {name: 'Main agent'})).toHaveClass('selected');
    cleanup();
    render(<Harness manager="projects" />);
    expect(screen.getByRole('button', {name: 'Main agent'})).not.toHaveAttribute('aria-current');
    expect(screen.getByRole('button', {name: /^alpha/})).not.toHaveAttribute('aria-current');
  });

  it('opens a conversation, the main agent and a new chat', () => {
    const onOpen = vi.fn();
    const onMain = vi.fn();
    const onNewChat = vi.fn();
    render(<Harness onOpen={onOpen} onMain={onMain} onNewChat={onNewChat} />);
    fireEvent.click(screen.getByRole('button', {name: /^beta/}));
    expect(onOpen).toHaveBeenCalledWith('beta');
    fireEvent.click(screen.getByRole('button', {name: 'Main agent'}));
    fireEvent.click(screen.getByRole('button', {name: 'New chat'}));
    expect(onMain).toHaveBeenCalledTimes(1);
    expect(onNewChat).toHaveBeenCalledTimes(1);
  });

  it('pins and archives against the latest revision and then refreshes', async () => {
    render(<Harness />);
    fireEvent.click(screen.getByRole('button', {name: 'Pin alpha'}));
    await waitFor(() => expect(mocks.refresh).toHaveBeenCalledTimes(1));
    const patch = mocks.request.mock.calls.find(([, init]) => init?.method === 'PATCH');
    expect(patch?.[0]).toBe('/api/workspace/sessions/alpha');
    expect(JSON.parse(patch?.[1].body)).toEqual({revision: 7, changes: {pinned: true}});
    fireEvent.click(screen.getByRole('button', {name: 'Archive alpha'}));
    await waitFor(() => expect(mocks.refresh).toHaveBeenCalledTimes(2));
    const patches = mocks.request.mock.calls.filter(([, init]) => init?.method === 'PATCH');
    expect(JSON.parse(patches[patches.length - 1][1].body).changes).toEqual({archived: true});
  });

  it('reports a failed update and does not refresh', async () => {
    mocks.request.mockRejectedValue(new Error('Conflict'));
    render(<Harness />);
    fireEvent.click(screen.getByRole('button', {name: 'Pin alpha'}));
    await waitFor(() => expect(mocks.report).toHaveBeenCalledWith(expect.objectContaining({message: 'Conflict'})));
    expect(mocks.refresh).not.toHaveBeenCalled();
  });

  it('opens a search field with the Find button, filters as you type and clears on Escape', async () => {
    render(<Harness />);
    expect(screen.queryByRole('textbox', {name: 'Search conversations'})).toBeNull();
    fireEvent.click(screen.getByRole('button', {name: 'Find a conversation'}));
    const field = screen.getByRole('textbox', {name: 'Search conversations'});
    await waitFor(() => expect(field).toHaveFocus());
    fireEvent.change(field, {target: {value: 'alp'}});
    expect([...document.querySelectorAll('.session-row-title')].map(node => node.textContent)).toEqual(['alpha']);
    fireEvent.change(field, {target: {value: 'zzz'}});
    expect(screen.getByText('No matching conversations.')).toBeInTheDocument();
    fireEvent.keyDown(field, {key: 'Escape'});
    expect(screen.queryByRole('textbox', {name: 'Search conversations'})).toBeNull();
    expect(document.querySelectorAll('.session-row')).toHaveLength(2);
  });

  it('keeps the search text while the sidebar is not mounted', () => {
    const view = render(<Harness />);
    act(() => sidebar.setQuery('beta'));
    view.rerender(<p>Another workspace</p>);
    expect(sidebar.query).toBe('beta');
  });

  it('says why the list is empty', () => {
    mocks.data.sessions = [];
    mocks.data.sessionsState = 'loading';
    const view = render(<Harness />);
    expect(screen.getByText('Loading conversations…')).toBeInTheDocument();
    mocks.data.sessionsState = 'failed';
    view.rerender(<Harness />);
    expect(screen.getByText(/Conversations unavailable/)).toBeInTheDocument();
    mocks.data.sessionsState = 'ready';
    view.rerender(<Harness />);
    expect(screen.getByText('Your other chats will appear here.')).toBeInTheDocument();
  });

  it('groups by project, collapses a group and remembers it for this principal', () => {
    mocks.data.sessions = [chat('beta', {project_id: 'api'}), chat('gamma', {project_id: 'api'})];
    render(<Harness />);
    act(() => sidebar.setGroupedChats(true));
    expect(screen.getByText('API server')).toBeInTheDocument();
    expect(document.querySelectorAll('.session-row')).toHaveLength(2);
    fireEvent.click(screen.getByRole('button', {name: 'API server'}));
    expect(mocks.setProjectId).toHaveBeenCalledWith('api');
    expect(document.querySelectorAll('.session-row')).toHaveLength(0);
    expect(JSON.parse(localStorage.getItem('termx-collapsed:owner')!)).toEqual(['api']);
    fireEvent.click(screen.getByRole('button', {name: 'API server'}));
    expect(document.querySelectorAll('.session-row')).toHaveLength(2);
    expect(JSON.parse(localStorage.getItem('termx-collapsed:owner')!)).toEqual([]);
  });

  it('restores collapsed groups from storage and ignores malformed storage', () => {
    localStorage.setItem('termx-collapsed:owner', JSON.stringify(['api', 3, null]));
    mocks.data.sessions = [chat('beta', {project_id: 'api'})];
    render(<Harness />);
    expect(sidebar.collapsed).toEqual(['api']);
    cleanup();
    localStorage.setItem('termx-collapsed:owner', '{not json');
    render(<Harness />);
    expect(sidebar.collapsed).toEqual([]);
  });

  it('can show archived conversations from the filter menu', async () => {
    render(<Harness />);
    fireEvent.click(screen.getByRole('button', {name: 'Conversation filters'}));
    fireEvent.click(await screen.findByRole('button', {name: 'Show archived'}));
    expect(mocks.setArchived).toHaveBeenCalledTimes(1);
  });

  it('uses taller rows for a preview and on touch screens', () => {
    vi.stubGlobal('matchMedia', () => ({matches: true, addEventListener: vi.fn(), removeEventListener: vi.fn()}));
    render(<Harness />);
    const heights = [...document.querySelectorAll('.session-list > div')].map(node => (node as HTMLElement).style.height);
    expect(heights).toEqual(['44px', '68px']);
  });
});
