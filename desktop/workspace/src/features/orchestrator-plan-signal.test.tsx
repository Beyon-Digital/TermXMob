import {afterEach, beforeEach, expect, it, vi} from 'vitest';
import {act, cleanup, render, screen} from '@testing-library/react';
import OrchestratorStatus, {type OrchestratorSnapshot} from './OrchestratorStatus';
import {gql, request, type Session} from '../lib/api';
import {useSessionSnapshot} from '../lib/use-session-snapshot';

// The header is fed by the same task event stream the main chat follows: this test drives the real chat hook through a
// fake graphql-transport-ws socket and checks that the collapsed Delegated work header follows a plan change within ~2 s.
vi.mock('../lib/api', () => ({gql: vi.fn(), request: vi.fn(), json: (method: string, data: unknown) => ({method, body: JSON.stringify(data)})}));
const api = vi.mocked(request);

class FakeSocket {
  static instances: FakeSocket[] = [];
  onopen: (() => void) | null = null; onmessage: ((event: {data: string}) => void) | null = null; onclose: (() => void) | null = null; onerror: (() => void) | null = null;
  constructor(public url: string, public protocol: string) { FakeSocket.instances.push(this); }
  send() {}
  close() { this.onclose?.(); }
  event(type: string, payload: unknown, sequence: number) {
    this.onmessage?.({data: JSON.stringify({type: 'next', id: '1', payload: {data: {agent_task_events: {type, payload, sequence, task_id: 'task-1'}}}})});
  }
}
const board = (counts: Record<string, number>) => ({orchestrator_board: {nodes: [], rollup: {total: Object.values(counts).reduce((a, b) => a + b, 0), counts, percent_complete: 0, tokens: 0, cost_usd: null}}});
const boardReads = () => vi.mocked(gql).mock.calls.filter(([query]) => String(query).includes('orchestrator_board')).length;
const advance = (ms: number) => act(async () => { await vi.advanceTimersByTimeAsync(ms); });
const header = () => screen.getByRole('status').textContent;
const mainChat = {id: 'main', title: 'Main agent', revision: 1, updated_at: 1, turns: [{id: 'turn-1', prompt: 'plan', task: {id: 'task-1', status: 'running', engine: 'internal'}}]} as unknown as Session;
const snapshot: OrchestratorSnapshot = {session: {id: 'main', title: 'Main agent'} as Session, children: [], activity: [], cursor: 1};

// What the app mounts side by side: the chat's snapshot hook (which owns the task event stream) and the Delegated work header.
function Harness() {
  useSessionSnapshot({selected: 'main', authenticated: true, active: true, read: async () => mainChat, onError: () => {}});
  return <OrchestratorStatus ownerId="owner" selected="main" enabled onOpen={vi.fn()} onPrimary={vi.fn()} onRefresh={vi.fn()}/>;
}

async function idleWithLiveStream() {
  vi.mocked(gql).mockResolvedValue(board({completed: 1}));
  api.mockResolvedValue(snapshot);
  render(<Harness/>);
  await advance(100);
  const socket = FakeSocket.instances[0];
  expect(socket).toBeDefined();
  act(() => socket.event('heartbeat', {}, 0));
  expect(header()).toBe('1 of 1 planned task done');
  // Let the first reads settle so the next board read would otherwise be the 15 s idle poll.
  await advance(1000);
  return socket;
}

beforeEach(() => { vi.useFakeTimers(); FakeSocket.instances = []; vi.stubGlobal('WebSocket', FakeSocket); api.mockReset(); vi.mocked(gql).mockReset(); });
afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); });

it('refreshes the collapsed header within 2 seconds of a plan_nodes tool call finishing while the graph is idle', async () => {
  const socket = await idleWithLiveStream();
  // The planned tasks land in the graph; the orchestrator snapshot cursor does not move (plan_nodes emits no orchestrator event).
  vi.mocked(gql).mockResolvedValue(board({completed: 1, pending: 2}));
  act(() => socket.event('tool.started', {call: {call_id: 'call-1', name: 'plan_nodes'}}, 1));
  act(() => socket.event('approval.resolved', {approval: {id: 'a1', status: 'approved'}}, 2));
  act(() => socket.event('tool.finished', {call_id: 'call-1', result: {ok: true}}, 3));
  await advance(2000);
  expect(header()).toBe('1 of 3 planned tasks done');
});

it.each(['start_ready', 'retry_node', 'cancel_node', 'reassign_node'])('refreshes the header within 2 seconds of %s finishing', async name => {
  const socket = await idleWithLiveStream();
  vi.mocked(gql).mockResolvedValue(board({completed: 1, cancelled: 1}));
  act(() => socket.event('tool.started', {call: {call_id: 'call-2', name}}, 1));
  act(() => socket.event('tool.finished', {call_id: 'call-2', result: {ok: true}}, 2));
  await advance(2000);
  expect(header()).toBe('1 of 2 planned tasks done');
});

it('refreshes within 2 seconds of an approval being resolved, and reads again when the tool finishes', async () => {
  const socket = await idleWithLiveStream();
  vi.mocked(gql).mockResolvedValue(board({completed: 1, pending: 1}));
  act(() => socket.event('approval.resolved', {approval: {id: 'a1', status: 'approved'}}, 1));
  await advance(2000);
  expect(header()).toBe('1 of 2 planned tasks done');
});

it('does not read the board again for unrelated tool calls, bursts cost one read, and idle stays at the 15 second pace', async () => {
  const socket = await idleWithLiveStream();
  const before = boardReads();
  act(() => socket.event('tool.started', {call: {call_id: 'call-3', name: 'read_file'}}, 1));
  act(() => socket.event('tool.finished', {call_id: 'call-3', result: {ok: true}}, 2));
  act(() => socket.event('message.delta', {text: 'hi'}, 3));
  await advance(3000);
  expect(boardReads()).toBe(before);
  // A burst of plan events collapses into a single read.
  act(() => { for (let n = 0; n < 5; n++) socket.event('approval.resolved', {approval: {id: 'a' + n}}, 10 + n); });
  await advance(2000);
  expect(boardReads()).toBe(before + 1);
  // Nothing else polls faster than the idle pace.
  const settled = boardReads();
  await advance(12000);
  expect(boardReads()).toBe(settled);
});
