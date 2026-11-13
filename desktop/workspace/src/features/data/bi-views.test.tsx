import {createRef} from 'react';
import userEvent from '@testing-library/user-event';
import {afterEach, beforeEach, expect, it, vi} from 'vitest';
import {cleanup, fireEvent, render, screen, waitFor, within} from '@testing-library/react';
import {dataRequest} from '../../lib/data-studio';
import {biDefinition, biListText, biListValue, biSvg} from '../../lib/data-bi';
import BIStudio, {ReportResult} from './BIStudio';
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
  expect(screen.getByLabelText('Data')).toHaveTextContent('Sales by region');
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
  expect(screen.getByLabelText('Data')).toHaveTextContent('orders');
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

it('edits the dataset through the grouped picker and saves the selected identity', async () => {
  const user = userEvent.setup(), query = {id: 'q1', revision: 1, name: 'Saved sales', connection_id: 'c', sql: 'select 1'};
  api([report]);
  render(<BIStudio {...props} queries={[query]}/>);
  await screen.findByRole('heading', {name: 'On a view'});
  await user.click(screen.getByLabelText('Data'));
  await user.click(screen.getByRole('menuitemradio', {name: 'Saved sales'}));
  expect(screen.getByLabelText('Data')).toHaveTextContent('Saved sales');
  request.mockResolvedValue({...report, query_id: 'q1', view_id: undefined, revision: 2});
  await user.click(screen.getByRole('button', {name: 'Save changes'}));
  await waitFor(() => expect(request.mock.calls.some(([, init]) => init?.method === 'PATCH')).toBe(true));
  const saved = JSON.parse(String(request.mock.calls.find(([, init]) => init?.method === 'PATCH')![1]?.body));
  expect(saved).toMatchObject({query_id: 'q1', visual: {kind: 'table', aggregate: 'none'}, filters: []});
  expect(saved).not.toHaveProperty('view_id');
});

it('allows New report for view data and excludes unreadable tables', async () => {
  const user = userEvent.setup();
  api([]);
  render(<BIStudio {...props} tables={[{name: 'private_view', columns: [], readable: false} as never]}/>);
  await screen.findByText('No reports yet');
  expect(screen.getByRole('button', {name: 'New report'})).toBeEnabled();
  await user.click(screen.getByRole('button', {name: 'New report'}));
  await user.click(screen.getByLabelText('Data'));
  expect(screen.getByRole('menuitemradio', {name: 'Sales by region'})).toBeInTheDocument();
  expect(screen.queryByRole('group', {name: 'Tables'})).not.toBeInTheDocument();
  expect(screen.queryByRole('menuitemradio', {name: /private/})).not.toBeInTheDocument();
  await user.keyboard('{Escape}{Escape}');
});

it('labels viewer and dashboard results from the job without fetching editor-only fields', async () => {
  const user = userEvent.setup(), bar = {...report, published: true, visual: {kind: 'bar', x: 'pv_r1', y: 'pv_total', aggregate: 'none'}};
  request.mockImplementation(async path => {
    if (path.endsWith('/reports')) return [bar];
    if (path.endsWith('/dashboards')) return [{id: 'd', revision: 1, name: 'Sales dashboard', report_ids: ['r']}];
    if (path.endsWith('/reports/r/run')) return {id: 'j', revision: 1, status: 'completed', connection_id: 'c',
      output: {columns: [{alias: 'pv_r1', label: 'Region', role: 'column', value_kind: 'text'}, {alias: 'pv_total', label: 'Sales total', role: 'column', value_kind: 'number'}], warnings: []},
      result: {columns: [{name: 'pv_r1'}, {name: 'pv_total'}], rows: [['East', 5], ['West', 7]], row_count: 2, truncated: false}};
    throw new Error('Unexpected ' + path);
  });
  render(<BIStudio {...props} role="viewer"/>);
  await screen.findByRole('heading', {name: 'On a view'});
  await user.click(screen.getByRole('button', {name: 'Run report'}));
  expect((await screen.findByRole('img')).querySelector('desc')).toHaveTextContent('Region by Sales total');
  await user.click(screen.getByRole('button', {name: 'View data table'}));
  expect(screen.getByRole('columnheader', {name: 'Sales total'})).toBeInTheDocument();
  await user.click(screen.getByRole('button', {name: 'Dashboards'}));
  await user.click(screen.getByRole('button', {name: 'Run dashboard'}));
  await waitFor(() => expect(screen.getByRole('img').querySelector('desc')).toHaveTextContent('Region by Sales total'));
  expect(screen.getByRole('img').textContent).not.toMatch(/pv_r1|pv_total/);
  expect(request.mock.calls.some(([path]) => path.includes('/fields'))).toBe(false);
});

