import {useId, useState, type FormEvent} from 'react';
import {Plus, X} from 'lucide-react';
import {Button} from '../../components/ui/button';
import {Dialog, DialogContent, DialogDescription, DialogTitle} from '../../components/ui/dialog';
import {Input} from '../../components/ui/input';
import {Select} from '../../components/ui/select';
import {describeFilter, isFilterGroup} from '../../lib/query-builder';
import {isFilterLeaf, type FilterLeaf, type FilterNode, type QuerySpec, type Scalar} from '../../lib/query-spec';
import {plainContext} from '../../lib/segments';
import './proposal-values.css';

type DraftValue = {raw: string; type: 'string' | 'number' | 'boolean'};
type DraftLeaf = {path: string; leaf: FilterLeaf; values: DraftValue[]};
type Props = {spec: QuerySpec; chipId: string; onChange: (spec: QuerySpec) => void; onClose: () => void};
const draftValue = (value: Scalar): DraftValue => ({raw: String(value), type: typeof value as DraftValue['type']});

function draftsFor(node: FilterNode, path = ''): DraftLeaf[] {
  if (isFilterGroup(node)) return node.items.flatMap((item, index) => draftsFor(item, path + ':' + index));
  if (!isFilterLeaf(node) || node.op === 'is_null' || node.op === 'not_null') return [];
  return [{path, leaf: node, values: (node.values ?? [node.value ?? '']).map(draftValue)}];
}

function parseValue(draft: DraftValue): Scalar | undefined {
  if (draft.type === 'boolean') return draft.raw === 'true' ? true : draft.raw === 'false' ? false : undefined;
  if (!draft.raw.trim()) return undefined;
  if (draft.type === 'number') return Number.isFinite(Number(draft.raw)) ? Number(draft.raw) : undefined;
  return draft.raw;
}

/** Edits literals only: fields, operators, match rules and primitive types are preserved. */
export default function ProposalValueEditor({spec, chipId, onChange, onClose}: Props) {
  const nodeIndex = Number(chipId.split(':')[1]), node = spec.filters.items[nodeIndex];
  return <Dialog open onOpenChange={open => {if (!open) onClose();}}>
    <DialogContent className="data-dialog copilot-value-dialog">
      <DialogTitle>{chipId === 'limit' ? 'Edit rows shown' : 'Edit filter values'}</DialogTitle>
      <DialogDescription>Update this suggestion before choosing how to use it.</DialogDescription>
      {chipId === 'limit' ? <LimitForm spec={spec} onChange={onChange} onClose={onClose}/>
        : node && <ValuesForm node={node} onClose={onClose} onChange={next => onChange({...spec, offset: 0, filters: {...spec.filters, items: spec.filters.items.map((item, index) => index === nodeIndex ? next : item)}})}/>}
    </DialogContent>
  </Dialog>;
}

function LimitForm({spec, onChange, onClose}: Omit<Props, 'chipId'>) {
  const id = useId(), [raw, setRaw] = useState(String(spec.limit)), [error, setError] = useState('');
  function submit(event: FormEvent) {
    event.preventDefault();
    const limit = Number(raw);
    if (!raw.trim() || !Number.isInteger(limit) || limit < 1 || limit > 1000) {setError('Enter a whole number from 1 to 1000.'); return;}
    onChange({...spec, limit, offset: 0}); onClose();
  }
  return <form className="copilot-value-form" onSubmit={submit} noValidate>
    <div className="data-field"><label htmlFor={id}>Rows shown</label><Input id={id} inputMode="numeric" value={raw} onChange={event => {setRaw(event.target.value); setError('');}} aria-invalid={!!error} aria-describedby={error ? id + '-error' : undefined}/>
      {error && <p className="data-field-error" role="alert" id={id + '-error'}>{error}</p>}</div>
    <EditorActions onClose={onClose}/>
  </form>;
}

