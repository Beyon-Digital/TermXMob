import {afterEach, expect, it, vi} from 'vitest';
import {cleanup, render, screen, within} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import PivotBuilder from './PivotBuilder';
import {emptyPivot} from '../../lib/pivot';
import {expectNoAxeViolations} from '../../test-axe';
import type {ColumnOption} from './ColumnFilter';
import type {PivotSpec} from '../../lib/query-spec';

afterEach(cleanup);
const ref = (column: string) => ({table_alias: 'sales', column});
const options: ColumnOption[] = [
  {ref: ref('region'), label: 'Region', kind: 'text'}, {ref: ref('status'), label: 'Status', kind: 'text'}, {ref: ref('amount'), label: 'Amount', kind: 'number'},
  {ref: ref('quantity'), label: 'Quantity', kind: 'integer'}, {ref: ref('day'), label: 'Day', kind: 'date'}, {ref: ref('notes'), label: 'Notes', kind: 'json'},
];
const filled: PivotSpec = {...emptyPivot(), rows: [{table_alias: 'sales', column: 'region'}]};
function view(pivot: PivotSpec = filled, extra: Partial<React.ComponentProps<typeof PivotBuilder>> = {}) {
  const onChange = vi.fn();
  const result = render(<PivotBuilder pivot={pivot} onChange={onChange} options={options} {...extra}/>);
  return {onChange, ...result};
}
const SQL_WORDS = /\b(sql|join|where|case|group by|aggregate|query-spec)\b/i;

it('uses plain words only', () => {
  const {container} = view({...filled, columns: {table_alias: 'sales', column: 'day', bucket: 'month'}, value: {fn: 'sum', table_alias: 'sales', column: 'amount'}});
  expect(container.textContent).not.toMatch(SQL_WORDS);
  for (const name of ['Row', 'Column', 'Columns by', 'Show', 'Sort rows by']) expect(screen.getAllByLabelText(name).length).toBeGreaterThan(0);
});
it('lists only suitable columns for the chosen measure', async () => {
  const user = userEvent.setup(), {onChange} = view();
  await user.selectOptions(screen.getByLabelText('Show'), 'sum');
  const next = onChange.mock.calls[0][0] as PivotSpec;
  expect(next.value).toEqual({fn: 'sum', table_alias: 'sales', column: 'amount'});
});
it('only offers number columns for a total', () => {
  view({...filled, value: {fn: 'sum', table_alias: 'sales', column: 'amount'}});
  const column = within(screen.getAllByLabelText('Column').find(select => select.id.includes('valuecolumn'))!);
  expect(column.getAllByRole('option').map(option => option.textContent)).toEqual(['Choose a column', 'Amount', 'Quantity']);
});
it('disables columns that cannot be used as columns, with the reason in the option', () => {
  view();
  const select = screen.getAllByLabelText('Column').find(item => item.id.endsWith('-column'))!;
  expect(within(select).getByRole('option', {name: "Amount (Decimal numbers can't be used as columns)"})).toBeDisabled();
  expect(within(select).getByRole('option', {name: /Notes/})).toBeDisabled();
  expect(within(select).getByRole('option', {name: 'Region (already used)'})).toBeDisabled();
  expect(within(select).getByRole('option', {name: 'Status'})).toBeEnabled();
});
it('asks for a step when a date is used for columns, defaulting to month', async () => {
  const user = userEvent.setup(), {onChange} = view();
  const select = screen.getAllByLabelText('Column').find(item => item.id.endsWith('-column'))!;
  await user.selectOptions(select, 'sales\u0000day');
  expect((onChange.mock.calls[0][0] as PivotSpec).columns).toEqual({table_alias: 'sales', column: 'day', bucket: 'month'});
});
it('shows the step for a date in the columns, with no "each date" choice, and marks steps the source lacks', () => {
  view({...filled, columns: {table_alias: 'sales', column: 'day', bucket: 'week'}}, {buckets: ['day', 'month']});
  const by = screen.getByLabelText('Columns by');
  expect(within(by).queryByRole('option', {name: 'Each date'})).toBeNull();
  expect(within(by).getByRole('option', {name: 'Week (not available for this source)'})).toBeDisabled();
});
it('lets a date row be grouped or left as each date', () => {
  view({...filled, rows: [{table_alias: 'sales', column: 'day', bucket: 'month'}]});
  expect(within(screen.getByLabelText('Row by')).getByRole('option', {name: 'Each date'})).toBeInTheDocument();
});
it('adds and removes a second row', async () => {
  const user = userEvent.setup(), {onChange} = view();
  await user.click(screen.getByRole('button', {name: 'Add another row'}));
  expect((onChange.mock.calls[0][0] as PivotSpec).rows).toHaveLength(2);
  cleanup();
  const second = view({...filled, rows: [...filled.rows, {table_alias: 'sales', column: 'status'}]});
  expect(screen.queryByRole('button', {name: 'Add another row'})).toBeNull();
  await user.click(screen.getByRole('button', {name: 'Remove second row'}));
  expect((second.onChange.mock.calls[0][0] as PivotSpec).rows).toHaveLength(1);
});
it('removes the columns again and keeps the totals on', async () => {
  const user = userEvent.setup(), {onChange} = view({...filled, totals: false, columns: {table_alias: 'sales', column: 'status'}});
  await user.selectOptions(screen.getAllByLabelText('Column').find(item => item.id.endsWith('-column'))!, '');
  expect(onChange.mock.calls[0][0]).toMatchObject({columns: null, totals: true});
});
it('keeps the total switched on without columns, with the reason shown', () => {
  view();
  const box = screen.getByRole('checkbox', {name: 'Show totals'});
  expect(box).toBeChecked();
  expect(box).toBeDisabled();
  expect(screen.getByText('Without columns, the total is the only number.')).toBeVisible();
});
it('changes the sort', async () => {
  const user = userEvent.setup(), {onChange} = view();
  await user.selectOptions(screen.getByLabelText('Sort rows by'), 'total:desc');
  expect((onChange.mock.calls[0][0] as PivotSpec).sort).toEqual({by: 'total', direction: 'desc'});
});
it('has no axe violations', async () => {
  const {container} = view({...filled, columns: {table_alias: 'sales', column: 'day', bucket: 'month'}});
  await expectNoAxeViolations(container);
});
