import {afterEach, beforeEach, expect, it, vi} from 'vitest';
import {cleanup, render, screen, waitFor, within} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import AuditPanel from './AuditPanel';
import {ApiError, request} from '../../lib/api';
import history from '../../test-utils/ui-fixtures/history.json';
import runs from '../../test-utils/ui-fixtures/runs.json';
import {expectNoAxeViolations} from '../../test-axe';

vi.mock('../../lib/api', () => ({request: vi.fn(), ApiError: class ApiError extends Error {constructor(public status: number, message: string) {super(message);}}}));
const api = vi.mocked(request);
let historyResult: unknown, runsResult: unknown;
beforeEach(() => {
  vi.clearAllMocks();
  historyResult = history.response; runsResult = runs.response;
  api.mockImplementation(async path => {
    if (path.includes('/history')) {if (historyResult instanceof Error) throw historyResult; return historyResult;}
    if (path.includes('/runs')) {if (runsResult instanceof Error) throw runsResult; return runsResult;}
    throw new Error('Unexpected request: ' + path);
  });
});
afterEach(cleanup);
const names = (id: string) => id === 's1' ? 'Big spenders' : undefined;
const view = (props: Partial<React.ComponentProps<typeof AuditPanel>> = {}) => render(<AuditPanel workspaceId="w1" viewId="v1" canSeeHistory refreshKey={1} names={names} {...props}/>);

