import {afterEach, beforeEach, expect, it, vi} from 'vitest';
import {act, cleanup, render, screen, waitFor, within} from '@testing-library/react';
import userEvent from '@testing-library/user-event';

const mock = vi.hoisted(() => ({
  instances: [] as any[],
  keyHandlers: [] as ((event: any) => boolean)[],
  linkHandlers: [] as ((event: any, uri: string) => void)[],
  searches: [] as any[],
  gql: vi.fn(),
  request: vi.fn(),
}));
vi.mock('@xterm/xterm', () => ({
  Terminal: class {
    options: any; reset = vi.fn(); write = vi.fn(); dispose = vi.fn(); focus = vi.fn(); cols = 80; rows = 24;
    constructor(options: any) { this.options = options; mock.instances.push(this); }
    loadAddon() {} open() {} onData() {}
    attachCustomKeyEventHandler(handler: (event: any) => boolean) { mock.keyHandlers.push(handler); }
  },
}));
vi.mock('@xterm/addon-fit', () => ({FitAddon: class { fit() {} }}));
vi.mock('@xterm/addon-search', () => ({
  SearchAddon: class {
    findNext = vi.fn(() => true); findPrevious = vi.fn(() => true); clearDecorations = vi.fn(); onDidChangeResults = vi.fn();
    constructor() { mock.searches.push(this); }
  },
}));
vi.mock('@xterm/addon-web-links', () => ({WebLinksAddon: class { constructor(handler: (event: any, uri: string) => void) { mock.linkHandlers.push(handler); } }}));
vi.mock('../../lib/api', () => ({gql: mock.gql, request: mock.request}));
vi.mock('../../lib/window-state', () => ({rememberTerminal: () => {}, recalledTerminal: () => ''}));

import TerminalPanel from '../TerminalPanel';
import {expectNoAxeViolations} from '../../test-axe';

class Socket {
  static OPEN = 1; static items: Socket[] = [];
  readyState = 1; binaryType = ''; onopen: any; onclose: any; onerror: any; onmessage: any;
  url: string; send = vi.fn(); close = vi.fn();
  constructor(url: string | URL) { this.url = String(url); Socket.items.push(this); }
}
const project = {id: 'project-a', name: 'Project A', path: '/project-a'};
const rows = [
  {id: 'pty-1', title: 'zsh', cwd: '/project-a', exited: false},
  {id: 'pty-2', title: 'pnpm dev', cwd: '/project-a', exited: false},
  {id: 'pty-3', title: 'logs', cwd: '/project-a', exited: false},
];
let inventory = rows;

beforeEach(() => {
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} });
  vi.stubGlobal('WebSocket', Socket);
  inventory = rows;
  mock.gql.mockReset().mockImplementation(async (query: string, variables?: any) => {
    if (query.includes('delete_session')) return {delete_session: {ok: true}};
    if (query.includes('create_session')) return {create_session: {id: 'pty-new', title: variables.input.title, cwd: variables.input.cwd, exited: false}};
    return {sessions: inventory};
  });
  mock.request.mockReset().mockResolvedValue({});
});
afterEach(() => {
  cleanup(); vi.unstubAllGlobals();
  Socket.items = []; mock.instances.length = 0; mock.keyHandlers.length = 0; mock.linkHandlers.length = 0; mock.searches.length = 0;
});

const tab = (name: string) => screen.getByRole('tab', {name});
async function renderPanel(first = 'zsh') {
  const view = render(<TerminalPanel project={project} onError={vi.fn()} />);
  await screen.findByRole('tab', {name: first});
  return view;
}
const closeMark = (id: string) => document.querySelector<HTMLElement>(`[data-session-id="${id}"] .terminal-tab-close`)!;

it('lists every session as a tab and attaches none until one is chosen', async () => {
  await renderPanel();
  expect(screen.getAllByRole('tab').map((node) => node.textContent)).toEqual(['zsh', 'pnpm dev', 'logs']);
  expect(screen.queryByRole('tab', {selected: true})).toBeNull();
  expect(Socket.items).toHaveLength(0);
  expect(mock.instances).toHaveLength(0);
  expect(screen.getByText(/Choose a terminal tab/)).toBeVisible();
});

