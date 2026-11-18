import {cleanup, render, screen, waitFor, within} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest';
import {gql} from '../lib/api';
import {
  blockedNote,
  boardChanges,
  canCancel,
  canReassign,
  canResumeSession,
  canRetry,
  cliSessionOf,
  executorChip,
  rollupCounts,
  rollupHeadline,
  type Board,
  type BoardNode,
} from '../lib/orchestrator-board';
import {expectNoAxeViolations} from '../test-axe';
import {OpenCliSessionContext} from './cli/open-cli-context';
import TaskBoard from './TaskBoard';

vi.mock('../lib/api', () => ({gql: vi.fn()}));
const api = vi.mocked(gql);

const now = () => Date.now() / 1000;
const node = (over: Partial<BoardNode>): BoardNode => ({
  id: 'n',
  title: 'Task',
  role: 'worker',
  depends_on: [],
  blocked_by: [],
  task_id: null,
  status: 'pending',
  attempts: 0,
  max_attempts: 2,
  last_event_at: null,
  result_summary: '',
  error: '',
  provider_id: null,
  model: null,
  tokens: 0,
  cost_usd: null,
  created_at: now() - 600,
  updated_at: now() - 60,
  ...over,
});
const board = (nodes: BoardNode[]): Board => ({
  nodes,
  rollup: {
    total: nodes.length,
    counts: nodes.reduce<Record<string, number>>((all, row) => ({...all, [row.status]: (all[row.status] || 0) + 1}), {}),
    percent_complete: Math.round((100 * nodes.filter(row => row.status === 'completed').length) / (nodes.length || 1)),
    tokens: 0,
    cost_usd: null,
  },
});

const internal = node({id: 'i', title: 'Summarise the repo', status: 'running', provider_id: 'p1', model: 'model-a'});
const engine = node({id: 'e', title: 'Refactor the parser', status: 'running', executor: {kind: 'engine', engine: 'codex', mode: 'agent'}});
const cliRunning = node({id: 'c1', title: 'Fix flaky test', status: 'running', executor: {kind: 'cli', cli: 'claude', mode: 'default', isolation: 'worktree'}, session_id: 'cli-1'});
const approval = node({id: 'c2', title: 'Update the schema', status: 'blocked', blocked_reason: 'needs_approval', executor: {kind: 'cli', cli: 'claude', mode: 'default', isolation: 'worktree'}, session_id: 'cli-2'});
const lost = node({id: 'c3', title: 'Port the CLI', status: 'blocked', blocked_reason: 'lost', resumable: true, executor: {kind: 'cli', cli: 'codex'}, session_id: 'cli-3'});

describe('board executors and the Blocked state', () => {
  it('names who does a node\'s work', () => {
    expect(executorChip(internal)).toEqual({label: 'Agent', title: 'Runs as an Internal Agent task'});
    expect(executorChip(engine).label).toBe('Engine: codex');
    expect(executorChip(cliRunning).label).toBe('CLI: claude');
    expect(executorChip(cliRunning).title).toContain('default, worktree');
    expect(executorChip(node({executor: null})).label).toBe('Agent');
  });

  it('counts blocked tasks with the ones that need attention', () => {
    const value = board([internal, approval, lost]);
    expect(rollupCounts(value.rollup).map(item => item.label + item.count)).toEqual(['Running1', 'Blocked2']);
    expect(rollupHeadline(value.rollup)).toBe('2 planned tasks need attention');
  });

  it('lets only a lost, resumable node resume, and only internal nodes be reassigned', () => {
    expect(canResumeSession(lost)).toBe(true);
    expect(canResumeSession(approval)).toBe(false);
    expect(canRetry(lost)).toBe(true);
    expect(canRetry(approval)).toBe(false);
    expect(canCancel(approval)).toBe(true);
    expect(canReassign(node({status: 'failed'}))).toBe(true);
    expect(canReassign(node({status: 'failed', executor: {kind: 'cli', cli: 'claude'}}))).toBe(false);
    expect(canReassign(node({status: 'failed', executor: {kind: 'engine', engine: 'codex'}}))).toBe(false);
    expect(cliSessionOf(cliRunning)).toBe('cli-1');
    expect(cliSessionOf(engine)).toBeNull();
  });

  it('says why a node is blocked, and announces a new block', () => {
    expect(blockedNote(approval)).toMatch(/needs your approval/);
    expect(blockedNote(lost)).toMatch(/Resume it/);
    expect(blockedNote(internal)).toBe('');
    const before = board([cliRunning]);
    const after = board([{...cliRunning, status: 'blocked', blocked_reason: 'waiting_input'}]);
    expect(boardChanges(before, after)).toEqual(['“Fix flaky test” is blocked: waiting for input.']);
    expect(boardChanges(after, after)).toEqual([]);
  });
});

