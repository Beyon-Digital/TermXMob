import {act, cleanup, renderHook, waitFor} from '@testing-library/react';
import {afterEach, beforeEach, expect, it, vi} from 'vitest';
import {request} from '../../lib/api';
import type {DataConnection} from '../../lib/data-studio';
import {useDataSource} from './use-source';

vi.mock('../../lib/api', () => ({request: vi.fn(), ApiError: class ApiError extends Error {constructor(public status: number, message: string) {super(message);}}}));
const {ApiError} = await import('../../lib/api');
const api = vi.mocked(request), refresh = vi.fn();
const source: DataConnection = {id: 'source', revision: 1, name: 'Local database', engine: 'sqlite', created_by: 'owner', config: {path: '/host/business.sqlite'}};
const generated = {...source, source_generation: 1, credential_generation: 0, read_policy: {verified: false}};
beforeEach(() => {vi.clearAllMocks(); api.mockImplementation(async path => {throw Error('Unexpected request: ' + path);});});
afterEach(cleanup);

it('contacts nothing until schema or test is requested', () => {
  const {result} = renderHook(() => useDataSource('workspace', source, refresh));
  expect(api).not.toHaveBeenCalled();
  expect(result.current.schemaStatus).toBe('idle');
});

it('loads the schema for the selected source on request', async () => {
  api.mockResolvedValue({tables: [{name: 'orders', columns: []}]});
  const {result} = renderHook(() => useDataSource('workspace', source, refresh));
  act(() => result.current.loadSchema());
  await waitFor(() => expect(result.current.schemaStatus).toBe('ready'));
  expect(result.current.schema?.tables[0].name).toBe('orders');
  expect(api).toHaveBeenCalledWith('/api/data/workspaces/workspace/connections/source/schema', expect.anything(), true);
});

it('drops a schema response after the selected source changes', async () => {
  let resolve!: (value: unknown) => void;
  api.mockReturnValue(new Promise(done => {resolve = done;}));
  const {result, rerender} = renderHook(({row}) => useDataSource('workspace', row, refresh), {initialProps: {row: source}});
  act(() => result.current.loadSchema());
  rerender({row: {...source, id: 'second'}});
  await act(async () => resolve({tables: [{name: 'private_previous_table', columns: []}]}));
  expect(result.current.schema).toBeNull();
  expect(result.current.schemaStatus).toBe('idle');
});

it('clears the schema when the profile revision changes', async () => {
  api.mockResolvedValue({tables: [{name: 'orders', columns: []}]});
  const {result, rerender} = renderHook(({row}) => useDataSource('workspace', row, refresh), {initialProps: {row: source}});
  act(() => result.current.loadSchema());
  await waitFor(() => expect(result.current.schemaStatus).toBe('ready'));
  rerender({row: {...source, revision: 2}});
  expect(result.current.schema).toBeNull();
});

it('keeps an explicitly requested schema while a successful test refresh arrives later', async () => {
  let resolveSchema!: (value: unknown) => void;
  api.mockImplementation(async path => path.endsWith('/test') ? {ok: true, message: 'Verified'}
    : new Promise(done => {resolveSchema = done;}));
  const {result, rerender} = renderHook(({row}) => useDataSource('workspace', row, refresh), {initialProps: {row: generated}});
  act(() => result.current.testConnection());
  await waitFor(() => expect(result.current.testing).toBe(false));
  expect(refresh).toHaveBeenCalledOnce();
  expect(api).toHaveBeenCalledTimes(1); // Testing alone never loads the schema.
  act(() => result.current.loadSchema());
  rerender({row: {...generated, revision: 2, read_policy: {verified: true}}});
  expect(result.current.schemaStatus).toBe('loading');
  await act(async () => resolveSchema({tables: [{name: 'orders', columns: []}]}));
  expect(result.current.schemaStatus).toBe('ready');
  expect(result.current.schema?.tables[0].name).toBe('orders');
  expect(api).toHaveBeenCalledTimes(2);
});

it('keeps loaded tables on a successful verification receipt without contacting the source again', async () => {
  api.mockResolvedValue({tables: [{name: 'orders', columns: []}]});
  const {result, rerender} = renderHook(({row}) => useDataSource('workspace', row, refresh), {initialProps: {row: generated}});
  act(() => result.current.loadSchema());
  await waitFor(() => expect(result.current.schemaStatus).toBe('ready'));
  rerender({row: {...generated, revision: 2, read_policy: {verified: true}}});
  expect(result.current.schema?.tables[0].name).toBe('orders');
  expect(result.current.schemaStatus).toBe('ready');
  expect(api).toHaveBeenCalledOnce();
});

const changes: [string, (row: DataConnection) => DataConnection | undefined][] = [
  ['source generation', row => ({...row, source_generation: 2})],
  ['credential generation', row => ({...row, credential_generation: 1})],
  ['config', row => ({...row, config: {path: '/host/another.sqlite'}})],
  ['engine', row => ({...row, engine: 'postgresql'})],
  ['shared access', row => ({...row, shared_read: false})],
  ['ownership', row => ({...row, created_by: 'another-person'})],
  ['configured credential', row => ({...row, credential_configured: false})],
  ['configured password', row => ({...row, password_configured: false})],
  ['verification', row => ({...row, read_policy: {verified: false}})],
  ['removed verification', row => ({...row, read_policy: undefined})],
  ['redacted permissions', row => ({...row, config: {}, source_generation: undefined, credential_generation: undefined, read_policy: undefined})],
  ['partial generations', row => ({...row, credential_generation: undefined})],
  ['source disappearance', () => undefined],
];

