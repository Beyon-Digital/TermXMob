/** Pure helpers for pivots: plain-language sentences, which columns suit which job, and drill-down filters. Nothing here shows SQL words. */
import {MEASURES, BUCKETS} from './query-builder';
import type {AggregateFn, ColumnRef, DateBucket, FilterGroup, FilterNode, OutputColumn, PivotDim, PivotSpec, PivotValue, QuerySpec, Scalar, ValueKind} from './query-spec';
import type {DrillCoordinates} from './data-views';

export const PIVOT_MAX_COLUMNS = 12;
export const PIVOT_ROW_LIMIT = 200;
const NUMBER_KINDS: ValueKind[] = ['integer', 'number'];
const ORDERABLE: ValueKind[] = ['integer', 'number', 'date', 'datetime', 'time', 'text', 'uuid', 'boolean'];
const DATE_KINDS: ValueKind[] = ['date', 'datetime'];
export const isDateKind = (kind?: ValueKind) => !!kind && DATE_KINDS.includes(kind);

/** A pivot with nothing chosen yet. The rows list may be empty while the person is still picking. */
export const emptyPivot = (): PivotSpec => ({rows: [], columns: null, value: {fn: 'count'}, totals: true, sort: {by: 'rows', direction: 'asc'}});
/** The spec to compile, or null while no row has been chosen. */
export function pivotQuery(base: QuerySpec, pivot: PivotSpec): QuerySpec | null {
  return pivot.rows.length ? {...base, select: [], group_by: [], aggregates: [], sort: [], pivot} : null;
}
export const bucketLabel = (bucket?: DateBucket) => BUCKETS.find(row => row.value === bucket)?.label.toLowerCase() || '';
/** Which buckets a coarser step could use: day, week, month, quarter, year. */
export function coarserBucket(bucket?: DateBucket): DateBucket | null {
  const order = BUCKETS.map(row => row.value), at = bucket ? order.indexOf(bucket) : -1;
  return at >= 0 && at < order.length - 1 ? order[Math.min(order.length - 1, at + 1)] : null;
}

/** Whether a column can hold the value of a measure. `fn` count needs no column. */
export function valueColumnAllowed(fn: AggregateFn, kind?: ValueKind): boolean {
  if (fn === 'count') return true;
  if (!kind) return false;
  if (fn === 'sum' || fn === 'avg') return NUMBER_KINDS.includes(kind);
  if (fn === 'min' || fn === 'max') return ORDERABLE.includes(kind);
  return kind !== 'json' && kind !== 'binary' && kind !== 'array';
}
/** Why a column cannot be used for the pivot's columns, or null when it can. */
export function columnDimIssue(kind?: ValueKind): string | null {
  if (kind === 'number') return "Decimal numbers can't be used as columns";
  if (kind === 'json' || kind === 'binary' || kind === 'array' || kind === 'time') return "This kind of column can't be used as columns";
  if (kind === 'other') return "This kind of column can't be used as columns";
  return null;
}
/** Dates must be grouped by a step; a text-like column or a whole number cannot. */
export const needsBucket = (kind?: ValueKind) => isDateKind(kind);

const inSentence = (text: string) => text.includes(' · ') ? text : text.toLowerCase();
const dimText = (dim: PivotDim, label: (ref: ColumnRef) => string) => dim.bucket ? bucketLabel(dim.bucket) + ' of ' + inSentence(label(dim)) : inSentence(label(dim));
/** "Total of amount by region, across month of day". */
export function pivotSentence(pivot: PivotSpec, label: (ref: ColumnRef) => string): string {
  const measure = MEASURES.find(row => row.fn === pivot.value.fn)!;
  const head = measure.needsColumn && pivot.value.table_alias && pivot.value.column ? measure.label + ' ' + inSentence(label({table_alias: pivot.value.table_alias, column: pivot.value.column})) : measure.label;
  if (!pivot.rows.length) return head;
  return head + ' by ' + pivot.rows.map(dim => dimText(dim, label)).join(' and ') + (pivot.columns ? ', across ' + dimText(pivot.columns, label) : '');
}
export const valueRef = (value: PivotValue): ColumnRef | undefined => value.table_alias && value.column ? {table_alias: value.table_alias, column: value.column} : undefined;

/** Result cells to show, in order: row labels, then cells, then the total. */
export type PivotLayout = {rows: OutputColumn[]; cells: OutputColumn[]; total?: OutputColumn};
export function pivotLayout(columns: OutputColumn[]): PivotLayout {
  return {rows: columns.filter(column => column.role === 'pivot_row'), cells: columns.filter(column => column.role === 'pivot_cell'), total: columns.find(column => column.role === 'pivot_total')};
}
/** Coordinates for a clicked cell. `columnIndex` is the position in the output; the total column drills the whole row. */
export function cellCoordinates(columns: OutputColumn[], row: unknown[], columnIndex: number): DrillCoordinates {
  const target = columns[columnIndex];
  const rows = columns.flatMap((column, at) => column.role === 'pivot_row' ? [(row[at] ?? null) as Scalar | null] : []);
  if (target?.role === 'pivot_cell') return {rows, column: {value: target.column_empty ? null : (target.column_value ?? null), empty: !!target.column_empty}};
  return {rows, column: null};
}
/** A readable name for a drill tab: "East · 2026-01". */
export function cellTitle(columns: OutputColumn[], row: unknown[], columnIndex: number): string {
  const rows = columns.flatMap((column, at) => column.role === 'pivot_row' ? [String(row[at] ?? '(empty)')] : []);
  const target = columns[columnIndex];
  return [...rows, ...(target?.role === 'pivot_cell' ? [target.label] : [])].join(' · ');
}

