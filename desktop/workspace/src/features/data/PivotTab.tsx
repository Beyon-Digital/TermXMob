import {useCallback, useEffect, useMemo, useRef, useState} from 'react';
import {Code2, Download, EllipsisVertical, Pencil, RefreshCw, RotateCcw, Save, SlidersHorizontal, Trash2} from 'lucide-react';
import {Button} from '../../components/ui/button';
import {Dialog, DialogContent, DialogDescription, DialogTitle} from '../../components/ui/dialog';
import {DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger} from '../../components/ui/dropdown-menu';
import {compileSpec, fetchCapabilities} from '../../lib/data-query';
import {dataDownload, dataError, useDataScope} from '../../lib/data-studio';
import {archiveView, createView, drillView, getView, updateView, viewError, viewExportPath, type DrillCoordinates, type ViewDetail, type ViewError} from '../../lib/data-views';
import {compactFilters, countFilters, emptySpec, humanize, kindGroup} from '../../lib/query-builder';
import {PIVOT_ROW_LIMIT, cellCoordinates, cellTitle, coarserBucket, bucketLabel, drillSpec, emptyPivot, isDateKind, pivotQuery, pivotSentence} from '../../lib/pivot';
import {mergeSegmentJoins, segmentNode} from '../../lib/segments';
import type {CapabilitiesResult, FilterGroup, Join, OutputColumn, PivotSpec, QuerySpec, SchemaTable} from '../../lib/query-spec';
import type {DataTab} from '../../lib/data-tabs';
import ConflictBanner from './ConflictBanner';
import FilterChips from './FilterChips';
import JoinPanel, {JoinList} from './JoinPanel';
import PivotBuilder from './PivotBuilder';
import PivotTable from './PivotTable';
import SaveViewDialog from './SaveViewDialog';
import ShowSql from './ShowSql';
import {tableKey} from './DataNavigation';
import {useSpecColumns} from './use-spec-columns';
import {useSpecRun, type CompiledOk} from './use-spec-run';
import {useViewRun} from './use-view-run';
import {useAutoSchema, type SourceController} from './use-source';
import type {ViewsController} from './use-views';

type Props = {
  workspaceId: string; connectionId: string; tab: DataTab; label: string; controller: SourceController; engineCanLoad: boolean; views: ViewsController;
  /** Whether this person may build and change views. Viewers get a read-only pivot. */ canEdit: boolean;
  onClose: () => void; onOpenAdvanced?: () => void;
  /** A view was created. `fromTabId` is the tab it came from. */ onSaved: (detail: ViewDetail, fromTabId: string) => void;
  onArchived: (id: string) => void;
  /** Opens the rows behind a number in a new tab. */ onDrill: (spec: QuerySpec, title: string, note?: string) => void;
};
const Skeleton = ({label}: {label: string}) => <div className="data-list data-gate" role="status" aria-label={label}>{[0, 1, 2, 3, 4].map(index => <span className="data-skeleton-row" key={index}/>)}</div>;
const slug = (name: string) => (name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '') || 'pivot').slice(0, 60);

