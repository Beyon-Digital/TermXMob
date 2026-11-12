import {afterEach, beforeEach, expect, it, vi} from 'vitest';
import {act, cleanup, fireEvent, render, screen, waitFor, within} from '@testing-library/react';
import OrchestratorStatus, {type OrchestratorChild, type OrchestratorSnapshot} from './OrchestratorStatus';
import CoordinationSidebar, {subagentAge} from './CoordinationSidebar';
import {gql, request, type Session, type Task} from '../lib/api';
import {expectNoAxeViolations} from '../test-axe';

vi.mock('../lib/api', () => ({gql: vi.fn(), request: vi.fn(), json: (method: string, data: unknown) => ({method, body: JSON.stringify(data)})}));
const api = vi.mocked(request), board = vi.mocked(gql), open = vi.fn();
const now = 1800000000;
const emptyBoard = {nodes: [], rollup: {total: 0, counts: {}, percent_complete: 0, tokens: 0, cost_usd: null}};
const child = (id: string, status = 'completed', extra: Partial<OrchestratorChild> = {}): OrchestratorChild => ({
  session_id: id, task_id: 'task-' + id, title: 'Agent ' + id, status, engine: 'codex', origin: 'delegated',
  capability_limits: {}, control_owner: 'orchestrator', revision: 3, updated_at: now - 3600, parent_session_id: 'main', ...extra,
});
const snapshot = (children: OrchestratorChild[] = []): OrchestratorSnapshot => ({session: {id: 'main', title: 'Main agent'} as Session, children, activity: [], cursor: 1});
let aside: HTMLElement, target: HTMLDivElement;
const props = {ownerId: 'owner', selected: 'main', enabled: true, onOpen: open, presentation: 'sidebar' as const};
const coordinatorReads = () => api.mock.calls.filter(([path]) => path === '/api/workspace/orchestrator');
const writes = () => api.mock.calls.filter(([, init]) => init?.method && !['GET', 'HEAD'].includes(init.method));
function response(value: OrchestratorSnapshot) {
  api.mockImplementation(async path => {
    if (path === '/api/workspace/orchestrator') return value;
    return {id: String(path).split('/tasks/')[1]?.split('?')[0], status: 'completed', prompt: '', approvals: []};
  });
}
beforeEach(() => {
  api.mockReset(); board.mockReset().mockResolvedValue({orchestrator_board: emptyBoard}); open.mockReset();
  vi.spyOn(Date, 'now').mockReturnValue(now * 1000);
  aside = document.createElement('aside'); aside.setAttribute('aria-label', 'Subagents');
  target = document.createElement('div'); aside.append(target); document.body.append(aside); response(snapshot());
});
afterEach(() => {cleanup(); aside.remove(); vi.restoreAllMocks();});

it('groups actual active and terminal states, retains failure labels, and opens the exact child without mutations', async () => {
  response(snapshot([child('running', 'running'), child('approval', 'awaiting_approval'), child('complete'), child('failed', 'failed'), child('cancelled', 'cancelled')]));
  const summary = vi.fn(); render(<OrchestratorStatus {...props} sidebarTarget={target} onSummary={summary}/>);
  const active = await screen.findByRole('region', {name: 'Active subagents'}), done = screen.getByRole('region', {name: 'Done subagents'});
  expect(within(active).getByRole('heading')).toHaveTextContent('2'); expect(within(done).getByRole('heading')).toHaveTextContent('3');
  expect(within(active).getByText('Needs approval')).toBeVisible(); expect(within(done).getByText('Failed')).toBeVisible(); expect(within(done).getByText('Cancelled')).toBeVisible();
  expect(within(done).getAllByText('1h')).toHaveLength(3);
  fireEvent.click(within(done).getByRole('button', {name: 'Open Agent failed'})); expect(open).toHaveBeenCalledWith('failed');
  await waitFor(() => expect(summary).toHaveBeenLastCalledWith({hasContent: true, loading: false, active: 2, attention: 2, done: 3, plans: 0, stale: false}));
  expect(coordinatorReads()).toHaveLength(1); expect(writes()).toHaveLength(0);
  expect(screen.queryByRole('button', {name: /Delegated work/})).not.toBeInTheDocument();
});

