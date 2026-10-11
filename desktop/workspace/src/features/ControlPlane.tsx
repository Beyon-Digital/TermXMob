import {useEffect, useMemo, useState} from 'react';
import {Activity, ArrowUpRight, Box, Folder, GitBranch, Layers, Plus, RefreshCw, Search, Shield, Workflow, X} from 'lucide-react';
import {Button} from '../components/ui/button';
import {Input} from '../components/ui/input';
import {Select} from '../components/ui/select';
import {type Engine, type Project, type Provider, type Session} from '../lib/api';
import {listSessions, SessionCache} from '../lib/workspace-data';
import {taskState} from '../lib/task-presentation';
import './control-plane.css';

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
  onPage: (page: ControlPage) => void;
  onManage: (section: string) => void;
  onNew: (projectId?: string) => void;
  onOpen: (sessionId: string, view: ExecutionView) => void;
  onProject: (project: Project) => void;
  onRefresh: () => void;
};
type Filter = 'all' | 'running' | 'attention' | 'completed';
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
  const {page, ownerId, enabled, projects, engines, providers, onPage, onManage, onNew, onOpen, onProject} = props;
  const [sessions, setSessions] = useState<Session[]>([]);
  const [loading, setLoading] = useState(true), [error, setError] = useState(''), [updated, setUpdated] = useState(0), [version, setVersion] = useState(0);
  const [query, setQuery] = useState(''), [project, setProject] = useState(''), [filter, setFilter] = useState<Filter>('all'), [limit, setLimit] = useState(40), [inspected, setInspected] = useState('');
  // Fetch the unarchived catalog independently of the sidebar's archive switch.
  // One request at a time; hidden tabs and locked sessions do not poll.
  useEffect(() => {
    if (!enabled) return;
    let stopped = false, busy = false;
    const refresh = async () => {
      if (stopped || busy || document.hidden) return;
      busy = true; setLoading(true);
      try { const rows = await listSessions(false); if (!stopped) { setSessions(rows); setUpdated(Date.now()); setError(''); } }
      catch (reason) { if (!stopped) setError(reason instanceof Error ? reason.message : String(reason)); }
      finally { busy = false; if (!stopped) setLoading(false); }
    };
    void refresh();
    const timer = setInterval(() => void refresh(), 10000);
    const visible = () => { if (!document.hidden) void refresh(); };
    document.addEventListener('visibilitychange', visible);
    return () => { stopped = true; clearInterval(timer); document.removeEventListener('visibilitychange', visible); };
  }, [ownerId, enabled, version]);
  const rows = useMemo(() => controlRows(sessions, query, project, filter), [sessions, query, project, filter]);
  const counts = {running: sessions.filter(row => taskState(row.latest_status || '').active).length, attention: sessions.filter(row => taskState(row.latest_status || '').attention).length, completed: sessions.filter(row => row.latest_status === 'completed').length};
  const inspectedRow = sessions.find(row => row.id === inspected);
  const openFilter = (value: Filter, projectId = project) => { setFilter(value); setProject(projectId); setLimit(40); setInspected(''); onPage('tasks'); };
  const heading = page === 'overview' ? 'Control plane' : page === 'projects' ? 'Projects' : 'Runs';
  return <section className="control-plane" aria-label="Desktop control plane">
    <header className="control-heading"><div><span className="control-eyebrow">TERMX WORKSPACE</span><h1>{heading}</h1><p>Coordinate projects and agents. Inspect execution. Review and deliver work.</p></div><div className="control-actions"><Button variant="outline" disabled={loading || !enabled} onClick={() => { setVersion(value => value + 1); props.onRefresh(); }}><RefreshCw size={14}/>Refresh</Button><Button onClick={() => onNew(project || undefined)}><Plus size={15}/>New task</Button></div></header>
    <nav className="control-tabs" aria-label="Control plane views">{([['overview', 'Overview'], ['projects', 'Projects'], ['tasks', 'Runs']] as const).map(([id, label]) => <button key={id} aria-current={page === id ? 'page' : undefined} onClick={() => onPage(id)}>{label}</button>)}<span role="status">{error ? 'Updates interrupted' : loading ? 'Refreshing…' : updated ? `Updated ${new Date(updated).toLocaleTimeString()}` : 'Waiting for host'}</span></nav>
    {error && <div className="control-error" role="alert">{error}{updated ? ' — showing the last received state.' : ' — run state is unavailable.'}<Button variant="ghost" onClick={() => setVersion(value => value + 1)}>Retry</Button></div>}
    {props.catalogState === 'failed' && <p className="control-error" role="alert">Project and engine catalog could not be refreshed. Use Refresh to retry.</p>}
    {page === 'overview' && <>
      <div className="control-metrics">{([{id:'attention', label:'Needs attention', icon:Shield}, {id:'running', label:'In progress', icon:Activity}, {id:'completed', label:'Completed', icon:GitBranch}] as const).map(item => <button key={item.id} onClick={() => openFilter(item.id, '')}><item.icon size={18}/><span>{item.label}</span><strong>{updated ? counts[item.id] : '—'}</strong><ArrowUpRight size={14}/></button>)}</div>
      <div className="control-overview-grid"><section className="control-section"><div className="control-section-heading"><h2>Attention queue</h2><Button variant="ghost" size="sm" onClick={() => openFilter('attention', '')}>View queue</Button></div><p>Latest run in each conversation, across your accessible projects.</p>
        {!updated ? <div className="control-empty">{loading ? 'Loading run state…' : 'Run state unavailable.'}</div> : !counts.attention ? <div className="control-empty"><Shield size={24}/><strong>No pending decisions</strong><span>Approvals, recovery decisions, paused runs and failures appear here.</span></div> : sessions.filter(row => taskState(row.latest_status || '').attention).slice(0, 5).map(row => <button className="control-queue-row" key={row.id} onClick={() => { setInspected(row.id); onPage('tasks'); }}><span><strong>{row.title || 'Untitled task'}</strong><small>{projects.find(item => item.id === row.project_id)?.name || row.cwd || 'Unassigned'}</small></span><Status value={row.latest_status}/><ArrowUpRight size={14}/></button>)}
      </section><section className="control-section"><div className="control-section-heading"><h2>Execution & configuration</h2><Layers size={17}/></div><p>{props.catalogState === 'ready' ? `${engines.length} installed engines · ${providers.filter(row => row.secret_configured).length} configured provider accounts` : 'Loading engine and provider catalog…'}</p><div className="control-destinations">{[
        {id:'agents', title:'Agent presets', text:'Instructions, tools, limits and execution policy', icon:Layers},
        {id:'runners', title:'Runners', text:'Provision execution environments and inspect jobs', icon:Box},
        {id:'automations', title:'Automations', text:'Schedules, goals and unattended work', icon:Workflow},
        {id:'models', title:'Models & engines', text:'Accounts, installed engines and capabilities', icon:Activity},
        {id:'access', title:'Access & sessions', text:'Pair remote clients and manage workspace access', icon:Shield},
      ].map(item => <button key={item.id} onClick={() => onManage(item.id)}><item.icon size={18}/><span><strong>{item.title}</strong><small>{item.text}</small></span><ArrowUpRight size={14}/></button>)}</div></section></div>
      <div className="control-section-heading"><h2>Project portfolio</h2><Button variant="ghost" onClick={() => onPage('projects')}>All projects</Button></div>
    </>}
    {(page === 'overview' || page === 'projects') && <>
      {!projects.length && <div className="control-empty"><Folder size={28}/><h2>{props.catalogState === 'loading' ? 'Loading projects…' : props.catalogState === 'failed' ? 'Projects unavailable' : 'Connect your first project'}</h2><p>Register a folder on the host to create tasks and open the development workbench.</p><Button onClick={() => onNew()}>Add a project</Button></div>}
      <div className="control-projects">{(page === 'overview' ? projects.slice(0, 6) : projects).map(item => {
        const related = sessions.filter(row => row.project_id === item.id), active = related.filter(row => taskState(row.latest_status || '').active).length, attention = related.filter(row => taskState(row.latest_status || '').attention).length;
        return <article key={item.id}><div className="control-section-heading"><Folder size={18}/><h3>{item.name}</h3></div><code title={item.path}>{item.path}</code><p>{updated ? `${active} active · ${attention} need attention · ${related.length} conversations` : 'Run counts unavailable'}</p><div className="control-actions"><Button size="sm" variant="outline" onClick={() => onProject(item)}>Open workbench</Button><Button size="sm" variant="ghost" onClick={() => openFilter('all', item.id)}>View runs</Button><Button size="sm" variant="ghost" onClick={() => onNew(item.id)}>New task</Button></div></article>;
      })}</div>
    </>}
    {page === 'tasks' && <>
      <div className="control-run-tools"><Search size={17}/><Input aria-label="Search runs" placeholder="Search tasks, branches, models or runners" value={query} onChange={event => { setQuery(event.target.value); setLimit(40); }}/><Select aria-label="Filter runs by project" value={project} onChange={event => { setProject(event.target.value); setLimit(40); }}><option value="">All projects</option>{projects.map(item => <option value={item.id} key={item.id}>{item.name}</option>)}</Select><Select aria-label="Filter runs by status" value={filter} onChange={event => { setFilter(event.target.value as Filter); setLimit(40); }}><option value="all">All states</option><option value="attention">Needs attention</option><option value="running">In progress</option><option value="completed">Completed</option></Select></div>
      <p className="control-description">Latest run per conversation. Open a workspace for the complete execution history and child-agent supervision.</p>
      <div className={'control-run-layout' + (inspectedRow ? ' with-inspector' : '')}><div className="control-table-wrap"><table className="control-run-table"><thead><tr><th scope="col">Task / project</th><th scope="col">State</th><th scope="col">Execution target</th><th scope="col">Updated</th></tr></thead><tbody>{rows.slice(0, limit).map(row => <tr key={row.id} className={row.id === inspected ? 'selected' : ''}><td><button aria-label={`Inspect ${row.title || 'Untitled task'}`} aria-pressed={row.id === inspected} onClick={() => setInspected(row.id)}>{row.title || 'Untitled task'}</button><small>{projects.find(item => item.id === row.project_id)?.name || 'Unassigned'}</small></td><td><Status value={row.latest_status}/></td><td><span>{row.engine || 'TermX'}{row.model ? ` · ${row.model}` : ''}</span><small>{row.runner_id ? `Runner ${row.runner_id}` : 'Host'} · {row.worktree_branch || row.cwd || 'No checkout'}</small></td><td><time dateTime={new Date(row.updated_at * 1000).toISOString()}>{new Date(row.updated_at * 1000).toLocaleString()}</time></td></tr>)}</tbody></table>{!rows.length && <div className="control-empty">{!updated ? loading ? 'Loading runs…' : 'Run state unavailable.' : query || project || filter !== 'all' ? 'No runs match these filters.' : 'No conversations yet. Create a task to begin.'}</div>}{rows.length > limit && <Button variant="outline" onClick={() => setLimit(value => value + 40)}>Show more runs</Button>}</div>
        {inspectedRow && <RunInspector key={ownerId + ':' + inspectedRow.id} row={inspectedRow} enabled={enabled} onClose={() => setInspected('')} onOpen={view => onOpen(inspectedRow.id, view)}/>}
      </div>
    </>}
  </section>;
}

