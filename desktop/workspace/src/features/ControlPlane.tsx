import {useEffect, useMemo, useRef, useState, type MouseEvent} from 'react';
import {ArrowUpRight, ChevronRight, Folder, MessageSquare, Plus, RefreshCw, Search} from 'lucide-react';
import {Button} from '../components/ui/button';
import {Input} from '../components/ui/input';
import {Select} from '../components/ui/select';
import {Collapsible, CollapsibleContent, CollapsibleTrigger} from '../components/ui/collapsible';
import {Dialog, DialogContent, DialogDescription, DialogTitle} from '../components/ui/dialog';
import {ApiError, type Engine, type Project, type Provider, type Session} from '../lib/api';
import {listSessions, SessionCache} from '../lib/workspace-data';
import {taskState} from '../lib/task-presentation';
import {latestExecutionTask} from '../lib/conversation-messages';
import {isManagedFolder, scopeProjects} from '../lib/project-labels';
import './control-plane.css';
import WorkspaceViews,{type WorkspaceView} from '../components/WorkspaceViews';

export type ControlPage = 'overview' | 'projects' | 'tasks';
export type ExecutionView = 'chat' | 'workbench' | 'review' | 'browser' | 'computer';
type Props = {
  page: ControlPage;
  ownerId: string;
  enabled: boolean;
  projects: Project[];
  engines: Engine[];
  providers: Provider[];
  catalogState: 'loading' | 'ready' | 'failed';
  configurationState?: 'loading' | 'ready' | 'failed';
  currentProjectId?: string;
  onPage: (page: WorkspaceView) => void;
  onManage: (section: string) => void;
  onNew: (projectId?: string) => void;
  onAddProject: () => void;
  onOpen: (sessionId: string, view: ExecutionView) => void;
  onProject: (project: Project) => void;
  onRefresh: () => void;
};
type Filter = 'all' | 'running' | 'attention' | 'completed';
type Catalog = {owner: string; sessions: Session[]; updated: number; loading: boolean; error: string};
type Inspection = {owner: string; id: string; page: ControlPage; trigger: HTMLButtonElement};
const emptyCatalog = (owner: string): Catalog => ({owner, sessions: [], updated: 0, loading: true, error: ''});
const losesAccess = (reason: unknown) => reason instanceof ApiError && [401, 403, 404, 423].includes(reason.status) || reason instanceof Error && reason.message === 'Conversation is unavailable';

export function controlRows(rows: Session[], query: string, project: string, filter: Filter) {
  const terms = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
  return rows.filter(row => {
    const state = taskState(row.latest_status || '');
    const text = [row.title, row.cwd, row.engine, row.model, row.worktree_branch, row.runner_id].join(' ').toLowerCase();
    return !row.archived && (!project || row.project_id === project) && terms.every(term => text.includes(term)) &&
      (filter === 'all' || filter === 'running' && state.active || filter === 'attention' && state.attention || filter === 'completed' && row.latest_status === 'completed');
  }).sort((a, b) => Number(taskState(b.latest_status || '').attention) - Number(taskState(a.latest_status || '').attention) || b.updated_at - a.updated_at);
}

