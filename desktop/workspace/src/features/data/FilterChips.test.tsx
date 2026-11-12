import {afterEach, expect, it, vi} from 'vitest';
import {cleanup, fireEvent, render, screen, within} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {useState} from 'react';
import FilterChips from './FilterChips';
import {ColumnFilter, buildLeaf, type ColumnOption} from './ColumnFilter';
import {describeFilter, emptySpec, humanize} from '../../lib/query-builder';
import type {FilterGroup} from '../../lib/query-spec';
import {expectNoAxeViolations} from '../../test-axe';

const ref = (column: string) => ({table_alias: 'orders', column});
const options: ColumnOption[] = [
  {ref: ref('status'), label: 'Status', kind: 'text'}, {ref: ref('amount'), label: 'Amount', kind: 'number'},
  {ref: ref('ordered_at'), label: 'Ordered at', kind: 'datetime'}, {ref: ref('paid'), label: 'Paid', kind: 'boolean'},
];
const context = {label: (r: {column: string}) => humanize(r.column), kind: (r: {column: string}) => options.find(o => o.ref.column === r.column)?.kind};
afterEach(cleanup);

it('builds filter values by column kind and explains bad input in plain words', () => {
  const inputs = {one: '', two: '', list: '', choice: 'yes'};
  expect(buildLeaf(ref('amount'), 'number', 'gt', {...inputs, one: '100'})).toEqual({field: ref('amount'), op: 'gt', value: 100});
  expect(buildLeaf(ref('amount'), 'number', 'gt', {...inputs, one: 'abc'})).toBe('Enter a number.');
  expect(buildLeaf(ref('amount'), 'number', 'between', {...inputs, one: '1', two: ''})).toBe('Enter two numbers.');
  expect(buildLeaf(ref('status'), 'text', 'in', {...inputs, list: 'open, paid ,'})).toEqual({field: ref('status'), op: 'in', values: ['open', 'paid']});
  expect(buildLeaf(ref('status'), 'text', 'in', {...inputs, list: ' , '})).toBe('Enter at least one value, separated by commas.');
  expect(buildLeaf(ref('status'), 'text', 'is_null', inputs)).toEqual({field: ref('status'), op: 'is_null'});
  expect(buildLeaf(ref('paid'), 'boolean', 'eq', {...inputs, choice: 'no'})).toEqual({field: ref('paid'), op: 'eq', value: false});
  expect(buildLeaf(ref('ordered_at'), 'datetime', 'between', {...inputs, one: '2026-01-01', two: '2026-03-31'})).toEqual({field: ref('ordered_at'), op: 'between', values: ['2026-01-01', '2026-03-31']});
  expect(buildLeaf(ref('status'), 'text', 'contains', {...inputs, one: ''})).toBe('Enter a value.');
});

it('opens a column filter, offers only fitting conditions and returns focus on Escape', async () => {
  const onApply = vi.fn(), user = userEvent.setup();
  render(<ColumnFilter column={options[1]} columns={options} active={false} onApply={onApply}/>);
  const trigger = screen.getByRole('button', {name: 'Filter Amount'});
  await user.click(trigger);
  const dialog = await screen.findByRole('dialog', {name: 'Filter Amount'});
  const conditions = within(dialog).getByLabelText('Condition');
  expect(within(conditions).getAllByRole('option').map(option => option.textContent)).toContain('is greater than');
  expect(within(conditions).queryByRole('option', {name: 'contains'})).not.toBeInTheDocument();
  await user.keyboard('{Escape}');
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(trigger).toHaveFocus();
  await user.click(trigger);
  const again = await screen.findByRole('dialog');
  await user.selectOptions(within(again).getByLabelText('Condition'), 'gt');
  await user.click(within(again).getByRole('button', {name: 'Apply'}));
  expect(within(again).getByRole('alert')).toHaveTextContent('Enter a number.');
  await user.type(within(again).getByLabelText('Value'), '100');
  await user.click(within(again).getByRole('button', {name: 'Apply'}));
  expect(onApply).toHaveBeenCalledWith({field: ref('amount'), op: 'gt', value: 100});
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
});

