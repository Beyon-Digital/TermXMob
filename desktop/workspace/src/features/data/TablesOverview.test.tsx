import {afterEach, beforeEach, expect, it, vi} from 'vitest';
import {cleanup, fireEvent, render, screen, within} from '@testing-library/react';
import TablesOverview, {overviewModeKey} from './TablesOverview';
import {expectNoAxeViolations} from '../../test-axe';
import type {SourceController} from './use-source';
import type {DataConnection, DataEngine} from '../../lib/data-studio';

beforeEach(() => localStorage.clear());
afterEach(cleanup);
const engine: DataEngine = {id: 'sqlite', label: 'SQLite', status: 'qualified', ready: true, capabilities: {read: true, schema: true}};
const source: DataConnection = {id: 'source', revision: 1, name: 'Orders source', engine: 'sqlite', config: {}, created_by: 'owner'};
const idle: SourceController = {schema: null, schemaStatus: 'idle', schemaError: '', needsTest: false, testing: false, testMessage: '', testError: '', loadSchema: vi.fn(), testConnection: vi.fn()};
const loaded: SourceController = {...idle, schemaStatus: 'ready', schema: {tables: [
  {name: 'orders', columns: [{name: 'id'}, {name: 'total'}], row_estimate: null},
  {name: 'customers', columns: [{name: 'id'}], row_estimate: 1200},
  {name: 'audit_view', columns: [], readable: false, reason: 'Views are not readable.'}]}};
function props(over: Partial<React.ComponentProps<typeof TablesOverview>> = {}): React.ComponentProps<typeof TablesOverview> {
  return {ownerId: 'owner', role: 'owner', hasConnections: true, source, engine, controller: idle, canEditSource: true, onConnect: vi.fn(), onEditSource: vi.fn(), onArchiveSource: vi.fn(), onOpenTable: vi.fn(), ...over};
}

it('has one sentence and one action when no source is connected', () => {
  const p = props({source: undefined, hasConnections: false});
  render(<TablesOverview {...p}/>);
  expect(screen.getByRole('heading', {name: 'Connect your database'})).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', {name: 'Connect database'}));
  expect(p.onConnect).toHaveBeenCalled();
});

it('tells a viewer to ask an editor instead of offering to connect', () => {
  render(<TablesOverview {...props({source: undefined, hasConnections: false, role: 'viewer'})}/>);
  expect(screen.getByText('A workspace editor can connect a database.')).toBeInTheDocument();
  expect(screen.queryByRole('button', {name: 'Connect database'})).not.toBeInTheDocument();
});

