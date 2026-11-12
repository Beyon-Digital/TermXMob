import {useId, useMemo, useState, type ReactNode} from 'react';
import {Layers} from 'lucide-react';
import {Button} from '../../components/ui/button';
import {Input} from '../../components/ui/input';
import {Popover, PopoverContent, PopoverTrigger} from '../../components/ui/popover';
import {humanize} from '../../lib/query-builder';
import {segmentCompatible} from '../../lib/segments';
import type {ViewSummary} from '../../lib/data-views';

export type SegmentContext = {
  /** Segments of the current source. */ items: ViewSummary[]; baseTable: string; names: (id: string) => string | undefined;
  onAdd: (id: string, negate: boolean) => void;
};
/** "In segment" / "Not in segment" choice, shared by the add menu and the chip popover. */
export function MembershipChoice({value, onChange, name}: {value: boolean; onChange: (negate: boolean) => void; name: string}) {
  return <fieldset className="data-segment-choice"><legend className="visually-hidden">Membership</legend>
    <label className="data-check"><input type="radio" name={name} checked={!value} onChange={() => onChange(false)}/>In segment</label>
    <label className="data-check"><input type="radio" name={name} checked={value} onChange={() => onChange(true)}/>Not in segment</label>
  </fieldset>;
}

/** Add a filter that keeps rows inside or outside a saved segment. Segments built on another table are listed, disabled, with the reason. */
export default function SegmentFilterMenu({context, children}: {context: SegmentContext; children?: ReactNode}) {
  const [open, setOpen] = useState(false), [negate, setNegate] = useState(false), [search, setSearch] = useState(''), id = useId();
  const needle = search.trim().toLowerCase();
  const shown = useMemo(() => context.items.filter(row => row.kind === 'segment' && (!needle || row.name.toLowerCase().includes(needle))), [context.items, needle]);
  const all = context.items.some(row => row.kind === 'segment');
  return <Popover open={open} onOpenChange={next => {setOpen(next); if (next) {setNegate(false); setSearch('');}}}>
    <PopoverTrigger asChild>{children || <Button variant="ghost" size="sm"><Layers size={16} strokeWidth={1.5}/>Add segment</Button>}</PopoverTrigger>
    <PopoverContent className="data-filter-popover" aria-label="Add segment filter">
      <div className="data-filter-form">
        <h3 className="data-popover-title">Segments</h3>
        {!all ? <p className="data-muted">No segments yet. Add filters, then choose Save as segment.</p> : <>
          <MembershipChoice value={negate} onChange={setNegate} name={id + '-membership'}/>
          <div className="data-field"><label htmlFor={id + '-search'}>Find a segment</label><Input id={id + '-search'} type="search" autoComplete="off" value={search} onChange={event => setSearch(event.target.value)}/></div>
          {shown.length ? <ul className="data-segment-list">{shown.map(row => {
            const fits = segmentCompatible(row, context.baseTable);
            return <li key={row.id}><button type="button" className="data-segment-option" disabled={!fits} onClick={() => {context.onAdd(row.id, negate); setOpen(false);}}>
              <span>{row.name}</span>{!fits && <small>Built on {humanize(row.base_table)}</small>}</button></li>;
          })}</ul> : <p className="data-muted">No segments match.</p>}
        </>}
      </div>
    </PopoverContent>
  </Popover>;
}