it('keeps one xterm per PTY mounted while its tab is hidden, and shows only the selected one', async () => {
  const user = userEvent.setup();
  await renderPanel();
  await user.click(tab('zsh'));
  await waitFor(() => expect(Socket.items).toHaveLength(1));
  await user.click(tab('pnpm dev'));
  await waitFor(() => expect(Socket.items).toHaveLength(2));
  expect(mock.instances).toHaveLength(2);
  expect(Socket.items.map((socket) => socket.url.split('/').at(-2))).toEqual(['pty-1', 'pty-2']);
  const first = mock.instances[0];

  await user.click(tab('zsh'));
  expect(tab('zsh')).toHaveAttribute('aria-selected', 'true');
  expect(mock.instances).toHaveLength(2);
  expect(Socket.items).toHaveLength(2);
  expect(first.dispose).not.toHaveBeenCalled();
  expect(first.reset).toHaveBeenCalledTimes(1);
  Socket.items.forEach((socket) => expect(socket.close).not.toHaveBeenCalled());

  const panels = screen.getAllByRole('tabpanel', {hidden: true});
  expect(panels).toHaveLength(2);
  expect(panels.filter((panel) => !panel.hidden)).toHaveLength(1);
  expect(panels.filter((panel) => panel.hidden)).toHaveLength(1);
  expect(panels.every((panel) => panel.querySelector('.terminal-host'))).toBe(true);
});

it('moves between tabs with the arrow keys, Home and End', async () => {
  const user = userEvent.setup();
  await renderPanel();
  await user.click(tab('zsh'));
  tab('zsh').focus();
  await user.keyboard('{ArrowRight}');
  expect(tab('pnpm dev')).toHaveFocus();
  expect(tab('pnpm dev')).toHaveAttribute('aria-selected', 'true');
  await user.keyboard('{End}');
  expect(tab('logs')).toHaveFocus();
  await user.keyboard('{ArrowRight}');
  expect(tab('zsh')).toHaveFocus();
  await user.keyboard('{ArrowLeft}');
  expect(tab('logs')).toHaveFocus();
  await user.keyboard('{Home}');
  expect(tab('zsh')).toHaveFocus();
});

it('opens a new terminal with the plus button, selects it and focuses it', async () => {
  const user = userEvent.setup();
  await renderPanel();
  await user.click(screen.getByRole('button', {name: 'New terminal'}));
  const created = await screen.findByRole('tab', {name: 'Project A'});
  expect(created).toHaveAttribute('aria-selected', 'true');
  expect(mock.gql).toHaveBeenCalledWith(expect.stringContaining('create_session'), {input: {cwd: '/project-a', sandbox_profile: 'workspace', title: 'Project A'}});
  await waitFor(() => expect(Socket.items.at(-1)?.url).toContain('/api/sessions/pty-new/pty'));
  await waitFor(() => expect(mock.instances.at(-1).focus).toHaveBeenCalled());
});

it('creates a workspace-scoped terminal through the workspace endpoint', async () => {
  const user = userEvent.setup();
  mock.request.mockResolvedValue({id: 'pty-ws', title: 'ws shell', cwd: '/project-a', exited: false});
  render(<TerminalPanel project={project} workspaceSession="conversation-a" onError={vi.fn()} />);
  await screen.findByRole('tab', {name: 'zsh'});
  await user.click(screen.getByRole('button', {name: 'New terminal'}));
  expect(await screen.findByRole('tab', {name: 'ws shell'})).toHaveAttribute('aria-selected', 'true');
  expect(mock.request).toHaveBeenCalledWith('/api/workspace/sessions/conversation-a/terminal', {method: 'POST'});
});

it('closes a tab, ends its PTY, disposes its xterm and selects a neighbour', async () => {
  const user = userEvent.setup();
  await renderPanel();
  await user.click(tab('pnpm dev'));
  await waitFor(() => expect(Socket.items).toHaveLength(1));
  const instance = mock.instances[0];
  await user.click(closeMark('pty-2'));
  await waitFor(() => expect(screen.queryByRole('tab', {name: 'pnpm dev'})).toBeNull());
  expect(mock.gql).toHaveBeenCalledWith(expect.stringContaining('delete_session'), {id: 'pty-2'});
  expect(instance.dispose).toHaveBeenCalledOnce();
  expect(Socket.items[0].close).toHaveBeenCalled();
  expect(screen.getAllByRole('tab').map((node) => node.textContent)).toEqual(['zsh', 'logs']);
  expect(tab('logs')).toHaveAttribute('aria-selected', 'true');
});

