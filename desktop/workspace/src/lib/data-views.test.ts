import {afterEach, beforeEach, expect, it, vi} from 'vitest';
import {ApiError, request} from './api';
import {archiveView, createView, drillView, fetchHistory, fetchRuns, getView, listViews, runView, runViewToCompletion, updateView, viewError, viewExportPath} from './data-views';
import conflict from './view-contract-fixtures/view-conflict.json';
import inUse from './view-contract-fixtures/view-delete-in-use.json';
import createPivot from './view-contract-fixtures/view-create-pivot.json';
import drillCell from './view-contract-fixtures/drill-pivot-cell.json';
import runJob from './view-contract-fixtures/run-job.json';
import tooMany from './view-contract-fixtures/compile-pivot-too-many.json';
import type {QuerySpec} from './query-spec';

vi.mock('./api', () => ({request: vi.fn(), ApiError: class ApiError extends Error {constructor(public status: number, message: string) {super(message);}}}));
const api = vi.mocked(request);
const spec = createPivot.request.spec as unknown as QuerySpec;
beforeEach(() => {vi.clearAllMocks(); api.mockResolvedValue({});});
afterEach(() => vi.useRealTimers());

it('reads a coded error out of the JSON text the API layer keeps in the message', () => {
  const error = viewError(new ApiError(409, JSON.stringify(conflict.response.detail)));
  expect(error).toMatchObject({status: 409, code: 'revision_conflict', current_revision: 5, updated_by: {name: 'Bob'}});
  expect(error.message).toContain('Bob changed this view');
});
it('lists who uses a segment that cannot be deleted', () => {
  const error = viewError(new ApiError(409, JSON.stringify(inUse.response.detail)));
  expect(error.code).toBe('segment_in_use');
  expect(error.used_by?.map(row => row.name)).toEqual(['Sales by region and month', 'Orders east']);
});
it('keeps the compiler errors of an invalid spec', () => {
  const error = viewError(new ApiError(422, JSON.stringify({code: 'invalid_spec', message: 'Fix the pivot.', errors: tooMany.response.errors})));
  expect(error.errors?.[0].code).toBe('pivot_too_many_columns');
});
it('keeps plain 403 and 404 text as it is', () => {
  expect(viewError(new ApiError(403, 'Only the workspace owner can delete this view.'))).toEqual({status: 403, message: 'Only the workspace owner can delete this view.'});
  expect(viewError(new ApiError(404, 'Not found'))).toMatchObject({status: 404, message: 'Not found'});
});
it('never shows raw JSON for malformed or unknown details', () => {
  expect(viewError(new ApiError(422, '{"detail": [')).message).not.toContain('{');
  expect(viewError(new ApiError(422, '[{"loc":["body"],"msg":"x"}]')).message).not.toContain('[{');
  expect(viewError(new ApiError(500, JSON.stringify({code: 'something_new', message: 'A new problem.'}))).message).toBe('A new problem.');
  expect(viewError('plain').message).toBe('plain');
  expect(viewError(undefined).message).toBe('Something went wrong. Try again.');
});

it('builds the view routes', async () => {
  await listViews('w 1', {kind: 'pivot', connectionId: 'c1'});
  expect(api.mock.calls[0][0]).toBe('/api/data/workspaces/w%201/views?kind=pivot&connection_id=c1');
  await getView('w1', 'v/1');
  expect(api.mock.calls[1][0]).toBe('/api/data/workspaces/w1/views/v%2F1');
  await createView('w1', {kind: 'pivot', name: 'P', spec});
  expect(api.mock.calls[2][1]).toMatchObject({method: 'POST'});
  expect(JSON.parse(String(api.mock.calls[2][1]?.body))).toEqual({kind: 'pivot', name: 'P', spec});
  await updateView('w1', 'v1', {revision: 3, name: 'Q'});
  expect(api.mock.calls[3][1]).toMatchObject({method: 'PATCH'});
  expect(JSON.parse(String(api.mock.calls[3][1]?.body))).toEqual({revision: 3, name: 'Q'});
  await archiveView('w1', 'v1', 3);
  expect(api.mock.calls[4][0]).toBe('/api/data/workspaces/w1/views/v1?revision=3');
  expect(api.mock.calls[4][1]).toMatchObject({method: 'DELETE'});
  await fetchHistory('w1', 'v1', {before: 4});
  expect(api.mock.calls[5][0]).toBe('/api/data/workspaces/w1/views/v1/history?limit=20&before=4');
  await fetchRuns('w1', 'v1', {before: 'a|b'});
  expect(api.mock.calls[6][0]).toBe('/api/data/workspaces/w1/views/v1/runs?limit=20&before=a%7Cb');
  await drillView('w1', 'v1', drillCell.request.coordinates);
  expect(JSON.parse(String(api.mock.calls[7][1]?.body))).toEqual({coordinates: drillCell.request.coordinates});
  expect(viewExportPath('w1', 'v1', 'j1')).toBe('/api/data/workspaces/w1/views/v1/runs/j1/export.csv');
});
it('sends one request id per run and only the options that were chosen', async () => {
  await runView('w1', 'v1', {mode: 'count'});
  const body = JSON.parse(String(api.mock.calls[0][1]?.body));
  expect(body.mode).toBe('count');
  expect(body).not.toHaveProperty('offset');
  expect(typeof body.request_id).toBe('string');
});

it('follows a view run until it finishes', async () => {
  vi.useFakeTimers();
  const queued = {...runJob.response, id: 'j1', status: 'queued'}, running = {...queued, status: 'running'}, done = {...queued, status: 'completed', result: {columns: [], rows: [[1]], row_count: 1, truncated: false}};
  api.mockResolvedValueOnce(queued).mockResolvedValueOnce(running).mockResolvedValueOnce(done);
  const seen: string[] = [];
  const promise = runViewToCompletion('w1', 'v1', {interval: 10, onJob: job => seen.push(job.status)});
  await vi.advanceTimersByTimeAsync(50);
  expect((await promise).status).toBe('completed');
  expect(seen).toEqual(['queued', 'running', 'completed']);
  expect(api.mock.calls[1][0]).toBe('/api/data/workspaces/w1/jobs/j1');
});
it('stops polling when the caller aborts', async () => {
  vi.useFakeTimers();
  api.mockResolvedValueOnce({...runJob.response, id: 'j1', status: 'running'});
  const abort = new AbortController(), promise = runViewToCompletion('w1', 'v1', {interval: 1000, signal: abort.signal});
  const settled = promise.catch(reason => reason);
  await vi.advanceTimersByTimeAsync(10);
  abort.abort();
  expect(((await settled) as DOMException).name).toBe('AbortError');
  expect(api).toHaveBeenCalledTimes(1);
});