/** Pivot tab: loads the saved view (if any), the segment it starts from (if any) and the schema, then hands over to the workspace. */
export default function PivotTab(props: Props) {
  const {tab, workspaceId, controller, label, onClose, canEdit} = props, scope = useDataScope();
  const [detail, setDetail] = useState<ViewDetail | null>(null), [load, setLoad] = useState<{phase: 'loading' | 'ready' | 'error'; error: string}>({phase: tab.viewId || tab.segmentId ? 'loading' : 'ready', error: ''});
  const [generation, setGeneration] = useState(0), [reloading, setReloading] = useState(false), [announce, setAnnounce] = useState(''), [attempt, setAttempt] = useState(0);
  const [seed, setSeed] = useState<{joins: Join[]; filters: FilterGroup} | null>(null);
  useEffect(() => {
    const id = tab.viewId || tab.segmentId;
    if (!id) {setLoad({phase: 'ready', error: ''}); return;}
    const abort = new AbortController();
    setLoad({phase: 'loading', error: ''});
    getView(workspaceId, id, abort.signal).then(result => {
      if (abort.signal.aborted) return;
      if (tab.viewId) setDetail(result);
      else if (tab.table) {
        const base = emptySpec(tab.connectionId || props.connectionId, tab.table), merged = mergeSegmentJoins(base, result.spec);
        setSeed({joins: merged.spec.joins, filters: {op: 'and', items: [segmentNode(id)]}});
      }
      setLoad({phase: 'ready', error: ''});
    }).catch(reason => {if (!abort.signal.aborted) setLoad({phase: 'error', error: viewError(reason).status === 404 ? 'This view is no longer available.' : viewError(reason).message});});
    return () => abort.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workspaceId, tab.viewId, tab.segmentId, attempt]);
  const reload = useCallback(async () => {
    if (!tab.viewId) return;
    setReloading(true);
    try {
      const next = await getView(workspaceId, tab.viewId);
      if (!scope.current()) return;
      setDetail(next); setGeneration(value => value + 1); setAnnounce('Reloaded revision ' + next.revision + '.');
      props.views.upsert(next);
    } catch (reason) {
      if (scope.current()) setAnnounce(viewError(reason).message);
    } finally {if (scope.current()) setReloading(false);}
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workspaceId, tab.viewId]);

  const readOnly = !canEdit || (!!detail && !detail.can_edit);
  useAutoSchema(controller, !readOnly && load.phase === 'ready' && props.engineCanLoad);
  if (load.phase === 'loading') return <Skeleton label={'Loading ' + label}/>;
  if (load.phase === 'error') return <div className="data-inline-error data-gate"><p role="alert">{load.error}</p><Button variant="secondary" size="sm" onClick={() => setAttempt(value => value + 1)}>Try again</Button><Button variant="ghost" size="sm" onClick={onClose}>Close tab</Button></div>;
  if (tab.viewId && !detail) return <Skeleton label={'Loading ' + label}/>;
  const tableName = detail ? detail.spec.from.table : tab.table || '';
  if (readOnly) return <><p className="visually-hidden" role="status">{announce}</p><PivotWorkspace key={generation + ':' + (detail?.revision ?? 0)} {...props} detail={detail} tables={[]} table={undefined} readOnly reload={reload} reloading={reloading} seed={null}/></>;
  const status = controller.schemaStatus, table = controller.schema?.tables.find(row => detail ? row.name === tableName : tableKey(row) === (tab.schema ? tab.schema + '.' : '') + tableName);
  if (status === 'idle' && props.engineCanLoad) return <Skeleton label={'Loading ' + label}/>;
  if (status === 'idle') return <div className="data-empty-inline data-gate"><p>Load the tables to open {label}.</p><Button onClick={controller.loadSchema} disabled={!props.engineCanLoad}>Load tables</Button></div>;
  if (status === 'loading') return <Skeleton label={'Loading ' + label}/>;
  if (status === 'error') return <div className="data-inline-error data-gate"><p role="alert">{controller.schemaError}</p>{controller.needsTest ? <Button variant="secondary" size="sm" onClick={controller.testConnection} disabled={controller.testing}>{controller.testing ? 'Testing…' : 'Test connection'}</Button> : <Button variant="secondary" size="sm" onClick={controller.loadSchema}>Try again</Button>}</div>;
  if (!table) return <div className="data-empty-inline data-gate"><p>{humanize(tableName)} is no longer in this source.</p><Button variant="secondary" onClick={onClose}>Close tab</Button></div>;
  return <><p className="visually-hidden" role="status">{announce}</p><PivotWorkspace key={generation + ':' + (detail?.revision ?? 0)} {...props} detail={detail} table={table} tables={controller.schema?.tables || []} readOnly={false} reload={reload} reloading={reloading} seed={seed}/></>;
}