it('closing an unselected tab leaves the selection alone, and the last tab leaves none', async () => {
  const user = userEvent.setup();
  inventory = rows.slice(0, 2);
  await renderPanel();
  await user.click(tab('zsh'));
  await user.click(closeMark('pty-2'));
  await waitFor(() => expect(screen.queryByRole('tab', {name: 'pnpm dev'})).toBeNull());
  expect(tab('zsh')).toHaveAttribute('aria-selected', 'true');
  await user.click(screen.getByRole('button', {name: 'Close terminal'}));
  await waitFor(() => expect(screen.queryAllByRole('tab')).toHaveLength(0));
  expect(screen.getByText(/No terminals are running/)).toBeVisible();
});

it('reports a failed close and keeps the tab', async () => {
  const user = userEvent.setup();
  const onError = vi.fn();
  mock.gql.mockImplementation(async (query: string) => { if (query.includes('delete_session')) throw new Error('not allowed'); return {sessions: rows}; });
  render(<TerminalPanel project={project} onError={onError} />);
  await screen.findByRole('tab', {name: 'zsh'});
  await user.click(closeMark('pty-1'));
  await waitFor(() => expect(onError).toHaveBeenCalledWith(expect.objectContaining({message: 'not allowed'})));
  expect(tab('zsh')).toBeInTheDocument();
});

it('numbers tabs that share a title', async () => {
  inventory = [rows[0], {...rows[0], id: 'pty-9'}];
  await renderPanel('zsh 1');
  expect(screen.getAllByRole('tab').map((node) => node.textContent)).toEqual(['zsh 1', 'zsh 2']);
});

it('opens find with Command-F inside the terminal, searches, and closes with Escape', async () => {
  const user = userEvent.setup();
  await renderPanel();
  await user.click(tab('zsh'));
  await waitFor(() => expect(mock.keyHandlers).toHaveLength(1));
  const preventDefault = vi.fn();
  let allowed = true;
  act(() => { allowed = mock.keyHandlers[0]({type: 'keydown', key: 'f', metaKey: true, ctrlKey: false, shiftKey: false, altKey: false, preventDefault}); });
  expect(allowed).toBe(false);
  expect(preventDefault).toHaveBeenCalled();
  const input = await screen.findByRole('textbox', {name: 'Find in terminal'});
  await waitFor(() => expect(input).toHaveFocus());
  await user.type(input, 'error');
  expect(mock.searches[0].findNext).toHaveBeenLastCalledWith('error', expect.objectContaining({incremental: true}));
  await user.keyboard('{Enter}');
  expect(mock.searches[0].findNext).toHaveBeenCalledTimes(6);
  await user.keyboard('{Shift>}{Enter}{/Shift}');
  expect(mock.searches[0].findPrevious).toHaveBeenCalledWith('error', expect.anything());
  await user.keyboard('{Escape}');
  expect(screen.queryByRole('textbox', {name: 'Find in terminal'})).toBeNull();
  expect(mock.searches[0].clearDecorations).toHaveBeenCalled();
  expect(mock.instances[0].focus).toHaveBeenCalled();
});

it('leaves plain Control-F, Tab and other keys to the shell', async () => {
  const user = userEvent.setup();
  await renderPanel();
  await user.click(tab('zsh'));
  await waitFor(() => expect(mock.keyHandlers).toHaveLength(1));
  expect(mock.keyHandlers[0]({type: 'keydown', key: 'f', metaKey: false, ctrlKey: true, shiftKey: false, altKey: false, preventDefault() {}})).toBe(true);
  expect(mock.keyHandlers[0]({type: 'keydown', key: 'Tab', metaKey: false, ctrlKey: false, shiftKey: false, altKey: false, preventDefault() {}})).toBe(true);
});

it('returns from the terminal to the active tab with Shift+Tab', async () => {
  const user = userEvent.setup();
  await renderPanel();
  await user.click(tab('pnpm dev'));
  await waitFor(() => expect(mock.keyHandlers).toHaveLength(1));
  act(() => { mock.keyHandlers[0]({type: 'keydown', key: 'Tab', shiftKey: true, metaKey: false, ctrlKey: false, altKey: false, preventDefault() {}}); });
  expect(tab('pnpm dev')).toHaveFocus();
});

