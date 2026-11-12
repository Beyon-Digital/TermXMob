import {describe, expect, it} from 'vitest';
import {addFilterNode, addJoin, countFilters, describeFilter, describeSummary, emptySpec, humanize, humanOp, nextSort, operatorsFor, removeFilterNode, removeJoin, replaceFilterNode, setGroupMode, summaryFields, uniqueAlias} from './query-builder';
import type {FilterLeaf, QuerySpec} from './query-spec';

const ctx = {label: (ref: {column: string}) => humanize(ref.column), kind: (ref: {column: string}) => ref.column === 'ordered_at' ? 'datetime' as const : ref.column === 'amount' ? 'number' as const : ref.column === 'paid' ? 'boolean' as const : 'text' as const};
const leaf = (column: string, op: FilterLeaf['op'], rest: Partial<FilterLeaf> = {}): FilterLeaf => ({field: {table_alias: 'orders', column}, op, ...rest});

describe('aliases', () => {
  it('lowercases, replaces symbols, suffixes collisions and caps at 32 characters', () => {
    expect(uniqueAlias('Order Items', [])).toBe('order_items');
    expect(uniqueAlias('orders', ['orders'])).toBe('orders_2');
    expect(uniqueAlias('orders', ['orders', 'orders_2'])).toBe('orders_3');
    expect(uniqueAlias('2024 sales', [])).toBe('t_2024_sales');
    expect(uniqueAlias('x'.repeat(40), [])).toHaveLength(32);
    expect(uniqueAlias('x'.repeat(40), ['x'.repeat(32)])).toHaveLength(32);
    expect(uniqueAlias('!!!', [])).toBe('t');
  });
});

describe('sentences', () => {
  it('reads operators as words, with date wording for date columns', () => {
    expect(humanOp('number', 'gt')).toBe('is greater than');
    expect(humanOp('datetime', 'gt')).toBe('is after');
    expect(humanOp('date', 'lte')).toBe('is on or before');
    expect(humanOp('text', 'in')).toBe('is any of');
  });
  it('describes filters in plain language', () => {
    expect(describeFilter(leaf('amount', 'gt', {value: 100}), ctx)).toBe('Amount is greater than 100');
    expect(describeFilter(leaf('ordered_at', 'between', {values: ['2026-01-01', '2026-03-31']}), ctx)).toBe('Ordered at is between 2026-01-01 and 2026-03-31');
    expect(describeFilter(leaf('status', 'in', {values: ['open', 'paid']}), ctx)).toBe('Status is any of open, paid');
    expect(describeFilter(leaf('note', 'contains', {value: 'late'}), ctx)).toBe("Note contains 'late'");
    expect(describeFilter(leaf('status', 'is_null'), ctx)).toBe('Status is empty');
    expect(describeFilter(leaf('paid', 'eq', {value: true}), ctx)).toBe('Paid is yes');
  });
  it('describes and builds summaries', () => {
    const summary = {measure: {fn: 'sum' as const, column: {table_alias: 'orders', column: 'amount'}}, by: {table_alias: 'orders', column: 'ordered_at'}, bucket: 'month' as const};
    expect(describeSummary(summary, ctx.label)).toBe('Total of amount by ordered at month');
    const fields = summaryFields(summary, ctx.label);
    expect(fields.aggregates[0]).toMatchObject({fn: 'sum', column: 'amount', label: 'Total of amount'});
    expect(fields.group_by[0]).toMatchObject({bucket: 'month', label: 'Ordered at by month'});
    expect(fields.outputs).toEqual(['Ordered at by month', 'Total of amount']);
    expect(describeSummary({measure: {fn: 'count'}}, ctx.label)).toBe('Number of rows');
  });
  it('offers only operators that fit the column and the engine', () => {
    expect(operatorsFor('boolean')).toEqual(['eq', 'is_null', 'not_null']);
    expect(operatorsFor('text', ['eq', 'contains'])).toEqual(['eq', 'contains']);
    expect(operatorsFor('json')).toEqual(['is_null', 'not_null']);
  });
});

describe('filter tree', () => {
  const root = emptySpec('c', 'orders').filters;
  it('adds, replaces, removes and switches groups at a path', () => {
    let tree = addFilterNode(root, [], leaf('amount', 'gt', {value: 1}));
    tree = addFilterNode(tree, [], {op: 'or', items: []});
    tree = addFilterNode(tree, [1], leaf('status', 'eq', {value: 'open'}));
    expect(countFilters(tree)).toBe(2);
    tree = setGroupMode(tree, [1], 'and');
    expect((tree.items[1] as {op: string}).op).toBe('and');
    tree = replaceFilterNode(tree, [], 0, leaf('amount', 'lt', {value: 5}));
    expect((tree.items[0] as FilterLeaf).op).toBe('lt');
    tree = removeFilterNode(tree, [1], 0);
    expect(countFilters(tree)).toBe(1);
  });
});

describe('joins and sort', () => {
  const base = emptySpec('c', 'orders');
  const join = (alias: string, on = 'orders') => ({kind: 'left' as const, table: alias, alias, on: [{left: {table_alias: on, column: 'x'}, right: {table_alias: alias, column: 'id'}}]});
  it('caps joins at four', () => {
    let spec: QuerySpec = base;
    for (const name of ['a', 'b', 'c', 'd', 'e']) spec = addJoin(spec, join(name));
    expect(spec.joins).toHaveLength(4);
  });
  it('removing a join removes what depends on it, including later joins', () => {
    let spec = addJoin(addJoin(base, join('customers')), join('regions', 'customers'));
    spec = {...spec, select: [{table_alias: 'orders', column: 'id'}, {table_alias: 'customers', column: 'name'}], sort: [{table_alias: 'customers', column: 'name', direction: 'asc'}], offset: 200,
      filters: {op: 'and', items: [leaf('amount', 'gt', {value: 1}), {...leaf('name', 'eq', {value: 'x'}), field: {table_alias: 'regions', column: 'name'}}]}};
    const next = removeJoin(spec, 'customers');
    expect(next.joins).toEqual([]);
    expect(next.select).toEqual([{table_alias: 'orders', column: 'id'}]);
    expect(next.sort).toEqual([]);
    expect(countFilters(next.filters)).toBe(1);
    expect(next.offset).toBe(0);
  });
  it('cycles sort ascending, descending, none and supports additive keys', () => {
    const col = {table_alias: 'orders', column: 'amount'};
    const one = nextSort([], col, false);
    expect(one).toEqual([{...col, direction: 'asc'}]);
    const two = nextSort(one, col, false);
    expect(two).toEqual([{...col, direction: 'desc'}]);
    expect(nextSort(two, col, false)).toEqual([]);
    const other = {table_alias: 'orders', column: 'status'};
    expect(nextSort(one, other, true)).toHaveLength(2);
    expect(nextSort(one, other, false)).toEqual([{...other, direction: 'asc'}]);
    expect(nextSort([], {output: 'Total'}, false)).toEqual([{output: 'Total', direction: 'asc'}]);
  });
});
