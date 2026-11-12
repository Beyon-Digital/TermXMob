import {Columns3} from 'lucide-react';
import {Button} from '../../components/ui/button';
import {Popover, PopoverContent, PopoverTrigger} from '../../components/ui/popover';
import {humanize, refKey, sameColumn, type TableColumns} from '../../lib/query-builder';
import type {ColumnRef} from '../../lib/query-spec';

type Props = {tables: TableColumns[]; visible: ColumnRef[] | null; onChange: (visible: ColumnRef[] | null) => void; disabled?: boolean};

/** Choose which columns the grid shows. `null` means all of them. */
export default function ColumnPicker({tables, visible, onChange, disabled = false}: Props) {
  const all = tables.flatMap(table => table.columns.map(column => ({table_alias: table.alias, column: column.name})));
  const current = visible ?? all, shownCount = current.length;
  function toggle(ref: ColumnRef, on: boolean) {
    const next = on ? all.filter(item => sameColumn(item, ref) || current.some(row => sameColumn(row, item))) : current.filter(item => !sameColumn(item, ref));
    if (!next.length) return;
    onChange(next.length === all.length ? null : next);
  }
  return <Popover>
    <PopoverTrigger asChild><Button variant="ghost" size="sm" disabled={disabled}><Columns3 size={16} strokeWidth={1.5}/>Columns</Button></PopoverTrigger>
    <PopoverContent className="data-column-popover" aria-label="Choose columns">
      <div className="data-section-heading"><h2 className="data-popover-title">Columns</h2><Button variant="ghost" size="sm" disabled={visible === null} onClick={() => onChange(null)}>Show all</Button></div>
      {tables.map(table => <fieldset key={table.alias} className="data-column-group"><legend>{tables.length > 1 ? humanize(table.table) : 'Columns to show'}</legend>
        {table.columns.map(column => {
          const ref = {table_alias: table.alias, column: column.name}, on = current.some(row => sameColumn(row, ref));
          return <label key={refKey(ref)} className="data-check"><input type="checkbox" checked={on} disabled={on && shownCount === 1} onChange={event => toggle(ref, event.target.checked)}/><span>{column.name}</span></label>;
        })}
      </fieldset>)}
      {shownCount === 1 && <p className="data-muted">Keep at least one column.</p>}
    </PopoverContent>
  </Popover>;
}
