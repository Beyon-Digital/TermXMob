import {useEffect, useLayoutEffect, useRef, useState, type ReactNode} from 'react';
import {ChevronRight, GitBranch, RefreshCw} from 'lucide-react';
import {AgentGlyph} from '../components/icons/AgentGlyph';
import {Button} from '../components/ui/button';
import {Collapsible, CollapsibleContent, CollapsibleTrigger} from '../components/ui/collapsible';
import {rollupHeadline, type BoardRollup} from '../lib/orchestrator-board';
import {taskState} from '../lib/task-presentation';
import type {OrchestratorActivity, OrchestratorChild} from './OrchestratorStatus';
import './coordination-sidebar.css';

const terminalStates = new Set(['completed', 'failed', 'cancelled']);
export const subagentFinished = (child: {status: string}) => terminalStates.has(child.status);
export const subagentNeedsAttention = (child: {status: string; pending_approvals?: number}) => taskState(child.status).attention || (child.pending_approvals || 0) > 0;

/** The host reports an update time, not a start time or a task duration. */
export function subagentAge(updatedAt: number, now = Date.now() / 1000) {
  if (!Number.isFinite(updatedAt) || updatedAt <= 0) return '—';
  const seconds = Math.max(0, Math.floor(now - updatedAt));
  if (seconds < 60) return 'now';
  if (seconds < 3600) return Math.floor(seconds / 60) + 'm';
  if (seconds < 86400) return Math.floor(seconds / 3600) + 'h';
  return Math.floor(seconds / 86400) + 'd';
}

export type CoordinationSidebarItem = {id: string; title: string; status: string; updated_at: number; task_id?: string|null; pending_approvals?: number};

type Props = {
  ownerId: string; children: OrchestratorChild[]; items?: CoordinationSidebarItem[]; showPlans?: boolean; note?: string; truncatedNote?: string; selected: string; loading: boolean; stale: boolean;
  error: string; enabled: boolean; truncated?: boolean; board: BoardRollup | null; boardError: string;
  planOpen: boolean; onPlanOpenChange: (open: boolean) => void; planContent: ReactNode;
  activity: OrchestratorActivity[]; onOpen: (id: string) => void; onRefresh: () => void;
};

function AgentGroup({label, rows, selected, limit, onMore, onOpen}: {
  label: 'Active'|'Done'; rows: CoordinationSidebarItem[]; selected: string; limit: number;
  onMore: () => void; onOpen: (id: string) => void;
}) {
  const group = useRef<HTMLElement>(null), revealFocus = useRef<number|null>(null);
  useLayoutEffect(() => {
    const index = revealFocus.current;
    if (index === null || limit <= index) return;
    revealFocus.current = null;
    const target = group.current?.querySelector<HTMLButtonElement>('[data-subagent-index="' + index + '"]');
    if (target && !target.closest('[hidden],[inert]')) target.focus();
  }, [limit]);
  return <section ref={group} className="coordination-agent-group" aria-label={label + ' subagents'}>
    <h3>{label}<span>{rows.length}</span></h3>
    {!rows.length ? <p className="coordination-group-empty">{label === 'Active' ? 'No active subagents' : 'No finished subagents'}</p> : <ul>
      {rows.slice(0, limit).map((child, index) => {
        const status = !child.task_id && child.status === 'idle' ? 'Not started' : taskState(child.status).label;
        const label = child.title || 'Untitled subagent';
        const updated = Number.isFinite(child.updated_at) && child.updated_at > 0 ? new Date(child.updated_at * 1000) : null;
        return <li key={child.id}>
          <button type="button" className="coordination-agent-row" aria-label={'Open ' + label} aria-current={selected === child.id ? 'page' : undefined}
            data-status={child.status} data-subagent-index={index} data-subagent-id={child.id} onClick={() => onOpen(child.id)}>
            <AgentGlyph seed={child.id} size={28}/>
            <span className="coordination-agent-copy"><strong>{label}</strong>{child.status !== 'completed' && <small>{status}{(child.pending_approvals || 0) > 0 && child.status !== 'awaiting_approval' ? ' · Needs approval' : ''}</small>}</span>
            {updated && Number.isFinite(updated.getTime()) ? <time dateTime={updated.toISOString()} title={'Updated ' + updated.toLocaleString()}>{subagentAge(child.updated_at)}</time> : <span className="coordination-agent-age" title="Update time unavailable">—</span>}
          </button>
        </li>;
      })}
    </ul>}
    {rows.length > limit && <Button variant="ghost" size="sm" className="coordination-show-more" aria-label={'Show more ' + label.toLowerCase() + ' subagents'} onClick={() => {revealFocus.current = limit; onMore();}}>Show more <span>{rows.length - limit}</span></Button>}
  </section>;
}

