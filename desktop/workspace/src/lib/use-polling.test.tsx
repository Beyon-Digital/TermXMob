import {act, cleanup, renderHook} from '@testing-library/react';
import {afterEach, beforeEach, expect, it, vi} from 'vitest';
import {usePolling} from './use-polling';

const setHidden = (hidden: boolean) => {
  Object.defineProperty(document, 'hidden', {configurable: true, value: hidden});
  document.dispatchEvent(new Event('visibilitychange'));
};
const advance = (ms: number) => act(async () => { await vi.advanceTimersByTimeAsync(ms); });

beforeEach(() => { vi.useFakeTimers(); setHidden(false); });
afterEach(() => { cleanup(); vi.useRealTimers(); setHidden(false); });

it('runs immediately and then on every interval while backoff is off', async () => {
  const poll = vi.fn(async () => 1);
  renderHook(() => usePolling(poll, {interval: 1000, backoff: false}));
  await advance(0);
  expect(poll).toHaveBeenCalledTimes(1);
  await advance(2000);
  expect(poll).toHaveBeenCalledTimes(3);
});

it('does nothing while disabled and starts when enabled', async () => {
  const poll = vi.fn(async () => 1);
  const {rerender} = renderHook(({enabled}) => usePolling(poll, {interval: 1000, enabled}), {initialProps: {enabled: false}});
  await advance(5000);
  expect(poll).not.toHaveBeenCalled();
  rerender({enabled: true});
  await advance(0);
  expect(poll).toHaveBeenCalledTimes(1);
});

it('skips ticks while the document is hidden and refetches when it becomes visible', async () => {
  const poll = vi.fn(async () => 1);
  renderHook(() => usePolling(poll, {interval: 1000, backoff: false}));
  await advance(0);
  poll.mockClear();
  act(() => setHidden(true));
  await advance(5000);
  expect(poll).not.toHaveBeenCalled();
  await act(async () => { setHidden(false); await vi.advanceTimersByTimeAsync(0); });
  expect(poll).toHaveBeenCalledTimes(1);
});

it('backs off while the result is unchanged and resets when it changes', async () => {
  let value = 1;
  const poll = vi.fn(async () => value);
  renderHook(() => usePolling(poll, {interval: 1000, maxInterval: 4000}));
  await advance(0);
  await advance(1000);
  expect(poll).toHaveBeenCalledTimes(2);
  await advance(1999);
  expect(poll).toHaveBeenCalledTimes(2);
  await advance(1);
  expect(poll).toHaveBeenCalledTimes(3);
  value = 2;
  await advance(4000);
  expect(poll).toHaveBeenCalledTimes(4);
  await advance(1000);
  expect(poll).toHaveBeenCalledTimes(5);
});

it('aborts the in-flight request on unmount and ignores its result', async () => {
  let signal!: AbortSignal;
  const onResult = vi.fn();
  const poll = vi.fn((s: AbortSignal) => { signal = s; return new Promise<number>(resolve => setTimeout(() => resolve(1), 500)); });
  const {unmount} = renderHook(() => usePolling(poll, {interval: 1000, onResult}));
  await advance(0);
  expect(signal.aborted).toBe(false);
  unmount();
  expect(signal.aborted).toBe(true);
  await advance(5000);
  expect(onResult).not.toHaveBeenCalled();
  expect(poll).toHaveBeenCalledTimes(1);
});

it('delivers results to the latest callback', async () => {
  const first = vi.fn(), second = vi.fn();
  const poll = vi.fn(async () => 7);
  const {rerender} = renderHook(({cb}) => usePolling(poll, {interval: 1000, onResult: cb}), {initialProps: {cb: first}});
  rerender({cb: second});
  await advance(0);
  expect(first).not.toHaveBeenCalled();
  expect(second).toHaveBeenCalledWith(7);
});

it('keeps polling after a failed request and reports the error', async () => {
  const onError = vi.fn();
  const poll = vi.fn().mockRejectedValueOnce(new Error('offline')).mockResolvedValue(1);
  renderHook(() => usePolling(poll, {interval: 1000, onError, backoff: false}));
  await advance(0);
  expect(onError).toHaveBeenCalledWith(expect.objectContaining({message: 'offline'}));
  await advance(1000);
  expect(poll).toHaveBeenCalledTimes(2);
});

