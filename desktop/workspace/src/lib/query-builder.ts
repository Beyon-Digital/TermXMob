/** Pure helpers for the visual query builder: aliases, plain-language sentences and filter-tree edits. No SQL words in anything shown to people. */
import type {Aggregate, AggregateFn, ColumnRef, DateBucket, FilterGroup, FilterLeaf, FilterNode, GroupBy, Join, LeafOp, QuerySpec, Scalar, SchemaColumn, SortKey, ValueKind} from './query-spec';

export const MAX_JOINS = 4;
export const DEFAULT_PAGE = 200;
export type KindGroup = 'text' | 'number' | 'date' | 'boolean' | 'other';
export type TableColumns = {alias: string; table: string; columns: SchemaColumn[]};

export function kindGroup(kind?: ValueKind): KindGroup {
  if (kind === 'integer' || kind === 'number') return 'number';
  if (kind === 'date' || kind === 'datetime') return 'date';
  if (kind === 'boolean') return 'boolean';
  if (kind === 'text') return 'text';
  return 'other';
}
/** Lowercase, non-alphanumeric to underscore, `_2` on collision, 32 characters at most. Never shown outside Show SQL. */
export function uniqueAlias(name: string, taken: readonly string[]): string {
  let base = name.toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '') || 't';
  if (/^[0-9]/.test(base)) base = 't_' + base;
  base = base.slice(0, 32);
  const used = new Set(taken.map(row => row.toLowerCase()));
  if (!used.has(base)) return base;
  for (let n = 2; ; n++) {
    const suffix = '_' + n, candidate = base.slice(0, 32 - suffix.length) + suffix;
    if (!used.has(candidate)) return candidate;
  }
}
export function emptySpec(source: string, table: string): QuerySpec {
  return {version: 1, source, from: {table, alias: uniqueAlias(table, [])}, joins: [], select: [], filters: {op: 'and', items: []}, group_by: [], aggregates: [], sort: [], limit: DEFAULT_PAGE, offset: 0};
}
export const specAliases = (spec: QuerySpec) => [spec.from.alias, ...spec.joins.map(join => join.alias)];
export const sameColumn = (a: ColumnRef, b: ColumnRef) => a.table_alias === b.table_alias && a.column === b.column;
export const refKey = (ref: ColumnRef) => ref.table_alias + '\u0000' + ref.column;

/** "ordered_at" becomes "Ordered at". Used in sentences only; source names are never altered. */
export function humanize(name: string): string {
  const text = name.replace(/[_\s]+/g, ' ').replace(/([a-z0-9])([A-Z])/g, '$1 $2').trim().toLowerCase();
  return text ? text[0].toUpperCase() + text.slice(1) : name;
}

