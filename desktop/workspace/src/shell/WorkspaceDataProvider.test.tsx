import {act, cleanup, render, screen, waitFor} from '@testing-library/react';
import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest';
import AuthGate from './AuthGate';
import {ErrorProvider, useErrorMessage} from './errors';
import WorkspaceDataProvider, {useWorkspaceData, type WorkspaceData} from './WorkspaceDataProvider';
import type {WorkspaceMode} from '../lib/workspace-mode';

const mocks = vi.hoisted(() => ({
  request: vi.fn(),
  gql: vi.fn(),
  listSessions: vi.fn(),
  read: vi.fn(),
  loadPins: vi.fn(),
  changePin: vi.fn(),
}));

vi.mock('../lib/api', () => ({
  request: mocks.request,
  gql: mocks.gql,
  json: (method: string, data: unknown) => ({method, body: JSON.stringify(data)}),
  login: vi.fn(),
  lockState: vi.fn(),
  lockWorkspace: vi.fn(),
  ApiError: class ApiError extends Error {},
}));
vi.mock('../lib/compatibility', () => ({checkCompatibility: vi.fn(async () => {})}));
vi.mock('../lib/lock-preservation', () => ({preserveBeforeLock: vi.fn(async () => [])}));
vi.mock('../lib/window-journal', () => ({restoreWindowDrafts: async () => {}}));
vi.mock('../lib/native', () => ({nativeWorkspace: () => false, listenNativeLocks: async () => () => {}}));
vi.mock('../components/PairSignIn', () => ({default: () => null}));
vi.mock('../components/HostStop', () => ({default: () => null}));
vi.mock('../components/LockScreen', () => ({default: () => null}));
vi.mock('../lib/workspace-data', () => ({
  listSessions: mocks.listSessions,
  SessionCache: class {
    read = mocks.read;
    history = mocks.read;
    clear() {}
  },
}));
vi.mock('../lib/project-pins', () => ({
  loadProjectPins: mocks.loadPins,
  changeProjectPin: mocks.changePin,
  orderProjectGroups: (rows: unknown) => rows,
}));

const chat = (id: string, title = id) => ({id, title, pinned: false, archived: false, revision: 1, updated_at: 0, turns: []});
const auth = {principal: {id: 'owner', display_name: 'Owner', scopes: []}, session_id: 'auth'};

let data: WorkspaceData;
let shownError = '';

function Probe() {
  data = useWorkspaceData();
  shownError = useErrorMessage().error;
  return (
    <p data-testid="state">
      {data.sessionsState}/{data.catalogState}/{data.configurationState}
    </p>
  );
}

function renderProvider(props: {workspace?: WorkspaceMode; manager?: string} = {}) {
  const tree = (workspace: WorkspaceMode, manager: string) => (
    <ErrorProvider>
      <AuthGate>
        <WorkspaceDataProvider workspace={workspace} manager={manager}>
          <Probe />
        </WorkspaceDataProvider>
      </AuthGate>
    </ErrorProvider>
  );
  const view = render(tree(props.workspace ?? 'agent', props.manager ?? ''));
  return {...view, rerenderWith: (workspace: WorkspaceMode, manager = '') => view.rerender(tree(workspace, manager))};
}

