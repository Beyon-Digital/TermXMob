import {act, cleanup, configure, fireEvent, render, screen, waitFor, within} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {useEffect, useRef} from 'react';
import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest';
import App from '../App';
import {LayoutPresetStore, defaultLayout as v1Default} from '../lib/layout-presets';
import {allTabs, editorGroups, type Group, type Split} from '../lib/layout/model';
import {LayoutBookStore, V2_PREFIX} from '../lib/layout/store';
import {SHELL_ENGINE_KEY, resetShellEngineMemory} from '../lib/shell-engine';

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
vi.mock('../features/Workbench', () => ({
  default: function Workbench({visible}: {visible: boolean}) {
    const counted = useRef(false);
    useEffect(() => {
      if (!counted.current) {
        counted.current = true;
        fixture.mounts.editor = (fixture.mounts.editor ?? 0) + 1;
      }
    }, []);
    return (
      <section className="workbench" aria-label="Editor fixture" data-visible={visible}>
        <textarea aria-label="Unsaved editor buffer" defaultValue="" />
      </section>
    );
  },
}));
vi.mock('../features/TerminalPanel', () => ({
  default: function Terminal() {
    const counted = useRef(false);
    useEffect(() => {
      if (!counted.current) {
        counted.current = true;
        fixture.mounts.terminal = (fixture.mounts.terminal ?? 0) + 1;
      }
    }, []);
    return <section aria-label="Terminal fixture">terminal</section>;
  },
}));

function openUrl(search: string) {
  history.replaceState(null, '', '/' + search);
}

