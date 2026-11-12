import {useId} from 'react';
import {Plus, X} from 'lucide-react';
import {Button} from '../../components/ui/button';
import {Select} from '../../components/ui/select';
import {BUCKETS, MEASURES, refKey} from '../../lib/query-builder';
import {columnDimIssue, isDateKind, valueColumnAllowed} from '../../lib/pivot';
import type {AggregateFn, ColumnRef, DateBucket, PivotDim, PivotSpec, ValueKind} from '../../lib/query-spec';
import type {ColumnOption} from './ColumnFilter';

type Props = {
  pivot: PivotSpec; onChange: (pivot: PivotSpec) => void; options: ColumnOption[];
  /** Buckets the source supports. Others stay visible, disabled, with the reason. */ buckets?: readonly DateBucket[];
  /** Kinds the source can use as pivot columns. */ columnKinds?: readonly ValueKind[];
  disabled?: boolean;
};
const parse = (value: string): ColumnRef | undefined => {const [table_alias, column] = value.split('\u0000'); return column === undefined ? undefined : {table_alias, column};};
const SORTS = [
  {value: 'rows:asc', label: 'Row names, A to Z'}, {value: 'rows:desc', label: 'Row names, Z to A'},
  {value: 'total:desc', label: 'Total, highest first'}, {value: 'total:asc', label: 'Total, lowest first'},
];

