import {describeFilter, isFilterGroup} from './query-builder';
import {describeSegmentNode, plainContext} from './segments';
import {isFilterLeaf, isSegmentFilter, type AggregateFn, type FilterGroup, type FilterNode, type PivotSpec, type QuerySpec} from './query-spec';

export type ProposalKind = 'table' | 'pivot' | 'segment';
export type SpecProposal = {type: ProposalKind; title: string; definition: {kind: ProposalKind; name: string; description: string; connection_id: string; source_name?: string; spec: QuerySpec}};

const isObject = (value: unknown): value is Record<string, unknown> => !!value && typeof value === 'object' && !Array.isArray(value);

/** A proposal from the server already passed the same checks as a save. This only guards the shape the card draws. */
export function isSpecProposal(value: unknown): value is SpecProposal {
  if (!isObject(value) || (value.type !== 'table' && value.type !== 'pivot' && value.type !== 'segment') || typeof value.title !== 'string') return false;
  const definition = value.definition;
  if (!isObject(definition) || typeof definition.connection_id !== 'string' || !isObject(definition.spec)) return false;
  const spec = definition.spec;
  return spec.version === 1 && typeof spec.source === 'string' && isObject(spec.from) && typeof spec.from.table === 'string'
    && isObject(spec.filters) && Array.isArray(spec.filters.items) && Array.isArray(spec.select)
    && (value.type !== 'pivot' || isObject(spec.pivot));
}

export type Chip = {id: string; group: string; text: string; removable: boolean};

const FN_WORDS: Record<AggregateFn, string> = {count: 'Number of rows', count_distinct: 'Different values of', sum: 'Total of', avg: 'Average of', min: 'Smallest', max: 'Largest'};
const label = (column: string) => plainContext.label({table_alias: '', column});

function valueText(value: PivotSpec['value']) {
  return value.column ? FN_WORDS[value.fn] + ' ' + label(value.column).toLowerCase() : FN_WORDS[value.fn];
}

function filterChip(node: FilterNode, index: number): Chip {
  const id = 'filter:' + index;
  if (isSegmentFilter(node)) return {id, group: 'Filters', text: describeSegmentNode(node, () => undefined), removable: true};
  if (isFilterLeaf(node)) return {id, group: 'Filters', text: describeFilter(node, plainContext), removable: true};
  const group = node as FilterGroup;
  return {id, group: 'Filters', text: (group.op === 'or' ? 'Any of ' : 'All of ') + group.items.length + (group.items.length === 1 ? ' condition' : ' conditions'), removable: true};
}

/** The plain-language pieces of a proposal. Each removable chip can be dropped before the person applies it. */
export function proposalChips(kind: ProposalKind, spec: QuerySpec): Chip[] {
  const chips: Chip[] = [{id: 'table', group: 'Table', text: label(spec.from.table), removable: false}];
  if (kind === 'table') spec.select.forEach((column, index) => chips.push({id: 'col:' + index, group: 'Columns', text: column.label || label(column.column), removable: spec.select.length > 1}));
  if (kind === 'pivot' && spec.pivot) {
    spec.pivot.rows.forEach((dim, index) => chips.push({id: 'row:' + index, group: 'Rows', text: dim.label || label(dim.column) + (dim.bucket ? ' by ' + dim.bucket : ''), removable: false}));
    if (spec.pivot.columns) chips.push({id: 'pivot-columns', group: 'Columns', text: spec.pivot.columns.label || label(spec.pivot.columns.column) + (spec.pivot.columns.bucket ? ' by ' + spec.pivot.columns.bucket : ''), removable: spec.pivot.totals});
    chips.push({id: 'value', group: 'Shows', text: valueText(spec.pivot.value), removable: false});
  }
  // A segment must keep one condition to mean anything, so its last filter stays.
  spec.filters.items.forEach((node, index) => {const chip = filterChip(node, index); chips.push(kind === 'segment' && spec.filters.items.length === 1 ? {...chip, removable: false} : chip);});
  if (kind === 'table') chips.push({id: 'limit', group: 'Rows shown', text: 'First ' + spec.limit, removable: false});
  return chips;
}

export function removeChip(spec: QuerySpec, id: string): QuerySpec {
  const [kind, raw] = id.split(':'), index = Number(raw);
  if (kind === 'col') return {...spec, select: spec.select.filter((_, at) => at !== index)};
  if (kind === 'filter') return {...spec, filters: {...spec.filters, items: spec.filters.items.filter((_, at) => at !== index)}};
  if (id === 'pivot-columns' && spec.pivot) return {...spec, pivot: {...spec.pivot, columns: null}};
  return spec;
}

/** A segment is only filters, so the columns, summary and sorting of a proposal are dropped. */
export function asSegmentSpec(spec: QuerySpec): QuerySpec {
  const {pivot: _pivot, ...rest} = spec;
  return {...rest, select: [], group_by: [], aggregates: [], sort: [], limit: 200, offset: 0};
}

export const hasFilters = (spec: QuerySpec) => spec.filters.items.some(node => !isFilterGroup(node) || node.items.length > 0);
