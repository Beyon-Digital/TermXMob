import {useEffect, useRef, useState, type CSSProperties, type KeyboardEvent} from 'react';
import {Ellipsis} from 'lucide-react';
import {Alert} from '../components/ui/alert';
import {Badge} from '../components/ui/badge';
import {Button} from '../components/ui/button';
import {FormField} from '../components/ui/form-field';
import {Select} from '../components/ui/select';
import {usePolling} from '../lib/use-polling';
import {
  STATUS_LABEL, boardChanges, canCancel, canReassign, canRetry, cancelNode, depths, elapsedLabel, loadBoard, loadModelChoices, nodeElapsed,
  outOfAttempts, reassignNode, retryNode, rollupCounts, rollupUsage, type Board, type BoardNode, type ModelChoice,
} from '../lib/orchestrator-board';
import './task-board.css';

type Action = 'retry' | 'reassign' | 'cancel';
/** Wording for one action while it runs and when it fails; the success message names the same verb. */
const COPY: Record<Action, {pending: string; next: string}> = {
  retry: {pending: 'Retrying…', next: 'Refresh the board to see its current state, then try again or reassign it to another model.'},
  reassign: {pending: 'Reassigning…', next: 'Pick a model from the list and try again, or refresh the board to see its current state.'},
  cancel: {pending: 'Cancelling…', next: 'Refresh the board to see its current state, then try again.'},
};
const WAITING_NOTE = {approval: 'Waiting for your approval. Open the task to approve or deny the action.', paused: 'Paused. Resume the task to continue.', recovery: 'Recovering after a restart. Open the task to confirm it can continue.'} as const;
const message = (reason: unknown) => (reason instanceof Error ? reason.message : String(reason)).trim().replace(/\.$/, '');
const triggerId = (nodeId: string) => `task-menu-${nodeId}-trigger`;
const choiceValue = (providerId: string | null, model: string | null) => providerId && model ? JSON.stringify([providerId, model]) : '';

function RowMenu({node, open, disabled, onToggle, onClose, onPick}: {node: BoardNode; open: boolean; disabled: boolean; onToggle: () => void; onClose: () => void; onPick: (action: Action) => void}) {
  const wrapper = useRef<HTMLDivElement>(null), trigger = useRef<HTMLButtonElement>(null);
  const items: {action: Action; label: string}[] = [
    ...(canRetry(node) ? [{action: 'retry' as const, label: 'Retry task'}] : []),
    ...(canReassign(node) ? [{action: 'reassign' as const, label: 'Reassign model'}] : []),
    ...(canCancel(node) ? [{action: 'cancel' as const, label: 'Cancel task'}] : []),
  ];
  useEffect(() => {
    if (!open) return;
    wrapper.current?.querySelector<HTMLElement>('[role="menuitem"]')?.focus();
    wrapper.current?.querySelector<HTMLElement>('[role="menu"]')?.scrollIntoView?.({block: 'nearest'});
    const outside = (event: PointerEvent) => { if (!wrapper.current?.contains(event.target as Node)) onClose(); };
    document.addEventListener('pointerdown', outside);
    return () => document.removeEventListener('pointerdown', outside);
  }, [open]);
  if (!items.length) return null;
  const move = (event: KeyboardEvent<HTMLDivElement>) => {
    const buttons = [...event.currentTarget.querySelectorAll<HTMLElement>('[role="menuitem"]')];
    const index = buttons.indexOf(document.activeElement as HTMLElement);
    const target = event.key === 'ArrowDown' ? (index + 1) % buttons.length : event.key === 'ArrowUp' ? (index - 1 + buttons.length) % buttons.length
      : event.key === 'Home' ? 0 : event.key === 'End' ? buttons.length - 1 : -1;
    if (target >= 0) { event.preventDefault(); buttons[target].focus(); }
    else if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); onClose(); trigger.current?.focus(); }
    else if (event.key === 'Tab') { trigger.current?.focus(); onClose(); }
  };
  const menuId = `task-menu-${node.id}`;
  return <div className="task-menu" ref={wrapper}>
    <Button ref={trigger} variant="ghost" size="icon" id={triggerId(node.id)} aria-label={`Actions for ${node.title}`} aria-haspopup="menu" aria-expanded={open} aria-disabled={disabled || undefined}
      aria-controls={open ? menuId : undefined} onClick={disabled ? undefined : onToggle}><Ellipsis size={15} aria-hidden="true"/></Button>
    {open && <div className="task-menu-list" id={menuId} role="menu" aria-labelledby={triggerId(node.id)} onKeyDown={move}>
      {items.map(item => <button key={item.action} type="button" role="menuitem" tabIndex={-1} data-danger={item.action === 'cancel' || undefined}
        onClick={() => { onClose(); trigger.current?.focus(); onPick(item.action); }}>{item.label}</button>)}
    </div>}
  </div>;
}

