import {useCallback, useEffect, useMemo, useRef, useState} from 'react';
import {Download, EllipsisVertical, Eye, Pencil, Save, Table2, Trash2} from 'lucide-react';
import {Button} from '../../components/ui/button';
import {Dialog, DialogContent, DialogDescription, DialogTitle} from '../../components/ui/dialog';
import {DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger} from '../../components/ui/dropdown-menu';
import {dataDownload, dataError, useDataScope} from '../../lib/data-studio';
import {archiveView, drillView, getView, runViewToCompletion, updateView, viewError, viewExportPath, type ViewDetail, type ViewError, type ViewUse} from '../../lib/data-views';
import {compactFilters, countFilters, humanize} from '../../lib/query-builder';
import {describeSegment} from '../../lib/segments';
import type {FilterGroup} from '../../lib/query-spec';
import type {QuerySpec} from '../../lib/query-spec';
import type {DataTab} from '../../lib/data-tabs';
import {tableKey} from './DataNavigation';
import AuditPanel from './AuditPanel';
import ConflictBanner from './ConflictBanner';
import FilterChips from './FilterChips';
import SaveViewDialog from './SaveViewDialog';
import {useSpecColumns} from './use-spec-columns';
import {useAutoSchema, type SourceController} from './use-source';
import type {ViewsController} from './use-views';

type Props = {
  workspaceId: string; connectionId: string; tab: DataTab; label: string; controller: SourceController; views: ViewsController; canEdit: boolean;
  onClose: () => void; onArchived: (id: string) => void;
  onDrill: (spec: QuerySpec, title: string, note?: string) => void;
  /** Opens another view's tab (a segment this one uses, or a view that uses this one). */ onOpenView: (use: {id: string; kind: ViewUse['kind']}) => void;
  /** Opens an unsaved pivot on the segment's table with the segment already applied. */ onUseForPivot: (segment: ViewDetail) => void;
};
const Skeleton = ({label}: {label: string}) => <div className="data-list data-gate" role="status" aria-label={label}>{[0, 1, 2, 3].map(index => <span className="data-skeleton-row" key={index}/>)}</div>;
const slug = (name: string) => (name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '') || 'segment').slice(0, 60);

/** Segment tab: loads the saved segment, then shows its definition, members, actions and audit trail. */
export default function SegmentTab(props: Props) {
  const {tab, workspaceId, label, onClose} = props, scope = useDataScope();
  const [detail, setDetail] = useState<ViewDetail | null>(null), [load, setLoad] = useState<{phase: 'loading' | 'ready' | 'error'; error: string}>({phase: 'loading', error: ''});
  const [attempt, setAttempt] = useState(0), [generation, setGeneration] = useState(0), [reloading, setReloading] = useState(false), [announce, setAnnounce] = useState('');
  useEffect(() => {
    if (!tab.viewId) return;
    const abort = new AbortController();
    setLoad({phase: 'loading', error: ''});
    getView(workspaceId, tab.viewId, abort.signal).then(result => {if (!abort.signal.aborted) {setDetail(result); setLoad({phase: 'ready', error: ''});}})
      .catch(reason => {if (!abort.signal.aborted) setLoad({phase: 'error', error: viewError(reason).status === 404 ? 'This segment is no longer available.' : viewError(reason).message});});
    return () => abort.abort();
  }, [workspaceId, tab.viewId, attempt]);
  const reload = useCallback(async () => {
    if (!tab.viewId) return;
    setReloading(true);
    try {
      const next = await getView(workspaceId, tab.viewId);
      if (!scope.current()) return;
      setDetail(next); setGeneration(value => value + 1); setAnnounce('Reloaded revision ' + next.revision + '.'); props.views.upsert(next);
    } catch (reason) {if (scope.current()) setAnnounce(viewError(reason).message);}
    finally {if (scope.current()) setReloading(false);}
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workspaceId, tab.viewId]);
  if (load.phase === 'loading' && !detail) return <Skeleton label={'Loading ' + label}/>;
  if (load.phase === 'error' && !detail) return <div className="data-inline-error data-gate"><p role="alert">{load.error}</p><Button variant="secondary" size="sm" onClick={() => setAttempt(value => value + 1)}>Try again</Button><Button variant="ghost" size="sm" onClick={onClose}>Close tab</Button></div>;
  if (!detail) return <Skeleton label={'Loading ' + label}/>;
  return <><p className="visually-hidden" role="status">{announce}</p><SegmentView key={generation + ':' + detail.id} {...props} detail={detail} reload={reload} reloading={reloading}/></>;
}