it('asks for Load schema before listing tables', () => {
  const controller = {...idle, loadSchema: vi.fn()};
  render(<TablesOverview {...props({controller})}/>);
  expect(screen.getByText('Load the schema to see the tables in this source.')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', {name: 'Load schema'}));
  expect(controller.loadSchema).toHaveBeenCalled();
  expect(screen.queryByRole('group', {name: 'Table layout'})).not.toBeInTheDocument();
});

it('disables Load schema when the engine cannot introspect', () => {
  render(<TablesOverview {...props({engine: {...engine, capabilities: {read: true}}})}/>);
  expect(screen.getByRole('button', {name: 'Load schema'})).toBeDisabled();
});

it('shows skeletons while loading and marks the region busy', () => {
  render(<TablesOverview {...props({controller: {...idle, schemaStatus: 'loading'}})}/>);
  expect(screen.getByRole('region', {name: 'Tables overview'})).toHaveAttribute('aria-busy', 'true');
  expect(screen.getByRole('status', {name: 'Loading tables'}).children.length).toBeGreaterThan(0);
});

it('lists tables with column counts and estimates, right-aligning numbers', async () => {
  const {container} = render(<TablesOverview {...props({controller: loaded})}/>);
  const row = screen.getByRole('row', {name: /orders/});
  expect(within(row).getAllByRole('cell')[0]).toHaveClass('data-number');
  expect(within(row).getByText('Not estimated')).toBeInTheDocument();
  expect(within(screen.getByRole('row', {name: /customers/})).getByText('1,200')).toBeInTheDocument();
  await expectNoAxeViolations(container);
});

it('explains an unreadable table and disables Open', () => {
  render(<TablesOverview {...props({controller: loaded})}/>);
  expect(screen.getByText('Views are not readable.')).toBeInTheDocument();
  expect(screen.getByRole('button', {name: 'Open audit_view'})).toBeDisabled();
});

it('toggles to tiles, keeps the choice per owner and never touches other storage', () => {
  const p = props({controller: loaded}), view = render(<TablesOverview {...p}/>);
  expect(screen.getByRole('button', {name: 'List view'})).toHaveAttribute('aria-pressed', 'true');
  fireEvent.click(screen.getByRole('button', {name: 'Grid view'}));
  expect(screen.getByRole('button', {name: 'Grid view'})).toHaveAttribute('aria-pressed', 'true');
  expect(screen.getAllByRole('listitem')).toHaveLength(3);
  expect(screen.getByText('2 columns')).toBeInTheDocument();
  expect(screen.getByText('Row count not estimated')).toBeInTheDocument();
  expect(screen.getByText('About 1,200 rows')).toBeInTheDocument();
  expect(localStorage.getItem(overviewModeKey('owner'))).toBe('grid');
  view.unmount();
  render(<TablesOverview {...p}/>);
  expect(screen.getByRole('button', {name: 'Grid view'})).toHaveAttribute('aria-pressed', 'true');
  render(<TablesOverview {...props({ownerId: 'someone-else', controller: loaded})}/>);
  expect(within(screen.getAllByRole('region', {name: 'Tables overview'})[1]).getByRole('button', {name: 'List view'})).toHaveAttribute('aria-pressed', 'true');
});

it('opens a table from either layout', () => {
  const p = props({controller: loaded});
  render(<TablesOverview {...p}/>);
  fireEvent.click(screen.getByRole('button', {name: 'Open customers'}));
  fireEvent.click(screen.getByRole('button', {name: 'Grid view'}));
  fireEvent.click(screen.getByRole('button', {name: 'Open orders'}));
  expect(p.onOpenTable).toHaveBeenNthCalledWith(1, expect.objectContaining({name: 'customers'}));
  expect(p.onOpenTable).toHaveBeenNthCalledWith(2, expect.objectContaining({name: 'orders'}));
});

it('shows a schema error with Try again, or Test connection for a 409', () => {
  const load = vi.fn(), test = vi.fn();
  const {rerender} = render(<TablesOverview {...props({controller: {...idle, schemaStatus: 'error', schemaError: 'Host unavailable', loadSchema: load}})}/>);
  expect(screen.getByRole('alert')).toHaveTextContent('Host unavailable');
  fireEvent.click(screen.getByRole('button', {name: 'Try again'}));
  expect(load).toHaveBeenCalled();
  rerender(<TablesOverview {...props({controller: {...idle, schemaStatus: 'error', schemaError: 'Test this source first to verify its current read-only role and session.', needsTest: true, testConnection: test}})}/>);
  fireEvent.click(screen.getAllByRole('button', {name: 'Test connection'}).at(-1)!);
  expect(test).toHaveBeenCalled();
});

it('has a sentence and a retry when the source has no visible tables', () => {
  render(<TablesOverview {...props({controller: {...idle, schemaStatus: 'ready', schema: {tables: []}}})}/>);
  expect(screen.getByText('This source has no visible tables.')).toBeInTheDocument();
  expect(screen.getByRole('button', {name: 'Load schema'})).toBeInTheDocument();
});

it('shows source actions to editors only, honouring ownership', () => {
  const view = render(<TablesOverview {...props({canEditSource: false})}/>);
  expect(screen.getByRole('button', {name: 'Edit connection'})).toBeDisabled();
  expect(screen.getByRole('button', {name: 'Archive connection'})).toBeDisabled();
  view.rerender(<TablesOverview {...props({role: 'viewer', canEditSource: false})}/>);
  expect(screen.queryByRole('button', {name: 'Test connection'})).not.toBeInTheDocument();
  expect(screen.getByText('Your viewer role can open published reports in Reports.')).toBeInTheDocument();
});

it('reports a connection test result in place', () => {
  render(<TablesOverview {...props({controller: {...idle, testMessage: 'Connection succeeded.'}})}/>);
  expect(screen.getByRole('status')).toHaveTextContent('Connection succeeded.');
});
