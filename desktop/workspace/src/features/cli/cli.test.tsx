import {act, cleanup, render, screen, waitFor, within} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest';
import {request} from '../../lib/api';
import type {CliAgent, CliSession, CliSessionEvent} from '../../lib/cli-sessions';
import CliSessionsSection from '../../shell/views/CliSessionsSection';
import {expectNoAxeViolations} from '../../test-axe';
import CliStatusBadge from './CliStatusBadge';
import CliTab from './CliTab';
import {CliSessionsProvider} from './CliSessionsProvider';
import NewCliSessionDialog from './NewCliSessionDialog';

vi.mock('../../lib/api', () => ({request: vi.fn(), json: (method: string, data?: unknown) => ({method, body: data === undefined ? undefined : JSON.stringify(data)})}));

let emit: (event: CliSessionEvent) => void = () => {};
vi.mock('../../lib/graphql-subscription', () => ({
  useGraphqlSubscription: (options: {onEvent: (event: CliSessionEvent) => void}) => {
    emit = options.onEvent;
  },
}));

const terminal = vi.hoisted(() => ({focus: vi.fn(), props: [] as {id: string; authorized: boolean}[]}));
vi.mock('../terminal/TerminalSession', async () => {
  const React = await import('react');
  return {
    default: React.forwardRef(function Fake(props: {id: string; authorized: boolean}, ref: React.Ref<unknown>) {
      terminal.props.push({id: props.id, authorized: props.authorized});
      React.useImperativeHandle(ref, () => ({focus: terminal.focus, interrupt: vi.fn()}));
      return <div data-testid="terminal" data-pty={props.id} />;
    }),
  };
});

const api = vi.mocked(request);

const session = (over: Partial<CliSession>): CliSession => ({
  id: 'c1',
  cli: 'claude',
  label: 'Claude Code',
  title: 'Claude Code · repo',
  status: 'working',
  status_source: 'hook',
  status_detail: 'hook:UserPromptSubmit',
  status_updated_at: 1,
  attention: false,
  project_id: 'p1',
  cwd: '/work/repo',
  isolation: 'none',
  worktree: null,
  pty_session_id: 'pty-1',
  mode: 'default',
  started_at: 1,
  updated_at: 1,
  ended_at: null,
  exit_code: null,
  ended_reason: null,
  resumable: false,
  resumed_from: null,
  resumed_to: null,
  ...over,
});

const agents: CliAgent[] = [
  {id: 'claude', label: 'Claude Code', installed: true, version: '2.1.326 (Claude Code)', hooks: 'claude-settings', resume: true, modes: ['bypass', 'default', 'read_only'], verified: true, note: ''},
  {id: 'codex', label: 'Codex', installed: true, version: '0.169.0', hooks: 'notify', resume: true, modes: ['default', 'read_only'], verified: true, note: ''},
  {id: 'gemini', label: 'Gemini', installed: false, version: null, hooks: 'none', resume: false, modes: ['default'], verified: false, note: ''},
];

let rows: CliSession[];
const calls = (method: string, path: string) => api.mock.calls.filter(([url, init]) => url === path && (init?.method ?? 'GET') === method);

beforeEach(() => {
  rows = [];
  terminal.focus.mockReset();
  terminal.props.length = 0;
  api.mockReset();
  api.mockImplementation(async (path: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET';
    if (path === '/api/cli-sessions' && method === 'GET') return {sessions: rows};
    if (path === '/api/cli-agents') return {agents};
    if (path === '/api/cli-sessions' && method === 'POST') return session({id: 'new', status: 'starting'});
    if (/\/stop$/.test(path) || /\/interrupt$/.test(path)) return {};
    if (/\/resume$/.test(path)) return session({id: 'resumed', status: 'starting', resumed_from: 'c1'});
    return {};
  });
});
afterEach(cleanup);

const wrap = (node: React.ReactNode) => render(<CliSessionsProvider enabled>{node}</CliSessionsProvider>);