function ReassignForm({node, choices, error, busy, onSubmit, onCancel}: {node: BoardNode; choices: ModelChoice[] | null; error: string; busy: boolean; onSubmit: (providerId: string, model: string) => void; onCancel: () => void}) {
  const current = choiceValue(node.provider_id, node.model);
  const [value, setValue] = useState(current);
  const listed = !!current && !!choices?.some(group => group.provider_id === node.provider_id && node.model != null && group.models.includes(node.model));
  if (error) return <div className="task-reassign"><Alert role="alert" className="task-alert"><span>Could not load models. {error}. Check the connection to this host, then keep the current model or try again.</span><Button size="sm" variant="ghost" onClick={onCancel}>Keep current model</Button></Alert></div>;
  if (!choices) return <p className="task-note" role="status">Loading models…</p>;
  const selected = value ? JSON.parse(value) as [string, string] : null;
  return <form className="task-reassign" onKeyDown={event => { if (event.key === 'Escape') { event.stopPropagation(); onCancel(); } }} onSubmit={event => { event.preventDefault(); if (selected && value !== current) onSubmit(selected[0], selected[1]); }}>
    {choices.length ? <FormField label={`Model for ${node.title}`} hint="Models found on your connected providers.">
      {control => <Select {...control} autoFocus value={value} onChange={event => setValue(event.target.value)}>
        {!value && <option value="" disabled>Choose a model</option>}
        {current && !listed && <option value={current}>{node.model} (current)</option>}
        {choices.map(group => <optgroup key={group.provider_id} label={group.provider}>
          {group.models.map(model => <option key={model} value={JSON.stringify([group.provider_id, model])}>{model}</option>)}
        </optgroup>)}
      </Select>}
    </FormField> : <p className="task-note">No models available. Connect a provider and refresh its models in Settings, then reassign this task.</p>}
    <div className="task-reassign-actions">
      <Button type="submit" size="sm" disabled={busy || !selected || value === current}>Reassign model</Button>
      <Button type="button" size="sm" variant="ghost" onClick={onCancel}>Keep current model</Button>
    </div>
  </form>;
}

