import {useCallback, useEffect, useState} from 'react';
import {ApiError} from '../../lib/api';
import {cancelJob} from '../../lib/data-query';
import {dataError, dataJobActive, type DataJob} from '../../lib/data-studio';
import {runViewToCompletion, viewError} from '../../lib/data-views';

export type ViewRunState = {phase: 'idle' | 'running' | 'ready' | 'error'; job?: DataJob; error: string; needsTest: boolean};
const idle: ViewRunState = {phase: 'idle', error: '', needsTest: false};
const friendly = (message: string) => /^\s*[[{]/.test(message) ? 'This view could not be run. Reload it and try again.' : message;

/** Run a saved view and follow its job, so the run lands in the view's history. A newer run, a revision change or leaving cancels the old one. */
export function useViewRun(workspaceId: string, viewId: string | undefined, revision: number, enabled: boolean, mode: 'rows' | 'count' = 'rows', offset = 0) {
  const [state, setState] = useState<ViewRunState>(idle), [tick, setTick] = useState(0);
  useEffect(() => {
    if (!viewId || !enabled) {setState(idle); return;}
    const abort = new AbortController();
    let active: DataJob | undefined;
    setState(previous => ({phase: 'running', job: previous.job, error: '', needsTest: false}));
    runViewToCompletion(workspaceId, viewId, {signal: abort.signal, mode, offset, onJob: job => {active = job;}}).then(job => {
      if (abort.signal.aborted) return;
      if (job.status === 'completed' && job.result) setState({phase: 'ready', job, error: '', needsTest: false});
      else setState(previous => ({phase: 'error', job: previous.job, error: job.error || (job.status === 'cancelled' ? 'The run was cancelled.' : 'The run did not finish.'), needsTest: false}));
    }).catch(reason => {
      if (abort.signal.aborted) return;
      const error = viewError(reason);
      setState(previous => ({phase: 'error', job: previous.job, error: friendly(error.code ? error.message : dataError(reason)), needsTest: error.code === 'source_untested' || reason instanceof ApiError && reason.status === 409 && !error.code}));
    });
    return () => {
      abort.abort();
      if (active && dataJobActive(active)) void cancelJob(workspaceId, active.id).catch(() => undefined);
    };
  }, [workspaceId, viewId, revision, enabled, mode, offset, tick]);
  const reload = useCallback(() => setTick(value => value + 1), []);
  return {state, reload};
}