it('reveals ten more rows and gives keyboard focus to the first newly revealed child', async () => {
  response(snapshot(Array.from({length: 15}, (_, index) => child(String(index), 'completed', {updated_at: now - index * 60}))));
  render(<OrchestratorStatus {...props} sidebarTarget={target}/>);
  const done = await screen.findByRole('region', {name: 'Done subagents'});
  expect(within(done).getAllByRole('button', {name: /^Open Agent/})).toHaveLength(10);
  const more = within(done).getByRole('button', {name: 'Show more done subagents'}); more.focus(); fireEvent.click(more);
  expect(within(done).getAllByRole('button', {name: /^Open Agent/})).toHaveLength(15);
  expect(within(done).getByRole('button', {name: 'Open Agent 10'})).toHaveFocus(); expect(open).not.toHaveBeenCalled(); expect(writes()).toHaveLength(0);
});

it('keeps one observer when its portal moves and exposes all agents from an unrelated chat', async () => {
  response(snapshot([child('worker')])); const view = render(<OrchestratorStatus {...props} selected="another-chat" sidebarTarget={target}/>);
  await screen.findByRole('button', {name: 'Open Agent worker'}); expect(coordinatorReads()).toHaveLength(1);
  const next = document.createElement('div'); aside.append(next);
  view.rerender(<OrchestratorStatus {...props} selected="another-chat" sidebarTarget={next}/>);
  expect(within(next).getByRole('button', {name: 'Open Agent worker'})).toBeVisible(); expect(target).toBeEmptyDOMElement();
  expect(coordinatorReads()).toHaveLength(1); expect(writes()).toHaveLength(0);
});

it('keeps the real source approval visible inline when the sidebar is hidden', async () => {
  const approval = child('reviewer', 'awaiting_approval', {pending_approvals: 1});
  const task: Task = {id: approval.task_id!, status: 'awaiting_approval', prompt: 'Synthetic review', approvals: [{id: 'permission', status: 'pending', kind: 'tool', payload: {reason: 'Synthetic source operation'}}]};
  api.mockImplementation(async path => path === '/api/workspace/orchestrator' ? snapshot([approval]) : task);
  aside.hidden = true; const view = render(<OrchestratorStatus {...props} sidebarTarget={target}/>);
  expect(await screen.findByRole('button', {name: 'Review action'})).toBeVisible();
  expect(view.container).toContainElement(screen.getByRole('button', {name: 'Review action'})); expect(screen.getByText('1 subagent needs attention')).toBeVisible();
  expect(api.mock.calls.filter(([path]) => path === '/api/workspace/tasks/task-reviewer?activity=false')).toHaveLength(1);
  expect(coordinatorReads()).toHaveLength(1); expect(writes()).toHaveLength(0);
});

it('retains ownership controls inline, disables them on pause, and never changes authority through a row click', async () => {
  response(snapshot([child('worker', 'running', {control_owner: 'user'})]));
  const view = render(<OrchestratorStatus {...props} selected="worker" sidebarTarget={target}/>);
  const control = await screen.findByRole('button', {name: 'Return to main agent'}); expect(control).toBeEnabled();
  fireEvent.click(screen.getByRole('button', {name: 'Open Agent worker'})); expect(writes()).toHaveLength(0);
  view.rerender(<OrchestratorStatus {...props} selected="worker" enabled={false} sidebarTarget={target}/>);
  expect(control).toBeDisabled(); expect(screen.getByText('Updates paused')).toBeVisible();
});

it('keeps loading and recoverable errors honest even without a mounted sidebar target', async () => {
  let reject!: (reason: Error) => void;
  api.mockReturnValue(new Promise((_, fail) => {reject = fail;})); const summary = vi.fn();
  const view = render(<OrchestratorStatus {...props} sidebarTarget={target} onSummary={summary}/>);
  expect(screen.getByText('Loading subagents…')).toBeVisible();
  await act(async () => reject(Error('Synthetic host unavailable')));
  expect(await screen.findByRole('alert')).toHaveTextContent('Synthetic host unavailable'); expect(screen.getByText('Subagents could not be loaded.')).toBeVisible();
  view.rerender(<OrchestratorStatus {...props} sidebarTarget={null} onSummary={summary}/>);
  expect(screen.getByRole('button', {name: 'Refresh status'})).toBeVisible();
  expect(summary).toHaveBeenLastCalledWith(expect.objectContaining({loading: false, hasContent: false, stale: true}));
});

