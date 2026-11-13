import {afterEach, beforeEach, expect, it, vi} from 'vitest';
import {cleanup, render, screen, waitFor, within} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import DataStudio from './DataStudio';
import {request} from '../../lib/api';
import {sizeGridScrollers} from '../../test-utils/virtual';
import '../../test-utils/codemirror';
import type {DataBootstrap} from '../../lib/data-studio';

vi.mock('../../lib/api', () => ({request: vi.fn(), ApiError: class ApiError extends Error {constructor(public status: number, message: string) {super(message);}}}));
vi.mock('../../lib/transfers', () => ({transfer: vi.fn()}));
vi.mock('./BIStudio', () => ({default: () => <section aria-label="BI reports"/>}));
vi.mock('./DataCopilot', () => ({default: () => <section aria-label="Copilot conversation"/>}));

const engine = {id: 'sqlite', label: 'SQLite', status: 'qualified', ready: true, fields: [], capabilities: {read: true, schema: true, cancel: true, export_csv: true}};
const tables = [
  {name: 'orders', columns: [{name: 'id', type: 'INTEGER', primary_key: true, value_kind: 'integer'}, {name: 'customer_id', type: 'INTEGER', value_kind: 'integer'}], row_estimate: null},
  {name: 'customers', columns: [{name: 'id', type: 'INTEGER', primary_key: true, value_kind: 'integer'}], row_estimate: 40}];
const api = vi.mocked(request);
const job = (rows: unknown[][]) => ({id: 'j', revision: 1, status: 'completed', connection_id: 'source', result: {columns: [], rows, row_count: rows.length, truncated: false}});
let counted: unknown[];
beforeEach(() => {
  vi.clearAllMocks(); sizeGridScrollers(); localStorage.clear(); counted = [];
  window.history.replaceState({}, '', '/?workspace=data');
  api.mockImplementation(async (path, init) => {
    const body = init?.body ? JSON.parse(String(init.body)) : {};
    if (path === '/api/data/bootstrap') return {workspaces: [{id: 'w', revision: 1, name: 'Analytics', role: 'owner'}], engines: [engine], capabilities: {}} satisfies DataBootstrap;
    if (path === '/api/data/workspaces/w') return {
      workspace: {id: 'w', revision: 1, name: 'Analytics', role: 'owner'}, members: [],
      connections: [{id: 'source', revision: 1, name: 'Orders source', engine: 'sqlite', config: {path: '/host/orders.sqlite'}, created_by: 'owner'}],
      queries: [], reports: [], dashboards: [], activity: [],
    };
    if (path.endsWith('/connections/source/schema')) return {tables};
    if (path.endsWith('/query-specs/compile')) {
      const spec = body.spec;
      return {ok: true, dialect: 'sqlite', sql: 'SELECT 1', parameters: {}, limit: 200, offset: 0, fetch_limit: 201, joins: [], schema_hash: 'h', warnings: [],
        columns: spec.aggregates.length ? [{label: 'Number of rows', value_kind: 'integer', role: 'aggregate', fn: 'count'}]
          : [{label: 'id', value_kind: 'integer', role: 'column', table_alias: spec.from.alias, column: 'id'}]};
    }
    if (path.endsWith('/query-specs/run')) {
      if (body.spec.aggregates.length) {counted.push(body.spec); return job([[1305]]);}
      return job([[1], [2]]);
    }
    if (path.endsWith('/relations')) return {connection_id: 'source', relations: []};
    if (path.endsWith('/capabilities')) return {connection_id: 'source', supported: true, dialect: {filter_ops: ['eq'], date_bucket: {buckets: ['month']}}};
    throw new Error('Unexpected request: ' + path);
  });
});
afterEach(() => {cleanup(); vi.restoreAllMocks();});

async function overview(user: ReturnType<typeof userEvent.setup>) {
  render(<DataStudio ownerId="owner" visible/>);
  await screen.findByRole('heading', {name: /source$/});
  const region = screen.getByRole('region', {name: 'Tables overview'});
  await user.click(within(region).getByRole('button', {name: 'Load tables'}));
  await within(region).findByRole('table');
  return region;
}

it('counts the rows of a table from the overview with the real count spec', async () => {
  const user = userEvent.setup(), region = await overview(user);
  await user.click(within(region).getByRole('button', {name: 'Count rows in orders'}));
  expect(await within(region).findByText('1,305')).toBeInTheDocument();
  expect(counted).toEqual([expect.objectContaining({source: 'source', from: {table: 'orders', alias: 'orders'}, aggregates: [{fn: 'count'}], limit: 1})]);
});

it('opens a table with the Combine panel showing from the More menu on the overview', async () => {
  const user = userEvent.setup(), region = await overview(user);
  await user.click(within(region).getByRole('button', {name: 'More actions for orders'}));
  await user.click(await screen.findByRole('menuitem', {name: 'Combine with another table'}));
  expect(await screen.findByRole('tab', {name: 'orders', selected: true})).toBeInTheDocument();
  expect(await screen.findByRole('dialog', {name: 'Combine with another table'})).toBeInTheDocument();
});

it('keeps a table tab state when the person visits the overview and comes back', async () => {
  const user = userEvent.setup(), region = await overview(user);
  await user.click(within(region).getByRole('button', {name: 'Open orders'}));
  await screen.findByRole('cell', {name: '1'});
  await user.click(screen.getByRole('button', {name: 'Summarize'}));
  expect(await screen.findByRole('region', {name: 'Summarize'})).toBeInTheDocument();
  await user.click(screen.getByRole('tab', {name: 'Overview'}));
  await user.click(screen.getByRole('tab', {name: 'orders'}));
  expect(screen.getByRole('region', {name: 'Summarize'})).toBeVisible();
  const runsBefore = api.mock.calls.filter(([path]) => path.endsWith('/query-specs/run')).length;
  await new Promise(done => setTimeout(done, 50));
  expect(api.mock.calls.filter(([path]) => path.endsWith('/query-specs/run')).length).toBe(runsBefore);
});

it('returns focus to the Advanced button when the SQL editor closes with Escape', async () => {
  const user = userEvent.setup();
  render(<DataStudio ownerId="owner" visible/>);
  await screen.findByRole('heading', {name: /source$/});
  await user.click(screen.getByRole('button', {name: 'Advanced'}));
  await user.click(await screen.findByRole('menuitemcheckbox', {name: 'SQL editor'}));
  const panel = await screen.findByRole('dialog', {name: 'SQL editor'});
  await waitFor(() => expect(panel.querySelector('.cm-editor.cm-focused')).not.toBeNull());
  await user.keyboard('{Escape}');
  await waitFor(() => expect(screen.queryByRole('dialog', {name: 'SQL editor'})).not.toBeInTheDocument());
  await waitFor(() => expect(screen.getByRole('button', {name: 'Advanced'})).toHaveFocus());
});

it('does not take focus for a SQL editor that was left open from the last visit', async () => {
  localStorage.setItem('termx.data.advanced.owner', 'true');
  localStorage.setItem('termx.data.tabs.owner.w', JSON.stringify({tabs: [{id: 'overview', kind: 'overview'}, {id: 'query:new', kind: 'query'}], activeId: 'query:new'}));
  render(<DataStudio ownerId="owner" visible/>);
  const panel = await screen.findByRole('dialog', {name: 'SQL editor'});
  await screen.findByLabelText('SQL query');
  expect(panel.querySelector('.cm-editor.cm-focused')).toBeNull();
  expect(panel.contains(document.activeElement)).toBe(false);
});
