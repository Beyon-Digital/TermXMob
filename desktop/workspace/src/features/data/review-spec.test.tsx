// Reviewer (spec lens) tests. Each one fails on the composed branch and pins a requirement of the approved spec/plan.
import {afterEach, beforeEach, expect, it, vi} from 'vitest';
import {useState} from 'react';
import {cleanup, fireEvent, render, screen} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import DataTabs from './DataTabs';
import TablesOverview from './TablesOverview';
import {initialTabs, openTab, tableTabId, type DataTab} from '../../lib/data-tabs';
import type {SourceController} from './use-source';
import type {DataConnection, DataEngine} from '../../lib/data-studio';

beforeEach(() => localStorage.clear());
afterEach(cleanup);

// Spec "Experience": "Filters, sorts ... are kept in memory per tab" (plan 5.3). TableTab keeps its filters in component state,
// and Radix TabsContent unmounts inactive panels unless forceMount is set (DataTabs only sets it for query tabs).
it('keeps a table tab working state when the person switches to another tab and back', async () => {
  const orders: DataTab = {id: tableTabId('src', 'orders'), kind: 'table', connectionId: 'src', table: 'orders'};
  function Harness() {
    const [state, setState] = useState(openTab(initialTabs(), orders));
    const Panel = ({tab}: {tab: DataTab}) => { const [text, setText] = useState(''); return <input aria-label={'Filter in ' + tab.id} value={text} onChange={event => setText(event.target.value)}/>; };
    return <DataTabs state={state} title={tab => tab.kind === 'overview' ? 'Overview' : tab.table!} onSelect={id => setState(previous => ({...previous, activeId: id}))} onClose={vi.fn()} panel={tab => <Panel tab={tab}/>}/>;
  }
  const user = userEvent.setup();
  render(<Harness/>);
  await user.type(screen.getByLabelText('Filter in ' + orders.id), 'status is open');
  await user.click(screen.getByRole('tab', {name: 'Overview'}));
  await user.click(screen.getByRole('tab', {name: 'orders'}));
  expect(screen.getByLabelText('Filter in ' + orders.id)).toHaveValue('status is open');
});

const engine: DataEngine = {id: 'sqlite', label: 'SQLite', status: 'qualified', ready: true, capabilities: {read: true, schema: true}};
const source: DataConnection = {id: 'source', revision: 1, name: 'Orders source', engine: 'sqlite', config: {}, created_by: 'owner'};
const controller: SourceController = {schema: {tables: [{name: 'orders', columns: [{name: 'id'}], row_estimate: null}]}, schemaStatus: 'ready', schemaError: '', needsTest: false, testing: false, testMessage: '', testError: '', loadSchema: vi.fn(), testConnection: vi.fn()};
const overview = () => <TablesOverview ownerId="owner" role="owner" hasConnections source={source} engine={engine} controller={controller} canEditSource onConnect={vi.fn()} onEditSource={vi.fn()} onArchiveSource={vi.fn()} onOpenTable={vi.fn()}/>;

// Plan 5.4: SQLite reports no row estimate, so each table offers "Count rows", which issues a count spec.
it('offers Count rows for a table without a row estimate', () => {
  render(overview());
  expect(screen.getByRole('button', {name: /Count rows/})).toBeInTheDocument();
});

// Spec "All tables overview": tile actions are Open and Combine (Pivot arrives in PR 2); plan 5.4: a More menu with "Combine with another table".
it('offers Combine from a table tile', () => {
  render(overview());
  fireEvent.click(screen.getByRole('button', {name: 'Grid view'}));
  expect(screen.getByRole('button', {name: /More|Combine/})).toBeInTheDocument();
});
