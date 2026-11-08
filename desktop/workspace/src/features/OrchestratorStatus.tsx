import {useEffect, useRef, useState} from 'react';
import {ArrowLeft, ChevronDown, ChevronRight, CircleAlert, GitBranch, RefreshCw} from 'lucide-react';
import {Button} from '../components/ui/button';
import {json, request, type Session} from '../lib/api';
import {loadBoard, rollupHeadline, type BoardRollup} from '../lib/orchestrator-board';
import {subscribePlanEvents} from '../lib/plan-signal';
import {taskState} from '../lib/task-presentation';
import {usePolling} from '../lib/use-polling';
import {useObservationActive} from '../lib/use-observation-active';
import OrchestratorDecisions from './OrchestratorDecisions';
import TaskBoard from './TaskBoard';
import './orchestrator-status.css';

export type CoordinationLimits = {
  time_budget?: string; tool_steps?: string; tokens?: string; cost?: string;
  steer?: string; resume?: string; approvals?: string; tools_filter?: string;
  elapsed_deadline_seconds?: number;
};
export type OrchestratorChild = {
  session_id: string; task_id: string | null; title: string; status: string;
  engine: string; origin: 'native' | 'adopted' | 'delegated'; capability_limits: CoordinationLimits;
  result?: string | null; error?: string | null; control_owner: 'user' | 'orchestrator';
  pending_approvals?: number;
  revision: number; updated_at: number; parent_session_id: string;
};
export type OrchestratorActivity = {
  id: string; sequence: number; kind: string; session_id: string; task_id?: string | null;
  status?: string; message: string; created_at: number;
};
export type OrchestratorSnapshot = {
  session: Session | null; children: OrchestratorChild[]; activity: OrchestratorActivity[]; cursor: number; children_truncated?: boolean;
};
type Props = {
  ownerId: string; selected: string; enabled: boolean; onOpen: (id: string) => void;
  onPrimary?: (id: string) => void; onRefresh?: () => void;
};

function engineLabel(engine?: string) {
  return ({internal: 'TermX Agent', codex: 'Codex', claude: 'Claude', acp: 'ACP agent'} as Record<string, string>)[engine || ''] || engine || 'Engine not reported';
}
function childState(child: OrchestratorChild) {
  const state = taskState(child.status);
  return !child.task_id && child.status === 'idle' ? {...state, label: 'No task started'} : state;
}
function capabilityLabel(value: string) {
  return ({native: 'Native engine support', gateway: 'TermX gateway', os: 'Operating system', advisory: 'Advisory only', unsupported: 'Unavailable', unverified: 'Not verified'} as Record<string, string>)[value] || value.replaceAll('_', ' ');
}
function ChildEngineDetails({child}: {child: OrchestratorChild}) {
  const limits = child.capability_limits;
  if (!limits) return null;
  const deadline = limits.elapsed_deadline_seconds;
  return <details className="orchestrator-engine-details" aria-label={'Engine details for ' + child.title}>
    <summary>Engine capabilities &amp; limits</summary>
    {child.control_owner === 'user' && <p>Coordinator monitoring applies after you give the main agent control. The engine retains its own settings.</p>}
    <dl>
      <dt>Engine</dt><dd>{engineLabel(child.engine)}</dd>
      {limits.time_budget && <><dt>Time allowance</dt><dd>{limits.time_budget === 'shared-host' ? 'Shared host time budget' : limits.time_budget === 'host-monitoring' ? 'Host monitoring; existing task limits are retained' : capabilityLabel(limits.time_budget)}</dd></>}
      {limits.tool_steps && <><dt>Work allowance</dt><dd>{limits.tool_steps === 'shared-host' ? 'Shared host tool-step budget' : limits.tool_steps === 'native-max-turns' ? 'Native turn limit; individual tool calls are not counted' : limits.tool_steps === 'unmeasured' ? 'Tool steps are not measured by TermX' : capabilityLabel(limits.tool_steps)}</dd></>}
      {limits.tokens && <><dt>Token usage</dt><dd>{limits.tokens === 'unmeasured' ? 'Not measured or capped by TermX' : capabilityLabel(limits.tokens)}</dd></>}
      {limits.cost && <><dt>Cost</dt><dd>{limits.cost === 'unmeasured' ? 'Not measured or capped by TermX' : capabilityLabel(limits.cost)}</dd></>}
      {([['steer', 'Steering'], ['resume', 'Resume'], ['approvals', 'Approvals'], ['tools_filter', 'Tool restrictions']] as const).map(([key, label]) => limits[key] ? <div key={key}><dt>{label}</dt><dd>{capabilityLabel(limits[key]!)}</dd></div> : null)}
      {typeof deadline === 'number' && Number.isFinite(deadline) && deadline > 0 && <><dt>Elapsed-time safety ceiling</dt><dd>{deadline % 3600 === 0 ? `${deadline / 3600} hours` : `${deadline} seconds`} while the main agent is in control</dd></>}
    </dl>
  </details>;
}

