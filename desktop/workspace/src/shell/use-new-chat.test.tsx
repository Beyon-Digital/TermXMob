import {act, cleanup, renderHook, waitFor} from '@testing-library/react';
import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest';
import {SETTINGS_LEAVE} from '../features/settings-forms';
import {useNewChat} from './use-new-chat';

const mocks = vi.hoisted(() => ({
  request: vi.fn(),
  gql: vi.fn(),
  report: vi.fn(),
  load: vi.fn(async () => {}),
  setSession: vi.fn(),
  setSelected: vi.fn(),
  setProjectId: vi.fn(),
  pending: {current: (() => true) as () => boolean},
  data: {} as Record<string, unknown>,
}));

vi.mock('../lib/api', () => ({
  request: mocks.request,
  gql: mocks.gql,
  json: (method: string, data: unknown) => ({method, body: JSON.stringify(data)}),
}));
vi.mock('./AuthGate', () => ({
  useAuth: () => ({auth: {principal: {id: 'owner'}}, fence: () => mocks.pending}),
}));
vi.mock('./errors', () => ({useReport: () => mocks.report}));
vi.mock('./WorkspaceDataProvider', () => ({useWorkspaceData: () => mocks.data}));

const chat = (id = 'new-chat', project_id: string | null = null) => ({id, title: id, project_id});

function setup(overrides: Record<string, unknown> = {}) {
  const navigationIntent = {current: 0};
  const options = {
    navigationIntent,
    workspace: 'agent' as const,
    manager: '',
    workspaceNow: {current: 'agent' as const},
    managerNow: {current: ''},
    setWorkspaceState: vi.fn(),
    setProjectWorkbenchId: vi.fn(),
    setManager: vi.fn(),
    chooseLayout: vi.fn(),
    setLeft: vi.fn(),
    ...overrides,
  };
  const hook = renderHook(() => useNewChat(options));
  return {...hook, options, navigationIntent};
}

