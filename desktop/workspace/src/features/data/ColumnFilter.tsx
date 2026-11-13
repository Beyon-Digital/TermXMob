import {useId, useState, type FormEvent, type ReactNode} from 'react';
import {ListFilter} from 'lucide-react';
import {Button} from '../../components/ui/button';
import {Input} from '../../components/ui/input';
import {Popover, PopoverContent, PopoverTrigger} from '../../components/ui/popover';
import {Select} from '../../components/ui/select';
import {humanOp, kindGroup, operatorsFor, refKey} from '../../lib/query-builder';
import type {ColumnRef, FilterLeaf, LeafOp, Scalar, ValueKind} from '../../lib/query-spec';

export type ColumnOption = {ref: ColumnRef; label: string; kind?: ValueKind};
type FormProps = {
  /** Fixed column (header filter or editing a chip). Without it the person picks the column first. */
  column?: ColumnOption; columns: ColumnOption[]; leaf?: FilterLeaf; allowedOps?: readonly LeafOp[]; onApply: (leaf: FilterLeaf) => void;
};
const NO_VALUE: LeafOp[] = ['is_null', 'not_null'];
const text = (value: Scalar | undefined) => value === undefined ? '' : String(value);

function parseNumber(raw: string): number | null {
  const value = raw.trim();
  return value && Number.isFinite(Number(value)) ? Number(value) : null;
}
/** Turn what the person typed into filter values, or an error sentence. */
export function buildLeaf(field: ColumnRef, kind: ValueKind | undefined, op: LeafOp, inputs: {one: string; two: string; list: string; choice: string}): FilterLeaf | string {
  const group = kindGroup(kind), one = (raw: string): Scalar | null => {
    if (group === 'number') return parseNumber(raw);
    if (group === 'boolean') return raw === 'yes';
    return raw.trim() ? (group === 'date' ? raw.trim() : raw) : null;
  };
  if (NO_VALUE.includes(op)) return {field, op};
  if (group === 'boolean') return {field, op, value: inputs.choice !== 'no'};
  if (op === 'between') {
    const from = one(inputs.one), to = one(inputs.two);
    return from === null || to === null ? (group === 'number' ? 'Enter two numbers.' : 'Enter both ends of the range.') : {field, op, values: [from, to]};
  }
  if (op === 'in' || op === 'not_in') {
    const values = inputs.list.split(',').map(part => part.trim()).filter(Boolean).map(part => group === 'number' ? parseNumber(part) : part);
    if (!values.length || values.some(value => value === null)) return group === 'number' ? 'Enter numbers separated by commas.' : 'Enter at least one value, separated by commas.';
    if (values.length > 200) return 'Use 200 values or fewer.';
    return {field, op, values: values as Scalar[]};
  }
  const value = one(inputs.one);
  if (value === null) return group === 'number' ? 'Enter a number.' : 'Enter a value.';
  return {field, op, value};
}

