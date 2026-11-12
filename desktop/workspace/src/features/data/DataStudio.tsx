import {useCallback, useEffect, useMemo, useRef, useState} from 'react';
import {createPortal} from 'react-dom';
import {MessageSquare, Settings2} from 'lucide-react';
import {Button} from '../../components/ui/button';
import {Dialog, DialogContent, DialogDescription, DialogTitle} from '../../components/ui/dialog';
import {Input} from '../../components/ui/input';
import {dataBody, dataError, dataPath, dataRequest, readDataSelection, rememberDataSelection, useDataScope, type DataBootstrap, type DataConnection, type DataQuery, type DataQueryDraft, type DataSnapshot, type DataWorkspace} from '../../lib/data-studio';
import {closeTab, initialTabs, openTab, pruneTabs, queryTabId, readTabs, rememberTabs, selectTab, tableTabId, type DataTab, type DataTabsState} from '../../lib/data-tabs';
import type {SchemaTable} from '../../lib/query-spec';
import DatabaseStudio from './DatabaseStudio';
import TeamSettings from './TeamSettings';
import BIStudio from './BIStudio';
import DataCopilot from './DataCopilot';
import DataNavigation, {tableLabel, type NamedRecord} from './DataNavigation';
import DataTabs from './DataTabs';
import TablesOverview from './TablesOverview';
import TableTab from './TableTab';
import SourceDialog from './SourceDialog';
import ArchiveSourceDialog from './ArchiveSourceDialog';
import {useDataSource} from './use-source';
import './data-studio.css';

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

const viewTitle: Record<'bi' | 'team', string> = {bi: 'Reports', team: 'Team'};
const named = (rows: unknown[]): NamedRecord[] => rows.filter((row): row is NamedRecord => !!row && typeof row === 'object' && typeof (row as NamedRecord).id === 'string' && typeof (row as NamedRecord).name === 'string');

