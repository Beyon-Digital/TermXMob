import {useCallback, useEffect, useMemo, useRef, useState} from 'react';
import type {CoordinationSidebarSummary} from '../features/OrchestratorStatus';
import type {Task} from '../lib/api';
import type {WorkspaceMode} from '../lib/workspace-mode';
import {useAuth} from './AuthGate';
import {useWorkspaceData} from './WorkspaceDataProvider';

/**
 * The Subagents side panel: whether it is open (remembered per principal, opened once by itself when there is something
 * to show), the summary counts the orchestrator reports, the tasks of the selected conversation, and the task being
 * inspected in the supervisor dialog.
 */
const emptySummary: CoordinationSidebarSummary = {
  hasContent: false,
  loading: false,
  active: 0,
  attention: 0,
  done: 0,
  plans: 0,
  stale: false,
};

const storageKey = (ownerId: string) => 'termx-coordination-sidebar:' + ownerId;

export type InspectedTask = {id: string; owner: string; sessionId: string};

export function useCoordination(options: {workspace: WorkspaceMode; manager: string}) {
  const {workspace, manager} = options;
  const {auth} = useAuth();
  const {session, selected} = useWorkspaceData();
  const [open, setOpen] = useState(false);
  const [target, setTarget] = useState<HTMLDivElement | null>(null);
  const [summary, setSummary] = useState<CoordinationSidebarSummary>(emptySummary);
  const [inspected, setInspected] = useState<InspectedTask | null>(null);
  const [retained, setRetained] = useState(false);
  const autoOpened = useRef(false);
  const asideRef = useRef<HTMLElement>(null);
  const toggleRef = useRef<HTMLButtonElement>(null);
  const ownerId = auth.principal.id;
  const visible = workspace === 'agent' && !manager && open;

  // Keep the panel mounted while it animates closed.
  useEffect(() => {
    if (visible) {
      setRetained(true);
      return;
    }
    if (!retained) return;
    const reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;
    const duration = parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--motion-exit')) || 120;
    const timer = setTimeout(() => setRetained(false), reduce ? 0 : duration);
    return () => clearTimeout(timer);
  }, [visible, retained]);

  // The remembered choice, per principal.
  useEffect(() => {
    let preference: string | null = null;
    try {
      preference = localStorage.getItem(storageKey(ownerId));
    } catch {}
    autoOpened.current = preference !== null;
    setOpen(preference === 'open');
    setSummary(emptySummary);
  }, [ownerId]);

  const receiveSummary = useCallback((value: CoordinationSidebarSummary) => {
    setSummary(previous => (JSON.stringify(previous) === JSON.stringify(value) ? previous : value));
    if (value.hasContent && !autoOpened.current) {
      autoOpened.current = true;
      setOpen(true);
    }
  }, []);

  /** Open or close the panel; closing returns focus to the toggle if it was inside. */
  function show(next: boolean) {
    autoOpened.current = true;
    setOpen(next);
    try {
      localStorage.setItem(storageKey(ownerId), next ? 'open' : 'closed');
    } catch {}
    if (!next && asideRef.current?.contains(document.activeElement)) toggleRef.current?.focus();
  }

  const tasks = useMemo(() => {
    const own = (session?.id === selected ? session.turns || [] : [])
      .map(turn => turn.task)
      .filter((task): task is Task => !!task && !task.coordination_report);
    return [...new Map(own.map(task => [task.id, task])).values()].reverse().slice(0, 10);
  }, [selected, session?.id, session?.turns]);

  return {
    open,
    visible,
    retained,
    summary,
    tasks,
    target,
    setTarget,
    asideRef,
    toggleRef,
    show,
    receiveSummary,
    inspected,
    inspect: (id: string) => setInspected({id, owner: ownerId, sessionId: selected}),
    stopInspecting: () => setInspected(null),
  };
}

export type Coordination = ReturnType<typeof useCoordination>;