it('aborts the in-flight request when the tab is hidden', async () => {
  let signal!: AbortSignal;
  const poll = vi.fn((s: AbortSignal) => { signal = s; return new Promise<number>(resolve => setTimeout(() => resolve(1), 500)); });
  renderHook(() => usePolling(poll, {interval: 1000}));
  await advance(0);
  expect(signal.aborted).toBe(false);
  act(() => setHidden(true));
  expect(signal.aborted).toBe(true);
});

it('restarts the loop with the new interval when options change', async () => {
  const poll = vi.fn(async () => 1);
  const {rerender} = renderHook(({interval}) => usePolling(poll, {interval, backoff: false}), {initialProps: {interval: 1000}});
  await advance(0);
  rerender({interval: 200});
  await advance(0);
  poll.mockClear();
  await advance(600);
  expect(poll).toHaveBeenCalledTimes(3);
});

it('treats structurally equal object results as unchanged by default', async () => {
  const poll = vi.fn(async () => ({revision: 1, items: ['a']}));
  renderHook(() => usePolling(poll, {interval: 1000, maxInterval: 4000}));
  await advance(0);
  await advance(1000);
  expect(poll).toHaveBeenCalledTimes(2);
  await advance(1999);
  expect(poll).toHaveBeenCalledTimes(2);
  await advance(1);
  expect(poll).toHaveBeenCalledTimes(3);
});

it('uses a custom isEqual to decide whether a result changed', async () => {
  let n = 0;
  const poll = vi.fn(async () => ({revision: 1, n: n++}));
  renderHook(() => usePolling(poll, {interval: 1000, maxInterval: 4000, isEqual: (a, b) => a.revision === b.revision}));
  await advance(0);
  await advance(1000);
  await advance(1999);
  expect(poll).toHaveBeenCalledTimes(2);
  await advance(1);
  expect(poll).toHaveBeenCalledTimes(3);
});

it('never backs off below the base interval when maxInterval is smaller', async () => {
  const poll = vi.fn(async () => 1);
  renderHook(() => usePolling(poll, {interval: 1000, maxInterval: 300}));
  await advance(0);
  await advance(1000);
  expect(poll).toHaveBeenCalledTimes(2);
  await advance(999);
  expect(poll).toHaveBeenCalledTimes(2);
  await advance(1);
  expect(poll).toHaveBeenCalledTimes(3);
});

it('restarts with a fresh request and aborts the old one when resetKey changes', async () => {
  const signals: AbortSignal[] = [];
  const poll = vi.fn((s: AbortSignal) => { signals.push(s); return new Promise<number>(resolve => setTimeout(() => resolve(1), 500)); });
  const {rerender, unmount} = renderHook(({key}) => usePolling(poll, {interval: 1000, resetKey: key}), {initialProps: {key: 'a'}});
  await advance(0);
  rerender({key: 'b'});
  await advance(0);
  expect(signals[0].aborted).toBe(true);
  expect(poll).toHaveBeenCalledTimes(2);
  unmount();
  expect(signals[1].aborted).toBe(true);
});

it('waits one interval before the first request when immediate is false', async () => {
  const poll = vi.fn(async () => 1);
  renderHook(() => usePolling(poll, {interval: 1000, immediate: false, backoff: false}));
  await advance(999);
  expect(poll).not.toHaveBeenCalled();
  await advance(1);
  expect(poll).toHaveBeenCalledTimes(1);
});

it('does not report or back off when onResult throws', async () => {
  const onError = vi.fn();
  vi.spyOn(console, 'error').mockImplementation(() => {});
  let n = 0;
  const onResult = vi.fn(() => { throw new Error('consumer bug'); });
  const poll = vi.fn(async () => n++);
  renderHook(() => usePolling(poll, {interval: 1000, onResult, onError}));
  await advance(0);
  expect(onResult).toHaveBeenCalledTimes(1);
  expect(onError).not.toHaveBeenCalled();
  await advance(1000);
  expect(poll).toHaveBeenCalledTimes(2);
});
