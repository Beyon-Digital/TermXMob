import {afterEach, expect, it, vi} from 'vitest';
import {cleanup, fireEvent, render, screen, within} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import PivotTable from './PivotTable';
import compile from '../../test-utils/ui-fixtures/compile-pivot.json';
import {expectNoAxeViolations} from '../../test-axe';
import type {OutputColumn} from '../../lib/query-spec';

afterEach(cleanup);
const columns = compile.response.columns as unknown as OutputColumn[];
const rows: unknown[][] = [['East', 100, 250, null, 350], ['West', 40, 1500, 60, 1600], [null, 0, null, 5, 5]];
const view = (onDrill?: (r: number, c: number) => void) => render(<PivotTable columns={columns} rows={rows} label="Sales by region" onDrill={onDrill}/>);

it('is a grid with row headers, column headers and counts', () => {
  view();
  const grid = screen.getByRole('grid', {name: 'Sales by region'});
  expect(grid).toHaveAttribute('aria-rowcount', '4');
  expect(grid).toHaveAttribute('aria-colcount', '5');
  expect(within(grid).getAllByRole('columnheader').map(cell => cell.textContent)).toEqual(['Region', '2026-01', '2026-02', '2026-03', 'Total amount']);
  expect(screen.getByRole('rowheader', {name: 'East'})).toBeInTheDocument();
  expect(screen.getByRole('rowheader', {name: '(empty)'})).toBeInTheDocument();
  expect(screen.getByRole('gridcell', {name: '1,500'})).toBeInTheDocument();
});
it('shows a dash with a label for a cell without data, and keeps zero', () => {
  view();
  expect(screen.getAllByRole('gridcell', {name: 'No data'})).toHaveLength(2);
  expect(screen.getAllByRole('gridcell', {name: '0'})).toHaveLength(1);
});
it('has exactly one cell in the tab order and moves with the arrow keys', async () => {
  const user = userEvent.setup();
  view();
  const cells = () => screen.getAllByRole('gridcell');
  expect(cells().filter(cell => cell.tabIndex === 0)).toHaveLength(1);
  await user.tab();
  expect(cells()[0]).toHaveFocus();
  await user.keyboard('{ArrowRight}');
  expect(cells()[1]).toHaveFocus();
  await user.keyboard('{ArrowDown}');
  expect(cells()[5]).toHaveFocus();
  await user.keyboard('{End}');
  expect(cells()[7]).toHaveFocus();
  await user.keyboard('{Home}');
  expect(cells()[4]).toHaveFocus();
  await user.keyboard('{Control>}{End}{/Control}');
  expect(cells()[cells().length - 1]).toHaveFocus();
  await user.keyboard('{Control>}{Home}{/Control}');
  expect(cells()[0]).toHaveFocus();
  expect(cells().filter(cell => cell.tabIndex === 0)).toHaveLength(1);
  await user.keyboard('{ArrowLeft}{ArrowUp}');
  expect(cells()[0]).toHaveFocus();
});
it('leaves the grid with Escape', async () => {
  const user = userEvent.setup();
  view();
  await user.tab();
  await user.keyboard('{Escape}');
  expect(screen.getByRole('region', {name: 'Sales by region (scrollable)'})).toHaveFocus();
});
it('opens the rows behind a number with a click, Enter or Space, and for the total', async () => {
  const user = userEvent.setup(), drill = vi.fn();
  view(drill);
  await user.click(screen.getByRole('gridcell', {name: '250'}));
  expect(drill).toHaveBeenLastCalledWith(0, 2);
  await user.keyboard('{Enter}');
  expect(drill).toHaveBeenLastCalledWith(0, 2);
  await user.keyboard('{ArrowRight}{ArrowRight} ');
  expect(drill).toHaveBeenLastCalledWith(0, 4);
  expect(drill).toHaveBeenCalledTimes(3);
});
it('does not offer drill-down when read-only', async () => {
  const user = userEvent.setup();
  view();
  await user.click(screen.getByRole('gridcell', {name: '250'}));
  fireEvent.keyDown(screen.getByRole('gridcell', {name: '250'}), {key: 'Enter'});
  expect(screen.getByRole('gridcell', {name: '250'})).not.toHaveAttribute('data-drill');
});
it('marks the empty column and the total, and has no axe violations', async () => {
  const withEmpty = columns.map((column, at) => at === 3 ? {...column, label: '(empty)', column_empty: true, column_value: null} : column);
  const {container} = render(<PivotTable columns={withEmpty} rows={rows} label="Sales" onDrill={() => undefined}/>);
  expect(screen.getByRole('columnheader', {name: '(empty)'})).toHaveAttribute('data-empty');
  await expectNoAxeViolations(container);
});
