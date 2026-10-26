import {act, cleanup, fireEvent, render, screen, waitFor} from '@testing-library/react';
import {afterEach, beforeEach, expect, it, vi} from 'vitest';
import {request, type Session, type Task} from '../lib/api';
import OrchestratorDecisions from './OrchestratorDecisions';
import OrchestratorStatus, {type OrchestratorChild} from './OrchestratorStatus';

vi.mock('../lib/api', () => ({request: vi.fn(), json: (method: string, data: unknown) => ({method, body: JSON.stringify(data)})}));
const api = vi.mocked(request), refreshed = vi.fn();
const child: OrchestratorChild = {session_id: 'source-chat', task_id: 'source/task', title: 'Native reviewer',
  status: 'awaiting_approval', pending_approvals: 1, engine: 'acp', origin: 'native', capability_limits: {},
  control_owner: 'orchestrator', revision: 3, updated_at: 100, parent_session_id: 'main'};
const task: Task = {id: 'source/task', status: 'awaiting_approval', prompt: 'Review the source',
  approvals: [{id: 'permission/1', status: 'pending', kind: 'tool', payload: {reason: 'Exact synthetic source operation'}}]};
const props = {ownerId: 'owner', children: [child], enabled: true, onRefresh: refreshed};
const posts = () => api.mock.calls.filter(([, init]) => init?.method === 'POST');
beforeEach(() => {api.mockReset(); refreshed.mockReset(); api.mockResolvedValue(task);});
afterEach(() => {cleanup(); vi.restoreAllMocks();});

it('shows the source decision in Main while delegated work is collapsed, with no navigation or ownership change', async () => {
  const open = vi.fn();
  let completeSnapshot!: (value: unknown) => void, completeSource!: (value: Task) => void;
  const snapshotRead = new Promise(resolve => {completeSnapshot = resolve;});
  const sourceRead = new Promise<Task>(resolve => {completeSource = resolve;});
  api.mockImplementation(async path => {
    if (path === '/api/workspace/orchestrator') return await snapshotRead;
    if (path === '/api/workspace/tasks/source%2Ftask?activity=false') return await sourceRead;
    throw Error('Unexpected request in Main decision fixture: ' + path);
  });
  render(<OrchestratorStatus ownerId="owner" selected="main" enabled onOpen={open}/>);
  expect(api.mock.calls.some(([path]) => path === '/api/workspace/orchestrator')).toBe(true);
  // Two independent effects are involved: the coordinator snapshot mounts the
  // source reader, then its authenticated task read mounts the exact review.
  // Settle each boundary explicitly instead of starting an expensive DOM poll
  // before the nested effect exists on an overloaded test host.
  await act(async () => completeSnapshot({session: {id: 'main'} as Session, children: [child], activity: [], cursor: 7}));
  expect(api.mock.calls.filter(([path]) => path === '/api/workspace/tasks/source%2Ftask?activity=false')).toHaveLength(1);
  await act(async () => completeSource(task));
  expect(screen.getByRole('button', {name: 'Review action'})).toBeVisible();
  expect(screen.getByRole('button', {name: /Delegated work/})).toHaveAttribute('aria-expanded', 'false');
  expect(screen.getByRole('article', {name: 'Requests from Native reviewer'})).toBeVisible();
  expect(screen.getByText('Main agent is coordinating')).toBeVisible();
  expect(open).not.toHaveBeenCalled(); expect(posts()).toHaveLength(0);
  expect(api.mock.calls.some(([path]) => path.endsWith('/control'))).toBe(false);
  expect(api.mock.calls.some(([path]) => path === '/api/workspace/tasks/source%2Ftask?activity=false')).toBe(true);
});

