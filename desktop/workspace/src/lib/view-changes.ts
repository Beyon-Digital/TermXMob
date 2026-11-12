/** Plain-language sentences for the changes the server records in a view's history. The server never writes sentences. */
import {describeFilter, humanize, type FilterContext} from './query-builder';
import {plainContext, describeSegmentNode} from './segments';
import {bucketLabel} from './pivot';
import type {Change} from './data-views';
import type {FilterLeaf, FilterSegment, Join, DateBucket} from './query-spec';

const isObject = (value: unknown): value is Record<string, unknown> => !!value && typeof value === 'object';
const quote = (value: unknown) => '"' + String(value ?? '') + '"';
function filterText(value: unknown, context: FilterContext, names: (id: string) => string | undefined): string {
  if (isObject(value) && 'segment' in value) return describeSegmentNode(value as FilterSegment, names);
  if (isObject(value) && 'field' in value && 'op' in value) return describeFilter(value as FilterLeaf, context);
  return 'a filter';
}
const listText = (value: unknown, pick: (item: Record<string, unknown>) => string) => Array.isArray(value) ? value.filter(isObject).map(pick).join(', ') : isObject(value) ? pick(value) : '';
const columnName = (item: Record<string, unknown>) => humanize(String(item.column ?? item.output ?? item.fn ?? ''));
const dimName = (item: Record<string, unknown>) => columnName(item) + (item.bucket ? ' by ' + bucketLabel(item.bucket as DateBucket) : '');
const WHAT: Partial<Record<Change['field'], string>> = {
  select: 'the columns shown', group_by: 'how rows are grouped', aggregates: 'the summary', sort: 'the sort order', 'pivot.rows': 'the pivot rows', 'pivot.columns': 'the pivot columns',
  'pivot.value': 'what the pivot adds up', 'pivot.totals': 'the pivot totals', 'pivot.sort': 'the pivot order', description: 'the description', from: 'the table', limit: 'the number of rows shown',
};
/** One sentence for one change: "Added filter: Amount is at least 5,000". */
export function describeChange(change: Change, names: (id: string) => string | undefined = () => undefined, context: FilterContext = plainContext): string {
  const {field, op, before, after} = change;
  if (field === 'name') return 'Renamed from ' + quote(before) + ' to ' + quote(after);
  if (field === 'filters') return (op === 'added' ? 'Added filter: ' : op === 'removed' ? 'Removed filter: ' : 'Changed filter: ') + filterText(op === 'removed' ? before : after, context, names);
  if (field === 'filters.match') return after === 'or' ? 'Now matches any of the filters' : 'Now matches all of the filters';
  if (field === 'joins') {
    const join = (op === 'removed' ? before : after) as Join | undefined;
    return op === 'removed' ? 'Stopped combining ' + humanize(join?.table || 'a table') : 'Combined ' + humanize(join?.table || 'a table');
  }
  if (field === 'select') return op === 'added' ? 'Showed ' + listText(after, columnName) : op === 'removed' ? 'Hid ' + listText(before, columnName) : 'Changed the columns shown';
  if (field === 'group_by' || field === 'pivot.rows' || field === 'pivot.columns') {
    const what = field === 'group_by' ? 'grouping' : field === 'pivot.rows' ? 'pivot rows' : 'pivot columns';
    return op === 'added' ? 'Added ' + what + ': ' + listText(after, dimName) : op === 'removed' ? 'Removed ' + what + ': ' + listText(before, dimName) : 'Changed the ' + what;
  }
  if (field === 'limit') return 'Changed the row limit to ' + String(after);
  if (field === 'pivot.totals') return after === false ? 'Turned the pivot totals off' : 'Turned the pivot totals on';
  return 'Changed ' + (WHAT[field] || field);
}