export default function ControlPlane(props: Props) {
  const {page, ownerId, enabled, projects, engines, providers, onPage, onManage, onNew, onAddProject, onOpen, onProject} = props;
  const [catalog, setCatalog] = useState<Catalog>(() => emptyCatalog(ownerId));
  const [version, setVersion] = useState(0);
  const [projectQuery, setProjectQuery] = useState(''), [showWorkspaces, setShowWorkspaces] = useState(false);
  const [query, setQuery] = useState(''), [project, setProject] = useState(''), [filter, setFilter] = useState<Filter>('all'), [limit, setLimit] = useState(40);
  const [inspection, setInspection] = useState<Inspection | null>(null);
  // Tag cached data with its authenticated owner. A prop change must not expose
  // the previous owner's rows while effect cleanup is still pending.
  const current = enabled && catalog.owner === ownerId ? catalog : emptyCatalog(ownerId);
  const {sessions, updated, loading, error} = current;
  useEffect(() => {
    if (!enabled) { setCatalog(emptyCatalog(ownerId)); setInspection(null); return; }
    let stopped = false, busy = false;
    const refresh = async () => {
      if (stopped || busy || document.hidden) return;
      busy = true;
      setCatalog(previous => ({...(previous.owner === ownerId ? previous : emptyCatalog(ownerId)), loading: true}));
      try {
        const rows = await listSessions(false);
        if (!stopped) setCatalog({owner: ownerId, sessions: rows, updated: Date.now(), loading: false, error: ''});
      } catch (reason) {
        if (!stopped) setCatalog(previous => ({...(previous.owner === ownerId && !losesAccess(reason) ? previous : emptyCatalog(ownerId)), loading: false, error: reason instanceof Error ? reason.message : String(reason)}));
      } finally { busy = false; }
    };
    void refresh();
    const timer = setInterval(() => void refresh(), 10000);
    const visible = () => { if (!document.hidden) void refresh(); };
    document.addEventListener('visibilitychange', visible);
    return () => { stopped = true; clearInterval(timer); document.removeEventListener('visibilitychange', visible); };
  }, [ownerId, enabled, version]);
  useEffect(() => { setInspection(null); }, [page, ownerId, enabled]);
  const rows = useMemo(() => controlRows(sessions, query, project, filter), [sessions, query, project, filter]);
  const accessible = useMemo(() => sessions.filter(row => !row.archived), [sessions]);
  const counts = {running: accessible.filter(row => taskState(row.latest_status || '').active).length, attention: accessible.filter(row => taskState(row.latest_status || '').attention).length, completed: accessible.filter(row => row.latest_status === 'completed').length};
  const inspectedRow = inspection?.owner === ownerId && inspection.page === page && enabled ? (page === 'tasks' ? rows : accessible).find(row => row.id === inspection.id) : undefined;
  useEffect(() => { if (inspection && !inspectedRow) setInspection(null); }, [inspection, inspectedRow]);
  const inspect = (row: Session, event: MouseEvent<HTMLButtonElement>) => {
    event.currentTarget.focus({preventScroll: true});
    setInspection({owner: ownerId, id: row.id, page, trigger: event.currentTarget});
  };
  const openFilter = (value: Filter, projectId = '') => { setFilter(value); setProject(projectId); setQuery(''); setLimit(40); setInspection(null); onPage('tasks'); };
  const navigate = (value: WorkspaceView) => { setInspection(null); onPage(value); };
  const refresh = () => { setVersion(value => value + 1); props.onRefresh(); };
  const configurationState = props.configurationState || props.catalogState;
  const currentProjectId = props.currentProjectId || project;
  const heading = page === 'overview' ? 'Overview' : page === 'projects' ? 'Projects' : 'Activity';
  return <section className={'control-plane control-page-' + page} aria-label="Projects workspace">
    <WorkspaceViews current={page} onNavigate={navigate}/>
    <header className="control-heading"><h1>{heading}</h1><div className="control-actions">
      <Button variant="ghost" size="icon" aria-label="Refresh" title="Refresh" disabled={!enabled || loading || props.catalogState === 'loading'} onClick={refresh}><RefreshCw size={20} aria-hidden="true"/></Button>
      {page === 'projects' ? <Button disabled={!enabled} onClick={onAddProject}><Plus size={16} aria-hidden="true"/>Add project</Button> : <Button disabled={!enabled} onClick={() => onNew(project || undefined)}><Plus size={16} aria-hidden="true"/>New chat</Button>}
    </div></header>
    {error && <div className="control-error" role="alert"><span>{error}. {updated ? 'Showing the last received activity.' : 'Activity is unavailable.'}</span><Button variant="ghost" disabled={!enabled || loading} onClick={() => setVersion(value => value + 1)}>Retry</Button></div>}
    {props.catalogState === 'failed' && <div className="control-error" role="alert"><span>{projects.length ? 'Projects could not refresh. Showing the last received folders.' : 'Projects are unavailable.'}</span><Button variant="ghost" disabled={!enabled || loading} onClick={refresh}>Refresh projects</Button></div>}
    {page === 'projects' && <>
      <div className="control-project-tools-row"><label className="control-search"><Search size={16} aria-hidden="true"/><span className="visually-hidden">Search projects</span><Input type="search" placeholder="Search projects" value={projectQuery} onChange={event => setProjectQuery(event.target.value)}/></label>{projects.some(isManagedFolder) && <Button size="sm" variant="ghost" aria-pressed={showWorkspaces} onClick={() => setShowWorkspaces(value => !value)}>{showWorkspaces ? 'Hide workspaces' : 'Show workspaces'}</Button>}</div>
      <ProjectCatalog projects={projects} sessions={accessible} query={projectQuery} showWorkspaces={showWorkspaces} currentProject={currentProjectId} catalogState={props.catalogState} received={!!updated} loading={loading} enabled={enabled} onClearSearch={() => setProjectQuery('')} onAddProject={onAddProject} onNew={onNew} onOpen={onOpen} onProject={onProject} onActivity={id => openFilter('all', id)}/>
    </>}
    {page === 'overview' && <>
      <div className="control-metrics" aria-label="Activity summary">{([{id: 'attention', label: 'Needs attention'}, {id: 'running', label: 'In progress'}, {id: 'completed', label: 'Completed'}] as const).map(item => <button key={item.id} onClick={() => openFilter(item.id)}><strong>{updated ? counts[item.id] : '—'}</strong><span>{item.label}</span></button>)}</div>
      <div className="control-overview-grid"><section className="control-section"><div className="control-section-heading"><h2>Needs attention</h2><Button variant="ghost" size="sm" onClick={() => openFilter('attention')}>View activity</Button></div>
        {!updated ? <div className="control-empty">{loading ? 'Loading activity…' : 'Activity is unavailable.'}</div> : !counts.attention ? <div className="control-empty">No pending decisions.</div> : <ul className="control-attention-list">{accessible.filter(row => taskState(row.latest_status || '').attention).slice(0, 5).map(row => <li key={row.id}><button className="control-queue-row" aria-label={`Inspect ${row.title || 'Untitled conversation'}`} onClick={event => inspect(row, event)}><span><strong>{row.title || 'Untitled conversation'}</strong><small>{projects.find(item => item.id === row.project_id)?.name || 'Unassigned'}</small></span><Status value={row.latest_status}/><ChevronRight size={16} aria-hidden="true"/></button></li>)}</ul>}
      </section><section className="control-section"><div className="control-section-heading"><h2>Manage</h2></div>
        <p className="control-catalog-summary">{configurationState === 'ready' ? `${engines.length} installed engines · ${providers.filter(row => row.secret_configured).length} configured accounts` : configurationState === 'failed' ? 'Configuration counts unavailable.' : 'Loading configuration…'}</p>
        <div className="control-destinations">{[{id: 'agents', title: 'Agents'}, {id: 'runners', title: 'Runners'}, {id: 'automations', title: 'Automations'}, {id: 'models', title: 'Models and engines'}, {id: 'access', title: 'Access and sessions'}].map(item => <button key={item.id} onClick={() => onManage(item.id)}><span>{item.title}</span><ArrowUpRight size={16} aria-hidden="true"/></button>)}</div>
      </section></div>
      <section className="control-section"><div className="control-section-heading"><h2>Projects</h2><Button variant="ghost" size="sm" onClick={() => navigate('projects')}>View projects</Button></div>
        {!projects.length ? <div className="control-empty"><p>{props.catalogState === 'loading' ? 'Loading projects…' : props.catalogState === 'failed' ? 'Projects are unavailable.' : 'Add a folder to start a project.'}</p>{props.catalogState === 'ready' && <Button variant="ghost" disabled={!enabled} onClick={onAddProject}>Add project</Button>}</div> : <ul className="control-overview-projects">{scopeProjects(projects, currentProjectId).slice(0, 6).map(item => <li key={item.id}><button className="control-project-open" disabled={!enabled} onClick={() => onProject(item)}><Folder size={20} aria-hidden="true"/><span><strong>{item.name}</strong><small title={item.path}>{item.path}</small></span><ChevronRight size={16} aria-hidden="true"/></button><Button variant="ghost" size="sm" disabled={!enabled} onClick={() => onNew(item.id)}>New chat</Button></li>)}</ul>}
      </section>
    </>}
    {page === 'tasks' && <>
      <div className="control-run-tools"><label className="control-search"><Search size={16} aria-hidden="true"/><span className="visually-hidden">Search activity</span><Input type="search" placeholder="Search activity" value={query} onChange={event => { setQuery(event.target.value); setLimit(40); }}/></label><Select aria-label="Filter activity by project" value={project} onChange={event => { setProject(event.target.value); setLimit(40); }}><option value="">All projects</option>{projects.map(item => <option value={item.id} key={item.id}>{item.name}</option>)}</Select><Select aria-label="Filter activity by status" value={filter} onChange={event => { setFilter(event.target.value as Filter); setLimit(40); }}><option value="all">All states</option><option value="attention">Needs attention</option><option value="running">In progress</option><option value="completed">Completed</option></Select></div>
      <p className="control-description">Latest activity in each conversation.</p>
      <div className="control-activity-columns" aria-hidden="true"><span>Conversation</span><span>State</span><span>Updated</span></div>
      <ul className="control-activity-list" aria-label="Activity">{rows.slice(0, limit).map(row => <li key={row.id}><button className="control-activity-row" aria-label={`Inspect ${row.title || 'Untitled conversation'}`} onClick={event => inspect(row, event)}><span className="control-activity-identity"><strong>{row.title || 'Untitled conversation'}</strong><small>{projects.find(item => item.id === row.project_id)?.name || 'Unassigned'}</small></span><Status value={row.latest_status}/><ProjectUpdated value={row.updated_at}/><ChevronRight size={16} aria-hidden="true"/></button></li>)}</ul>
      {!rows.length && <div className="control-empty">{!updated ? loading ? 'Loading activity…' : 'Activity is unavailable.' : query || project || filter !== 'all' ? <><p>No activity matches these filters.</p><Button variant="ghost" onClick={() => { setQuery(''); setProject(''); setFilter('all'); }}>Clear filters</Button></> : 'No conversations yet.'}</div>}
      {rows.length > limit && <Button variant="ghost" onClick={() => setLimit(value => value + 40)}>Show more</Button>}
    </>}
    <Dialog open={!!inspectedRow} onOpenChange={open => { if (!open) setInspection(null); }}>{inspectedRow && <RunInspector key={ownerId + ':' + inspectedRow.id} row={inspectedRow} trigger={inspection!.trigger} enabled={enabled} onOpen={view => { setInspection(null); onOpen(inspectedRow.id, view); }}/>}</Dialog>
  </section>;
}