beforeEach(() => {
  configure({asyncUtilTimeout: 10000});
  localStorage.clear();
  resetShellEngineMemory();
  fixture.mounts = {};
  fixture.detached = false;
  openUrl('?session=conversation&shell=ide');
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

const stored = () => {
  const key = Object.keys(localStorage).find(item => item.startsWith(V2_PREFIX));
  return key ? JSON.parse(localStorage.getItem(key)!) : null;
};
const book = () => new LayoutBookStore(fixture.owner).read();

async function ready() {
  await screen.findByRole('tablist', {name: /Editor group 1 tabs/});
  await waitFor(() => expect(screen.getByRole('button', {name: 'Open editor'})).toBeEnabled());
}

describe('the layout engine flag', () => {
  it('shows the old dock by default and the tab groups with ?shell=ide', async () => {
    openUrl('?session=conversation');
    const view = render(<App />);
    await screen.findByRole('region', {name: 'conversation panel'});
    expect(screen.queryByRole('tablist', {name: /Editor group/})).not.toBeInTheDocument();
    view.unmount();
    openUrl('?session=conversation&shell=ide');
    render(<App />);
    await ready();
    expect(screen.queryByRole('region', {name: 'conversation panel'})).not.toBeInTheDocument();
  });

  it('follows the stored preference, and the address wins over it', async () => {
    localStorage.setItem(SHELL_ENGINE_KEY, 'on');
    openUrl('?session=conversation');
    const view = render(<App />);
    await ready();
    view.unmount();
    openUrl('?session=conversation&shell=classic');
    render(<App />);
    await screen.findByRole('region', {name: 'conversation panel'});
  });

  it('turns on from Settings, now, without a reload, and back off again', async () => {
    const user = userEvent.setup();
    openUrl('?session=conversation');
    render(<App />);
    await screen.findByRole('region', {name: 'conversation panel'});
    await user.click(screen.getByRole('button', {name: /^Settings/}));
    const toggle = await screen.findByRole('checkbox', {name: 'Use the new workspace layout (preview)'});
    expect(toggle).not.toBeChecked();
    await user.click(toggle);
    await ready();
    expect(localStorage.getItem(SHELL_ENGINE_KEY)).toBe('on');
    // The Settings dialog belonged to the old layout; the new one starts at the workspace.
    expect(screen.queryByRole('dialog', {name: 'Settings'})).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', {name: /^Settings/}));
    const tab = await screen.findByRole('tab', {name: 'Settings'});
    expect(tab).toHaveAttribute('aria-selected', 'true');
    const back = await screen.findByRole('checkbox', {name: 'Use the new workspace layout (preview)'});
    expect(back).toBeChecked();
    await user.click(back);
    await screen.findByRole('region', {name: 'conversation panel'});
    expect(localStorage.getItem(SHELL_ENGINE_KEY)).toBeNull();
  });

  it('shows the toggle as fixed while the address sets the layout', async () => {
    const user = userEvent.setup();
    render(<App />);
    await ready();
    await user.click(screen.getByRole('button', {name: /^Settings/}));
    const toggle = await screen.findByRole('checkbox', {name: 'Use the new workspace layout (preview)'});
    expect(toggle).toBeChecked();
    expect(toggle).toBeDisabled();
  });
});

describe('the tab layout in the shell', () => {
  it('starts with the main agent chat in one group, the side bar open and no panel', async () => {
    render(<App />);
    await ready();
    const tabs = within(screen.getByRole('tablist', {name: 'Editor group 1 tabs'})).getAllByRole('tab');
    expect(tabs.map(tab => tab.textContent)).toEqual(['Tree shell']);
    expect(screen.getByRole('navigation', {name: 'Workspace navigation'})).toBeInTheDocument();
    expect(screen.queryByRole('group', {name: 'Panel'})).not.toBeInTheDocument();
    expect(screen.getByRole('complementary', {name: 'Chats and agents'})).toBeInTheDocument();
  });

  it('opens an editor tab from the chat and keeps the editor mounted through a split and a move', async () => {
    const user = userEvent.setup();
    render(<App />);
    await ready();
    await user.click(screen.getByRole('button', {name: 'Open editor'}));
    const editor = await screen.findByRole('tab', {name: 'Editor'});
    expect(editor).toHaveAttribute('aria-selected', 'true');
    const buffer = await screen.findByRole('textbox', {name: 'Unsaved editor buffer'});
    await user.type(buffer, 'unsaved work');
    const panel = buffer.closest('[role=tabpanel]')!;

    await user.pointer({keys: '[MouseRight]', target: editor});
    await user.click(await screen.findByRole('menuitem', {name: 'Split right'}));
    expect(screen.getAllByRole('tablist')).toHaveLength(2);
    expect(screen.getByRole('textbox', {name: 'Unsaved editor buffer'})).toBe(buffer);
    expect(buffer.closest('[role=tabpanel]')).toBe(panel);

    await user.pointer({keys: '[MouseRight]', target: screen.getByRole('tab', {name: 'Editor'})});
    await user.click(await screen.findByRole('menuitem', {name: 'Move to other group'}));
    expect(screen.getByRole('textbox', {name: 'Unsaved editor buffer'})).toBe(buffer);
    expect(buffer).toHaveValue('unsaved work');
    expect(fixture.mounts.editor).toBe(1);
  });

  it('opens the terminal in the bottom panel', async () => {
    const user = userEvent.setup();
    render(<App />);
    await ready();
    await user.click(screen.getByRole('button', {name: 'Open terminal'}));
    await screen.findByRole('group', {name: 'Panel'});
    expect(within(screen.getByRole('group', {name: 'Panel'})).getByRole('tab', {name: 'Terminal'})).toBeInTheDocument();
    expect(await screen.findByLabelText('Terminal fixture')).toBeVisible();
  });

  it('splits right with the keyboard (Mod+\\) and down with the chord, focuses groups with Mod+1 and Mod+2', async () => {
    const user = userEvent.setup();
    render(<App />);
    await ready();
    await user.click(screen.getByRole('button', {name: 'Open editor'}));
    await screen.findByRole('tab', {name: 'Editor'});
    fireEvent.keyDown(window, {key: '\\', ctrlKey: true});
    await waitFor(() => expect(screen.getAllByRole('tablist')).toHaveLength(2));
    const focused = () => document.querySelector('.tab-group[data-focused]')?.getAttribute('aria-label');
    // Splitting moves the active tab (the editor) into a new group, which takes the focus.
    expect(focused()).toBe('Editor group 2');
    fireEvent.keyDown(window, {key: '1', ctrlKey: true});
    await waitFor(() => expect(focused()).toBe('Editor group 1'));
    fireEvent.keyDown(window, {key: '2', ctrlKey: true});
    await waitFor(() => expect(focused()).toBe('Editor group 2'));
    // Mod+1 meant "Chat" in the old dock; here it never opens a second chat.
    expect(screen.getAllByRole('tab', {name: 'Tree shell'})).toHaveLength(1);
    await waitFor(() => expect(stored()).not.toBeNull());
  });

  it('closes the focused tab with Mod+W and brings it back with Shift+Mod+T', async () => {
    const user = userEvent.setup();
    render(<App />);
    await ready();
    await user.click(screen.getByRole('button', {name: 'Open editor'}));
    await screen.findByRole('tab', {name: 'Editor'});
    fireEvent.keyDown(window, {key: 'w', ctrlKey: true});
    await waitFor(() => expect(screen.queryByRole('tab', {name: 'Editor'})).not.toBeInTheDocument());
    fireEvent.keyDown(window, {key: 't', ctrlKey: true, shiftKey: true});
    expect(await screen.findByRole('tab', {name: 'Editor'})).toBeInTheDocument();
    fireEvent.keyDown(window, {key: 'Tab', ctrlKey: true});
    await waitFor(() => expect(screen.getByRole('tab', {name: 'Tree shell'})).toHaveAttribute('aria-selected', 'true'));
  });

  it('maximizes a group with the chord and restores it with Escape', async () => {
    const user = userEvent.setup();
    render(<App />);
    await ready();
    await user.click(screen.getByRole('button', {name: 'Open editor'}));
    fireEvent.keyDown(window, {key: '\\', ctrlKey: true});
    await waitFor(() => expect(screen.getAllByRole('tablist')).toHaveLength(2));
    fireEvent.keyDown(window, {key: 'k', ctrlKey: true});
    fireEvent.keyDown(window, {key: 'z'});
    await waitFor(() => expect(screen.getAllByRole('tablist')).toHaveLength(1));
    fireEvent.keyDown(window, {key: 'Escape'});
    await waitFor(() => expect(screen.getAllByRole('tablist')).toHaveLength(2));
  });

  it('lists the new commands in the palette, and not the old dock ones', async () => {
    const user = userEvent.setup();
    render(<App />);
    await ready();
    await user.click(screen.getByRole('button', {name: 'Workspace actions'}));
    await user.click(within(await screen.findByRole('dialog', {name: 'Workspace actions'})).getByRole('button', {name: 'Commands'}));
    const palette = await screen.findByRole('dialog', {name: 'Workspace commands'});
    expect(within(palette).getByText('Split group right')).toBeInTheDocument();
    expect(within(palette).getByText('Move tab to other group')).toBeInTheDocument();
    expect(within(palette).queryByText('Toggle right panel')).not.toBeInTheDocument();
    expect(within(palette).queryByText('Focus mode / restore layout')).not.toBeInTheDocument();
  });

  it('writes the layout to the v2 key shortly after a change, and restores it on the next visit', async () => {
    const user = userEvent.setup();
    const view = render(<App />);
    await ready();
    await user.click(screen.getByRole('button', {name: 'Open editor'}));
    fireEvent.keyDown(window, {key: '\\', ctrlKey: true});
    await waitFor(() => expect(stored()?.current && editorGroups(stored().current.root).length).toBe(2));
    expect(stored().version).toBe(2);
    view.unmount();
    render(<App />);
    await waitFor(() => expect(screen.getAllByRole('tablist')).toHaveLength(2));
  });

  it('migrates the v1 presets on first use and leaves the v1 key untouched', async () => {
    const v1 = new LayoutPresetStore(fixture.owner);
    v1.remember({...v1Default('workbench'), left: true});
    const raw = localStorage.getItem(v1.key);
    render(<App />);
    await waitFor(() => expect(screen.getAllByRole('tablist').length).toBeGreaterThanOrEqual(2));
    const names = within(screen.getAllByRole('tablist')[0]).getAllByRole('tab').map(tab => tab.textContent);
    expect(names).toEqual(['Editor']);
    await waitFor(() => expect(stored()).not.toBeNull());
    expect(localStorage.getItem(v1.key)).toBe(raw);
  });

  it('opens Settings as a tab, not a dialog, and asks before closing it with unsaved edits', async () => {
    const user = userEvent.setup();
    render(<App />);
    await ready();
    await user.click(screen.getByRole('button', {name: /^Settings/}));
    const tab = await screen.findByRole('tab', {name: 'Settings'});
    expect(screen.queryByRole('dialog', {name: 'Settings'})).not.toBeInTheDocument();
    const panel = document.getElementById(tab.getAttribute('aria-controls')!)!;
    expect(within(panel).getByRole('heading', {name: 'General'})).toBeInTheDocument();
    // A clean tab closes without asking.
    await user.click(tab.querySelector('.tab-close')!);
    await waitFor(() => expect(screen.queryByRole('tab', {name: 'Settings'})).not.toBeInTheDocument());
    expect(screen.queryByRole('dialog', {name: 'Discard unsaved changes?'})).not.toBeInTheDocument();
  });

  it('opens a project page as a Projects tab and a settings category by retargeting the one Settings tab', async () => {
    const user = userEvent.setup();
    render(<App />);
    await ready();
    await user.click(screen.getByRole('button', {name: 'Projects'}));
    await screen.findByRole('tab', {name: 'Projects'});
    await user.click(screen.getByRole('button', {name: /^Settings/}));
    await screen.findByRole('tab', {name: 'Settings'});
    fireEvent.keyDown(window, {key: ',', ctrlKey: true});
    await waitFor(() => expect(screen.getAllByRole('tab', {name: 'Settings'})).toHaveLength(1));
    await waitFor(() => expect(allTabs(book().current).filter(tab => tab.kind === 'settings')).toHaveLength(1));
  });
});

describe('detached windows and phones', () => {
  it('boots a detached window with one tab and never writes the main window\'s layout', async () => {
    fixture.detached = true;
    openUrl('?session=conversation&shell=ide&layout=workbench&detached=true');
    render(<App />);
    const tabs = await screen.findAllByRole('tab');
    expect(tabs.map(tab => tab.textContent)).toEqual(['Editor']);
    await new Promise(resolve => setTimeout(resolve, 300));
    expect(stored()).toBeNull();
  });

  it('projects the tree to one tab stack on a phone and leaves the stored layout alone', async () => {
    const user = userEvent.setup();
    const view = render(<App />);
    await ready();
    await user.click(screen.getByRole('button', {name: 'Open editor'}));
    fireEvent.keyDown(window, {key: '\\', ctrlKey: true});
    await waitFor(() => expect(screen.getAllByRole('tablist')).toHaveLength(2));
    await waitFor(() => expect(stored()?.current && editorGroups(stored().current.root).length).toBe(2));
    const before = JSON.stringify(stored());
    view.unmount();

    vi.stubGlobal('matchMedia', vi.fn((query: string) => ({matches: query.includes('767'), addEventListener: vi.fn(), removeEventListener: vi.fn()})));
    render(<App />);
    await waitFor(() => expect(screen.getAllByRole('tablist')).toHaveLength(1));
    expect(screen.queryByRole('separator', {name: 'Resize groups'})).not.toBeInTheDocument();
    await user.click(screen.getByRole('tab', {name: 'Editor'}));
    await new Promise(resolve => setTimeout(resolve, 300));
    expect(stored()).toEqual(JSON.parse(before));
    expect(editorGroups((stored().current as {root: Group | Split}).root as never)).toHaveLength(2);
  });
});

describe('the old dock is untouched', () => {
  it('keeps its own storage and does not create a v2 book', async () => {
    openUrl('?session=conversation');
    render(<App />);
    await screen.findByRole('region', {name: 'conversation panel'});
    await act(async () => {
      await new Promise(resolve => setTimeout(resolve, 300));
    });
    expect(stored()).toBeNull();
    expect(Object.keys(localStorage).some(key => key.startsWith('termx-layout-presets-v1:'))).toBe(true);
  });
});