it.each(changes)('drops a pending schema when %s changes', async (_name, change) => {
  let resolve!: (value: unknown) => void;
  api.mockReturnValue(new Promise(done => {resolve = done;}));
  const initial: DataConnection = {...generated, shared_read: true, credential_configured: true, password_configured: true, read_policy: {verified: true}};
  const {result, rerender} = renderHook(({row}: {row: DataConnection | undefined}) => useDataSource('workspace', row, refresh), {initialProps: {row: initial as DataConnection | undefined}});
  act(() => result.current.loadSchema());
  rerender({row: change(initial)});
  await act(async () => resolve({tables: [{name: 'private_previous_table', columns: []}]}));
  expect(result.current.schema).toBeNull();
  expect(result.current.schemaStatus).toBe('idle');
  expect(api).toHaveBeenCalledOnce();
});

it('drops a schema after switching workspace even if the source identifier is unchanged', async () => {
  let resolve!: (value: unknown) => void;
  api.mockReturnValue(new Promise(done => {resolve = done;}));
  const {result, rerender} = renderHook(({wid}) => useDataSource(wid, generated, refresh), {initialProps: {wid: 'first'}});
  act(() => result.current.loadSchema());
  rerender({wid: 'second'});
  await act(async () => resolve({tables: [{name: 'private_previous_table', columns: []}]}));
  expect(result.current.schema).toBeNull();
  expect(api).toHaveBeenCalledOnce();
});

it('uses the revision fallback when only one generation is available', async () => {
  api.mockResolvedValue({tables: [{name: 'orders', columns: []}]});
  const partial = {...source, source_generation: 1};
  const {result, rerender} = renderHook(({row}) => useDataSource('workspace', row, refresh), {initialProps: {row: partial}});
  act(() => result.current.loadSchema());
  await waitFor(() => expect(result.current.schemaStatus).toBe('ready'));
  rerender({row: {...partial, revision: 2}});
  expect(result.current.schema).toBeNull();
});

it.each(['unverified', 'network error'])('clears cached tables after a test returns %s without implicitly loading again', async outcome => {
  api.mockResolvedValueOnce({tables: [{name: 'orders', columns: []}]});
  const {result} = renderHook(() => useDataSource('workspace', generated, refresh));
  act(() => result.current.loadSchema());
  await waitFor(() => expect(result.current.schemaStatus).toBe('ready'));
  if (outcome === 'unverified') api.mockResolvedValueOnce({ok: false, message: 'Role could not be verified'});
  else api.mockRejectedValueOnce(new Error('Host unavailable'));
  act(() => result.current.testConnection());
  await waitFor(() => expect(result.current.schemaStatus).toBe('error'));
  expect(result.current.schema).toBeNull();
  expect(result.current.needsTest).toBe(true);
  expect(refresh).not.toHaveBeenCalled();
  expect(api).toHaveBeenCalledTimes(2);
});

it('reports a specific failed test without refreshing', async () => {
  api.mockResolvedValue({ok: false, message: 'Read-only role could not be verified.'});
  const {result} = renderHook(() => useDataSource('workspace', source, refresh));
  act(() => result.current.testConnection());
  await waitFor(() => expect(result.current.testMessage).toBe('Read-only role could not be verified.'));
  expect(refresh).not.toHaveBeenCalled();
  expect(api).toHaveBeenCalledTimes(1);
});

it('refreshes the workspace after a passing test', async () => {
  api.mockResolvedValue({ok: true, message: ''});
  const {result} = renderHook(() => useDataSource('workspace', source, refresh));
  act(() => result.current.testConnection());
  await waitFor(() => expect(refresh).toHaveBeenCalled());
  expect(result.current.testMessage).toBe('Connection succeeded.');
});

it('asks for a source test when the server answers 409', async () => {
  api.mockRejectedValue(new ApiError(409, 'Test this source first to verify its current read-only role and session.'));
  const {result} = renderHook(() => useDataSource('workspace', source, refresh));
  act(() => result.current.loadSchema());
  await waitFor(() => expect(result.current.schemaStatus).toBe('error'));
  expect(result.current.needsTest).toBe(true);
  expect(result.current.schemaError).toContain('Test this source first');
});

it('keeps other failures as plain errors', async () => {
  api.mockRejectedValue(new Error('Host unavailable'));
  const {result} = renderHook(() => useDataSource('workspace', source, refresh));
  act(() => result.current.loadSchema());
  await waitFor(() => expect(result.current.schemaStatus).toBe('error'));
  expect(result.current.needsTest).toBe(false);
});

it('ignores a second request while one is in flight', () => {
  api.mockReturnValue(new Promise(() => {}));
  const {result} = renderHook(() => useDataSource('workspace', source, refresh));
  act(() => {result.current.loadSchema(); result.current.loadSchema();});
  expect(api).toHaveBeenCalledTimes(1);
});