it('discards the previous account list before accepting the next account snapshot', async () => {
  response(snapshot([child('old-owner')])); const view = render(<OrchestratorStatus {...props} sidebarTarget={target}/>);
  await screen.findByRole('button', {name: 'Open Agent old-owner'});
  api.mockReturnValue(new Promise(() => {})); view.rerender(<OrchestratorStatus {...props} ownerId="next-owner" sidebarTarget={target}/>);
  expect(screen.queryByRole('button', {name: 'Open Agent old-owner'})).not.toBeInTheDocument(); expect(screen.getByText('Loading subagents…')).toBeVisible();
});

it('keeps plans discoverable without mounting a second coordination observer', async () => {
  render(<OrchestratorStatus {...props} sidebarTarget={target}/>);
  await screen.findByText('No active subagents'); const reads = coordinatorReads().length;
  fireEvent.click(screen.getByRole('button', {name: /Plans/}));
  expect(await screen.findByRole('region', {name: 'Task board'})).toBeVisible(); expect(screen.getByText(/No planned tasks yet/)).toBeVisible();
  expect(coordinatorReads()).toHaveLength(reads); expect(writes()).toHaveLength(0);
  await expectNoAxeViolations(document.body);
});

it('uses actual update ages and never fabricates a duration for missing timestamps', () => {
  expect(subagentAge(now - 3600, now)).toBe('1h'); expect(subagentAge(now - 120, now)).toBe('2m');
  expect(subagentAge(now + 60, now)).toBe('now'); expect(subagentAge(0, now)).toBe('—'); expect(subagentAge(Number.NaN, now)).toBe('—');
});

it('preserves the focused child when a live status update moves its row into Done', () => {
  const current = child('moving', 'running');
  const inputs = {ownerId: 'owner', selected: '', loading: false, stale: false, error: '', enabled: true, board: null, boardError: '', planOpen: false, onPlanOpenChange: vi.fn(), planContent: null, activity: [], onOpen: open, onRefresh: vi.fn()};
  const view = render(<CoordinationSidebar {...inputs} children={[current]}/>);
  screen.getByRole('button', {name: 'Open Agent moving'}).focus();
  view.rerender(<CoordinationSidebar {...inputs} children={[{...current, status: 'completed'}]}/>);
  expect(within(screen.getByRole('region', {name: 'Done subagents'})).getByRole('button', {name: 'Open Agent moving'})).toHaveFocus();
});

it('removes the previous account plan before waiting for the new account board', async () => {
  const node = {id: 'old-plan', title: 'Previous account plan', role: 'worker', depends_on: [], blocked_by: [], task_id: null, status: 'pending', attempts: 0, max_attempts: 3, last_event_at: null, result_summary: '', error: '', provider_id: null, model: null, tokens: 0, cost_usd: null, created_at: now, updated_at: now};
  board.mockResolvedValue({orchestrator_board: {nodes: [node], rollup: {...emptyBoard.rollup, total: 1, counts: {pending: 1}}}});
  const view = render(<OrchestratorStatus {...props} sidebarTarget={target}/>);
  await screen.findByText('No active subagents'); fireEvent.click(screen.getByRole('button', {name: /Plans/}));
  await screen.findByText('Previous account plan');
  board.mockReturnValue(new Promise(() => {})); api.mockReturnValue(new Promise(() => {}));
  view.rerender(<OrchestratorStatus {...props} ownerId="new-owner" sidebarTarget={target}/>);
  expect(screen.queryByText('Previous account plan')).not.toBeInTheDocument();
  expect(screen.queryByRole('region', {name: 'Task board'})).not.toBeInTheDocument(); expect(screen.getByText('Loading subagents…')).toBeVisible();
});

it.each(['recovery_confirmation_required', 'paused', 'failed'])('keeps %s attention actionable when the sidebar is closed', async status => {
  response(snapshot([child('needs-help', status)])); aside.hidden = true;
  render(<OrchestratorStatus {...props} sidebarTarget={target}/>);
  expect(await screen.findByText('1 subagent needs attention')).toBeVisible();
  fireEvent.click(screen.getByRole('button', {name: 'Open chat'})); expect(open).toHaveBeenCalledWith('needs-help'); expect(writes()).toHaveLength(0);
});