function Status({value}: {value?: string | null}) {
  const state = taskState(value || '');
  return <span className={'control-state ' + state.tone}>{state.label}</span>;
}

function RunInspector({row, enabled, onClose, onOpen}: {row: Session; enabled: boolean; onClose: () => void; onOpen: (view: ExecutionView) => void}) {
  const [session, setSession] = useState<Session | null>(null), [error, setError] = useState('');
  useEffect(() => {
    if (!enabled) return;
    let stopped = false, busy = false;
    const cache = new SessionCache();
    const read = async () => {
      if (stopped || busy || document.hidden) return;
      busy = true;
      try { const value = await cache.read(row.id); if (!stopped) { setSession(value); setError(''); } }
      catch (reason) { if (!stopped) setError(reason instanceof Error ? reason.message : String(reason)); }
      finally { busy = false; }
    };
    void read(); const timer = setInterval(() => void read(), 3000);
    return () => { stopped = true; cache.clear(); clearInterval(timer); };
  }, [row.id, enabled]);
  const current = session || row, task = session?.turns?.at(-1)?.task;
  const pending = task?.approvals?.filter(approval => approval.status === 'pending') || [];
  return <aside className="control-inspector" aria-label="Run inspector"><div className="control-section-heading"><h2>Run inspector</h2><Button size="icon" variant="ghost" aria-label="Close run inspector" onClick={onClose}><X size={16}/></Button></div><Status value={task?.status || current.latest_status}/><h3>{current.title}</h3>
    {error && <p className="control-error" role="alert">{error}. Refreshing automatically; details may be stale.</p>}
    {!session && !error && <p role="status">Loading execution details…</p>}
    <dl><dt>Engine / model</dt><dd>{current.engine} · {current.model || 'Default model'}</dd><dt>Execution</dt><dd>{current.runner_id ? `Runner ${current.runner_id}` : 'Host'}</dd><dt>Checkout</dt><dd>{current.cwd || 'No checkout'}{current.worktree_branch && <><br/>{current.worktree_branch}</>}</dd></dl>
    {task && <section><h4>Current task</h4><p>{task.prompt}</p>{pending.length > 0 && <p className="control-attention">{pending.length} pending approval{pending.length > 1 ? 's' : ''}. Review the exact action in the workspace.</p>}{task.error && <p className="control-error">{task.error}</p>}{task.result && <details><summary>Latest result</summary><p className="control-result">{task.result}</p></details>}</section>}
    <div className="control-inspector-actions"><Button onClick={() => onOpen('chat')}>{taskState(task?.status || current.latest_status || '').attention ? 'Review in workspace' : 'Open workspace'}</Button><Button variant="outline" onClick={() => onOpen('review')}>Changes & delivery</Button><Button variant="ghost" onClick={() => onOpen('workbench')}>Files & terminal</Button><Button variant="ghost" onClick={() => onOpen('browser')}>Browser & preview</Button></div>
  </aside>;
}