describe('TaskBoard with executors', () => {
  let current: Board;
  const openCli = vi.fn();
  const calls = (word: string) => api.mock.calls.filter(([query]) => query.includes(word));
  beforeEach(() => {
    current = board([internal, engine, cliRunning, approval, lost]);
    openCli.mockReset();
    api.mockReset();
    api.mockImplementation(async (query: string) => {
      if (query.includes('retry_node')) return {retry_node: current};
      if (query.includes('cancel_node')) return {cancel_node: current};
      return {orchestrator_board: current};
    });
  });
  afterEach(cleanup);

  const renderBoard = () =>
    render(
      <OpenCliSessionContext.Provider value={openCli}>
        <TaskBoard enabled />
      </OpenCliSessionContext.Provider>,
    );
  const row = (title: string) => screen.getByRole('listitem', {name: title});

  it('shows an executor chip on every row', async () => {
    renderBoard();
    await screen.findByRole('list', {name: 'Tasks'});
    expect(within(row('Summarise the repo')).getByText('Agent')).toBeInTheDocument();
    expect(within(row('Refactor the parser')).getByText('Engine: codex')).toBeInTheDocument();
    expect(within(row('Fix flaky test')).getByText('CLI: claude')).toBeInTheDocument();
  });

  it('shows Blocked with its reason in words and a way to open the session', async () => {
    const user = userEvent.setup();
    renderBoard();
    await screen.findByRole('list', {name: 'Tasks'});
    const blocked = row('Update the schema');
    expect(within(blocked).getByText('Blocked · needs approval')).toBeInTheDocument();
    expect(within(blocked).getByText(/needs your approval/)).toBeInTheDocument();
    await user.click(within(blocked).getByRole('button', {name: 'Open CLI session'}));
    expect(openCli).toHaveBeenCalledWith('cli-2');
  });

  it('offers Open CLI session, Resume session and Cancel in the row menu, using the existing mutations', async () => {
    const user = userEvent.setup();
    renderBoard();
    await screen.findByRole('list', {name: 'Tasks'});
    await user.click(within(row('Port the CLI')).getByRole('button', {name: 'Actions for Port the CLI'}));
    const items = screen.getAllByRole('menuitem').map(item => item.textContent);
    expect(items).toEqual(['Open CLI session', 'Resume session', 'Cancel task']);
    await user.click(screen.getByRole('menuitem', {name: 'Resume session'}));
    await waitFor(() => expect(calls('retry_node')).toHaveLength(1));
    expect(calls('retry_node')[0][1]).toEqual({id: 'c3'});
  });

  it('cancels a blocked node', async () => {
    const user = userEvent.setup();
    renderBoard();
    await screen.findByRole('list', {name: 'Tasks'});
    await user.click(within(row('Update the schema')).getByRole('button', {name: 'Actions for Update the schema'}));
    expect(screen.queryByRole('menuitem', {name: /Retry|Resume/})).not.toBeInTheDocument();
    await user.click(screen.getByRole('menuitem', {name: 'Cancel task'}));
    await waitFor(() => expect(calls('cancel_node')).toHaveLength(1));
  });

  it('does not offer to reassign an engine or CLI node, and hides Open CLI session without a CLI tab', async () => {
    const user = userEvent.setup();
    render(<TaskBoard enabled />);
    await screen.findByRole('list', {name: 'Tasks'});
    await user.click(within(row('Fix flaky test')).getByRole('button', {name: 'Actions for Fix flaky test'}));
    expect(screen.getByRole('menu').textContent).toBe('Cancel task');
    expect(within(row('Update the schema')).queryByRole('button', {name: 'Open CLI session'})).not.toBeInTheDocument();
  });

  it('has no axe violations', async () => {
    const {container} = renderBoard();
    await screen.findByRole('list', {name: 'Tasks'});
    await expectNoAxeViolations(container);
  });
});
