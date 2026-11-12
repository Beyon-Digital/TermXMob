/** Client for the visual query endpoints. Compile never executes; run returns a job that is polled to completion. */
import {dataBody, dataPath, dataRequest, dataRequestId, dataJobActive, type DataJob} from './data-studio';
import type {CapabilitiesResult, CompileResult, QuerySpec, RelationsResult} from './query-spec';

export const compileSpec = (workspaceId: string, spec: QuerySpec, signal?: AbortSignal) =>
  dataRequest<CompileResult>(dataPath(workspaceId, '/query-specs/compile'), {...dataBody('POST', {spec}), signal});
export const runSpec = (workspaceId: string, spec: QuerySpec, requestId: string = dataRequestId(), signal?: AbortSignal) =>
  dataRequest<DataJob>(dataPath(workspaceId, '/query-specs/run'), {...dataBody('POST', {request_id: requestId, spec}), signal});
export const fetchRelations = (workspaceId: string, connectionId: string, table?: string, signal?: AbortSignal) =>
  dataRequest<RelationsResult>(dataPath(workspaceId, '/connections/' + encodeURIComponent(connectionId) + '/relations' + (table ? '?table=' + encodeURIComponent(table) : '')), {signal});
export const fetchCapabilities = (workspaceId: string, connectionId: string, signal?: AbortSignal) =>
  dataRequest<CapabilitiesResult>(dataPath(workspaceId, '/connections/' + encodeURIComponent(connectionId) + '/capabilities'), {signal});
export const fetchJob = (workspaceId: string, jobId: string, signal?: AbortSignal) =>
  dataRequest<DataJob>(dataPath(workspaceId, '/jobs/' + encodeURIComponent(jobId)), {signal});
export const cancelJob = (workspaceId: string, jobId: string) =>
  dataRequest<DataJob>(dataPath(workspaceId, '/jobs/' + encodeURIComponent(jobId) + '/cancel'), dataBody('POST', {}));

const wait = (ms: number, signal?: AbortSignal) => new Promise<void>((resolve, reject) => {
  if (signal?.aborted) return reject(new DOMException('Aborted', 'AbortError'));
  const timer = setTimeout(resolve, ms);
  signal?.addEventListener('abort', () => {clearTimeout(timer); reject(new DOMException('Aborted', 'AbortError'));}, {once: true});
});

/** Start a spec run and follow the job until it finishes. `onJob` sees every state, so callers can show progress. */
export async function runSpecToCompletion(workspaceId: string, spec: QuerySpec, options: {signal?: AbortSignal; onJob?: (job: DataJob) => void; interval?: number; requestId?: string} = {}): Promise<DataJob> {
  let job = await runSpec(workspaceId, spec, options.requestId, options.signal);
  options.onJob?.(job);
  while (dataJobActive(job)) {
    await wait(options.interval ?? 750, options.signal);
    job = await fetchJob(workspaceId, job.id, options.signal);
    options.onJob?.(job);
  }
  return job;
}
