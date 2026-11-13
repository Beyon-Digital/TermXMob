import {useCallback, useEffect, useRef, useState} from 'react';
import {Button} from '../../components/ui/button';
import {Tabs, TabsContent, TabsList, TabsTrigger} from '../../components/ui/tabs';
import {dataError} from '../../lib/data-studio';
import {fetchHistory, fetchRuns, viewError, type RevisionRow, type RunRow} from '../../lib/data-views';
import {describeChange} from '../../lib/view-changes';
import {formatDuration, formatWhen} from '../../lib/view-time';
import type {FilterContext} from '../../lib/query-builder';
import {plainContext} from '../../lib/segments';

type Props = {
  workspaceId: string; viewId: string;
  /** Owners and editors see the revisions and everyone's runs. Viewers see only their own runs. */ canSeeHistory: boolean;
  /** Changes whenever the view is saved or run, so the lists reload. */ refreshKey: string | number;
  names: (id: string) => string | undefined; context?: FilterContext; noun?: string;
};
type Page<T> = {rows: T[]; status: 'loading' | 'ready' | 'error'; error: string; more: boolean; loadingMore: boolean};

/** Load a newest-first list a page at a time. A new `refreshKey` starts over; leaving cancels the request. */
function usePages<T, C>(load: (cursor: C | null, signal: AbortSignal) => Promise<{rows: T[]; next: C | null}>, refreshKey: unknown, enabled: boolean) {
  const [page, setPage] = useState<Page<T>>({rows: [], status: 'loading', error: '', more: false, loadingMore: false}), cursor = useRef<C | null>(null), loadRef = useRef(load), [tick, setTick] = useState(0);
  loadRef.current = load;
  useEffect(() => {
    if (!enabled) return;
    const abort = new AbortController();
    setPage(previous => ({...previous, status: previous.rows.length ? previous.status : 'loading', error: ''}));
    loadRef.current(null, abort.signal).then(result => {
      if (abort.signal.aborted) return;
      cursor.current = result.next;
      setPage({rows: result.rows, status: 'ready', error: '', more: result.next !== null, loadingMore: false});
    }).catch(reason => {if (!abort.signal.aborted) setPage(previous => ({...previous, status: 'error', error: viewError(reason).message || dataError(reason)}));});
    return () => abort.abort();
  }, [refreshKey, enabled, tick]);
  const more = useCallback(() => {
    if (cursor.current === null) return;
    const abort = new AbortController();
    setPage(previous => ({...previous, loadingMore: true}));
    loadRef.current(cursor.current, abort.signal).then(result => {
      cursor.current = result.next;
      setPage(previous => ({rows: [...previous.rows, ...result.rows], status: 'ready', error: '', more: result.next !== null, loadingMore: false}));
    }).catch(reason => setPage(previous => ({...previous, loadingMore: false, error: viewError(reason).message})));
  }, []);
  return {page, more, retry: useCallback(() => setTick(value => value + 1), [])};
}
const Skeleton = ({label}: {label: string}) => <div className="data-list" role="status" aria-label={label}>{[0, 1, 2].map(index => <span className="data-skeleton-row" key={index}/>)}</div>;
const STATUS: Record<RunRow['status'], string> = {queued: 'Waiting', running: 'Running', completed: 'Completed', failed: 'Failed', cancelled: 'Cancelled', unknown: 'Unknown'};

