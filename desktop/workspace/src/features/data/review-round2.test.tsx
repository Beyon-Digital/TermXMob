import {readFileSync} from 'node:fs';
import {afterEach, expect, it, vi} from 'vitest';
import {cleanup, render, screen, waitFor} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {useState} from 'react';
import DataTabs from './DataTabs';
import SaveViewDialog from './SaveViewDialog';
import PivotTab from './PivotTab';
import {headerLabel} from './PivotTable';
import {initialTabs, openTab, selectTab, tableTabId, type DataTab} from '../../lib/data-tabs';
import {controller, installViewApi, newBehaviour} from '../../test-utils/views-api';
import type {OutputColumn} from '../../lib/query-spec';

vi.mock('../../lib/api', () => ({request: vi.fn(), ApiError: class ApiError extends Error {constructor(public status: number, message: string) {super(message);}}}));
afterEach(cleanup);
const css = (name: string) => readFileSync(new URL(name, import.meta.url), 'utf8');

it('moves focus to the new panel when a control in the old one opens a tab', () => {
  const orders: DataTab = {id: tableTabId('src', 'orders'), kind: 'table', connectionId: 'src', table: 'orders'};
  const title = (tab: DataTab) => tab.kind === 'overview' ? 'Overview' : 'orders';
  const panel = (tab: DataTab) => <><h1>{title(tab)}</h1><button type="button">{'Action in ' + title(tab)}</button></>;
  const base = openTab(initialTabs(), orders);
  const props = {title, onSelect: vi.fn(), onClose: vi.fn(), panel};
  const {rerender} = render(<DataTabs state={selectTab(base, 'overview')} {...props}/>);
  screen.getByRole('button', {name: 'Action in Overview'}).focus();
  rerender(<DataTabs state={selectTab(base, orders.id)} {...props}/>);
  const shown = document.querySelector('.data-tab-panel:not([hidden])')!;
  expect(shown.contains(document.activeElement) && document.activeElement !== document.body).toBe(true);
  expect(document.activeElement).toHaveTextContent('orders');
});

it('leaves focus alone when the person picks a tab', async () => {
  const orders: DataTab = {id: tableTabId('src', 'orders'), kind: 'table', connectionId: 'src', table: 'orders'};
  const user = userEvent.setup(), state = openTab(initialTabs(), orders);
  render(<DataTabs state={selectTab(state, 'overview')} title={tab => tab.kind === 'overview' ? 'Overview' : 'orders'} onSelect={vi.fn()} onClose={vi.fn()} panel={() => <p>Panel</p>}/>);
  await user.click(screen.getByRole('tab', {name: 'orders'}));
  expect(screen.getByRole('tab', {name: 'orders'})).toHaveFocus();
});

it('puts focus on the panel heading when the dialog opener is gone after saving', async () => {
  const user = userEvent.setup();
  function Host() {
    const [open, setOpen] = useState(false), [saved, setSaved] = useState(false);
    return <div className="data-tab-panel"><h1>Sales by region</h1>
      {saved ? <button key="refresh" type="button">Refresh</button> : <button key="save" type="button" onClick={() => setOpen(true)}>Save view</button>}
      <SaveViewDialog open={open} title="Save pivot" submitLabel="Save" onSubmit={async () => {setSaved(true);}} onClose={() => setOpen(false)}/></div>;
  }
  render(<Host/>);
  await user.click(screen.getByRole('button', {name: 'Save view'}));
  await user.type(await screen.findByLabelText('Name'), 'Sales');
  await user.keyboard('{Enter}');
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  await waitFor(() => expect(screen.getByRole('heading', {name: 'Sales by region'})).toHaveFocus());
});

it('opens a saved pivot without asking for a second click to load the tables', async () => {
  installViewApi(newBehaviour());
  const loadSchema = vi.fn();
  render(<div className="data-tab-panel"><PivotTab workspaceId="w1" connectionId="c1" tab={{id: 'pivot:new:c1:sales:1', kind: 'pivot', connectionId: 'c1', table: 'sales'}} label="Pivot"
    controller={controller({schemaStatus: 'idle', schema: null, loadSchema})} engineCanLoad views={{} as never} canEdit onClose={vi.fn()} onSaved={vi.fn()} onArchived={vi.fn()} onDrill={vi.fn()}/></div>);
  await waitFor(() => expect(loadSchema).toHaveBeenCalledTimes(1));
  expect(screen.queryByText(/schema/i)).toBeNull();
  expect(screen.getByRole('status', {name: 'Loading Pivot'})).toBeInTheDocument();
});

it('labels pivot headers like the builder and the last number as a total', () => {
  const row = {label: 'region', value_kind: 'text', role: 'pivot_row', column: 'region'} as OutputColumn;
  expect(headerLabel(row, true)).toBe('Region');
  expect(headerLabel({...row, label: 'Where'}, true)).toBe('Where');
  const total = {label: 'Number of rows', value_kind: 'integer', role: 'pivot_total', fn: 'count'} as OutputColumn;
  expect(headerLabel(total, true)).toBe('Total');
  expect(headerLabel(total, false)).toBe('Number of rows');
  expect(headerLabel({...total, label: 'Total amount', fn: 'sum'}, true)).toBe('Total amount');
  expect(headerLabel({...total, label: 'Average amount', fn: 'avg'}, true)).toBe('Average amount overall');
});

it('keeps a long tab name inside its tab and its close button inside the tab', () => {
  const studio = css('data-studio.css');
  expect(studio).toMatch(/\.data-tab\{[^}]*min-width:0[^}]*max-width:240px/);
  expect(studio).toMatch(/\.data-tab \.ui-tabs-trigger\{[^}]*flex:1 1 auto;min-width:0/);
  expect(studio).toMatch(/\.data-tab-close\{[^}]*flex:none/);
});

it('draws no vertical rule in pivots and animates no colours in tabs', () => {
  expect(css('data-query.css')).not.toMatch(/\.data-pivot[^{]*th:first-child[^{]*\{[^}]*border-right/);
  for (const name of ['data-studio.css', '../../components/ui/overlays.css']) expect(css(name)).not.toMatch(/\.(?:ui-tabs-trigger|data-tab-close)\{[^}]*transition/);
});

it('keeps the tab focus ring inside the scrolling list', () => {
  expect(css('../../components/ui/overlays.css')).toMatch(/\.ui-tabs-trigger:focus-visible\{outline-offset:-2px\}/);
});
