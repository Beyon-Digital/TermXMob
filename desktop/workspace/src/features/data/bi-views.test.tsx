import {afterEach, beforeEach, expect, it, vi} from 'vitest';
import {cleanup, fireEvent, render, screen, waitFor, within} from '@testing-library/react';
import {dataRequest} from '../../lib/data-studio';
import {biDefinition, biListText, biListValue} from '../../lib/data-bi';
import BIStudio from './BIStudio';
import type {ViewSummary} from '../../lib/data-views';

vi.mock('../../lib/data-studio', async original => ({...await original<typeof import('../../lib/data-studio')>(), dataRequest: vi.fn(), dataDownload: vi.fn()}));
const request = vi.mocked(dataRequest);
const view = {id: 'v1', kind: 'pivot', name: 'Sales by region', description: '', connection_id: 'c', base_table: 'sales', revision: 1} as unknown as ViewSummary;
const fields = [{name: 'pv_r1', label: 'Region', kind: 'text'}, {name: 'pv_total', label: 'Total', kind: 'integer'}];
const report = {id: 'r', revision: 1, name: 'On a view', view_id: 'v1', visual: {kind: 'table' as const, aggregate: 'none' as const}, filters: []};
const props = {workspaceId: 'w', connections: [], queries: [], role: 'owner' as const, views: [view], connectionId: 'c'};
function api(reports: unknown[]) {
  request.mockImplementation(async (path, init) => {
    if (path.endsWith('/reports') && !init?.method) return reports;
    if (path.endsWith('/dashboards')) return [];
    if (path.endsWith('/report-datasets/views/v1/fields')) return {view_id: 'v1', fields};
    throw new Error('Unexpected ' + path);
  });
}
beforeEach(() => {request.mockReset();});
afterEach(cleanup);

it('creates a report on a view without writing SQL', async () => {
  api([]);
  render(<BIStudio {...props}/>);
  await screen.findByText('No reports yet');
  fireEvent.click(screen.getByRole('button', {name: 'Create report'}));
  fireEvent.change(screen.getByLabelText('Name'), {target: {value: 'Regional'}});
  expect(screen.getByLabelText('Data')).toHaveValue('view:v1');
  request.mockImplementation(async (path, init) => {
    if (path.endsWith('/reports') && init?.method === 'POST') return {...report, name: 'Regional'};
    if (path.endsWith('/report-datasets/views/v1/fields')) return {view_id: 'v1', fields};
    throw new Error('Unexpected ' + path);
  });
  fireEvent.click(screen.getByRole('button', {name: 'Create'}));
  await screen.findByRole('heading', {name: 'Regional'});
  const post = request.mock.calls.find(([, init]) => init?.method === 'POST')!;
  expect(JSON.parse(String(post[1]?.body))).toEqual({name: 'Regional', view_id: 'v1', visual: {kind: 'table', aggregate: 'none'}, filters: []});
});

it('offers the fields of a view before the report has run, with names people read', async () => {
  api([{...report, visual: {kind: 'bar', x: 'pv_r1', y: 'pv_total', aggregate: 'sum'}}]);
  render(<BIStudio {...props}/>);
  await screen.findByRole('heading', {name: 'On a view'});
  const category = await screen.findByLabelText('Category');
  await waitFor(() => expect(within(category).getByRole('option', {name: 'Region'})).toBeInTheDocument());
  fireEvent.click(screen.getByRole('button', {name: 'Add filter'}));
  const field = screen.getByLabelText('Filter field');
  expect(within(field).getByRole('option', {name: 'Total'})).toBeInTheDocument();
});

