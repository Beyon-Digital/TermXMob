import {afterEach, beforeEach, expect, it, vi} from 'vitest';
import {act, cleanup, render} from '@testing-library/react';
import OrchestratorStatus, {boardIsLive, COLLAPSED_BOARD_IDLE_MS, COLLAPSED_BOARD_LIVE_MS, type OrchestratorChild, type OrchestratorSnapshot} from './OrchestratorStatus';
import {gql, request, type Session} from '../lib/api';

vi.mock('../lib/api', () => ({gql: vi.fn(), request: vi.fn(), json: (method: string, data: unknown) => ({method, body: JSON.stringify(data)})}));
const api = vi.mocked(request);
const child: OrchestratorChild = {session_id: 'child-chat', task_id: 'child-task', title: 'Review access controls', status: 'running', engine: 'internal', origin: 'delegated', capability_limits: {time_budget: 'shared-host', tool_steps: 'shared-host', tokens: 'unmeasured', cost: 'unmeasured'}, control_owner: 'orchestrator', revision: 7, updated_at: 1000, parent_session_id: 'main'};
const snapshot = (children: OrchestratorChild[], cursor: number): OrchestratorSnapshot => ({session: {id: 'main', title: 'Main agent'} as Session, children, activity: [], cursor});
const props = {ownerId: 'owner', selected: 'main', enabled: true, onOpen: vi.fn(), onPrimary: vi.fn(), onRefresh: vi.fn()};
const board = (counts: Record<string, number>) => ({orchestrator_board: {nodes: [], rollup: {total: Object.values(counts).reduce((a, b) => a + b, 0), counts, percent_complete: 0, tokens: 0, cost_usd: null}}});
const boardReads = () => vi.mocked(gql).mock.calls.filter(([query]) => String(query).includes('orchestrator_board')).length;
const advance = (ms: number) => act(async () => { await vi.advanceTimersByTimeAsync(ms); });

beforeEach(() => { vi.useFakeTimers(); api.mockReset(); vi.mocked(gql).mockReset(); });
afterEach(() => { cleanup(); vi.useRealTimers(); });

it('classifies a plan with running, waiting or stalled nodes as live and fixes the two paces', () => {
  const rollup = (counts: Record<string, number>) => ({total: 1, counts, percent_complete: 0, tokens: 0, cost_usd: null});
  expect(boardIsLive(null)).toBe(false);
  expect(boardIsLive(rollup({completed: 3, failed: 1, cancelled: 1}))).toBe(false);
  for (const status of ['running', 'pending', 'stalled']) expect(boardIsLive(rollup({[status]: 1}))).toBe(true);
  expect([COLLAPSED_BOARD_LIVE_MS, COLLAPSED_BOARD_IDLE_MS]).toEqual([4000, 15000]);
});

it('re-reads the plan graph for the collapsed header every 4 seconds while planned work is live', async () => {
  vi.mocked(gql).mockResolvedValue(board({running: 1, pending: 1}));
  api.mockResolvedValue(snapshot([], 1));
  render(<OrchestratorStatus {...props}/>);
  await advance(0);
  // The first read finds live work, so the loop restarts at the 4 s pace with a fresh read.
  const start = boardReads();
  expect(start).toBeGreaterThanOrEqual(1);
  await advance(4100);
  expect(boardReads()).toBe(start + 1);
  await advance(4000);
  expect(boardReads()).toBe(start + 2);
  await advance(4000);
  expect(boardReads()).toBe(start + 3);
});

it('keeps a steady 15 second pace for the collapsed header when nothing is planned or running', async () => {
  vi.mocked(gql).mockResolvedValue(board({completed: 2}));
  api.mockResolvedValue(snapshot([], 1));
  render(<OrchestratorStatus {...props}/>);
  await advance(0);
  const start = boardReads();
  await advance(14000);
  expect(boardReads()).toBe(start);
  await advance(1500);
  expect(boardReads()).toBe(start + 1);
  // No backoff: an unchanged board is still read every 15 s, not every 30 or 60.
  await advance(15000);
  expect(boardReads()).toBe(start + 2);
  await advance(15000);
  expect(boardReads()).toBe(start + 3);
});

it('reads the board at once on a new orchestrator event and speeds up while a delegated chat runs', async () => {
  vi.mocked(gql).mockResolvedValue(board({completed: 1}));
  api.mockResolvedValue(snapshot([], 1));
  render(<OrchestratorStatus {...props}/>);
  await advance(0);
  const idle = boardReads();
  api.mockResolvedValue(snapshot([child], 2));
  // The status poll (every 4 s) sees the moved cursor and the running chat; the board is read right then, not 15 s later.
  await advance(4100);
  expect(boardReads()).toBeGreaterThan(idle);
  const live = boardReads();
  await advance(4100);
  expect(boardReads()).toBe(live + 1);
});
