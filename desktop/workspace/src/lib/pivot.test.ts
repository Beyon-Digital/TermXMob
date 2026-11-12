import {expect, it} from 'vitest';
import {bucketRange, cellCoordinates, cellTitle, coarserBucket, columnDimIssue, drillSpec, emptyPivot, formatPivotNumber, pivotLayout, pivotQuery, pivotSentence, rowLabel, valueColumnAllowed} from './pivot';
import {emptySpec, humanize} from './query-builder';
import drillCell from './view-contract-fixtures/drill-pivot-cell.json';
import drillTotal from './view-contract-fixtures/drill-total.json';
import compile from './view-contract-fixtures/compile-pivot.json';
import createPivot from './view-contract-fixtures/view-create-pivot.json';
import type {OutputColumn, QuerySpec} from './query-spec';

const label = (ref: {column: string}) => humanize(ref.column);
const pivotSpec = createPivot.request.spec as unknown as QuerySpec;
const columns = compile.response.columns as unknown as OutputColumn[];

it('reads a pivot as a sentence', () => {
  expect(pivotSentence(pivotSpec.pivot!, label)).toBe('Total of amount by region, across month of day');
  expect(pivotSentence({...pivotSpec.pivot!, columns: null, value: {fn: 'count'}, rows: [{table_alias: 's', column: 'region'}, {table_alias: 's', column: 'status'}]}, label)).toBe('Number of rows by region and status');
  expect(pivotSentence(emptyPivot(), label)).toBe('Number of rows');
});
it('only builds a query once a row is chosen', () => {
  const base = emptySpec('c1', 'sales');
  expect(pivotQuery(base, emptyPivot())).toBeNull();
  const spec = pivotQuery({...base, select: [{table_alias: 'sales', column: 'id'}]}, pivotSpec.pivot!);
  expect(spec?.pivot).toEqual(pivotSpec.pivot);
  expect(spec?.select).toEqual([]);
});
it('offers only columns that suit each measure', () => {
  expect(valueColumnAllowed('sum', 'integer')).toBe(true);
  expect(valueColumnAllowed('sum', 'text')).toBe(false);
  expect(valueColumnAllowed('avg', 'date')).toBe(false);
  expect(valueColumnAllowed('min', 'date')).toBe(true);
  expect(valueColumnAllowed('count_distinct', 'json')).toBe(false);
  expect(valueColumnAllowed('count', undefined)).toBe(true);
});
it('explains why a column cannot be used as columns', () => {
  expect(columnDimIssue('number')).toBe("Decimal numbers can't be used as columns");
  expect(columnDimIssue('json')).toMatch(/can't be used as columns/);
  expect(columnDimIssue('text')).toBeNull();
  expect(columnDimIssue('date')).toBeNull();
});
it('steps a bucket one size coarser', () => {
  expect(coarserBucket('day')).toBe('week');
  expect(coarserBucket('month')).toBe('quarter');
  expect(coarserBucket('year')).toBeNull();
  expect(coarserBucket(undefined)).toBeNull();
});
it('turns bucket labels into half-open date ranges', () => {
  expect(bucketRange('month', '2026-01')).toEqual(['2026-01-01', '2026-02-01']);
  expect(bucketRange('month', '2026-12')).toEqual(['2026-12-01', '2027-01-01']);
  expect(bucketRange('quarter', '2026-Q4')).toEqual(['2026-10-01', '2027-01-01']);
  expect(bucketRange('year', '2026')).toEqual(['2026-01-01', '2027-01-01']);
  expect(bucketRange('day', '2026-02-28')).toEqual(['2026-02-28', '2026-03-01']);
  expect(bucketRange('week', '2026-W01')).toEqual(['2025-12-29', '2026-01-05']);
  expect(bucketRange('week', '2020-W53')).toEqual(['2020-12-28', '2021-01-04']);
  expect(bucketRange('month', 'January')).toBeNull();
});
it('builds the same drill filters the server returns for a cell', () => {
  const local = drillSpec(pivotSpec, drillCell.request.coordinates)!;
  expect(local.filters.items).toEqual(drillCell.response.spec.filters.items);
  expect(local.pivot).toBeUndefined();
  expect(local.limit).toBe(200);
  expect(drillSpec(pivotSpec, drillTotal.request.coordinates)!.filters.items).toEqual(drillTotal.response.spec.filters.items);
});
it('drills empty values as "is empty" and keeps the original filters', () => {
  const filters = {op: 'and' as const, items: [{field: {table_alias: 'sales', column: 'amount'}, op: 'gt' as const, value: 5}]};
  const spec = drillSpec({...pivotSpec, filters}, {rows: [null], column: {value: null, empty: true}})!;
  expect(spec.filters.items).toEqual([filters.items[0], {field: {table_alias: 'sales', column: 'region'}, op: 'is_null'}, {field: {table_alias: 'sales', column: 'day'}, op: 'is_null'}]);
});
it('refuses coordinates that do not fit the pivot', () => {
  expect(drillSpec(pivotSpec, {rows: ['East', 'extra'], column: null})).toBeNull();
  expect(drillSpec({...pivotSpec, pivot: {...pivotSpec.pivot!, columns: null}}, {rows: ['East'], column: {value: 'x', empty: false}})).toBeNull();
  expect(drillSpec(emptySpec('c', 't'), {rows: []})).toBeNull();
});
it('finds the coordinates of a clicked cell from the output metadata', () => {
  const row = ['East', 10, 20, 30, 60];
  expect(cellCoordinates(columns, row, 2)).toEqual({rows: ['East'], column: {value: '2026-02', empty: false}});
  expect(cellCoordinates(columns, row, 4)).toEqual({rows: ['East'], column: null});
  expect(cellTitle(columns, row, 1)).toBe('East · 2026-01');
  expect(cellTitle(columns, [null, 1, 2, 3, 6], 4)).toBe('(empty) · Total amount');
  const empty = [...columns]; empty[1] = {...empty[1], column_empty: true, column_value: null, label: '(empty)'};
  expect(cellCoordinates(empty, row, 1)).toEqual({rows: ['East'], column: {value: null, empty: true}});
});
it('separates row labels, cells and the total', () => {
  const layout = pivotLayout(columns);
  expect([layout.rows.length, layout.cells.length, layout.total?.label]).toEqual([1, 3, 'Total amount']);
});
it('formats numbers and empty row labels', () => {
  expect(formatPivotNumber(1234)).toBe('1,234');
  expect(formatPivotNumber(12.3456)).toBe('12.35');
  expect(formatPivotNumber('1500')).toBe('1,500');
  expect(rowLabel(null)).toBe('(empty)');
  expect(rowLabel('')).toBe('(empty)');
  expect(rowLabel('East')).toBe('East');
});
