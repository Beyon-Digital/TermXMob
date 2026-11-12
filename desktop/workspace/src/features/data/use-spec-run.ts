import {useCallback, useEffect, useRef, useState} from 'react';
import {ApiError} from '../../lib/api';
import {cancelJob, compileSpec, runSpecToCompletion} from '../../lib/data-query';
import {dataError, dataJobActive, type DataJob} from '../../lib/data-studio';
import type {CompileResult, QuerySpec, SpecError} from '../../lib/query-spec';

export type CompiledOk = Extract<CompileResult, {ok: true}>;
export type SpecPhase = 'idle' | 'compiling' | 'running' | 'ready' | 'invalid' | 'error';
export type SpecRun = {
  phase: SpecPhase;
  /** Latest successful compile: drives Show SQL and the join hints. */
  compile?: CompiledOk;
  /** The last finished result and the compile it belongs to, kept on screen while the next one loads. */
  shown?: {compile: CompiledOk; job: DataJob; spec: QuerySpec};
  errors: SpecError[]; error: string;
  /** The server wants a source test before it will read this source. */
  needsTest: boolean;
};
const initial: SpecRun = {phase: 'idle', errors: [], error: '', needsTest: false};
/** The server answers a malformed request with a JSON list of field problems. People never need to read that. */
const friendly = (message: string) => /^\s*[[{]/.test(message) ? 'This view could not be run. Clear the filters or reset the view, then try again.' : message;

/** Compile a spec (debounced), run it as a job and follow the job. A newer spec cancels the older run. */
export function useSpecRun(workspaceId: string, spec: QuerySpec | null, enabled = true, delay = 250) {
  const [state, setState] = useState<SpecRun>(initial), [tick, setTick] = useState(0), manual = useRef(false);
  const key = spec ? JSON.stringify(spec) : '';
  useEffect(() => {
    if (!spec || !enabled) {setState(initial); return;}
    const abort = new AbortController(), wait = manual.current ? 0 : delay;
    let active: DataJob | undefined;
    manual.current = false;
    setState(previous => ({...previous, phase: 'compiling', error: '', errors: [], needsTest: false}));
    const timer = setTimeout(async () => {
      try {
        const compiled = await compileSpec(workspaceId, spec, abort.signal);
        if (abort.signal.aborted) return;
        if (!compiled.ok) {setState(previous => ({...previous, phase: 'invalid', errors: compiled.errors, error: ''})); return;}
        setState(previous => ({...previous, phase: 'running', compile: compiled}));
        const job = await runSpecToCompletion(workspaceId, spec, {signal: abort.signal, onJob: next => {active = next;}});
        if (abort.signal.aborted) return;
        if (job.status === 'completed' && job.result) setState(previous => ({...previous, phase: 'ready', shown: {compile: compiled, job, spec}}));
        else setState(previous => ({...previous, phase: 'error', error: job.error || (job.status === 'cancelled' ? 'The query was cancelled.' : 'The query did not finish.')}));
      } catch (reason) {
        if (abort.signal.aborted) return;
        setState(previous => ({...previous, phase: 'error', error: friendly(dataError(reason)), needsTest: reason instanceof ApiError && reason.status === 409}));
      }
    }, wait);
    return () => {
      clearTimeout(timer); abort.abort();
      if (active && dataJobActive(active)) void cancelJob(workspaceId, active.id).catch(() => undefined);
    };
    // The spec is compared by value so an equal rebuild does not re-run.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workspaceId, key, enabled, tick, delay]);
  const reload = useCallback(() => {manual.current = true; setTick(value => value + 1);}, []);
  return {state, reload};
}