function SegmentView({workspaceId, connectionId, controller, views, canEdit, onArchived, onDrill, onOpenView, onUseForPivot, detail, reload, reloading}: Props & {detail: ViewDetail; reload: () => Promise<void>; reloading: boolean}) {
  const scope = useDataScope(), editable = canEdit && detail.can_edit;
  const [saved, setSaved] = useState(detail), [filters, setFilters] = useState<FilterGroup>(detail.spec.filters), [baseline, setBaseline] = useState(JSON.stringify(compactFilters(detail.spec.filters)));
  const [editing, setEditing] = useState(false), [busy, setBusy] = useState(false), [conflict, setConflict] = useState<ViewError | null>(null), [problem, setProblem] = useState(''), [message, setMessage] = useState('');
  const [dialog, setDialog] = useState<'rename' | 'delete' | null>(null), [dropError, setDropError] = useState(''), [inUse, setInUse] = useState<ViewUse[]>([]);
  const [count, setCount] = useState<{phase: 'idle' | 'running' | 'done' | 'error'; value?: number; revision?: number; error?: string}>({phase: 'idle'});
  const pending = useRef<'rename' | 'delete' | null>(null), [exporting, setExporting] = useState(false), [opening, setOpening] = useState(false), [runs, setRuns] = useState(0);
  const spec = saved.spec, table = controller.schema?.tables;
  useAutoSchema(controller, editing && editable);
  const { labelFor, kindFor, options } = useSpecColumns(spec.from, spec.joins, table || []);
  const dirty = JSON.stringify(compactFilters(filters)) !== baseline;
  const names = views.nameOf;
  const lines = useMemo(() => describeSegment(filters, {label: labelFor, kind: kindFor}, id => id === saved.id ? undefined : names(id) ?? saved.references.find(row => row.id === id && row.state === 'ok')?.name), [filters, labelFor, kindFor, names, saved]);
  const tableName = humanize(spec.from.table), schemaReady = controller.schemaStatus === 'ready' && !!table?.find(row => tableKey(row) === spec.from.table || row.name === spec.from.table);

  async function save() {
    if (busy || !countFilters(filters)) return;
    setBusy(true); setConflict(null); setProblem(''); setMessage('');
    try {
      const next = await updateView(workspaceId, saved.id, {revision: saved.revision, spec: {...saved.spec, filters: compactFilters(filters)}});
      if (!scope.current()) return;
      setSaved(next); setBaseline(JSON.stringify(compactFilters(next.spec.filters))); setFilters(next.spec.filters); views.upsert(next); setRuns(value => value + 1);
      setMessage('Saved changes.' + (next.affected_views ? ' ' + next.affected_views + (next.affected_views === 1 ? ' view uses' : ' views use') + ' this segment.' : ''));
    } catch (reason) {
      if (!scope.current()) return;
      const error = viewError(reason);
      if (error.code === 'revision_conflict') setConflict(error); else setProblem(error.errors?.[0]?.message || error.message);
    } finally {if (scope.current()) setBusy(false);}
  }
  async function countMembers() {
    if (count.phase === 'running') return;
    const abort = scope.controller();
    setCount({phase: 'running'}); setProblem('');
    try {
      const job = await runViewToCompletion(workspaceId, saved.id, {mode: 'count', signal: abort.signal});
      if (!scope.current() || abort.signal.aborted) return;
      const cell = job.result?.rows?.[0]?.[0];
      if (job.status === 'completed' && typeof cell === 'number') setCount({phase: 'done', value: cell, revision: saved.revision});
      else setCount({phase: 'error', error: job.error || 'The count could not be completed.'});
      setRuns(value => value + 1);
    } catch (reason) {if (scope.current() && !abort.signal.aborted) setCount({phase: 'error', error: viewError(reason).message});}
    finally {scope.release(abort);}
  }
  async function showRows() {
    if (opening) return;
    setOpening(true); setProblem('');
    try {
      const result = await drillView(workspaceId, saved.id, {});
      if (scope.current()) onDrill(result.spec, 'Members of ' + saved.name, result.exact ? undefined : result.notes.join(' '));
    } catch (reason) {if (scope.current()) setProblem(viewError(reason).message);}
    finally {if (scope.current()) setOpening(false);}
  }
  async function exportCsv() {
    if (exporting) return;
    const abort = scope.controller();
    setExporting(true); setProblem('');
    try {
      const job = await runViewToCompletion(workspaceId, saved.id, {mode: 'rows', signal: abort.signal});
      if (!scope.current() || abort.signal.aborted) return;
      if (job.status !== 'completed') {setProblem(job.error || 'The export could not be completed.'); return;}
      await dataDownload(workspaceId, job.id, abort.signal, () => scope.current(), {path: viewExportPath(workspaceId, saved.id, job.id), filename: slug(saved.name) + '.csv'});
      setRuns(value => value + 1);
    } catch (reason) {if (scope.current()) setProblem(viewError(reason).message || dataError(reason));}
    finally {scope.release(abort); if (scope.current()) setExporting(false);}
  }
  async function rename(name: string) {
    const next = await updateView(workspaceId, saved.id, {revision: saved.revision, name});
    setSaved(next); views.upsert(next); setMessage('Renamed to ' + next.name + '.'); setRuns(value => value + 1);
  }
  async function remove() {
    if (busy) return;
    setBusy(true); setDropError(''); setInUse([]);
    try {
      await archiveView(workspaceId, saved.id, saved.revision);
      if (!scope.current()) return;
      views.remove(saved.id); setDialog(null); onArchived(saved.id);
    } catch (reason) {
      if (!scope.current()) return;
      const error = viewError(reason);
      if (error.code === 'revision_conflict') {setConflict(error); setDialog(null);}
      else if (error.code === 'segment_in_use') {setInUse(error.used_by || []); setDropError(error.message);}
      else setDropError(error.message);
    } finally {if (scope.current()) setBusy(false);}
  }
  const stale = count.phase === 'done' && count.revision !== saved.revision;
  const segments = {items: views.views.filter(row => row.kind === 'segment' && row.connection_id === connectionId && row.id !== saved.id), baseTable: spec.from.table, names, onAdd: (id: string, negate: boolean) => setFilters(previous => ({...previous, items: [...previous.items, negate ? {segment: id, negate: true} : {segment: id}]}))};
  const emptyFilters = !countFilters(filters);
  return <section className="data-table-tab data-segment-tab" aria-label={'Segment ' + saved.name}>
    <header><h1>{saved.name}</h1><p className="data-muted">Segment on {tableName} · revision {saved.revision}</p></header>
    {conflict && <ConflictBanner error={conflict} reloading={reloading} onReload={() => void reload()} onDismiss={() => setConflict(null)}/>}
    <div className="data-toolbar data-table-toolbar" role="toolbar" aria-label="Segment tools">
      {dirty && editable ? <Button size="sm" onClick={() => void save()} disabled={busy || emptyFilters}><Save size={16} strokeWidth={1.5}/>{busy ? 'Saving…' : 'Save changes'}</Button>
        : editable ? <Button size="sm" onClick={() => void showRows()} disabled={opening}><Eye size={16} strokeWidth={1.5}/>{opening ? 'Opening…' : 'Show underlying rows'}</Button> : null}
      {dirty && editable && <Button variant="ghost" size="sm" onClick={() => {setFilters(JSON.parse(baseline)); setMessage('Went back to the saved segment.');}}>Reset</Button>}
      {editable && <Button variant="ghost" size="sm" aria-expanded={editing} onClick={() => setEditing(value => !value)}><Pencil size={16} strokeWidth={1.5}/>{editing ? 'Done editing' : 'Edit filters'}</Button>}
      {editable && <Button variant="ghost" size="sm" onClick={() => onUseForPivot(saved)} disabled={dirty}><Table2 size={16} strokeWidth={1.5}/>Use as source for a pivot</Button>}
      <Button variant="ghost" size="sm" onClick={() => void exportCsv()} disabled={exporting || dirty}><Download size={16} strokeWidth={1.5}/>{exporting ? 'Exporting…' : 'Export CSV'}</Button>
      {editable && <DropdownMenu>
        <DropdownMenuTrigger asChild><Button variant="ghost" size="icon" aria-label="More actions for this segment"><EllipsisVertical size={16} strokeWidth={1.5}/></Button></DropdownMenuTrigger>
        <DropdownMenuContent aria-label="Segment actions" onCloseAutoFocus={() => {if (pending.current) {setDialog(pending.current); pending.current = null;}}}>
          <DropdownMenuItem onSelect={() => {pending.current = 'rename';}}><Pencil size={16} strokeWidth={1.5}/>Rename</DropdownMenuItem>
          <DropdownMenuItem onSelect={() => {setDropError(''); setInUse([]); pending.current = 'delete';}}><Trash2 size={16} strokeWidth={1.5}/>Delete</DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>}
    </div>
    {dirty && editable && <p className="data-muted data-toolbar-reason">Save your changes to use the actions below.</p>}
    {problem && <p className="data-field-error" role="alert">{problem}</p>}
    {message && <p className="data-hint" role="status">{message}</p>}

    <section className="data-segment-section" aria-labelledby="segment-definition">
      <h2 id="segment-definition" className="data-section-title">Definition</h2>
      {lines.length ? <ul className="data-definition">{lines.map((line, index) => <li key={index} style={{paddingLeft: line.depth * 16}}>
        {line.segmentId && names(line.segmentId) ? <button type="button" className="data-link" onClick={() => onOpenView({id: line.segmentId!, kind: 'segment'})}>{line.text}</button> : line.text}</li>)}</ul>
        : <p className="data-muted">This segment has no filters, so every row is a member.</p>}
      {editing && editable && (schemaReady ? <FilterChips filters={filters} context={{label: labelFor, kind: kindFor}} options={options} onChange={setFilters} segments={segments}/>
        : controller.schemaStatus === 'idle' || controller.schemaStatus === 'loading' ? <Skeleton label="Loading the tables"/>
        : <div className="data-empty-inline data-gate"><p>Load the tables to edit the filters.</p><Button variant="secondary" onClick={controller.schemaStatus === 'error' && controller.needsTest ? controller.testConnection : controller.loadSchema}>{controller.schemaStatus === 'error' ? 'Try again' : 'Load tables'}</Button></div>)}
      {saved.references.some(row => row.state === 'missing') && <p className="data-field-error" role="alert">A segment this one uses was deleted. Edit the filters to remove it.</p>}
    </section>

    <section className="data-segment-section" aria-labelledby="segment-members">
      <h2 id="segment-members" className="data-section-title">Members</h2>
      <div className="data-toolbar">
        <Button variant="secondary" size="sm" onClick={() => void countMembers()} disabled={count.phase === 'running' || dirty}>{count.phase === 'running' ? 'Counting…' : 'Count members'}</Button>
        <p className="data-member-count" role="status" data-stale={stale || undefined}>
          {count.phase === 'done' ? (count.value ?? 0).toLocaleString() + ((count.value ?? 0) === 1 ? ' member' : ' members') + (stale ? '. Count again after your changes.' : '') : count.phase === 'error' ? '' : ''}
        </p>
      </div>
      {count.phase === 'error' && <p className="data-field-error" role="alert">{count.error}</p>}
    </section>

    {editable && saved.used_by.length > 0 && <section className="data-segment-section" aria-labelledby="segment-used">
      <h2 id="segment-used" className="data-section-title">Used by</h2>
      <ul className="data-definition">{saved.used_by.map(use => <li key={use.id}><button type="button" className="data-link" onClick={() => onOpenView(use)}>{use.name}</button> <span className="data-muted">{use.kind === 'pivot' ? 'Pivot' : use.kind === 'segment' ? 'Segment' : 'Table view'}</span></li>)}</ul>
    </section>}

    <AuditPanel workspaceId={workspaceId} viewId={saved.id} canSeeHistory={editable} refreshKey={saved.revision + ':' + runs} names={names} context={{label: labelFor, kind: kindFor}}/>

    <SaveViewDialog open={dialog === 'rename'} title="Rename segment" submitLabel="Save" initialName={saved.name} onSubmit={rename} onFail={error => {if (error.code === 'revision_conflict') {setConflict(error); return true;} return false;}} onClose={() => setDialog(null)}/>
    <Dialog open={dialog === 'delete'} onOpenChange={open => {if (!open && !busy) setDialog(null);}}><DialogContent className="data-dialog" closeDisabled={busy}>
      <DialogTitle>Delete segment</DialogTitle><DialogDescription>{'Delete ' + saved.name + '? Filters that use it will show that it was removed.'}</DialogDescription>
      {dropError && <p role="alert">{dropError}</p>}
      {inUse.length > 0 && <ul className="data-definition" aria-label="Views that use this segment">{inUse.map(use => <li key={use.id}><button type="button" className="data-link" onClick={() => {setDialog(null); onOpenView(use);}}>{use.name}</button></li>)}</ul>}
      <div className="data-dialog-actions"><Button variant="ghost" disabled={busy} onClick={() => setDialog(null)}>Cancel</Button><Button variant="destructive" disabled={busy || inUse.length > 0} onClick={() => void remove()}>{busy ? 'Deleting…' : 'Delete segment'}</Button></div>
    </DialogContent></Dialog>
  </section>;
}