function ValuesForm({node, onChange, onClose}: {node: FilterNode; onChange: (node: FilterNode) => void; onClose: () => void}) {
  const id = useId(), [drafts, setDrafts] = useState(() => draftsFor(node)), [errors, setErrors] = useState<Record<string, string>>({});
  const update = (path: string, change: (draft: DraftLeaf) => DraftLeaf) => {
    setDrafts(rows => rows.map(row => row.path === path ? change(row) : row));
    setErrors(rows => ({...rows, [path]: ''}));
  };
  function submit(event: FormEvent) {
    event.preventDefault();
    const failures: Record<string, string> = {}, replacements = new Map<string, FilterLeaf>();
    for (const draft of drafts) {
      const values = draft.values.map(parseValue), list = draft.leaf.op === 'in' || draft.leaf.op === 'not_in';
      if (!values.length || values.some(value => value === undefined)) failures[draft.path] = draft.values.some(value => value.type === 'number') ? 'Enter a number for each value.' : 'Enter each value.';
      else if (list && values.length > 200) failures[draft.path] = 'Use 200 values or fewer.';
      else if (draft.leaf.op === 'between' && typeof values[0] === 'number' && typeof values[1] === 'number' && values[0] > values[1]) failures[draft.path] = 'The end of the range must be at least the start.';
      else {
        const {value: _value, values: _values, ...leaf} = draft.leaf;
        replacements.set(draft.path, list || draft.leaf.op === 'between' ? {...leaf, values: values as Scalar[]} : {...leaf, value: values[0] as Scalar});
      }
    }
    setErrors(failures);
    if (Object.keys(failures).length) return;
    const replace = (item: FilterNode, path = ''): FilterNode => isFilterGroup(item) ? {...item, items: item.items.map((child, index) => replace(child, path + ':' + index))} : replacements.get(path) ?? item;
    onChange(replace(node)); onClose();
  }
  return <form className="copilot-value-form" onSubmit={submit} noValidate>
    {drafts.map((draft, at) => {
      const list = draft.leaf.op === 'in' || draft.leaf.op === 'not_in', range = draft.leaf.op === 'between', errorId = id + '-error-' + at;
      return <fieldset className="copilot-value-fields" key={draft.path}><legend>{describeFilter(draft.leaf, plainContext)}</legend>
        {draft.values.map((value, index) => {
          const inputId = id + '-' + at + '-' + index, label = range ? (index ? 'To' : 'From') : list ? 'Value ' + (index + 1) : 'Value';
          const change = (raw: string) => update(draft.path, row => ({...row, values: row.values.map((item, position) => position === index ? {...item, raw} : item)}));
          return <div className="copilot-value-row" key={index}><div className="data-field"><label htmlFor={inputId}>{label}</label>
            {value.type === 'boolean' ? <Select id={inputId} value={value.raw} onChange={event => change(event.target.value)} aria-invalid={!!errors[draft.path]} aria-describedby={errors[draft.path] ? errorId : undefined}><option value="true">Yes</option><option value="false">No</option></Select>
              : <Input id={inputId} inputMode={value.type === 'number' ? 'decimal' : undefined} value={value.raw} onChange={event => change(event.target.value)} aria-invalid={!!errors[draft.path]} aria-describedby={errors[draft.path] ? errorId : undefined}/>}
          </div>{list && <Button variant="ghost" size="icon" disabled={draft.values.length === 1} aria-label={'Remove value ' + (index + 1)} onClick={() => update(draft.path, row => ({...row, values: row.values.filter((_, position) => position !== index)}))}><X size={16} strokeWidth={1.5}/></Button>}</div>;
        })}
        {errors[draft.path] && <p className="data-field-error" role="alert" id={errorId}>{errors[draft.path]}</p>}
        {list && <Button variant="ghost" disabled={draft.values.length >= 200} onClick={() => update(draft.path, row => ({...row, values: [...row.values, {...row.values[0], raw: row.values[0].type === 'boolean' ? 'true' : ''}]}))}><Plus size={16} strokeWidth={1.5}/>Add value</Button>}
      </fieldset>;
    })}
    <EditorActions onClose={onClose}/>
  </form>;
}

function EditorActions({onClose}: {onClose: () => void}) {
  return <div className="data-dialog-actions"><Button variant="ghost" onClick={onClose}>Cancel</Button><Button type="submit">Update suggestion</Button></div>;
}