beforeEach(() => {
  mocks.pending.current = () => true;
  mocks.data = {
    projects: [{id: 'p1', name: 'One', path: '/one'}],
    providers: [
      {id: 'unconfigured', secret_configured: false},
      {id: 'configured', secret_configured: true},
    ],
    projectId: '',
    setProjectId: mocks.setProjectId,
    selected: '',
    setSelected: mocks.setSelected,
    selection: {current: ''},
    session: null,
    setSession: mocks.setSession,
    load: mocks.load,
  };
  mocks.request.mockImplementation(async (path: string) => {
    if (path.startsWith('/api/workspace/presets')) return {presets: []};
    return chat();
  });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const created = () => JSON.parse(mocks.request.mock.calls.find(([path]) => path === '/api/workspace/sessions')![1].body);

describe('one-click new chat', () => {
  it('creates an agent conversation with the first configured provider and shows it', async () => {
    const {result, options, navigationIntent} = setup();
    await act(async () => {
      result.current.openNewChat();
    });
    await waitFor(() => expect(mocks.load).toHaveBeenCalledTimes(1));
    expect(created()).toEqual({
      engine: 'internal',
      provider_id: 'configured',
      model: null,
      mode: 'agent',
      custom_agent_id: null,
    });
    expect(options.setWorkspaceState).toHaveBeenCalledWith('agent');
    expect(options.setProjectWorkbenchId).toHaveBeenCalledWith('');
    expect(mocks.setSession).toHaveBeenCalledWith(chat());
    expect(mocks.setSelected).toHaveBeenCalledWith('new-chat');
    expect(mocks.setProjectId).toHaveBeenCalledWith('');
    expect(options.setManager).toHaveBeenCalledWith('');
    expect(options.chooseLayout).toHaveBeenCalledWith('chat');
    expect(result.current.newComposerFocus.current).toEqual({id: 'new-chat', intent: navigationIntent.current});
    expect(result.current.registering).toBe(false);
  });

  it('creates inside a project and takes its path', async () => {
    const {result} = setup();
    await act(async () => {
      result.current.openNewChat('p1');
    });
    await waitFor(() => expect(mocks.load).toHaveBeenCalled());
    expect(created()).toMatchObject({project_id: 'p1', cwd: '/one'});
  });

  it('ignores a second request while one is in flight', async () => {
    let finish!: (value: unknown) => void;
    mocks.request.mockImplementation(() => new Promise(resolve => (finish = resolve)));
    const {result} = setup();
    act(() => result.current.openNewChat());
    act(() => result.current.openNewChat());
    expect(mocks.request).toHaveBeenCalledTimes(1);
    await act(async () => finish(chat()));
    await waitFor(() => expect(result.current.registering).toBe(false));
  });

  it('only reloads when the person navigated elsewhere before it finished', async () => {
    const {result, options, navigationIntent} = setup();
    mocks.request.mockImplementation(async () => {
      navigationIntent.current += 1;
      return chat();
    });
    await act(async () => {
      result.current.openNewChat();
    });
    await waitFor(() => expect(mocks.load).toHaveBeenCalledTimes(1));
    expect(mocks.setSelected).not.toHaveBeenCalled();
    expect(options.chooseLayout).not.toHaveBeenCalled();
    expect(result.current.newComposerFocus.current).toBeNull();
  });

  it('reports a failure unless the session changed meanwhile, and can try again', async () => {
    mocks.request.mockRejectedValueOnce(new Error('No capacity'));
    const {result} = setup();
    await act(async () => {
      result.current.openNewChat();
    });
    expect(mocks.report).toHaveBeenCalledWith(expect.objectContaining({message: 'No capacity'}));
    expect(result.current.registering).toBe(false);
    mocks.request.mockRejectedValue(new Error('Stale'));
    mocks.pending.current = () => false;
    await act(async () => {
      result.current.openNewChat();
    });
    expect(mocks.report).toHaveBeenCalledTimes(1);
  });

  it('collapses the sidebar on a narrow window', async () => {
    vi.stubGlobal('innerWidth', 600);
    try {
      const {result, options} = setup();
      await act(async () => {
        result.current.openNewChat();
      });
      await waitFor(() => expect(options.setLeft).toHaveBeenCalledWith(false));
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it('waits for unsaved settings to let go before leaving them', async () => {
    let proceed: (() => void) | undefined;
    const veto = (event: Event) => {
      event.preventDefault();
      proceed = (event as CustomEvent<{proceed: () => void}>).detail.proceed;
    };
    window.addEventListener(SETTINGS_LEAVE, veto);
    try {
      const {result} = setup({manager: 'models'});
      act(() => result.current.openNewChat());
      expect(mocks.request).not.toHaveBeenCalled();
      await act(async () => proceed!());
      await waitFor(() => expect(mocks.load).toHaveBeenCalled());
      expect(mocks.request).toHaveBeenCalled();
    } finally {
      window.removeEventListener(SETTINGS_LEAVE, veto);
    }
  });
});

describe('new chat with options', () => {
  it('opens a clean dialog for a project and loads that project’s presets', async () => {
    mocks.request.mockImplementation(async (path: string) =>
      path.startsWith('/api/workspace/presets')
        ? {presets: [{id: 'preset', name: 'Reviewer', engine: 'inherit', model: 'm1', workspace_revision: 'r'}]}
        : chat('from-preset', 'p1'),
    );
    const {result, navigationIntent} = setup();
    act(() => result.current.openNewChatWithOptions('p1'));
    expect(result.current.newOpen).toBe(true);
    expect(mocks.setProjectId).toHaveBeenCalledWith('p1');
    expect(navigationIntent.current).toBe(1);
    expect(result.current.folder).toBe('');
    expect(result.current.newPreset).toBe('');
    mocks.data.projectId = 'p1';
  });

  it('fetches the presets of the chosen project while open and maps an inherited engine to the internal one', async () => {
    mocks.data.projectId = 'p 1';
    mocks.request.mockImplementation(async (path: string) =>
      path.startsWith('/api/workspace/presets')
        ? {presets: [{id: 'preset', name: 'Reviewer', engine: 'inherit', model: 'm1', provider_id: 'pp', workspace_revision: 'r'}]}
        : chat('from-preset'),
    );
    const {result} = setup();
    act(() => result.current.setNewOpen(true));
    await waitFor(() => expect(result.current.newPresets).toHaveLength(1));
    expect(mocks.request).toHaveBeenCalledWith('/api/workspace/presets?project_id=p%201');
    expect(result.current.newPresetsLoading).toBe(false);
    act(() => result.current.setNewPreset('preset'));
    await act(async () => {
      await result.current.createConversation();
    });
    expect(created()).toMatchObject({engine: 'internal', provider_id: 'pp', model: 'm1', custom_agent_id: 'preset'});
    expect(result.current.newOpen).toBe(false);
    expect(result.current.newPreset).toBe('');
  });

  it('registers a typed project folder first and creates the chat inside it', async () => {
    mocks.gql.mockResolvedValue({register_project: {id: 'folder-project', name: 'folder', path: '/work/folder'}});
    const {result} = setup();
    act(() => result.current.setFolder('/work/folder'));
    await act(async () => {
      await result.current.createConversation();
    });
    expect(mocks.gql).toHaveBeenCalledWith(expect.stringContaining('register_project'), {
      input: {path: '/work/folder', name: ''},
    });
    expect(created()).toMatchObject({project_id: 'folder-project', cwd: '/work/folder'});
    expect(result.current.folder).toBe('');
  });

  it('focuses the new conversation’s composer once it renders', async () => {
    const host = document.createElement('div');
    host.id = 'agent-workspace';
    document.body.append(host);
    try {
      const {result, rerender} = setup();
      await act(async () => {
        result.current.openNewChat();
      });
      await waitFor(() => expect(result.current.newComposerFocus.current).not.toBeNull());
      // The shell now shows the new conversation; the composer is not rendered yet.
      mocks.data.selected = 'new-chat';
      mocks.data.session = chat();
      mocks.data.selection = {current: 'new-chat'};
      rerender();
      const field = document.createElement('textarea');
      field.name = 'message';
      host.append(field);
      await waitFor(() => expect(document.activeElement).toBe(field));
      expect(result.current.newComposerFocus.current).toBeNull();
    } finally {
      host.remove();
    }
  });
});
