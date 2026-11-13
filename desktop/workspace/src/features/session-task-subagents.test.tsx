import {afterEach, beforeEach, expect, it, vi} from 'vitest';
import {act, cleanup, fireEvent, render, screen, waitFor} from '@testing-library/react';
import {Profiler} from 'react';
import SessionTaskSubagents from './SessionTaskSubagents';
import OrchestratorStatus, {type CoordinationSidebarSummary} from './OrchestratorStatus';
import {request, gql, type Task} from '../lib/api';
vi.mock('../lib/api', () => ({request: vi.fn(), gql: vi.fn(), json: (method: string, body: unknown) => ({method, body: JSON.stringify(body)})}));
const api = vi.mocked(request), graph = vi.mocked(gql), inspect = vi.fn(), summary = vi.fn();
const root: Task = {id: 'coding-root', status: 'completed', prompt: 'Implement the ordinary project'};
const worker = {id: 'coding-child', parent_id: root.id, agent_label: 'Review SQL boundaries', prompt: 'Audit exact rows', status: 'completed', updated_at: Date.now() / 1000 - 180, children: []};
let target: HTMLDivElement;
const props = {ownerId: 'owner', sessionId: 'coding-chat', tasks: [root], visible: true, enabled: true, onSummary: summary, onInspectTask: inspect};
beforeEach(() => {api.mockReset().mockResolvedValue({...root, children: [worker]}); graph.mockReset().mockResolvedValue({orchestrator_board: {nodes: [], rollup: {total: 0, counts: {}}}}); inspect.mockReset(); summary.mockReset(); target = document.createElement('div'); document.body.append(target);});
afterEach(() => {cleanup(); target.remove(); vi.restoreAllMocks();});
it('uses actual authorized nested task IDs for ordinary project inspection, without fake conversations', async () => {
  api.mockImplementation(async path => path === '/api/workspace/orchestrator' ? {session: {id: 'main'}, children: [{session_id: 'unrelated-global-chat', title: 'Global worker', status: 'running', updated_at: 1}], activity: [], cursor: 1} : {...root, children: [worker]});
  const open = vi.fn(); render(<OrchestratorStatus ownerId="owner" selected="coding-chat" enabled onOpen={open} presentation="sidebar" sidebarTarget={target} selectedTasks={[root]} sidebarVisible onInspectTask={inspect} onSummary={summary}/>);
  fireEvent.click(await screen.findByRole('button', {name: 'Open Review SQL boundaries'}));
  expect(inspect).toHaveBeenCalledWith(worker.id); expect(open).not.toHaveBeenCalled(); expect(screen.queryByText('Global worker')).not.toBeInTheDocument();
  expect(api.mock.calls.filter(([path]) => String(path).includes('/tasks/'))).toEqual([['/api/workspace/tasks/coding-root?activity=false', expect.objectContaining({signal: expect.any(AbortSignal)})]]);
  await waitFor(() => expect(summary).toHaveBeenLastCalledWith({
    active: 0, attention: 0, done: 1, plans: 0, hasContent: true, loading: false, stale: false,
  }));
  expect(screen.queryByRole('button', {name: /Plans/})).not.toBeInTheDocument();
});
it('publishes an ordinary inspection summary after its authorized child row commits', async () => {
  const events: Array<Record<string, unknown>> = [];
  const record = (event: Record<string, unknown>) => {events.push({index: events.length, ...event});};
  const published: Array<{index: number; value: CoordinationSidebarSummary}> = [];
  let latest: CoordinationSidebarSummary | undefined;
  summary.mockImplementation((value: CoordinationSidebarSummary) => {
    latest = {...value};
    published.push({index: events.length, value: latest});
    record({event: 'summary', value: latest, rowPresent: !!target.querySelector('[data-subagent-id="coding-child"]')});
  });
  let finishMetadata!: (value: unknown) => void;
  let finishTree!: (value: Task) => void;
  const metadata = new Promise(resolve => {finishMetadata = resolve;});
  const tree = new Promise<Task>(resolve => {finishTree = resolve;});
  const treePath = '/api/workspace/tasks/coding-root?activity=false';
  api.mockImplementation(path => {
    record({event: 'request', path});
    if (path === '/api/workspace/orchestrator') return metadata;
    if (path === treePath) return tree;
    return Promise.reject(Error('Unexpected request: ' + path));
  });
  const open = vi.fn();
  let firstRow: {index: number; rowId: string | null; status: string | null; connected: boolean; loadingNotice: boolean; summary: CoordinationSidebarSummary | undefined} | undefined;
  const scene = () => <Profiler id="ordinary-inspection" onRender={(_id, phase) => {
    const row = target.querySelector<HTMLButtonElement>('[data-subagent-id="coding-child"]');
    const snapshot = {
      index: events.length, rowId: row?.getAttribute('data-subagent-id') ?? null,
      status: row?.getAttribute('data-status') ?? null, connected: row?.isConnected ?? false,
      loadingNotice: target.textContent?.includes('Loading subagents…') ?? false,
      summary: latest && {...latest}, treeReads: api.mock.calls.filter(([path]) => path === treePath).length,
    };
    if (row && !firstRow) firstRow = snapshot;
    record({event: 'commit', phase, ...snapshot});
  }}><OrchestratorStatus ownerId="owner" selected="coding-chat" enabled onOpen={open} presentation="sidebar" sidebarTarget={target} selectedTasks={[root]} sidebarVisible onInspectTask={inspect} onSummary={summary}/></Profiler>;
  const view = render(scene());
  expect(summary).toHaveBeenLastCalledWith(expect.objectContaining({
    active: 0, done: 0, plans: 0, hasContent: false, loading: true,
  }));
  expect(screen.queryByRole('button', {name: 'Open Review SQL boundaries'})).not.toBeInTheDocument();
  expect(api.mock.calls.map(([path]) => path)).toEqual(['/api/workspace/orchestrator']);

  record({event: 'metadata-response'});
  await act(async () => {finishMetadata({
    session: {id: 'main'},
    children: [{session_id: 'unrelated-global-chat', title: 'Global worker', status: 'running', updated_at: 1}],
    activity: [], cursor: 1,
  });});
  await waitFor(() => expect(api).toHaveBeenCalledWith(treePath, {signal: expect.any(AbortSignal)}));
  expect(summary).toHaveBeenLastCalledWith(expect.objectContaining({
    active: 0, done: 0, plans: 0, hasContent: false, loading: true,
  }));
  expect(screen.queryByRole('button', {name: 'Open Review SQL boundaries'})).not.toBeInTheDocument();
  expect(screen.queryByText('Global worker')).not.toBeInTheDocument();

  record({event: 'tree-response'});
  await act(async () => {finishTree({...root, children: [worker]});});
  const readySummary: CoordinationSidebarSummary = {active: 0, attention: 0, done: 1, hasContent: true, loading: false, plans: 0, stale: false};
  await waitFor(() => {
    expect(screen.getByRole('button', {name: 'Open Review SQL boundaries'})).toBeInTheDocument();
    expect(summary).toHaveBeenLastCalledWith(readySummary);
  });
  expect(firstRow).toMatchObject({
    rowId: worker.id, status: 'completed', connected: true, loadingNotice: false,
    summary: {active: 0, done: 0, plans: 0, hasContent: false, loading: true},
  });
  const readyPublication = published.find(item => item.value.hasContent && !item.value.loading);
  expect(readyPublication).toBeDefined();
  expect(readyPublication!.index).toBeGreaterThan(firstRow!.index);
  fireEvent.click(screen.getByRole('button', {name: 'Open Review SQL boundaries'}));
  expect(inspect).toHaveBeenCalledTimes(1);
  expect(inspect).toHaveBeenCalledWith(worker.id);
  expect(open).not.toHaveBeenCalled();
  expect(screen.queryByText('Global worker')).not.toBeInTheDocument();
  expect(screen.queryByRole('button', {name: /Plans/})).not.toBeInTheDocument();

  view.rerender(scene());
  expect(screen.getByRole('button', {name: 'Open Review SQL boundaries'})).toBeInTheDocument();
  expect(summary).toHaveBeenLastCalledWith(readySummary);
  for (const item of published.filter(item => item.index >= readyPublication!.index)) expect(item.value).toEqual(readySummary);
  expect(api.mock.calls).toEqual([
    ['/api/workspace/orchestrator', {signal: expect.any(AbortSignal)}],
    [treePath, {signal: expect.any(AbortSignal)}],
  ]);
  record({event: 'same-identity-settled', summary: latest, rowId: worker.id});
});
it('does not prefetch completed history while hidden, then reads up to ten recent roots on demand', async () => {
  const tasks = Array.from({length: 15}, (_, index) => ({...root, id: 'root-' + index}));
  api.mockImplementation(async path => ({...root, id: String(path).split('/tasks/')[1].split('?')[0], children: []}));
  const view = render(<SessionTaskSubagents {...props} tasks={tasks} visible={false} target={target}/>); expect(api).not.toHaveBeenCalled();
  view.rerender(<SessionTaskSubagents {...props} tasks={tasks} visible target={target}/>);
  await waitFor(() => expect(api).toHaveBeenCalledTimes(10));
  expect(api.mock.calls.every(([path]) => !String(path).includes('/root-0?') && !String(path).includes('/root-4?'))).toBe(true);
  view.rerender(<SessionTaskSubagents {...props} tasks={[...tasks]} visible target={target}/>); await act(async () => {}); expect(api).toHaveBeenCalledTimes(10);
});
it.each([
  {status: 'running', active: 1, done: 0},
  {status: 'completed', active: 0, done: 1},
])('discovers $status host children from current evidence while the sidebar is closed', async ({status, active, done}) => {
  const hint = {...worker, id: 'hint-only-child', agent_label: 'Unverified child hint', status: 'running'};
  const evidence = {...root, children: [hint]};
  let finish!: (value: Task) => void;
  api.mockReturnValue(new Promise<Task>(resolve => {finish = resolve;}));

  render(<SessionTaskSubagents {...props} tasks={[evidence]} visible={false} target={target}/>);

  expect(api).toHaveBeenCalledTimes(1);
  expect(summary).toHaveBeenLastCalledWith(expect.objectContaining({
    hasContent: false, loading: true, active: 0, done: 0, plans: 0,
  }));
  expect(screen.getByText('Loading subagents…')).toBeInTheDocument();
  expect(screen.queryByRole('button', {name: 'Open Review SQL boundaries'})).not.toBeInTheDocument();
  expect(screen.queryByRole('button', {name: 'Open Unverified child hint'})).not.toBeInTheDocument();

  await act(async () => {finish({...root, children: [{...worker, status}]});});
  await waitFor(() => {
    expect(screen.getByRole('button', {name: 'Open Review SQL boundaries'})).toBeInTheDocument();
    expect(summary).toHaveBeenLastCalledWith(expect.objectContaining({
      hasContent: true, loading: false, active, done, plans: 0,
    }));
  });

  expect(screen.queryByText('Loading subagents…')).not.toBeInTheDocument();
  expect(screen.queryByRole('button', {name: 'Open Unverified child hint'})).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', {name: 'Open Review SQL boundaries'}));
  expect(inspect).toHaveBeenCalledWith(worker.id);
  expect(api.mock.calls).toEqual([
    ['/api/workspace/tasks/coding-root?activity=false', {signal: expect.any(AbortSignal)}],
  ]);
});
it('clears stale authorization data on a rejected refresh, and allows a read-only retry', async () => {
  const tasks = [{...root, status: 'running'}];
  const view = render(<SessionTaskSubagents {...props} tasks={tasks} target={target}/>); await screen.findByRole('button', {name: 'Open Review SQL boundaries'});
  api.mockRejectedValue(Error('Access was revoked'));
  view.rerender(<SessionTaskSubagents {...props} tasks={tasks} enabled={false} target={target}/>);
  view.rerender(<SessionTaskSubagents {...props} tasks={tasks} target={target}/>);
  expect(await screen.findByRole('alert')).toHaveTextContent('Access was revoked'); expect(screen.queryByRole('button', {name: 'Open Review SQL boundaries'})).not.toBeInTheDocument();
  api.mockResolvedValue({...root, children: [worker]}); fireEvent.click(screen.getAllByRole('button', {name: 'Refresh subagents'})[0]);
  await screen.findByRole('button', {name: 'Open Review SQL boundaries'}); expect(api.mock.calls.every(([, init]) => !init?.method)).toBe(true);
});
it('discards late tree responses after switching conversations or accounts', async () => {
  let finish!: (value: unknown) => void; api.mockReturnValue(new Promise(resolve => {finish = resolve;}));
  const view = render(<SessionTaskSubagents {...props} target={target}/>); const signal = api.mock.calls[0][1]?.signal;
  view.rerender(<SessionTaskSubagents {...props} ownerId="another-owner" sessionId="another-chat" tasks={[]} target={target}/>);
  expect(signal?.aborted).toBe(true); await act(async () => finish({...root, children: [worker]}));
  expect(screen.queryByRole('button', {name: 'Open Review SQL boundaries'})).not.toBeInTheDocument(); expect(summary).toHaveBeenLastCalledWith(expect.objectContaining({hasContent: false, active: 0, done: 0}));
});
it('retains actionable recovery attention outside a closed sidebar and opens the real supervisor', async () => {
  api.mockResolvedValue({...root, children: [{...worker, status: 'recovery_confirmation_required'}]}); target.hidden = true;
  render(<SessionTaskSubagents {...props} target={target}/>);
  expect(await screen.findByText('1 subagent needs attention')).toBeVisible(); fireEvent.click(screen.getByRole('button', {name: 'Inspect task'})); expect(inspect).toHaveBeenCalledWith(worker.id);
});

it('rechecks only active trees after the bounded historical discovery read', async () => {
  const tasks = [root, {...root, id: 'old-root'}];
  api.mockImplementation(async path => String(path).includes('/coding-root?') ? {...root, children: [{...worker, status: 'paused'}]} : {...root, id: 'old-root', children: []});
  const view = render(<SessionTaskSubagents {...props} tasks={tasks} target={target}/>);
  await screen.findByRole('button', {name: 'Open Review SQL boundaries'}); expect(api).toHaveBeenCalledTimes(2);
  view.rerender(<SessionTaskSubagents {...props} tasks={tasks} enabled={false} target={target}/>);
  view.rerender(<SessionTaskSubagents {...props} tasks={tasks} target={target}/>);
  await waitFor(() => expect(api).toHaveBeenCalledTimes(3)); expect(api.mock.calls.at(-1)?.[0]).toBe('/api/workspace/tasks/coding-root?activity=false');
});
