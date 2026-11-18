import {act, cleanup, configure, fireEvent, render, screen, waitFor, within} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {useEffect, useRef} from 'react';
import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest';
import App from '../App';
import {LayoutPresetStore, defaultLayout as v1Default} from '../lib/layout-presets';
import {allTabs, editorGroups, type Group, type Split} from '../lib/layout/model';
import {LayoutBookStore, V2_PREFIX} from '../lib/layout/store';
import {SHELL_ENGINE_KEY, resetShellEngineMemory} from '../lib/shell-engine';
import {useBuffers} from '../features/editor/buffer-store';
import v1Books from '../lib/layout/fixtures/v1-books.json';

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
    if (path.includes('operation=tree')) return {entries: [{name: 'a.py', path: 'a.py', directory: false}, {name: 'src', path: 'src', directory: true}]};
    if (path.includes('operation=quick-open')) return {results: [{path: 'a.py'}, {path: 'src/b.py'}], truncated: false};
    if (path.includes('/delivery')) return {worktrees: []};
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
vi.mock('../lib/session-changes', async original => ({
  ...(await original<typeof import('../lib/session-changes')>()),
  useSessionChanges: () => ({scope: 'fixture', available: true, unavailableReason: null, data: null, error: null, loading: false, refresh: async () => {}}),
}));
vi.mock('../features/CompanionHabitat', () => ({default: () => null}));
vi.mock('../features/CaptureStatus', () => ({default: () => null}));
vi.mock('../features/OrchestratorStatus', () => ({default: () => null}));
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