function ProjectCatalog({projects, sessions, query, showWorkspaces, currentProject, catalogState, received, loading, enabled, onClearSearch, onAddProject, onNew, onOpen, onProject, onActivity}: Pick<Props, 'projects' | 'catalogState' | 'enabled' | 'onAddProject' | 'onNew' | 'onOpen' | 'onProject'> & {sessions: Session[]; query: string; showWorkspaces: boolean; currentProject: string; received: boolean; loading: boolean; onClearSearch: () => void; onActivity: (id: string) => void}) {
  const visible = useMemo(() => {
    const terms = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
    const scoped = new Set(scopeProjects(projects, currentProject).map(item => item.id));
    return projects.filter(item => (showWorkspaces || terms.length > 0 || scoped.has(item.id)) && terms.every(term => `${item.name} ${item.path}`.toLowerCase().includes(term)));
  }, [projects, query, showWorkspaces, currentProject]);
  const related = useMemo(() => {
    const byProject = new Map<string, Session[]>();
    sessions.filter(row => !row.archived && row.project_id).forEach(row => {
      const rows = byProject.get(row.project_id!) || [];
      rows.push(row); byProject.set(row.project_id!, rows);
    });
    byProject.forEach(rows => rows.sort((a, b) => b.updated_at - a.updated_at));
    return byProject;
  }, [sessions]);
  if (!projects.length) return <div className="control-empty control-project-empty"><p>{catalogState === 'loading' ? 'Loading projects…' : catalogState === 'failed' ? 'Projects are unavailable.' : 'Add a folder to start a project.'}</p>{catalogState === 'ready' && <Button variant="ghost" disabled={!enabled} onClick={onAddProject}>Add project</Button>}</div>;
  return <div className="control-project-catalog">
    <div className="control-project-columns" aria-hidden="true"><span>Project</span><span>Updated</span></div>
    {!visible.length ? <div className="control-empty control-project-empty"><p>{query.trim() ? `No projects match “${query.trim()}”.` : 'No project folders to show.'}</p>{query.trim() && <Button variant="ghost" onClick={onClearSearch}>Clear search</Button>}</div> : <ul className="control-project-list" aria-label="Projects">{visible.map(item => {
      const conversations = related.get(item.id) || [];
      const latest = conversations.reduce((value, row) => Number.isFinite(row.updated_at) && row.updated_at > value ? row.updated_at : value, 0);
      return <Collapsible key={item.id} asChild><li className="control-project-group">
        <div className="control-project-row"><CollapsibleTrigger className="control-project-trigger" aria-label={item.name} title={item.path}><Folder size={20} strokeWidth={1.5} aria-hidden="true"/><span className="control-project-identity"><strong>{item.name}</strong><small title={item.path}>{item.path}</small></span><ChevronRight className="control-project-chevron" size={16} aria-hidden="true"/></CollapsibleTrigger><ProjectUpdated value={received ? latest : undefined} loading={!received && loading} description="Latest conversation update"/></div>
        <CollapsibleContent className="control-project-content">
          {conversations.length ? <ul className="control-project-conversations" aria-label={`${item.name} conversations`}>{conversations.map(row => <li key={row.id}><button className="control-project-conversation" aria-label={`Open ${row.title || 'Untitled conversation'}`} disabled={!enabled} onClick={() => onOpen(row.id, 'chat')}><MessageSquare size={16} strokeWidth={1.5} aria-hidden="true"/><span>{row.title || 'Untitled conversation'}</span><ProjectUpdated value={row.updated_at}/></button></li>)}</ul> : <p className="control-project-no-chats">{received ? 'No conversations in this project yet.' : loading ? 'Loading conversations…' : 'Conversations are unavailable.'}</p>}
          <div className="control-project-tools"><Button variant="ghost" size="sm" disabled={!enabled} onClick={() => onProject(item)}>Open folder</Button><Button variant="ghost" size="sm" onClick={() => onActivity(item.id)}>Activity</Button><Button variant="ghost" size="sm" disabled={!enabled} onClick={() => onNew(item.id)}>New chat</Button></div>
        </CollapsibleContent>
      </li></Collapsible>;
    })}</ul>}
  </div>;
}

