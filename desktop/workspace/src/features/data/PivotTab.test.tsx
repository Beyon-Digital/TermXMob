import {afterEach, beforeEach, expect, it, vi} from 'vitest';
import {cleanup, render, screen, waitFor, within} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import PivotTab from './PivotTab';
import {ApiError} from '../../lib/api';
import {transfer} from '../../lib/transfers';
import conflict from '../../lib/view-contract-fixtures/view-conflict.json';
import tooMany from '../../lib/view-contract-fixtures/compile-pivot-too-many.json';
import {controller, detail, installViewApi, newBehaviour, type Behaviour} from '../../test-utils/views-api';
import {expectNoAxeViolations} from '../../test-axe';
import type {DataTab} from '../../lib/data-tabs';
import type {ViewsController} from './use-views';

vi.mock('../../lib/api', () => ({request: vi.fn(), ApiError: class ApiError extends Error {constructor(public status: number, message: string) {super(message);}}}));
vi.mock('../../lib/transfers', () => ({transfer: vi.fn()}));
let behaviour: Behaviour;
const draftTab: DataTab = {id: 'pivot:new:c1:sales:1', kind: 'pivot', connectionId: 'c1', table: 'sales'};
const savedTab: DataTab = {id: 'pivot:view:v1', kind: 'pivot', viewId: 'v1'};
const views = (): ViewsController => ({views: [], status: 'ready', error: '', refresh: vi.fn(), upsert: vi.fn(), remove: vi.fn(), byId: () => undefined, nameOf: () => undefined});

beforeEach(() => {
  vi.clearAllMocks();
  behaviour = newBehaviour();
  installViewApi(behaviour);
  vi.stubGlobal('URL', Object.assign(URL, {createObjectURL: vi.fn(() => 'blob:x'), revokeObjectURL: vi.fn()}));
  vi.mocked(transfer).mockResolvedValue(new Blob(['a,b']));
});
afterEach(() => {cleanup(); vi.unstubAllGlobals();});

function view(tab: DataTab = draftTab, props: Partial<React.ComponentProps<typeof PivotTab>> = {}) {
  const handlers = {onClose: vi.fn(), onSaved: vi.fn(), onArchived: vi.fn(), onDrill: vi.fn(), onOpenAdvanced: vi.fn()}, store = views();
  const result = render(<PivotTab workspaceId="w1" connectionId="c1" tab={tab} label="Pivot" controller={controller()} engineCanLoad views={store} canEdit {...handlers} {...props}/>);
  return {...result, ...handlers, store};
}
const calls = (suffix: string, method?: string) => behaviour.calls.filter(call => call.path.split('?')[0].endsWith(suffix) && (!method || call.method === method));
const primaryButtons = () => Array.from(document.querySelectorAll('.button-primary'));