it('edits an is any of filter as a comma separated list', async () => {
  api([{...report, filters: [{field: 'pv_r1', operator: 'eq', value: 'East'}]}]);
  render(<BIStudio {...props}/>);
  await screen.findByRole('heading', {name: 'On a view'});
  await screen.findByLabelText('Value');
  fireEvent.change(screen.getByLabelText('Condition'), {target: {value: 'in'}});
  const values = await screen.findByLabelText('Values');
  fireEvent.change(values, {target: {value: 'East, West,'}});
  expect(values).toHaveValue('East, West,');
  request.mockImplementation(async (path, init) => {
    if (path.endsWith('/reports/r') && init?.method === 'PATCH') return {...report, revision: 2};
    throw new Error('Unexpected ' + path);
  });
  fireEvent.click(screen.getByRole('button', {name: 'Save changes'}));
  await waitFor(() => expect(request.mock.calls.some(([, init]) => init?.method === 'PATCH')).toBe(true));
  const patch = JSON.parse(String(request.mock.calls.find(([, init]) => init?.method === 'PATCH')![1]?.body));
  expect(patch.filters).toEqual([{field: 'pv_r1', operator: 'in', value: ['East', 'West']}]);
  expect(patch.view_id).toBe('v1');
});

it('turns a chosen table into a plain table view for the report', async () => {
  api([]);
  render(<BIStudio {...props} views={[]} tables={[{name: 'orders', columns: [], readable: true} as never]}/>);
  await screen.findByText('No reports yet');
  fireEvent.click(screen.getByRole('button', {name: 'Create report'}));
  fireEvent.change(screen.getByLabelText('Name'), {target: {value: 'Orders report'}});
  expect(screen.getByLabelText('Data')).toHaveValue('table:orders');
  const created = {...view, id: 'v9', kind: 'table', name: 'Report data: orders', base_table: 'orders'};
  const onViewCreated = vi.fn();
  cleanup();
  api([]);
  render(<BIStudio {...props} views={[]} tables={[{name: 'orders', columns: [], readable: true} as never]} onViewCreated={onViewCreated}/>);
  await screen.findByText('No reports yet');
  fireEvent.click(screen.getByRole('button', {name: 'Create report'}));
  fireEvent.change(screen.getByLabelText('Name'), {target: {value: 'Orders report'}});
  request.mockImplementation(async (path, init) => {
    if (path.endsWith('/views') && init?.method === 'POST') return created;
    if (path.endsWith('/reports') && init?.method === 'POST') return {...report, view_id: 'v9', name: 'Orders report'};
    if (path.includes('/report-datasets/')) return {view_id: 'v9', fields};
    throw new Error('Unexpected ' + path);
  });
  fireEvent.click(screen.getByRole('button', {name: 'Create'}));
  await screen.findByRole('heading', {name: 'Orders report'});
  const made = JSON.parse(String(request.mock.calls.find(([path, init]) => path.endsWith('/views') && init?.method === 'POST')![1]?.body));
  expect(made).toMatchObject({kind: 'table', name: 'Report data: orders', spec: {source: 'c', from: {table: 'orders'}}});
  expect(onViewCreated).toHaveBeenCalledWith(created);
});

it('parses and prints list values', () => {
  expect(biListValue('a, b ,, 3', false)).toEqual(['a', 'b', '3']);
  expect(biListValue('1, 2, x', true)).toEqual([1, 2, 'x']);
  expect(biListText(['a', 2, null])).toBe('a, 2, ');
  expect(biDefinition({...report, query_id: undefined} as never)).toMatchObject({view_id: 'v1'});
});

it('shows the names people read instead of the view\'s SQL names in the result', async () => {
  const bar = {...report, visual: {kind: 'bar' as const, x: 'pv_r1', y: 'pv_total', aggregate: 'sum' as const}};
  api([bar]);
  render(<BIStudio {...props}/>);
  await screen.findByRole('heading', {name: 'On a view'});
  await screen.findByLabelText('Category');
  await waitFor(() => expect(screen.getAllByRole('option', {name: 'Total'}).length).toBeGreaterThan(0));
  const base = request.getMockImplementation()!;
  request.mockImplementation(async (path, init) => path.endsWith('/run') ? {id: 'j', status: 'completed', result: {columns: [{name: 'pv_r1'}, {name: 'pv_total'}], rows: [['East', 5], ['West', 7]], truncated: false}} : base(path, init));
  fireEvent.click(screen.getByRole('button', {name: 'Run report'}));
  const chart = await screen.findByRole('img', {name: /bar chart/});
  expect(chart.textContent).not.toContain('pv_r1');
  expect(chart.textContent).toContain('Region');
});