/** How often the collapsed header re-reads the plan graph: quickly while work is live, slowly when nothing is planned or running. */
export const COLLAPSED_BOARD_LIVE_MS = 4000, COLLAPSED_BOARD_IDLE_MS = 15000, PLAN_EVENT_DEBOUNCE_MS = 300;
/** A plan with nodes that are running, waiting to run, or stalled is still changing. */
export const boardIsLive = (board: BoardRollup | null) => !!board && ['running', 'pending', 'stalled'].some(status => (board.counts[status] || 0) > 0);

/** The primary chat owns coordination; inspecting a child never starts work. */
export default function OrchestratorStatus({ownerId, selected, enabled, onOpen, onPrimary, onRefresh}: Props) {
  const observing = useObservationActive(enabled);
  const [snapshot, setSnapshot] = useState<OrchestratorSnapshot | null>(null), [receivedOwner, setReceivedOwner] = useState('');
  const [expanded, setExpanded] = useState(false), [error, setError] = useState(''), [readError, setReadError] = useState('');
  const [stale, setStale] = useState(false), [busy, setBusy] = useState(''), [stateOwner, setStateOwner] = useState(ownerId);
  const [board, setBoard] = useState<BoardRollup | null>(null);
  useEffect(() => setBoard(null), [ownerId]);
  // A plan change made through the main chat (plan_nodes, start_ready, retry_node, cancel_node, an approval) moves no orchestrator
  // cursor. The chat's task event stream reports it instead; a short debounce turns a burst into one restart of the board loop.
  const [planTick, setPlanTick] = useState(0);
  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | undefined;
    const unsubscribe = subscribePlanEvents(() => { if (!timer) timer = setTimeout(() => { timer = undefined; setPlanTick(value => value + 1); }, PLAN_EVENT_DEBOUNCE_MS); });
    return () => { unsubscribe(); clearTimeout(timer); };
  }, []);
  // The board is mounted only while the section is open. While it is collapsed, read the same board so the header
  // counts planned tasks too; once open, the board itself feeds the header.
  // Poll every 4 s while delegated work or a plan is live so the collapsed header tracks the plan graph, and every 15 s when idle.
  // A new orchestrator event (the snapshot cursor moving) or a plan event from the main chat restarts the loop, which reads the board at once.
  const live = (snapshot?.children || []).some(row => taskState(row.status).active) || boardIsLive(board);
  usePolling(() => loadBoard(), {interval: live ? COLLAPSED_BOARD_LIVE_MS : COLLAPSED_BOARD_IDLE_MS, backoff: false, enabled: observing && !expanded && selected !== undefined, resetKey: ownerId + ':' + (snapshot?.cursor ?? '') + ':' + planTick, onResult: value => setBoard(value.rollup)});
  const read = useRef<AbortController | null>(null), flight = useRef<object | null>(null);
  const generation = useRef(0), identity = useRef(ownerId), alive = useRef(true);
  const observation = useRef(observing), selection = useRef(selected), primaryCallback = useRef(onPrimary);
  observation.current = observing; selection.current = selected; primaryCallback.current = onPrimary;
  if (identity.current !== ownerId) { identity.current = ownerId; generation.current++; }
  const fresh = (owner: string, ticket: number) => alive.current && identity.current === owner && generation.current === ticket;

  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  useEffect(() => {
    read.current?.abort(); read.current = null; flight.current = null;
    setStateOwner(ownerId); setSnapshot(null); setReceivedOwner(''); setExpanded(false); setError(''); setReadError(''); setStale(false); setBusy('');
  }, [ownerId]);

  async function refresh() {
    if (!observation.current || read.current && !read.current.signal.aborted) return;
    const owner = ownerId, ticket = generation.current, controller = new AbortController();
    read.current = controller;
    try {
      const value = await request<OrchestratorSnapshot>('/api/workspace/orchestrator', {signal: controller.signal});
      if (!controller.signal.aborted && observation.current && fresh(owner, ticket)) {
        setSnapshot(value); setReceivedOwner(owner); setStale(false); setReadError('');
        if (value.session?.id) primaryCallback.current?.(value.session.id);
      }
    } catch (reason) {
      if (!controller.signal.aborted && observation.current && fresh(owner, ticket)) {
        setStale(true); setReadError(reason instanceof Error ? reason.message : String(reason));
      }
    } finally { if (read.current === controller) read.current = null; }
  }

  useEffect(() => {
    if (!observing) return;
    void refresh();
    const timer = setInterval(() => void refresh(), 4000);
    return () => { clearInterval(timer); read.current?.abort(); };
  }, [ownerId, observing]);

  async function changeControl(child: OrchestratorChild) {
    if (flight.current || !observation.current || stale) return;
    const owner = ownerId, ticket = generation.current, selectedAtStart = selected, token = {};
    const next = child.control_owner === 'user' ? 'orchestrator' : 'user';
    flight.current = token; setBusy(child.session_id); setError('');
    try {
      const value = await request<OrchestratorChild>('/api/workspace/orchestrator/children/' + encodeURIComponent(child.session_id) + '/control', json('POST', {owner: next, revision: child.revision}));
      if (fresh(owner, ticket)) {
        setSnapshot(current => current && ({...current, children: current.children.map(row => row.session_id === value.session_id ? value : row)}));
        onRefresh?.();
        // A late ownership response must never navigate away from the user's newer selection.
        if (selection.current === selectedAtStart && next === 'orchestrator' && snapshot?.session) onOpen(snapshot.session.id);
      }
    } catch (reason) {
      if (fresh(owner, ticket)) {
        setError(reason instanceof Error ? reason.message : String(reason));
        // Resolve current ownership after a conflict or uncertain outcome. Never replay the mutation.
        void refresh();
      }
    } finally { if (flight.current === token) { flight.current = null; if (fresh(owner, ticket)) setBusy(''); } }
  }

  const current = receivedOwner === ownerId ? snapshot : null;
  const primary = current?.session, children = current?.children || [];
  const failure = stateOwner === ownerId ? error || readError : '';
  const isStale = stateOwner === ownerId && stale;
  const child = children.find(row => row.session_id === selected);
  if (current && !primary || primary && selected && selected !== primary.id && !child) return null;
  const active = children.filter(row => taskState(row.status).active).length;
  const attention = children.filter(row => taskState(row.status).attention).length;
  const recent = [...(current?.activity || [])].slice(-12).reverse();
  const botReports = recent.filter(item => item.kind === 'bot.message').length;
  const ordered = [...children].sort((a, b) => Number(taskState(b.status).attention) - Number(taskState(a.status).attention) || b.updated_at - a.updated_at);
  return <section className={'orchestrator-status' + (child ? ' orchestrator-child-view' : '')} aria-label="Main agent coordination">
    {child ? <div className="orchestrator-child-banner">
      <Button variant="ghost" size="sm" onClick={() => primary && onOpen(primary.id)}><ArrowLeft size={13}/>Main agent</Button>
      <span>{child.control_owner === 'user' ? 'You are directing this chat' : 'Managed by your main agent'}</span>
      <Button variant="outline" size="sm" disabled={!!busy || isStale || !observing} onClick={() => void changeControl(child)}>{busy === child.session_id ? 'Updating…' : child.control_owner === 'user' ? child.origin === 'adopted' ? 'Give main agent control' : 'Return to main agent' : 'Take control'}</Button>
      <p>{child.origin === 'adopted' && child.control_owner === 'user' ? 'This existing chat is included in updates. You stay in control until you give the main agent control.' : child.control_owner === 'user' ? 'The main agent can see progress, but waits for you to return control before steering this chat.' : 'Take control to direct this agent yourself. Its progress still returns to your main chat.'}</p>
      <p className="orchestrator-engine-label">Engine: {engineLabel(child.engine)}{child.origin === 'adopted' ? ' · Existing chat' : ''}</p>
      <ChildEngineDetails child={child}/>
    </div> : <div className="orchestrator-summary">
      <Button variant="ghost" size="sm" className="orchestrator-disclosure" aria-expanded={expanded} aria-controls="orchestrator-detail" onClick={() => setExpanded(value => !value)}>
        {expanded ? <ChevronDown size={14}/> : <ChevronRight size={14}/>}<GitBranch size={14}/><span>Delegated work</span>
        <small role="status">{isStale ? 'Updates interrupted' : !current ? 'Connecting…' : attention ? `${attention} need attention` : active ? `${active} in progress` : board?.total ? rollupHeadline(board) : children.length ? `${children.length} finished or idle` : botReports ? `${botReports} ${botReports === 1 ? 'report' : 'reports'} from bots` : 'No delegated chats yet'}</small>
      </Button>
      {attention > 0 && !expanded && <Button variant="ghost" size="sm" onClick={() => setExpanded(true)}><CircleAlert size={13}/>Review</Button>}
    </div>}
    {failure && <div className="orchestrator-error" role="alert"><span>{failure}{isStale && current ? ' Showing the last received state.' : ''}</span><Button variant="ghost" size="sm" disabled={!observing || !!busy} onClick={() => { setError(''); setReadError(''); void refresh(); }}><RefreshCw size={12}/>Refresh status</Button></div>}
    {!child && primary && selected === primary.id && <OrchestratorDecisions ownerId={ownerId} children={children} enabled={observing && !isStale} onRefresh={() => {void refresh(); onRefresh?.();}}/>}
    {!child && expanded && <div id="orchestrator-detail" className="orchestrator-detail">
      <p>Your main agent coordinates delegated work here. Open a chat to inspect it; take control when you want to direct it yourself.</p>
      <TaskBoard enabled={observing && !isStale} onSummary={setBoard}/>
      {!current && !failure && <p role="status">Loading delegated work…</p>}
      {current && !children.length && !botReports && <p className="orchestrator-empty">Ask your main agent to delegate a task. Results, blockers, and requests for decisions will appear here.</p>}
      {current?.children_truncated && <p>Showing up to 100 delegated chats, prioritizing active work. Other conversations remain available in chat history.</p>}
      {ordered.length > 0 && <ul className="orchestrator-children">{ordered.map(row => <li key={row.session_id}>
        <button className="orchestrator-child-link" onClick={() => onOpen(row.session_id)}><strong>{row.title || 'Delegated task'}</strong><span className={'orchestrator-state ' + childState(row).tone}>{childState(row).label}</span><ChevronRight size={13}/></button>
        <small>{row.control_owner === 'user' ? 'You are in control' : 'Main agent is coordinating'}</small>
        <small className="orchestrator-engine-label">Engine: {engineLabel(row.engine)}{row.origin === 'adopted' ? ' · Existing chat' : ''}</small>
        {row.error ? <p className="orchestrator-child-error">{row.error}</p> : row.result ? <p>{row.result}</p> : null}
        <ChildEngineDetails child={row}/>
      </li>)}</ul>}
      {recent.length > 0 && <details className="orchestrator-activity"><summary>Recent updates</summary><ol>{recent.map(item => <li key={item.id || item.sequence}><time dateTime={new Date(item.created_at * 1000).toISOString()}>{new Date(item.created_at * 1000).toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'})}</time><span>{item.message}</span>{item.session_id && <button onClick={() => onOpen(item.session_id)}>Open chat</button>}</li>)}</ol></details>}
    </div>}
  </section>;
}
