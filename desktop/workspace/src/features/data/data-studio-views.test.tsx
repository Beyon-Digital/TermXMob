import {afterEach, beforeEach, expect, it, vi} from 'vitest';
import {act, cleanup, fireEvent, render, screen, waitFor, within} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import DataStudio, {NOTICE_MS} from './DataStudio';
import {request} from '../../lib/api';
import {sizeGridScrollers} from '../../test-utils/virtual';
import {expectNoAxeViolations} from '../../test-axe';
import type {DataBootstrap, DataSnapshot, DataWorkspace} from '../../lib/data-studio';
import type {ViewSummary} from '../../lib/data-views';

vi.mock('../../lib/api', () => ({request: vi.fn(), ApiError: class ApiError extends Error {constructor(public status: number, message: string) {super(message);}}}));
vi.mock('../../lib/transfers', () => ({transfer: vi.fn()}));
vi.mock('./BIStudio', () => ({default: () => <section aria-label="BI reports"/>}));
vi.mock('./DataCopilot', () => ({default: () => <section aria-label="Copilot conversation"/>}));

const owner: DataWorkspace = {id: 'workspace', revision: 1, name: 'Analytics', role: 'owner'}, second: DataWorkspace = {id: 'second', revision: 1, name: 'Finance', role: 'owner'};
const engine = {id: 'sqlite', label: 'SQLite', status: 'qualified', ready: true, fields: [{name: 'path', label: 'Database path on host', type: 'file' as const, required: true}], capabilities: {read: true, schema: true, cancel: true, export_csv: true}};
const person = {id: 'p', name: 'Alice'};
const summary = (id: string, kind: ViewSummary['kind'], name: string, extra: Partial<ViewSummary> = {}): ViewSummary => ({id, kind, name, description: '', connection_id: 'source', base_table: 'orders', revision: 1, created_by: person, updated_by: person,
  created_at: '2026-11-13T09:00:00+00:00', updated_at: '2026-11-13T09:00:00+00:00', used_by_count: 0, can_edit: true, can_run: true, ...extra});
const snapshot = (workspace = owner, role: DataWorkspace['role'] = 'owner'): DataSnapshot => ({workspace: {...workspace, role}, members: [], connections: [{id: workspace.id === 'workspace' ? 'source' : 'source-2', revision: 1, name: workspace.id === 'workspace' ? 'Orders source' : 'Finance source', engine: 'sqlite', config: {path: '/host/a.sqlite'}, created_by: 'owner'}], queries: [], reports: [], dashboards: [], activity: []});
const tables = [{name: 'orders', columns: [{name: 'id', type: 'INTEGER', value_kind: 'integer' as const}, {name: 'status', type: 'TEXT', value_kind: 'text' as const}], row_estimate: 40}, {name: 'customers', columns: [{name: 'id', type: 'INTEGER', value_kind: 'integer' as const}], row_estimate: 12}];
const api = vi.mocked(request);
let role: DataWorkspace['role'], capabilities: Record<string, unknown>, viewList: ViewSummary[], signals: AbortSignal[];

beforeEach(() => {
  vi.clearAllMocks(); sizeGridScrollers(); localStorage.clear(); window.history.replaceState({}, '', '/?workspace=data');
  role = 'owner'; capabilities = {views: true, pivot: true, segments: true}; signals = [];
  viewList = [summary('p1', 'pivot', 'Sales by region'), summary('s1', 'segment', 'Big orders', {used_by_count: 2})];
  api.mockImplementation(async (path, init) => {
    const method = init?.method || 'GET';
    if (path === '/api/data/bootstrap') return {workspaces: [{...owner, role}, {...second, role}], engines: [engine], capabilities} satisfies DataBootstrap;
    if (path.endsWith('/views') && method === 'GET') {if (init?.signal) signals.push(init.signal); return {views: viewList};}
    if (path.endsWith('/connections/source/schema')) return {tables};
    if (path.endsWith('/connections/source') && method === 'PATCH') return {...snapshot().connections[0], revision: 2};
    if (path === '/api/data/workspaces/workspace') return snapshot(owner, role);
    if (path === '/api/data/workspaces/second') return snapshot(second, role);
    throw new Error('Unexpected request: ' + method + ' ' + path);
  });
});
afterEach(() => {cleanup(); vi.useRealTimers(); vi.restoreAllMocks();});
const sidebar = () => screen.getByRole('navigation', {name: 'Data navigation'});
const ready = () => screen.findByRole('heading', {name: /source$/});
const viewRequests = () => api.mock.calls.filter(([path, init]) => String(path).endsWith('/views') && (init?.method || 'GET') === 'GET');