const FILTER_OPS: Record<KindGroup, LeafOp[]> = {
  text: ['eq', 'ne', 'contains', 'starts_with', 'in', 'not_in', 'is_null', 'not_null'],
  number: ['eq', 'ne', 'gt', 'gte', 'lt', 'lte', 'between', 'in', 'not_in', 'is_null', 'not_null'],
  date: ['eq', 'gt', 'gte', 'lt', 'lte', 'between', 'is_null', 'not_null'],
  boolean: ['eq', 'is_null', 'not_null'],
  other: ['is_null', 'not_null'],
};
/** Operators that make sense for a column kind, limited to the ones the engine supports. */
export function operatorsFor(kind: ValueKind | undefined, allowed?: readonly LeafOp[]): LeafOp[] {
  const ops = FILTER_OPS[kindGroup(kind)];
  return allowed ? ops.filter(op => allowed.includes(op)) : ops;
}
const OP_TEXT: Record<LeafOp, string> = {
  eq: 'is', ne: 'is not', gt: 'is greater than', gte: 'is at least', lt: 'is less than', lte: 'is at most', between: 'is between',
  in: 'is any of', not_in: 'is none of', contains: 'contains', starts_with: 'starts with', is_null: 'is empty', not_null: 'is not empty',
};
const DATE_OP_TEXT: Partial<Record<LeafOp, string>> = {gt: 'is after', gte: 'is on or after', lt: 'is before', lte: 'is on or before', eq: 'is on'};
/** The operator as it reads in a sentence. */
export function humanOp(kind: ValueKind | undefined, op: LeafOp): string {
  return (kindGroup(kind) === 'date' ? DATE_OP_TEXT[op] : undefined) ?? OP_TEXT[op];
}
function shown(value: Scalar | undefined, kind: ValueKind | undefined) {
  if (typeof value === 'boolean') return value ? 'yes' : 'no';
  if (kindGroup(kind) === 'boolean') return value === 1 || value === 'true' || value === '1' ? 'yes' : 'no';
  return String(value ?? '');
}
export type FilterContext = {label: (ref: ColumnRef) => string; kind: (ref: ColumnRef) => ValueKind | undefined};
/** A plain-language sentence for one filter: "Amount is greater than 100". */
export function describeFilter(leaf: FilterLeaf, context: FilterContext): string {
  const kind = context.kind(leaf.field), name = context.label(leaf.field), op = humanOp(kind, leaf.op);
  switch (leaf.op) {
    case 'is_null': case 'not_null': return name + ' ' + op;
    case 'between': return name + ' ' + op + ' ' + shown(leaf.values?.[0], kind) + ' and ' + shown(leaf.values?.[1], kind);
    case 'in': case 'not_in': return name + ' ' + op + ' ' + (leaf.values || []).map(value => shown(value, kind)).join(', ');
    case 'contains': case 'starts_with': return name + ' ' + op + " '" + String(leaf.value ?? '') + "'";
    default: return name + ' ' + op + ' ' + shown(leaf.value, kind);
  }
}

export type Measure = {fn: AggregateFn; column?: ColumnRef};
export type Summary = {measure: Measure; by?: ColumnRef; bucket?: DateBucket};
export const MEASURES: {fn: AggregateFn; label: string; needsColumn: boolean; numeric?: boolean}[] = [
  {fn: 'count', label: 'Number of rows', needsColumn: false},
  {fn: 'count_distinct', label: 'Unique values of', needsColumn: true},
  {fn: 'sum', label: 'Total of', needsColumn: true, numeric: true},
  {fn: 'avg', label: 'Average of', needsColumn: true, numeric: true},
  {fn: 'min', label: 'Smallest', needsColumn: true},
  {fn: 'max', label: 'Largest', needsColumn: true},
];
export const BUCKETS: {value: DateBucket; label: string}[] = [
  {value: 'day', label: 'Day'}, {value: 'week', label: 'Week'}, {value: 'month', label: 'Month'}, {value: 'quarter', label: 'Quarter'}, {value: 'year', label: 'Year'},
];
/** Plain column names read in lower case inside a sentence; "Table · column" labels keep their capitals. */
const inSentence = (text: string) => text.includes(' · ') ? text : text.toLowerCase();
const measureText = (summary: Summary, label: (ref: ColumnRef) => string) => {
  const measure = MEASURES.find(row => row.fn === summary.measure.fn)!;
  return measure.needsColumn && summary.measure.column ? measure.label + ' ' + inSentence(label(summary.measure.column)) : measure.label;
};
/** "Total of amount by month". */
export function describeSummary(summary: Summary, label: (ref: ColumnRef) => string): string {
  const head = measureText(summary, label);
  if (!summary.by) return head;
  return head + ' by ' + inSentence(label(summary.by)) + (summary.bucket ? ' ' + summary.bucket : '');
}
/** Spec fields for a summary. Output labels double as sort keys, so they are built once here. */
export function summaryFields(summary: Summary, label: (ref: ColumnRef) => string): {group_by: GroupBy[]; aggregates: Aggregate[]; outputs: string[]} {
  const aggregate: Aggregate = {fn: summary.measure.fn, label: measureText(summary, label).slice(0, 60)};
  if (summary.measure.column) {aggregate.table_alias = summary.measure.column.table_alias; aggregate.column = summary.measure.column.column;}
  const groups: GroupBy[] = summary.by ? [{...summary.by, ...(summary.bucket ? {bucket: summary.bucket} : {}), label: (summary.bucket ? label(summary.by) + ' by ' + summary.bucket : label(summary.by)).slice(0, 60)}] : [];
  return {group_by: groups, aggregates: [aggregate], outputs: [...groups.map(row => row.label!), aggregate.label!]};
}

