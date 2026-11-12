import {afterEach, beforeEach, expect, it, vi} from 'vitest';
import {cleanup, render, screen, waitFor} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import DataStudio from './DataStudio';
import {request} from '../../lib/api';
import {sizeGridScrollers} from '../../test-utils/virtual';
import '../../test-utils/codemirror';
import type {QuerySpec} from '../../lib/query-spec';

const spec: QuerySpec = {version: 1, source: 'source', from: {table: 'orders', alias: 'o'}, joins: [], select: [{table_alias: 'o', column: 'id'}],
  filters: {op: 'and', items: [{field: {table_alias: 'o', column: 'customer_id'}, op: 'eq', value: 7}]}, group_by: [], aggregates: [], sort: [], limit: 50, offset: 0};
const pivot: QuerySpec = {...spec, select: [], pivot: {rows: [{table_alias: 'o', column: 'customer_id'}], columns: null, value: {fn: 'count'}, totals: true, sort: {by: 'rows', direction: 'asc'}}};

vi.mock('../../lib/api', () => ({request: vi.fn(), ApiError: class ApiError extends Error {constructor(public status: number, message: string) {super(message);}}}));
vi.mock('../../lib/transfers', () => ({transfer: vi.fn()}));
vi.mock('./BIStudio', () => ({default: () => <section aria-label="BI reports"/>}));
vi.mock('./DataCopilot', () => ({default: (props: {role: string; connectionId?: string; onApplyTable: (spec: QuerySpec, title: string, note: string) => void; onOpenPivot: (spec: QuerySpec, title: string) => void; onSaveSegment: (spec: QuerySpec, name: string, description: string) => Promise<unknown>}) =>
  <section aria-label="Copilot conversation"><p>Role {props.role}, source {props.connectionId}</p>
    <button onClick={() => props.onApplyTable(spec, 'Orders for customer 7', 'Suggested by Copilot.')}>Test apply</button>
    <button onClick={() => props.onOpenPivot(pivot, 'Orders by customer')}>Test pivot</button>
    <button onClick={() => void props.onSaveSegment({...spec, select: []}, 'Customer 7', '')}>Test segment</button></section>}));

const tables = [{name: 'orders', columns: [{name: 'id', type: 'INTEGER', primary_key: true, value_kind: 'integer'}, {name: 'customer_id', type: 'INTEGER', value_kind: 'integer'}], row_estimate: null}];
const api = vi.mocked(request);
let created: unknown[];
beforeEach(() => {
  vi.clearAllMocks(); sizeGridScrollers(); localStorage.clear(); created = [];
  window.history.replaceState({}, '', '/?workspace=data');
  api.mockImplementation(async (path, init) => {
    const body = init?.body ? JSON.parse(String(init.body)) : {};
    if (path === '/api/data/bootstrap') return {workspaces: [{id: 'w', revision: 1, name: 'Analytics', role: 'owner'}], engines: [{id: 'sqlite', label: 'SQLite', status: 'qualified', ready: true, fields: [], capabilities: {read: true, schema: true, cancel: true, export_csv: true}}], capabilities: {views: true}};
    if (path === '/api/data/workspaces/w') return {workspace: {id: 'w', revision: 1, name: 'Analytics', role: 'owner'}, members: [],
      connections: [{id: 'source', revision: 1, name: 'Orders source', engine: 'sqlite', config: {path: '/host/orders.sqlite'}, created_by: 'owner'}], queries: [], reports: [], dashboards: [], activity: []};
    if (path.endsWith('/connections/source/schema')) return {tables};
    if (path.endsWith('/views') && init?.method === 'POST') {created.push(body); return {id: 'v1', revision: 1, name: body.name, kind: body.kind, connection_id: 'source', base_table: 'orders', spec: body.spec};}
    if (path.includes('/views')) return {views: []};
    if (path.endsWith('/capabilities')) return {connection_id: 'source', supported: true, dialect: {filter_ops: ['eq'], date_bucket: {buckets: ['month']}}};
    if (path.endsWith('/relations')) return {connection_id: 'source', relations: []};
    if (path.endsWith('/query-specs/compile')) return {ok: true, dialect: 'sqlite', sql: 'SELECT 1', parameters: {}, limit: 50, offset: 0, fetch_limit: 51, joins: [], schema_hash: 'h', warnings: [], columns: [{label: 'id', value_kind: 'integer', role: 'column', table_alias: 'o', column: 'id'}]};
    if (path.endsWith('/query-specs/run')) return {id: 'j', revision: 1, status: 'completed', connection_id: 'source', result: {columns: [], rows: [], row_count: 0, truncated: false}};
    return {};
  });
});
afterEach(() => {cleanup(); vi.restoreAllMocks();});

async function openCopilot(user: ReturnType<typeof userEvent.setup>) {
  render(<DataStudio ownerId="owner" visible/>);
  await screen.findByRole('heading', {name: /source$/});
  await user.click(screen.getByRole('button', {name: 'Copilot'}));
  return screen.getByRole('complementary', {name: 'Data Copilot'});
}

it('mounts Copilot in the resizable right slot with the person\'s role and source', async () => {
  const user = userEvent.setup(), panel = await openCopilot(user);
  expect(panel).toContainElement(screen.getByRole('separator', {name: 'Resize Copilot'}));
  expect(screen.getByText('Role owner, source source')).toBeInTheDocument();
});

it('opens an applied table suggestion as its own unsaved tab without saving anything', async () => {
  const user = userEvent.setup();
  await openCopilot(user);
  await user.click(screen.getByRole('button', {name: 'Test apply'}));
  expect(await screen.findByRole('tab', {name: /Orders for customer 7/})).toHaveAttribute('aria-selected', 'true');
  expect(created).toEqual([]);
});

it('opens a pivot suggestion as a new pivot tab seeded with the suggestion', async () => {
  const user = userEvent.setup();
  await openCopilot(user);
  await user.click(screen.getByRole('button', {name: 'Test pivot'}));
  expect(await screen.findByRole('tab', {name: /New pivot on orders/})).toHaveAttribute('aria-selected', 'true');
  await waitFor(() => expect(api.mock.calls.some(([path, init]) => String(path).endsWith('/query-specs/compile') && JSON.parse(String(init?.body)).spec.pivot?.rows[0].column === 'customer_id')).toBe(true));
  expect(created).toEqual([]);
});

it('saves a segment only when the card asks, as a segment view', async () => {
  const user = userEvent.setup();
  await openCopilot(user);
  await user.click(screen.getByRole('button', {name: 'Test segment'}));
  await waitFor(() => expect(created).toHaveLength(1));
  expect(created[0]).toMatchObject({kind: 'segment', name: 'Customer 7', spec: {select: []}});
});