function ProjectUpdated({value, loading = false, description = 'Updated', full = false}: {value?: number; loading?: boolean; description?: string; full?: boolean}) {
  const date = value && Number.isFinite(value) && value > 0 ? new Date(value * 1000) : null;
  if (!date || !Number.isFinite(date.getTime())) return <span className="control-project-updated" title={loading ? 'Loading conversation updates' : 'No conversation update available'}>{loading ? '…' : '—'}</span>;
  return <time className="control-project-updated" dateTime={date.toISOString()} title={`${description}: ${date.toLocaleString()}`}>{full ? date.toLocaleString() : date.toLocaleDateString(undefined, {month: 'short', day: 'numeric', year: date.getFullYear() === new Date().getFullYear() ? undefined : 'numeric'})}</time>;
}

function Status({value}: {value?: string | null}) {
  const state = taskState(value || '');
  return <span className={'control-state ' + state.tone}>{state.label}</span>;
}

function RunInspector({row, enabled, trigger, onOpen}: {row: Session; enabled: boolean; trigger: HTMLButtonElement; onOpen: (view: ExecutionView) => void}) {
  const content = useRef<HTMLDivElement>(null);
  const [session, setSession] = useState<Session | null>(null), [error, setError] = useState(''), [unavailable, setUnavailable] = useState(false), [retry, setRetry] = useState(0);
  useEffect(() => {
    if (!enabled) return;
    let stopped = false, busy = false;
    const cache = new SessionCache();
    const read = async () => {
      if (stopped || busy || document.hidden) return;
      busy = true;
      try {
        const value = await cache.read(row.id);
        if (value.id !== row.id) throw new Error('Conversation is unavailable');
        if (!stopped) { setSession(value); setError(''); setUnavailable(false); }
      } catch (reason) {
        if (!stopped) { setError(reason instanceof Error ? reason.message : String(reason)); if (losesAccess(reason)) { cache.clear(); setSession(null); setUnavailable(true); } }
      } finally { busy = false; }
    };
    void read(); const timer = setInterval(() => void read(), 3000);
    const visible = () => { if (!document.hidden) void read(); };
    document.addEventListener('visibilitychange', visible);
    return () => { stopped = true; cache.clear(); clearInterval(timer); document.removeEventListener('visibilitychange', visible); };
  }, [row.id, enabled, retry]);
  const current = session || row, task = latestExecutionTask(session?.turns || []);
  const pending = task?.approvals?.filter(approval => approval.status === 'pending') || [];
  return <DialogContent ref={content} className="control-inspector" onOpenAutoFocus={event => { const close = content.current?.querySelector<HTMLButtonElement>('.dialog-close'); if (close) { event.preventDefault(); close.focus({preventScroll: true}); } }} onCloseAutoFocus={event => { if (trigger.isConnected) { event.preventDefault(); trigger.focus({preventScroll: true}); } }}>
    <div className="control-inspector-heading"><DialogDescription>Activity details</DialogDescription><DialogTitle>{unavailable ? 'Conversation unavailable' : current.title || 'Untitled conversation'}</DialogTitle>{!unavailable && <Status value={task?.status || current.latest_status}/>}</div>
    <div className="control-inspector-scroll" role="region" aria-label="Activity details content" tabIndex={0}>
      {error && <div className="control-error" role="alert"><span>{error}{session ? '. Showing the last received details.' : '.'}</span><Button variant="ghost" size="sm" onClick={() => setRetry(value => value + 1)}>Retry</Button></div>}
      {!session && !error && <p role="status">Loading details…</p>}
      {!unavailable && <><dl><div><dt>Engine</dt><dd>{current.engine || 'TermX'}</dd></div><div><dt>Model</dt><dd>{current.model || 'Default'}</dd></div><div><dt>Execution</dt><dd>{current.runner_id ? `Runner ${current.runner_id}` : 'Host'}</dd></div><div><dt>Checkout</dt><dd>{current.cwd || 'No checkout'}</dd></div>{current.worktree_branch && <div><dt>Branch</dt><dd>{current.worktree_branch}</dd></div>}<div><dt>Updated</dt><dd><ProjectUpdated value={current.updated_at} full/></dd></div></dl>
      {task && <section className="control-current-task"><h3>Latest task</h3><p>{task.prompt}</p>{pending.length > 0 && <p className="control-attention">{pending.length} pending approval{pending.length > 1 ? 's' : ''}.</p>}{task.error && <p className="control-error">{task.error}</p>}{task.result && <Collapsible><CollapsibleTrigger className="control-result-trigger">Latest result<ChevronRight size={16} aria-hidden="true"/></CollapsibleTrigger><CollapsibleContent><p className="control-result">{task.result}</p></CollapsibleContent></Collapsible>}</section>}</>}
    </div>
    {!unavailable && <div className="control-inspector-actions"><Button disabled={!enabled} onClick={() => onOpen('chat')}>Open chat</Button><Button variant="ghost" disabled={!enabled} onClick={() => onOpen('review')}>Changes</Button><Button variant="ghost" disabled={!enabled} onClick={() => onOpen('workbench')}>Files</Button><Button variant="ghost" disabled={!enabled} onClick={() => onOpen('browser')}>Browser</Button></div>}
  </DialogContent>;
}
