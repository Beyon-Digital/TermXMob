import {lazy, Suspense, useEffect, useRef, useState, type KeyboardEvent, type PointerEvent} from 'react';
import {GripHorizontal, Play, RotateCcw, Save, Square, Trash2, X} from 'lucide-react';
import {Button} from '../../components/ui/button';
import {Popover, PopoverAnchor, PopoverContent} from '../../components/ui/popover';
import {clampOffset, readPanelLayout, writePanelLayout, type PanelLayout} from '../../lib/data-panel-layout';
import {plainSqlError} from '../../lib/data-sql-errors';

const SqlEditor = lazy(() => import('./SqlEditor'));
/** The Advanced menu button. Closing the panel returns focus here. */
export const ADVANCED_TRIGGER_ID = 'data-advanced-trigger';

type Props = {
  open: boolean; onClose: () => void; title: string;
  sql: string; onSql: (value: string) => void; parameterText: string; onParameterText: (value: string) => void;
  /** Editing is locked while a request is in flight. */ locked: boolean;
  canRun: boolean; running: boolean; runLabel: string; canCancel: boolean; onRun: () => void; onCancel: () => void;
  canSave: boolean; onSave: () => void; archiveName?: string; onArchive?: () => void; archiveDisabled?: boolean;
  /** Grows each time the person asks for the editor (menu or button). A panel restored on load leaves it at 0 and keeps focus where it was. */
  focusRequest?: number;
  /** Whose panel size and position to remember. */ ownerId?: string;
  /** Saved queries of the open source, listed so another one can be opened without leaving the editor. */
  queries?: {id: string; name: string}[]; activeQueryId?: string; onOpenQuery?: (id: string) => void;
  /** The last run's or save's error. It is shown here because the panel floats over the results. */ error?: string;
};
const MOD = typeof navigator !== 'undefined' && /mac|iphone|ipad/i.test(navigator.platform || navigator.userAgent) ? '⌘' : 'Ctrl';
const NARROW = 640, STEP = 16;

/**
 * Optional SQL editor. A floating, non-modal panel: the grid behind it stays usable, Esc or the close button dismisses it.
 * It is anchored under the workbench heading and floats over the results.
 */