it('shows revisions newest first with who, when and a sentence for each change', async () => {
  view();
  const list = await screen.findByRole('list', {name: 'Revisions'});
  const items = within(list).getAllByRole('listitem').filter(item => item.parentElement === list);
  expect(items).toHaveLength(3);
  expect(items[0]).toHaveTextContent('Revision 3 · Bob');
  expect(within(items[0]).getByText('Added filter: Amount is at least 5000')).toBeInTheDocument();
  expect(within(items[0]).getByText('Removed filter: Amount is at least 1000')).toBeInTheDocument();
  expect(items[1]).toHaveTextContent('Renamed from "Large orders" to "Big sales"');
  expect(items[2]).toHaveTextContent('Created by Alice');
});
it('shows archived revisions and saves that changed nothing', async () => {
  historyResult = {view_id: 'v1', next_before: null, revisions: [
    {revision: 3, action: 'archived', at: '2026-11-13T10:00:00+00:00', actor: {id: 'p', name: 'Bob'}, name: 'x', changes: []},
    {revision: 2, action: 'updated', at: '2026-11-13T09:00:00+00:00', actor: {id: 'p', name: 'Bob'}, name: 'x', changes: []}]};
  view();
  expect(await screen.findByText('Deleted by Bob')).toBeInTheDocument();
  expect(screen.getByText('Saved with no visible changes.')).toBeInTheDocument();
});
it('pages with Show earlier using the revision cursor', async () => {
  const user = userEvent.setup();
  historyResult = {...history.response, revisions: history.response.revisions.slice(0, 1), next_before: 3};
  view();
  await screen.findByRole('list', {name: 'Revisions'});
  historyResult = {...history.response, revisions: history.response.revisions.slice(1), next_before: null};
  await user.click(screen.getByRole('button', {name: 'Show earlier'}));
  expect(await screen.findByText('Created by Alice')).toBeInTheDocument();
  expect(api.mock.calls.at(-1)?.[0]).toContain('before=3');
  expect(screen.queryByRole('button', {name: 'Show earlier'})).toBeNull();
});
it('lists runs with who, rows, time, result and revision', async () => {
  const user = userEvent.setup();
  view();
  await user.click(await screen.findByRole('tab', {name: 'Runs'}));
  const table = await screen.findByRole('table', {name: 'Runs'});
  const row = within(table).getAllByRole('row')[1];
  expect(within(row).getByRole('rowheader')).toHaveTextContent('Bob');
  expect(row).toHaveTextContent('4');
  expect(row).toHaveTextContent('0.3 s');
  expect(row).toHaveTextContent('Completed');
  expect(row).toHaveTextContent('3');
});
it('shows a failed run with its safe message and a count run as a count', async () => {
  const user = userEvent.setup();
  runsResult = {view_id: 'v1', next_cursor: null, runs: [
    {...runs.response.runs[0], job_id: 'a', status: 'failed', error: 'The source took too long.', row_count: null, duration_ms: 4000},
    {...runs.response.runs[0], job_id: 'b', mode: 'count', mine: true}]};
  view();
  await user.click(await screen.findByRole('tab', {name: 'Runs'}));
  expect(await screen.findByText('The source took too long.')).toBeInTheDocument();
  expect(screen.getByText('Failed')).toBeInTheDocument();
  expect(screen.getByRole('rowheader', {name: 'You'})).toBeInTheDocument();
  expect(screen.getByText('Count')).toBeInTheDocument();
});
it('gives viewers only their own runs and no revisions', async () => {
  view({canSeeHistory: false});
  expect(await screen.findByRole('tab', {name: 'Your runs'})).toBeInTheDocument();
  expect(screen.queryByRole('tab', {name: 'Revisions'})).toBeNull();
  expect(api.mock.calls.every(call => !String(call[0]).includes('/history'))).toBe(true);
  await screen.findByRole('table', {name: 'Your runs'});
});
it('moves between the tabs with the arrow keys', async () => {
  const user = userEvent.setup();
  view();
  await screen.findByRole('list', {name: 'Revisions'});
  screen.getByRole('tab', {name: 'Revisions'}).focus();
  await user.keyboard('{ArrowRight}');
  expect(screen.getByRole('tab', {name: 'Runs'})).toHaveFocus();
  await user.keyboard('{Home}');
  expect(screen.getByRole('tab', {name: 'Revisions'})).toHaveFocus();
});
it('says what is missing and offers a retry when loading fails', async () => {
  const user = userEvent.setup();
  historyResult = new ApiError(500, 'The history is unavailable.');
  view();
  expect(await screen.findByRole('alert')).toHaveTextContent('The history is unavailable.');
  historyResult = history.response;
  await user.click(screen.getByRole('button', {name: 'Try again'}));
  expect(await screen.findByRole('list', {name: 'Revisions'})).toBeInTheDocument();
});
it('has an empty state and a loading skeleton', async () => {
  historyResult = {view_id: 'v1', revisions: [], next_before: null};
  view();
  expect(screen.getByRole('status', {name: 'Loading revisions'})).toBeInTheDocument();
  expect(await screen.findByText(/Nothing here yet/)).toBeInTheDocument();
});
it('reloads when the refresh key changes', async () => {
  const {rerender} = view();
  await screen.findByRole('list', {name: 'Revisions'});
  const before = api.mock.calls.length;
  rerender(<AuditPanel workspaceId="w1" viewId="v1" canSeeHistory refreshKey={2} names={names}/>);
  await waitFor(() => expect(api.mock.calls.length).toBeGreaterThan(before));
});
it('has no axe violations on either tab', async () => {
  const user = userEvent.setup(), {container} = view();
  await screen.findByRole('list', {name: 'Revisions'});
  await expectNoAxeViolations(container);
  await user.click(screen.getByRole('tab', {name: 'Runs'}));
  await screen.findByRole('table', {name: 'Runs'});
  await expectNoAxeViolations(container);
});
it('puts the runs table in a labelled region that the keyboard can reach, so it scrolls on a narrow screen', async () => {
  const user = userEvent.setup();
  view();
  await user.click(await screen.findByRole('tab', {name: 'Runs'}));
  const region = await screen.findByRole('region', {name: 'Runs (scrollable)'});
  expect(region).toHaveAttribute('tabindex', '0');
  expect(within(region).getByRole('table', {name: 'Runs'})).toBeInTheDocument();
});