function DataSession({ownerId, navigationHost, onNavigate, retainedDrafts, onRetain}: {ownerId?: string; navigationHost?: HTMLElement | null; onNavigate?: () => void; retainedDrafts: Record<string, DataQueryDraft>; onRetain: (key: string, draft: DataQueryDraft) => void}) {
  const [selection, setSelection] = useState(() => readDataSelection(ownerId)), [bootstrap, setBootstrap] = useState<DataBootstrap | null>(null), [snapshot, setSnapshot] = useState<DataSnapshot | null>(null);
  const [loading, setLoading] = useState(true), [error, setError] = useState(''), [refresh, setRefresh] = useState(0), [dialog, setDialog] = useState<'create' | 'rename' | 'archive' | null>(null), [name, setName] = useState(''), [busy, setBusy] = useState(false), [dialogError, setDialogError] = useState('');
  const [copilot, setCopilot] = useState(false), [queryDraft, setQueryDraft] = useState<{tabId: string; draft: DataQueryDraft} | null>(null), [activeQueryId, setActiveQueryId] = useState(''), [manageDialog, setManageDialog] = useState(false);
  const [sourceDialog, setSourceDialog] = useState<{open: boolean; record?: DataConnection}>({open: false}), [archiveSource, setArchiveSource] = useState<DataConnection | undefined>(), [notice, setNotice] = useState('');
  const [tabsByWorkspace, setTabsByWorkspace] = useState<Record<string, DataTabsState>>({});
  const scope = useDataScope(), snapshotTicket = useRef(0), flight = useRef(false), workspaceId = selection.workspaceId, connectionId = selection.connectionId;
  const currentWorkspace = bootstrap?.workspaces.find(row => row.id === workspaceId), role = snapshot?.workspace.role || currentWorkspace?.role || 'viewer', writable = role !== 'viewer';
  const source = snapshot?.connections.find(row => row.id === connectionId), engines = bootstrap?.engines || [], engine = engines.find(row => row.id === source?.engine);
  const requestRefresh = useCallback(() => setRefresh(value => value + 1), []);
  const controller = useDataSource(workspaceId, source, requestRefresh);

  useEffect(() => {rememberDataSelection(ownerId, selection);}, [ownerId, selection]);
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
    updateTabs(state => pruneTabs(state, tab => tab.kind === 'table' ? snapshot.connections.some(row => row.id === tab.connectionId) : !tab.queryId || snapshot.queries.some(row => row.id === tab.queryId)));
  }, [snapshot, workspaceId, updateTabs]);
  const queries = snapshot?.queries || [];
  const shownTabs = useMemo<DataTabsState>(() => {
    const tabs = stored.tabs.filter(tab => tab.kind === 'overview' || tab.kind === 'table' && tab.connectionId === connectionId || tab.kind === 'query' && (!tab.queryId || queries.find(row => row.id === tab.queryId)?.connection_id === connectionId || !snapshot));
    return {tabs, activeId: tabs.some(tab => tab.id === stored.activeId) ? stored.activeId : 'overview'};
  }, [stored, connectionId, queries, snapshot]);
  const tabTitle = (tab: DataTab) => tab.kind === 'overview' ? 'Overview' : tab.kind === 'table' ? tableLabel({name: tab.table!, schema: tab.schema}) : tab.queryId ? queries.find(row => row.id === tab.queryId)?.name || 'Saved query' : 'New query';
  const showDatabase = (tab?: DataTab) => {
    if (tab) updateTabs(state => openTab(state, tab));
    setSelection(previous => ({...previous, view: 'database'}));
    onNavigate?.();
  };

  function selectWorkspace(id: string) {setError(''); setSnapshot(null); setSelection(value => ({...value, workspaceId: id, connectionId: ''})); setQueryDraft(null); setActiveQueryId(''); setCopilot(false);}
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
  }
  function openSavedQuery(row: DataQuery) {
    if (row.connection_id !== connectionId) setSelection(value => ({...value, connectionId: row.connection_id}));
    showDatabase({id: queryTabId(row.id), kind: 'query', queryId: row.id});
  }
  function openTable(table: SchemaTable) {showDatabase({id: tableTabId(connectionId, table.name, table.schema), kind: 'table', connectionId, table: table.name, ...(table.schema ? {schema: table.schema} : {})});}

  const reports = useMemo(() => named(snapshot?.reports || []), [snapshot]), dashboards = useMemo(() => named(snapshot?.dashboards || []), [snapshot]);
  const nav = <DataNavigation loading={loading} workspaces={bootstrap?.workspaces || []} workspaceId={workspaceId} workspaceBusy={busy} onWorkspace={selectWorkspace} onCreateWorkspace={() => openDialog('create')}
    role={role} connections={snapshot?.connections || []} connectionId={connectionId} onConnection={id => {setSelection(previous => ({...previous, connectionId: id})); onNavigate?.();}} onConnect={() => setSourceDialog({open: true})}
    source={controller} queries={queries} reports={reports} dashboards={dashboards} view={selection.view} activeTabId={shownTabs.activeId}
    onOpenOverview={() => {updateTabs(state => selectTab(state, 'overview')); showDatabase();}} onOpenTable={openTable} onOpenQuery={openSavedQuery}
    onNewQuery={() => showDatabase({id: queryTabId(), kind: 'query'})} onOpenView={view => {setSelection(previous => ({...previous, view})); onNavigate?.();}}/>;
  const navigation = navigationHost === undefined ? <nav className="data-navigation-inline" aria-label="Data navigation">{nav}</nav> : navigationHost ? createPortal(nav, navigationHost) : null;

  const actions = <>
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
    if (tab.kind === 'overview') return <TablesOverview ownerId={ownerId} role={role} hasConnections={snapshot.connections.length > 0} source={source} engine={engine} controller={controller}
      canEditSource={writable && (!source?.created_by || source.created_by === ownerId)} onConnect={() => setSourceDialog({open: true})} onEditSource={() => setSourceDialog({open: true, record: source})} onArchiveSource={() => setArchiveSource(source)} onOpenTable={openTable}/>;
    if (tab.kind === 'table') return <TableTab name={(tab.schema ? tab.schema + '.' : '') + tab.table} label={tabTitle(tab)} controller={controller} engineCanLoad={!!engine?.ready && !!engine.capabilities.schema} onClose={() => updateTabs(state => closeTab(state, tab.id))}/>;
    const retainedKey = workspaceId + ':' + tab.id;
    return <DatabaseStudio key={tab.id} ownerId={ownerId} workspaceId={workspaceId} connections={snapshot.connections} queries={snapshot.queries} engines={engines} role={role} connectionId={connectionId}
      onConnectionChange={id => setSelection(previous => ({...previous, connectionId: id}))} onRefresh={requestRefresh} draft={queryDraft?.tabId === tab.id ? queryDraft.draft : null} onDraftConsumed={() => setQueryDraft(null)}
      initialQueryId={tab.queryId} initialDraft={retainedDrafts[retainedKey] || null} onDraftSnapshot={draft => onRetain(retainedKey, draft)}
      active={active && selection.view === 'database'} onQueryChange={query => setActiveQueryId(query.id || '')} onQueryArchived={id => updateTabs(state => closeTab(state, queryTabId(id)))}/>;
  }

  let content;
  if (loading && !bootstrap) content = <>{plainHeader('Data')}<p className="data-loading" role="status">Loading Data Studio…</p></>;
  else if (!workspaceId) content = <>{plainHeader('Data')}{banner}<div className="data-empty"><h2>Your data, together</h2><p>Create a workspace to connect a database and build reports with your team.</p><Button onClick={() => openDialog('create')}>Create workspace</Button></div></>;
  else if (!snapshot) content = <>{plainHeader('Data')}{banner}{error ? null : <p className="data-loading" role="status">Opening workspace…</p>}</>;
  else if (selection.view === 'database') content = <DataTabs state={shownTabs} title={tabTitle} actions={actions} banner={banner}
    onSelect={id => updateTabs(state => selectTab(state, id))} onClose={id => updateTabs(state => closeTab(state, id))} panel={panel}/>;
  else content = <>{plainHeader(viewTitle[selection.view as 'bi' | 'team'])}{banner}<div className="data-content" key={workspaceId}>
    {selection.view === 'bi' && <BIStudio workspaceId={workspaceId} connections={snapshot.connections} queries={snapshot.queries} role={role} active onOpenQuery={openQuery} onRefresh={requestRefresh}/>}
    {selection.view === 'team' && <TeamSettings workspaceId={workspaceId} members={snapshot.members} role={role} ownerId={ownerId} onRefresh={requestRefresh}/>}
  </div></>;

  return <section className="data-studio" aria-label="Data Studio">
    {navigation}
    <div className="data-main">{content}</div>
    {copilot && snapshot && <aside className="data-copilot-panel" aria-label="Data Copilot"><DataCopilot role={role} workspaceId={workspaceId} connectionId={connectionId || undefined} queryId={activeQueryId || undefined} visible onClose={() => setCopilot(false)} onOpenQuery={openQuery} onSaveReport={() => {setSelection(value => ({...value, view: 'bi'})); requestRefresh();}}/></aside>}
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