it('starts a draft on a text column, runs it live and offers Save view as the only primary action', async () => {
  view();
  expect(screen.getByRole('status', {name: 'Loading pivot'})).toBeInTheDocument();
  expect(await screen.findByRole('grid', {name: 'New pivot on Sales'})).toBeInTheDocument();
  expect(screen.getByRole('heading', {level: 1, name: 'New pivot on Sales'})).toBeInTheDocument();
  expect(screen.getByText('Number of rows by region')).toBeInTheDocument();
  expect(calls('/query-specs/compile')[0].body.spec.pivot.rows).toEqual([{table_alias: 'sales', column: 'region'}]);
  expect(calls('/views/v1/run')).toHaveLength(0);
  expect(primaryButtons().map(button => button.textContent)).toEqual(['Save view']);
  expect(screen.getByRole('region', {name: 'Results'})).toHaveAttribute('aria-busy', 'false');
});
it('re-runs when the builder changes', async () => {
  const user = userEvent.setup();
  view();
  await screen.findByRole('grid');
  await user.selectOptions(screen.getByLabelText('Show'), 'sum');
  await waitFor(() => expect(calls('/query-specs/run').at(-1)?.body.spec.pivot.value).toEqual({fn: 'sum', table_alias: 'sales', column: 'amount'}));
});
it('saves a new pivot through the dialog and hands the saved view to the studio', async () => {
  const user = userEvent.setup(), {onSaved, store} = view();
  await screen.findByRole('grid');
  await user.click(screen.getByRole('button', {name: 'Save view'}));
  const dialog = await screen.findByRole('dialog', {name: 'Save pivot'});
  await user.type(within(dialog).getByLabelText('Name'), 'Sales by region');
  await user.click(within(dialog).getByRole('button', {name: 'Save view'}));
  await waitFor(() => expect(onSaved).toHaveBeenCalled());
  expect(calls('/views', 'POST')[0].body).toMatchObject({kind: 'pivot', name: 'Sales by region'});
  expect(calls('/views', 'POST')[0].body.spec.pivot.rows).toHaveLength(1);
  expect(onSaved.mock.calls[0][1]).toBe(draftTab.id);
  expect(store.upsert).toHaveBeenCalled();
});
it('runs a clean saved pivot through the view, with no primary action and a Refresh button', async () => {
  view(savedTab);
  expect(await screen.findByRole('grid', {name: 'Sales by region and month'})).toBeInTheDocument();
  expect(calls('/views/v1/run', 'POST')).toHaveLength(1);
  expect(calls('/query-specs/run')).toHaveLength(0);
  expect(primaryButtons()).toHaveLength(0);
  expect(screen.getByRole('button', {name: 'Refresh'})).toBeInTheDocument();
});
it('turns into a live preview with Save changes as the only primary action once edited', async () => {
  const user = userEvent.setup();
  view(savedTab);
  await screen.findByRole('grid');
  await user.selectOptions(screen.getByLabelText('Show'), 'count');
  await waitFor(() => expect(primaryButtons().map(button => button.textContent)).toEqual(['Save changes']));
  await waitFor(() => expect(calls('/query-specs/run').length).toBeGreaterThan(0));
  expect(screen.getByRole('button', {name: 'Reset'})).toBeInTheDocument();
  await user.click(screen.getByRole('button', {name: 'Save changes'}));
  await waitFor(() => expect(calls('/views/v1', 'PATCH')).toHaveLength(1));
  expect(calls('/views/v1', 'PATCH')[0].body.revision).toBe(1);
  expect(calls('/views/v1', 'PATCH')[0].body.spec.pivot.value.fn).toBe('count');
  await waitFor(() => expect(primaryButtons()).toHaveLength(0));
  expect(screen.getByText('Saved changes.')).toBeInTheDocument();
});
it('puts the pivot back with Reset', async () => {
  const user = userEvent.setup();
  view(savedTab);
  await screen.findByRole('grid');
  await user.selectOptions(screen.getByLabelText('Show'), 'count');
  await user.click(await screen.findByRole('button', {name: 'Reset'}));
  expect(screen.getByLabelText('Show')).toHaveValue('sum');
  await waitFor(() => expect(primaryButtons()).toHaveLength(0));
});
it('shows the conflict banner on a stale save and reloads the latest revision', async () => {
  const user = userEvent.setup();
  view(savedTab);
  await screen.findByRole('grid');
  await user.selectOptions(screen.getByLabelText('Show'), 'count');
  behaviour.fail['PATCH /views/v1'] = new ApiError(409, JSON.stringify(conflict.response.detail));
  await user.click(await screen.findByRole('button', {name: 'Save changes'}));
  const banner = await screen.findByRole('heading', {name: /Bob changed this view/});
  expect(banner).toHaveFocus();
  expect(screen.getByRole('alert')).toHaveTextContent('Your edits have not been saved.');
  expect(screen.getByRole('button', {name: 'Save as a new view'})).toBeInTheDocument();
  behaviour.fail = {};
  behaviour.views.v1 = {...behaviour.views.v1, revision: 5, name: 'Changed by Bob'};
  await user.click(screen.getByRole('button', {name: 'Reload latest'}));
  expect(await screen.findByRole('heading', {level: 1, name: 'Changed by Bob'})).toBeInTheDocument();
  await waitFor(() => expect(screen.queryByRole('heading', {name: /Bob changed this view/})).toBeNull());
  expect(screen.getByText('Reloaded revision 5.')).toBeInTheDocument();
  expect(screen.getByLabelText('Show')).toHaveValue('sum');
  expect(primaryButtons()).toHaveLength(0);
});
it('opens the rows behind a cell through the server for a clean saved pivot', async () => {
  const user = userEvent.setup(), {onDrill} = view(savedTab);
  await user.click(await screen.findByRole('gridcell', {name: '250'}));
  await waitFor(() => expect(onDrill).toHaveBeenCalled());
  expect(calls('/views/v1/drill')[0].body).toEqual({coordinates: {rows: ['East'], column: {value: '2026-02', empty: false}}});
  expect(onDrill.mock.calls[0][1]).toBe('East · 2026-02');
});
it('builds the drill locally for a pivot that is not saved, without sending SQL anywhere', async () => {
  const user = userEvent.setup(), {onDrill} = view();
  await user.click(await screen.findByRole('gridcell', {name: '1,600'}));
  expect(onDrill).toHaveBeenCalledTimes(1);
  expect(calls('/drill')).toHaveLength(0);
  expect(onDrill.mock.calls[0][0].pivot).toBeUndefined();
  expect(onDrill.mock.calls[0][0].filters.items).toEqual([{field: {table_alias: 'sales', column: 'region'}, op: 'eq', value: 'West'}]);
  expect(onDrill.mock.calls[0][1]).toBe('West · Total amount');
});
it('says when there are too many columns and offers a coarser step', async () => {
  const user = userEvent.setup();
  behaviour.reply['/query-specs/compile'] = tooMany.response;
  view(savedTab);
  await screen.findByRole('grid');
  await user.selectOptions(screen.getByLabelText('Show'), 'count');
  expect(await screen.findByRole('alert')).toHaveTextContent('more than 12 different values');
  await user.click(screen.getByRole('button', {name: 'Group by quarter'}));
  await waitFor(() => expect(calls('/query-specs/compile').at(-1)?.body.spec.pivot.columns.bucket).toBe('quarter'));
});
it('shows a run error with Try again, and Test connection when the source needs a test', async () => {
  const user = userEvent.setup();
  behaviour.fail['/query-specs/run'] = new ApiError(500, 'The source is not reachable.');
  const first = view();
  expect(await screen.findByText('The source is not reachable.')).toBeInTheDocument();
  behaviour.fail = {};
  await user.click(screen.getByRole('button', {name: 'Try again'}));
  expect(await screen.findByRole('grid')).toBeInTheDocument();
  first.unmount();
  behaviour.fail['/query-specs/run'] = new ApiError(409, 'Test this source first.');
  const second = view();
  expect(await screen.findByRole('button', {name: 'Test connection'})).toBeInTheDocument();
  await user.click(screen.getByRole('button', {name: 'Test connection'}));
  expect(second.container).toBeTruthy();
});
it('has an empty state with Clear filters when nothing matches', async () => {
  behaviour.rows = [];
  const user = userEvent.setup();
  view(savedTab);
  await screen.findByRole('button', {name: 'Refresh'});
  expect(await screen.findByText('There is nothing to show yet.')).toBeInTheDocument();
  await user.click(screen.getByRole('button', {name: 'Add filter'}));
  expect(await screen.findByRole('dialog', {name: 'Add filter'})).toBeInTheDocument();
});
it('explains an unsupported source and offers Advanced SQL', async () => {
  behaviour.capabilities = {connection_id: 'c1', engine: 'x', label: 'X', supported: false, reason: 'Pivots are not supported for X yet.', qualified: false, requires_source_test: false, source_tested: false, dialect: null};
  const user = userEvent.setup(), {onOpenAdvanced} = view();
  expect(await screen.findByText('Pivots are not supported for X yet.')).toBeInTheDocument();
  await user.click(screen.getByRole('button', {name: 'Open Advanced SQL'}));
  expect(onOpenAdvanced).toHaveBeenCalled();
});
it('exports a clean saved pivot through its view run', async () => {
  const user = userEvent.setup();
  view(savedTab);
  await screen.findByRole('grid');
  await user.click(screen.getByRole('button', {name: 'Export CSV'}));
  await waitFor(() => expect(transfer).toHaveBeenCalled());
  expect(vi.mocked(transfer).mock.calls[0][0]).toMatch(/\/views\/v1\/runs\/run-\d+\/export\.csv$/);
});
it('exports an unsaved pivot through its job', async () => {
  const user = userEvent.setup();
  view();
  await screen.findByRole('grid');
  await user.click(screen.getByRole('button', {name: 'Export CSV'}));
  await waitFor(() => expect(transfer).toHaveBeenCalled());
  expect(vi.mocked(transfer).mock.calls[0][0]).toBe('/api/data/workspaces/w1/jobs/live1/export.csv');
});
it('renames and deletes a saved pivot', async () => {
  const user = userEvent.setup(), {onArchived, store} = view(savedTab);
  await screen.findByRole('grid');
  await user.click(screen.getByRole('button', {name: 'More actions for this pivot'}));
  await user.click(await screen.findByRole('menuitem', {name: 'Rename'}));
  const rename = await screen.findByRole('dialog', {name: 'Rename pivot'});
  await user.clear(within(rename).getByLabelText('Name'));
  await user.type(within(rename).getByLabelText('Name'), 'Regions');
  await user.click(within(rename).getByRole('button', {name: 'Save'}));
  await waitFor(() => expect(calls('/views/v1', 'PATCH')[0].body).toEqual({revision: 1, name: 'Regions'}));
  await user.click(screen.getByRole('button', {name: 'More actions for this pivot'}));
  await user.click(await screen.findByRole('menuitem', {name: 'Delete'}));
  await user.click(await screen.findByRole('button', {name: 'Delete pivot'}));
  await waitFor(() => expect(onArchived).toHaveBeenCalledWith('v1'));
  expect(calls('/views/v1', 'DELETE')[0].path).toContain('revision=2');
  expect(store.remove).toHaveBeenCalledWith('v1');
});
it('keeps the delete dialog open with the reason when someone else may not delete', async () => {
  const user = userEvent.setup();
  behaviour.fail['revision='] = new ApiError(403, 'Only the workspace owner or the person who created this view can delete it.');
  view(savedTab);
  await screen.findByRole('grid');
  await user.click(screen.getByRole('button', {name: 'More actions for this pivot'}));
  await user.click(await screen.findByRole('menuitem', {name: 'Delete'}));
  await user.click(await screen.findByRole('button', {name: 'Delete pivot'}));
  expect(await screen.findByText(/Only the workspace owner/)).toBeInTheDocument();
});
it('shows a read-only pivot to a viewer: no builder, no save, no drill', async () => {
  behaviour.views.v1 = detail('pivot', {can_edit: false});
  const {container} = view(savedTab, {canEdit: false});
  const grid = await screen.findByRole('grid');
  expect(screen.queryByRole('region', {name: 'Pivot setup'})).toBeNull();
  expect(screen.queryByRole('button', {name: /Save/})).toBeNull();
  expect(within(grid).getAllByRole('gridcell')[0]).not.toHaveAttribute('data-drill');
  expect(screen.getByRole('button', {name: 'Export CSV'})).toBeInTheDocument();
  expect(calls('/capabilities')).toHaveLength(0);
  expect(calls('/query-specs/compile')).toHaveLength(0);
  await expectNoAxeViolations(container);
});
it('shows an error with a retry when the saved view cannot be loaded', async () => {
  const user = userEvent.setup();
  behaviour.fail['/views/v1'] = new ApiError(404, 'Not found');
  const {onClose} = view(savedTab);
  expect(await screen.findByText('This view is no longer available.')).toBeInTheDocument();
  await user.click(screen.getByRole('button', {name: 'Close tab'}));
  expect(onClose).toHaveBeenCalled();
});
it('starts from a segment: the filter is applied and the segment chip is visible', async () => {
  behaviour.views.seg = detail('segment', {id: 'seg', name: 'Big sales'});
  const store = views(); store.views = [behaviour.views.seg]; store.nameOf = id => id === 'seg' ? 'Big sales' : undefined;
  view({...draftTab, segmentId: 'seg'}, {views: store});
  expect(await screen.findByRole('button', {name: 'In segment Big sales'})).toBeInTheDocument();
  await waitFor(() => expect(calls('/query-specs/compile').at(-1)?.body.spec.filters.items).toEqual([{segment: 'seg'}]));
});
it('has no axe violations in the draft, saved and dirty states', async () => {
  const user = userEvent.setup(), {container} = view(savedTab);
  await screen.findByRole('grid');
  await expectNoAxeViolations(container);
  await user.selectOptions(screen.getByLabelText('Show'), 'count');
  await screen.findByRole('button', {name: 'Save changes'});
  await expectNoAxeViolations(container);
});