/** Rows, Columns and Value in plain language. Applies as the controls change, so the view keeps one primary action. */
export default function PivotBuilder({pivot, onChange, options, buckets, columnKinds, disabled = false}: Props) {
  const id = useId(), kindOf = (ref?: ColumnRef) => options.find(option => ref && refKey(option.ref) === refKey(ref))?.kind;
  const taken = (skip: 'row0' | 'row1' | 'column') => [...pivot.rows.map((dim, at) => 'row' + at === skip ? null : dim), skip === 'column' ? null : pivot.columns].filter((dim): dim is PivotDim => !!dim);
  const dim = (ref: ColumnRef, previous?: PivotDim): PivotDim => {
    const kind = kindOf(ref), same = previous && previous.table_alias === ref.table_alias && previous.column === ref.column;
    return {table_alias: ref.table_alias, column: ref.column, ...(isDateKind(kind) ? {bucket: same && previous.bucket ? previous.bucket : 'month'} : {})};
  };
  const setRow = (at: number, ref: ColumnRef | undefined) => onChange({...pivot, rows: ref ? pivot.rows.map((row, index) => index === at ? dim(ref, row) : row) : pivot.rows.filter((_, index) => index !== at)});
  const bucketOptions = (required: boolean) => <>
    {!required && <option value="">Each date</option>}
    {BUCKETS.map(row => {const off = !!buckets && !buckets.includes(row.value); return <option key={row.value} value={row.value} disabled={off}>{row.label}{off ? ' (not available for this source)' : ''}</option>;})}
  </>;
  const measure = MEASURES.find(row => row.fn === pivot.value.fn)!, valueOptions = options.filter(option => valueColumnAllowed(pivot.value.fn, option.kind));
  const withBucket = (current: PivotDim, bucket: string, required: boolean): PivotDim => {
    const {bucket: _drop, ...rest} = current;
    return bucket ? {...rest, bucket: bucket as DateBucket} : required ? {...rest, bucket: 'month'} : rest;
  };
  function setFn(fn: AggregateFn) {
    const next = MEASURES.find(row => row.fn === fn)!, pool = options.filter(option => valueColumnAllowed(fn, option.kind)), current = pivot.value;
    // A measure of an identifier is rarely what people want, so an id column is the last choice.
    const first = pool.find(option => !/(^|_)id$/i.test(option.ref.column)) ?? pool[0];
    const keep = current.table_alias && current.column && pool.some(option => refKey(option.ref) === refKey({table_alias: current.table_alias!, column: current.column!})) ? {table_alias: current.table_alias, column: current.column} : first && {table_alias: first.ref.table_alias, column: first.ref.column};
    onChange({...pivot, value: next.needsColumn ? {fn, ...keep} : {fn}});
  }
  const columnIssue = (option: ColumnOption) => {
    const issue = columnDimIssue(option.kind);
    if (issue) return issue;
    if (columnKinds && option.kind && !isDateKind(option.kind) && !columnKinds.includes(option.kind)) return "This source can't use this kind of column as columns";
    return null;
  };
  const usedElsewhere = (option: ColumnOption, skip: 'row0' | 'row1' | 'column') => taken(skip).some(row => row.table_alias === option.ref.table_alias && row.column === option.ref.column);
  const sortValue = pivot.sort.by + ':' + pivot.sort.direction, hasColumns = !!pivot.columns;
  const dimRow = (current: PivotDim | undefined, at: number, skip: 'row0' | 'row1') => <div className="data-pivot-dim" key={at}>
    <div className="data-field"><label htmlFor={id + '-row' + at}>{at === 0 ? 'Row' : 'Second row'}</label>
      <Select id={id + '-row' + at} value={current ? refKey(current) : ''} disabled={disabled} onChange={event => setRow(at, parse(event.target.value))}>
        {!current && <option value="">Choose a column</option>}
        {options.map(option => {const dup = usedElsewhere(option, skip); return <option key={refKey(option.ref)} value={refKey(option.ref)} disabled={dup}>{option.label}{dup ? ' (already used)' : ''}</option>;})}</Select></div>
    {current && isDateKind(kindOf(current)) && <div className="data-field"><label htmlFor={id + '-rowby' + at}>{at === 0 ? 'Row' : 'Second row'} by</label>
      <Select id={id + '-rowby' + at} value={current.bucket || ''} disabled={disabled} onChange={event => onChange({...pivot, rows: pivot.rows.map((row, index) => index === at ? withBucket(row, event.target.value, false) : row)})}>{bucketOptions(false)}</Select></div>}
    {at === 1 && <Button variant="ghost" size="icon" aria-label="Remove second row" title="Remove second row" disabled={disabled} onClick={() => setRow(1, undefined)}><X size={16} strokeWidth={1.5}/></Button>}
  </div>;
  return <section className="data-pivot-builder" id="pivot-setup" aria-label="Pivot setup">
    <div className="data-pivot-group" role="group" aria-labelledby={id + '-rows'}>
      <h2 className="data-pivot-heading" id={id + '-rows'}>Rows</h2>
      {dimRow(pivot.rows[0], 0, 'row0')}
      {pivot.rows[1] ? dimRow(pivot.rows[1], 1, 'row1') : pivot.rows.length > 0 && <Button variant="ghost" size="sm" disabled={disabled} onClick={() => {
        const pick = options.find(option => !usedElsewhere(option, 'row1') && !(pivot.columns && pivot.columns.table_alias === option.ref.table_alias && pivot.columns.column === option.ref.column));
        if (pick) onChange({...pivot, rows: [...pivot.rows, dim(pick.ref)]});
      }}><Plus size={16} strokeWidth={1.5}/>Add another row</Button>}
    </div>
    <div className="data-pivot-group" role="group" aria-labelledby={id + '-columns'}>
      <h2 className="data-pivot-heading" id={id + '-columns'}>Columns</h2>
      <div className="data-pivot-dim">
        <div className="data-field"><label htmlFor={id + '-column'}>Column</label>
          <Select id={id + '-column'} value={pivot.columns ? refKey(pivot.columns) : ''} disabled={disabled} onChange={event => {
            const ref = parse(event.target.value);
            onChange({...pivot, columns: ref ? dim(ref, pivot.columns || undefined) : null, totals: ref ? pivot.totals : true});
          }}><option value="">None</option>
            {options.map(option => {const issue = columnIssue(option), dup = usedElsewhere(option, 'column');
              return <option key={refKey(option.ref)} value={refKey(option.ref)} disabled={!!issue || dup}>{option.label}{issue ? ' (' + issue + ')' : dup ? ' (already used)' : ''}</option>;})}</Select></div>
        {pivot.columns && isDateKind(kindOf(pivot.columns)) && <div className="data-field"><label htmlFor={id + '-columnby'}>Columns by</label>
          <Select id={id + '-columnby'} value={pivot.columns.bucket || 'month'} disabled={disabled} onChange={event => onChange({...pivot, columns: withBucket(pivot.columns!, event.target.value, true)})}>{bucketOptions(true)}</Select></div>}
      </div>
    </div>
    <div className="data-pivot-group" role="group" aria-labelledby={id + '-value'}>
      <h2 className="data-pivot-heading" id={id + '-value'}>Value</h2>
      <div className="data-pivot-dim">
        <div className="data-field"><label htmlFor={id + '-measure'}>Show</label>
          <Select id={id + '-measure'} value={pivot.value.fn} disabled={disabled} onChange={event => setFn(event.target.value as AggregateFn)}>{MEASURES.map(row => <option key={row.fn} value={row.fn}>{row.label}</option>)}</Select></div>
        {measure.needsColumn && <div className="data-field"><label htmlFor={id + '-valuecolumn'}>Column</label>
          <Select id={id + '-valuecolumn'} disabled={disabled || !valueOptions.length} value={pivot.value.table_alias && pivot.value.column ? refKey({table_alias: pivot.value.table_alias, column: pivot.value.column}) : ''}
            onChange={event => {const ref = parse(event.target.value); onChange({...pivot, value: {fn: pivot.value.fn, ...(ref ? {table_alias: ref.table_alias, column: ref.column} : {})}});}}>
            {!valueOptions.length ? <option value="">No suitable columns</option> : <option value="" disabled>Choose a column</option>}
            {valueOptions.map(option => <option key={refKey(option.ref)} value={refKey(option.ref)}>{option.label}</option>)}</Select></div>}
      </div>
    </div>
    <div className="data-pivot-group" role="group" aria-labelledby={id + '-order'}>
      <h2 className="data-pivot-heading" id={id + '-order'}>Order</h2>
      <div className="data-pivot-dim">
        <div className="data-field"><label htmlFor={id + '-sort'}>Sort rows by</label>
          <Select id={id + '-sort'} value={sortValue} disabled={disabled} onChange={event => {const [by, direction] = event.target.value.split(':'); onChange({...pivot, sort: {by: by as 'rows' | 'total', direction: direction as 'asc' | 'desc'}});}}>
            {SORTS.map(row => <option key={row.value} value={row.value}>{row.label}</option>)}</Select></div>
        <label className="data-check" title={hasColumns ? undefined : 'Without columns, the total is the only number.'}>
          <input type="checkbox" checked={pivot.totals || !hasColumns} disabled={disabled || !hasColumns} onChange={event => onChange({...pivot, totals: event.target.checked})}/>Show totals</label>
        {!hasColumns && <span className="data-muted">Without columns, the total is the only number.</span>}
      </div>
    </div>
  </section>;
}
