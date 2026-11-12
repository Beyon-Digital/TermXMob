import {afterEach, beforeEach, expect, it, vi} from 'vitest';
import {cancelJob, compileSpec, fetchCapabilities, fetchRelations, runSpec, runSpecToCompletion} from './data-query';
import {request} from './api';
import {emptySpec} from './query-builder';

vi.mock('./api', () => ({request: vi.fn(), ApiError: class ApiError extends Error {constructor(public status: number, message: string) {super(message);}}}));
const api = vi.mocked(request), spec = emptySpec('c1', 'orders');
const job = (status: string, extra: object = {}) => ({id: 'j1', revision: 1, status, connection_id: 'c1', ...extra});
const body = (call: number) => JSON.parse(String(api.mock.calls[call][1]?.body));

beforeEach(() => api.mockReset());
afterEach(() => vi.useRealTimers());

it('posts the spec to compile and run, and escapes ids in the path', async () => {
  api.mockResolvedValue({});
  await compileSpec('w 1', spec);
  await runSpec('w 1', spec, 'req-1');
  await fetchRelations('w 1', 'c/1', 'my table');
  await fetchCapabilities('w 1', 'c/1');
  await cancelJob('w 1', 'j/1');
  expect(api.mock.calls.map(call => call[0])).toEqual([
    '/api/data/workspaces/w%201/query-specs/compile', '/api/data/workspaces/w%201/query-specs/run',
    '/api/data/workspaces/w%201/connections/c%2F1/relations?table=my%20table', '/api/data/workspaces/w%201/connections/c%2F1/capabilities',
    '/api/data/workspaces/w%201/jobs/j%2F1/cancel']);
  expect(body(0)).toEqual({spec});
  expect(body(1)).toEqual({request_id: 'req-1', spec});
});

it('polls a queued job until it is finished and reports every state', async () => {
  vi.useFakeTimers();
  api.mockResolvedValueOnce(job('queued')).mockResolvedValueOnce(job('running')).mockResolvedValueOnce(job('completed', {result: {rows: [[1]]}}));
  const seen: string[] = [];
  const done = runSpecToCompletion('w', spec, {interval: 100, onJob: row => seen.push(row.status)});
  await vi.advanceTimersByTimeAsync(250);
  expect((await done).status).toBe('completed');
  expect(seen).toEqual(['queued', 'running', 'completed']);
  expect(api.mock.calls.map(call => call[0]).slice(1)).toEqual(['/api/data/workspaces/w/jobs/j1', '/api/data/workspaces/w/jobs/j1']);
});

it('returns a job that already finished without polling', async () => {
  api.mockResolvedValueOnce(job('failed', {error: 'The query ran longer than 30 seconds and was stopped.'}));
  const result = await runSpecToCompletion('w', spec);
  expect(result.error).toContain('stopped');
  expect(api).toHaveBeenCalledTimes(1);
});

it('uses the request id it is given, so a retry is idempotent', async () => {
  api.mockResolvedValueOnce(job('completed'));
  await runSpecToCompletion('w', spec, {requestId: 'same'});
  expect(body(0).request_id).toBe('same');
});

it('stops polling when aborted', async () => {
  vi.useFakeTimers();
  api.mockResolvedValue(job('running'));
  const abort = new AbortController(), caught = runSpecToCompletion('w', spec, {signal: abort.signal, interval: 100}).catch(error => error);
  await vi.advanceTimersByTimeAsync(150);
  abort.abort();
  expect(await caught).toMatchObject({name: 'AbortError'});
  const calls = api.mock.calls.length;
  await vi.advanceTimersByTimeAsync(1000);
  expect(api.mock.calls.length).toBe(calls);
});

it('passes the 409 that asks for a source test straight to the caller', async () => {
  api.mockRejectedValueOnce(Object.assign(new Error('Test this source first to verify its current read-only role and session.'), {status: 409}));
  await expect(runSpecToCompletion('w', spec)).rejects.toMatchObject({status: 409, message: expect.stringContaining('Test this source first')});
});

it('passes a 429 from the run caps to the caller with its message', async () => {
  api.mockRejectedValueOnce(Object.assign(new Error('You already have 4 queries running. Wait for one to finish or cancel it, then run again.'), {status: 429}));
  await expect(runSpecToCompletion('w', spec)).rejects.toMatchObject({status: 429, message: expect.stringContaining('already have 4 queries running')});
});