/** Presentation only: the parent owns the single coordination observer and board. */
export default function CoordinationSidebar({ownerId, children, items, showPlans = true, note, truncatedNote, selected, loading, stale, error, enabled, truncated, board, boardError, planOpen, onPlanOpenChange, planContent, activity, onOpen, onRefresh}: Props) {
  const [activeLimit, setActiveLimit] = useState(10), [doneLimit, setDoneLimit] = useState(10);
  const root = useRef<HTMLDivElement>(null), focusedAgent = useRef<string|null>(null), focusOwner = useRef(ownerId);
  if (focusOwner.current !== ownerId) {focusOwner.current = ownerId; focusedAgent.current = null;}
  // A status update can move a focused row from Active to Done. Preserve that
  // same agent's focus without taking focus back from another app control.
  useLayoutEffect(() => {
    if (!enabled || !focusedAgent.current || document.activeElement !== document.body || !document.hasFocus()) return;
    const target = [...(root.current?.querySelectorAll<HTMLButtonElement>('[data-subagent-id]') || [])].find(node => node.dataset.subagentId === focusedAgent.current);
    if (target && !target.closest('[hidden],[inert]')) target.focus();
  }, [children, items, enabled]);
  useEffect(() => {setActiveLimit(10); setDoneLimit(10);}, [ownerId]);
  const rows = items || children.map(child => ({...child, id: child.session_id}));
  const ordered = [...rows].sort((a, b) => Number(subagentNeedsAttention(b)) - Number(subagentNeedsAttention(a)) || b.updated_at - a.updated_at || a.id.localeCompare(b.id));
  const active = ordered.filter(child => !subagentFinished(child)), done = ordered.filter(subagentFinished);
  return <div ref={root} onFocus={event => {const row = (event.target as HTMLElement).closest<HTMLElement>('[data-subagent-id]'); focusedAgent.current = row?.dataset.subagentId || null;}} onBlur={() => {focusedAgent.current = null;}} className="coordination-sidebar" aria-label="Subagent activity" aria-busy={loading || undefined}>
    {note && <p className="coordination-sidebar-note">{note}</p>}
    {loading ? <p className="coordination-sidebar-note" role="status">Loading subagents…</p> : <>
      {(stale || error) && <div className="coordination-sidebar-notice"><p>{rows.length ? 'Updates interrupted. Showing the last received agents.' : 'Subagents could not be loaded.'}</p><Button variant="ghost" size="sm" disabled={!enabled} onClick={onRefresh}><RefreshCw size={14}/>Refresh subagents</Button></div>}
      {!enabled && <p className="coordination-sidebar-note" role="status">Updates paused</p>}
      <AgentGroup label="Active" rows={active} selected={selected} limit={activeLimit} onMore={() => setActiveLimit(value => value + 10)} onOpen={onOpen}/>
      <AgentGroup label="Done" rows={done} selected={selected} limit={doneLimit} onMore={() => setDoneLimit(value => value + 10)} onOpen={onOpen}/>
      {truncated && <p className="coordination-sidebar-note">{truncatedNote || 'Showing up to 100 subagents. Other conversations remain in chat history.'}</p>}
    </>}
    {showPlans && <Collapsible className="coordination-sidebar-plans" open={planOpen} onOpenChange={onPlanOpenChange}>
      <CollapsibleTrigger className="coordination-plan-trigger"><GitBranch size={16} aria-hidden="true"/><span>Plans</span>{board && <small>{board.total}</small>}<ChevronRight size={16} aria-hidden="true"/></CollapsibleTrigger>
      {!planOpen && board && board.total > 0 && <p className="coordination-plan-summary">{rollupHeadline(board)}</p>}
      {!planOpen && boardError && <p className="coordination-sidebar-note">Plan updates unavailable. Open Plans to retry.</p>}
      <CollapsibleContent className="coordination-plan-content">{planOpen && planContent}</CollapsibleContent>
    </Collapsible>}
    {activity.length > 0 && <details className="coordination-sidebar-recent"><summary>Recent activity</summary><ol>{activity.map(item => <li key={item.id || item.sequence}><span>{item.message}</span>{item.session_id && <button type="button" onClick={() => onOpen(item.session_id)}>Open chat</button>}</li>)}</ol></details>}
  </div>;
}
