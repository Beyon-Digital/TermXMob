import {act, cleanup, renderHook} from '@testing-library/react';
import {afterEach, beforeEach, expect, it, vi} from 'vitest';
import type {Session} from './api';
import {keepIdentity, useSessionSnapshot} from './use-session-snapshot';

const sockets: {url: string; onopen?: () => void; onmessage?: (event: {data: string}) => void; onclose?: () => void; close: () => void; send: (data: string) => void}[] = [];
class FakeSocket {
  onopen?: () => void; onmessage?: (event: {data: string}) => void; onclose?: () => void;
  constructor(public url: string) { sockets.push(this); }
  send() {}
  close() { this.onclose?.(); }
}
const setHidden = (hidden: boolean) => { Object.defineProperty(document, 'hidden', {configurable: true, value: hidden}); document.dispatchEvent(new Event('visibilitychange')); };
const advance = (ms: number) => act(async () => { await vi.advanceTimersByTimeAsync(ms); });
const row = (patch: Partial<Session> = {}): Session => ({id: 's1', title: 's1', engine: 'internal', mode: 'ask', pinned: false, archived: false, draft_text: '', revision: 1, updated_at: 1, scroll: 0, turns: [], ...patch} as Session);

const runningRow = (patch: Partial<Session> = {}) => row({turns: [{id: 'turn-1', prompt: 'go', task: {id: 'task-1', status: 'running', engine: 'internal'}}] as never, ...patch});

beforeEach(() => { vi.useFakeTimers(); sockets.length = 0; vi.stubGlobal('WebSocket', FakeSocket); setHidden(false); });
afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); setHidden(false); });

const options = (read: (id: string) => Promise<Session>, extra: Partial<Parameters<typeof useSessionSnapshot>[0]> = {}) => ({selected: 's1', authenticated: true, active: true, read, onError: vi.fn(), ...extra});

it('keeps the previous object when the polled snapshot is unchanged', () => {
  const first = row({turns: [{id: 't1'}] as never});
  expect(keepIdentity(first, row({turns: [{id: 't1'}] as never}))).toBe(first);
  const next = row({revision: 2});
  expect(keepIdentity(first, next)).toBe(next);
  expect(keepIdentity(first, row({turns: [{id: 't1'}, {id: 't2'}] as never}))).not.toBe(first);
  expect(keepIdentity(row({id: 'other'}), row())).not.toBe(null);
  expect(keepIdentity(null, first)).toBe(first);
});

it('polls the selected session and does not re-render with an identical snapshot', async () => {
  const read = vi.fn(async () => row());
  const renders = vi.fn();
  const {result} = renderHook(() => { const value = useSessionSnapshot(options(read)); renders(value[0]); return value; });
  await advance(0);
  const first = result.current[0];
  expect(first?.id).toBe('s1');
  await advance(1500);
  expect(read).toHaveBeenCalledTimes(2);
  expect(result.current[0]).toBe(first);
});

it('stops all work on unmount and while the tab is hidden', async () => {
  const read = vi.fn(async () => row());
  const {unmount} = renderHook(() => useSessionSnapshot(options(read)));
  await advance(0);
  read.mockClear();
  act(() => setHidden(true));
  await advance(20000);
  expect(read).not.toHaveBeenCalled();
  await act(async () => { setHidden(false); await vi.advanceTimersByTimeAsync(0); });
  expect(read).toHaveBeenCalledTimes(1);
  unmount();
  read.mockClear();
  await advance(60000);
  expect(read).not.toHaveBeenCalled();
});

it('clears the snapshot when nothing is selected and restarts when the selection changes', async () => {
  const read = vi.fn(async (id: string) => row({id}));
  const {result, rerender} = renderHook((props: {selected: string}) => useSessionSnapshot(options(read, props)), {initialProps: {selected: 's1'}});
  await advance(0);
  expect(result.current[0]?.id).toBe('s1');
  rerender({selected: 's2'});
  await advance(0);
  expect(read).toHaveBeenLastCalledWith('s2');
  expect(result.current[0]?.id).toBe('s2');
  rerender({selected: ''});
  await advance(0);
  expect(result.current[0]).toBeNull();
});

it('does not poll while the workspace is inactive', async () => {
  const read = vi.fn(async () => row());
  renderHook(() => useSessionSnapshot(options(read, {active: false})));
  await advance(10000);
  expect(read).not.toHaveBeenCalled();
});

it('reports polling failures once per failure and keeps going', async () => {
  const onError = vi.fn();
  const read = vi.fn().mockRejectedValueOnce(new Error('offline')).mockResolvedValue(row());
  const {result} = renderHook(() => useSessionSnapshot(options(read, {onError})));
  await advance(0);
  expect(onError).toHaveBeenCalledWith(expect.objectContaining({message: 'offline'}));
  await advance(3000);
  expect(result.current[0]?.id).toBe('s1');
});

it('follows a running task through the event stream and slows polling while it is live', async () => {
  let revision = 1;
  const read = vi.fn(async () => runningRow({revision}));
  renderHook(() => useSessionSnapshot(options(read)));
  await advance(0);
  expect(sockets).toHaveLength(1);
  read.mockClear();
  // The stream goes live: the safety-net poll is far slower than 1.5 s.
  const socket = sockets[0];
  await act(async () => { socket.onopen?.(); socket.onmessage?.({data: JSON.stringify({type: 'connection_ack'})}); socket.onmessage?.({data: JSON.stringify({type: 'next', payload: {data: {agent_task_events: {type: 'heartbeat', sequence: 0}}}})}); });
  await advance(0);
  read.mockClear();
  await advance(5000);
  expect(read).not.toHaveBeenCalled();
  // An event triggers a coalesced refresh.
  revision = 2;
  await act(async () => {
    socket.onmessage?.({data: JSON.stringify({type: 'next', payload: {data: {agent_task_events: {type: 'tool.output', sequence: 2}}}})});
    socket.onmessage?.({data: JSON.stringify({type: 'next', payload: {data: {agent_task_events: {type: 'tool.output', sequence: 3}}}})});
    await vi.advanceTimersByTimeAsync(500);
  });
  expect(read).toHaveBeenCalledTimes(1);
});

it('returns to the fast poll when the stream drops', async () => {
  const read = vi.fn(async () => runningRow());
  renderHook(() => useSessionSnapshot(options(read)));
  await advance(0);
  const socket = sockets[0];
  await act(async () => { socket.onopen?.(); socket.onmessage?.({data: JSON.stringify({type: 'connection_ack'})}); socket.onmessage?.({data: JSON.stringify({type: 'next', payload: {data: {agent_task_events: {type: 'heartbeat', sequence: 0}}}})}); });
  await act(async () => { socket.onclose?.(); });
  read.mockClear();
  await advance(1600);
  expect(read).toHaveBeenCalled();
});

it('restarts the loop when the principal changes while the same conversation stays selected', async () => {
  const read = vi.fn(async () => row());
  const {rerender} = renderHook((props: {principalId: string}) => useSessionSnapshot(options(read, props)), {initialProps: {principalId: 'a'}});
  await advance(0);
  read.mockClear();
  rerender({principalId: 'b'});
  await advance(0);
  expect(read).toHaveBeenCalledTimes(1);
});
