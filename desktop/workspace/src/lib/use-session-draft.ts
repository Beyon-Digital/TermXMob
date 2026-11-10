import {useEffect, useRef, useSyncExternalStore} from 'react';
import {draftStoreState, stoppingTasks, submittingDrafts, subscribeDrafts} from './drafts';

/**
 * Subscribes a conversation to the shared draft registry. Nothing is read from `window` events: the
 * registry publishes a new snapshot whenever a submission starts or ends, a Stop is requested, or a
 * draft is handed over from another window.
 */
export function useSessionDraft(sessionId?: string | null) {
  const store = useSyncExternalStore(subscribeDrafts, draftStoreState, draftStoreState);
  return {
    sending: !!sessionId && submittingDrafts.has(sessionId),
    submissionRevision: store.submission,
    handoff: store.handoff,
    isStopping: (taskId: string) => stoppingTasks.has(taskId),
  };
}

/** Runs `effect` when `value` changes after mount. The value present at mount is already reflected in render. */
export function useWhenChanged<T>(value: T, effect: (value: T) => void) {
  const seen = useRef(value);
  const latest = useRef(effect);
  useEffect(() => { latest.current = effect; });
  useEffect(() => {
    if (Object.is(seen.current, value)) return;
    seen.current = value;
    latest.current(value);
  }, [value]);
}