function Harness({initial}: {initial?: FilterGroup}) {
  const [filters, setFilters] = useState<FilterGroup>(initial || emptySpec('c', 'orders').filters);
  return <FilterChips filters={filters} context={context} options={options} onChange={setFilters}/>;
}
const open = {op: 'and' as const, items: [
  {field: ref('amount'), op: 'gt' as const, value: 100}, {field: ref('status'), op: 'in' as const, values: ['open', 'paid']},
]};

it('shows each filter as a sentence, lets Match any switch the group, and removes with the keyboard', async () => {
  const user = userEvent.setup();
  render(<Harness initial={open}/>);
  expect(screen.getByRole('button', {name: 'Amount is greater than 100'})).toBeInTheDocument();
  expect(screen.getByRole('button', {name: 'Status is any of open, paid'})).toBeInTheDocument();
  await user.selectOptions(screen.getByLabelText('Match mode'), 'or');
  expect(screen.getByLabelText('Match mode')).toHaveValue('or');
  screen.getByRole('button', {name: 'Amount is greater than 100'}).focus();
  await user.keyboard('{Delete}');
  expect(screen.queryByRole('button', {name: 'Amount is greater than 100'})).not.toBeInTheDocument();
  await user.click(screen.getByRole('button', {name: 'Remove filter: Status is any of open, paid'}));
  expect(screen.queryByRole('button', {name: /Status is any of/})).not.toBeInTheDocument();
});

it('edits a filter from its chip and adds a filter from "Add filter"', async () => {
  const user = userEvent.setup();
  render(<Harness initial={{op: 'and', items: [open.items[0]]}}/>);
  await user.click(screen.getByRole('button', {name: 'Amount is greater than 100'}));
  const edit = await screen.findByRole('dialog');
  const value = within(edit).getByLabelText('Value');
  expect(value).toHaveValue('100');
  await user.clear(value); await user.type(value, '250');
  await user.click(within(edit).getByRole('button', {name: 'Apply'}));
  expect(screen.getByRole('button', {name: 'Amount is greater than 250'})).toBeInTheDocument();
  await user.click(screen.getByRole('button', {name: 'Add filter'}));
  const add = await screen.findByRole('dialog');
  await user.selectOptions(within(add).getByLabelText('Column'), screen.getAllByRole('option', {name: 'Status'})[0]);
  await user.selectOptions(within(add).getByLabelText('Condition'), 'is_null');
  await user.click(within(add).getByRole('button', {name: 'Apply'}));
  expect(screen.getByRole('button', {name: 'Status is empty'})).toBeInTheDocument();
  expect(describeFilter({field: ref('status'), op: 'is_null'}, context)).toBe('Status is empty');
});

it('creates a nested group with its own match mode', async () => {
  const user = userEvent.setup();
  render(<Harness initial={{op: 'and', items: [open.items[0]]}}/>);
  await user.click(screen.getByRole('button', {name: 'Add group'}));
  const group = screen.getByRole('group', {name: 'Filter group 2'});
  expect(within(group).getByLabelText('Match mode for group 2')).toBeInTheDocument();
  await user.click(within(group).getByRole('button', {name: 'Add filter'}));
  const dialog = await screen.findByRole('dialog');
  await user.selectOptions(within(dialog).getByLabelText('Column'), screen.getAllByRole('option', {name: 'Paid'})[0]);
  await user.click(within(dialog).getByRole('button', {name: 'Apply'}));
  expect(within(group).getByRole('button', {name: 'Paid is yes'})).toBeInTheDocument();
});

it('has no axe violations with chips, a nested group and an open popover', async () => {
  const user = userEvent.setup();
  const {container} = render(<Harness initial={{op: 'and', items: [open.items[0], {op: 'or', items: [open.items[1]]}]}}/>);
  await expectNoAxeViolations(container);
  await user.click(screen.getByRole('button', {name: 'Amount is greater than 100'}));
  await expectNoAxeViolations(await screen.findByRole('dialog'));
  fireEvent.keyDown(document.activeElement || document.body, {key: 'Escape'});
});
