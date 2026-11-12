import {useCallback, useEffect, useMemo, useRef, useState} from 'react';
import {createPortal} from 'react-dom';
import {MessageSquare, Settings2, Wrench} from 'lucide-react';
import {Button} from '../../components/ui/button';
import {DropdownMenu, DropdownMenuCheckboxItem, DropdownMenuContent, DropdownMenuTrigger} from '../../components/ui/dropdown-menu';
import {Dialog, DialogContent, DialogDescription, DialogTitle} from '../../components/ui/dialog';
import {Input} from '../../components/ui/input';
import {dataBody, dataError, dataPath, dataRequest, readDataSelection, rememberDataSelection, useDataScope, type DataBootstrap, type DataConnection, type DataQuery, type DataQueryDraft, type DataSnapshot, type DataWorkspace} from '../../lib/data-studio';
import {closeTab, drillTabId, initialTabs, openTab, pivotDraftTabId, pivotViewTabId, pruneTabs, queryTabId, readTabs, rememberTabs, schemaFor, segmentTabId, selectTab, tableTabId, type DataTab, type DataTabsState} from '../../lib/data-tabs';
import {createView, type ViewDetail, type ViewSummary, type ViewUse} from '../../lib/data-views';
import type {QuerySpec, SchemaTable} from '../../lib/query-spec';
import DatabaseStudio from './DatabaseStudio';
import TeamSettings from './TeamSettings';
import BIStudio from './BIStudio';
import DataCopilot from './DataCopilot';
import CopilotPanel from './CopilotPanel';
import DataNavigation, {tableKey, tableLabel, type NamedRecord} from './DataNavigation';
import DataTabs from './DataTabs';
import TablesOverview from './TablesOverview';
import TableTab from './TableTab';
import PivotTab from './PivotTab';
import SegmentTab from './SegmentTab';
import {useViews} from './use-views';
import {ADVANCED_TRIGGER_ID} from './AdvancedSql';
import SourceDialog from './SourceDialog';
import ArchiveSourceDialog from './ArchiveSourceDialog';
import {useDataSource} from './use-source';
import './data-studio.css';
import './data-query.css';

type Props = {
  ownerId?: string; visible: boolean; observing?: boolean; locked?: boolean;
  /** undefined: render the navigation inline (standalone). null: the shell sidebar is closed. An element: render into the shell sidebar. */
  navigationHost?: HTMLElement | null;
  /** Called after the person picks something in the sidebar, so a floating sidebar can close. */
  onNavigate?: () => void;
};
export default function DataStudio({ownerId, visible, observing = true, locked = false, navigationHost, onNavigate}: Props) {
  const [blocked, setBlocked] = useState(false), [retained, setRetained] = useState<{ownerId?: string; drafts: Record<string, DataQueryDraft>} | null>(null);
  useEffect(() => {
    const lock = () => {setBlocked(true); setRetained(null);}, unlock = () => setBlocked(false);
    window.addEventListener('termx-session-locked', lock); window.addEventListener('termx-signed-out', lock); window.addEventListener('termx-session-unlocked', unlock);
    return () => {window.removeEventListener('termx-session-locked', lock); window.removeEventListener('termx-signed-out', lock); window.removeEventListener('termx-session-unlocked', unlock);};
  }, []);
  useEffect(() => {setBlocked(locked); setRetained(null);}, [locked, ownerId]);
  if (!visible) return null;
  if (locked || blocked) return <section className="data-studio data-suspended"><p role="status">Unlock to open Data Studio.</p></section>;
  if (!observing) return <section className="data-studio data-suspended"><p role="status">Data Studio is paused.</p></section>;
  return <DataSession key={ownerId || 'managed'} ownerId={ownerId} navigationHost={navigationHost} onNavigate={onNavigate}
    retainedDrafts={retained && retained.ownerId === ownerId ? retained.drafts : {}}
    onRetain={(key, draft) => setRetained(previous => ({ownerId, drafts: {...(previous && previous.ownerId === ownerId ? previous.drafts : {}), [key]: draft}}))}/>;
}

