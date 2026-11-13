import {useState} from 'react';
import {Check, X} from 'lucide-react';
import {Button} from '../../components/ui/button';
import {Input} from '../../components/ui/input';
import {asSegmentSpec, hasFilters, proposalChips, removeChip, type SpecProposal} from '../../lib/copilot-proposals';
import type {QuerySpec} from '../../lib/query-spec';
import SaveViewDialog from './SaveViewDialog';
import ProposalValueEditor from './ProposalValueEditor';

const KIND_LABEL = {table: 'Table', pivot: 'Pivot', segment: 'Segment'} as const;

type Props = {
  proposal: SpecProposal; id: string; canSave: boolean; busy?: boolean;
  onApplyTable?: (spec: QuerySpec, title: string, note: string) => void;
  onOpenPivot?: (spec: QuerySpec, title: string) => void;
  /** Saves for real, on this person's click only. Resolves when the segment exists. */
  onSaveSegment?: (spec: QuerySpec, name: string, description: string) => Promise<unknown>;
};

/** One structured proposal as plain-language chips. Values and removals stay local until the person chooses an action. */
export default function ProposalCard({proposal, id, canSave, busy = false, onApplyTable, onOpenPivot, onSaveSegment}: Props) {
  const {type, definition} = proposal;
  const [spec, setSpec] = useState<QuerySpec>(definition.spec), [name, setName] = useState(proposal.title), [dialog, setDialog] = useState(false), [saved, setSaved] = useState(false), [applied, setApplied] = useState('');
  const [editing, setEditing] = useState<string | null>(null);
  const chips = proposalChips(type, spec), edited = JSON.stringify(spec) !== JSON.stringify(definition.spec);
  const title = name.trim() || proposal.title;
  const canSegment = canSave && !!onSaveSegment && hasFilters(spec) && !saved;
  const apply = (action: string, run: () => void) => {run(); setApplied(action);};
  const tableButton = onApplyTable && (type === 'table' || type === 'segment') &&
    <Button variant={type === 'table' ? 'default' : 'secondary'} disabled={busy} onClick={() => apply('Applied to a table tab.', () => onApplyTable(type === 'segment' ? asSegmentSpec(spec) : spec, title, 'Suggested by Copilot. Changes here do not change your data.'))}>Apply to table</Button>;
  const pivotButton = onOpenPivot && type === 'pivot' && <Button disabled={busy} onClick={() => apply('Opened as a new pivot.', () => onOpenPivot(spec, title))}>Open as pivot</Button>;
  const segmentButton = canSegment && <Button variant={type === 'segment' ? 'default' : 'secondary'} disabled={busy} onClick={() => setDialog(true)}>Save as segment</Button>;
  const groups = chips.reduce<{group: string; items: typeof chips}[]>((rows, chip) => {const last = rows[rows.length - 1]; if (last?.group === chip.group) last.items.push(chip); else rows.push({group: chip.group, items: [chip]}); return rows;}, []);
  return <section className="copilot-proposal" aria-labelledby={id + '-title'}>
    <header><span className="copilot-proposal-kind">{KIND_LABEL[type]} suggestion</span><h3 id={id + '-title'}>{title}</h3></header>
    <label className="copilot-proposal-name"><span>Suggestion name</span><Input value={name} maxLength={200} onChange={event => setName(event.target.value)}/></label>
    <dl className="copilot-chips">{groups.map(({group, items}) => <div className="copilot-chip-row" key={group}><dt>{group}</dt><dd>{items.map(chip => <span className="copilot-chip" key={chip.id}>
      {chip.editable ? <button type="button" className="copilot-chip-edit" disabled={busy} aria-label={'Edit ' + chip.text} onClick={() => setEditing(chip.id)}>{chip.text}</button> : chip.text}
      {chip.removable && <button type="button" className="copilot-chip-remove" disabled={busy} aria-label={'Remove ' + chip.text} onClick={() => {setSpec(value => removeChip(value, chip.id)); setApplied(''); setSaved(false);}}><X size={12}/></button>}</span>)}</dd></div>)}</dl>
    {edited && <button type="button" className="copilot-chip-reset" disabled={busy} onClick={() => {setSpec(definition.spec); setApplied(''); setSaved(false);}}>Undo changes</button>}
    <div className="copilot-proposal-actions">{pivotButton}{tableButton}{segmentButton}</div>
    <p className="copilot-proposal-status" role="status">{saved ? <><Check size={14}/> Segment saved.</> : applied}</p>
    {editing && <ProposalValueEditor spec={spec} chipId={editing} onChange={next => {setSpec(next); setApplied(''); setSaved(false);}} onClose={() => setEditing(null)}/>}
    {onSaveSegment && <SaveViewDialog open={dialog} title="Save as segment" submitLabel="Save segment" initialName={title} helper="Everyone with access to this source can use it in filters."
      onSubmit={async segmentName => {await onSaveSegment(asSegmentSpec(spec), segmentName, definition.description); setSaved(true); setDialog(false);}} onClose={() => setDialog(false)}/>}
  </section>;
}