it('lists Pivots and Segments for the selected source with usage counts and opens them as tabs', async () => {
  const user = userEvent.setup();
  render(<DataStudio ownerId="owner" visible/>);
  await ready();
  const pivots = await within(sidebar()).findByRole('navigation', {name: 'Pivots'});
  await user.click(within(pivots).getByRole('button', {name: 'Sales by region'}));
  expect(await screen.findByRole('tab', {name: 'Sales by region', selected: true})).toBeInTheDocument();
  expect(within(pivots).getByRole('button', {name: 'Sales by region'})).toHaveAttribute('aria-current', 'page');
  const segments = within(sidebar()).getByRole('navigation', {name: 'Segments'});
  expect(within(segments).getByRole('button', {name: 'Big orders Used in 2'})).toBeInTheDocument();
  await user.click(within(segments).getByRole('button', {name: /Big orders/}));
  expect(await screen.findByRole('tab', {name: 'Big orders', selected: true})).toBeInTheDocument();
  const stored = JSON.parse(localStorage.getItem('termx.data.tabs.owner.workspace')!);
  expect(stored.tabs.slice(1)).toEqual([{id: 'pivot:view:p1', kind: 'pivot', connectionId: 'source', viewId: 'p1'}, {id: 'segment:view:s1', kind: 'segment', viewId: 's1'}]);
  expect(JSON.stringify(stored)).not.toMatch(/Sales by region|Big orders/);
});
it('shows the empty sentences when there are no views', async () => {
  viewList = [];
  render(<DataStudio ownerId="owner" visible/>);
  await ready();
  expect(await within(sidebar()).findByText('No pivots yet. Start one from a table.')).toBeInTheDocument();
  expect(within(sidebar()).getByText('No segments yet. Add filters to a table, then choose Save as segment.')).toBeInTheDocument();
});
it('leaves the groups out, and asks for no views, when the server has none', async () => {
  capabilities = {};
  render(<DataStudio ownerId="owner" visible/>);
  await ready();
  expect(within(sidebar()).queryByRole('navigation', {name: 'Pivots'})).toBeNull();
  expect(within(sidebar()).queryByRole('navigation', {name: 'Segments'})).toBeNull();
  expect(viewRequests()).toHaveLength(0);
});
it('starts a pivot from the plus next to Pivots, choosing a table by keyboard', async () => {
  const user = userEvent.setup();
  render(<DataStudio ownerId="owner" visible/>);
  await ready();
  await user.click(within(sidebar()).getByRole('button', {name: 'Load tables'}));
  await within(sidebar()).findByRole('button', {name: 'orders'});
  await user.click(screen.getByRole('button', {name: 'New pivot'}));
  const popover = await screen.findByRole('dialog', {name: 'New pivot'});
  expect(within(popover).getByLabelText('Find a table')).toHaveFocus();
  await user.keyboard('{ArrowDown}');
  expect(within(popover).getAllByRole('button')[0]).toHaveFocus();
  await user.keyboard('{Enter}');
  expect(await screen.findByRole('tab', {name: 'New pivot on orders', selected: true})).toBeInTheDocument();
  expect(JSON.parse(localStorage.getItem('termx.data.tabs.owner.workspace')!).tabs[1]).toEqual({id: 'pivot:new:source:orders:1', kind: 'pivot', connectionId: 'source', table: 'orders'});
});
it('closes the New pivot popover with Escape and returns focus to the plus', async () => {
  const user = userEvent.setup();
  render(<DataStudio ownerId="owner" visible/>);
  await ready();
  await user.click(screen.getByRole('button', {name: 'New pivot'}));
  await screen.findByRole('dialog', {name: 'New pivot'});
  await user.keyboard('{Escape}');
  await waitFor(() => expect(screen.getByRole('button', {name: 'New pivot'})).toHaveFocus());
});
it('offers Pivot this table in the overview menu', async () => {
  const user = userEvent.setup();
  render(<DataStudio ownerId="owner" visible/>);
  await ready();
  await user.click(screen.getAllByRole('button', {name: 'Load tables'}).find(button => !sidebar().contains(button))!);
  await user.click(await screen.findByRole('button', {name: 'More actions for customers'}));
  await user.click(await screen.findByRole('menuitem', {name: 'Pivot this table'}));
  expect(await screen.findByRole('tab', {name: 'New pivot on customers', selected: true})).toBeInTheDocument();
});
it('gives viewers the groups without a way to start a pivot', async () => {
  role = 'viewer';
  render(<DataStudio ownerId="owner" visible/>);
  await ready();
  expect(await within(sidebar()).findByRole('button', {name: 'Sales by region'})).toBeInTheDocument();
  expect(screen.queryByRole('button', {name: 'New pivot'})).toBeNull();
});
it('counts pivots and segments in the search status', async () => {
  const user = userEvent.setup();
  render(<DataStudio ownerId="owner" visible/>);
  await ready();
  await within(sidebar()).findByRole('button', {name: 'Sales by region'});
  await user.type(within(sidebar()).getByLabelText('Search tables'), 'sales');
  expect(within(sidebar()).getByRole('status')).toHaveTextContent('0 tables match, 1 pivot, 0 segments, 0 saved queries');
});
it('closes the tab of a view someone else deleted and says so', async () => {
  localStorage.setItem('termx.data.tabs.owner.workspace', JSON.stringify({tabs: [{id: 'overview', kind: 'overview'}, {id: 'pivot:view:gone', kind: 'pivot', viewId: 'gone', connectionId: 'source'}], activeId: 'pivot:view:gone'}));
  render(<DataStudio ownerId="owner" visible/>);
  await ready();
  expect(await screen.findByText('A view you had open was deleted.')).toBeInTheDocument();
  expect(screen.queryByRole('tab', {name: 'Pivot'})).toBeNull();
  expect(screen.getByRole('tab', {name: 'Overview', selected: true})).toBeInTheDocument();
});
it('aborts the views request when the workspace changes', async () => {
  const user = userEvent.setup();
  render(<DataStudio ownerId="owner" visible/>);
  await ready();
  await within(sidebar()).findByRole('button', {name: 'Sales by region'});
  const first = signals[0];
  await user.click(screen.getByRole('combobox', {name: 'Data workspace'}));
  await user.click(await screen.findByRole('option', {name: 'Finance'}));
  await screen.findByRole('heading', {name: 'Finance source'});
  expect(first.aborted).toBe(true);
});

