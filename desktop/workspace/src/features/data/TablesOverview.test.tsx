import {afterEach, beforeEach, expect, it, vi} from 'vitest';
import {cleanup, fireEvent, render, screen, waitFor, within} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import TablesOverview, {overviewModeKey} from './TablesOverview';
import {expectNoAxeViolations} from '../../test-axe';
import type {SourceController} from './use-source';
import type {DataConnection, DataEngine} from '../../lib/data-studio';
import {runSpecToCompletion} from '../../lib/data-query';

vi.mock('../../lib/data-query', () => ({runSpecToCompletion: vi.fn()}));
const count = vi.mocked(runSpecToCompletion);

beforeEach(() => {localStorage.clear(); count.mockReset();});
afterEach(cleanup);
const engine: DataEngine = {id: 'sqlite', label: 'SQLite', status: 'qualified', ready: true, capabilities: {read: true, schema: true}};
const source: DataConnection = {id: 'source', revision: 1, name: 'Orders source', engine: 'sqlite', config: {}, created_by: 'owner'};
const idle: SourceController = {schema: null, schemaStatus: 'idle', schemaError: '', needsTest: false, testing: false, testMessage: '', testError: '', loadSchema: vi.fn(), testConnection: vi.fn()};
const loaded: SourceController = {...idle, schemaStatus: 'ready', schema: {tables: [
  {name: 'orders', columns: [{name: 'id'}, {name: 'total'}], row_estimate: null},
  {name: 'customers', columns: [{name: 'id'}], row_estimate: 1200},
  {name: 'audit_view', columns: [], readable: false, reason: 'Views are not readable.'}]}};
function props(over: Partial<React.ComponentProps<typeof TablesOverview>> = {}): React.ComponentProps<typeof TablesOverview> {
  return {workspaceId: 'w', ownerId: 'owner', role: 'owner', hasConnections: true, source, engine, controller: idle, canEditSource: true, onConnect: vi.fn(), onEditSource: vi.fn(), onArchiveSource: vi.fn(), onOpenTable: vi.fn(), ...over};
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
  expect(within(row).getByRole('button', {name: 'Count rows in orders'})).toBeInTheDocument();
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
  expect(screen.getByRole('button', {name: 'Count rows in orders'})).toBeInTheDocument();
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

const job = (over: object) => ({id: 'j', status: 'completed', connection_id: 'source', result: {columns: [], rows: [[1305]], row_count: 1, truncated: false}, ...over}) as never;

it('counts the rows of a table without an estimate and announces the number politely', async () => {
  count.mockResolvedValue(job({}));
  render(<TablesOverview {...props({controller: loaded})}/>);
  const row = screen.getByRole('row', {name: /orders/});
  await userEvent.click(within(row).getByRole('button', {name: 'Count rows in orders'}));
  expect(await within(row).findByText('1,305')).toBeInTheDocument();
  expect(within(row).getByText('1,305').closest('[aria-live]')).toHaveAttribute('aria-live', 'polite');
  const [, spec] = count.mock.calls[0];
  expect(count.mock.calls[0][0]).toBe('w');
  expect(spec).toMatchObject({source: 'source', from: {table: 'orders'}, aggregates: [{fn: 'count'}], select: [], limit: 1});
  expect(within(row).queryByRole('button', {name: /Count rows/})).not.toBeInTheDocument();
});

it('shows Counting while the count runs and disables the button', async () => {
  count.mockReturnValue(new Promise(() => undefined));
  render(<TablesOverview {...props({controller: loaded})}/>);
  await userEvent.click(screen.getByRole('button', {name: 'Count rows in orders'}));
  expect(screen.getByRole('button', {name: 'Count rows in orders'})).toBeDisabled();
  expect(screen.getByRole('button', {name: 'Count rows in orders'})).toHaveTextContent('Counting…');
});

it('says why a count failed and offers to try again, including the source-test 409', async () => {
  count.mockRejectedValueOnce(new Error('Test this source first to verify its current read-only role and session.'));
  count.mockResolvedValueOnce(job({}));
  render(<TablesOverview {...props({controller: loaded})}/>);
  await userEvent.click(screen.getByRole('button', {name: 'Count rows in orders'}));
  expect(await screen.findByRole('alert')).toHaveTextContent('Test this source first');
  await userEvent.click(screen.getByRole('button', {name: 'Try again'}));
  expect(await screen.findByText('1,305')).toBeInTheDocument();
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
});

it('reports a count job that failed on the server', async () => {
  count.mockResolvedValue(job({status: 'failed', error: 'The query ran longer than 30 seconds and was stopped.', result: null}));
  render(<TablesOverview {...props({controller: loaded})}/>);
  await userEvent.click(screen.getByRole('button', {name: 'Count rows in orders'}));
  expect(await screen.findByRole('alert')).toHaveTextContent('stopped');
});

it('does not offer Count rows where the source gave an estimate or the table cannot be read', () => {
  render(<TablesOverview {...props({controller: loaded})}/>);
  expect(screen.getAllByRole('button', {name: /Count rows/})).toHaveLength(1);
  expect(within(screen.getByRole('row', {name: /customers/})).getByText('1,200')).toBeInTheDocument();
});

it('offers Combine from the More menu of a row and a tile', async () => {
  const onCombineTable = vi.fn(), user = userEvent.setup();
  render(<TablesOverview {...props({controller: loaded, onCombineTable})}/>);
  await user.click(screen.getByRole('button', {name: 'More actions for orders'}));
  await user.click(await screen.findByRole('menuitem', {name: 'Combine with another table'}));
  expect(onCombineTable).toHaveBeenCalledWith(expect.objectContaining({name: 'orders'}));
  expect(screen.getByRole('button', {name: 'More actions for audit_view'})).toBeDisabled();
  fireEvent.click(screen.getByRole('button', {name: 'Grid view'}));
  await user.click(screen.getByRole('button', {name: 'More actions for customers'}));
  await user.click(await screen.findByRole('menuitem', {name: 'Combine with another table'}));
  expect(onCombineTable).toHaveBeenLastCalledWith(expect.objectContaining({name: 'customers'}));
});

it('has no More menu when combining is not available, for example for a viewer', () => {
  render(<TablesOverview {...props({controller: loaded})}/>);
  expect(screen.queryByRole('button', {name: /More actions/})).not.toBeInTheDocument();
});

it('has no accessibility violations with the Count rows and More actions controls', async () => {
  const {container} = render(<TablesOverview {...props({controller: loaded, onCombineTable: vi.fn()})}/>);
  await expectNoAxeViolations(container);
  await waitFor(() => expect(screen.getByRole('table')).toBeInTheDocument());
});
