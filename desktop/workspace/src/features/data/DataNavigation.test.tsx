import {afterEach, expect, it, vi} from 'vitest';
import {cleanup, fireEvent, render, screen, within} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import DataNavigation, {TABLE_PAGE} from './DataNavigation';
import {expectNoAxeViolations} from '../../test-axe';
import type {SourceController} from './use-source';
import type {DataQuery} from '../../lib/data-studio';

afterEach(cleanup);
const idle: SourceController = {schema: null, schemaStatus: 'idle', schemaError: '', needsTest: false, testing: false, testMessage: '', testError: '', loadSchema: vi.fn(), testConnection: vi.fn()};
const ready = (names: string[]): SourceController => ({...idle, schemaStatus: 'ready', schema: {tables: names.map(name => ({name, columns: []}))}});
const query: DataQuery = {id: 'q1', revision: 1, name: 'Customer count', connection_id: 'source', sql: 'SELECT 1'};
function props(over: Partial<React.ComponentProps<typeof DataNavigation>> = {}): React.ComponentProps<typeof DataNavigation> {
  return {loading: false, workspaces: [{id: 'w', revision: 1, name: 'Analytics', role: 'owner'}], workspaceId: 'w', workspaceBusy: false, onWorkspace: vi.fn(), onCreateWorkspace: vi.fn(),
    role: 'owner', connections: [{id: 'source', revision: 1, name: 'Orders source', engine: 'sqlite', config: {}}], connectionId: 'source', onConnection: vi.fn(), onConnect: vi.fn(),
    source: idle, queries: [query], reports: [{id: 'r1', name: 'Monthly revenue'}], dashboards: [{id: 'd1', name: 'Board'}], view: 'database', activeTabId: 'overview',
    onOpenOverview: vi.fn(), onOpenTable: vi.fn(), onOpenQuery: vi.fn(), onNewQuery: vi.fn(), onOpenView: vi.fn(), ...over};
}

it('lists the Studio groups as labelled navs', async () => {
  const {container} = render(<DataNavigation {...props({source: ready(['orders'])})}/>);
  for (const name of ['Tables', 'Saved queries', 'Reports', 'Dashboards', 'Team']) expect(screen.getByRole('navigation', {name})).toBeInTheDocument();
  expect(screen.getByRole('button', {name: 'Monthly revenue'})).toBeInTheDocument();
  expect(screen.getByRole('button', {name: 'Members'})).toBeInTheDocument();
  await expectNoAxeViolations(container);
});

it('offers Load schema before the source has been contacted', () => {
  const load = vi.fn();
  render(<DataNavigation {...props({source: {...idle, loadSchema: load}})}/>);
  fireEvent.click(screen.getByRole('button', {name: 'Load schema'}));
  expect(load).toHaveBeenCalledTimes(1);
});

it('shows skeleton rows while the schema loads', () => {
  render(<DataNavigation {...props({source: {...idle, schemaStatus: 'loading'}})}/>);
  expect(screen.getByRole('navigation', {name: 'Tables'})).toHaveAttribute('aria-busy', 'true');
  expect(screen.getByRole('status', {name: 'Loading tables'}).children).toHaveLength(4);
});

it('shows an inline error with a retry, or a Test connection action when the server asks for one', () => {
  const load = vi.fn(), test = vi.fn();
  const {rerender} = render(<DataNavigation {...props({source: {...idle, schemaStatus: 'error', schemaError: 'Host unavailable', loadSchema: load}})}/>);
  expect(screen.getByRole('alert')).toHaveTextContent('Host unavailable');
  fireEvent.click(screen.getByRole('button', {name: 'Try again'}));
  expect(load).toHaveBeenCalled();
  rerender(<DataNavigation {...props({source: {...idle, schemaStatus: 'error', schemaError: 'Test this source first', needsTest: true, testConnection: test}})}/>);
  fireEvent.click(screen.getByRole('button', {name: 'Test connection'}));
  expect(test).toHaveBeenCalled();
  expect(screen.queryByRole('button', {name: 'Try again'})).not.toBeInTheDocument();
});