// Drill-down: the same rule the server applies, used for pivots that are not saved yet.
const pad = (value: number, size = 2) => String(value).padStart(size, '0');
const iso = (date: Date) => date.getUTCFullYear() + '-' + pad(date.getUTCMonth() + 1) + '-' + pad(date.getUTCDate());
const utc = (year: number, month: number, day: number) => new Date(Date.UTC(year, month, day));
/** Half-open range [start, end) for a bucket label such as 2026-01, 2026-Q1, 2026-W05. Null when the label is not a bucket. */
export function bucketRange(bucket: DateBucket, label: string): [string, string] | null {
  let match;
  if (bucket === 'day' && /^\d{4}-\d{2}-\d{2}$/.test(label)) {
    const [y, m, d] = label.split('-').map(Number);
    return [label, iso(utc(y, m - 1, d + 1))];
  }
  if (bucket === 'month' && (match = /^(\d{4})-(\d{2})$/.exec(label))) return [iso(utc(+match[1], +match[2] - 1, 1)), iso(utc(+match[1], +match[2], 1))];
  if (bucket === 'quarter' && (match = /^(\d{4})-Q([1-4])$/.exec(label))) return [iso(utc(+match[1], (+match[2] - 1) * 3, 1)), iso(utc(+match[1], +match[2] * 3, 1))];
  if (bucket === 'year' && (match = /^(\d{4})$/.exec(label))) return [iso(utc(+match[1], 0, 1)), iso(utc(+match[1] + 1, 0, 1))];
  if (bucket === 'week' && (match = /^(\d{4})-W(\d{2})$/.exec(label))) {
    const january4 = utc(+match[1], 0, 4), monday = new Date(january4.getTime() - ((january4.getUTCDay() + 6) % 7) * 86400000 + (+match[2] - 1) * 7 * 86400000);
    return [iso(monday), iso(new Date(monday.getTime() + 7 * 86400000))];
  }
  return null;
}
function condition(dim: PivotDim, value: Scalar | null, empty: boolean): FilterNode[] {
  const field = {table_alias: dim.table_alias, column: dim.column};
  if (empty || value === null) return [{field, op: 'is_null'}];
  if (dim.bucket) {
    const range = bucketRange(dim.bucket, String(value));
    if (!range) return [{field, op: 'eq', value}];
    return dim.bucket === 'day' ? [{field, op: 'eq', value: range[0]}] : [{field, op: 'gte', value: range[0]}, {field, op: 'lt', value: range[1]}];
  }
  return [{field, op: 'eq', value}];
}
/** The rows behind one pivot cell: the pivot's own filters plus one condition per coordinate. */
export function drillSpec(spec: QuerySpec, coordinates: DrillCoordinates): QuerySpec | null {
  const pivot = spec.pivot;
  if (!pivot) return null;
  const items: FilterNode[] = [];
  const rows = coordinates.rows || [];
  if (rows.length !== pivot.rows.length) return null;
  pivot.rows.forEach((dim, index) => items.push(...condition(dim, rows[index], rows[index] === null)));
  if (coordinates.column) {
    if (!pivot.columns) return null;
    items.push(...condition(pivot.columns, coordinates.column.value, coordinates.column.empty));
  }
  const own = spec.filters.items.length ? (spec.filters.op === 'and' ? spec.filters.items : [spec.filters]) : [];
  const filters: FilterGroup = {op: 'and', items: [...own, ...items]};
  const {pivot: _pivot, ...rest} = spec;
  return {...rest, select: [], group_by: [], aggregates: [], sort: [], filters, limit: PIVOT_ROW_LIMIT, offset: 0};
}

/** The text in a grid cell. Whole numbers keep their digits, decimals show at most two. */
export function formatPivotNumber(value: unknown): string {
  if (typeof value === 'number') return Number.isInteger(value) ? value.toLocaleString() : value.toLocaleString(undefined, {maximumFractionDigits: 2});
  if (typeof value === 'string' && value !== '' && Number.isFinite(Number(value))) return formatPivotNumber(Number(value));
  return String(value);
}
export const EMPTY_LABEL = '(empty)';
export const rowLabel = (value: unknown) => value === null || value === undefined || value === '' ? EMPTY_LABEL : String(value);
export const dimensionLabel = (dim: PivotDim | undefined, label: (ref: ColumnRef) => string) => dim ? humanizeDim(dim, label) : '';
const humanizeDim = (dim: PivotDim, label: (ref: ColumnRef) => string) => label(dim) + (dim.bucket ? ' by ' + dim.bucket : '');