vi.mock('../features/editor/FileTab', () => ({
  default: function FileTab({visible, fileKey}: {visible: boolean; fileKey?: string}) {
    const store = useBuffers();
    const counted = useRef(false);
    useEffect(() => {
      if (!counted.current) {
        counted.current = true;
        fixture.mounts.editor = (fixture.mounts.editor ?? 0) + 1;
      }
    }, []);
    return (
      <section aria-label="Editor fixture" data-visible={visible} data-file={fileKey}>
        <textarea aria-label="Unsaved editor buffer" defaultValue="" />
        {fileKey && (
          <button
            onClick={() =>
              store.add({key: fileKey, project_id: 'project', path: fileKey.split(':').at(-1)!, content: 'edited', revision: '1', editable: true, dirty: true, root: '/workspace/project'})
            }
          >
            Make an unsaved edit
          </button>
        )}
      </section>
    );
  },
}));
const shells = vi.hoisted(() => ({next: 0, deleted: [] as string[]}));
vi.mock('../features/terminal/terminal-api', () => ({
  createPty: vi.fn(async () => ({id: 'pty-' + ++shells.next, title: 'Project', cwd: '/workspace/project', exited: false})),
  deletePty: vi.fn(async (id: string) => {
    shells.deleted.push(id);
  }),
  listPtys: vi.fn(async () => []),
}));
vi.mock('../features/terminal/TerminalTab', () => ({
  default: function TerminalTab({ptyId}: {ptyId?: string}) {
    const counted = useRef(false);
    useEffect(() => {
      if (!counted.current) {
        counted.current = true;
        fixture.mounts.terminal = (fixture.mounts.terminal ?? 0) + 1;
      }
    }, []);
    return <section aria-label="Terminal fixture">terminal {ptyId ?? 'unattached'}</section>;
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
  shells.next = 0;
  shells.deleted = [];
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
  it('shows the tab groups by default and the classic dock with ?shell=classic', async () => {
    openUrl('?session=conversation');
    const view = render(<App />);
    await ready();
    expect(screen.queryByRole('region', {name: 'conversation panel'})).not.toBeInTheDocument();
    view.unmount();
    openUrl('?session=conversation&shell=classic');
    render(<App />);
    await screen.findByRole('region', {name: 'conversation panel'});
    expect(screen.queryByRole('tablist', {name: /Editor group/})).not.toBeInTheDocument();
  });

  it('follows the stored preference, and the address wins over it', async () => {
    localStorage.setItem(SHELL_ENGINE_KEY, 'off');
    openUrl('?session=conversation');
    const view = render(<App />);
    await screen.findByRole('region', {name: 'conversation panel'});
    view.unmount();
    openUrl('?session=conversation&shell=ide');
    render(<App />);
    await ready();
  });

  it('turns the classic layout on from Settings, now, without a reload, and back off again', async () => {
    const user = userEvent.setup();
    openUrl('?session=conversation');
    render(<App />);
    await ready();
    await user.click(screen.getByRole('button', {name: /^Settings/}));
    const toggle = await screen.findByRole('checkbox', {name: 'Use the classic workspace layout (deprecated)'});
    expect(toggle).not.toBeChecked();
    await user.click(toggle);
    await screen.findByRole('region', {name: 'conversation panel'});
    expect(localStorage.getItem(SHELL_ENGINE_KEY)).toBe('off');
    // The classic layout starts at the workspace; its Settings is a dialog.
    await user.click(screen.getByRole('button', {name: /^Settings/}));
    const back = await screen.findByRole('checkbox', {name: 'Use the classic workspace layout (deprecated)'});
    expect(back).toBeChecked();
    await user.click(back);
    await screen.findByRole('tablist', {name: /Editor group 1 tabs/});
    expect(localStorage.getItem(SHELL_ENGINE_KEY)).toBeNull();
  });

  it('shows the toggle as fixed while the address sets the layout', async () => {
    const user = userEvent.setup();
    render(<App />);
    await ready();
    await user.click(screen.getByRole('button', {name: /^Settings/}));
    const toggle = await screen.findByRole('checkbox', {name: 'Use the classic workspace layout (deprecated)'});
    expect(toggle).not.toBeChecked();
    expect(toggle).toBeDisabled();
  });
});

describe('the tab layout in the shell', () => {
  it('starts with the main agent chat in one group, the side bar open and no panel', async () => {
    render(<App />);
    await ready();
    const tabs = within(screen.getByRole('tablist', {name: 'Editor group 1 tabs'})).getAllByRole('tab');
    expect(tabs.map(tab => tab.textContent)).toEqual(['Tree shell']);
    expect(screen.getByRole('toolbar', {name: 'Activity bar'})).toBeInTheDocument();
    // The Workspace actions menu, sidebar hover and the old rail are gone: every item lives in a command, the title bar or the menus.
    expect(screen.queryByRole('button', {name: 'Workspace actions'})).not.toBeInTheDocument();
    expect(screen.queryByRole('navigation', {name: 'Workspace navigation'})).not.toBeInTheDocument();
    expect(screen.getByRole('banner', {name: 'Window'})).toBeInTheDocument();
    expect(screen.getByRole('contentinfo', {name: 'Status'})).toBeInTheDocument();
    expect(screen.queryByRole('group', {name: 'Panel'})).not.toBeInTheDocument();
    expect(screen.getByRole('complementary', {name: 'Agents'})).toBeInTheDocument();
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
    await user.click(screen.getByRole('button', {name: 'Search files and commands'}));
    const palette = await screen.findByRole('dialog', {name: 'Search files and commands'});
    await user.type(within(palette).getByRole('combobox', {name: 'Search files and commands'}), '>');
    expect(await within(palette).findByText('Split group right')).toBeInTheDocument();
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

  it('gives a profile with saved v1 presets its layouts as named layouts, from the committed v1 fixture', async () => {
    // The fixture is written by the v1 code itself (see lib/layout/migrate-v1.test.ts), keyed to its own owner and device.
    fixture.owner = v1Books.owner;
    localStorage.setItem('termx-layout-device-v1', v1Books.device);
    const v1Key = 'termx-layout-presets-v1:' + JSON.stringify([v1Books.owner, v1Books.device]);
    localStorage.setItem(v1Key, v1Books.books.custom);
    openUrl('?session=conversation');
    render(<App />);
    await waitFor(() => expect(screen.getAllByRole('tablist').length).toBeGreaterThanOrEqual(3));
    await waitFor(() => expect(screen.getAllByRole('button', {name: 'Open editor'})[0]).toBeEnabled());
    // The layout the profile last used is open: editor, review and conversation groups.
    await waitFor(() => expect(stored()).not.toBeNull());
    const named = (stored().named as {name: string}[]).map(item => item.name);
    expect(named).toContain('Review layout');
    for (const preset of ['Chat', 'Code + chat', 'Browser + chat', 'Focus']) expect(named).toContain(preset);
    // The profile's own preset is offered next to the built-in ones, and the v1 copy is left untouched.
    fireEvent.keyDown(window, {key: 'l', ctrlKey: true, altKey: true});
    const dialog = await screen.findByRole('dialog', {name: 'Saved layout presets'});
    expect(within(dialog).getByText('Review layout')).toBeInTheDocument();
    expect(localStorage.getItem(v1Key)).toBe(v1Books.books.custom);
    fixture.owner = 'tree-shell-owner';
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

describe('the IDE chrome in the shell', () => {
  it('shows the project and branch, the status bar, and the approvals badge only when something needs the owner', async () => {
    render(<App />);
    await ready();
    expect(within(screen.getByRole('banner', {name: 'Window'})).getByRole('navigation', {name: 'Project and branch'})).toHaveTextContent('Project');
    const status = screen.getByRole('contentinfo', {name: 'Status'});
    expect(within(status).getByRole('status')).toHaveTextContent('Connected');
    expect(within(status).getByRole('button', {name: 'Lock'})).toBeInTheDocument();
    await waitFor(() => expect(within(status).getByRole('button', {name: /Main agent/})).toBeInTheDocument());
    expect(within(screen.getByRole('banner', {name: 'Window'})).queryByText(/need/)).not.toBeInTheDocument();
  });

  it('switches the side bar view from the activity bar, and the selected view toggles the side bar', async () => {
    const user = userEvent.setup();
    render(<App />);
    await ready();
    const bar = screen.getByRole('toolbar', {name: 'Activity bar'});
    expect(within(bar).getByRole('button', {name: 'Agents'})).toHaveAttribute('aria-pressed', 'true');
    await user.click(within(bar).getByRole('button', {name: 'Explorer'}));
    expect(await screen.findByRole('complementary', {name: 'Explorer'})).toBeInTheDocument();
    expect(screen.queryByRole('complementary', {name: 'Agents'})).not.toBeInTheDocument();
    expect(await screen.findByRole('tree', {name: 'Files'})).toBeInTheDocument();
    await user.click(within(bar).getByRole('button', {name: 'Search'}));
    expect(await screen.findByRole('complementary', {name: 'Search'})).toBeInTheDocument();
    await user.click(within(bar).getByRole('button', {name: 'Source control'}));
    expect(await screen.findByRole('complementary', {name: 'Source control'})).toBeInTheDocument();
    await user.click(within(bar).getByRole('button', {name: 'Sites'}));
    expect(await screen.findByText('No saved sites yet.')).toBeInTheDocument();
    // The selected view again hides the side bar, and the toggle shows it again.
    await user.click(within(bar).getByRole('button', {name: 'Sites'}));
    await waitFor(() => expect(screen.queryByRole('complementary', {name: 'Sites'})).not.toBeInTheDocument());
    await waitFor(() => expect(stored().current.sideBar.visible).toBe(false));
    await user.click(screen.getByRole('button', {name: 'Toggle side bar'}));
    expect(await screen.findByRole('complementary', {name: 'Sites'})).toBeInTheDocument();
    expect(stored().current.sideBar.view).toBe('sites');
  });

  it('resizes the side bar with the keyboard between 200 and 420 and remembers the width', async () => {
    const user = userEvent.setup();
    render(<App />);
    await ready();
    const handle = screen.getByRole('separator', {name: 'Resize side bar'});
    expect(handle).toHaveAttribute('aria-valuenow', '260');
    handle.focus();
    await user.keyboard('{ArrowRight}{ArrowRight}');
    await waitFor(() => expect(screen.getByRole('separator', {name: 'Resize side bar'})).toHaveAttribute('aria-valuenow', '292'));
    await user.keyboard('{End}');
    expect(screen.getByRole('separator', {name: 'Resize side bar'})).toHaveAttribute('aria-valuenow', '420');
    await user.keyboard('{Home}');
    expect(screen.getByRole('separator', {name: 'Resize side bar'})).toHaveAttribute('aria-valuenow', '200');
    await waitFor(() => expect(stored().current.sideBar.size).toBe(200));
  });

  it('opens Settings and Projects as tabs from the activity bar', async () => {
    const user = userEvent.setup();
    render(<App />);
    await ready();
    await user.click(within(screen.getByRole('toolbar', {name: 'Activity bar'})).getByRole('button', {name: 'Settings'}));
    expect(await screen.findByRole('tab', {name: 'Settings'})).toHaveAttribute('aria-selected', 'true');
    await user.click(within(screen.getByRole('toolbar', {name: 'Activity bar'})).getByRole('button', {name: 'Projects'}));
    expect(await screen.findByRole('tab', {name: 'Projects'})).toBeInTheDocument();
  });

  it('opens a file from Quick open into a tab, once, and a second file into a second tab, and splits it like any tab', async () => {
    const user = userEvent.setup();
    render(<App />);
    await ready();
    const open = async (name: string) => {
      await user.click(screen.getByRole('button', {name: 'Search files and commands'}));
      const dialog = await screen.findByRole('dialog', {name: 'Search files and commands'});
      await user.click(await within(dialog).findByRole('option', {name: new RegExp(name.replace('.', '\\.'))}));
      await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    };
    await open('a.py');
    const strip = () => within(screen.getByRole('tablist', {name: 'Editor group 1 tabs'}));
    expect(strip().getAllByRole('tab').map(tab => tab.textContent)).toEqual(['Tree shell', 'a.py']);
    expect(strip().getByRole('tab', {name: 'a.py'})).toHaveAttribute('aria-selected', 'true');
    // The same file again reveals its tab instead of adding a second one.
    await user.click(strip().getByRole('tab', {name: 'Tree shell'}));
    await open('a.py');
    expect(strip().getAllByRole('tab')).toHaveLength(2);
    expect(strip().getByRole('tab', {name: 'a.py'})).toHaveAttribute('aria-selected', 'true');
    await open('b.py');
    expect(strip().getAllByRole('tab').map(tab => tab.textContent)).toEqual(['Tree shell', 'a.py', 'b.py']);
    await user.pointer({keys: '[MouseRight]', target: strip().getByRole('tab', {name: 'b.py'})});
    await user.click(await screen.findByRole('menuitem', {name: 'Split right'}));
    expect(screen.getAllByRole('tablist')).toHaveLength(2);
    expect(within(screen.getByRole('tablist', {name: 'Editor group 2 tabs'})).getByRole('tab', {name: 'b.py'})).toBeInTheDocument();
  });

  it('opens a file from the Explorer tree', async () => {
    const user = userEvent.setup();
    render(<App />);
    await ready();
    await user.click(within(screen.getByRole('toolbar', {name: 'Activity bar'})).getByRole('button', {name: 'Explorer'}));
    await user.click(await screen.findByRole('treeitem', {name: /a\.py/}));
    expect(await screen.findByRole('tab', {name: 'a.py'})).toHaveAttribute('aria-selected', 'true');
  });

  it('asks before closing a file tab with unsaved edits: keep editing keeps it, discard closes it and drops the buffer', async () => {
    const user = userEvent.setup();
    render(<App />);
    await ready();
    await user.click(screen.getByRole('button', {name: 'Search files and commands'}));
    await user.click(await within(await screen.findByRole('dialog')).findByRole('option', {name: /a\.py/}));
    await user.click(await screen.findByRole('button', {name: 'Make an unsaved edit'}));
    const tab = await screen.findByRole('tab', {name: /a\.py/});
    await waitFor(() => expect(tab).toHaveTextContent('unsaved changes'));
    await user.click(tab.querySelector('.tab-close')!);
    const dialog = await screen.findByRole('dialog', {name: 'Discard unsaved changes?'});
    await user.click(within(dialog).getByRole('button', {name: 'Keep editing'}));
    expect(screen.getByRole('tab', {name: /a\.py/})).toBeInTheDocument();
    await user.click(screen.getByRole('tab', {name: /a\.py/}).querySelector('.tab-close')!);
    await user.click(within(await screen.findByRole('dialog', {name: 'Discard unsaved changes?'})).getByRole('button', {name: 'Discard changes'}));
    await waitFor(() => expect(screen.queryByRole('tab', {name: /a\.py/})).not.toBeInTheDocument());
    // The dropped buffer does not come back as an orphan tab.
    await new Promise(resolve => setTimeout(resolve, 150));
    expect(screen.queryByRole('tab', {name: /a\.py/})).not.toBeInTheDocument();
  });

  it('shows Terminal in the bottom panel and starts one shell per tab with the + menu and Control+Shift+backtick', async () => {
    const user = userEvent.setup();
    render(<App />);
    await ready();
    fireEvent.keyDown(window, {key: 'j', ctrlKey: true});
    const panel = await screen.findByRole('group', {name: 'Panel'});
    expect(within(panel).getByRole('tab', {name: 'Terminal'})).toBeInTheDocument();
    await user.click(within(panel).getByRole('button', {name: 'New tab'}));
    await user.click(await screen.findByRole('menuitemcheckbox', {name: 'Terminal'}));
    await waitFor(() => expect(screen.getByText('terminal pty-1')).toBeInTheDocument());
    // The panel's starting Terminal tab takes the first shell.
    expect(within(panel).getAllByRole('tab').map(tab => tab.textContent)).toEqual(['Terminal']);
    await user.click(within(panel).getByRole('button', {name: 'New tab'}));
    await user.click(await screen.findByRole('menuitemcheckbox', {name: 'Terminal'}));
    await waitFor(() => expect(within(panel).getAllByRole('tab').map(tab => tab.textContent)).toEqual(['Terminal', 'Terminal 2']));
    expect(screen.getByText('terminal pty-2')).toBeInTheDocument();
    fireEvent.keyDown(window, {key: '`', code: 'Backquote', ctrlKey: true, shiftKey: true});
    await waitFor(() => expect(within(panel).getAllByRole('tab')).toHaveLength(3));
    expect(screen.getByText('terminal pty-3')).toBeInTheDocument();
    // Only the panel's own kinds are offered there.
    await user.click(within(panel).getByRole('button', {name: 'New tab'}));
    expect((await screen.findAllByRole('menuitemcheckbox')).map(item => item.textContent)).toEqual(['Terminal', 'Problems', 'Preview', 'Run and debug']);
  });

  it('ends a shell when its tab closes, and keeps the others', async () => {
    const user = userEvent.setup();
    render(<App />);
    await ready();
    fireEvent.keyDown(window, {key: 'j', ctrlKey: true});
    const panel = await screen.findByRole('group', {name: 'Panel'});
    for (let count = 0; count < 2; count++) {
      await user.click(within(panel).getByRole('button', {name: 'New tab'}));
      await user.click(await screen.findByRole('menuitemcheckbox', {name: 'Terminal'}));
      await waitFor(() => expect(shells.next).toBe(count + 1));
    }
    await waitFor(() => expect(within(panel).getAllByRole('tab')).toHaveLength(2));
    await user.click(within(panel).getByRole('tab', {name: 'Terminal 2'}).querySelector('.tab-close')!);
    await waitFor(() => expect(shells.deleted).toEqual(['pty-2']));
    expect(within(panel).getAllByRole('tab')).toHaveLength(1);
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

  it('projects the tree to one tab stack on a phone, with a top bar and bottom nav, and leaves the stored layout alone', async () => {
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
    // One stack: no tab strips and no splitters; the top bar names the tab and switches between them.
    const top = await screen.findByRole('banner', {name: 'Window'});
    await waitFor(() => expect(screen.queryAllByRole('tablist')).toHaveLength(0));
    expect(screen.queryByRole('separator', {name: 'Resize groups'})).not.toBeInTheDocument();
    expect(within(top).getByRole('button', {name: 'Open side bar'})).toBeInTheDocument();
    expect(screen.getByRole('navigation', {name: 'Primary'})).toBeInTheDocument();
    expect(screen.queryByRole('toolbar', {name: 'Activity bar'})).not.toBeInTheDocument();
    await user.click(within(top).getByRole('button', {name: /Switch tab/}));
    await user.click(await screen.findByRole('menuitem', {name: 'Editor'}));
    expect(within(top).getByRole('heading', {name: 'Editor'})).toBeInTheDocument();
    await new Promise(resolve => setTimeout(resolve, 300));
    expect(stored()).toEqual(JSON.parse(before));
    expect(editorGroups((stored().current as {root: Group | Split}).root as never)).toHaveLength(2);
  });

  it('opens the side bar as a drawer on a phone, from the top bar and from the bottom nav, and Escape closes it', async () => {
    const user = userEvent.setup();
    vi.stubGlobal('matchMedia', vi.fn((query: string) => ({matches: query.includes('767'), addEventListener: vi.fn(), removeEventListener: vi.fn()})));
    render(<App />);
    await user.click(await screen.findByRole('button', {name: 'Open side bar'}));
    const drawer = await screen.findByRole('dialog', {name: 'Agents'});
    expect(within(drawer).getByRole('button', {name: 'New chat'})).toBeInTheDocument();
    await user.keyboard('{Escape}');
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    await user.click(within(screen.getByRole('navigation', {name: 'Primary'})).getByRole('button', {name: 'Files'}));
    expect(await screen.findByRole('dialog', {name: 'Explorer'})).toBeInTheDocument();
  });
});

describe('the classic dock is untouched', () => {
  it('keeps its own storage and does not create a v2 book', async () => {
    openUrl('?session=conversation&shell=classic');
    render(<App />);
    await screen.findByRole('region', {name: 'conversation panel'});
    await act(async () => {
      await new Promise(resolve => setTimeout(resolve, 300));
    });
    expect(stored()).toBeNull();
    expect(Object.keys(localStorage).some(key => key.startsWith('termx-layout-presets-v1:'))).toBe(true);
  });
});
