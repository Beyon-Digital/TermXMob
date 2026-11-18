import {rememberTerminal, recalledTerminal} from '../lib/window-state';
import {readTerminalSelection, saveTerminalSelection} from '../lib/terminal-selection';
import {useCallback, useEffect, useMemo, useRef, useState} from 'react';
import {ChevronDown, ChevronUp, CircleStop, Plus, Trash2, X} from 'lucide-react';
import {gql, request, type Project} from '../lib/api';
import {Button} from '../components/ui/button';
import {Tabs, TabsContent} from '../components/ui/tabs';
import TerminalSession, {type SessionHandle, type SessionStatus} from './terminal/TerminalSession';
import TerminalTabs from './terminal/TerminalTabs';
import './terminal/terminal.css';

type Pty = {id: string; title: string; cwd: string; exited: boolean};
const SESSIONS = 'query{sessions{id title cwd exited}}';

/** Distinct tab names: the host titles every terminal in a project the same, so repeats are numbered. */
function tabLabels(rows: Pty[]) {
  const seen = new Map<string, number>();
  const total = new Map<string, number>();
  for (const row of rows) total.set(row.title, (total.get(row.title) ?? 0) + 1);
  return rows.map((row) => {
    const n = (seen.get(row.title) ?? 0) + 1;
    seen.set(row.title, n);
    return {id: row.id, label: (total.get(row.title) ?? 0) > 1 ? row.title + ' ' + n : row.title};
  });
}

