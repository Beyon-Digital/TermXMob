import {cleanup, configure, render, screen, waitFor, within} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {afterEach, beforeEach, expect, it, vi} from 'vitest';
import App from '../App';
import {resetShellEngineMemory} from '../lib/shell-engine';

const fixture = vi.hoisted(() => ({
  owner: 'tree-shell-owner',
  session: {
    id: 'conversation',
    title: 'Tree shell',
    project_id: 'project',
    cwd: '/workspace/project',
    worktree_id: null,
    revision: 1,
    turns: [],
    engine: 'internal',
    mode: 'agent',
  },
  project: {id: 'project', name: 'Project', path: '/workspace/project'},
  mounts: {} as Record<string, number>,
  detached: false,
}));

vi.mock('../lib/compatibility', () => ({checkCompatibility: vi.fn(async () => {})}));
vi.mock('../lib/use-observation-active', () => ({useObservationActive: (visible = true) => visible}));
vi.mock('../lib/workspace-fullscreen', () => ({workspaceFullscreen: vi.fn(async () => false)}));
vi.mock('../lib/native', async original => ({
  ...(await original<typeof import('../lib/native')>()),
  nativeWorkspace: () => true,
  isDetachedWindow: () => fixture.detached,
  listenNativeLocks: async () => () => {},
  listenNativeRedock: async () => () => {},
}));
vi.mock('../lib/api', async original => ({
  ...(await original<typeof import('../lib/api')>()),
  request: vi.fn(async (path: string) => {
    if (path === '/auth/methods') return {configured: true, methods: []};
    if (path === '/auth/me')
      return {principal: {id: fixture.owner, display_name: 'Owner', scopes: []}, session_id: 'authenticated-session'};
    if (path === '/api/workspace/project-pins') return {revision: 0, projects: []};
    throw Error('Unexpected request: ' + path);
  }),
  gql: vi.fn(async () => ({projects: [fixture.project], engines: [], agent_providers: []})),
}));
vi.mock('../lib/workspace-data', () => ({
  listSessions: vi.fn(async () => [fixture.session]),
  SessionCache: class {
    async read() {
      return fixture.session;
    }
    clear() {}
  },
}));
vi.mock('../lib/session-changes', () => ({
  useSessionChanges: () => ({scope: 'fixture', available: true, unavailableReason: null, data: null, error: null, loading: false, refresh: async () => {}}),
}));
vi.mock('../features/CompanionHabitat', () => ({default: () => null}));
vi.mock('../features/CaptureStatus', () => ({default: () => null}));
vi.mock('../features/BrowserSurface', () => ({default: () => null}));
vi.mock('../features/Chat', () => ({
  default: ({session, onOpenSurface}: {session: unknown; onOpenSurface: (surface: 'editor' | 'terminal' | 'browser') => void}) => (
    <div>
      <button disabled={!session} onClick={() => onOpenSurface('editor')}>
        Open editor
      </button>
      <button disabled={!session} onClick={() => onOpenSurface('terminal')}>
        Open terminal
      </button>
      <button disabled={!session} onClick={() => onOpenSurface('browser')}>
        Open browser
      </button>
    </div>
  ),
}));
vi.mock('../features/SettingsGeneral', () => ({
  // A page with an edit the settings surface does not treat as autosave: typing here is an unsaved change.
  default: () => (
    <section aria-label="General fixture">
      <h2>General</h2>
      <input aria-label="Draft name" />
    </section>
  ),
}));

beforeEach(() => {
  configure({asyncUtilTimeout: 10000});
  localStorage.clear();
  resetShellEngineMemory();
  history.replaceState(null, '', '/?session=conversation&shell=ide');
  vi.stubGlobal('innerWidth', 1280);
  vi.stubGlobal('matchMedia', vi.fn(() => ({matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn()})));
  vi.stubGlobal('ResizeObserver', class {observe() {} unobserve() {} disconnect() {}});
});
afterEach(() => {
  cleanup();
  configure({asyncUtilTimeout: 1000});
  vi.unstubAllGlobals();
  localStorage.clear();
  history.replaceState(null, '', '/');
});

async function openSettings() {
  const user = userEvent.setup();
  render(<App />);
  await waitFor(() => expect(screen.getByRole('button', {name: 'Open editor'})).toBeEnabled());
  await user.click(screen.getByRole('button', {name: /^Settings/}));
  const tab = await screen.findByRole('tab', {name: 'Settings'});
  await user.type(await screen.findByRole('textbox', {name: 'Draft name'}), 'edit');
  return {user, tab};
}

it('marks a Settings tab with unsaved edits and asks before closing it, using the existing discard dialog', async () => {
  const {user, tab} = await openSettings();
  await waitFor(() => expect(screen.getByRole('tab', {name: /Settings\s*, unsaved changes/})).toBeInTheDocument());
  await user.click(tab.querySelector('.tab-close')!);
  const dialog = await screen.findByRole('dialog', {name: 'Discard unsaved changes?'});
  // Keep editing: the tab stays.
  await user.click(within(dialog).getByRole('button', {name: 'Keep editing'}));
  expect(screen.getByRole('tab', {name: /Settings/})).toBeInTheDocument();
  // Discard: the tab closes.
  await user.click(screen.getByRole('tab', {name: /Settings/}).querySelector('.tab-close')!);
  await user.click(within(await screen.findByRole('dialog', {name: 'Discard unsaved changes?'})).getByRole('button', {name: 'Discard changes'}));
  await waitFor(() => expect(screen.queryByRole('tab', {name: /Settings/})).not.toBeInTheDocument());
});

it('asks before Close others would drop the dirty Settings tab', async () => {
  const {user} = await openSettings();
  await user.click(screen.getByRole('tab', {name: 'Tree shell'}));
  await user.pointer({keys: '[MouseRight]', target: screen.getByRole('tab', {name: 'Tree shell'})});
  await user.click(await screen.findByRole('menuitem', {name: 'Close others'}));
  const dialog = await screen.findByRole('dialog', {name: 'Discard unsaved changes?'});
  await user.click(within(dialog).getByRole('button', {name: 'Keep editing'}));
  expect(screen.getByRole('tab', {name: /Settings/})).toBeInTheDocument();
});