// Filter tree. The interface offers one level of nesting; a path is a list of group indexes from the root.
export type GroupPath = number[];
export const isFilterGroup = (node: FilterNode): node is FilterGroup => node.op === 'and' || node.op === 'or';
function editGroup(group: FilterGroup, path: GroupPath, change: (group: FilterGroup) => FilterGroup): FilterGroup {
  if (!path.length) return change(group);
  const [head, ...rest] = path, target = group.items[head];
  if (!target || !isFilterGroup(target)) return group;
  return {...group, items: group.items.map((item, index) => index === head ? editGroup(target, rest, change) : item)};
}
export const addFilterNode = (root: FilterGroup, path: GroupPath, node: FilterNode) => editGroup(root, path, group => ({...group, items: [...group.items, node]}));
export const replaceFilterNode = (root: FilterGroup, path: GroupPath, index: number, node: FilterNode) => editGroup(root, path, group => ({...group, items: group.items.map((item, at) => at === index ? node : item)}));
export const removeFilterNode = (root: FilterGroup, path: GroupPath, index: number) => editGroup(root, path, group => ({...group, items: group.items.filter((_, at) => at !== index)}));
export const setGroupMode = (root: FilterGroup, path: GroupPath, op: 'and' | 'or') => editGroup(root, path, group => ({...group, op}));
export function countFilters(group: FilterGroup): number {
  return group.items.reduce((total, item) => total + (isFilterGroup(item) ? countFilters(item) : 1), 0);
}
function pruneFilters(group: FilterGroup, drop: (ref: ColumnRef) => boolean): FilterGroup {
  const items: FilterNode[] = [];
  for (const item of group.items) {
    if (isFilterGroup(item)) {const next = pruneFilters(item, drop); if (next.items.length) items.push(next);}
    else if (!drop(item.field)) items.push(item);
  }
  return {...group, items};
}

/** Remove a joined table and everything that depends on it: later joins pointing at it, columns, filters and sorts. */
export function removeJoin(spec: QuerySpec, alias: string): QuerySpec {
  const gone = new Set<string>([alias]);
  for (let changed = true; changed;) {
    changed = false;
    for (const join of spec.joins) {
      if (!gone.has(join.alias) && join.on.some(pair => gone.has(pair.left.table_alias) || gone.has(pair.right.table_alias))) {gone.add(join.alias); changed = true;}
    }
  }
  const drop = (ref: {table_alias?: string}) => !!ref.table_alias && gone.has(ref.table_alias);
  return {
    ...spec, joins: spec.joins.filter(join => !gone.has(join.alias)), select: spec.select.filter(row => !drop(row)), filters: pruneFilters(spec.filters, drop),
    group_by: spec.group_by.filter(row => !drop(row)), aggregates: spec.aggregates.filter(row => !drop(row)), sort: spec.sort.filter(key => !('table_alias' in key) || !drop(key)), offset: 0,
  };
}
export function addJoin(spec: QuerySpec, join: Join): QuerySpec {
  return spec.joins.length >= MAX_JOINS ? spec : {...spec, joins: [...spec.joins, join], offset: 0};
}

/** Sort cycles ascending, descending, none. Additive (shift-click) keeps other keys, up to four. */
export function nextSort(sort: SortKey[], column: ColumnRef | {output: string}, additive: boolean): SortKey[] {
  const matches = (key: SortKey) => 'output' in column ? 'output' in key && key.output === column.output : 'table_alias' in key && 'table_alias' in column && sameColumn(key, column);
  const current = sort.find(matches), rest = sort.filter(key => !matches(key));
  const direction = !current ? 'asc' : current.direction === 'asc' ? 'desc' : null;
  const base = additive ? rest : [];
  if (!direction) return base;
  return [...base, {...column, direction} as SortKey].slice(-4);
}
