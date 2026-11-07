import {act, cleanup, renderHook} from '@testing-library/react';
import {afterEach, beforeEach, expect, it, vi} from 'vitest';
import {useTaskEvents} from './task-events';

class FakeSocket {
  static instances: FakeSocket[] = [];
  readyState = 0;
  sent: Record<string, unknown>[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((event: {data: string}) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  constructor(public url: string, public protocol: string) { FakeSocket.instances.push(this); }
  send(data: string) { this.sent.push(JSON.parse(data)); }
  close() { this.readyState = 3; this.onclose?.(); }
  open() { this.readyState = 1; this.onopen?.(); }
  receive(message: Record<string, unknown>) { this.onmessage?.({data: JSON.stringify(message)}); }
}

beforeEach(() => { vi.useFakeTimers(); FakeSocket.instances = []; vi.stubGlobal('WebSocket', FakeSocket); });
afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); });

it('subscribes to agent_task_events over graphql-transport-ws and reports non-heartbeat events', () => {
  const onEvent = vi.fn(), onLive = vi.fn();
  renderHook(() => useTaskEvents('task-1', {onEvent, onLive}));
  const socket = FakeSocket.instances[0];
  expect(socket.url).toMatch(/^wss?:\/\/.+\/graphql$/);
  expect(socket.protocol).toBe('graphql-transport-ws');
  act(() => socket.open());
  expect(socket.sent[0]).toMatchObject({type: 'connection_init'});
  act(() => socket.receive({type: 'connection_ack'}));
  expect(socket.sent[1]).toMatchObject({type: 'subscribe', payload: {variables: {taskId: 'task-1'}}});
  expect(onLive).not.toHaveBeenCalled();
  act(() => socket.receive({type: 'next', id: socket.sent[1].id, payload: {data: {agent_task_events: {type: 'heartbeat', sequence: 0}}}}));
  expect(onLive).toHaveBeenLastCalledWith(true);
  expect(onEvent).not.toHaveBeenCalled();
  act(() => socket.receive({type: 'next', id: socket.sent[1].id, payload: {data: {agent_task_events: {type: 'task.completed', sequence: 4}}}}));
  expect(onEvent).toHaveBeenCalledWith(expect.objectContaining({type: 'task.completed', sequence: 4}));
});

it('does not connect while disabled or without a task, and closes on unmount', () => {
  const {rerender, unmount} = renderHook(({taskId}: {taskId: string | null}) => useTaskEvents(taskId, {onEvent: vi.fn()}), {initialProps: {taskId: null as string | null}});
  expect(FakeSocket.instances).toHaveLength(0);
  rerender({taskId: 'task-2'});
  expect(FakeSocket.instances).toHaveLength(1);
  const socket = FakeSocket.instances[0];
  unmount();
  expect(socket.readyState).toBe(3);
});

it('reports not live after the socket drops and reconnects with backoff', () => {
  const onLive = vi.fn();
  renderHook(() => useTaskEvents('task-3', {onEvent: vi.fn(), onLive}));
  const first = FakeSocket.instances[0];
  act(() => { first.open(); first.receive({type: 'connection_ack'}); first.receive({type: 'next', id: '1', payload: {data: {agent_task_events: {type: 'heartbeat', sequence: 0}}}}); });
  expect(onLive).toHaveBeenLastCalledWith(true);
  act(() => { first.readyState = 3; first.onclose?.(); });
  expect(onLive).toHaveBeenLastCalledWith(false);
  expect(FakeSocket.instances).toHaveLength(1);
  act(() => { vi.advanceTimersByTime(1000); });
  expect(FakeSocket.instances).toHaveLength(2);
});

it('does nothing when WebSocket is unavailable', () => {
  vi.stubGlobal('WebSocket', undefined);
  const onLive = vi.fn();
  renderHook(() => useTaskEvents('task-4', {onEvent: vi.fn(), onLive}));
  expect(onLive).not.toHaveBeenCalledWith(true);
});

it('stops reconnecting after the hidden tab closes the connection and resumes when visible', () => {
  renderHook(() => useTaskEvents('task-5', {onEvent: vi.fn()}));
  const first = FakeSocket.instances[0];
  Object.defineProperty(document, 'hidden', {configurable: true, value: true});
  act(() => { document.dispatchEvent(new Event('visibilitychange')); });
  expect(first.readyState).toBe(3);
  act(() => { vi.advanceTimersByTime(60000); });
  expect(FakeSocket.instances).toHaveLength(1);
  Object.defineProperty(document, 'hidden', {configurable: true, value: false});
  act(() => { document.dispatchEvent(new Event('visibilitychange')); });
  expect(FakeSocket.instances).toHaveLength(2);
});

it('does not reconnect or report live when the server rejects the subscription', () => {
  const onLive = vi.fn();
  renderHook(() => useTaskEvents('task-6', {onEvent: vi.fn(), onLive}));
  const first = FakeSocket.instances[0];
  act(() => { first.open(); first.receive({type: 'connection_ack'}); first.receive({type: 'error', id: '1', payload: [{message: 'Task not found'}]}); });
  act(() => { vi.advanceTimersByTime(60000); });
  expect(FakeSocket.instances).toHaveLength(1);
  expect(onLive).not.toHaveBeenCalled();
});

it('keeps backing off when the server completes the subscription without delivering anything', () => {
  renderHook(() => useTaskEvents('task-7', {onEvent: vi.fn()}));
  act(() => { FakeSocket.instances[0].open(); FakeSocket.instances[0].receive({type: 'connection_ack'}); FakeSocket.instances[0].receive({type: 'complete', id: '1'}); });
  act(() => { vi.advanceTimersByTime(1000); });
  expect(FakeSocket.instances).toHaveLength(2);
  act(() => { FakeSocket.instances[1].open(); FakeSocket.instances[1].receive({type: 'connection_ack'}); FakeSocket.instances[1].receive({type: 'complete', id: '1'}); });
  act(() => { vi.advanceTimersByTime(1000); });
  expect(FakeSocket.instances).toHaveLength(2);
  act(() => { vi.advanceTimersByTime(1000); });
  expect(FakeSocket.instances).toHaveLength(3);
});