describe('CliStatusBadge', () => {
  it('always has the words, an icon, and a tone that is never the only signal', () => {
    const {container} = render(
      <>
        {['working', 'waiting_input', 'needs_approval', 'done', 'failed', 'lost'].map(status => (
          <CliStatusBadge key={status} status={status} />
        ))}
      </>,
    );
    for (const label of ['Working', 'Waiting for input', 'Needs approval', 'Done', 'Failed', 'Lost']) expect(screen.getByText(label)).toBeVisible();
    const badges = [...container.querySelectorAll('.cli-status')];
    expect(badges).toHaveLength(6);
    for (const badge of badges) expect(badge.querySelector('svg')).not.toBeNull();
    expect(container.querySelector('[data-status="working"] svg')).toHaveClass('cli-spin');
    expect(container.querySelector('[data-status="needs_approval"]')).toHaveAttribute('data-tone', 'attention');
    expect(container.querySelector('[data-status="done"]')).toHaveAttribute('data-tone', 'ok');
    expect(container.querySelector('[data-status="failed"]')).toHaveAttribute('data-tone', 'danger');
  });

  it('keeps the words for a screen reader when compact', () => {
    render(<CliStatusBadge status="needs_approval" compact />);
    expect(screen.getByText('Needs approval')).toHaveClass('visually-hidden');
  });

  it('has no axe violations', async () => {
    const {container} = render(<CliStatusBadge status="needs_approval" />);
    await expectNoAxeViolations(container);
  });
});

describe('CLI sessions list', () => {
  it('lists sessions that need the owner first, with status, place and a count in the heading', async () => {
    rows = [
      session({id: 'a', title: 'Codex · api', cli: 'codex', status: 'working', updated_at: 50}),
      session({id: 'b', title: 'Claude Code · app', status: 'needs_approval', attention: true, updated_at: 5, worktree: {branch: 'termx/cli-b'}}),
      session({id: 'c', title: 'Claude Code · old', status: 'done', updated_at: 99}),
    ];
    const onOpen = vi.fn();
    const user = userEvent.setup();
    wrap(<CliSessionsSection onNew={vi.fn()} onOpen={onOpen} />);
    const list = await screen.findByRole('list', {name: 'CLI sessions'});
    const items = within(list).getAllByRole('button');
    expect(items.map(item => item.textContent)).toEqual([
      expect.stringContaining('Claude Code · app'),
      expect.stringContaining('Codex · api'),
      expect.stringContaining('Claude Code · old'),
    ]);
    expect(items[0]).toHaveTextContent('termx/cli-b');
    expect(items[0]).toHaveTextContent('Needs approval');
    expect(screen.getByRole('heading', {name: /CLI sessions/})).toHaveTextContent('1 needs you');
    await user.click(items[0]);
    expect(onOpen).toHaveBeenCalledWith('b');
  });

  it('follows the subscription: a status change reorders the list', async () => {
    rows = [session({id: 'a', title: 'A', status: 'working', updated_at: 5}), session({id: 'b', title: 'B', status: 'working', updated_at: 1})];
    wrap(<CliSessionsSection onNew={vi.fn()} onOpen={vi.fn()} />);
    const list = await screen.findByRole('list', {name: 'CLI sessions'});
    expect(within(list).getAllByRole('button')[0]).toHaveTextContent('A');
    act(() => emit({type: 'cli.session.status', session: session({id: 'b', title: 'B', status: 'needs_approval', attention: true})}));
    await waitFor(() => expect(within(list).getAllByRole('button')[0]).toHaveTextContent('B'));
    expect(within(list).getAllByRole('button')[0]).toHaveTextContent('Needs approval');
  });

  it('says there are none, offers New CLI session as a ghost button, and hides itself without access', async () => {
    const user = userEvent.setup();
    const onNew = vi.fn();
    const first = wrap(<CliSessionsSection onNew={onNew} onOpen={vi.fn()} />);
    expect(await screen.findByText(/CLI sessions on this host will appear here/)).toBeInTheDocument();
    const start = screen.getByRole('button', {name: 'New CLI session'});
    expect(start).toHaveClass('button-ghost');
    await user.click(start);
    expect(onNew).toHaveBeenCalled();
    first.unmount();
    api.mockImplementation(async () => {
      throw new Error('missing terminal authority (403)');
    });
    wrap(<CliSessionsSection onNew={onNew} onOpen={vi.fn()} />);
    await waitFor(() => expect(screen.queryByRole('heading', {name: /CLI sessions/})).not.toBeInTheDocument());
  });

  it('has no axe violations', async () => {
    rows = [session({id: 'b', status: 'needs_approval', attention: true}), session({id: 'c', status: 'lost'})];
    const {container} = wrap(<CliSessionsSection onNew={vi.fn()} onOpen={vi.fn()} />);
    await screen.findByRole('list', {name: 'CLI sessions'});
    await expectNoAxeViolations(container);
  });
});