it('closing the source review sends nothing; explicit confirmation resolves its exact ID once', async () => {
  let finish!: (value: unknown) => void;
  api.mockImplementation(async (_path, init) => init?.method === 'POST'
    ? new Promise(resolve => {finish = resolve;}) : task);
  render(<OrchestratorDecisions {...props}/>);
  fireEvent.click(await screen.findByRole('button', {name: 'Review action'}));
  expect(screen.getByText('Exact synthetic source operation')).toBeVisible();
  fireEvent.click(screen.getByRole('button', {name: 'Close'}));
  expect(posts()).toHaveLength(0);
  fireEvent.click(screen.getByRole('button', {name: 'Review action'}));
  const allow = screen.getByRole('button', {name: 'Allow once'});
  fireEvent.click(allow); fireEvent.click(allow);
  expect(posts()).toHaveLength(1);
  expect(posts()[0][0]).toBe('/api/workspace/tasks/source%2Ftask/approvals/permission%2F1/resolve');
  expect(JSON.parse(String(posts()[0][1]?.body))).toEqual({decision: 'approved'});
  await act(async () => finish({}));
  await waitFor(() => expect(refreshed).toHaveBeenCalledOnce());
  expect(api.mock.calls.some(([path]) => path.endsWith('/control'))).toBe(false);
});

it('denies the original request without granting control or synthesizing another approval', async () => {
  api.mockImplementation(async (_path, init) => init?.method === 'POST' ? {} as any : task);
  render(<OrchestratorDecisions {...props} children={[{...child, control_owner: 'user'}]}/>);
  fireEvent.click(await screen.findByRole('button', {name: 'Review action'}));
  fireEvent.click(screen.getByRole('button', {name: 'Deny'}));
  await waitFor(() => expect(refreshed).toHaveBeenCalledOnce());
  expect(posts()).toHaveLength(1);
  expect(JSON.parse(String(posts()[0][1]?.body))).toEqual({decision: 'denied'});
  expect(posts()[0][0]).toContain('/source%2Ftask/approvals/permission%2F1/resolve');
  expect(api.mock.calls.some(([path]) => path.endsWith('/control'))).toBe(false);
});

it('drops an open source payload immediately when a newer read reports revoked access', async () => {
  const view = render(<OrchestratorDecisions {...props}/>);
  fireEvent.click(await screen.findByRole('button', {name: 'Review action'}));
  api.mockRejectedValueOnce(Error('Forbidden'));
  view.rerender(<OrchestratorDecisions {...props} children={[{...child, updated_at: 101}]}/>);
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(screen.queryByText('Exact synthetic source operation')).not.toBeInTheDocument();
  expect(await screen.findByRole('alert')).toHaveTextContent('current access');
  expect(screen.queryByRole('button', {name: 'Review action'})).not.toBeInTheDocument();
  expect(posts()).toHaveLength(0);
});

it('discards delayed responses across an account switch, including switching back', async () => {
  const pending: ((value: Task) => void)[] = [];
  api.mockImplementation(() => new Promise(resolve => pending.push(resolve)));
  const view = render(<OrchestratorDecisions {...props}/>);
  view.rerender(<OrchestratorDecisions {...props} ownerId="other"/>);
  view.rerender(<OrchestratorDecisions {...props}/>);
  await act(async () => pending[0](task));
  expect(screen.queryByRole('button', {name: 'Review action'})).not.toBeInTheDocument();
  await act(async () => pending[1](task));
  expect(screen.queryByRole('button', {name: 'Review action'})).not.toBeInTheDocument();
  await act(async () => pending[2](task));
  expect(screen.getByRole('button', {name: 'Review action'})).toBeVisible();
});

it('aborts hidden or stale reads and never leaves a review dialog active', async () => {
  const view = render(<OrchestratorDecisions {...props}/>);
  fireEvent.click(await screen.findByRole('button', {name: 'Review action'}));
  view.rerender(<OrchestratorDecisions {...props} enabled={false}/>);
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  const calls = api.mock.calls.length;
  await act(async () => {});
  expect(api).toHaveBeenCalledTimes(calls);
  let finish!: (value: Task) => void;
  api.mockImplementation(() => new Promise(resolve => {finish = resolve;}));
  view.rerender(<OrchestratorDecisions {...props}/>);
  const signal = api.mock.calls.at(-1)![1]?.signal;
  view.rerender(<OrchestratorDecisions {...props} enabled={false}/>);
  expect(signal?.aborted).toBe(true);
  await act(async () => finish(task));
  expect(screen.queryByRole('button', {name: 'Review action'})).not.toBeInTheDocument();
});