it('keeps duplicate display labels separate by raw alias, including SVG export and table data', () => {
  const svgRef = createRef<SVGSVGElement>(), bar = {...report, visual: {kind: 'bar' as const, x: 'pv_r1', y: 'pv_total', aggregate: 'none' as const}};
  const job = {id: 'j', revision: 1, status: 'completed' as const, connection_id: 'c',
    output: {columns: [{alias: 'pv_total', label: 'Sales', role: 'column' as const, value_kind: 'number' as const}, {alias: 'pv_r1', label: 'Sales', role: 'column' as const, value_kind: 'text' as const}], warnings: []},
    result: {columns: [{name: 'pv_r1'}, {name: 'pv_total'}], rows: [['East', 5], ['West', 7]], row_count: 2, truncated: false}};
  const {container} = render(<ReportResult report={bar} job={job} svgRef={svgRef}/>);
  expect(screen.getByRole('img').querySelector('desc')).toHaveTextContent('Sales by Sales');
  expect(container.querySelector('svg rect title')).toHaveTextContent('East: 5');
  const exported = biSvg(svgRef.current!);
  expect(exported).toContain('Sales by Sales');
  expect(exported).not.toMatch(/pv_r1|pv_total/);
  fireEvent.click(screen.getByRole('button', {name: 'View data table'}));
  expect(screen.getAllByRole('columnheader', {name: 'Sales'})).toHaveLength(2);
  expect(job.result.columns.map(column => column.name)).toEqual(['pv_r1', 'pv_total']);
  expect(bar.visual).toMatchObject({x: 'pv_r1', y: 'pv_total'});
});

it('labels an aggregated KPI using its returned alias and backend visual', () => {
  render(<ReportResult report={{...report, visual: {kind: 'metric', y: 'pv_total', aggregate: 'sum'}}}
    job={{id: 'j', revision: 1, status: 'completed', connection_id: 'c',
      report_context: {visual: {kind: 'metric', y: 'sum', aggregate: 'sum'}},
      output: {columns: [{alias: 'sum', label: 'Sum of sales total', role: 'aggregate', value_kind: 'number'}], warnings: []},
      result: {columns: [{name: 'sum'}], rows: [[12]], row_count: 1, truncated: false}}}/>);
  expect(screen.getByText('Sum of sales total')).toBeInTheDocument();
  expect(screen.getByText('12')).toBeInTheDocument();
});

it.each(['owner', 'editor'] as const)('opens and runs an archived published report as %s without fetching the archived view, then rebinds it', async role => {
  const user = userEvent.setup(), currentView = {...view, id: 'v2', name: 'Current sales'};
  const archived = {...report, published_version_id: 'published', using_published_version: true,
    visual: {kind: 'bar' as const, x: 'pv_r1', y: 'pv_total', aggregate: 'none' as const}};
  const rebound = {...report, view_id: 'v2', revision: 2};
  request.mockImplementation(async (path, init) => {
    if (path.endsWith('/reports')) return [archived];
    if (path.endsWith('/dashboards')) return [];
    if (path.endsWith('/reports/r/run')) return {id: 'j', revision: 1, status: 'completed', connection_id: 'c',
      output: {columns: [{alias: 'pv_r1', label: 'Region', role: 'column', value_kind: 'text'}, {alias: 'pv_total', label: 'Sales total', role: 'column', value_kind: 'number'}], warnings: []},
      result: {columns: [{name: 'pv_r1'}, {name: 'pv_total'}], rows: [['East', 5]], row_count: 1, truncated: false}};
    if (path.endsWith('/report-datasets/views/v2/fields')) return {view_id: 'v2', fields};
    if (path.endsWith('/reports/r') && init?.method === 'PATCH') return rebound;
    throw new Error('View not found');
  });
  render(<BIStudio {...props} role={role} views={[currentView]}/>);
  await screen.findByRole('heading', {name: 'On a view'});
  expect(screen.getByText('Published version · source view archived')).toBeInTheDocument();
  expect(screen.getByText('Choose different data to edit this report.')).toBeInTheDocument();
  expect(screen.queryByText('Loading the fields of this view…')).not.toBeInTheDocument();
  expect(screen.getByRole('button', {name: 'Publish to workspace'})).toBeDisabled();
  await user.click(screen.getByRole('button', {name: 'Run report'}));
  expect((await screen.findByRole('img')).querySelector('desc')).toHaveTextContent('Region by Sales total');
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  expect(request.mock.calls.some(([path]) => path.endsWith('/report-datasets/views/v1/fields'))).toBe(false);
  await user.click(screen.getByLabelText('Data'));
  await user.click(screen.getByRole('menuitemradio', {name: 'Current sales'}));
  await waitFor(() => expect(request.mock.calls.some(([path]) => path.endsWith('/report-datasets/views/v2/fields'))).toBe(true));
  expect(screen.getByRole('button', {name: 'Save changes'})).toBeEnabled();
  await user.click(screen.getByRole('button', {name: 'Save changes'}));
  await waitFor(() => expect(screen.queryByText('Published version · source view archived')).not.toBeInTheDocument());
  const saved = JSON.parse(String(request.mock.calls.find(([, init]) => init?.method === 'PATCH')![1]?.body));
  expect(saved).toMatchObject({revision: 1, view_id: 'v2', visual: {kind: 'table', aggregate: 'none'}, filters: []});
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
});

it('keeps recovery guidance and the Data picker available when an archived table report is edited', async () => {
  const user = userEvent.setup(), query = {id: 'q1', revision: 1, name: 'Current sales', connection_id: 'c', sql: 'select 1'};
  api([{...report, published_version_id: 'published', using_published_version: true}]);
  render(<BIStudio {...props} views={[]} queries={[query]}/>);
  await screen.findByRole('heading', {name: 'On a view'});
  await user.type(screen.getByLabelText('Name'), ' renamed');
  expect(screen.getByText('Choose different data to edit this report.')).toBeInTheDocument();
  expect(screen.getByLabelText('Data')).toBeEnabled();
  expect(screen.getByRole('button', {name: 'Save changes'})).toBeDisabled();
  expect(screen.getByRole('button', {name: 'Run report'})).toBeDisabled();
  expect(request.mock.calls.some(([path]) => path.includes('/fields'))).toBe(false);
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
});
