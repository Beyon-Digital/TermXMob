import {afterEach, beforeEach, expect, it, vi} from 'vitest';
import {cleanup, render, screen, waitFor, within} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import TableTab from './TableTab';
import FilterChips from './FilterChips';
import {ApiError} from '../../lib/api';
import {humanize} from '../../lib/query-builder';
import segmentMissing from '../../lib/view-contract-fixtures/compile-segment-missing.json';
import {controller, detail, installViewApi, newBehaviour, type Behaviour} from '../../test-utils/views-api';
import {sizeGridScrollers} from '../../test-utils/virtual';
import {expectNoAxeViolations} from '../../test-axe';
import type {FilterGroup, QuerySpec} from '../../lib/query-spec';
import type {ViewsController} from './use-views';
import type {ViewSummary} from '../../lib/data-views';

vi.mock('../../lib/api', () => ({request: vi.fn(), ApiError: class ApiError extends Error {constructor(public status: number, message: string) {super(message);}}}));
vi.mock('../../lib/transfers', () => ({transfer: vi.fn()}));
let behaviour: Behaviour;
const segment = (id: string, name: string, base = 'sales'): ViewSummary => ({...detail('segment', {id, name}), base_table: base} as ViewSummary);
const views = (items: ViewSummary[] = [segment('s1', 'Big sales'), segment('s2', 'Customers in the east', 'customers')]): ViewsController => ({views: items, status: 'ready', error: '', refresh: vi.fn(),
  upsert: vi.fn(), remove: vi.fn(), byId: id => items.find(row => row.id === id), nameOf: id => items.find(row => row.id === id)?.name});
beforeEach(() => {
  vi.clearAllMocks(); sizeGridScrollers();
  behaviour = newBehaviour();
  behaviour.views.s1 = detail('segment', {id: 's1', name: 'Big sales'});
  installViewApi(behaviour);
});
afterEach(cleanup);
const calls = (suffix: string, method?: string) => behaviour.calls.filter(call => call.path.split('?')[0].endsWith(suffix) && (!method || call.method === method));
function view(props: Partial<React.ComponentProps<typeof TableTab>> = {}) {
  const onViewSaved = vi.fn(), store = views();
  const result = render(<TableTab workspaceId="w1" connectionId="c1" name="sales" label="sales" controller={controller()} engineCanLoad onClose={vi.fn()} views={store} canSave onViewSaved={onViewSaved} {...props}/>);
  return {...result, onViewSaved, store};
}
async function addFilter(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByRole('button', {name: 'Add filter'}));
  const dialog = await screen.findByRole('dialog', {name: 'Add filter'});
  await user.selectOptions(within(dialog).getByLabelText('Column'), 'sales\u0000status');
  await user.type(within(dialog).getByLabelText('Value'), 'open');
  await user.click(within(dialog).getByRole('button', {name: 'Apply'}));
}

