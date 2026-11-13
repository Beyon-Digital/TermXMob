import {useId} from 'react';
import {Button} from '../../components/ui/button';
import {Select} from '../../components/ui/select';
import {BUCKETS, MEASURES, describeSummary, kindGroup, refKey, type Summary} from '../../lib/query-builder';
import type {AggregateFn, ColumnRef, DateBucket} from '../../lib/query-spec';
import type {ColumnOption} from './ColumnFilter';

type Props = {
  options: ColumnOption[]; summary: Summary; label: (ref: ColumnRef) => string;
  /** Buckets the source supports. Others stay visible, disabled, with the reason. */ buckets?: readonly DateBucket[];
  onChange: (summary: Summary) => void; onClear: () => void;
};
const parse = (value: string): ColumnRef | undefined => {const [table_alias, column] = value.split('\u0000'); return column === undefined ? undefined : {table_alias, column};};

/** Measure, grouping and date bucket. Applies as the controls change, so the view keeps a single primary action. */
export default function SummarizePanel({options, summary, label, buckets, onChange, onClear}: Props) {
  const id = useId(), measure = MEASURES.find(row => row.fn === summary.measure.fn)!;
  const measureOptions = options.filter(option => !measure.numeric || kindGroup(option.kind) === 'number');
  const by = options.find(option => summary.by && refKey(option.ref) === refKey(summary.by)), dated = by && kindGroup(by.kind) === 'date';
  function setFn(fn: AggregateFn) {
    const next = MEASURES.find(row => row.fn === fn)!, pool = options.filter(option => !next.numeric || kindGroup(option.kind) === 'number');
    const keep = summary.measure.column && pool.some(option => refKey(option.ref) === refKey(summary.measure.column!)) ? summary.measure.column : pool[0]?.ref;
    onChange({...summary, measure: next.needsColumn ? {fn, column: keep} : {fn}});
  }
  return <section className="data-summarize" aria-label="Summarize">
    <div className="data-summarize-controls">
      <div className="data-field"><label htmlFor={id + '-fn'}>Measure</label>
        <Select id={id + '-fn'} value={summary.measure.fn} onChange={event => setFn(event.target.value as AggregateFn)}>{MEASURES.map(row => <option key={row.fn} value={row.fn}>{row.label}</option>)}</Select></div>
      {measure.needsColumn && <div className="data-field"><label htmlFor={id + '-column'}>Column</label>
        <Select id={id + '-column'} value={summary.measure.column ? refKey(summary.measure.column) : ''} onChange={event => onChange({...summary, measure: {fn: summary.measure.fn, column: parse(event.target.value)}})}>
          {!measureOptions.length && <option value="">No suitable columns</option>}{measureOptions.map(option => <option key={refKey(option.ref)} value={refKey(option.ref)}>{option.label}</option>)}</Select></div>}
      <div className="data-field"><label htmlFor={id + '-by'}>Group by</label>
        <Select id={id + '-by'} value={summary.by ? refKey(summary.by) : ''} onChange={event => {
          const ref = parse(event.target.value), option = options.find(row => ref && refKey(row.ref) === refKey(ref));
          onChange({measure: summary.measure, by: ref, bucket: option && kindGroup(option.kind) === 'date' ? summary.bucket || 'month' : undefined});
        }}><option value="">No grouping</option>{options.map(option => <option key={refKey(option.ref)} value={refKey(option.ref)}>{option.label}</option>)}</Select></div>
      {dated && <div className="data-field"><label htmlFor={id + '-bucket'}>Date bucket</label>
        <Select id={id + '-bucket'} value={summary.bucket || 'month'} onChange={event => onChange({...summary, bucket: event.target.value as DateBucket})}>
          {BUCKETS.map(row => {const off = !!buckets && !buckets.includes(row.value); return <option key={row.value} value={row.value} disabled={off}>{row.label}{off ? ' (not available for this source)' : ''}</option>;})}</Select></div>}
    </div>
    <div className="data-summarize-footer"><p role="status" className="data-muted">{describeSummary(summary, label)}</p><Button variant="ghost" size="sm" onClick={onClear}>Clear summary</Button></div>
  </section>;
}