describe('New CLI session dialog', () => {
  const projects = [
    {id: 'p1', path: '/work/repo', name: 'repo'},
    {id: 'p2', path: '/work/api', name: 'api'},
  ];

  function open(onCreated = vi.fn()) {
    const view = wrap(<NewCliSessionDialog open onOpenChange={vi.fn()} projects={projects} projectId="p1" onCreated={onCreated} />);
    return {onCreated, ...view};
  }

  it('lists installed CLIs with their version and disables one that is missing, with the reason', async () => {
    open();
    const select = await screen.findByLabelText('CLI');
    await waitFor(() => expect(select).toBeEnabled());
    const options = within(select).getAllByRole('option');
    expect(options.map(option => option.textContent)).toEqual(['Claude Code 2.1.326', 'Codex 0.169.0', 'Gemini (not installed)']);
    expect(options[2]).toBeDisabled();
    expect(screen.getByText('Gemini is not installed on this host.')).toBeInTheDocument();
  });

  it('never offers permission bypass', async () => {
    open();
    const permissions = await screen.findByLabelText('Permissions');
    expect(within(permissions).getAllByRole('option').map(option => (option as HTMLOptionElement).value)).toEqual(['default', 'read_only']);
    expect(screen.queryByText(/bypass|yolo|dangerously/i)).not.toBeInTheDocument();
  });

  it('starts a worktree session with its prompt and hands the new session to the shell', async () => {
    const user = userEvent.setup();
    const {onCreated} = open();
    await waitFor(() => expect(screen.getByLabelText('CLI')).toBeEnabled());
    await user.selectOptions(screen.getByLabelText('CLI'), 'codex');
    await user.selectOptions(screen.getByLabelText('Project'), 'p2');
    await user.type(screen.getByLabelText(/First instruction/), 'Fix the flaky test');
    await user.selectOptions(screen.getByLabelText('Where it works'), 'worktree');
    await user.selectOptions(screen.getByLabelText('Permissions'), 'read_only');
    expect(screen.getByRole('button', {name: 'Start session'})).toHaveClass('button-primary');
    await user.click(screen.getByRole('button', {name: 'Start session'}));
    await waitFor(() => expect(onCreated).toHaveBeenCalledWith(expect.objectContaining({id: 'new'})));
    expect(JSON.parse(String(calls('POST', '/api/cli-sessions')[0][1]?.body))).toEqual({
      cli: 'codex',
      project_id: 'p2',
      prompt: 'Fix the flaky test',
      isolation: 'worktree',
      mode: 'read_only',
    });
  });

  it('shows the host\'s refusal in place', async () => {
    const user = userEvent.setup();
    open();
    await waitFor(() => expect(screen.getByLabelText('CLI')).toBeEnabled());
    const original = api.getMockImplementation()!;
    api.mockImplementation(async (path, init) => {
      if (path === '/api/cli-sessions' && init?.method === 'POST') throw new Error('Claude Code is not installed.');
      return original(path, init);
    });
    await user.click(screen.getByRole('button', {name: 'Start session'}));
    expect(await screen.findByText('Claude Code is not installed.')).toHaveAttribute('role', 'alert');
  });

  it('has no axe violations', async () => {
    open();
    await waitFor(() => expect(screen.getByLabelText('CLI')).toBeEnabled());
    await expectNoAxeViolations(document.body);
  });
});

