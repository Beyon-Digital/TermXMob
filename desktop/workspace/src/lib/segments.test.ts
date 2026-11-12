import {expect, it} from 'vitest';
import {describeSegment, describeSegmentNode, mergeSegmentJoins, segmentCompatible, segmentIds, segmentNode} from './segments';
import {describeChange} from './view-changes';
import {countFilters, emptySpec, humanize, isFilterGroup, removeFilterNode} from './query-builder';
import history from './view-contract-fixtures/history.json';
import type {Change} from './data-views';
import type {FilterGroup, Join, QuerySpec} from './query-spec';

const ctx = {label: (ref: {column: string}) => humanize(ref.column), kind: (ref: {column: string}) => ref.column === 'amount' ? 'number' as const : undefined};
const names = (id: string) => ({s1: 'Big spenders', s2: 'East'} as Record<string, string>)[id];
const regions = (alias: string): Join => ({kind: 'left', table: 'regions', alias, on: [{left: {table_alias: 'sales', column: 'region_id'}, right: {table_alias: alias, column: 'id'}}]});

it('matches segments by base table and source', () => {
  const row = {kind: 'segment' as const, base_table: 'sales', connection_id: 'c1'};
  expect(segmentCompatible(row, 'sales', 'c1')).toBe(true);
  expect(segmentCompatible(row, 'orders', 'c1')).toBe(false);
  expect(segmentCompatible(row, 'sales', 'c2')).toBe(false);
  expect(segmentCompatible({...row, kind: 'pivot'}, 'sales')).toBe(false);
});
it('adds only the joins a segment needs and says which', () => {
  const table = emptySpec('c1', 'sales'), segment = {...emptySpec('c1', 'sales'), joins: [regions('r')]};
  const merged = mergeSegmentJoins(table, segment);
  expect(merged.added).toEqual(['regions']);
  expect(merged.spec.joins).toHaveLength(1);
  expect(merged.spec.joins[0].on[0].right.table_alias).toBe('r');
});
it('recognises an equivalent join under another alias and adds nothing', () => {
  const table = {...emptySpec('c1', 'sales'), joins: [regions('regions')]}, segment = {...emptySpec('c1', 'sales'), joins: [regions('r')]};
  const merged = mergeSegmentJoins(table, segment);
  expect(merged.added).toEqual([]);
  expect(merged.spec).toBe(table);
});
it('gives a new join a free alias when the segment alias is taken by another table', () => {
  const taken: Join = {kind: 'inner', table: 'customers', alias: 'r', on: [{left: {table_alias: 'sales', column: 'customer_id'}, right: {table_alias: 'r', column: 'id'}}]};
  const merged = mergeSegmentJoins({...emptySpec('c1', 'sales'), joins: [taken]}, {...emptySpec('c1', 'sales'), joins: [regions('r')]});
  expect(merged.spec.joins.map(join => join.alias)).toEqual(['r', 'regions']);
  expect(merged.spec.joins[1].on[0].right.table_alias).toBe('regions');
});
it('remaps the base alias of a segment onto the table', () => {
  const table = {...emptySpec('c1', 'sales'), from: {table: 'sales', alias: 's'}}, segment = {...emptySpec('c1', 'sales'), joins: [regions('r')]};
  expect(mergeSegmentJoins(table, segment).spec.joins[0].on[0].left.table_alias).toBe('s');
});
it('collects the segments a filter tree uses once each, including nested groups', () => {
  const tree: FilterGroup = {op: 'and', items: [segmentNode('s1'), {op: 'or', items: [segmentNode('s2', true), segmentNode('s1')]}]};
  expect(segmentIds(tree)).toEqual(['s1', 's2']);
  expect(countFilters(tree)).toBe(3);
  expect(isFilterGroup(segmentNode('s1'))).toBe(false);
  expect(removeFilterNode(tree, [], 0).items).toHaveLength(1);
});
it('names segment filters, and a removed one plainly', () => {
  expect(describeSegmentNode(segmentNode('s1'), names)).toBe('In segment Big spenders');
  expect(describeSegmentNode(segmentNode('s1', true), names)).toBe('Not in segment Big spenders');
  expect(describeSegmentNode(segmentNode('gone'), names)).toBe('Segment removed');
});
it('writes a definition as plain lines with group headings', () => {
  const tree: FilterGroup = {op: 'and', items: [{field: {table_alias: 's', column: 'amount'}, op: 'gte', value: 5000}, segmentNode('s2')]};
  expect(describeSegment(tree, ctx, names)).toEqual([
    {text: 'Match all of these', depth: 0}, {text: 'Amount is at least 5000', depth: 1}, {text: 'In segment East', depth: 1, segmentId: 's2'}]);
  expect(describeSegment({op: 'and', items: [tree.items[0]]}, ctx, names)).toEqual([{text: 'Amount is at least 5000', depth: 0}]);
  expect(describeSegment({op: 'and', items: []}, ctx, names)).toEqual([]);
});

it('turns every recorded change into a sentence', () => {
  const sentences = (history.response.revisions[0].changes as Change[]).map(change => describeChange(change, names));
  expect(sentences).toEqual(['Added filter: Amount is greater than 200', 'Removed filter: Amount is greater than 100']);
  expect(describeChange({field: 'name', op: 'changed', before: 'Large orders', after: 'Big sales'})).toBe('Renamed from "Large orders" to "Big sales"');
  const check = (change: Change) => describeChange(change, names);
  expect(check({field: 'filters.match', op: 'changed', before: 'and', after: 'or'})).toBe('Now matches any of the filters');
  expect(check({field: 'filters', op: 'added', after: {segment: 's1', negate: true}})).toBe('Added filter: Not in segment Big spenders');
  expect(check({field: 'joins', op: 'added', after: regions('r')})).toBe('Combined Regions');
  expect(check({field: 'joins', op: 'removed', before: regions('r')})).toBe('Stopped combining Regions');
  expect(check({field: 'select', op: 'changed'})).toBe('Changed the columns shown');
  expect(check({field: 'group_by', op: 'added', after: [{table_alias: 's', column: 'ordered_at', bucket: 'month'}]})).toBe('Added grouping: Ordered at by month');
  expect(check({field: 'pivot.rows', op: 'changed'})).toBe('Changed the pivot rows');
  expect(check({field: 'pivot.totals', op: 'changed', after: false})).toBe('Turned the pivot totals off');
  expect(check({field: 'limit', op: 'changed', after: 500})).toBe('Changed the row limit to 500');
  expect(check({field: 'sort', op: 'changed'})).toBe('Changed the sort order');
  expect(check({field: 'description', op: 'changed'})).toBe('Changed the description');
});