/** The main agent's planned tasks: an indented list with live status dots and per-row controls. */
export default function TaskBoard({enabled}: {enabled: boolean}) {
  const [board, setBoard] = useState<Board | null>(null), [loadError, setLoadError] = useState('');
  const [now, setNow] = useState(() => Date.now() / 1000), [announcement, setAnnouncement] = useState('');
  const [menuFor, setMenuFor] = useState<string | null>(null), [reassigning, setReassigning] = useState<string | null>(null);
  const [pending, setPending] = useState<Record<string, Action>>({}), [rowErrors, setRowErrors] = useState<Record<string, string>>({});
  const [choices, setChoices] = useState<ModelChoice[] | null>(null), [choicesError, setChoicesError] = useState('');
  const [refreshKey, setRefreshKey] = useState(0), [restoreFocus, setRestoreFocus] = useState<string | null>(null);
  const busy = useRef(new Set<string>()), latest = useRef<Board | null>(null);

  // Each board refresh also moves `now`, so elapsed labels stay current without a second timer.
  // A change the polling found (a task failed, stalled or started waiting on you) is announced as well as shown.
  const announce = (text: string) => setAnnouncement(previous => previous === text ? `${text}\u00a0` : text);
  const accept = (value: Board) => {
    const changes = boardChanges(latest.current, value);
    latest.current = value;
    setBoard(value); setNow(Date.now() / 1000); setLoadError('');
    if (changes.length) announce(changes.join(' '));
  };
  // Changing the key aborts a poll that started earlier, so it cannot overwrite a newer board, and fetches again at once.
  const refresh = () => setRefreshKey(key => key + 1);
  usePolling(() => loadBoard(), {interval: 4000, backoff: false, enabled, resetKey: refreshKey, onResult: accept, onError: reason => setLoadError(message(reason))});

  useEffect(() => {
    if (restoreFocus == null) return;
    document.getElementById(triggerId(restoreFocus))?.focus();
    setRestoreFocus(null);
  }, [restoreFocus]);

  async function openReassign(node: BoardNode) {
    setReassigning(node.id);
    if (choices) return;
    setChoicesError('');
    try { setChoices(await loadModelChoices()); } catch (reason) { setChoicesError(message(reason)); }
  }
  const closeReassign = (node: BoardNode) => { setReassigning(current => current === node.id ? null : current); setRestoreFocus(node.id); };
  async function run(node: BoardNode, action: Action, work: () => Promise<Board>, done: (next: Board) => string) {
    if (busy.current.has(node.id)) return;
    busy.current.add(node.id);
    setPending(all => ({...all, [node.id]: action}));
    setRowErrors(all => { const {[node.id]: _dropped, ...rest} = all; return rest; });
    try {
      const next = await work();
      accept(next);
      refresh();
      if (action === 'reassign') closeReassign(node);
      const text = done(next);
      announce(text);
    } catch (reason) {
      setRowErrors(all => ({...all, [node.id]: `Could not ${action} “${node.title}”. ${message(reason)}. ${COPY[action].next}`}));
    } finally {
      busy.current.delete(node.id);
      setPending(all => { const {[node.id]: _done, ...rest} = all; return rest; });
    }
  }
  /** The host cancels every dependent of a cancelled task, so the message counts the ones that changed. */
  const cancelled = (node: BoardNode, next: Board) => {
    const dependents = next.nodes.filter(row => row.status === 'cancelled' && row.id !== node.id && board?.nodes.find(old => old.id === row.id)?.status !== 'cancelled').length;
    return dependents ? `Cancelled “${node.title}” and ${dependents} ${dependents === 1 ? 'task that depends' : 'tasks that depend'} on it.` : `Cancelled “${node.title}”.`;
  };
  const pick = (node: BoardNode, action: Action) => {
    if (action === 'retry') void run(node, action, () => retryNode(node.id), () => `Retried “${node.title}”.`);
    else if (action === 'cancel') void run(node, action, () => cancelNode(node.id), next => cancelled(node, next));
    else void openReassign(node);
  };

  const nodes = board?.nodes || [], levels = depths(nodes), titles = new Map(nodes.map(node => [node.id, node.title]));
  const names = (ids: string[]) => ids.map(id => titles.get(id) || 'an earlier task').join(', ');
  return <section className="task-board" aria-label="Task board">
    <div className="visually-hidden" role="status" aria-label="Task board updates">{announcement}</div>
    {loadError && <Alert role="alert" className="task-alert"><span>{board ? `Could not refresh the task board. ${loadError}. Showing the last received state.` : `Could not load the task board. ${loadError}. Check the connection to this host, then refresh the board.`}</span>
      <Button size="sm" variant="ghost" onClick={() => { setLoadError(''); refresh(); }}>Refresh board</Button></Alert>}
    {!board && !loadError && <p className="task-note" role="status">Loading task board…</p>}
    {board && board.rollup.total === 0 && <p className="task-empty">No planned tasks yet. Ask your main agent to split a goal into tasks and they appear here.</p>}
    {board && board.rollup.total > 0 && <>
      <div className="task-rollup" role="group" aria-label="Task board summary">
        <div className="task-rollup-bar" role="progressbar" aria-label="Tasks complete" aria-valuemin={0} aria-valuemax={100} aria-valuenow={board.rollup.percent_complete}><span style={{width: `${board.rollup.percent_complete}%`}}/></div>
        <p><span>{board.rollup.percent_complete}% complete</span>
          {rollupCounts(board.rollup).map(item => <span key={item.status}>{item.count} {item.label.toLowerCase()}</span>)}
          <span>{rollupUsage(board.rollup)}</span></p>
      </div>
      <ul className="task-list" aria-label="Tasks">
        {nodes.map(node => {
          const action = pending[node.id], elapsed = nodeElapsed(node, now), stalled = node.status === 'stalled';
          const waiting = node.status === 'pending' && node.blocked_by.length > 0;
          return <li key={node.id} className="task-row" aria-label={node.title} aria-busy={action ? true : undefined} data-status={node.status} style={{'--depth': Math.min(levels.get(node.id) || 0, 4)} as CSSProperties}>
            <div className="task-row-main">
              <span className="task-dot" aria-hidden="true" data-status={node.status} data-live={node.status === 'running' || undefined}/>
              <span className="task-title">{node.title}</span>
              {stalled ? <Badge variant="secondary" className="task-stalled">Stalled</Badge> : <span className="task-status">{STATUS_LABEL[node.status] || node.status}</span>}
              {node.model && <Badge variant="secondary" className="task-chip" title={node.provider_id ? `${node.model} on ${node.provider_id}` : node.model}>{node.model}</Badge>}
              {elapsed && <span className="task-elapsed">{elapsed}</span>}
              <RowMenu node={node} open={menuFor === node.id && !action} disabled={!!action} onToggle={() => setMenuFor(menuFor === node.id ? null : node.id)} onClose={() => setMenuFor(null)} onPick={choice => pick(node, choice)}/>
            </div>
            {action && <p className="task-note" role="status">{COPY[action].pending}</p>}
            {(waiting || node.depends_on.length > 0) && <p className="task-note">{waiting ? `Waiting for ${names(node.blocked_by)}` : `Depends on ${names(node.depends_on)}`}</p>}
            {node.waiting && <p className="task-note">{WAITING_NOTE[node.waiting]}</p>}
            {stalled && node.last_event_at != null && <p className="task-note">No updates for {elapsedLabel(now - node.last_event_at).toLowerCase()}. Retry it, reassign it to another model, or cancel it.</p>}
            {node.error && <p className="task-note task-error">{node.error}</p>}
            {node.status === 'completed' && node.result_summary && <p className="task-note task-summary">{node.result_summary}</p>}
            {outOfAttempts(node) && <p className="task-note">Used all {node.max_attempts} attempts. Reassign it to another model{node.status === 'cancelled' ? ' to run it again.' : ' or cancel it.'}</p>}
            {rowErrors[node.id] && <Alert role="alert" className="task-alert"><span>{rowErrors[node.id]}</span>
              <Button size="sm" variant="ghost" onClick={() => { setRowErrors(all => { const {[node.id]: _dropped, ...rest} = all; return rest; }); setRestoreFocus(node.id); }}>Dismiss</Button></Alert>}
            {reassigning === node.id && <ReassignForm node={node} choices={choices} error={choicesError} busy={action === 'reassign'}
              onSubmit={(providerId, model) => void run(node, 'reassign', () => reassignNode(node.id, providerId, model), () => `Reassigned “${node.title}” to ${model}.`)}
              onCancel={() => closeReassign(node)}/>}
          </li>;
        })}
      </ul>
    </>}
  </section>;
}