async function saveConnection(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole('button', {name: 'Edit connection'}));
  await user.click(await screen.findByRole('button', {name: 'Save connection'}));
}
it('shows the connection notice, lets it be dismissed, and never stores it', async () => {
  const user = userEvent.setup();
  render(<DataStudio ownerId="owner" visible/>);
  await ready();
  await saveConnection(user);
  expect(await screen.findByText(/Connection saved\. Test it when you are ready\./)).toBeInTheDocument();
  expect(screen.getByText(/Connection saved/).closest('[role=status]')).toBeTruthy();
  await user.click(screen.getByRole('button', {name: 'Dismiss notice'}));
  expect(screen.queryByText(/Connection saved/)).toBeNull();
  await saveConnection(user);
  await screen.findByText(/Connection saved/);
  const everything = JSON.stringify({...localStorage}) + location.href;
  expect(everything).not.toMatch(/Connection saved/);
});
it('clears the connection notice when the person opens another tab', async () => {
  const user = userEvent.setup();
  render(<DataStudio ownerId="owner" visible/>);
  await ready();
  await user.click(within(sidebar()).getByRole('button', {name: 'Load tables'}));
  await user.click(await within(sidebar()).findByRole('button', {name: 'orders'}));
  await screen.findByRole('tab', {name: 'orders', selected: true});
  await user.click(screen.getByRole('tab', {name: 'Overview'}));
  await saveConnection(user);
  await screen.findByText(/Connection saved/);
  await user.click(screen.getByRole('tab', {name: 'orders'}));
  expect(screen.queryByText(/Connection saved/)).toBeNull();
});
it('clears the connection notice when another source or workspace is chosen', async () => {
  const user = userEvent.setup();
  render(<DataStudio ownerId="owner" visible/>);
  await ready();
  await saveConnection(user);
  await screen.findByText(/Connection saved/);
  await user.click(screen.getByRole('combobox', {name: 'Data workspace'}));
  await user.click(await screen.findByRole('option', {name: 'Finance'}));
  await screen.findByRole('heading', {name: 'Finance source'});
  expect(screen.queryByText(/Connection saved/)).toBeNull();
});
it('clears the connection notice by itself after a few seconds', async () => {
  vi.useFakeTimers({shouldAdvanceTime: true});
  render(<DataStudio ownerId="owner" visible/>);
  await ready();
  fireEvent.click(screen.getByRole('button', {name: 'Edit connection'}));
  fireEvent.click(await screen.findByRole('button', {name: 'Save connection'}));
  await screen.findByText(/Connection saved/);
  act(() => {vi.advanceTimersByTime(NOTICE_MS - 100);});
  expect(screen.getByText(/Connection saved/)).toBeInTheDocument();
  act(() => {vi.advanceTimersByTime(200);});
  expect(screen.queryByText(/Connection saved/)).toBeNull();
});
it('does not bring the notice back after a reload', async () => {
  const user = userEvent.setup(), first = render(<DataStudio ownerId="owner" visible/>);
  await ready();
  await saveConnection(user);
  await screen.findByText(/Connection saved/);
  first.unmount();
  render(<DataStudio ownerId="owner" visible/>);
  await ready();
  expect(screen.queryByText(/Connection saved/)).toBeNull();
});
it('has no axe violations with the new sidebar groups', async () => {
  const {container} = render(<DataStudio ownerId="owner" visible/>);
  await ready();
  await within(sidebar()).findByRole('button', {name: 'Sales by region'});
  await expectNoAxeViolations(container);
});
