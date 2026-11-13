import {useId, useMemo, useState, type KeyboardEvent} from 'react';
import {Check, ChevronsUpDown} from 'lucide-react';
import {Input} from '../../components/ui/input';
import {Popover, PopoverContent, PopoverTrigger} from '../../components/ui/popover';

export type PickerOption = {value: string; label: string};
type Props = {
  id?: string; /** The accessible name, which stays on the trigger. */ label: string; value: string; options: PickerOption[]; onChange: (value: string) => void;
  /** Shown when there is nothing to choose. */ emptyText: string; disabled?: boolean; className?: string;
};
/** Show a search box once the list is long enough that scanning it is slow. */
export const PICKER_SEARCH_FROM = 8;

/**
 * A Radix popover picker for a short list of named things (workspaces, sources). The trigger is a combobox button, the list a
 * listbox: arrows move, Enter or Space picks, typing in the search narrows, Esc closes and returns focus to the trigger.
 */
export default function DataPicker({id, label, value, options, onChange, emptyText, disabled = false, className}: Props) {
  const [open, setOpen] = useState(false), [search, setSearch] = useState(''), listId = useId();
  const needle = search.trim().toLowerCase(), searchable = options.length >= PICKER_SEARCH_FROM;
  const shown = useMemo(() => options.filter(option => !needle || option.label.toLowerCase().includes(needle)), [options, needle]);
  const selected = options.find(option => option.value === value);
  function keys(event: KeyboardEvent<HTMLElement>) {
    if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp' && event.key !== 'Home' && event.key !== 'End') return;
    const items = Array.from(event.currentTarget.querySelectorAll<HTMLElement>('input, [role="option"]')), at = items.indexOf(document.activeElement as HTMLElement);
    event.preventDefault();
    const next = event.key === 'Home' ? 0 : event.key === 'End' ? items.length - 1 : Math.max(0, Math.min(items.length - 1, at + (event.key === 'ArrowDown' ? 1 : -1)));
    items[next]?.focus();
  }
  return <Popover open={open} onOpenChange={next => {setOpen(next); if (next) setSearch('');}}>
    <PopoverTrigger asChild>
      <button type="button" id={id} role="combobox" aria-label={label} aria-haspopup="listbox" aria-expanded={open} aria-controls={open ? listId : undefined}
        className={'data-picker-trigger input select ' + (className || '')} disabled={disabled}>
        <span className="data-picker-value" data-empty={selected ? undefined : true}>{selected ? selected.label : emptyText}</span>
        <ChevronsUpDown size={16} strokeWidth={1.5} aria-hidden="true"/>
      </button>
    </PopoverTrigger>
    <PopoverContent className="data-picker-popover" aria-label={label} align="start" onKeyDown={keys}
      onOpenAutoFocus={event => {if (!searchable) {event.preventDefault(); (event.currentTarget as HTMLElement).querySelector<HTMLElement>('[aria-selected="true"], [role="option"]')?.focus();}}}>
      {searchable && <Input type="search" autoComplete="off" aria-label={'Find in ' + label.toLowerCase()} placeholder="Search" value={search} onChange={event => setSearch(event.target.value)}/>}
      {shown.length ? <ul id={listId} className="data-picker-list" role="listbox" aria-label={label}>
        {shown.map(option => <li key={option.value} role="presentation">
          <button type="button" role="option" aria-selected={option.value === value} className="data-picker-option" title={option.label}
            onClick={() => {setOpen(false); if (option.value !== value) onChange(option.value);}}>
            <span>{option.label}</span>{option.value === value && <Check size={16} strokeWidth={1.5} aria-hidden="true"/>}
          </button></li>)}
      </ul> : <p className="data-muted">{options.length ? 'Nothing matches.' : emptyText}</p>}
    </PopoverContent>
  </Popover>;
}