type WorkspaceProps = Props & {detail: ViewDetail | null; table: SchemaTable | undefined; tables: SchemaTable[]; readOnly: boolean; reload: () => Promise<void>; reloading: boolean; seed: {joins: Join[]; filters: FilterGroup} | null};
type Shown = {columns: OutputColumn[]; rows: unknown[][]; truncated: boolean; jobId: string; viaView: boolean};

function defaultPivot(table: SchemaTable, alias: string): PivotSpec {
  const column = table.columns.find(row => kindGroup(row.value_kind) === 'text') || table.columns[0];
  return {...emptyPivot(), rows: column ? [{table_alias: alias, column: column.name, ...(isDateKind(column.value_kind) ? {bucket: 'month' as const} : {})}] : []};
}

function PivotWorkspace({workspaceId, connectionId, tab, controller, views, onOpenAdvanced, onSaved, onArchived, onDrill, onClose, detail, table, tables, readOnly, reload, reloading, seed}: WorkspaceProps) {
  const scope = useDataScope();
  const base = useMemo<QuerySpec>(() => detail ? {...emptySpec(connectionId, detail.spec.from.table), from: detail.spec.from} : emptySpec(connectionId, table!.name), [connectionId, detail, table]);
  const [saved, setSaved] = useState<ViewDetail | null>(detail);
  const [joins, setJoins] = useState<Join[]>(() => detail ? detail.spec.joins : seed?.joins || []);
  const [filters, setFilters] = useState<FilterGroup>(() => detail ? detail.spec.filters : seed?.filters || base.filters);
  const [pivot, setPivot] = useState<PivotSpec>(() => detail ? detail.spec.pivot || emptyPivot() : table ? defaultPivot(table, base.from.alias) : emptyPivot());
  const [offset, setOffset] = useState(0), [sql, setSql] = useState(false), [busy, setBusy] = useState(false);
  // On a narrow screen a saved pivot opens on its numbers; the setup is one tap away.
  const [setup, setSetup] = useState(() => !detail || !window.matchMedia?.('(max-width: 767px)').matches);
  const [dialog, setDialog] = useState<'save' | 'copy' | 'rename' | 'delete' | null>(null), [conflict, setConflict] = useState<ViewError | null>(null);
  const [message, setMessage] = useState(''), [problem, setProblem] = useState(''), [exporting, setExporting] = useState(false), [capabilities, setCapabilities] = useState<CapabilitiesResult | null>(null);
  const pending = useRef<'rename' | 'delete' | null>(null);
  const [sqlCompile, setSqlCompile] = useState<CompiledOk | undefined>(), [dropError, setDropError] = useState('');
  const { labelFor, kindFor, options } = useSpecColumns(base.from, joins, tables);
  useEffect(() => {
    if (readOnly) return;
    const abort = new AbortController();
    fetchCapabilities(workspaceId, connectionId, abort.signal).then(result => {if (!abort.signal.aborted) setCapabilities(result);}).catch(() => undefined);
    return () => abort.abort();
  }, [workspaceId, connectionId, readOnly]);

  const spec = useMemo(() => pivotQuery({...base, joins, filters: compactFilters(filters), offset}, pivot), [base, joins, filters, pivot, offset]);
  const key = spec ? JSON.stringify({...spec, offset: 0}) : '';
  const baseline = useRef(detail && spec ? key : '');
  const dirty = !saved || key !== baseline.current;
  const supported = capabilities ? capabilities.supported : true;
  const live = useSpecRun(workspaceId, spec, !readOnly && supported && dirty), viaView = useViewRun(workspaceId, saved?.id, saved?.revision ?? 0, !!saved && !dirty && supported, 'rows', offset);
  const ready: Shown | null = useMemo(() => {
    if (saved && !dirty) {
      const job = viaView.state.job;
      return job?.result && job.output ? {columns: job.output.columns, rows: job.result.rows, truncated: job.result.truncated, jobId: job.id, viaView: true} : null;
    }
    const shown = live.state.shown;
    return shown?.job.result ? {columns: shown.compile.columns, rows: shown.job.result.rows, truncated: shown.job.result.truncated, jobId: shown.job.id, viaView: false} : null;
  }, [saved, dirty, viaView.state.job, live.state.shown]);
  const last = useRef<Shown | null>(null);
  if (ready) last.current = ready;
  const shown = ready || last.current;
  const clean = !!saved && !dirty, phase = clean ? viaView.state.phase : live.state.phase;
  const loading = clean ? viaView.state.phase === 'running' : live.state.phase === 'compiling' || live.state.phase === 'running';
  const errors = clean ? [] : live.state.errors, runError = clean ? viaView.state.error : live.state.error, needsTest = clean ? viaView.state.needsTest : live.state.needsTest;
  const reloadRun = clean ? viaView.reload : live.reload;
  const limit = spec?.limit || PIVOT_ROW_LIMIT, pageRows = shown ? shown.rows.slice(0, limit) : [], hasMore = !!shown && (shown.truncated || shown.rows.length > limit);
  const sentence = pivotSentence(pivot, labelFor), name = saved?.name || 'New pivot on ' + humanize(table?.name || base.from.table);
  const change = (next: Partial<{pivot: PivotSpec; joins: Join[]; filters: FilterGroup}>) => {
    setOffset(0); setMessage(''); setProblem('');
    if (next.pivot) setPivot(next.pivot);
    if (next.joins) setJoins(next.joins);
    if (next.filters) setFilters(next.filters);
  };
  const canEdit = !readOnly;

  useEffect(() => {
    if (!sql || !spec || live.state.compile || !saved || supported === false) {setSqlCompile(undefined); return;}
    const abort = new AbortController();
    compileSpec(workspaceId, spec, abort.signal).then(result => {if (!abort.signal.aborted && result.ok) setSqlCompile(result);}).catch(() => undefined);
    return () => abort.abort();
  }, [sql, key, saved, live.state.compile, supported, workspaceId]); // eslint-disable-line react-hooks/exhaustive-deps

  async function addSegment(id: string, negate: boolean) {
    setProblem('');
    try {
      const segment = await getView(workspaceId, id);
      if (!scope.current()) return;
      const merged = mergeSegmentJoins({...base, joins}, segment.spec);
      change({joins: merged.spec.joins, filters: {...filters, items: [...filters.items, segmentNode(id, negate)]}});
      if (merged.added.length) setMessage('Combined ' + merged.added.map(humanize).join(' and ') + ' so this segment works.');
    } catch (reason) {if (scope.current()) setProblem(viewError(reason).message);}
  }
  async function saveChanges() {
    if (!saved || !spec || busy) return;
    setBusy(true); setConflict(null); setProblem('');
    try {
      const next = await updateView(workspaceId, saved.id, {revision: saved.revision, spec});
      if (!scope.current()) return;
      baseline.current = key; setSaved(next); views.upsert(next); setMessage('Saved changes.');
    } catch (reason) {
      if (!scope.current()) return;
      const error = viewError(reason);
      if (error.code === 'revision_conflict') setConflict(error); else setProblem(error.errors?.[0]?.message || error.message);
    } finally {if (scope.current()) setBusy(false);}
  }
  async function createPivot(viewName: string, fromTabId: string) {
    if (!spec) return;
    const created = await createView(workspaceId, {kind: 'pivot', name: viewName, spec});
    views.upsert(created); onSaved(created, fromTabId);
  }
  async function rename(viewName: string) {
    if (!saved) return;
    const next = await updateView(workspaceId, saved.id, {revision: saved.revision, name: viewName});
    baseline.current = baseline.current; setSaved(next); views.upsert(next); setMessage('Renamed to ' + next.name + '.');
  }
  async function remove() {
    if (!saved || busy) return;
    setBusy(true); setDropError('');
    try {
      await archiveView(workspaceId, saved.id, saved.revision);
      if (!scope.current()) return;
      views.remove(saved.id); setDialog(null); onArchived(saved.id);
    } catch (reason) {
      if (!scope.current()) return;
      const error = viewError(reason);
      if (error.code === 'revision_conflict') {setConflict(error); setDialog(null);} else setDropError(error.message);
    } finally {if (scope.current()) setBusy(false);}
  }
  function resetPivot() {
    if (!saved) return;
    setJoins(saved.spec.joins); setFilters(saved.spec.filters); setPivot(saved.spec.pivot || emptyPivot()); setOffset(0); setMessage('Went back to the saved pivot.'); setProblem('');
  }
  async function drill(rowIndex: number, columnIndex: number) {
    if (!shown || !spec) return;
    const row = pageRows[rowIndex], coordinates: DrillCoordinates = cellCoordinates(shown.columns, row, columnIndex), title = cellTitle(shown.columns, row, columnIndex);
    setProblem('');
    if (saved && !dirty) {
      try {
        const result = await drillView(workspaceId, saved.id, coordinates);
        if (scope.current()) onDrill(result.spec, title, result.exact ? undefined : result.notes.join(' '));
      } catch (reason) {if (scope.current()) setProblem(viewError(reason).message);}
      return;
    }
    const local = drillSpec(spec, coordinates);
    if (local) onDrill(local, title); else setProblem('These rows could not be opened. Reset the pivot and try again.');
  }
  async function exportCsv() {
    if (!shown || exporting) return;
    const abort = scope.controller();
    setExporting(true); setProblem('');
    try {
      await dataDownload(workspaceId, shown.jobId, abort.signal, () => scope.current(), shown.viaView && saved ? {path: viewExportPath(workspaceId, saved.id, shown.jobId), filename: slug(saved.name) + '.csv'} : {filename: slug(name) + '.csv'});
    } catch (reason) {if (scope.current()) setProblem(dataError(reason));}
    finally {scope.release(abort); if (scope.current()) setExporting(false);}
  }

  const tooMany = errors.find(error => error.code === 'pivot_too_many_columns'), coarser = coarserBucket(pivot.columns?.bucket);
  const clearFilters = () => change({filters: base.filters});
  let results;
  if (!supported) results = <div className="data-empty-inline"><p>{capabilities?.reason || 'Pivots are not available for this source.'}</p>{onOpenAdvanced && <Button variant="secondary" onClick={onOpenAdvanced}>Open Advanced SQL</Button>}</div>;
  else if (!spec) results = <div className="data-empty-inline"><p>Choose a column for the rows to see the pivot.</p></div>;
  else if (errors.length) results = <div className="data-inline-error"><p role="alert">{errors[0].message}</p>
    {tooMany && pivot.columns && coarser ? <Button variant="secondary" size="sm" onClick={() => change({pivot: {...pivot, columns: {...pivot.columns!, bucket: coarser}}})}>Group by {bucketLabel(coarser)}</Button>
      : tooMany ? <Button variant="secondary" size="sm" onClick={() => change({pivot: {...pivot, columns: null, totals: true}})}>Remove the columns</Button>
      : errors[0].code === 'segment_missing' ? <Button variant="secondary" size="sm" onClick={() => {
        const at = /filters\.items\[(\d+)\]/.exec(errors[0].path)?.[1];
        if (at !== undefined) change({filters: {...filters, items: filters.items.filter((_, index) => index !== Number(at))}});
      }}>Remove filter</Button> : countFilters(filters) > 0 ? <Button variant="secondary" size="sm" onClick={clearFilters}>Clear filters</Button> : null}</div>;
  else if (phase === 'error' && !loading) results = <div className="data-inline-error"><p role="alert">{runError}</p>{needsTest ? <Button variant="secondary" size="sm" onClick={controller.testConnection} disabled={controller.testing}>{controller.testing ? 'Testing…' : 'Test connection'}</Button> : <Button variant="secondary" size="sm" onClick={reloadRun}>Try again</Button>}</div>;
  else if (shown && !loading && !pageRows.length) results = offset > 0
    ? <div className="data-empty-inline"><p>There are no more rows.</p><Button variant="secondary" onClick={() => setOffset(Math.max(0, offset - limit))}>Previous rows</Button></div>
    : <div className="data-empty-inline"><p>{countFilters(filters) ? 'No rows match these filters.' : 'There is nothing to show yet.'}</p>{countFilters(filters) ? <Button variant="secondary" onClick={clearFilters}>Clear filters</Button> : <Button variant="secondary" onClick={reloadRun}>Refresh</Button>}</div>;
  else if (!shown) results = <div className="data-pivot-skeleton" role="status" aria-label="Loading pivot">{[0, 1, 2, 3, 4, 5].map(index => <span className="data-skeleton-row" key={index}/>)}</div>;
  else results = <>
    <PivotTable columns={shown.columns} rows={pageRows} label={name} onDrill={readOnly ? undefined : (r, c) => void drill(r, c)}/>
    <footer className="data-grid-footer">
      <p className="data-muted" role="status">{pageRows.length ? 'Rows ' + (offset + 1).toLocaleString() + ' to ' + (offset + pageRows.length).toLocaleString() + (hasMore ? '' : ' of ' + (offset + pageRows.length).toLocaleString()) : ''}{hasMore && offset === 0 ? '. Showing the first ' + limit.toLocaleString() + ' rows.' : ''}</p>
      <div className="data-toolbar">
        <Button variant="ghost" size="sm" disabled={offset === 0 || loading} onClick={() => setOffset(Math.max(0, offset - limit))}>Previous</Button>
        <Button variant="ghost" size="sm" disabled={!hasMore || loading} onClick={() => setOffset(offset + limit)}>Next</Button>
      </div>
    </footer>
  </>;

  const primary = canEdit && spec ? (!saved ? <Button size="sm" onClick={() => setDialog('save')}><Save size={16} strokeWidth={1.5}/>Save view</Button>
    : dirty ? <Button size="sm" onClick={() => void saveChanges()} disabled={busy}><Save size={16} strokeWidth={1.5}/>{busy ? 'Saving…' : 'Save changes'}</Button> : null) : null;
  const segmentItems = views.views.filter(row => row.kind === 'segment' && row.connection_id === connectionId);
  return <section className="data-table-tab data-pivot-tab" aria-label={'Pivot ' + name}>
    <header><h1>{name}</h1><p className="data-muted">{sentence}</p></header>
    {conflict && <ConflictBanner error={conflict} reloading={reloading} onReload={() => void reload()} onDismiss={() => setConflict(null)} onSaveCopy={spec ? () => setDialog('copy') : undefined}/>}
    <div className="data-toolbar data-table-toolbar" role="toolbar" aria-label="Pivot tools">
      {primary}
      {canEdit && <JoinPanel workspaceId={workspaceId} connectionId={connectionId} spec={{...base, joins}} tables={tables} onAdd={join => change({joins: [...joins, join]})}/>}
      {canEdit && <Button variant="ghost" size="sm" aria-expanded={setup} aria-controls="pivot-setup" onClick={() => setSetup(value => !value)}><SlidersHorizontal size={16} strokeWidth={1.5}/>{setup ? 'Hide setup' : 'Show setup'}</Button>}
      <Button variant="ghost" size="sm" aria-expanded={sql} onClick={() => setSql(value => !value)} disabled={readOnly}><Code2 size={16} strokeWidth={1.5}/>Show SQL</Button>
      <Button variant="ghost" size="sm" onClick={() => void exportCsv()} disabled={!shown || exporting || loading}><Download size={16} strokeWidth={1.5}/>{exporting ? 'Exporting…' : 'Export CSV'}</Button>
      {clean && <Button variant="ghost" size="sm" onClick={reloadRun} disabled={loading}><RefreshCw size={16} strokeWidth={1.5}/>Refresh</Button>}
      {canEdit && saved && dirty && <Button variant="ghost" size="sm" onClick={resetPivot}><RotateCcw size={16} strokeWidth={1.5}/>Reset</Button>}
      {canEdit && saved && <DropdownMenu>
        <DropdownMenuTrigger asChild><Button variant="ghost" size="icon" aria-label="More actions for this pivot"><EllipsisVertical size={16} strokeWidth={1.5}/></Button></DropdownMenuTrigger>
        <DropdownMenuContent aria-label="Pivot actions" onCloseAutoFocus={() => {if (pending.current) {setDialog(pending.current); pending.current = null;}}}>
          <DropdownMenuItem onSelect={() => {pending.current = 'rename';}}><Pencil size={16} strokeWidth={1.5}/>Rename</DropdownMenuItem>
          <DropdownMenuItem onSelect={() => {pending.current = 'delete';}}><Trash2 size={16} strokeWidth={1.5}/>Delete</DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>}
    </div>
    {canEdit && <>
      {setup && <PivotBuilder pivot={pivot} onChange={next => change({pivot: next})} options={options} buckets={capabilities?.dialect?.date_bucket.buckets}
        columnKinds={capabilities?.dialect && capabilities.dialect.pivot ? capabilities.dialect.pivot.column_kinds : undefined}/>}
      <JoinList spec={{...base, joins}} infos={live.state.compile?.joins || []} onRemove={alias => change({joins: joins.filter(join => join.alias !== alias)})}/>
      <FilterChips filters={filters} context={{label: labelFor, kind: kindFor}} options={options} allowedOps={capabilities?.dialect?.filter_ops} onChange={next => change({filters: next})}
        segments={{items: segmentItems, baseTable: base.from.table, names: views.nameOf, onAdd: (id, negate) => void addSegment(id, negate)}}/>
    </>}
    {problem && <p className="data-field-error" role="alert">{problem}</p>}
    {message && <p className="data-hint" role="status">{message}</p>}
    {sql && <ShowSql compile={live.state.compile || sqlCompile} errors={errors}/>}
    <section className="data-results" aria-label="Results" aria-busy={loading}>
      <div className="data-results-body" data-stale={loading && shown ? true : undefined}>{results}</div>
    </section>
    <SaveViewDialog open={dialog === 'save'} title="Save pivot" submitLabel="Save view" helper="Everyone with access to this source can open it." onSubmit={viewName => createPivot(viewName, tab.id)} onClose={() => setDialog(null)}/>
    <SaveViewDialog open={dialog === 'copy'} title="Save as a new view" submitLabel="Save view" initialName={saved ? saved.name + ' copy' : ''} onSubmit={viewName => createPivot(viewName, tab.id)} onClose={() => setDialog(null)}/>
    <SaveViewDialog open={dialog === 'rename'} title="Rename pivot" submitLabel="Save" initialName={saved?.name} onSubmit={rename} onFail={error => {if (error.code === 'revision_conflict') {setConflict(error); return true;} return false;}} onClose={() => setDialog(null)}/>
    <Dialog open={dialog === 'delete'} onOpenChange={open => {if (!open && !busy) setDialog(null);}}><DialogContent className="data-dialog" closeDisabled={busy}>
      <DialogTitle>Delete pivot</DialogTitle><DialogDescription>{saved ? 'Delete ' + saved.name + '? People who use it will no longer find it.' : ''}</DialogDescription>
      {dropError && <p role="alert">{dropError}</p>}
      <div className="data-dialog-actions"><Button variant="ghost" disabled={busy} onClick={() => setDialog(null)}>Cancel</Button><Button variant="destructive" disabled={busy} onClick={() => void remove()}>{busy ? 'Deleting…' : 'Delete pivot'}</Button></div>
    </DialogContent></Dialog>
  </section>;
}