it('disables Save as segment with a visible reason until a filter exists', async () => {
  const user = userEvent.setup();
  view();
  const button = await screen.findByRole('button', {name: 'Save as segment'});
  expect(button).toBeDisabled();
  expect(screen.getByText('Add a filter first')).toBeVisible();
  expect(button).toHaveAttribute('aria-describedby', 'save-segment-reason');
  await addFilter(user);
  await waitFor(() => expect(screen.getByRole('button', {name: 'Save as segment'})).toBeEnabled());
  expect(screen.queryByText('Add a filter first')).toBeNull();
});
it('saves the filters of the table as a segment and announces it', async () => {
  const user = userEvent.setup(), {onViewSaved, store} = view();
  await addFilter(user);
  await user.click(await screen.findByRole('button', {name: 'Save as segment'}));
  const dialog = await screen.findByRole('dialog', {name: 'Save as segment'});
  expect(within(dialog).getByText('A segment is a saved set of filters you can reuse.')).toBeInTheDocument();
  await user.type(within(dialog).getByLabelText('Name'), 'Open sales');
  await user.click(within(dialog).getByRole('button', {name: 'Save segment'}));
  expect(await screen.findByText('Saved segment Open sales')).toBeInTheDocument();
  const body = calls('/views', 'POST')[0].body;
  expect(body).toMatchObject({kind: 'segment', name: 'Open sales'});
  expect(body.spec).toMatchObject({from: {table: 'sales', alias: 'sales'}, joins: [], select: [], group_by: [], aggregates: [], sort: [], limit: 200, offset: 0});
  expect(body.spec.filters.items).toEqual([{field: {table_alias: 'sales', column: 'status'}, op: 'eq', value: 'open'}]);
  expect(onViewSaved).toHaveBeenCalled();
  expect(store.upsert).toHaveBeenCalled();
  await waitFor(() => expect(screen.getByRole('button', {name: 'Save as segment'})).toHaveFocus());
});
it('shows a taken segment name under the field', async () => {
  const user = userEvent.setup();
  behaviour.fail['POST /views'] = new ApiError(409, JSON.stringify({code: 'name_taken', message: 'A segment with this name already exists.'}));
  view();
  await addFilter(user);
  await user.click(await screen.findByRole('button', {name: 'Save as segment'}));
  const dialog = await screen.findByRole('dialog', {name: 'Save as segment'});
  await user.type(within(dialog).getByLabelText('Name'), 'Big sales');
  await user.click(within(dialog).getByRole('button', {name: 'Save segment'}));
  expect(await within(dialog).findByRole('alert')).toHaveTextContent('A segment with this name already exists.');
});
it('has no segment controls when the person cannot save views or the server has none', async () => {
  view({canSave: false});
  await screen.findByRole('button', {name: 'Add filter'});
  expect(screen.queryByRole('button', {name: 'Save as segment'})).toBeNull();
  cleanup();
  view({views: undefined, canSave: false});
  await screen.findByRole('button', {name: 'Add filter'});
  expect(screen.queryByRole('button', {name: 'Add segment'})).toBeNull();
});
it('filters by a segment, lists segments from other tables as disabled with the reason, and runs with the node', async () => {
  const user = userEvent.setup();
  view();
  await user.click(await screen.findByRole('button', {name: 'Add segment'}));
  const popover = await screen.findByRole('dialog', {name: 'Add segment filter'});
  expect(within(popover).getByRole('button', {name: /Customers in the east/})).toBeDisabled();
  expect(within(popover).getByText('Built on Customers')).toBeInTheDocument();
  await user.click(within(popover).getByRole('radio', {name: 'Not in segment'}));
  await user.click(within(popover).getByRole('button', {name: 'Big sales'}));
  expect(await screen.findByRole('button', {name: 'Not in segment Big sales'})).toBeInTheDocument();
  await waitFor(() => expect(calls('/query-specs/run').at(-1)?.body.spec.filters.items).toEqual([{segment: 's1', negate: true}]));
  expect(calls('/views/s1', 'GET')).toHaveLength(1);
});
it('searches the segment list and says when nothing matches', async () => {
  const user = userEvent.setup();
  view();
  await user.click(await screen.findByRole('button', {name: 'Add segment'}));
  const popover = await screen.findByRole('dialog', {name: 'Add segment filter'});
  await user.type(within(popover).getByLabelText('Find a segment'), 'zzz');
  expect(within(popover).getByText('No segments match.')).toBeInTheDocument();
});
it('explains how to make a first segment when there are none', async () => {
  const user = userEvent.setup();
  view({views: views([])});
  await user.click(await screen.findByRole('button', {name: 'Add segment'}));
  expect(await screen.findByText('No segments yet. Add filters, then choose Save as segment.')).toBeInTheDocument();
});
it('combines the tables a segment needs and says so', async () => {
  const user = userEvent.setup(), spec = detail('segment').spec;
  behaviour.views.s1 = detail('segment', {id: 's1', name: 'Big sales', spec: {...spec, joins: [{kind: 'left', table: 'regions', alias: 'r', on: [{left: {table_alias: 'sales', column: 'id'}, right: {table_alias: 'r', column: 'id'}}]}]}});
  view();
  await user.click(await screen.findByRole('button', {name: 'Add segment'}));
  await user.click(within(await screen.findByRole('dialog', {name: 'Add segment filter'})).getByRole('button', {name: 'Big sales'}));
  expect(await screen.findByText('Combined Regions so this segment works.')).toBeInTheDocument();
  await waitFor(() => expect(calls('/query-specs/run').at(-1)?.body.spec.joins.map((join: {table: string}) => join.table)).toEqual(['regions']));
});
it('says so when the segment cannot be read, and adds nothing', async () => {
  const user = userEvent.setup();
  behaviour.fail['/views/s1'] = new ApiError(404, 'Not found');
  view();
  await user.click(await screen.findByRole('button', {name: 'Add segment'}));
  await user.click(within(await screen.findByRole('dialog', {name: 'Add segment filter'})).getByRole('button', {name: 'Big sales'}));
  expect(await screen.findByRole('alert')).toHaveTextContent('Not found');
  expect(screen.queryByRole('button', {name: /segment Big sales/})).toBeNull();
});
it('flips a segment chip between In and Not in, and removes it with Delete', async () => {
  const user = userEvent.setup();
  view();
  await user.click(await screen.findByRole('button', {name: 'Add segment'}));
  await user.click(within(await screen.findByRole('dialog', {name: 'Add segment filter'})).getByRole('button', {name: 'Big sales'}));
  const chip = await screen.findByRole('button', {name: 'In segment Big sales'});
  await user.click(chip);
  const choice = await screen.findByRole('dialog', {name: 'Edit filter: In segment Big sales'});
  await user.click(within(choice).getByRole('radio', {name: 'Not in segment'}));
  await waitFor(() => expect(screen.getByRole('button', {name: 'Not in segment Big sales'})).toBeInTheDocument());
  await user.keyboard('{Escape}');
  screen.getByRole('button', {name: 'Not in segment Big sales'}).focus();
  await user.keyboard('{Delete}');
  expect(screen.queryByRole('button', {name: /segment Big sales/})).toBeNull();
});
it('marks a removed segment and offers Remove filter when the run reports it missing', async () => {
  const user = userEvent.setup();
  const spec: QuerySpec = {...detail('segment').spec, filters: {op: 'and', items: [{segment: 'gone'}]}};
  behaviour.reply['/query-specs/compile'] = segmentMissing.response;
  view({seed: spec, views: views([])});
  expect(await screen.findByText('Segment removed')).toBeInTheDocument();
  expect(await screen.findByRole('alert')).toHaveTextContent('The segment this filter uses was deleted.');
  await user.click(screen.getByRole('button', {name: 'Remove filter'}));
  expect(screen.queryByText('Segment removed')).toBeNull();
});
it('opens a drill tab seeded with its rows and a note, without save controls', async () => {
  const spec: QuerySpec = {...detail('segment').spec, filters: {op: 'and', items: [{field: {table_alias: 'sales', column: 'region'}, op: 'eq', value: 'East'}]}};
  view({seed: spec, label: 'East · 2026-01', subtitle: 'The rows behind this number.'});
  expect(await screen.findByRole('heading', {level: 1, name: 'East · 2026-01'})).toBeInTheDocument();
  expect(screen.getByText('The rows behind this number.')).toBeInTheDocument();
  expect(screen.getByRole('button', {name: 'Remove filter: Region is East'})).toBeInTheDocument();
  expect(screen.queryByRole('button', {name: 'Save as segment'})).toBeNull();
  await waitFor(() => expect(calls('/query-specs/run').at(-1)?.body.spec.filters.items).toEqual(spec.filters.items));
});

const ctx = {label: (ref: {column: string}) => humanize(ref.column), kind: () => 'text' as const};
it('keeps segment nodes when other filters change, and has no axe violations', async () => {
  const onChange = vi.fn(), filters: FilterGroup = {op: 'and', items: [{segment: 's1'}, {field: {table_alias: 'sales', column: 'status'}, op: 'eq', value: 'open'}]};
  const user = userEvent.setup(), {container} = render(<FilterChips filters={filters} context={ctx} options={[]} onChange={onChange} segments={{items: [segment('s1', 'Big sales')], baseTable: 'sales', names: id => id === 's1' ? 'Big sales' : undefined, onAdd: vi.fn()}}/>);
  await user.click(screen.getByRole('button', {name: 'Remove filter: Status is open'}));
  expect(onChange.mock.calls[0][0].items).toEqual([{segment: 's1'}]);
  await expectNoAxeViolations(container);
});
