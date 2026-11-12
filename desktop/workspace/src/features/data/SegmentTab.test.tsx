import {afterEach, beforeEach, expect, it, vi} from 'vitest';
import {cleanup, render, screen, waitFor, within} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import SegmentTab from './SegmentTab';
import {ApiError} from '../../lib/api';
import {transfer} from '../../lib/transfers';
import conflict from '../../lib/view-contract-fixtures/view-conflict.json';
import inUse from '../../lib/view-contract-fixtures/view-delete-in-use.json';
import {controller, detail, installViewApi, newBehaviour, type Behaviour} from '../../test-utils/views-api';
import {expectNoAxeViolations} from '../../test-axe';
import type {ViewsController} from './use-views';

vi.mock('../../lib/api', () => ({request: vi.fn(), ApiError: class ApiError extends Error {constructor(public status: number, message: string) {super(message);}}}));
vi.mock('../../lib/transfers', () => ({transfer: vi.fn()}));
let behaviour: Behaviour;
const tab = {id: 'segment:view:v1', kind: 'segment' as const, viewId: 'v1'};
const names: Record<string, string> = {v1: 'Big sales', seg2: 'East coast'};
const views = (): ViewsController => ({views: [], status: 'ready', error: '', refresh: vi.fn(), upsert: vi.fn(), remove: vi.fn(), byId: () => undefined, nameOf: id => names[id]});

beforeEach(() => {
  vi.clearAllMocks();
  behaviour = newBehaviour();
  behaviour.views.v1 = detail('segment', {name: 'Big sales', revision: 3});
  installViewApi(behaviour);
  vi.stubGlobal('URL', Object.assign(URL, {createObjectURL: vi.fn(() => 'blob:x'), revokeObjectURL: vi.fn()}));
  vi.mocked(transfer).mockResolvedValue(new Blob(['a']));
});
afterEach(() => {cleanup(); vi.unstubAllGlobals();});
function view(props: Partial<React.ComponentProps<typeof SegmentTab>> = {}) {
  const handlers = {onClose: vi.fn(), onArchived: vi.fn(), onDrill: vi.fn(), onOpenView: vi.fn(), onUseForPivot: vi.fn()}, store = views();
  const result = render(<SegmentTab workspaceId="w1" connectionId="c1" tab={tab} label="Segment" controller={controller()} views={store} canEdit {...handlers} {...props}/>);
  return {...result, ...handlers, store};
}
const calls = (suffix: string, method?: string) => behaviour.calls.filter(call => call.path.split('?')[0].endsWith(suffix) && (!method || call.method === method));
const primary = () => Array.from(document.querySelectorAll('.button-primary')).map(button => button.textContent);

