/** Pure helpers for segments: which ones fit a table, merging the joins a segment needs, and plain-language definitions. */
import {describeFilter, humanize, isFilterGroup, uniqueAlias, type FilterContext} from './query-builder';
import {isFilterLeaf, isSegmentFilter, type ColumnRef, type FilterGroup, type FilterNode, type FilterSegment, type Join, type QuerySpec} from './query-spec';
import type {ViewSummary} from './data-views';

/** A segment fits a table when it is built on the same base table and source. */
export const segmentCompatible = (view: Pick<ViewSummary, 'kind' | 'base_table' | 'connection_id'>, baseTable: string, connectionId?: string) =>
  view.kind === 'segment' && view.base_table === baseTable && (!connectionId || view.connection_id === connectionId);

const sameOn = (a: Join, b: Join, map: Map<string, string>) => a.on.length === b.on.length && a.on.every(pair => b.on.some(other => {
  const left = map.get(pair.left.table_alias) ?? pair.left.table_alias, right = map.get(pair.right.table_alias) ?? pair.right.table_alias;
  return left === other.left.table_alias && right === other.right.table_alias && pair.left.column === other.left.column && pair.right.column === other.right.column;
}));
/**
 * Add the joins a segment needs that the table does not have yet. Joins are matched by kind, table and the columns they match on, never by alias.
 * The server only matches; it never adds. `added` lists the table names, for the status message.
 */
export function mergeSegmentJoins(spec: QuerySpec, segment: QuerySpec): {spec: QuerySpec; added: string[]} {
  const map = new Map<string, string>([[segment.from.alias, spec.from.alias]]), joins = [...spec.joins], added: string[] = [];
  for (const join of segment.joins) {
    const found = joins.find(row => row.kind === join.kind && row.table === join.table && sameOn(join, row, map));
    if (found) {map.set(join.alias, found.alias); continue;}
    const taken = [spec.from.alias, ...joins.map(row => row.alias)], alias = taken.includes(join.alias) ? uniqueAlias(join.table, taken) : join.alias;
    map.set(join.alias, alias);
    joins.push({...join, alias, on: join.on.map(pair => ({left: {...pair.left, table_alias: map.get(pair.left.table_alias) ?? pair.left.table_alias}, right: {...pair.right, table_alias: map.get(pair.right.table_alias) ?? pair.right.table_alias}}))});
    added.push(join.table);
  }
  return {spec: added.length ? {...spec, joins} : spec, added};
}

export const segmentNode = (id: string, negate = false): FilterSegment => negate ? {segment: id, negate: true} : {segment: id};
/** Segment ids a filter tree refers to, in order, without repeats. */
export function segmentIds(group: FilterGroup): string[] {
  const found: string[] = [];
  const walk = (node: FilterNode) => {
    if (isSegmentFilter(node)) {if (!found.includes(node.segment)) found.push(node.segment);}
    else if (isFilterGroup(node)) node.items.forEach(walk);
  };
  walk(group);
  return found;
}
/** "In segment Big spenders" / "Not in segment Big spenders". A segment that no longer exists reads "Segment removed". */
export function describeSegmentNode(node: FilterSegment, names: (id: string) => string | undefined): string {
  const name = names(node.segment);
  return name === undefined ? 'Segment removed' : (node.negate ? 'Not in segment ' : 'In segment ') + name;
}

export type DefinitionLine = {text: string; depth: number; segmentId?: string};
/** The definition of a segment as plain sentences, one per condition, with group headings. */
export function describeSegment(filters: FilterGroup, context: FilterContext, names: (id: string) => string | undefined): DefinitionLine[] {
  const lines: DefinitionLine[] = [];
  const walk = (group: FilterGroup, depth: number, top: boolean) => {
    if (group.items.length > 1 || !top) lines.push({text: group.op === 'or' ? 'Match any of these' : 'Match all of these', depth});
    for (const item of group.items) {
      if (isSegmentFilter(item)) lines.push({text: describeSegmentNode(item, names), depth: top && group.items.length <= 1 ? depth : depth + 1, segmentId: item.segment});
      else if (isFilterLeaf(item)) lines.push({text: describeFilter(item, context), depth: top && group.items.length <= 1 ? depth : depth + 1});
      else walk(item, depth + 1, false);
    }
  };
  walk(filters, 0, true);
  return lines;
}
/** Context for sentences when no schema is at hand (history rows): names come from the column, the kind is unknown. */
export const plainContext: FilterContext = {label: (ref: ColumnRef) => humanize(ref.column), kind: () => undefined};
