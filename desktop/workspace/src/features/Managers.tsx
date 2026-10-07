import { useEffect, useState } from 'react';
import { Button } from '../components/ui/button';
import { Input } from '../components/ui/input';
import { request } from '../lib/api';
import './managers.css';
import ProviderAccountForm from './ProviderAccountForm';
import { MemoryEditor } from './MemoryEditor';
import { AgentPresetEditor } from './AgentPresetEditor';
import { ManagedPairingForm } from './ManagedPairingForm';
import ExtensionManager from './ExtensionManager';
import McpManager from './McpManager';

type Row = Record<string, any>;
export interface ManagersProps { section: string; projectId: string | null; sessionId: string | null; onSection?: (name: string) => void; onCapabilitiesChanged?: () => void }
const groups = [['models', 'Models & engines'], ['agents', 'Agents'], ['extensions', 'Plugins & skills'], ['connections', 'Tools & connections'], ['memory', 'Memory'], ['hooks', 'Hooks'], ['automations', 'Automations'], ['access', 'Access & sessions']];
const scoped = (project: string | null) => project ? `?project_id=${encodeURIComponent(project)}` : '';
const body = (value: unknown, method = 'POST'): RequestInit => ({ method, body: JSON.stringify(value), headers: { 'Content-Type': 'application/json' } });
const date = (value?: number) => value ? new Date(value * 1000).toLocaleString() : '—';
async function gql<T = Row>(query: string, variables: Row = {}): Promise<T> {
  const result = await request<{ data?: T; errors?: { message: string }[] }>('/graphql', body({ query, variables }));
  if (result.errors?.length) throw new Error(result.errors.map(e => e.message).join('\n'));
  return result.data as T;
}
function download(name: string, value: unknown) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2)], { type: 'application/json' }));
  const anchor = document.createElement('a'); anchor.href = url; anchor.download = name; anchor.click(); URL.revokeObjectURL(url);
}
function Json({ value }: { value: unknown }) { return <pre className="manager-json">{JSON.stringify(value, null, 2)}</pre> }
function Field({ label, children }: { label: string; children: React.ReactNode }) { return <label className="manager-field"><span>{label}</span>{children}</label> }
function Empty({ children }: { children: React.ReactNode }) { return <div className="manager-empty">{children}</div> }
function Item({ title, meta, children }: { title: string; meta?: React.ReactNode; children?: React.ReactNode }) { return <article className="manager-item"><div><h3>{title}</h3>{meta && <p>{meta}</p>}</div><div className="manager-item-actions">{children}</div></article> }

