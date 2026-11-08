import {useCallback, useEffect, useRef, useState} from 'react';
import type {Session} from './api';
import {latestExecutionTask} from './conversation-messages';
import {taskIsActive} from './tool-activity';
import {notePlanEvent} from './plan-signal';
import {useTaskEvents} from './task-events';
import {usePolling} from './use-polling';

const FAST = 1500;
/** Safety-net interval while the task event stream is delivering updates. */
const LIVE = 15000;
const IDLE_CEILING = 6000;
/** Several events in a burst cost one snapshot request. */
const EVENT_DEBOUNCE = 250;

/**
 * Returns `previous` when `next` is the same snapshot, so React skips the render and memoised
 * children keep their props. A different revision is always a change; equal revisions are compared
 * structurally because turns and task state move without bumping the session revision.
 */
export function keepIdentity(previous: Session | null, next: Session): Session {
  if (!previous || previous.id !== next.id || previous.revision !== next.revision) return next;
  try { return JSON.stringify(previous) === JSON.stringify(next) ? previous : next; } catch { return next; }
}

export interface SessionSnapshotOptions {
  selected: string;
  authenticated: boolean;
  /** Restarts the loop when the signed-in principal changes while the same conversation stays selected. */
  principalId?: string;
  /** False while the workspace is hidden, locked or showing a settings page. */
  active: boolean;
  read: (id: string) => Promise<Session>;
  onError: (error: unknown) => void;
}

/** Owns the selected session's snapshot: polled while visible, pushed by task events while its latest task runs. */
export function useSessionSnapshot(options: SessionSnapshotOptions) {
  const {selected, authenticated, active, principalId = ''} = options;
  const [session, setSession] = useState<Session | null>(null);
  const task = session?.id === selected ? latestExecutionTask(session.turns || []) : undefined;
  const runningTaskId = task && (taskIsActive(task.status) || task.status === 'awaiting_approval') ? task.id : null;
  const [live, setLive] = useState(false);
  const [eventTick, setEventTick] = useState(0);
  const latest = useRef(options);
  const current = useRef<Session | null>(null);
  const eventTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  useEffect(() => { latest.current = options; });
  useEffect(() => { current.current = session; }, [session]);

  const fetchSnapshot = useCallback(async () => keepIdentity(current.current, await latest.current.read(selected)), [selected]);
  const ready = active && authenticated && !!selected;
  const streamTask = ready ? runningTaskId : null;
  const streaming = live && !!streamTask;

  useEffect(() => { if (active && (!authenticated || !selected)) setSession(null); }, [active, authenticated, selected]);

  // A task event bumps `eventTick`, which restarts the loop with an immediate request and a reset backoff.
  usePolling(fetchSnapshot, {
    interval: streaming ? LIVE : FAST,
    maxInterval: streaming ? LIVE : IDLE_CEILING,
    enabled: ready,
    resetKey: `${selected}:${principalId}:${eventTick}`,
    isEqual: Object.is,
    onResult: setSession,
    onError: error => latest.current.onError(error),
  });

  useTaskEvents(streamTask, {
    onLive: setLive,
    onEvent: event => {
      notePlanEvent(event);
      if (eventTimer.current) return;
      eventTimer.current = setTimeout(() => { eventTimer.current = undefined; setEventTick(value => value + 1); }, EVENT_DEBOUNCE);
    },
  });
  useEffect(() => () => { clearTimeout(eventTimer.current); eventTimer.current = undefined; }, [selected, streamTask]);

  return [session, setSession] as const;
}
