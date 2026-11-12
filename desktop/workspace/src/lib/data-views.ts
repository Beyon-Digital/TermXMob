/** Client for saved views (tables, pivots and segments), their history and their runs. Types mirror section 7 of the pivots and segments plan. */
import {ApiError} from './api';
import {fetchJob} from './data-query';
import {dataBody, dataJobActive, dataPath, dataRequest, dataRequestId, type DataJob} from './data-studio';
import type {QuerySpec, Scalar, SpecError} from './query-spec';

export type ViewKind = 'table' | 'pivot' | 'segment';
export type Person = {id: string; name: string};
export type ViewSummary = {id: string; kind: ViewKind; name: string; description: string; connection_id: string; base_table: string; revision: number;
  created_by: Person; updated_by: Person; created_at: string; updated_at: string; used_by_count: number; can_edit: boolean; can_run: boolean};
export type ViewRef = {id: string; name: string; state: 'ok' | 'missing'};
export type ViewUse = {id: string; kind: ViewKind; name: string};
export type ViewDetail = ViewSummary & {spec: QuerySpec; references: ViewRef[]; used_by: ViewUse[]; affected_views?: number};
export type ChangeField = 'name' | 'description' | 'from' | 'joins' | 'filters' | 'filters.match' | 'select' | 'group_by' | 'aggregates' | 'sort' | 'limit'
  | 'pivot.rows' | 'pivot.columns' | 'pivot.value' | 'pivot.totals' | 'pivot.sort';
export type Change = {field: ChangeField; op: 'added' | 'removed' | 'changed'; before?: unknown; after?: unknown};
export type RevisionRow = {revision: number; action: 'created' | 'updated' | 'archived'; at: string; actor: Person; name: string; changes: Change[]};
export type HistoryPage = {view_id: string; revisions: RevisionRow[]; next_before: number | null};
export type RunRow = {job_id: string; actor: Person; mine: boolean; view_revision: number; mode: 'rows' | 'count'; status: DataJob['status'];
  started_at: string; finished_at: string | null; duration_ms: number | null; row_count: number | null; truncated: boolean | null; error: string | null};
export type RunsPage = {view_id: string; runs: RunRow[]; next_cursor: string | null};
export type DrillCoordinates = {rows?: (Scalar | null)[]; column?: {value: Scalar | null; empty: boolean} | null; groups?: (Scalar | null)[]};
export type DrillResult = {spec: QuerySpec; exact: boolean; notes: string[]; job: DataJob | null};
export type ViewErrorCode = 'revision_conflict' | 'name_taken' | 'view_limit' | 'segment_in_use' | 'invalid_spec' | 'invalid_view' | 'bad_coordinates' | 'source_untested';
export type ViewError = {status: number; code?: ViewErrorCode; message: string; current_revision?: number; updated_by?: Person; updated_at?: string; used_by?: ViewUse[]; errors?: SpecError[]};