it('reads no more than eight source tasks sequentially without its own polling loop', async () => {
  let finish!: (value: Task) => void;
  api.mockImplementationOnce(() => new Promise(resolve => {finish = resolve;}));
  api.mockImplementation(async path => ({id: decodeURIComponent(path.split('/').at(-1)!.split('?')[0]),
    prompt: '', status: 'completed', approvals: []}) as Task);
  const children = Array.from({length: 10}, (_, index) => ({...child, task_id: 'task-' + index, session_id: 'chat-' + index}));
  render(<OrchestratorDecisions {...props} children={children}/>);
  expect(api).toHaveBeenCalledOnce();
  await act(async () => finish({id: 'task-0', prompt: '', status: 'completed', approvals: []}));
  await waitFor(() => expect(api).toHaveBeenCalledTimes(8));
  expect(screen.getByText(/Showing the first 8 agents/)).toBeVisible();
  expect(posts()).toHaveLength(0);
});

it('rejects a mismatched task response and removes a request resolved elsewhere', async () => {
  api.mockResolvedValueOnce({...task, id: 'different-task'});
  const view = render(<OrchestratorDecisions {...props}/>);
  await screen.findByRole('alert');
  expect(screen.queryByRole('button', {name: 'Review action'})).not.toBeInTheDocument();
  view.rerender(<OrchestratorDecisions {...props} children={[{...child, pending_approvals: 0}]}/>);
  expect(screen.queryByRole('region', {name: 'Decisions for your agents'})).not.toBeInTheDocument();
  expect(posts()).toHaveLength(0);
});

it('a failed decision refreshes the source but never retransmits the mutation', async () => {
  api.mockImplementation(async (_path, init) => {
    if (init?.method === 'POST') throw Error('Uncertain response');
    return task;
  });
  render(<OrchestratorDecisions {...props}/>);
  fireEvent.click(await screen.findByRole('button', {name: 'Review action'}));
  fireEvent.click(screen.getByRole('button', {name: 'Allow once'}));
  expect(await screen.findByRole('alert')).toHaveTextContent('did not confirm');
  await waitFor(() => expect(api.mock.calls.filter(([, init]) => !init?.method)).toHaveLength(2));
  expect(posts()).toHaveLength(1);
});

it('uses the original budget review and its exact allowance without a coordinator grant', async () => {
  const budget = {...task, id: 'budget-task', limits: {max_steps: 2, max_seconds: 60}, approvals: [{id: 'budget-source',
    status: 'pending', kind: 'budget', payload: {suggested_limits: {max_steps: 4, max_seconds: 90}}}]};
  api.mockImplementation(async (_path, init) => init?.method === 'POST' ? {} as any : budget);
  render(<OrchestratorDecisions {...props} children={[{...child, task_id: 'budget-task'}]}/>);
  fireEvent.click(await screen.findByRole('button', {name: 'Review budget extension'}));
  expect(screen.getByLabelText('Extended max_steps')).toHaveValue(4);
  fireEvent.click(screen.getByRole('button', {name: 'Approve budget extension'}));
  await waitFor(() => expect(refreshed).toHaveBeenCalledOnce());
  expect(posts()).toHaveLength(1);
  expect(posts()[0][0]).toBe('/api/workspace/tasks/budget-task/approvals/budget-source/resolve');
  expect(JSON.parse(String(posts()[0][1]?.body))).toEqual({decision: 'approved', limits: {max_steps: 4, max_seconds: 90}});
});

it('keeps an unchanged exact review open while another agent requests a decision', async () => {
  api.mockImplementation(async path => path.includes('other-task')
    ? {...task, id: 'other-task', approvals: []} : task);
  const view = render(<OrchestratorDecisions {...props}/>);
  fireEvent.click(await screen.findByRole('button', {name: 'Review action'}));
  view.rerender(<OrchestratorDecisions {...props} children={[child,
    {...child, session_id: 'other-chat', task_id: 'other-task', title: 'Another worker'}]}/>);
  expect(screen.getByRole('dialog', {name: 'Review action'})).toBeVisible();
  await waitFor(() => expect(api).toHaveBeenCalledTimes(3));
  expect(screen.getByRole('dialog', {name: 'Review action'})).toBeVisible();
  expect(screen.getByText('Exact synthetic source operation')).toBeVisible();
  expect(posts()).toHaveLength(0);
});