export function Managers({ section: incoming, projectId, sessionId, onSection, onCapabilitiesChanged }: ManagersProps) {
  const section = ({ plugins: 'extensions', skills: 'extensions', engines: 'models', schedules: 'automations', goals: 'automations', sessions: 'access', mcp: 'connections' } as Row)[incoming] || incoming || 'models';
  const [data, setData] = useState<Row>({}), [me, setMe] = useState<Row>({}), [loading, setLoading] = useState(true), [busy, setBusy] = useState(false);
  const [error, setError] = useState(''), [notice, setNotice] = useState(''), [search, setSearch] = useState(''), [version, setVersion] = useState(0);
  const [draft, setDraft] = useState<Row>({}), [preview, setPreview] = useState<Row | null>(null), [selected, setSelected] = useState<string>('');
  const [editingProvider,setEditingProvider]=useState<Row|null>(null);
  const [editingMemory,setEditingMemory]=useState<Row|null>(null);
  const [editingPreset,setEditingPreset]=useState<Row|null>(null);
  useEffect(()=>{setEditingMemory(null);setEditingPreset(null)},[section,projectId]);
  const admin = (me.principal?.scopes || []).includes('host-admin');
  useEffect(() => { let cancelled = false; request<Row>('/auth/me').then(value => { if (!cancelled) setMe(value) }).catch(() => {}); return () => { cancelled = true } }, []);
  useEffect(() => {
    let cancelled = false; setLoading(true); setError(''); setDraft({}); setPreview(null); setSelected('');
    async function load(): Promise<Row> {
      if (section === 'models') return gql('{ engines { id label installed version auth_state error capabilities } agent_providers { id name kind model base_url secret_configured capabilities } acp_registry }');
      if (section === 'agents') return gql('{ custom_agents { id name description instructions engine model enabled tools tools_mode toolsets deny_tools limits approval_mode sandbox_profile file_revision mcp_connections } }');
      if (section === 'connections') return {};
      if (section === 'memory') return request<Row>(`/api/workspace/memory${scoped(projectId)}`);
      if (section === 'hooks') { const [hooks, runs] = await Promise.all([request<Row>(`/api/workspace/hooks${scoped(projectId)}`), request<Row>(`/api/workspace/hook-runs${scoped(projectId)}`)]); return { ...hooks, ...runs } }
      if (section === 'automations') return request<Row>(`/api/workspace/automations${scoped(projectId)}`);
      if (section === 'extensions') return request<Row>('/api/workspace/extensions');
      if (section === 'access') {
        const [access, sessions, methods] = await Promise.all([request<Row>('/auth/access'), request<Row[]>('/auth/sessions'), request<Row>('/auth/methods')]);
        const result: Row = { access, sessions, methods };
        if (admin) { const [principals, adapters, configuration] = await Promise.all([request<Row[]>('/auth/admin/principals'), request<Row[]>('/auth/admin/adapters'), request<Row>('/auth/admin/adapters/configuration')]); Object.assign(result, { principals, adapters, configuration }) }
        return result;
      }
      return {};
    }
    load().then(value => { if (!cancelled) setData(value) }).catch(exc => { if (!cancelled) setError(exc.message) }).finally(() => { if (!cancelled) setLoading(false) });
    return () => { cancelled = true };
  }, [section, projectId, admin, version]);
  const change = (name: string, value: unknown) => setDraft(current => ({ ...current, [name]: value }));
  const matches = (row: Row) => `${row.id} ${row.name || row.label || row.content || row.event || ''}`.toLowerCase().includes(search.toLowerCase());
  const mutate = async (fn: () => Promise<unknown>, message: string, refresh = true) => { setBusy(true); setError(''); setNotice(''); try { const result = await fn(); if(section==='models')onCapabilitiesChanged?.(); setNotice(message); if (refresh) setVersion(v => v + 1); return result } catch (exc) { setError(exc instanceof Error ? exc.message : String(exc)); return null } finally { setBusy(false) } };
  const post = (path: string, value: unknown, message: string, method = 'POST') => mutate(() => request(path, body(value, method)), message);
  const jsonDraft = (key: string, fallback: unknown) => JSON.parse(draft[key] || JSON.stringify(fallback));
  const confirmDelete = (path: string, message: string) => mutate(() => request(path, { method: 'DELETE' }), message);
  const textarea = (key: string, label: string, placeholder = '') => <Field label={label}><textarea value={draft[key] || ''} placeholder={placeholder} onChange={e => change(key, e.target.value)} /></Field>;
  const input = (key: string, label: string, options: { placeholder?: string; type?: string; defaultValue?: string } = {}) => <Field label={label}><Input value={draft[key] ?? options.defaultValue ?? ''} type={options.type || 'text'} placeholder={options.placeholder} onChange={e => change(key, e.target.value)} /></Field>;
  const action = (label: string, fn: () => unknown, secondary = false) => <Button disabled={busy} variant={secondary ? 'secondary' : 'default'} onClick={fn}>{label}</Button>;
  const select = (key: string, label: string, values: [string, string][], fallback: string) => <Field label={label}><select value={draft[key] || fallback} onChange={e => change(key, e.target.value)}>{values.map(([value, text]) => <option key={value} value={value}>{text}</option>)}</select></Field>;
  const scheduleSpec = () => draft.scheduleKind === 'interval' ? { kind: 'interval', seconds: Number(draft.intervalMinutes || 60) * 60 } : { kind: 'daily', time: draft.time || '09:00', timezone: draft.timezone || Intl.DateTimeFormat().resolvedOptions().timeZone };
  const limits = () => ({ max_steps: Number(draft.maxSteps || 128), max_seconds: Number(draft.maxSeconds || 3600), shell_timeout_s: 120, max_parallel_subagents: Number(draft.parallel || 3), max_subagents_total: Number(draft.totalAgents || 8) });

  return <section className="managers" aria-label="Workspace managers">
    <aside className="manager-nav"><h2>Workspace settings</h2>{groups.map(([id, title]) => <button key={id} className={id === section ? 'selected' : ''} aria-current={id === section ? 'page' : undefined} onClick={() => onSection?.(id)}>{title}</button>)}</aside>
    <main className="manager-main"><header className="manager-heading"><div><h1>{groups.find(([id]) => id === section)?.[1] || 'Workspace settings'}</h1><p>{projectId ? 'Settings for this project and your account.' : 'Manage capabilities, permissions and continuity.'}</p></div><Button variant="secondary" disabled={loading || busy} onClick={() => setVersion(v => v + 1)}>Refresh</Button></header>
      <Input className="manager-search" placeholder="Search this manager…" aria-label="Search settings" value={search} onChange={e => setSearch(e.target.value)} />
      {error && <div role="alert" className="manager-error">{error}<button onClick={() => setVersion(v => v + 1)}>Retry</button></div>}{notice && <div role="status" className="manager-notice">{notice}</div>}
      {loading && section !== 'extensions' ? <Empty>Loading current settings…</Empty> : <>
      {section === 'models' && <><h2>Installed engines</h2>{(data.engines || []).filter(matches).map((engine: Row) => <Item key={engine.id} title={engine.label} meta={`${engine.installed ? 'Installed' : 'Needs installation'} · ${engine.auth_state || 'Unknown account status'}${engine.version ? ` · ${engine.version}` : ''}`}>
        {action('Inspect capabilities', () => setPreview(engine), true)}{action('Refresh models', () => mutate(async () => { const value = await gql('query($id:String!){engine_models(engine_id:$id)}', { id: engine.id }); setPreview(value); return value }, 'Model catalog refreshed', false), true)}
        <details><summary>Account sign-in</summary><Input aria-label={`Sign-in method for ${engine.label}`} placeholder="Method ID from engine account settings" value={draft[`method-${engine.id}`] || ''} onChange={e => change(`method-${engine.id}`, e.target.value)} />{action('Sign in', () => mutate(() => gql('mutation($id:String!,$method:String!){authenticate_engine(engine_id:$id,method_id:$method)}', { id: engine.id, method: draft[`method-${engine.id}`] || '' }), 'Sign-in request completed'))}</details>
      </Item>)}{preview && <details open><summary>Selected engine capabilities/catalog</summary><Json value={preview} /></details>}
      <h2>Provider accounts</h2>{(data.agent_providers || []).filter(matches).map((provider: Row) => <Item key={provider.id} title={provider.name || provider.id} meta={`${provider.kind} · ${provider.secret_configured ? 'Credential configured' : 'Credential required'} · ${provider.model || 'Choose model per session'}`}>{action('Test connection', () => mutate(() => gql('mutation($id:String!){test_agent_provider(provider_id:$id)}', { id: provider.id }), 'Provider connection tested'), true)}{admin && ['openai','openai-compatible'].includes(provider.kind) && action('Edit account',()=>setEditingProvider(provider),true)}{admin && action('Remove account', () => mutate(() => gql('mutation($id:String!){delete_agent_provider(provider_id:$id){ok}}', { id: provider.id }), 'Provider removed'), true)}</Item>)}
      {admin && editingProvider && <ProviderAccountForm key={editingProvider.id} account={editingProvider as any} busy={busy} onCancel={()=>setEditingProvider(null)} onSave={async input=>{const result=await mutate(()=>gql('mutation($input:AgentProviderInput!){save_agent_provider(input:$input){id}}',{input}),'Provider account updated');if(result!==null)setEditingProvider(null);return result}}/>}
      {admin && <details><summary>Add provider account</summary><ProviderAccountForm busy={busy} onSave={input=>mutate(()=>gql('mutation($input:AgentProviderInput!){save_agent_provider(input:$input){id}}',{input}),'Provider account saved')}/></details>}
      {admin && <details><summary>Install a compatible engine</summary><div className="manager-form">{input('registryId', 'Registry package ID')}{action('Refresh registry', () => mutate(() => gql('mutation{refresh_acp_registry}'), 'Registry refreshed'))}{action('Install engine', () => mutate(() => gql('mutation($id:String!){install_acp_runner(registry_id:$id)}', { id: draft.registryId }), 'Engine installed'))}<Json value={data.acp_registry} /></div></details>}<p className="manager-help">Choose an engine or model in each chat composer. Account changes do not retarget running sessions. API-key providers and subscription engines use separate billing.</p></>}

      {section === 'agents' && <><h2>Presets for future sessions</h2>{(data.custom_agents || []).filter(matches).map((agent: Row) => <Item key={agent.id} title={agent.name} meta={agent.description}>{action('Inspect', () => setPreview(agent), true)}{admin && action('Edit preset',()=>setEditingPreset(agent),true)}{admin && action('Remove', () => mutate(() => gql('mutation($id:String!){delete_custom_agent(agent_id:$id){deleted}}', { id: agent.id }), 'Agent preset removed'), true)}</Item>)}{preview && <Json value={preview} />}
      {admin && editingPreset && <AgentPresetEditor key={editingPreset.id} preset={editingPreset} onCancel={()=>setEditingPreset(null)} onSave={async input=>{const result=await mutate(()=>gql('mutation($id:String!,$input:CustomAgentPatchInput!){patch_custom_agent(agent_id:$id,input:$input){id}}',{id:editingPreset.id,input}),'Preset updated for future runs');if(result!==null)setEditingPreset(null);return result}}/>}
      {admin && <details open><summary>Create agent preset</summary><AgentPresetEditor onSave={input=>mutate(()=>gql('mutation($input:CustomAgentInput!){create_custom_agent(input:$input){id}}',{input}),'Preset saved')}/></details>}</>}

      {section === 'memory' && <><h2>Memory with provenance</h2>{!(data.memory || []).length && <Empty>No memory saved. Add facts and decisions that should carry into future sessions.</Empty>}{(data.memory || []).filter(matches).map((memory: Row) => <article key={memory.id} className="manager-memory"><p>{memory.content}</p><small>{memory.provenance} · {memory.project_id ? 'Project' : 'Your account'} · expires {date(memory.expires_at)}{memory.excluded ? ' · Excluded from execution' : ''}</small><div>{action('Edit memory',()=>setEditingMemory(memory),true)}{action(memory.excluded ? 'Include in context' : 'Exclude from context', () => post('/api/workspace/memory', { identifier: memory.id, revision: memory.revision, content: memory.content, provenance: memory.provenance, project_id: memory.project_id || null, retention_days: memory.retention_days, excluded: !memory.excluded }, 'Memory scope updated'), true)}{action('Delete', () => confirmDelete(`/api/workspace/memory/${memory.id}`, 'Memory deleted'), true)}</div></article>)}
      {editingMemory && <MemoryEditor key={editingMemory.id} memory={editingMemory as any} onCancel={()=>setEditingMemory(null)} onSaved={()=>{setEditingMemory(null);setNotice('Memory updated');setVersion(v=>v+1)}}/>}
      <details open><summary>Add memory</summary><div className="manager-form">{textarea('memoryContent', 'Fact or decision')}{input('provenance', 'Source or reason')}{input('retention', 'Retention in days', { type: 'number', defaultValue: '30' })}{action('Save memory', () => post('/api/workspace/memory', { content: draft.memoryContent, provenance: draft.provenance, project_id: projectId, retention_days: Number(draft.retention || 30) }, 'Memory saved'))}{action('Export memory', () => mutate(async () => { const value = await request(`/api/workspace/memory/export${scoped(projectId)}`); download('termx-memory.json', value) }, 'Memory exported', false), true)}</div></details></>}

      {section === 'hooks' && <><h2>Project lifecycle</h2>{!projectId && <Empty>Select a project to configure lifecycle hooks.</Empty>}{(data.hooks || []).filter(matches).map((hook: Row) => <Item key={hook.id} title={hook.event.replaceAll('_', ' ')} meta={`${hook.argv.join(' ')} · timeout ${hook.timeout_s}s · ${hook.enabled ? 'Enabled' : 'Disabled'}`}>
        {action(hook.enabled ? 'Disable' : 'Enable', () => post(`/api/workspace/hooks/${hook.id}`, { enabled: !hook.enabled, revision: hook.revision }, 'Hook updated', 'PATCH'), true)}{action('Remove', () => confirmDelete(`/api/workspace/hooks/${hook.id}`, 'Hook removed'), true)}</Item>)}
      {projectId && <details open><summary>Add a hook</summary><div className="manager-form">{select('hookEvent', 'When to run', [['before_turn','Before turn'],['after_turn','After turn'],['task_failed','Task failed']], 'before_turn')}{textarea('hookArgv', 'Command and arguments', '["python", "scripts/check.py"]')}{input('hookCwd', 'Project folder')}{input('hookTimeout', 'Timeout in seconds', { type: 'number', defaultValue: '10' })}{action('Save disabled hook', () => mutate(() => request('/api/workspace/hooks', body({ project_id: projectId, event: draft.hookEvent || 'before_turn', argv: jsonDraft('hookArgv', []), cwd: draft.hookCwd, timeout_s: Number(draft.hookTimeout || 10), enabled: false, capabilities: [] })), 'Hook saved; enable it after reviewing its command'))}<p className="manager-help">Hooks execute project code. The host checks file permissions and enforces timeout and sandbox policy.</p></div></details>}
      <details><summary>Execution history</summary><Json value={data.runs || []} /></details></>}

      {section === 'automations' && <><h2>Goals and schedules</h2>{!(data.goal || []).length && <Empty>Create a bounded goal in the current chat, then grant unattended execution and preview its schedule.</Empty>}{(data.goal || []).filter(matches).map((goal: Row) => <Item key={goal.id} title={goal.success_criteria} meta={`${goal.status} · ${goal.runs_started}/${goal.max_runs} runs`}>
        {action('Schedule', () => { setSelected(goal.id); setDraft({ maxRuns: goal.max_runs, maxSteps:goal.limits?.max_steps, maxSeconds:goal.limits?.max_seconds, parallel:goal.limits?.max_parallel_subagents, totalAgents:goal.limits?.max_subagents_total }); setPreview(null) }, true)}{action('Cancel goal and children', () => post(`/api/workspace/goals/${goal.id}/cancel`, {}, 'Goal and descendant tasks cancelled'), true)}</Item>)}
      {(data.schedule || []).map((schedule: Row) => <Item key={schedule.id} title={schedule.prompt} meta={`${schedule.state} · next ${date(schedule.next_run)} · missed ${schedule.missed}, overlap ${schedule.overlap}`}>
        {action(schedule.enabled ? 'Pause' : 'Resume', () => post(`/api/workspace/schedules/${schedule.id}`, { enabled: !schedule.enabled, revision: schedule.revision }, 'Schedule updated', 'PATCH'), true)}</Item>)}
      <details open><summary>{selected ? 'Authorize scheduled work' : 'Create a bounded goal'}</summary><div className="manager-form">{!selected && textarea('success', 'Success criteria')}{input('maxRuns', 'Maximum runs', { type: 'number', defaultValue: '5' })}{input('maxSteps', 'Maximum steps per run', { type: 'number', defaultValue: '128' })}{input('maxSeconds', 'Maximum seconds per run', { type: 'number', defaultValue: '3600' })}{input('parallel', 'Parallel subagents', { type: 'number', defaultValue: '3' })}{input('totalAgents', 'Total subagents per run', { type: 'number', defaultValue: '8' })}
        {!selected ? <>{!sessionId && <p>Select a chat before creating a goal.</p>}{action('Create goal', () => post('/api/workspace/goals', { conversation_id: sessionId, success_criteria: draft.success, max_runs: Number(draft.maxRuns || 5), limits: limits() }, 'Bounded goal created'))}</> : <>
          {textarea('scheduledPrompt', 'Prompt for each run')}{select('scheduleKind', 'Schedule', [['daily','Daily'],['interval','Every interval']], 'daily')}{draft.scheduleKind === 'interval' ? input('intervalMinutes', 'Interval in minutes', { type:'number',defaultValue:'60' }) : <>{input('time', 'Time', { type: 'time', defaultValue: '09:00' })}{input('timezone','Timezone', { defaultValue: Intl.DateTimeFormat().resolvedOptions().timeZone })}</>}
          {select('missed', 'If the host was offline', [['skip','Skip missed run'],['once','Run once on return']], 'skip')}{select('overlap', 'If an earlier run is active', [['skip','Skip overlapping run'],['queue','Queue one run']], 'skip')}{input('grantDays','Authority expires after days', { type:'number',defaultValue:'7' })}
          {action('Preview next runs', () => mutate(async () => { const result = await request<Row>('/api/workspace/schedules/preview', body(scheduleSpec())); setPreview(result); return result }, 'Schedule preview ready', false), true)}
          {preview && <ul>{(preview.next_runs || []).map((value: number) => <li key={value}>{date(value)}</li>)}</ul>}
          {action('Authorize and create schedule', () => mutate(async () => { const grant = await request<Row>('/api/workspace/delegations', body({ goal_id:selected, expires_at:Date.now()/1000 + Number(draft.grantDays || 7)*86400, max_runs:Number(draft.maxRuns || 5), limits:limits() })); return request('/api/workspace/schedules', body({ goal_id:selected, grant_id:grant.id, prompt:draft.scheduledPrompt, spec:scheduleSpec(), missed:draft.missed || 'skip', overlap:draft.overlap || 'skip', enabled:true })) }, 'Schedule authorized and saved'))}
          <p className="manager-help">This grants unattended runs within the shown budget until expiry. A pending approval remains pending until someone resolves it.</p></>}
      </div></details><details><summary>Run and delegation history</summary><Json value={{ runs:data.schedule_run || [], grants:data.delegation || [] }} /></details>
      {(data.delegation || []).filter((grant: Row) => !grant.revoked).map((grant: Row) => <Item key={grant.id} title={`Execution grant · ${grant.goal_id}`} meta={`Expires ${date(grant.expires_at)} · ${grant.used_runs}/${grant.max_runs} runs`}>{action('Revoke unattended authority', () => confirmDelete(`/api/workspace/delegations/${grant.id}`, 'Delegated authority revoked'), true)}</Item>)}</>}

      {section === 'extensions' && <ExtensionManager key={projectId||'global'} data={data} projectId={projectId} onChanged={()=>setVersion(v=>v+1)}/>}

      {section === 'connections' && <McpManager projectId={projectId} admin={admin} search={search} version={version} onChanged={()=>setVersion(value=>value+1)}/>}

      {section === 'access' && <>{me.principal?.id && me.session_id && me.host_id && <ManagedPairingForm actor={me as {principal:{id:string;display_name:string;scopes:string[]};session_id:string;host_id:string}}/>}<h2>Your sessions</h2><p className="manager-help">{data.access?.role || 'Member'} · {data.access?.runtime_boundary || 'Host policy'} · Managed application permissions apply to each resource. Trusted host execution shares the host OS user.</p>
      {(data.sessions || []).map((session:Row) => <Item key={session.id} title={session.device_name || 'Unnamed device'} meta={`${session.strength} · expires ${date(session.expires)}${session.revoked ? ' · Revoked' : session.id === me.session_id ? ' · This session' : ''}`}>
        {!session.revoked && action('Revoke session',()=>confirmDelete(`/auth/sessions/${session.id}`,'Session revoked'),true)}</Item>)}
      {admin && <><h2>People and project access</h2>{(data.principals || []).filter(matches).map((principal:Row) => <Item key={principal.id} title={principal.display_name} meta={`${principal.role || 'No role'} · ${principal.enabled ? 'Enabled' : 'Disabled'}${principal.trusted_execution ? ' · Trusted host execution' : ''}`}>
        {action('Manage access',()=>{setSelected(principal.id);setDraft({role:principal.role})},true)}{action('Revoke all devices',()=>confirmDelete(`/auth/admin/principals/${principal.id}/sessions`,'Device sessions revoked'),true)}
      </Item>)}
      {selected && <div className="manager-form">{select('role','Host role',[['admin','Administrator'],['operator','Operator'],['viewer','Viewer']],'viewer')}<label><input type="checkbox" checked={Boolean(draft.trusted)} onChange={e=>change('trusted',e.target.checked)} />Allow trusted host execution (shared OS user)</label>{action('Save role',()=>post(`/auth/admin/principals/${selected}/role`,{role:draft.role || 'viewer',trusted_execution:Boolean(draft.trusted)},'Role updated','PUT'))}
        {projectId && <>{select('projectRole','This project',[['view','Read files and chats'],['operate','Read, edit and run']],'view')}{action('Grant project access',()=>post(`/auth/admin/principals/${selected}/projects/${projectId}`,{scopes:draft.projectRole==='operate' ? ['files-read','files-write','git-read','git-write','agent-view','agent-control','agent-run','terminal-view','terminal-control'] : ['files-read','git-read','agent-view','terminal-view']},'Project access granted','PUT'))}{action('Revoke project access',()=>confirmDelete(`/auth/admin/principals/${selected}/projects/${projectId}`,'Project access revoked'),true)}</>}
        {input('issuer','Identity issuer')}{input('subject','Stable identity subject')}{action('Bind verified identity',()=>post(`/auth/admin/principals/${selected}/bindings`,{issuer:draft.issuer,subject:draft.subject},'Explicit identity mapping saved'))}
      </div>}
      <details><summary>Add a person</summary><div className="manager-form">{input('personName','Name or local username')}{input('personPassword','Local password (leave empty for SSO)',{type:'password'})}{select('newRole','Initial role',[['viewer','Viewer'],['operator','Operator'],['admin','Administrator']],'viewer')}{action('Create account',()=>post('/auth/admin/principals',{name:draft.personName,password:draft.personPassword || null,role:draft.newRole || 'viewer'},'Account created'))}</div></details>
      <h2>Sign-in methods</h2>{(data.adapters || []).map((adapter:Row)=><Item key={adapter.id} title={adapter.label} meta={`${adapter.enabled ? 'Enabled' : 'Disabled'} · ${adapter.health} · v${adapter.version}`}>
        {action('Check health',()=>post(`/auth/admin/adapters/${adapter.id}/health`,{},'Method health checked'),true)}{action(adapter.enabled ? 'Disable and revoke sessions' : 'Enable',()=>post(`/auth/admin/adapters/${adapter.id}`,{enabled:!adapter.enabled,session_policy:'revoke'},'Sign-in method updated','PUT'),true)}</Item>)}
      <details><summary>Configure enterprise identity</summary><div className="manager-form">{textarea('authConfig','Provider trust configuration',JSON.stringify(data.configuration?.configuration,null,2))}{input('testAdapter','Adapter ID to test')}{textarea('authEvidence','Signed assertion test evidence','{"assertion":"…"}')}
        {action('Test login and policy',()=>mutate(()=>request('/auth/admin/adapters/configuration/test',body({configuration:jsonDraft('authConfig',data.configuration?.configuration),adapter_id:draft.testAdapter,evidence:jsonDraft('authEvidence',{})})),'Identity login and current policy verified',false),true)}
        {action('Test SSO login',()=>mutate(async()=>{const result=await request<Row>('/auth/admin/adapters/configuration/begin',body({configuration:jsonDraft('authConfig',data.configuration?.configuration),adapter_id:draft.testAdapter}));window.location.assign(result.authorization_url);return result},'SSO test started',false),true)}
        {select('sessionPolicy','Existing provider sessions',[['revoke','Revoke immediately'],['expire','Keep until expiry']],'revoke')}{action('Activate tested configuration',()=>post('/auth/admin/adapters/configuration',{configuration:jsonDraft('authConfig',data.configuration?.configuration),session_policy:draft.sessionPolicy || 'revoke'},'Tested identity configuration activated','PUT'))}<p className="manager-help">Every configured provider needs a current verified login and policy test. Identity bindings use stable issuer and subject, never email equality.</p></div></details>
      <details><summary>Host recovery and migration</summary><div className="manager-form">{action('Generate one-use owner recovery code',()=>mutate(async()=>{const result=await request<Row>('/auth/admin/recovery-code',body({}));setPreview(result);return result},'Save this recovery credential privately; it is displayed once',false))}{preview?.recovery_code && <output className="manager-recovery">{preview.recovery_code}</output>}{action('End legacy device migration now',()=>post('/auth/admin/migration/end',{},'Legacy credentials no longer accepted'),true)}{action('Inspect access audit',()=>mutate(async()=>{setPreview(await request('/auth/admin/audit'));return null},'Audit loaded',false),true)}{preview && !preview.recovery_code && <Json value={preview} />}</div></details></>}
      </>}
      </>}
    </main>
  </section>;
}
export default Managers;