const CODES: ViewErrorCode[] = ['revision_conflict', 'name_taken', 'view_limit', 'segment_in_use', 'invalid_spec', 'invalid_view', 'bad_coordinates', 'source_untested'];
/** The API layer keeps an object `detail` as JSON text in the error message. Plain strings (401, 403, 404) keep their status and text. */
export function viewError(reason: unknown): ViewError {
  const status = reason instanceof ApiError ? reason.status : (reason as {status?: number} | null)?.status ?? 0;
  const text = reason instanceof Error ? reason.message : typeof reason === 'string' ? reason : 'Something went wrong. Try again.';
  if (/^\s*\{/.test(text)) {
    try {
      const detail = JSON.parse(text) as Record<string, unknown>;
      const body = (detail.detail && typeof detail.detail === 'object' ? detail.detail : detail) as Record<string, unknown>;
      const code = CODES.find(value => value === body.code);
      if (typeof body.message === 'string' || code) {
        const errors = Array.isArray(body.errors) ? body.errors as SpecError[] : undefined;
        return {status, code, message: typeof body.message === 'string' ? body.message : errors?.[0]?.message || 'This view could not be saved.',
          ...(typeof body.current_revision === 'number' ? {current_revision: body.current_revision} : {}),
          ...(body.updated_by && typeof body.updated_by === 'object' ? {updated_by: body.updated_by as Person} : {}),
          ...(typeof body.updated_at === 'string' ? {updated_at: body.updated_at} : {}),
          ...(Array.isArray(body.used_by) ? {used_by: body.used_by as ViewUse[]} : {}), ...(errors ? {errors} : {})};
      }
    } catch {/* fall through to the plain message */}
    return {status, message: 'This view could not be saved. Reload and try again.'};
  }
  if (/^\s*\[/.test(text)) return {status, message: 'This view could not be saved. Reload and try again.'};
  return {status, message: text};
}

const base = (workspaceId: string, suffix = '') => dataPath(workspaceId, '/views' + suffix);
const id = encodeURIComponent;
export const listViews = (workspaceId: string, filter: {kind?: ViewKind; connectionId?: string} = {}, signal?: AbortSignal) => {
  const query = new URLSearchParams();
  if (filter.kind) query.set('kind', filter.kind);
  if (filter.connectionId) query.set('connection_id', filter.connectionId);
  const text = query.toString();
  return dataRequest<{views: ViewSummary[]}>(base(workspaceId, text ? '?' + text : ''), {signal});
};
export const getView = (workspaceId: string, viewId: string, signal?: AbortSignal) => dataRequest<ViewDetail>(base(workspaceId, '/' + id(viewId)), {signal});
export const createView = (workspaceId: string, body: {kind: ViewKind; name: string; description?: string; spec: QuerySpec}, signal?: AbortSignal) =>
  dataRequest<ViewDetail>(base(workspaceId), {...dataBody('POST', body), signal});
export const updateView = (workspaceId: string, viewId: string, body: {revision: number; name?: string; description?: string; spec?: QuerySpec}, signal?: AbortSignal) =>
  dataRequest<ViewDetail>(base(workspaceId, '/' + id(viewId)), {...dataBody('PATCH', body), signal});
export const archiveView = (workspaceId: string, viewId: string, revision: number, signal?: AbortSignal) =>
  dataRequest<ViewDetail>(base(workspaceId, '/' + id(viewId) + '?revision=' + revision), {...dataBody('DELETE'), signal});
export const fetchHistory = (workspaceId: string, viewId: string, options: {limit?: number; before?: number | null} = {}, signal?: AbortSignal) => {
  const query = new URLSearchParams({limit: String(options.limit ?? 20)});
  if (options.before) query.set('before', String(options.before));
  return dataRequest<HistoryPage>(base(workspaceId, '/' + id(viewId) + '/history?' + query), {signal});
};
export const fetchRuns = (workspaceId: string, viewId: string, options: {limit?: number; before?: string | null} = {}, signal?: AbortSignal) => {
  const query = new URLSearchParams({limit: String(options.limit ?? 20)});
  if (options.before) query.set('before', options.before);
  return dataRequest<RunsPage>(base(workspaceId, '/' + id(viewId) + '/runs?' + query), {signal});
};
export const runView = (workspaceId: string, viewId: string, options: {requestId?: string; mode?: 'rows' | 'count'; offset?: number} = {}, signal?: AbortSignal) =>
  dataRequest<DataJob>(base(workspaceId, '/' + id(viewId) + '/run'), {...dataBody('POST', {request_id: options.requestId ?? dataRequestId(), ...(options.mode ? {mode: options.mode} : {}), ...(options.offset ? {offset: options.offset} : {})}), signal});
export const drillView = (workspaceId: string, viewId: string, coordinates: DrillCoordinates, signal?: AbortSignal) =>
  dataRequest<DrillResult>(base(workspaceId, '/' + id(viewId) + '/drill'), {...dataBody('POST', {coordinates}), signal});
export const viewExportPath = (workspaceId: string, viewId: string, jobId: string) => base(workspaceId, '/' + id(viewId) + '/runs/' + id(jobId) + '/export.csv');

const wait = (ms: number, signal?: AbortSignal) => new Promise<void>((resolve, reject) => {
  if (signal?.aborted) return reject(new DOMException('Aborted', 'AbortError'));
  const timer = setTimeout(resolve, ms);
  signal?.addEventListener('abort', () => {clearTimeout(timer); reject(new DOMException('Aborted', 'AbortError'));}, {once: true});
});
/** Run a view and follow its job. One request id per submit, so a retry of the same click cannot start a second run. */
export async function runViewToCompletion(workspaceId: string, viewId: string, options: {signal?: AbortSignal; onJob?: (job: DataJob) => void; interval?: number; requestId?: string; mode?: 'rows' | 'count'; offset?: number} = {}): Promise<DataJob> {
  let job = await runView(workspaceId, viewId, {requestId: options.requestId, mode: options.mode, offset: options.offset}, options.signal);
  options.onJob?.(job);
  while (dataJobActive(job)) {
    await wait(options.interval ?? 750, options.signal);
    job = await fetchJob(workspaceId, job.id, options.signal);
    options.onJob?.(job);
  }
  return job;
}