export default function TerminalPanel({project, onError, workspaceSession, executionRoot, ownerId}: {ownerId?: string; workspaceSession?: string; executionRoot?: string; project: Project | null; onError: (error: unknown) => void}) {
  const [sessions, setSessions] = useState<Pty[]>([]);
  const [selected, setSelected] = useState('');
  const [visited, setVisited] = useState<string[]>([]);
  const [creating, setCreating] = useState(false);
  const [closing, setClosing] = useState('');
  const [locked, setLocked] = useState(false);
  const [status, setStatus] = useState<Record<string, SessionStatus>>({});
  const [transportEpoch, setTransportEpoch] = useState(0);
  const [inventoryAuthorization, setInventoryAuthorization] = useState('');
  const [focusTick, setFocusTick] = useState(0);
  const [finding, setFinding] = useState(false);
  const [term, setTerm] = useState('');
  const [matches, setMatches] = useState<{index: number; count: number} | null>(null);

  const paused = useRef(false);
  const inventoryTarget = useRef('');
  const selectionTarget = useRef('');
  const handles = useRef(new Map<string, SessionHandle>());
  const tablist = useRef<HTMLDivElement>(null);
  const addButton = useRef<HTMLButtonElement>(null);
  const findInput = useRef<HTMLInputElement>(null);
  const selectedRef = useRef(selected);
  selectedRef.current = selected;

  const targetKey = JSON.stringify([project?.id, project?.path, executionRoot, workspaceSession, ownerId]);
  const transportTarget = targetKey + ':' + transportEpoch;
  const targetGeneration = useRef({key: transportTarget, generation: 0});
  if (targetGeneration.current.key !== transportTarget) targetGeneration.current = {key: transportTarget, generation: targetGeneration.current.generation + 1};
  const authorized = !locked && inventoryAuthorization === transportTarget;

  useEffect(() => {
    const lock = () => { paused.current = true; setLocked(true); setCreating(false); setTransportEpoch((value) => value + 1); };
    const unlock = () => { paused.current = false; setLocked(false); setTransportEpoch((value) => value + 1); };
    window.addEventListener('termx-locked', lock);
    window.addEventListener('termx-session-unlocked', unlock);
    return () => { window.removeEventListener('termx-locked', lock); window.removeEventListener('termx-session-unlocked', unlock); };
  }, []);

  // The host inventory decides which PTYs this window may attach to. Nothing connects until it has answered for the current target.
  useEffect(() => {
    if (paused.current) return;
    const target = JSON.stringify([project?.id, project?.path, executionRoot, workspaceSession, ownerId]);
    if (inventoryTarget.current !== target) {
      inventoryTarget.current = target;
      setCreating(false);
      setSelected('');
      setVisited([]);
      setStatus({});
    }
    let cancelled = false;
    gql<{sessions: Pty[]}>(SESSIONS).then((data) => {
      if (cancelled || paused.current) return;
      const allowed = data.sessions.filter((row) => !row.exited && (!project || row.cwd === (executionRoot || project.path)));
      setSessions(allowed);
      const remembered = workspaceSession ? (recalledTerminal(workspaceSession) || (ownerId ? readTerminalSelection(ownerId, workspaceSession) : '')) : '';
      selectionTarget.current = workspaceSession || '';
      setSelected(allowed.some((row) => row.id === remembered) ? remembered : '');
      setInventoryAuthorization(transportTarget);
    }).catch((error) => { if (!cancelled && !paused.current) onError(error); });
    return () => { cancelled = true; };
  }, [project?.id, project?.path, executionRoot, workspaceSession, ownerId, transportEpoch]);

  useEffect(() => {
    let current = true;
    const handoff = (event: Event) => {
      const detail = (event as CustomEvent<{sessionId: string; id: string}>).detail;
      if (!current || paused.current || detail.sessionId !== workspaceSession) return;
      gql<{sessions: Pty[]}>(SESSIONS).then((data) => {
        if (!current || paused.current) return;
        const allowed = data.sessions.filter((row) => !row.exited && row.cwd === (executionRoot || project?.path));
        if (!allowed.some((row) => row.id === detail.id)) throw new Error('Transferred terminal is no longer available in this checkout');
        selectionTarget.current = workspaceSession || '';
        setSessions(allowed);
        setSelected(detail.id);
        setInventoryAuthorization(transportTarget);
      }).catch((error) => { if (current && !paused.current) onError(error); });
    };
    window.addEventListener('termx-terminal-handoff', handoff);
    return () => { current = false; window.removeEventListener('termx-terminal-handoff', handoff); };
  }, [workspaceSession, executionRoot, project?.id, ownerId, transportEpoch]);

  useEffect(() => {
    if (workspaceSession && selected && selectionTarget.current === workspaceSession) {
      rememberTerminal(workspaceSession, selected);
      if (ownerId) saveTerminalSelection(ownerId, workspaceSession, selected);
    }
  }, [workspaceSession, selected, ownerId]);

  // A session is attached the first time its tab is selected, then stays mounted and connected while another tab is in front.
  useEffect(() => { if (selected) setVisited((rows) => (rows.includes(selected) ? rows : [...rows, selected])); }, [selected]);
  const mounted = useMemo(() => sessions.filter((row) => row.id === selected || visited.includes(row.id)), [sessions, visited, selected]);

  useEffect(() => { if (focusTick) handles.current.get(selectedRef.current)?.focus(); }, [focusTick]);
  // A tab chosen elsewhere (a new terminal, a handoff) may sit past the visible end of a scrolling strip.
  useEffect(() => { tablist.current?.querySelector<HTMLElement>('[role=tab][data-state=active]')?.scrollIntoView?.({block: 'nearest', inline: 'nearest'}); }, [selected, sessions.length]);

  async function create() {
    if (!project || paused.current) return;
    const generation = targetGeneration.current.generation;
    setCreating(true);
    try {
      const data = workspaceSession
        ? {create_session: await request<Pty>('/api/workspace/sessions/' + workspaceSession + '/terminal', {method: 'POST'})}
        : await gql<{create_session: Pty}>('mutation($input:CreateSessionInput!){create_session(input:$input){id title cwd exited}}', {input: {cwd: project.path, sandbox_profile: 'workspace', title: project.name}});
      if (targetGeneration.current.generation !== generation || paused.current) return;
      selectionTarget.current = workspaceSession || '';
      setSessions((rows) => [...rows, data.create_session]);
      setSelected(data.create_session.id);
      setInventoryAuthorization(transportTarget);
      setFocusTick((value) => value + 1);
    } catch (error) {
      if (targetGeneration.current.generation === generation && !paused.current) onError(error);
    } finally {
      if (targetGeneration.current.generation === generation) setCreating(false);
    }
  }

  async function close(id: string) {
    if (paused.current || closing) return;
    const generation = targetGeneration.current.generation;
    setClosing(id);
    try {
      await gql<{delete_session: {ok: boolean}}>('mutation($id:String!){delete_session(session_id:$id){ok}}', {id});
      if (targetGeneration.current.generation !== generation) return;
      const index = sessions.findIndex((row) => row.id === id);
      const rest = sessions.filter((row) => row.id !== id);
      setSessions(rest);
      setVisited((rows) => rows.filter((row) => row !== id));
      setStatus((rows) => { const {[id]: _gone, ...others} = rows; return others; });
      if (selectedRef.current === id) {
        selectionTarget.current = workspaceSession || '';
        setSelected(rest[Math.min(index, rest.length - 1)]?.id ?? '');
      }
    } catch (error) {
      if (targetGeneration.current.generation === generation && !paused.current) onError(error);
    } finally {
      if (targetGeneration.current.generation === generation) setClosing('');
    }
  }

  const choose = (id: string) => { selectionTarget.current = workspaceSession || ''; setSelected(id); };
  const onStatus = useCallback((id: string, next: SessionStatus) => {
    setStatus((rows) => (rows[id]?.state === next.state && rows[id]?.connected === next.connected ? rows : {...rows, [id]: next}));
  }, []);
  const onResults = useCallback((id: string, index: number, count: number) => { if (id === selectedRef.current) setMatches({index, count}); }, []);
  const leave = useCallback(() => {
    const list = tablist.current;
    (list?.querySelector<HTMLElement>('[role=tab][data-state=active]') ?? list?.querySelector<HTMLElement>('[role=tab]') ?? addButton.current)?.focus();
  }, []);
  const openFind = useCallback(() => { setFinding(true); requestAnimationFrame(() => { findInput.current?.focus(); findInput.current?.select(); }); }, []);

  function closeFind() {
    const handle = handles.current.get(selectedRef.current);
    handle?.clearSearch();
    setFinding(false);
    setMatches(null);
    handle?.focus();
  }
  function runFind(value: string, backwards = false) {
    const handle = handles.current.get(selectedRef.current);
    if (!handle) return;
    if (!value) { handle.clearSearch(); setMatches(null); return; }
    const found = backwards ? handle.findPrevious(value) : handle.findNext(value);
    if (!found) setMatches({index: -1, count: 0});
  }

  const connectedId = !locked && status[selected]?.connected ? selected : '';
  const label = locked ? 'Locked' : status[selected]?.state ?? 'Disconnected';
  const tabs = tabLabels(sessions);
  const result = !term ? '' : matches && matches.count === 0 ? 'No matches' : matches && matches.index >= 0 ? matches.index + 1 + ' of ' + (matches.count >= 1000 ? '1000+' : matches.count) : '';

  return (
    <section className="terminal-region" aria-label="Terminal" data-connected-session={connectedId}>
      <Tabs className="terminal-tabs-root" value={selected} onValueChange={choose}>
        <div className="terminal-toolbar">
          <TerminalTabs tabs={tabs} onClose={(id) => void close(id)} onActivate={() => setFocusTick((value) => value + 1)} tablistRef={tablist} />
          <Button ref={addButton} variant="ghost" size="icon" aria-label="New terminal" title={project ? 'New terminal' : 'Open or add a project to start a terminal'} disabled={!project || creating} onClick={create}><Plus size={16} strokeWidth={1.5} aria-hidden="true" /></Button>
          {selected && <Button variant="ghost" size="icon" aria-label="Close terminal" title="Close this terminal and end its shell" disabled={!!closing} onClick={() => void close(selected)}><Trash2 size={16} strokeWidth={1.5} aria-hidden="true" /></Button>}
          <span className="flex-spacer" />
          <small className="terminal-status" aria-live="polite">{project ? label : 'No project open'}</small>
          {selected && <Button className="terminal-interrupt" variant="ghost" size="sm" aria-label="Interrupt" title="Send Control-C to the running command" onClick={() => handles.current.get(selected)?.interrupt()}><CircleStop size={16} strokeWidth={1.5} aria-hidden="true" /><span className="terminal-interrupt-label">Interrupt</span></Button>}
        </div>
        {finding && (
          <div className="terminal-find" role="search" aria-label="Find in terminal">
            <input
              ref={findInput}
              className="terminal-find-input"
              aria-label="Find in terminal"
              placeholder="Find"
              value={term}
              onChange={(event) => { setTerm(event.target.value); runFind(event.target.value); }}
              onKeyDown={(event) => {
                if (event.key === 'Enter') { event.preventDefault(); runFind(term, event.shiftKey); }
                else if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); closeFind(); }
              }}
            />
            <span className="terminal-find-count" aria-live="polite">{result}</span>
            <Button variant="ghost" size="icon" aria-label="Previous match" title="Previous match" disabled={!term} onClick={() => runFind(term, true)}><ChevronUp size={16} strokeWidth={1.5} aria-hidden="true" /></Button>
            <Button variant="ghost" size="icon" aria-label="Next match" title="Next match" disabled={!term} onClick={() => runFind(term)}><ChevronDown size={16} strokeWidth={1.5} aria-hidden="true" /></Button>
            <Button variant="ghost" size="icon" aria-label="Close find" title="Close find" onClick={closeFind}><X size={16} strokeWidth={1.5} aria-hidden="true" /></Button>
          </div>
        )}
        {!project && <p className="terminal-empty">Terminals run inside a project folder. Add or open a project to start one.</p>}
        {project && !selected && <p className="terminal-empty">{sessions.length ? 'Choose a terminal tab, or open a new terminal with the plus button.' : 'No terminals are running. Open one with the plus button.'}</p>}
        <div className="terminal-panes">
          {mounted.map((row) => (
            <TabsContent key={row.id} value={row.id} forceMount hidden={row.id !== selected} tabIndex={-1} className="terminal-pane">
              <TerminalSession
                ref={(handle) => { if (handle) handles.current.set(row.id, handle); else handles.current.delete(row.id); }}
                id={row.id}
                active={row.id === selected}
                authorized={authorized}
                transportKey={transportTarget}
                onStatus={onStatus}
                onError={onError}
                onLeave={leave}
                onFind={openFind}
                onResults={onResults}
              />
            </TabsContent>
          ))}
        </div>
      </Tabs>
    </section>
  );
}