/** Who changed the view and who ran it. Revisions for editors, runs for everyone (viewers see their own). */
export default function AuditPanel({workspaceId, viewId, canSeeHistory, refreshKey, names, context = plainContext, noun = 'segment'}: Props) {
  const [tab, setTab] = useState(canSeeHistory ? 'revisions' : 'runs');
  const revisions = usePages<RevisionRow, number>(async (before, signal) => {
    const result = await fetchHistory(workspaceId, viewId, {before}, signal);
    return {rows: result.revisions, next: result.next_before};
  }, refreshKey + viewId, canSeeHistory && tab === 'revisions');
  const runs = usePages<RunRow, string>(async (before, signal) => {
    const result = await fetchRuns(workspaceId, viewId, {before}, signal);
    return {rows: result.runs, next: result.next_cursor};
  }, refreshKey + viewId, tab === 'runs');
  return <section className="data-audit" aria-label="Audit">
    <h2 className="data-audit-title">Audit</h2>
    <Tabs value={tab} onValueChange={setTab}>
      <TabsList aria-label="Audit views">
        {canSeeHistory && <TabsTrigger value="revisions">Revisions</TabsTrigger>}
        <TabsTrigger value="runs">{canSeeHistory ? 'Runs' : 'Your runs'}</TabsTrigger>
      </TabsList>
      {canSeeHistory ? <TabsContent value="revisions" className="data-audit-panel">
        {revisions.page.status === 'loading' && <Skeleton label="Loading revisions"/>}
        {revisions.page.status === 'error' && !revisions.page.rows.length && <div className="data-inline-error"><p role="alert">{revisions.page.error}</p><Button variant="secondary" size="sm" onClick={revisions.retry}>Try again</Button></div>}
        {revisions.page.status === 'ready' && !revisions.page.rows.length && <div className="data-empty-inline"><p>Nothing here yet. Changes appear after the first save.</p></div>}
        {revisions.page.rows.length > 0 && <ol className="data-audit-list" aria-label="Revisions">{revisions.page.rows.map(row => <li key={row.revision}>
          <p className="data-audit-line"><strong>Revision {row.revision}</strong> · {row.actor.name} · <time dateTime={row.at}>{formatWhen(row.at)}</time></p>
          {row.action === 'created' ? <p className="data-muted">Created by {row.actor.name}</p> : row.action === 'archived' ? <p className="data-muted">Deleted by {row.actor.name}</p>
            : row.changes.length ? <ul className="data-audit-changes">{row.changes.map((change, index) => <li key={index}>{describeChange(change, names, context)}</li>)}</ul> : <p className="data-muted">Saved with no visible changes.</p>}
        </li>)}</ol>}
        {revisions.page.error && revisions.page.rows.length > 0 && <p className="data-field-error" role="alert">{revisions.page.error}</p>}
        {revisions.page.more && <Button variant="ghost" size="sm" onClick={revisions.more} disabled={revisions.page.loadingMore}>{revisions.page.loadingMore ? 'Loading…' : 'Show earlier'}</Button>}
      </TabsContent> : null}
      <TabsContent value="runs" className="data-audit-panel">
        {runs.page.status === 'loading' && <Skeleton label="Loading runs"/>}
        {runs.page.status === 'error' && !runs.page.rows.length && <div className="data-inline-error"><p role="alert">{runs.page.error}</p><Button variant="secondary" size="sm" onClick={runs.retry}>Try again</Button></div>}
        {runs.page.status === 'ready' && !runs.page.rows.length && <div className="data-empty-inline"><p>Nothing here yet. This {noun} has not been run{canSeeHistory ? '' : ' by you'}.</p></div>}
        {runs.page.rows.length > 0 && <div className="data-table-scroll" role="region" aria-label={(canSeeHistory ? 'Runs' : 'Your runs') + ' (scrollable)'} tabIndex={0}><table className="data-table-list data-audit-runs" aria-label={canSeeHistory ? 'Runs' : 'Your runs'}>
          <thead><tr><th scope="col">Who</th><th scope="col">When</th><th scope="col" className="data-number">Rows</th><th scope="col" className="data-number">Time</th><th scope="col">Result</th><th scope="col" className="data-number">Revision</th></tr></thead>
          <tbody>{runs.page.rows.map(row => <tr key={row.job_id}>
            <th scope="row">{row.mine ? 'You' : row.actor.name}</th>
            <td><time dateTime={row.started_at}>{formatWhen(row.started_at)}</time></td>
            <td className="data-number">{row.mode === 'count' ? 'Count' : row.row_count === null ? '' : row.row_count.toLocaleString() + (row.truncated ? '+' : '')}</td>
            <td className="data-number">{formatDuration(row.duration_ms)}</td>
            <td>{STATUS[row.status]}{row.status === 'failed' && row.error ? <small className="data-audit-error">{row.error}</small> : null}</td>
            <td className="data-number">{row.view_revision}</td>
          </tr>)}</tbody>
        </table></div>}
        {runs.page.error && runs.page.rows.length > 0 && <p className="data-field-error" role="alert">{runs.page.error}</p>}
        {runs.page.more && <Button variant="ghost" size="sm" onClick={runs.more} disabled={runs.page.loadingMore}>{runs.page.loadingMore ? 'Loading…' : 'Show earlier'}</Button>}
      </TabsContent>
    </Tabs>
  </section>;
}