it('opens a printed web link only on Command-click and never a script URL', async () => {
  const open = vi.fn(() => null);
  vi.stubGlobal('open', open);
  const user = userEvent.setup();
  await renderPanel();
  await user.click(tab('zsh'));
  await waitFor(() => expect(mock.linkHandlers).toHaveLength(1));
  mock.linkHandlers[0]({metaKey: false, ctrlKey: false}, 'https://example.com');
  expect(open).not.toHaveBeenCalled();
  mock.linkHandlers[0]({metaKey: true, ctrlKey: false}, 'https://example.com/a');
  expect(open).toHaveBeenCalledWith('https://example.com/a', '_blank', 'noopener,noreferrer');
  mock.linkHandlers[0]({metaKey: true, ctrlKey: false}, 'javascript:alert(1)');
  expect(open).toHaveBeenCalledOnce();
});

it('sends interrupt to the selected session only', async () => {
  const user = userEvent.setup();
  await renderPanel();
  await user.click(tab('zsh'));
  await waitFor(() => expect(Socket.items).toHaveLength(1));
  act(() => Socket.items[0].onopen());
  await user.click(tab('pnpm dev'));
  await waitFor(() => expect(Socket.items).toHaveLength(2));
  act(() => Socket.items[1].onopen());
  await user.click(screen.getByRole('button', {name: 'Interrupt'}));
  expect(Socket.items[1].send).toHaveBeenCalledWith(JSON.stringify({type: 'signal', name: 'int'}));
  expect(Socket.items[0].send.mock.calls.map(([data]) => JSON.parse(data).type)).not.toContain('signal');
});

it('shows the connection state of the selected tab', async () => {
  const user = userEvent.setup();
  await renderPanel();
  await user.click(tab('zsh'));
  await waitFor(() => expect(Socket.items).toHaveLength(1));
  expect(screen.getByText('Connecting')).toBeVisible();
  act(() => Socket.items[0].onopen());
  expect(screen.getByText('Connected')).toBeVisible();
  expect(screen.getByRole('region', {name: 'Terminal'})).toHaveAttribute('data-connected-session', 'pty-1');
  await user.click(tab('pnpm dev'));
  await waitFor(() => expect(Socket.items).toHaveLength(2));
  expect(screen.getByRole('region', {name: 'Terminal'})).toHaveAttribute('data-connected-session', '');
});

it('has no axe violations with tabs, a hidden session and the find bar open', async () => {
  const user = userEvent.setup();
  const {container} = await renderPanel();
  await user.click(tab('zsh'));
  await user.click(tab('pnpm dev'));
  await waitFor(() => expect(mock.keyHandlers).toHaveLength(2));
  act(() => { mock.keyHandlers[1]({type: 'keydown', key: 'f', metaKey: true, ctrlKey: false, shiftKey: false, altKey: false, preventDefault() {}}); });
  await screen.findByRole('textbox', {name: 'Find in terminal'});
  await expectNoAxeViolations(container);
});

it('says why there is no terminal without a project', async () => {
  render(<TerminalPanel project={null} onError={vi.fn()} />);
  expect(await screen.findByText(/Terminals run inside a project folder/)).toBeVisible();
  expect(screen.getByRole('button', {name: 'New terminal'})).toBeDisabled();
  expect(within(screen.getByRole('region', {name: 'Terminal'})).getByText('No project open')).toBeVisible();
});

it('closes the selected terminal from the keyboard-reachable toolbar button', async () => {
  const user = userEvent.setup();
  await renderPanel();
  await user.click(tab('pnpm dev'));
  expect(screen.getByRole('button', {name: 'Close terminal'})).toBeEnabled();
  await user.click(screen.getByRole('button', {name: 'Close terminal'}));
  await waitFor(() => expect(screen.queryByRole('tab', {name: 'pnpm dev'})).toBeNull());
  expect(mock.gql).toHaveBeenCalledWith(expect.stringContaining('delete_session'), {id: 'pty-2'});
});

it('does not offer to close a terminal when none is selected', async () => {
  await renderPanel();
  expect(screen.queryByRole('button', {name: 'Close terminal'})).toBeNull();
});

it('does not select the tab it is closing when the close mark is pressed', async () => {
  const user = userEvent.setup();
  await renderPanel();
  await user.click(tab('zsh'));
  await user.click(closeMark('pty-3'));
  await waitFor(() => expect(screen.queryByRole('tab', {name: 'logs'})).toBeNull());
  expect(tab('zsh')).toHaveAttribute('aria-selected', 'true');
});