/** The editor shared by the header popover, the chip popover and "Add filter". */
export function FilterForm({column, columns, leaf, allowedOps, onApply}: FormProps) {
  const id = useId();
  const [picked, setPicked] = useState(column ? refKey(column.ref) : leaf ? refKey(leaf.field) : '');
  const current = column || columns.find(option => refKey(option.ref) === picked);
  const ops = operatorsFor(current?.kind, allowedOps), group = kindGroup(current?.kind);
  const [op, setOp] = useState<LeafOp>(leaf && ops.includes(leaf.op) ? leaf.op : ops[0] || 'is_null');
  const [one, setOne] = useState(leaf?.value !== undefined ? text(leaf.value) : leaf?.values?.[0] !== undefined && leaf.op === 'between' ? text(leaf.values[0]) : '');
  const [two, setTwo] = useState(leaf?.op === 'between' ? text(leaf.values?.[1]) : '');
  const [list, setList] = useState(leaf && (leaf.op === 'in' || leaf.op === 'not_in') ? (leaf.values || []).join(', ') : '');
  const [choice, setChoice] = useState(leaf?.value === false ? 'no' : 'yes');
  const [error, setError] = useState('');
  const activeOp: LeafOp = ops.includes(op) ? op : ops[0] || 'is_null';
  function pickColumn(next: string) {
    setPicked(next);
    const option = columns.find(row => refKey(row.ref) === next), nextOps = operatorsFor(option?.kind, allowedOps);
    setOp(nextOps[0] || 'is_null'); setError('');
  }
  function submit(event: FormEvent) {
    event.preventDefault();
    if (!current) {setError('Choose a column.'); return;}
    const built = buildLeaf(current.ref, current.kind, activeOp, {one, two, list, choice});
    if (typeof built === 'string') {setError(built); return;}
    onApply(built);
  }
  const inputType = group === 'date' ? 'date' : 'text';
  return <form className="data-filter-form" onSubmit={submit} noValidate>
    {!column && <div className="data-field"><label htmlFor={id + '-column'}>Column</label>
      <Select id={id + '-column'} value={picked} onChange={event => pickColumn(event.target.value)}><option value="">Choose a column</option>
        {columns.map(option => <option key={refKey(option.ref)} value={refKey(option.ref)}>{option.label}</option>)}</Select></div>}
    {current && <>
      <div className="data-field"><label htmlFor={id + '-op'}>Condition</label>
        <Select id={id + '-op'} value={activeOp} onChange={event => {setOp(event.target.value as LeafOp); setError('');}}>{ops.map(value => <option key={value} value={value}>{humanOp(current.kind, value)}</option>)}</Select></div>
      {group === 'boolean' && !NO_VALUE.includes(activeOp) && <div className="data-field"><label htmlFor={id + '-choice'}>Value</label>
        <Select id={id + '-choice'} value={choice} onChange={event => setChoice(event.target.value)}><option value="yes">Yes</option><option value="no">No</option></Select></div>}
      {group !== 'boolean' && activeOp === 'between' && <div className="data-field-pair">
        <div className="data-field"><label htmlFor={id + '-from'}>From</label><Input id={id + '-from'} type={inputType} inputMode={group === 'number' ? 'decimal' : undefined} value={one} onChange={event => setOne(event.target.value)} aria-invalid={!!error}/></div>
        <div className="data-field"><label htmlFor={id + '-to'}>To</label><Input id={id + '-to'} type={inputType} inputMode={group === 'number' ? 'decimal' : undefined} value={two} onChange={event => setTwo(event.target.value)} aria-invalid={!!error}/></div>
      </div>}
      {group !== 'boolean' && (activeOp === 'in' || activeOp === 'not_in') && <div className="data-field"><label htmlFor={id + '-list'}>Values, separated by commas</label>
        <Input id={id + '-list'} value={list} onChange={event => setList(event.target.value)} aria-invalid={!!error}/></div>}
      {group !== 'boolean' && !NO_VALUE.includes(activeOp) && activeOp !== 'between' && activeOp !== 'in' && activeOp !== 'not_in' && <div className="data-field"><label htmlFor={id + '-value'}>Value</label>
        <Input id={id + '-value'} type={inputType} inputMode={group === 'number' ? 'decimal' : undefined} value={one} onChange={event => setOne(event.target.value)} aria-invalid={!!error}/></div>}
    </>}
    {error && <p className="data-field-error" role="alert">{error}</p>}
    <div className="data-popover-actions"><Button type="submit" size="sm" disabled={!current}>Apply</Button></div>
  </form>;
}

type PopoverProps = FormProps & {title: string; children: ReactNode; onApply: (leaf: FilterLeaf) => void};
/** A popover around FilterForm. `children` is the trigger button. Esc closes it and focus returns to the trigger. */
export function FilterPopover({children, title, onApply, ...form}: PopoverProps) {
  const [open, setOpen] = useState(false);
  return <Popover open={open} onOpenChange={setOpen}>
    <PopoverTrigger asChild>{children}</PopoverTrigger>
    <PopoverContent className="data-filter-popover" aria-label={title}>
      <FilterForm {...form} key={String(open)} onApply={leaf => {onApply(leaf); setOpen(false);}}/>
    </PopoverContent>
  </Popover>;
}

/** The header button that opens a column's filter. */
export function ColumnFilter({column, columns, active, allowedOps, onApply}: {column: ColumnOption; columns: ColumnOption[]; active: boolean; allowedOps?: readonly LeafOp[]; onApply: (leaf: FilterLeaf) => void}) {
  return <FilterPopover title={'Filter ' + column.label} column={column} columns={columns} allowedOps={allowedOps} onApply={onApply}>
    <Button variant="ghost" size="icon" className="data-grid-filter" data-active={active || undefined} aria-label={'Filter ' + column.label}><ListFilter size={16} strokeWidth={1.5}/></Button>
  </FilterPopover>;
}