it('shows the definition in plain language and Show underlying rows as the only primary action', async () => {
  view();
  expect(await screen.findByRole('heading', {level: 1, name: 'Big sales'})).toBeInTheDocument();
  expect(screen.getByText('Segment on Sales · revision 3')).toBeInTheDocument();
  expect(screen.getByText('Amount is at least 5000')).toBeInTheDocument();
  expect(primary()).toEqual(['Show underlying rows']);
  expect(calls('/run')).toHaveLength(0);
});
it('counts members only when asked and announces the number', async () => {
  const user = userEvent.setup();
  view();
  await screen.findByRole('heading', {level: 1});
  await user.click(screen.getByRole('button', {name: 'Count members'}));
  expect(await screen.findByText('42 members')).toBeInTheDocument();
  expect(calls('/views/v1/run')).toHaveLength(1);
  expect(calls('/views/v1/run')[0].body.mode).toBe('count');
});
it('opens the member rows in a drill tab', async () => {
  const user = userEvent.setup(), {onDrill} = view();
  await user.click(await screen.findByRole('button', {name: 'Show underlying rows'}));
  await waitFor(() => expect(onDrill).toHaveBeenCalled());
  expect(calls('/views/v1/drill')[0].body).toEqual({coordinates: {}});
  expect(onDrill.mock.calls[0][1]).toBe('Members of Big sales');
});
it('starts a pivot from the segment', async () => {
  const user = userEvent.setup(), {onUseForPivot} = view();
  await user.click(await screen.findByRole('button', {name: 'Use as source for a pivot'}));
  expect(onUseForPivot.mock.calls[0][0]).toMatchObject({id: 'v1'});
});
it('edits the filters, shows Save changes as the only primary action, and saves with the held revision', async () => {
  const user = userEvent.setup(), {store} = view();
  await screen.findByRole('heading', {level: 1});
  await user.click(screen.getByRole('button', {name: 'Edit filters'}));
  await user.click(await screen.findByRole('button', {name: 'Remove filter: Amount is at least 5000'}));
  await waitFor(() => expect(primary()).toEqual(['Save changes']));
  expect(screen.getByRole('button', {name: 'Save changes'})).toBeDisabled();
  expect(screen.getByRole('button', {name: 'Count members'})).toBeDisabled();
  expect(screen.getByText('Save your changes to use the actions below.')).toBeInTheDocument();
  await user.click(screen.getByRole('button', {name: 'Reset'}));
  expect(screen.getByRole('button', {name: 'Remove filter: Amount is at least 5000'})).toBeInTheDocument();
  await user.click(screen.getByRole('button', {name: 'Add filter'}));
  const dialog = await screen.findByRole('dialog', {name: 'Add filter'});
  await user.selectOptions(within(dialog).getByLabelText('Column'), 'sales\u0000amount');
  await user.selectOptions(within(dialog).getByLabelText('Condition'), 'lt');
  await user.type(within(dialog).getByLabelText('Value'), '9000');
  await user.click(within(dialog).getByRole('button', {name: 'Apply'}));
  await user.click(await screen.findByRole('button', {name: 'Save changes'}));
  await waitFor(() => expect(calls('/views/v1', 'PATCH')).toHaveLength(1));
  expect(calls('/views/v1', 'PATCH')[0].body.revision).toBe(3);
  expect(calls('/views/v1', 'PATCH')[0].body.spec.filters.items).toHaveLength(2);
  expect(await screen.findByText('Saved changes.')).toBeInTheDocument();
  expect(screen.getByText('Segment on Sales · revision 4')).toBeInTheDocument();
  expect(store.upsert).toHaveBeenCalled();
});
it('does not save a segment with no filters', async () => {
  const user = userEvent.setup();
  view();
  await screen.findByRole('heading', {level: 1});
  await user.click(screen.getByRole('button', {name: 'Edit filters'}));
  await user.click(await screen.findByRole('button', {name: 'Remove filter: Amount is at least 5000'}));
  expect(screen.getByRole('button', {name: 'Save changes'})).toBeDisabled();
  expect(screen.getByText('This segment has no filters, so every row is a member.')).toBeInTheDocument();
});
it('shows the conflict banner on a stale save and reloads the latest revision', async () => {
  const user = userEvent.setup();
  view();
  await screen.findByRole('heading', {level: 1});
  await user.click(screen.getByRole('button', {name: 'Edit filters'}));
  await user.click(await screen.findByRole('button', {name: 'Add group'}));
  await user.click(screen.getByRole('button', {name: 'Remove group 2'}));
  await user.click(await screen.findByRole('button', {name: 'Remove filter: Amount is at least 5000'}));
  await user.click(screen.getByRole('button', {name: 'Add filter'}));
  const dialog = await screen.findByRole('dialog', {name: 'Add filter'});
  await user.selectOptions(within(dialog).getByLabelText('Column'), 'sales\u0000status');
  await user.type(within(dialog).getByLabelText('Value'), 'open');
  await user.click(within(dialog).getByRole('button', {name: 'Apply'}));
  behaviour.fail['PATCH /views/v1'] = new ApiError(409, JSON.stringify(conflict.response.detail));
  await user.click(await screen.findByRole('button', {name: 'Save changes'}));
  expect(await screen.findByRole('heading', {name: /Bob changed this view/})).toHaveFocus();
  behaviour.fail = {};
  behaviour.views.v1 = {...behaviour.views.v1, revision: 5};
  await user.click(screen.getByRole('button', {name: 'Reload latest'}));
  expect(await screen.findByText('Segment on Sales · revision 5')).toBeInTheDocument();
  expect(screen.getByText('Amount is at least 5000')).toBeInTheDocument();
  expect(screen.queryByRole('heading', {name: /Bob changed this view/})).toBeNull();
});
it('links to a segment this one uses', async () => {
  const user = userEvent.setup(), spec = detail('segment').spec;
  behaviour.views.v1 = detail('segment', {name: 'Big sales', spec: {...spec, filters: {op: 'and', items: [...spec.filters.items, {segment: 'seg2'}]}}});
  const {onOpenView} = view();
  await user.click(await screen.findByRole('button', {name: 'In segment East coast'}));
  expect(onOpenView).toHaveBeenCalledWith({id: 'seg2', kind: 'segment'});
});
it('says when a segment it uses was deleted', async () => {
  const spec = detail('segment').spec;
  behaviour.views.v1 = detail('segment', {name: 'Big sales', spec: {...spec, filters: {op: 'and', items: [{segment: 'gone'}]}}, references: [{id: 'gone', name: 'Old', state: 'missing'}]});
  view();
  expect(await screen.findByText('Segment removed')).toBeInTheDocument();
  expect(screen.getByRole('alert')).toHaveTextContent('A segment this one uses was deleted.');
});
it('lists the views that use it and opens them', async () => {
  const user = userEvent.setup();
  behaviour.views.v1 = detail('segment', {name: 'Big sales', used_by: [{id: 'p9', kind: 'pivot', name: 'Sales by region'}]});
  const {onOpenView} = view();
  await user.click(await screen.findByRole('button', {name: 'Sales by region'}));
  expect(onOpenView).toHaveBeenCalledWith({id: 'p9', kind: 'pivot', name: 'Sales by region'});
});
it('blocks deleting a segment that is in use and lists who uses it', async () => {
  const user = userEvent.setup(), {onArchived, onOpenView} = view();
  behaviour.fail['DELETE /views/v1'] = new ApiError(409, JSON.stringify(inUse.response.detail));
  await screen.findByRole('heading', {level: 1});
  await user.click(screen.getByRole('button', {name: 'More actions for this segment'}));
  await user.click(await screen.findByRole('menuitem', {name: 'Delete'}));
  await user.click(await screen.findByRole('button', {name: 'Delete segment'}));
  const list = await screen.findByRole('list', {name: 'Views that use this segment'});
  expect(within(list).getAllByRole('button').map(button => button.textContent)).toEqual(['Sales by region and month', 'Orders east']);
  expect(screen.getByRole('button', {name: 'Delete segment'})).toBeDisabled();
  expect(onArchived).not.toHaveBeenCalled();
  await user.click(within(list).getAllByRole('button')[0]);
  expect(onOpenView).toHaveBeenCalled();
});
it('deletes an unused segment', async () => {
  const user = userEvent.setup(), {onArchived, store} = view();
  await screen.findByRole('heading', {level: 1});
  await user.click(screen.getByRole('button', {name: 'More actions for this segment'}));
  await user.click(await screen.findByRole('menuitem', {name: 'Delete'}));
  await user.click(await screen.findByRole('button', {name: 'Delete segment'}));
  await waitFor(() => expect(onArchived).toHaveBeenCalledWith('v1'));
  expect(calls('/views/v1', 'DELETE')[0].path).toContain('revision=3');
  expect(store.remove).toHaveBeenCalledWith('v1');
});
it('exports the members through a view run', async () => {
  const user = userEvent.setup();
  view();
  await user.click(await screen.findByRole('button', {name: 'Export CSV'}));
  await waitFor(() => expect(transfer).toHaveBeenCalled());
  expect(vi.mocked(transfer).mock.calls[0][0]).toMatch(/\/views\/v1\/runs\/run-\d+\/export\.csv$/);
  expect(calls('/views/v1/run')[0].body.mode).toBe('rows');
});
it('shows a viewer the definition, the count and their own runs, and nothing to change', async () => {
  const user = userEvent.setup();
  behaviour.views.v1 = detail('segment', {name: 'Big sales', can_edit: false});
  view({canEdit: false});
  await screen.findByRole('heading', {level: 1});
  for (const name of ['Edit filters', 'Show underlying rows', 'Use as source for a pivot', 'More actions for this segment', 'Save changes']) expect(screen.queryByRole('button', {name})).toBeNull();
  expect(screen.getByRole('tab', {name: 'Your runs'})).toBeInTheDocument();
  expect(screen.queryByRole('tab', {name: 'Revisions'})).toBeNull();
  await user.click(screen.getByRole('button', {name: 'Count members'}));
  expect(await screen.findByText('42 members')).toBeInTheDocument();
  expect(calls('/history')).toHaveLength(0);
});
it('offers a retry when the segment cannot be loaded', async () => {
  const user = userEvent.setup();
  behaviour.fail['/views/v1'] = new ApiError(404, 'Not found');
  const {onClose} = view();
  expect(await screen.findByText('This segment is no longer available.')).toBeInTheDocument();
  await user.click(screen.getByRole('button', {name: 'Close tab'}));
  expect(onClose).toHaveBeenCalled();
});
it('keeps the audit panel on screen with revisions and runs, and has no axe violations', async () => {
  const {container} = view();
  await screen.findByRole('heading', {level: 1});
  expect(screen.getByRole('region', {name: 'Audit'})).toBeInTheDocument();
  await screen.findByText(/Nothing here yet/);
  await expectNoAxeViolations(container);
});
