import {lazy, Suspense} from 'react';
import {Play, Save, Square, Trash2, X} from 'lucide-react';
import {Button} from '../../components/ui/button';
import {Popover, PopoverAnchor, PopoverContent} from '../../components/ui/popover';

const SqlEditor = lazy(() => import('./SqlEditor'));

type Props = {
  open: boolean; onClose: () => void; title: string;
  sql: string; onSql: (value: string) => void; parameterText: string; onParameterText: (value: string) => void;
  /** Editing is locked while a request is in flight. */ locked: boolean;
  canRun: boolean; running: boolean; runLabel: string; canCancel: boolean; onRun: () => void; onCancel: () => void;
  canSave: boolean; onSave: () => void; archiveName?: string; onArchive?: () => void; archiveDisabled?: boolean;
};

/**
 * Optional SQL editor. A floating, non-modal panel: the grid behind it stays usable, Esc or the close button dismisses it.
 * It is anchored under the workbench heading and floats over the results.
 */
export default function AdvancedSql({open, onClose, title, sql, onSql, parameterText, onParameterText, locked, canRun, running, runLabel, canCancel, onRun, onCancel, canSave, onSave, archiveName, onArchive, archiveDisabled}: Props) {
  return <Popover open={open} modal={false} onOpenChange={next => {if (!next) onClose();}}>
    <PopoverAnchor asChild><div className="data-advanced-anchor" aria-hidden="true"/></PopoverAnchor>
    <PopoverContent className="data-advanced-panel" align="start" sideOffset={8} aria-label="SQL editor" onInteractOutside={event => event.preventDefault()}>
      <div className="data-advanced-header"><h2 className="data-popover-title">{title}</h2>
        <Button variant="ghost" size="icon" aria-label="Close SQL editor" title="Close SQL editor" onClick={onClose}><X size={16} strokeWidth={1.5}/></Button></div>
      <Suspense fallback={<div className="data-skeleton-editor" role="status" aria-label="Loading editor"/>}>
        <SqlEditor value={sql} onChange={onSql} onRun={canRun ? onRun : undefined} disabled={locked}/>
      </Suspense>
      <details className="data-parameters"><summary>Query parameters</summary>
        <label>Parameters (JSON object)<textarea className="input" spellCheck={false} value={parameterText} onChange={event => onParameterText(event.target.value)} disabled={locked}/></label></details>
      <div className="data-advanced-actions">
        {onArchive && <Button variant="destructive" size="sm" aria-label={'Archive query ' + archiveName} onClick={onArchive} disabled={archiveDisabled}><Trash2 size={16} strokeWidth={1.5}/>Archive</Button>}
        <Button variant="secondary" size="sm" onClick={onSave} disabled={!canSave}><Save size={16} strokeWidth={1.5}/>Save query</Button>
        {running && canCancel
          ? <Button variant="secondary" size="sm" onClick={onCancel}><Square size={16} strokeWidth={1.5}/>Cancel</Button>
          : <Button size="sm" onClick={onRun} disabled={!canRun}><Play size={16} strokeWidth={1.5}/>{runLabel}</Button>}
      </div>
    </PopoverContent>
  </Popover>;
}