export const NOTICE_MS = 8000;
const viewTitle: Record<'bi' | 'team', string> = {bi: 'Reports', team: 'Team'};
/** Whether the person last left the SQL editor open. A boolean only; SQL text is never stored. */
export const advancedKey = (ownerId?: string) => 'termx.data.advanced.' + (ownerId || 'managed');
function readAdvanced(ownerId?: string) {try {return localStorage.getItem(advancedKey(ownerId)) === 'true';} catch {return false;}}
function rememberAdvanced(ownerId: string | undefined, open: boolean) {try {localStorage.setItem(advancedKey(ownerId), String(open));} catch {/* storage may be blocked */}}
const named = (rows: unknown[]): NamedRecord[] => rows.filter((row): row is NamedRecord => !!row && typeof row === 'object' && typeof (row as NamedRecord).id === 'string' && typeof (row as NamedRecord).name === 'string');

function DataSession({ownerId, navigationHost, onNavigate, retainedDrafts, onRetain}: {ownerId?: string; navigationHost?: HTMLElement | null; onNavigate?: () => void; retainedDrafts: Record<string, DataQueryDraft>; onRetain: (key: string, draft: DataQueryDraft) => void}) {
  const [selection, setSelection] = useState(() => readDataSelection(ownerId)), [bootstrap, setBootstrap] = useState<DataBootstrap | null>(null), [snapshot, setSnapshot] = useState<DataSnapshot | null>(null);
  const [loading, setLoading] = useState(true), [error, setError] = useState(''), [refresh, setRefresh] = useState(0), [dialog, setDialog] = useState<'create' | 'rename' | 'archive' | null>(null), [name, setName] = useState(''), [busy, setBusy] = useState(false), [dialogError, setDialogError] = useState('');
  const [copilot, setCopilot] = useState(false), [queryDraft, setQueryDraft] = useState<{tabId: string; draft: DataQueryDraft} | null>(null), [activeQueryId, setActiveQueryId] = useState(''), [manageDialog, setManageDialog] = useState(false);
  const [sourceDialog, setSourceDialog] = useState<{open: boolean; record?: DataConnection}>({open: false}), [archiveSource, setArchiveSource] = useState<DataConnection | undefined>(), [notice, setNotice] = useState('');
  const [tabsByWorkspace, setTabsByWorkspace] = useState<Record<string, DataTabsState>>({});
  const [advancedOn, setAdvancedOn] = useState(() => readAdvanced(ownerId)), openedFromMenu = useRef(false), [focusRequest, setFocusRequest] = useState({tabId: '', n: 0}), [combineRequest, setCombineRequest] = useState({tabId: '', n: 0});
  const setAdvanced = useCallback((open: boolean) => {setAdvancedOn(open); rememberAdvanced(ownerId, open);}, [ownerId]);
  const scope = useDataScope(), snapshotTicket = useRef(0), flight = useRef(false), workspaceId = selection.workspaceId, connectionId = selection.connectionId;
  const currentWorkspace = bootstrap?.workspaces.find(row => row.id === workspaceId), role = snapshot?.workspace.role || currentWorkspace?.role || 'viewer', writable = role !== 'viewer';
  const source = snapshot?.connections.find(row => row.id === connectionId), engines = bootstrap?.engines || [], engine = engines.find(row => row.id === source?.engine);
  const requestRefresh = useCallback(() => setRefresh(value => value + 1), []);
  const controller = useDataSource(workspaceId, source, requestRefresh);
  const viewsEnabled = bootstrap?.capabilities?.views === true, views = useViews(workspaceId, viewsEnabled), draftCounter = useRef(0);

  useEffect(() => {rememberDataSelection(ownerId, selection);}, [ownerId, selection]);
  // A notice is short-lived: it clears itself after a few seconds and whenever the person moves somewhere else. It is never stored.
  useEffect(() => {if (!notice) return; const timer = setTimeout(() => setNotice(''), NOTICE_MS); return () => clearTimeout(timer);}, [notice]);
  useEffect(() => {const pop = () => setSelection(readDataSelection(ownerId)); window.addEventListener('popstate', pop); return () => window.removeEventListener('popstate', pop);}, [ownerId]);
  useEffect(() => {
    const abort = scope.controller(); setLoading(true); setError('');
    void dataRequest<DataBootstrap>(dataPath(undefined, '/bootstrap'), {signal: abort.signal}).then(result => {
      if (!scope.current() || abort.signal.aborted) return;
      setBootstrap(result);
      setSelection(value => ({...value, workspaceId: result.workspaces.some(row => row.id === value.workspaceId) ? value.workspaceId : result.workspaces[0]?.id || '', connectionId: result.workspaces.some(row => row.id === value.workspaceId) ? value.connectionId : ''}));
    }).catch(reason => {if (scope.current() && !abort.signal.aborted) setError(dataError(reason));}).finally(() => {if (scope.current() && !abort.signal.aborted) setLoading(false); scope.release(abort);});
    return () => abort.abort();
  }, [refresh]);
  useEffect(() => {
    const ticket = ++snapshotTicket.current;
    setSnapshot(previous => previous?.workspace.id === workspaceId ? previous : null);
    if (!workspaceId) return;
    const abort = scope.controller();
    void dataRequest<DataSnapshot>(dataPath(workspaceId), {signal: abort.signal}).then(value => {
      if (scope.current() && !abort.signal.aborted && ticket === snapshotTicket.current) {
        setSnapshot(value);
        setSelection(previous => ({...previous, connectionId: value.connections.some(row => row.id === previous.connectionId) ? previous.connectionId : value.connections[0]?.id || ''}));
      }
    }).catch(reason => {
      if (scope.current() && !abort.signal.aborted && ticket === snapshotTicket.current) {setSnapshot(null); setError(dataError(reason));}
    }).finally(() => scope.release(abort));
    return () => abort.abort();
  }, [workspaceId, refresh]);

  // Open tabs. Only identifiers are stored; the current source decides which tabs are shown.
  const stored = useMemo(() => workspaceId ? tabsByWorkspace[workspaceId] || readTabs(ownerId, workspaceId) : initialTabs(), [tabsByWorkspace, workspaceId, ownerId]);
  const updateTabs = useCallback((change: (state: DataTabsState) => DataTabsState) => {
    if (!workspaceId) return;
    setTabsByWorkspace(previous => {
      const base = previous[workspaceId] || readTabs(ownerId, workspaceId), next = change(base);
      if (next === base) return previous;
      rememberTabs(ownerId, workspaceId, next);
      return {...previous, [workspaceId]: next};
    });
  }, [ownerId, workspaceId]);
  useEffect(() => {
    if (!snapshot || snapshot.workspace.id !== workspaceId) return;
    updateTabs(state => pruneTabs(state, tab => tab.kind === 'table' || tab.kind === 'drill' || tab.kind === 'pivot' && !tab.viewId ? snapshot.connections.some(row => row.id === tab.connectionId) : tab.kind === 'pivot' || tab.kind === 'segment' ? true : !tab.queryId || snapshot.queries.some(row => row.id === tab.queryId)));
  }, [snapshot, workspaceId, updateTabs]);
  // A view someone else deleted disappears from the list; its open tab goes with it.
  useEffect(() => {
    if (views.status !== 'ready') return;
    const gone = (tab: DataTab) => (tab.kind === 'pivot' || tab.kind === 'segment') && !!tab.viewId && !views.byId(tab.viewId);
    const current = tabsByWorkspace[workspaceId] || (workspaceId ? readTabs(ownerId, workspaceId) : null);
    if (!current?.tabs.some(gone)) return;
    updateTabs(state => pruneTabs(state, tab => !gone(tab)));
    setNotice('A view you had open was deleted.');
  }, [views.status, views.views]); // eslint-disable-line react-hooks/exhaustive-deps
  const queries = snapshot?.queries || [];
  const shownTabs = useMemo<DataTabsState>(() => {
    const viewHere = (tab: DataTab) => {const row = tab.viewId ? views.byId(tab.viewId) : undefined; return row ? row.connection_id === connectionId : views.status !== 'ready';};
    const tabs = stored.tabs.filter(tab => tab.kind === 'overview' || (tab.kind === 'table' || tab.kind === 'drill') && tab.connectionId === connectionId || tab.kind === 'pivot' && (tab.viewId ? viewHere(tab) : tab.connectionId === connectionId) || tab.kind === 'segment' && viewHere(tab)
      || tab.kind === 'query' && (!tab.queryId || queries.find(row => row.id === tab.queryId)?.connection_id === connectionId || !snapshot));
    return {tabs, activeId: tabs.some(tab => tab.id === stored.activeId) ? stored.activeId : 'overview'};
  }, [stored, connectionId, queries, snapshot, views.status, views.views]); // eslint-disable-line react-hooks/exhaustive-deps
  const tabTitle = (tab: DataTab) => tab.kind === 'overview' ? 'Overview' : tab.kind === 'drill' ? tab.title || 'Rows' : tab.kind === 'pivot' ? (tab.viewId ? views.nameOf(tab.viewId) || 'Pivot' : 'New pivot on ' + tableLabel({name: tab.table!, schema: tab.schema})) : tab.kind === 'segment' ? (tab.viewId && views.nameOf(tab.viewId)) || 'Segment' : tab.kind === 'table' ? tableLabel({name: tab.table!, schema: tab.schema}) : tab.queryId ? queries.find(row => row.id === tab.queryId)?.name || 'Saved query' : 'New query';
  const showDatabase = (tab?: DataTab) => {
    setNotice('');
    if (tab) updateTabs(state => openTab(state, tab));
    setSelection(previous => ({...previous, view: 'database'}));
    onNavigate?.();
  };

  function selectWorkspace(id: string) {setNotice(''); setError(''); setSnapshot(null); setSelection(value => ({...value, workspaceId: id, connectionId: ''})); setQueryDraft(null); setActiveQueryId(''); setCopilot(false);}
  function openDialog(value: 'create' | 'rename' | 'archive') {setName(value === 'create' ? '' : currentWorkspace?.name || ''); setDialogError(''); setDialog(value);}
  async function saveWorkspace() {
    if (flight.current) return;
    const target = dialog;
    if (!target || target !== 'create' && !currentWorkspace) return;
    flight.current = true; setBusy(true); setDialogError('');
    const abort = scope.controller();
    try {
      const path = target === 'create' ? dataPath(undefined, '/workspaces') : dataPath(workspaceId);
      const result = await dataRequest<DataWorkspace>(path, {...dataBody(target === 'create' ? 'POST' : target === 'archive' ? 'DELETE' : 'PATCH', target === 'create' ? {name: name.trim()} : {revision: currentWorkspace!.revision, ...(target === 'rename' ? {name: name.trim()} : {})}), signal: abort.signal});
      if (!scope.current() || abort.signal.aborted) return;
      setDialog(null);
      if (target === 'create') setSelection(value => ({...value, workspaceId: result.id, connectionId: '', view: 'database'}));
      if (target === 'archive') setSelection(value => ({...value, workspaceId: '', connectionId: ''}));
      setRefresh(value => value + 1);
    } catch (reason) {
      if (scope.current() && !abort.signal.aborted) setDialogError(dataError(reason));
    } finally {
      flight.current = false;
      if (scope.current()) setBusy(false);
      scope.release(abort);
    }
  }
  function openQuery(draft: DataQueryDraft) {
    const tab: DataTab = draft.id ? {id: queryTabId(draft.id), kind: 'query', queryId: draft.id} : {id: queryTabId(), kind: 'query'};
    setQueryDraft({tabId: tab.id, draft});
    setSelection(value => ({...value, view: 'database', connectionId: draft.connection_id}));
    updateTabs(state => openTab(state, tab));
    if (writable) setAdvanced(true);
  }
  function openSavedQuery(row: DataQuery) {
    if (row.connection_id !== connectionId) setSelection(value => ({...value, connectionId: row.connection_id}));
    if (writable) setAdvanced(true);
    showDatabase({id: queryTabId(row.id), kind: 'query', queryId: row.id});
  }
  /** Show the SQL editor over a query tab. A table or overview tab gets a new query tab first. */
  function openAdvanced() {
    if (!writable) return;
    setAdvanced(true);
    let target = shownTabs.tabs.find(tab => tab.id === shownTabs.activeId && tab.kind === 'query')?.id;
    if (!target || selection.view !== 'database') {
      target = queryTabId();
      showDatabase({id: target, kind: 'query'});
    }
    setFocusRequest(previous => ({tabId: target!, n: previous.n + 1}));
  }
  const activeKind = shownTabs.tabs.find(tab => tab.id === shownTabs.activeId)?.kind, advancedShown = writable && advancedOn && activeKind === 'query' && selection.view === 'database';
  function openPivotDraft(table: SchemaTable, segmentId?: string) {
    draftCounter.current += 1;
    showDatabase({id: pivotDraftTabId(connectionId, tableKey(table), draftCounter.current), kind: 'pivot', connectionId, table: table.name, ...(table.schema ? {schema: table.schema} : {}), ...(segmentId ? {segmentId} : {})});
  }
  const openPivot = (row: ViewSummary) => showDatabase({id: pivotViewTabId(row.id), kind: 'pivot', viewId: row.id, connectionId: row.connection_id});
  const openSegment = (row: Pick<ViewSummary, 'id'>) => showDatabase({id: segmentTabId(row.id), kind: 'segment', viewId: row.id});
  const openUse = (use: ViewUse | Pick<ViewSummary, 'id' | 'kind'>) => {if (use.kind === 'pivot') showDatabase({id: pivotViewTabId(use.id), kind: 'pivot', viewId: use.id}); else if (use.kind === 'segment') openSegment(use);};
  const schemaPart = (table: string) => {const schema = schemaFor(controller.schema?.tables, table); return schema ? {schema} : {};};
  function useForPivot(segment: ViewDetail) {
    draftCounter.current += 1;
    showDatabase({id: pivotDraftTabId(segment.connection_id, segment.spec.from.table, draftCounter.current), kind: 'pivot', connectionId: segment.connection_id, table: segment.spec.from.table, ...schemaPart(segment.spec.from.table), segmentId: segment.id});
  }
  function openDrill(spec: QuerySpec, title: string, note?: string) {
    draftCounter.current += 1;
    showDatabase({id: drillTabId(draftCounter.current), kind: 'drill', connectionId, table: spec.from.table, ...schemaPart(spec.from.table), spec, title, ...(note ? {note} : {})});
  }
  /** Copilot proposals open as new, unsaved tabs. The person's own click is what opens them. */
  function applyProposalTable(spec: QuerySpec, title: string, note: string) {
    draftCounter.current += 1;
    const schema = schemaFor(controller.schema?.tables, spec.from.table);
    if (spec.source !== connectionId) setSelection(value => ({...value, connectionId: spec.source}));
    showDatabase({id: drillTabId(draftCounter.current), kind: 'drill', connectionId: spec.source, table: spec.from.table, ...(schema ? {schema} : {}), spec, title, note});
  }
  function openProposalPivot(spec: QuerySpec, title: string) {
    draftCounter.current += 1;
    const schema = schemaFor(controller.schema?.tables, spec.from.table);
    if (spec.source !== connectionId) setSelection(value => ({...value, connectionId: spec.source}));
    showDatabase({id: pivotDraftTabId(spec.source, (schema ? schema + '.' : '') + spec.from.table, draftCounter.current), kind: 'pivot', connectionId: spec.source, table: spec.from.table, ...(schema ? {schema} : {}), spec, title});
  }
  async function saveProposalSegment(spec: QuerySpec, name: string, description: string) {
    const detail = await createView(workspaceId, {kind: 'segment', name, description, spec});
    views.refresh();
    setNotice('Saved segment ' + detail.name + '.');
    return detail;
  }
  function pivotSaved(detail: ViewDetail, fromTabId: string) {
    updateTabs(state => openTab(fromTabId.startsWith('pivot:new:') ? closeTab(state, fromTabId) : state, {id: pivotViewTabId(detail.id), kind: 'pivot', viewId: detail.id, connectionId: detail.connection_id}));
    setNotice('Saved pivot ' + detail.name + '.');
  }
  function viewArchived(id: string, kind: 'pivot' | 'segment') {
    updateTabs(state => closeTab(state, kind === 'pivot' ? pivotViewTabId(id) : segmentTabId(id)));
    setNotice(kind === 'pivot' ? 'Pivot deleted.' : 'Segment deleted.');
  }
  function openTable(table: SchemaTable) {showDatabase({id: tableTabId(connectionId, table.name, table.schema), kind: 'table', connectionId, table: table.name, ...(table.schema ? {schema: table.schema} : {})});}
  function combineTable(table: SchemaTable) {
    openTable(table);
    setCombineRequest(previous => ({tabId: tableTabId(connectionId, table.name, table.schema), n: previous.n + 1}));
  }

  const reports = useMemo(() => named(snapshot?.reports || []), [snapshot]), dashboards = useMemo(() => named(snapshot?.dashboards || []), [snapshot]);
  const nav = <DataNavigation loading={loading} workspaces={bootstrap?.workspaces || []} workspaceId={workspaceId} workspaceBusy={busy} onWorkspace={selectWorkspace} onCreateWorkspace={() => openDialog('create')}
    role={role} connections={snapshot?.connections || []} connectionId={connectionId} onConnection={id => {setNotice(''); setSelection(previous => ({...previous, connectionId: id})); onNavigate?.();}} onConnect={() => setSourceDialog({open: true})}
    source={controller} queries={queries} reports={reports} dashboards={dashboards} view={selection.view} activeTabId={shownTabs.activeId}
    onOpenOverview={() => {updateTabs(state => selectTab(state, 'overview')); showDatabase();}} onOpenTable={openTable} onOpenQuery={openSavedQuery} views={viewsEnabled ? views : undefined} onOpenPivot={openPivot} onOpenSegment={openSegment} onNewPivot={table => openPivotDraft(table)}
    onNewQuery={() => {if (writable) setAdvanced(true); showDatabase({id: queryTabId(), kind: 'query'});}} onOpenView={view => {setSelection(previous => ({...previous, view})); onNavigate?.();}}/>;
  const navigation = navigationHost === undefined ? <nav className="data-navigation-inline" aria-label="Data navigation">{nav}</nav> : navigationHost ? createPortal(nav, navigationHost) : null;

  const actions = <>
    {workspaceId && writable && <DropdownMenu>
      <DropdownMenuTrigger asChild><Button variant="ghost" id={ADVANCED_TRIGGER_ID} aria-label="Advanced"><Wrench size={20} strokeWidth={1.5}/><span className="data-action-label">Advanced</span></Button></DropdownMenuTrigger>
      <DropdownMenuContent aria-label="Advanced" onCloseAutoFocus={event => {if (openedFromMenu.current) {event.preventDefault(); openedFromMenu.current = false;}}}>
        <DropdownMenuCheckboxItem checked={advancedShown} onCheckedChange={checked => {if (checked) {openedFromMenu.current = true; openAdvanced();} else setAdvanced(false);}}>SQL editor</DropdownMenuCheckboxItem>
      </DropdownMenuContent>
    </DropdownMenu>}
    {workspaceId && role === 'owner' && <Button variant="ghost" size="icon" aria-label="Workspace settings" title="Workspace settings" onClick={() => setManageDialog(true)}><Settings2 size={20}/></Button>}
    <Button variant="ghost" aria-pressed={copilot} onClick={() => setCopilot(value => !value)} disabled={!workspaceId}><MessageSquare size={20}/><span className="data-action-label">Copilot</span></Button>
  </>;
  const banner = <>
    {error && <div className="data-inline-error"><p role="alert">{error}</p><Button variant="secondary" size="sm" onClick={() => {setError(''); setRefresh(value => value + 1);}}>Try again</Button></div>}
    {notice && <p className="data-notice" role="status">{notice}<Button variant="ghost" size="sm" aria-label="Dismiss notice" onClick={() => setNotice('')}>Dismiss</Button></p>}
  </>;
  const plainHeader = (title: string) => <header className="data-header"><h1 className="data-view-title">{title}</h1><div className="data-toolbar">{actions}</div></header>;

  function panel(tab: DataTab, active: boolean) {
    if (!snapshot) return null;
    if (tab.kind === 'overview') return <TablesOverview workspaceId={workspaceId} ownerId={ownerId} role={role} hasConnections={snapshot.connections.length > 0} source={source} engine={engine} controller={controller}
      canEditSource={writable && (!source?.created_by || source.created_by === ownerId)} onConnect={() => setSourceDialog({open: true})} onEditSource={() => setSourceDialog({open: true, record: source})} onArchiveSource={() => setArchiveSource(source)} onOpenTable={openTable} onCombineTable={writable ? combineTable : undefined} onPivotTable={writable && viewsEnabled ? table => openPivotDraft(table) : undefined}/>;
    const engineCanLoad = !!engine?.ready && !!engine.capabilities.schema, closeThis = () => updateTabs(state => closeTab(state, tab.id));
    if (tab.kind === 'table') return <TableTab key={tab.id} workspaceId={workspaceId} connectionId={tab.connectionId || connectionId} name={(tab.schema ? tab.schema + '.' : '') + tab.table} label={tabTitle(tab)} controller={controller} engineCanLoad={engineCanLoad} onClose={closeThis} onOpenAdvanced={writable ? openAdvanced : undefined} combineRequest={combineRequest.tabId === tab.id ? combineRequest.n : 0}
      views={viewsEnabled ? views : undefined} canSave={writable && viewsEnabled}/>;
    if (tab.kind === 'drill') return <TableTab key={tab.id} workspaceId={workspaceId} connectionId={tab.connectionId || connectionId} name={(tab.schema ? tab.schema + '.' : '') + tab.table} label={tabTitle(tab)} controller={controller} engineCanLoad={engineCanLoad} onClose={closeThis}
      views={viewsEnabled ? views : undefined} seed={tab.spec} subtitle={tab.note || 'The rows behind this number. Changes here do not change the original.'}/>;
    if (tab.kind === 'pivot') return <PivotTab key={tab.id} workspaceId={workspaceId} connectionId={tab.connectionId || connectionId} tab={tab} label={tabTitle(tab)} controller={controller} engineCanLoad={engineCanLoad} views={views} canEdit={writable}
      onClose={closeThis} onOpenAdvanced={writable ? openAdvanced : undefined} onSaved={pivotSaved} onArchived={id => viewArchived(id, 'pivot')} onDrill={openDrill}/>;
    if (tab.kind === 'segment') return <SegmentTab key={tab.id} workspaceId={workspaceId} connectionId={connectionId} tab={tab} label={tabTitle(tab)} controller={controller} views={views} canEdit={writable}
      onClose={closeThis} onArchived={id => viewArchived(id, 'segment')} onDrill={openDrill} onOpenView={openUse} onUseForPivot={useForPivot}/>;
    const retainedKey = workspaceId + ':' + tab.id;
    return <DatabaseStudio key={tab.id} ownerId={ownerId} workspaceId={workspaceId} connections={snapshot.connections} queries={snapshot.queries} engines={engines} role={role} connectionId={connectionId}
      onConnectionChange={id => setSelection(previous => ({...previous, connectionId: id}))} onRefresh={requestRefresh} draft={queryDraft?.tabId === tab.id ? queryDraft.draft : null} onDraftConsumed={() => setQueryDraft(null)}
      initialQueryId={tab.queryId} initialDraft={retainedDrafts[retainedKey] || null} onDraftSnapshot={draft => onRetain(retainedKey, draft)}
      active={active && selection.view === 'database'} panelOpen={writable && advancedOn} onPanelOpen={writable ? openAdvanced : undefined} onPanelClose={() => setAdvanced(false)} panelFocus={focusRequest.tabId === tab.id ? focusRequest.n : 0} onQueryChange={query => setActiveQueryId(query.id || '')} onOpenSavedQuery={id => {const row = snapshot.queries.find(item => item.id === id); if (row) openSavedQuery(row);}} onQueryArchived={id => updateTabs(state => closeTab(state, queryTabId(id)))}/>;
  }

  let content;
  if (loading && !bootstrap) content = <>{plainHeader('Data')}<p className="data-loading" role="status">Loading Data Studio…</p></>;
  else if (!workspaceId) content = <>{plainHeader('Data')}{banner}<div className="data-empty"><h2>Your data, together</h2><p>Create a workspace to connect a database and build reports with your team.</p><Button onClick={() => openDialog('create')}>Create workspace</Button></div></>;
  else if (!snapshot) content = <>{plainHeader('Data')}{banner}{error ? null : <p className="data-loading" role="status">Opening workspace…</p>}</>;
  else if (selection.view === 'database') content = <DataTabs state={shownTabs} title={tabTitle} actions={actions} banner={banner}
    onSelect={id => {setNotice(''); updateTabs(state => selectTab(state, id));}} onClose={id => {setNotice(''); updateTabs(state => closeTab(state, id));}} panel={panel}/>;
  else content = <>{plainHeader(viewTitle[selection.view as 'bi' | 'team'])}{banner}<div className="data-content" key={workspaceId}>
    {selection.view === 'bi' && <BIStudio workspaceId={workspaceId} connections={snapshot.connections} queries={snapshot.queries} role={role} active onOpenQuery={openQuery} onRefresh={requestRefresh}/>}
    {selection.view === 'team' && <TeamSettings workspaceId={workspaceId} members={snapshot.members} role={role} ownerId={ownerId} onRefresh={requestRefresh}/>}
  </div></>;

  return <section className="data-studio" aria-label="Data Studio">
    {navigation}
    <div className="data-main">{content}</div>
    {copilot && snapshot && <CopilotPanel><DataCopilot role={role} workspaceId={workspaceId} connectionId={connectionId || undefined} queryId={activeQueryId || undefined} visible onClose={() => setCopilot(false)} onOpenQuery={openQuery} onSaveReport={() => {setSelection(value => ({...value, view: 'bi'})); requestRefresh();}}
      onApplyTable={applyProposalTable} onOpenPivot={openProposalPivot} onSaveSegment={saveProposalSegment}/></CopilotPanel>}
    <SourceDialog open={sourceDialog.open} record={sourceDialog.record} engines={engines} workspaceId={workspaceId} ownerId={ownerId} onClose={() => setSourceDialog({open: false})}
      onSaved={result => {setSourceDialog({open: false}); setSelection(value => ({...value, connectionId: result.id})); setNotice('Connection saved. Test it when you are ready.'); requestRefresh();}}/>
    <ArchiveSourceDialog record={archiveSource} workspaceId={workspaceId} onClose={() => setArchiveSource(undefined)}
      onArchived={record => {setArchiveSource(undefined); if (record.id === connectionId) setSelection(value => ({...value, connectionId: ''})); setNotice('Connection archived.'); requestRefresh();}}/>
    <Dialog open={manageDialog} onOpenChange={setManageDialog}><DialogContent className="data-dialog"><DialogTitle>Workspace settings</DialogTitle><DialogDescription>{currentWorkspace?.name}</DialogDescription><div className="data-workspace-menu"><Button variant="ghost" onClick={() => {setManageDialog(false); openDialog('rename');}}>Rename workspace</Button><Button variant="ghost" onClick={() => {setManageDialog(false); openDialog('archive');}}>Archive workspace</Button></div></DialogContent></Dialog>
    <Dialog open={dialog !== null} onOpenChange={open => {if (!open && !busy) setDialog(null);}}><DialogContent className="data-dialog" closeDisabled={busy}><DialogTitle>{dialog === 'create' ? 'Create workspace' : dialog === 'rename' ? 'Rename workspace' : 'Archive workspace'}</DialogTitle><DialogDescription>{dialog === 'archive' ? 'The workspace and its saved objects will be archived for everyone.' : 'A shared place for database connections, saved queries and reports.'}</DialogDescription>
      <form onSubmit={event => {event.preventDefault(); void saveWorkspace();}}>{dialog !== 'archive' && <label>Workspace name<Input autoFocus value={name} onChange={event => setName(event.target.value)} maxLength={120} disabled={busy} required/></label>}{dialogError && <p role="alert">{dialogError}</p>}
        <div className="data-dialog-actions"><Button variant="ghost" disabled={busy} onClick={() => setDialog(null)}>Cancel</Button><Button variant={dialog === 'archive' ? 'destructive' : 'default'} type="submit" disabled={busy || dialog !== 'archive' && !name.trim()}>{busy ? 'Saving…' : dialog === 'archive' ? 'Archive workspace' : dialog === 'rename' ? 'Save' : 'Create workspace'}</Button></div></form></DialogContent></Dialog>
  </section>;
}
