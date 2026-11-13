import {afterEach, beforeEach, expect, it, vi} from 'vitest';
import {cleanup, fireEvent, render, screen, within} from '@testing-library/react';
import QueryGrid, {type GridColumn} from './QueryGrid';
import {expectNoAxeViolations} from '../../test-axe';
import {sizeGridScrollers} from '../../test-utils/virtual';

const columns: GridColumn[] = [
  {key: 'id', label: 'id', kind: 'integer', sortable: true, sort: 'asc'},
  {key: 'status', label: 'status', kind: 'text', sortable: true},
  {key: 'amount', label: 'amount', kind: 'number', sortable: true},
  {key: 'paid', label: 'paid', kind: 'boolean'},
];
const rows = Array.from({length: 500}, (_, index) => [index + 1, index % 2 ? null : 'open', 10.5 * index, index % 2 === 0]);
beforeEach(() => sizeGridScrollers(800, 400));
afterEach(() => {cleanup(); vi.restoreAllMocks();});

it('renders only the visible rows of a large result as a table', () => {
  render(<QueryGrid columns={columns} rows={rows}/>);
  const table = screen.getByRole('table', {name: 'Result table'});
  expect(table).toHaveAttribute('aria-rowcount', '501');
  const rendered = screen.getAllByRole('row');
  expect(rendered.length).toBeGreaterThan(5);
  expect(rendered.length).toBeLessThan(40);
  expect(screen.getAllByRole('columnheader')).toHaveLength(4);
});
it('shows empty values as muted text, right-aligns numbers and formats booleans', () => {
  render(<QueryGrid columns={columns} rows={rows}/>);
  const second = screen.getAllByRole('row')[2];
  expect(within(second).getAllByRole('cell')[1]).toHaveTextContent('empty');
  expect(within(second).getAllByRole('cell')[1]).toHaveAttribute('data-empty', 'true');
  expect(within(second).getAllByRole('cell')[2]).toHaveAttribute('data-align', 'end');
  expect(within(second).getAllByRole('cell')[3]).toHaveTextContent('No');
  expect(screen.queryByText('NULL')).not.toBeInTheDocument();
});
it('exposes sort state on the header and reports clicks, with shift for additional keys', () => {
  const onSort = vi.fn();
  render(<QueryGrid columns={columns} rows={rows} onSort={onSort}/>);
  expect(screen.getByRole('columnheader', {name: /^id/})).toHaveAttribute('aria-sort', 'ascending');
  expect(screen.getByRole('columnheader', {name: /^status/})).toHaveAttribute('aria-sort', 'none');
  fireEvent.click(screen.getByRole('button', {name: 'status'}));
  expect(onSort).toHaveBeenLastCalledWith(1, false);
  fireEvent.click(screen.getByRole('button', {name: 'amount'}), {shiftKey: true});
  expect(onSort).toHaveBeenLastCalledWith(2, true);
});
it('keeps the final column layout while loading', () => {
  render(<QueryGrid columns={columns} rows={[]} loading/>);
  expect(screen.getAllByRole('columnheader')).toHaveLength(4);
  expect(screen.queryAllByRole('cell')).toHaveLength(0);
});
it('has a focusable scroll region and no axe violations', async () => {
  const {container} = render(<QueryGrid columns={columns} rows={rows} renderFilter={(index, column) => <button type="button" aria-label={'Filter ' + column.label + index}>f</button>}/>);
  const region = screen.getByRole('region', {name: 'Result table'});
  expect(region).toHaveAttribute('tabindex', '0');
  region.focus();
  expect(region).toHaveFocus();
  await expectNoAxeViolations(container);
});
