import {readFileSync} from 'node:fs';
import {afterEach, beforeEach, expect, it, vi} from 'vitest';
import {cleanup, render, screen, within} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import PivotTab from './PivotTab';
import SegmentTab from './SegmentTab';
import TablesOverview from './TablesOverview';
import {controller, detail, installViewApi, newBehaviour, type Behaviour} from '../../test-utils/views-api';
import {sizeGridScrollers} from '../../test-utils/virtual';
import type {ViewsController} from './use-views';

vi.mock('../../lib/api', () => ({request: vi.fn(), ApiError: class ApiError extends Error {constructor(public status: number, message: string) {super(message);}}}));
vi.mock('../../lib/transfers', () => ({transfer: vi.fn()}));
let behaviour: Behaviour;
beforeEach(() => {vi.clearAllMocks(); sizeGridScrollers(); behaviour = newBehaviour(); behaviour.views.s1 = detail('segment', {id: 's1', name: 'Big sales'}); installViewApi(behaviour);});
afterEach(cleanup);
const store: ViewsController = {views: [], status: 'ready', error: '', refresh: vi.fn(), upsert: vi.fn(), remove: vi.fn(), byId: () => undefined, nameOf: () => undefined};
const css = (name: string) => readFileSync(new URL('./' + name, import.meta.url), 'utf8');

/** Words that belong to the database, not to the person using the view. "Show SQL" and "Advanced SQL" are the one allowed exception. */
function leaks(text: string) {
  const cleaned = text.replace(/Show SQL|Open Advanced SQL|Advanced SQL|Generated SQL \(read-only\)/g, '');
  return cleaned.match(/\b(SQL|JOIN|WHERE|CASE|GROUP BY|SELECT|aggregate|query-spec|segment_[a-z_]+|pivot_[a-z_]+)\b|\baggregate/gi) || [];
}

it('keeps database words out of the pivot tab', async () => {
  const {container} = render(<PivotTab workspaceId="w1" connectionId="c1" tab={{id: 'pivot:view:v1', kind: 'pivot', viewId: 'v1'}} label="Pivot" controller={controller()} engineCanLoad views={store} canEdit
    onClose={vi.fn()} onSaved={vi.fn()} onArchived={vi.fn()} onDrill={vi.fn()}/>);
  await screen.findByRole('grid');
  expect(leaks(container.textContent || '')).toEqual([]);
});
it('keeps database words out of the segment tab and its audit panel', async () => {
  const user = userEvent.setup();
  const {container} = render(<SegmentTab workspaceId="w1" connectionId="c1" tab={{id: 'segment:view:s1', kind: 'segment', viewId: 's1'}} label="Segment" controller={controller()} views={store} canEdit
    onClose={vi.fn()} onArchived={vi.fn()} onDrill={vi.fn()} onOpenView={vi.fn()} onUseForPivot={vi.fn()}/>);
  await screen.findByRole('heading', {level: 1});
  await screen.findByText(/Nothing here yet/);
  await user.click(screen.getByRole('tab', {name: 'Runs'}));
  expect(leaks(container.textContent || '')).toEqual([]);
});

const tables = [{name: 'orders', row_estimate: 10, columns: [{name: 'id'}]}];
it('puts the overview table in a scroll wrapper so no header is clipped on a narrow screen', async () => {
  const user = userEvent.setup();
  render(<TablesOverview workspaceId="w1" role="owner" hasConnections source={{id: 'c1', revision: 1, name: 'Source', engine: 'sqlite', config: {}}} controller={controller({schema: {tables}})}
    canEditSource onConnect={vi.fn()} onEditSource={vi.fn()} onArchiveSource={vi.fn()} onOpenTable={vi.fn()} onPivotTable={vi.fn()}/>);
  const table = await screen.findByRole('table');
  expect(table.parentElement).toHaveClass('data-table-scroll');
  expect(screen.getByRole('columnheader', {name: 'Rows'})).toBeInTheDocument();
  await user.click(screen.getByRole('button', {name: 'More actions for orders'}));
  expect(await screen.findByRole('menuitem', {name: 'Pivot this table'})).toBeInTheDocument();
});
it('styles the overview table to scroll sideways and keep its headers whole', () => {
  const query = css('data-query.css'), studio = css('data-studio.css');
  expect(query).toMatch(/\.data-table-scroll\{[^}]*overflow-x:auto/);
  expect(query).toMatch(/\.data-table-list thead th\{[^}]*white-space:nowrap/);
  expect(query).toMatch(/\.data-number\{[^}]*min-width|th\.data-number\{[^}]*min-width:88px/);
  expect(studio).not.toMatch(/\.data-row-action\{[^}]*width:\s*1%/);
  expect(studio).not.toMatch(/\.data-table-list[^{]*\{[^}]*\bwidth:\s*\d+px/);
});
it('keeps the pivot grid on tokens: sticky first column, tabular numbers and a visible focus ring', () => {
  const query = css('data-query.css');
  expect(query).toMatch(/\.data-pivot\{[^}]*font-variant-numeric:tabular-nums/);
  expect(query).toMatch(/\.data-pivot thead th:first-child[^{]*\{[^}]*position:sticky/);
  expect(query).toMatch(/\.data-pivot-cell:focus-visible\{[^}]*outline/);
  expect(within(document.body).queryByRole('grid')).toBeNull();
});