it('filters tables and saved queries by the search text and announces the result', async () => {
  const user = userEvent.setup();
  render(<DataNavigation {...props({source: ready(['orders', 'order_items', 'customers'])})}/>);
  await user.type(screen.getByLabelText('Search tables'), 'order');
  expect(screen.queryByRole('button', {name: 'customers'})).not.toBeInTheDocument();
  expect(screen.getByRole('button', {name: 'order_items'})).toBeInTheDocument();
  expect(screen.getByRole('status')).toHaveTextContent('2 tables match, 0 saved queries');
  await user.clear(screen.getByLabelText('Search tables'));
  await user.type(screen.getByLabelText('Search tables'), 'zzz');
  expect(screen.getByText('No tables match.')).toBeInTheDocument();
  expect(screen.getByText('No saved queries match.')).toBeInTheDocument();
});

it('renders a long table list in pages instead of all at once', () => {
  render(<DataNavigation {...props({source: ready(Array.from({length: TABLE_PAGE + 50}, (_, index) => 't' + String(index).padStart(3, '0')))})}/>);
  const nav = screen.getByRole('navigation', {name: 'Tables'});
  expect(within(nav).getAllByRole('button').filter(button => /^t\d+$/.test(button.textContent || ''))).toHaveLength(TABLE_PAGE);
  fireEvent.click(within(nav).getByRole('button', {name: 'Show more tables'}));
  expect(within(nav).getAllByRole('button').filter(button => /^t\d+$/.test(button.textContent || ''))).toHaveLength(TABLE_PAGE + 50);
});

it('marks the active tab row as the current page and opens tables, queries and views', () => {
  const p = props({source: ready(['orders']), activeTabId: 'table:source:orders'});
  render(<DataNavigation {...p}/>);
  expect(screen.getByRole('button', {name: 'orders'})).toHaveAttribute('aria-current', 'page');
  fireEvent.click(screen.getByRole('button', {name: 'orders'}));
  expect(p.onOpenTable).toHaveBeenCalledWith(expect.objectContaining({name: 'orders'}));
  fireEvent.click(screen.getByRole('button', {name: 'Customer count'}));
  expect(p.onOpenQuery).toHaveBeenCalledWith(query);
  fireEvent.click(screen.getByRole('button', {name: 'New query'}));
  expect(p.onNewQuery).toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', {name: 'Members'}));
  expect(p.onOpenView).toHaveBeenCalledWith('team');
  fireEvent.click(screen.getByRole('button', {name: 'Monthly revenue'}));
  expect(p.onOpenView).toHaveBeenCalledWith('bi');
});

it('disables a table the connector excluded and explains why', () => {
  const source: SourceController = {...idle, schemaStatus: 'ready', schema: {tables: [{name: 'secret_view', columns: [], readable: false, reason: 'Views are not readable.'}]}};
  render(<DataNavigation {...props({source})}/>);
  expect(screen.getByRole('button', {name: 'secret_view'})).toBeDisabled();
  expect(screen.getByRole('button', {name: 'secret_view'})).toHaveAttribute('title', 'Views are not readable.');
});

it('has a one-sentence empty state with no source and no table group', () => {
  render(<DataNavigation {...props({connections: [], connectionId: '', queries: []})}/>);
  expect(screen.getByRole('combobox', {name: 'Data source'})).toHaveDisplayValue('No sources yet');
  expect(screen.queryByRole('navigation', {name: 'Tables'})).not.toBeInTheDocument();
  expect(screen.queryByRole('button', {name: 'Connect database'})).not.toBeInTheDocument();
});

it('lets viewers see reports and team only', () => {
  render(<DataNavigation {...props({role: 'viewer'})}/>);
  expect(screen.getByText('Ask an editor to share a table with you.')).toBeInTheDocument();
  expect(screen.queryByRole('navigation', {name: 'Saved queries'})).not.toBeInTheDocument();
  expect(screen.queryByRole('button', {name: 'Connect database'})).not.toBeInTheDocument();
  expect(screen.queryByLabelText('Search tables')).not.toBeInTheDocument();
});

it('has a keyboard path through the scope controls and rows in document order', async () => {
  const user = userEvent.setup();
  render(<DataNavigation {...props({source: ready(['orders'])})}/>);
  await user.tab();
  expect(screen.getByRole('combobox', {name: 'Data workspace'})).toHaveFocus();
  await user.tab(); await user.tab();
  expect(screen.getByRole('combobox', {name: 'Data source'})).toHaveFocus();
});