beforeEach(() => {
  shownError = '';
  data = undefined as unknown as WorkspaceData;
  mocks.request.mockImplementation(async (path: string) => {
    if (path === '/auth/methods') return {configured: true, methods: []};
    if (path === '/auth/me') return auth;
    throw new Error('unexpected ' + path);
  });
  mocks.gql.mockImplementation(async (query: string) =>
    query.includes('engines')
      ? {
          engines: [
            {id: 'a', label: 'Alpha', installed: true},
            {id: 'b', label: 'Beta', installed: false},
          ],
          agent_providers: [{id: 'p1', name: 'Provider', secret_configured: true}],
        }
      : {projects: [{id: 'proj', name: 'Project', path: '/work/proj'}]},
  );
  mocks.listSessions.mockResolvedValue([chat('one'), chat('two')]);
  mocks.read.mockImplementation(async (id: string) => chat(id, 'Read ' + id));
  mocks.loadPins.mockResolvedValue({revision: 1, projects: []});
  mocks.changePin.mockImplementation(async (id: string, pinned: boolean, revision: number) => ({
    revision: revision + 1,
    projects: pinned ? [id] : [],
  }));
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('WorkspaceDataProvider', () => {
  it('loads projects, installed engines, providers, conversations and pins on demand', async () => {
    renderProvider();
    await screen.findByTestId('state');
    expect(data.sessionsState).toBe('loading');
    await act(async () => {
      await data.load();
    });
    expect(screen.getByTestId('state')).toHaveTextContent('ready/ready/ready');
    expect(data.projects.map(project => project.id)).toEqual(['proj']);
    expect(data.engines.map(engine => [engine.id, engine.name, engine.available])).toEqual([['a', 'Alpha', true]]);
    expect(data.providers.map(provider => provider.id)).toEqual(['p1']);
    expect(data.sessions.map(row => row.id)).toEqual(['one', 'two']);
    expect(data.pinsReady).toBe(true);
    expect(data.projectPins).toEqual({revision: 1, projects: []});
  });

  it('marks only the failing part as failed and reports what went wrong', async () => {
    mocks.listSessions.mockRejectedValue(new Error('List unavailable'));
    renderProvider();
    await screen.findByTestId('state');
    await act(async () => {
      await data.load();
    });
    expect(screen.getByTestId('state')).toHaveTextContent('failed/ready/ready');
    expect(shownError).toBe('List unavailable');
    expect(data.projects).toHaveLength(1);
  });

  it('does not load while the Data workspace is showing, and loads again when leaving it', async () => {
    const view = renderProvider({workspace: 'data'});
    await screen.findByTestId('state');
    await act(async () => {
      await data.load();
    });
    expect(mocks.gql).not.toHaveBeenCalled();
    expect(mocks.listSessions).not.toHaveBeenCalled();
    view.rerenderWith('agent');
    await waitFor(() => expect(mocks.listSessions).toHaveBeenCalledTimes(1));
  });

  it('reloads the list with the archive filter when it changes', async () => {
    renderProvider();
    await screen.findByTestId('state');
    await act(async () => {
      await data.load();
    });
    expect(mocks.listSessions).toHaveBeenLastCalledWith(false);
    act(() => data.setArchived(true));
    await waitFor(() => expect(mocks.listSessions).toHaveBeenLastCalledWith(true));
  });

  it('refreshes the list and then re-reads the selected conversation', async () => {
    renderProvider();
    await screen.findByTestId('state');
    act(() => data.setSelected('two'));
    await waitFor(() => expect(data.session?.id).toBe('two'));
    mocks.read.mockClear();
    mocks.read.mockImplementation(async (id: string) => chat(id, 'Fresh ' + id));
    await act(async () => {
      await data.refresh();
    });
    expect(mocks.read).toHaveBeenCalledWith('two');
    expect(data.session?.title).toBe('Fresh two');
    expect(data.selection.current).toBe('two');
  });

  it('pins and unpins a project against the stored revision', async () => {
    renderProvider();
    await screen.findByTestId('state');
    await act(async () => {
      await data.load();
    });
    await act(async () => {
      await data.pinProject('proj');
    });
    expect(mocks.changePin).toHaveBeenLastCalledWith('proj', true, 1);
    expect(data.projectPins).toEqual({revision: 2, projects: ['proj']});
    expect(data.pinBusy).toBe('');
    await act(async () => {
      await data.pinProject('proj');
    });
    expect(mocks.changePin).toHaveBeenLastCalledWith('proj', false, 2);
    expect(data.projectPins.projects).toEqual([]);
  });

  it('ignores a pin request until the pins have loaded', async () => {
    renderProvider();
    await screen.findByTestId('state');
    await act(async () => {
      await data.pinProject('proj');
    });
    expect(mocks.changePin).not.toHaveBeenCalled();
  });

  it('reloads the pins from the host when a pin change fails', async () => {
    renderProvider();
    await screen.findByTestId('state');
    await act(async () => {
      await data.load();
    });
    mocks.changePin.mockRejectedValue(new Error('Pins changed elsewhere'));
    mocks.loadPins.mockResolvedValue({revision: 5, projects: ['other']});
    await act(async () => {
      await data.pinProject('proj');
    });
    expect(shownError).toBe('Pins changed elsewhere');
    expect(data.projectPins).toEqual({revision: 5, projects: ['other']});
    expect(data.pinBusy).toBe('');
  });
});