export default function AdvancedSql({open, onClose, title, sql, onSql, parameterText, onParameterText, locked, canRun, running, runLabel, canCancel, onRun, onCancel, canSave, onSave, archiveName, onArchive, archiveDisabled, focusRequest = 0, ownerId, queries = [], activeQueryId, onOpenQuery, error}: Props) {
  const consumed = useRef(0), [focusTick, setFocusTick] = useState(0), panel = useRef<HTMLDivElement | null>(null);
  const [layout, setLayout] = useState<PanelLayout>(() => readPanelLayout(ownerId)), drag = useRef<{x: number; y: number; from: PanelLayout} | null>(null);
  const wide = typeof window === 'undefined' || window.innerWidth >= NARROW, custom = layout.x !== 0 || layout.y !== 0 || layout.width !== undefined || layout.height !== undefined;
  const save = (next: PanelLayout) => {setLayout(next); writePanelLayout(ownerId, next);};
  // A person resizing the panel (the corner handle) changes its inline size; remember what it settles at.
  useEffect(() => {
    const node = panel.current;
    if (!open || !node || !wide || typeof ResizeObserver === 'undefined') return;
    let first = true;
    const observer = new ResizeObserver(() => {
      if (first) {first = false; return;}
      const box = node.getBoundingClientRect();
      setLayout(previous => {
        const next = {...previous, width: Math.round(box.width), height: Math.round(box.height)};
        writePanelLayout(ownerId, next);
        return next;
      });
    });
    observer.observe(node);
    return () => observer.disconnect();
  }, [open, wide, ownerId]);
  function startDrag(event: PointerEvent<HTMLButtonElement>) {
    if (!wide) return;
    drag.current = {x: event.clientX, y: event.clientY, from: layout};
    event.currentTarget.setPointerCapture?.(event.pointerId);
  }
  function moveDrag(event: PointerEvent<HTMLButtonElement>) {
    const start = drag.current;
    if (!start) return;
    setLayout({...start.from, x: clampOffset(start.from.x + event.clientX - start.x, window.innerWidth), y: clampOffset(start.from.y + event.clientY - start.y, window.innerHeight)});
  }
  function endDrag() {
    if (!drag.current) return;
    drag.current = null;
    writePanelLayout(ownerId, layout);
  }
  function moveKeys(event: KeyboardEvent<HTMLButtonElement>) {
    const delta = {ArrowLeft: [-STEP, 0], ArrowRight: [STEP, 0], ArrowUp: [0, -STEP], ArrowDown: [0, STEP]}[event.key as 'ArrowLeft'];
    if (!delta || !wide) return;
    event.preventDefault();
    save({...layout, x: clampOffset(layout.x + delta[0], window.innerWidth), y: clampOffset(layout.y + delta[1], window.innerHeight)});
  }
  const shownError = plainSqlError(error);
  return <Popover open={open} modal={false} onOpenChange={next => {if (!next) onClose();}}>
    <PopoverAnchor asChild><div className="data-advanced-anchor" aria-hidden="true"/></PopoverAnchor>
    <PopoverContent ref={panel} className="data-advanced-panel" align="start" sideOffset={8} aria-label="SQL editor"
      style={wide ? {translate: layout.x + 'px ' + layout.y + 'px', ...(layout.width ? {width: layout.width} : {}), ...(layout.height ? {height: layout.height} : {})} : undefined} onInteractOutside={event => event.preventDefault()}
      onOpenAutoFocus={event => {event.preventDefault(); if (focusRequest > consumed.current) {consumed.current = focusRequest; setFocusTick(value => value + 1);}}}
      onCloseAutoFocus={event => {event.preventDefault(); document.getElementById(ADVANCED_TRIGGER_ID)?.focus();}}>
      <div className="data-advanced-header"><h2 className="data-popover-title">{title}</h2>
        {wide && <Button variant="ghost" size="icon" className="data-advanced-move" aria-label="Move SQL editor" title="Drag, or use the arrow keys, to move the editor" onPointerDown={startDrag} onPointerMove={moveDrag} onPointerUp={endDrag} onPointerCancel={endDrag} onKeyDown={moveKeys}><GripHorizontal size={16} strokeWidth={1.5}/></Button>}
        {custom && <Button variant="ghost" size="icon" aria-label="Reset editor size and position" title="Reset size and position" onClick={() => save({x: 0, y: 0})}><RotateCcw size={16} strokeWidth={1.5}/></Button>}
        <Button variant="ghost" size="icon" aria-label="Close SQL editor" title="Close SQL editor" onClick={onClose}><X size={16} strokeWidth={1.5}/></Button></div>
      <Suspense fallback={<div className="data-skeleton-editor" role="status" aria-label="Loading editor"/>}>
        <SqlEditor value={sql} onChange={onSql} onRun={canRun ? onRun : undefined} disabled={locked} focusTick={focusTick}/>
      </Suspense>
      {shownError && <p className="data-advanced-error" role="alert">{shownError}</p>}
      {queries.length > 0 && onOpenQuery && <details className="data-parameters data-saved-queries"><summary>Saved queries ({queries.length})</summary>
        <ul className="data-saved-query-list">{queries.map(row => <li key={row.id}><button type="button" className="data-nav-row" aria-current={row.id === activeQueryId ? 'true' : undefined} onClick={() => onOpenQuery(row.id)}><span>{row.name}</span></button></li>)}</ul></details>}
      <details className="data-parameters"><summary>Query parameters</summary>
        <label>Parameters (JSON object)<textarea className="input" spellCheck={false} value={parameterText} onChange={event => onParameterText(event.target.value)} disabled={locked}/></label></details>
      <div className="data-advanced-actions">
        {onArchive && <Button variant="destructive" size="sm" aria-label={'Archive query ' + archiveName} onClick={onArchive} disabled={archiveDisabled}><Trash2 size={16} strokeWidth={1.5}/>Archive</Button>}
        <Button variant="secondary" size="sm" onClick={onSave} disabled={!canSave}><Save size={16} strokeWidth={1.5}/>Save query</Button>
        <span className="data-muted data-run-hint">{MOD}+Enter to run</span>
        {running && canCancel
          ? <Button variant="secondary" size="sm" onClick={onCancel}><Square size={16} strokeWidth={1.5}/>Cancel</Button>
          : <Button size="sm" onClick={onRun} disabled={!canRun}><Play size={16} strokeWidth={1.5}/>{runLabel}</Button>}
      </div>
    </PopoverContent>
  </Popover>;
}
