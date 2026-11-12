import {act, cleanup, renderHook, waitFor} from '@testing-library/react';
import {afterEach, beforeEach, expect, it, vi} from 'vitest';
import {request} from '../../lib/api';
import type {DataConnection} from '../../lib/data-studio';
import {useDataSource} from './use-source';

vi.mock('../../lib/api', () => ({request: vi.fn(), ApiError: class ApiError extends Error {constructor(public status: number, message: string) {super(message);}}}));
const {ApiError} = await import('../../lib/api');
const api = vi.mocked(request), refresh = vi.fn();
const source: DataConnection = {id: 'source', revision: 1, name: 'Local database', engine: 'sqlite', created_by: 'owner', config: {path: '/host/business.sqlite'}};
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