describe('CLI tab', () => {
  const tab = (extra: Partial<React.ComponentProps<typeof CliTab>> = {}) => {
    const props = {sessionId: 'c1', tabDomId: 'tab-x', visible: true, ownerId: 'owner', locked: false, onClose: vi.fn(), onOpenSession: vi.fn(), onError: vi.fn(), ...extra};
    return {props, ...wrap(<CliTab {...props} />)};
  };

  it('streams the session\'s own terminal and shows status, Interrupt and Stop', async () => {
    rows = [session({})];
    const user = userEvent.setup();
    tab();
    expect(await screen.findByTestId('terminal')).toHaveAttribute('data-pty', 'pty-1');
    const header = screen.getByRole('banner');
    expect(within(header).getByText('Working')).toBeInTheDocument();
    expect(within(header).getByText('hook:UserPromptSubmit')).toBeInTheDocument();
    await user.click(within(header).getByRole('button', {name: 'Interrupt'}));
    await waitFor(() => expect(calls('POST', '/api/cli-sessions/c1/interrupt')).toHaveLength(1));
    expect(within(header).queryByRole('button', {name: 'Resume'})).not.toBeInTheDocument();
  });

  it('asks before stopping, and does not stop on cancel or on closing the tab', async () => {
    rows = [session({})];
    const user = userEvent.setup();
    const {props, unmount} = tab();
    await screen.findByTestId('terminal');
    await user.click(screen.getByRole('button', {name: 'Stop'}));
    const dialog = await screen.findByRole('dialog', {name: 'Stop this session?'});
    await user.click(within(dialog).getByRole('button', {name: 'Cancel'}));
    expect(calls('POST', '/api/cli-sessions/c1/stop')).toHaveLength(0);
    unmount();
    expect(calls('POST', '/api/cli-sessions/c1/stop')).toHaveLength(0);
    expect(props.onClose).not.toHaveBeenCalled();
  });

  it('stops after confirmation', async () => {
    rows = [session({})];
    const user = userEvent.setup();
    tab();
    await screen.findByTestId('terminal');
    await user.click(screen.getByRole('button', {name: 'Stop'}));
    await user.click(within(await screen.findByRole('dialog')).getByRole('button', {name: 'Stop session'}));
    await waitFor(() => expect(calls('POST', '/api/cli-sessions/c1/stop')).toHaveLength(1));
  });

  it('hints at the terminal when it needs approval, offers Focus terminal and never answers for the user', async () => {
    rows = [session({status: 'needs_approval', attention: true, status_detail: 'heuristic:trust folder', worktree: {branch: 'termx/cli-c1'}, isolation: 'worktree'})];
    const user = userEvent.setup();
    tab();
    await screen.findByTestId('terminal');
    const hint = screen.getByText(/waiting for a decision in its terminal/);
    expect(hint).toBeVisible();
    await user.click(screen.getByRole('button', {name: 'Focus terminal'}));
    expect(terminal.focus).toHaveBeenCalled();
    expect(api.mock.calls.some(([url]) => /\/input$/.test(String(url)))).toBe(false);
  });

  it('updates the badge and swaps the terminal for the end state when the session ends', async () => {
    rows = [session({})];
    tab();
    await screen.findByTestId('terminal');
    act(() => emit({type: 'cli.session.exited', session: session({status: 'done', exit_code: 0, ended_reason: 'exited', ended_at: 2, attention: false})}));
    await waitFor(() => expect(screen.queryByTestId('terminal')).not.toBeInTheDocument());
    expect(within(screen.getByRole('banner')).getByText('Done')).toBeInTheDocument();
    expect(screen.getByText('This session exited with exit code 0.')).toBeInTheDocument();
    expect(screen.queryByRole('button', {name: 'Stop'})).not.toBeInTheDocument();
  });

  it('resumes a lost, resumable session after confirmation and opens the new one', async () => {
    rows = [session({status: 'lost', resumable: true, ended_reason: 'lost', attention: false})];
    const user = userEvent.setup();
    const {props} = tab();
    await screen.findByText('This session was lost: lost.');
    await user.click(screen.getByRole('button', {name: 'Resume'}));
    const dialog = await screen.findByRole('dialog', {name: 'Resume this session?'});
    expect(within(dialog).getByText(/ask to trust the folder again/)).toBeInTheDocument();
    expect(calls('POST', '/api/cli-sessions/c1/resume')).toHaveLength(0);
    await user.click(within(dialog).getByRole('button', {name: 'Resume session'}));
    await waitFor(() => expect(props.onOpenSession).toHaveBeenCalledWith('resumed'));
  });

  it('offers no Resume for a finished session that cannot resume, and says when the session is not on this host', async () => {
    rows = [session({status: 'done', resumable: false})];
    const first = tab();
    await screen.findByText(/This session/);
    expect(screen.queryByRole('button', {name: 'Resume'})).not.toBeInTheDocument();
    first.unmount();
    rows = [];
    tab({sessionId: 'missing'});
    expect(await screen.findByText('This CLI session is not on this host.')).toBeInTheDocument();
  });

  it('does not connect the terminal while the workspace is locked', async () => {
    rows = [session({})];
    tab({locked: true});
    await screen.findByTestId('terminal');
    expect(terminal.props.at(-1)).toEqual({id: 'pty-1', authorized: false});
  });

  it('has no axe violations', async () => {
    rows = [session({status: 'needs_approval', attention: true})];
    const {container} = tab();
    await screen.findByTestId('terminal');
    await expectNoAxeViolations(container);
  });
});
