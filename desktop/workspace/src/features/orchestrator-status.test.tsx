import {afterEach, beforeEach, expect, it, vi} from 'vitest';
import {act, cleanup, fireEvent, render, screen, waitFor} from '@testing-library/react';
import OrchestratorStatus, {type OrchestratorChild, type OrchestratorSnapshot} from './OrchestratorStatus';
import {request, type Session} from '../lib/api';

vi.mock('../lib/api', () => ({request: vi.fn(), json: (method: string, data: unknown) => ({method, body: JSON.stringify(data)})}));
const api = vi.mocked(request), open = vi.fn(), primary = vi.fn(), refreshed = vi.fn();
const child: OrchestratorChild = {session_id: 'child-chat', task_id: 'child-task', title: 'Review access controls', status: 'awaiting_approval', engine: 'internal', origin: 'delegated', capability_limits: {time_budget: 'shared-host', tool_steps: 'shared-host', tokens: 'unmeasured', cost: 'unmeasured'}, control_owner: 'orchestrator', revision: 7, updated_at: 1000, parent_session_id: 'main'};
const snapshot = (children = [child]): OrchestratorSnapshot => ({
  session: {id: 'main', title: 'Main agent'} as Session, children,
  activity: [{id: 'update', sequence: 1, kind: 'child.updated', session_id: 'child-chat', task_id: 'child-task', status: 'awaiting_approval', message: 'Access review needs a decision.', created_at: 1000}], cursor: 1,
});
const props = {ownerId: 'owner', selected: 'main', enabled: true, onOpen: open, onPrimary: primary, onRefresh: refreshed};
beforeEach(() => { api.mockReset(); api.mockResolvedValue(snapshot()); open.mockReset(); primary.mockReset(); refreshed.mockReset(); });
afterEach(() => { cleanup(); vi.useRealTimers(); vi.restoreAllMocks(); });

it('summarizes delegated work without replacing the main conversation or dispatching work', async () => {
  render(<OrchestratorStatus {...props}/>);
  await screen.findByText('1 need attention');
  expect(primary).toHaveBeenCalledWith('main');
  expect(screen.queryByText(child.title)).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', {name: /Delegated work/}));
  fireEvent.click(screen.getByRole('button', {name: /Review access controls/}));
  expect(open).toHaveBeenCalledWith('child-chat');
  expect(api.mock.calls.every(([, init]) => !init?.method)).toBe(true);
  expect(screen.getByText('Main agent is coordinating')).toBeVisible();
  expect(screen.getByText('Access review needs a decision.')).toBeInTheDocument();
});

it('takes direct control using the exact child revision and never approves its pending tools', async () => {
  api.mockImplementation(async (_path, init) => init?.method === 'POST' ? {...child, control_owner: 'user', revision: 8} : snapshot());
  render(<OrchestratorStatus {...props} selected="child-chat"/>);
  fireEvent.click(await screen.findByRole('button', {name: 'Take control'}));
  await screen.findByText('You are directing this chat');
  expect(api.mock.calls.find(([, init]) => init?.method === 'POST')).toEqual([
    '/api/workspace/orchestrator/children/child-chat/control',
    {method: 'POST', body: JSON.stringify({owner: 'user', revision: 7})},
  ]);
  expect(api.mock.calls.some(([path]) => path.includes('approval'))).toBe(false);
  expect(refreshed).toHaveBeenCalledOnce();
  expect(open).not.toHaveBeenCalled();
});

it('returns ownership explicitly and opens the primary only after the host confirms', async () => {
  let finish!: (value: OrchestratorChild) => void;
  api.mockImplementation(async (_path, init) => init?.method === 'POST' ? new Promise<OrchestratorChild>(resolve => { finish = resolve; }) : snapshot([{...child, control_owner: 'user'}]));
  render(<OrchestratorStatus {...props} selected="child-chat"/>);
  fireEvent.click(await screen.findByRole('button', {name: 'Return to main agent'}));
  expect(open).not.toHaveBeenCalled();
  expect(screen.getByRole('button', {name: 'Updating…'})).toBeDisabled();
  await act(async () => finish({...child, revision: 8}));
  expect(open).toHaveBeenCalledWith('main');
});

it('a late ownership response cannot navigate away from a newer user selection or duplicate a transfer', async () => {
  let finish!: (value: OrchestratorChild) => void;
  api.mockImplementation(async (_path, init) => init?.method === 'POST' ? new Promise<OrchestratorChild>(resolve => { finish = resolve; }) : snapshot([{...child, control_owner: 'user'}]));
  const view = render(<OrchestratorStatus {...props} selected="child-chat"/>);
  const button = await screen.findByRole('button', {name: 'Return to main agent'});
  fireEvent.click(button); fireEvent.click(button);
  view.rerender(<OrchestratorStatus {...props} selected="another-chat"/>);
  await act(async () => finish({...child, revision: 8}));
  expect(open).not.toHaveBeenCalled();
  expect(api.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(1);
});

it('resolves conflict state by reading ownership and does not replay an uncertain mutation', async () => {
  let reads = 0;
  api.mockImplementation(async (_path, init) => {
    if (init?.method === 'POST') throw Error('Ownership changed in another window. Refresh before continuing.');
    reads++; return snapshot(reads === 1 ? [child] : [{...child, control_owner: 'user', revision: 9}]);
  });
  render(<OrchestratorStatus {...props} selected="child-chat"/>);
  fireEvent.click(await screen.findByRole('button', {name: 'Take control'}));
  expect(await screen.findByRole('alert')).toHaveTextContent('Ownership changed');
  await screen.findByText('You are directing this chat');
  expect(api.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(1);
  expect(reads).toBe(2);
});

it('keeps received results clearly stale on failed reads and recovers without mutation', async () => {
  vi.useFakeTimers();
  render(<OrchestratorStatus {...props}/>);
  await act(async () => {});
  fireEvent.click(screen.getByRole('button', {name: /Delegated work/}));
  api.mockRejectedValueOnce(Error('Host disconnected'));
  await act(async () => { vi.advanceTimersByTime(4000); });
  expect(screen.getByRole('alert')).toHaveTextContent('Showing the last received state');
  expect(screen.getByText(child.title)).toBeInTheDocument();
  expect(screen.getByText('Updates interrupted')).toBeVisible();
  fireEvent.click(screen.getByRole('button', {name: 'Refresh status'}));
  await act(async () => {});
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  expect(api.mock.calls.every(([, init]) => !init?.method)).toBe(true);
});

it('pauses and aborts hidden or unfocused reads, with one read in flight', async () => {
  vi.useFakeTimers();
  let finish!: (value: OrchestratorSnapshot) => void;
  api.mockImplementation(() => new Promise<OrchestratorSnapshot>(resolve => { finish = resolve; }));
  const view = render(<OrchestratorStatus {...props}/>);
  const signal = api.mock.calls[0][1]?.signal;
  await act(async () => { vi.advanceTimersByTime(12000); });
  expect(api).toHaveBeenCalledOnce();
  view.rerender(<OrchestratorStatus {...props} enabled={false}/>);
  expect(signal?.aborted).toBe(true);
  await act(async () => finish(snapshot()));
  expect(primary).not.toHaveBeenCalled();
  view.rerender(<OrchestratorStatus {...props}/>);
  await act(async () => {});
  expect(api).toHaveBeenCalledTimes(2);
  act(() => window.dispatchEvent(new Event('blur')));
  await act(async () => { vi.advanceTimersByTime(12000); });
  expect(api).toHaveBeenCalledTimes(2);
});

it('discards late responses after an account switch, including switching back', async () => {
  const pending: ((value: OrchestratorSnapshot) => void)[] = [];
  api.mockImplementation(() => new Promise<OrchestratorSnapshot>(resolve => pending.push(resolve)));
  const view = render(<OrchestratorStatus {...props}/>);
  view.rerender(<OrchestratorStatus {...props} ownerId="other-owner"/>);
  view.rerender(<OrchestratorStatus {...props}/>);
  await act(async () => pending[0]({...snapshot(), session: {id: 'foreign-main'} as Session}));
  expect(primary).not.toHaveBeenCalledWith('foreign-main');
  await act(async () => pending[2](snapshot([])));
  await waitFor(() => expect(primary).toHaveBeenCalledWith('main'));
  expect(screen.getByText('No tasks yet')).toBeVisible();
});

it('does not show unrelated conversation content or a nonexistent primary', async () => {
  const view = render(<OrchestratorStatus {...props} selected="unrelated"/>);
  await waitFor(() => expect(primary).toHaveBeenCalledWith('main'));
  expect(screen.queryByRole('region', {name: 'Main agent coordination'})).not.toBeInTheDocument();
  api.mockResolvedValue({session: null, children: [], activity: [], cursor: 0});
  view.rerender(<OrchestratorStatus {...props} ownerId="fresh-owner" selected=""/>);
  await waitFor(() => expect(screen.queryByText('Delegated work')).not.toBeInTheDocument());
});


it('reports the actual native engine and capability limits without claiming tool, token, or cost caps', async () => {
  const native: OrchestratorChild = {...child, engine: 'custom-acp-reviewer', origin: 'native', status: 'running', capability_limits: {
    time_budget: 'shared-host', tool_steps: 'unmeasured', tokens: 'unmeasured', cost: 'unmeasured',
    steer: 'unverified', resume: 'native', approvals: 'gateway', tools_filter: 'advisory', elapsed_deadline_seconds: 86400,
  }};
  api.mockResolvedValue(snapshot([native]));
  render(<OrchestratorStatus {...props}/>);
  await screen.findByText('1 in progress');
  fireEvent.click(screen.getByRole('button', {name: /Delegated work/}));
  expect(screen.getByText('Engine: custom-acp-reviewer')).toBeVisible();
  const details = screen.getByLabelText('Engine details for Review access controls');
  expect(details).not.toHaveAttribute('open');
  fireEvent.click(screen.getByText('Engine capabilities & limits'));
  expect(details).toHaveAttribute('open');
  expect(screen.getByText('Tool steps are not measured by TermX')).toBeVisible();
  expect(screen.getAllByText('Not measured or capped by TermX')).toHaveLength(2);
  expect(screen.getByText('Not verified')).toBeVisible();
  expect(screen.getByText('Advisory only')).toBeVisible();
  expect(screen.getByText('24 hours while the main agent is in control')).toBeVisible();
  expect(api.mock.calls.every(([, init]) => !init?.method)).toBe(true);
});

it('keeps an adopted idle chat user-owned until the user explicitly grants main-agent control', async () => {
  const adopted: OrchestratorChild = {...child, task_id: null, engine: 'codex', origin: 'adopted', status: 'idle', control_owner: 'user', capability_limits: {time_budget: 'host-monitoring', tool_steps: 'unmeasured'}};
  api.mockImplementation(async (_path, init) => init?.method === 'POST' ? {...adopted, control_owner: 'orchestrator', revision: 8} : snapshot([adopted]));
  const view = render(<OrchestratorStatus {...props}/>);
  await screen.findByText('1 finished or idle');
  fireEvent.click(screen.getByRole('button', {name: /Delegated work/}));
  expect(screen.getByText('No task started')).toBeVisible();
  expect(screen.getByText('Engine: Codex · Existing chat')).toBeVisible();
  view.rerender(<OrchestratorStatus {...props} selected="child-chat"/>);
  expect(screen.getByText('This existing chat is included in updates. You stay in control until you give the main agent control.')).toBeVisible();
  expect(screen.queryByRole('button', {name: 'Return to main agent'})).not.toBeInTheDocument();
  expect(api.mock.calls.some(([, init]) => init?.method === 'POST')).toBe(false);
  fireEvent.click(screen.getByRole('button', {name: 'Give main agent control'}));
  await waitFor(() => expect(open).toHaveBeenCalledWith('main'));
  expect(api.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(1);
  expect(api.mock.calls.find(([, init]) => init?.method === 'POST')?.[1]?.body).toBe(JSON.stringify({owner: 'orchestrator', revision: 7}));
});
