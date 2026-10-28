import {settingsCategories,canonicalSettingsCategory} from '../lib/settings-categories';
import {SettingsNavigation,useSettingsSave} from './SettingsSurface';
import LanTlsSettings from './LanTlsSettings';
import HostServiceSettings from './HostServiceSettings';
import CloudAccounts from './CloudAccounts';
import { useCallback, useEffect, useState } from 'react';
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
import ModelFavoritesManager from './ModelFavoritesManager';
import IdentityGroupsManager from './IdentityGroupsManager';
import AuditRetentionManager from './AuditRetentionManager';
import ScheduleManager from './ScheduleManager';
import SessionPolicyManager from './SessionPolicyManager';

type Row = Record<string, any>;
export interface ManagersProps { section: string; projectId: string | null; sessionId: string | null; onSection?: (name: string) => void; onCapabilitiesChanged?: () => void; embedded?: boolean }
const groups = settingsCategories.map(({id,label})=>[id,label]);
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
function Json({ value }: { value: unknown }) { return <details><summary>Technical details</summary><pre className="manager-json">{JSON.stringify(value, null, 2)}</pre></details> }
function Field({ label, children }: { label: string; children: React.ReactNode }) { return <label className="manager-field"><span>{label}</span>{children}</label> }
function Empty({ children }: { children: React.ReactNode }) { return <div className="manager-empty">{children}</div> }
function Disclosure({ title, children }: { title: string; children: React.ReactNode }) {
  const [visited, setVisited] = useState(false);
  return <details className="manager-disclosure" onToggle={event => { if (event.currentTarget.open) setVisited(true) }}><summary>{title}</summary>{visited && <div className="manager-disclosure-body">{children}</div>}</details>;
}
function EngineAuthentication({ engine, busy, onAuthenticate }: { engine: Row; busy: boolean; onAuthenticate: (methodId: string) => unknown }) {
  if (!engine.installed) return null;
  const methods: Row[] = engine.protocol?.name === 'acp' && Array.isArray(engine.protocol.auth_methods)
    ? engine.protocol.auth_methods.filter((method: Row) => typeof method?.id === 'string' && method.id.trim()) : [];
  if (methods.length) return <div className="manager-item-actions" role="group" aria-label={`Sign-in methods for ${engine.label}`}>{methods.map(method => <Button key={method.id} variant="secondary" disabled={busy} onClick={() => onAuthenticate(method.id)}>{method.name || method.label || method.id}</Button>)}</div>;
  return engine.auth_state === 'unauthenticated' ? <p className="manager-help">{engine.auth_detail || `Manage sign-in in ${engine.label} on this host.`}</p> : null;
}
function Item({ title, meta, children }: { title: string; meta?: React.ReactNode; children?: React.ReactNode }) { return <article className="manager-item"><div><h3>{title}</h3>{meta && <p>{meta}</p>}</div><div className="manager-item-actions">{children}</div></article> }

export function Managers({ section: incoming, projectId, sessionId, onSection, onCapabilitiesChanged, embedded=false }: ManagersProps) {
  const section = canonicalSettingsCategory(incoming);
  const beginSave=useSettingsSave();
  const [data, setData] = useState<Row>({}), [me, setMe] = useState<Row>({}), [loading, setLoading] = useState(true), [busy, setBusy] = useState(false);
  const [error, setError] = useState(''), [notice, setNotice] = useState(''), [search, setSearch] = useState(''), [version, setVersion] = useState(0);
  const [draft, setDraft] = useState<Row>({}), [preview, setPreview] = useState<Row | null>(null), [selected, setSelected] = useState<string>('');
  const [editingProvider,setEditingProvider]=useState<Row|null>(null);
  const [editingMemory,setEditingMemory]=useState<Row|null>(null);
  const [editingPreset,setEditingPreset]=useState<Row|null>(null);
  useEffect(()=>{setEditingMemory(null);setEditingPreset(null)},[section,projectId]);
  useEffect(()=>{setSearch('')},[section]);
  const admin = (me.principal?.scopes || []).includes('host-admin');
  useEffect(() => { let cancelled = false; request<Row>('/auth/me').then(value => { if (!cancelled) setMe(value) }).catch(() => {}); return () => { cancelled = true } }, []);
  useEffect(()=>{setData({});setDraft({});setPreview(null);setSelected('')},[section,projectId,admin]);
  useEffect(() => {
    let cancelled = false; setLoading(true); setError('');
    async function load(): Promise<Row> {
      if (section === 'models') return gql('{ engines { id label installed version auth_state auth_detail protocol error capabilities } agent_providers { id name kind model base_url secret_configured capabilities } acp_registry }');
      if (section === 'agents') return gql('{ engines { id label installed } custom_agents { id name description instructions engine model enabled tools tools_mode toolsets deny_tools limits approval_mode sandbox_profile file_revision mcp_connections } }');
      if (section === 'connections') return {};
      if (section === 'memory') return request<Row>(`/api/workspace/memory${scoped(projectId)}`);
      if (section === 'hooks') {
        // The command's project is an execution boundary, never the settings scope.
        const catalog = await gql<{projects: Row[]}>('{ projects { id name path } }');
        const targets = catalog.projects || [];
        const results = await Promise.allSettled(targets.map(async target => {
          const [hooks, runs] = await Promise.all([
            request<Row>(`/api/workspace/hooks${scoped(target.id)}`),
            request<Row>(`/api/workspace/hook-runs${scoped(target.id)}`),
          ]);
          return { target, hooks: hooks.hooks || [], runs: runs.runs || [] };
        }));
        const hooks: Row[] = [], runs: Row[] = [], hookProjects: Row[] = [], unavailable: string[] = [];
        results.forEach((result, index) => {
          if (result.status === 'fulfilled') { hooks.push(...result.value.hooks); runs.push(...result.value.runs); hookProjects.push(result.value.target) }
          else unavailable.push(targets[index].name);
        });
        return { hooks, runs, hookProjects, unavailable };
      }
      if (section === 'automations') return {};
      if (section === 'extensions') return request<Row>('/api/workspace/extensions');
      if (section === 'access') {
        const [access, sessions, methods] = await Promise.all([request<Row>('/auth/access'), request<Row[]>('/auth/sessions'), request<Row>('/auth/methods')]);
        const result: Row = { access, sessions, methods };
        if (admin) { const [principals, adapters, configuration] = await Promise.all([request<Row[]>('/auth/admin/principals'), request<Row[]>('/auth/admin/adapters'), request<Row>('/auth/admin/adapters/configuration')]); const catalog=await gql<{projects:{id:string;name:string}[]}>('{projects{id name}}');Object.assign(result, { principals, adapters, configuration,identityProjects:catalog.projects }) }
        return result;
      }
      return {};
    }
    load().then(value => { if (!cancelled) setData(value) }).catch(exc => { if (!cancelled) setError(exc.message) }).finally(() => { if (!cancelled) setLoading(false) });
    return () => { cancelled = true };
  }, [section, projectId, admin, version]);
  const setIdentityError=useCallback((reason:unknown)=>setError(reason instanceof Error?reason.message:'Identity settings could not be updated.'),[]);
  const change = (name: string, value: unknown) => setDraft(current => ({ ...current, [name]: value }));
  const matches = (row: Row) => {
    const terms=search.toLowerCase().trim().split(/\s+/).filter(Boolean);
    const text=[row.id,row.name,row.label,row.content,row.event,row.prompt,row.argv?.join(' '),row.cwd,(data.hookProjects || []).find((project: Row) => project.id === row.project_id)?.name,row.success_criteria,row.device_name,row.display_name,row.state,row.status,row.strength,row.spec?.kind,row.spec?.time,row.spec?.timezone,row.next_run?date(row.next_run):'',row.expires?date(row.expires):'',row.revoked?'revoked':'',row.id===me.session_id?'this session':'',row.enabled===true?'enabled':row.enabled===false?'paused':''].filter(Boolean).join(' ').toLowerCase();
    return terms.every(term=>text.includes(term));
  };
  const mutate = async (fn: () => Promise<unknown>, message: string, refresh = true) => { const saved=beginSave(); setBusy(true); setError(''); setNotice(''); try { const result = await fn(); if(refresh)saved(); if(section==='models')onCapabilitiesChanged?.(); setNotice(message); if (refresh) setVersion(v => v + 1); return result } catch (exc) { setError(exc instanceof Error ? exc.message : String(exc)); return null } finally { setBusy(false) } };
  const post = (path: string, value: unknown, message: string, method = 'POST') => mutate(() => request(path, body(value, method)), message);
  const jsonDraft = (key: string, fallback: unknown) => JSON.parse(draft[key] || JSON.stringify(fallback));
  const argvDraft=(value:string)=>value?.trim().startsWith("[")?JSON.parse(value):value?.split("\n").map(item=>item.trim()).filter(Boolean)||[];
  const confirmDelete = (path: string, message: string) => mutate(() => request(path, { method: 'DELETE' }), message);
  const textarea = (key: string, label: string, placeholder = '') => <Field label={label}><textarea required={["memoryContent","hookArgv"].includes(key)} value={draft[key] || ''} placeholder={placeholder} onChange={e => change(key, e.target.value)} /></Field>;
  const input = (key: string, label: string, options: { placeholder?: string; type?: string; defaultValue?: string } = {}) => <Field label={label}><Input required={["provenance","personName","hookCwd"].includes(key)} min={key==='retention'||key==='hookTimeout'?1:undefined} max={key==='retention'?3650:key==='hookTimeout'?60:undefined} value={draft[key] ?? options.defaultValue ?? ''} type={options.type || 'text'} placeholder={options.placeholder} onChange={e => change(key, e.target.value)} /></Field>;
  const action = (label: string, fn: () => unknown, secondary = false, disabled = false) => <Button disabled={busy || disabled} variant={secondary ? 'secondary' : 'default'} onClick={fn}>{label}</Button>;
  const select = (key: string, label: string, values: [string, string][], fallback: string) => <Field label={label}><select value={draft[key] || fallback} onChange={e => change(key, e.target.value)}>{values.map(([value, text]) => <option key={value} value={value}>{text}</option>)}</select></Field>;


  const emptyList = (rows: Row[], fallback: string) => !rows.filter(matches).length && <Empty>{search.trim() ? 'No matches. Try another search.' : fallback}</Empty>;
  const hookProject = (data.hookProjects || []).find((project: Row) => project.id === draft.hookProject);
  const hookCwd = draft.hookCwd ?? hookProject?.path ?? '';
  const validHook = Boolean(hookProject && draft.hookArgv?.trim() && hookCwd.trim() && Number.isInteger(Number(draft.hookTimeout || 10)) && Number(draft.hookTimeout || 10) >= 1 && Number(draft.hookTimeout || 10) <= 60);

  return <section className={"managers"+(embedded?" managers-embedded":"")} aria-label="Account and host settings">
    {!embedded&&<SettingsNavigation category={section} onSelect={id=>onSection?.(id)}/>}
    <main className="manager-main"><header className="manager-heading"><div><h1>{groups.find(([id]) => id === section)?.[1] || 'Settings'}</h1><p>{settingsCategories.find(item => item.id === section)?.description || 'Manage your account on this host.'}</p></div><Button variant="secondary" disabled={loading || busy} onClick={() => {if(window.dispatchEvent(new Event('termx-settings-leave',{cancelable:true})))setVersion(v => v + 1)}}>Refresh</Button></header>
      <Input className="manager-search" placeholder="Search this page…" aria-label="Search settings" value={search} onChange={e => setSearch(e.target.value)} />
      {error && <div role="alert" className="manager-error">{error}<button onClick={() => setVersion(v => v + 1)}>Retry</button></div>}{notice && <div role="status" className="manager-notice">{notice}</div>}
      {loading && !Object.keys(data).length && !['extensions','automations'].includes(section) ? <Empty>Loading current settings…</Empty> : <>
      {section === 'cloud' && <CloudAccounts search={search}/>}
      {section === 'models' && <><h2>Installed engines</h2>{emptyList(data.engines || [], 'No engines installed. Add an engine to start working.')}{(data.engines || []).filter(matches).map((engine: Row) => <Item key={engine.id} title={engine.label} meta={`${engine.installed ? 'Installed' : 'Needs installation'}${engine.installed && engine.auth_state === 'authenticated' ? ' · Signed in' : engine.installed && engine.auth_state === 'unauthenticated' ? ' · Sign-in needed' : ''}`}>
        <details className="manager-item-details"><summary>Details</summary>{engine.version && <p>Version {engine.version}</p>}{action('Inspect capabilities', () => setPreview(engine), true)}{action('Refresh models', () => mutate(async () => { const value = await gql('query($id:String!){engine_models(engine_id:$id)}', { id: engine.id }); setPreview(value); return value }, 'Model catalog refreshed', false), true)}</details>
        <EngineAuthentication engine={engine} busy={busy} onAuthenticate={method => mutate(() => gql('mutation($id:String!,$method:String!){authenticate_engine(engine_id:$id,method_id:$method)}', { id: engine.id, method }), 'Sign-in request completed')} />
      </Item>)}{preview && <details open><summary>Selected engine capabilities/catalog</summary><Json value={preview} /></details>}
      {me.principal?.id&&<ModelFavoritesManager ownerId={me.principal.id} engines={data.engines||[]} accounts={data.agent_providers||[]} search={search}/>}
      <h2>Provider accounts</h2>{emptyList(data.agent_providers || [], 'No provider accounts connected.')}{(data.agent_providers || []).filter(matches).map((provider: Row) => <Item key={provider.id} title={provider.name || provider.id} meta={`${provider.kind} · ${provider.secret_configured ? 'Credential configured' : 'Credential required'} · ${provider.model || 'Choose model per session'}`}>{action('Test connection', () => mutate(() => gql('mutation($id:String!){test_agent_provider(provider_id:$id)}', { id: provider.id }), 'Provider connection tested'), true)}{admin && ['openai','openai-compatible'].includes(provider.kind) && action('Edit account',()=>setEditingProvider(provider),true)}{admin && action('Remove account', () => mutate(() => gql('mutation($id:String!){delete_agent_provider(provider_id:$id){ok}}', { id: provider.id }), 'Provider removed'), true)}</Item>)}
      {admin && editingProvider && <ProviderAccountForm key={editingProvider.id} account={editingProvider as any} busy={busy} onCancel={()=>setEditingProvider(null)} onSave={async input=>{const result=await mutate(()=>gql('mutation($input:AgentProviderInput!){save_agent_provider(input:$input){id}}',{input}),'Provider account updated');if(result!==null)setEditingProvider(null);return result}}/>}
      {admin && <details><summary>Add provider account</summary><ProviderAccountForm busy={busy} onSave={input=>mutate(()=>gql('mutation($input:AgentProviderInput!){save_agent_provider(input:$input){id}}',{input}),'Provider account saved')}/></details>}
      {admin && <details><summary>Install a compatible engine</summary><div className="manager-form">{input('registryId', 'Registry package ID')}{action('Refresh registry', () => mutate(() => gql('mutation{refresh_acp_registry}'), 'Registry refreshed'))}{action('Install engine', () => mutate(() => gql('mutation($id:String!){install_acp_runner(registry_id:$id)}', { id: draft.registryId }), 'Engine installed'))}<Json value={data.acp_registry} /></div></details>}<p className="manager-help">Choose an engine or model in each chat composer. Account changes do not retarget running sessions. API-key providers and subscription engines use separate billing.</p></>}

      {section === 'agents' && <><h2>Agent presets</h2>{emptyList(data.custom_agents || [], 'No presets yet. Create one to reuse an agent’s instructions.')}{(data.custom_agents || []).filter(matches).map((agent: Row) => <Item key={agent.id} title={agent.name} meta={agent.description}>{action('Inspect', () => setPreview(agent), true)}{admin && action('Edit preset',()=>setEditingPreset(agent),true)}{admin && action('Remove', () => mutate(() => gql('mutation($id:String!){delete_custom_agent(agent_id:$id){deleted}}', { id: agent.id }), 'Agent preset removed'), true)}</Item>)}{preview && <Json value={preview} />}
      {admin && editingPreset && <AgentPresetEditor engines={(data.engines||[]).filter((row:Row)=>row.installed)} key={editingPreset.id} preset={editingPreset} onCancel={()=>setEditingPreset(null)} onSave={async input=>{const result=await mutate(()=>gql('mutation($id:String!,$input:CustomAgentPatchInput!){patch_custom_agent(agent_id:$id,input:$input){id}}',{id:editingPreset.id,input}),'Preset updated for future runs');if(result!==null)setEditingPreset(null);return result}}/>}
      {admin && <details className="manager-disclosure"><summary>Create agent preset</summary><AgentPresetEditor engines={(data.engines||[]).filter((row:Row)=>row.installed)} onSave={input=>mutate(()=>gql('mutation($input:CustomAgentInput!){create_custom_agent(input:$input){id}}',{input}),'Preset saved')}/></details>}</>}

      {section === 'memory' && <><h2>Saved memory</h2>{emptyList(data.memory || [], 'No memory saved. Add facts and decisions for future sessions.')}{(data.memory || []).filter(matches).map((memory: Row) => <article key={memory.id} className="manager-memory"><p>{memory.content}</p><small>{memory.excluded ? 'Excluded from execution' : 'Included in context'}</small><details className="manager-item-details"><summary>Source and retention</summary><p>{memory.provenance}</p><p>{memory.project_id ? 'Project memory' : 'Account memory'} · expires {date(memory.expires_at)}</p></details><div>{action('Edit memory',()=>setEditingMemory(memory),true)}{action(memory.excluded ? 'Include in context' : 'Exclude from context', () => post('/api/workspace/memory', { identifier: memory.id, revision: memory.revision, content: memory.content, provenance: memory.provenance, project_id: memory.project_id || null, retention_days: memory.retention_days, excluded: !memory.excluded }, 'Memory scope updated'), true)}{action('Delete', () => confirmDelete(`/api/workspace/memory/${memory.id}`, 'Memory deleted'), true)}</div></article>)}
      {editingMemory && <MemoryEditor key={editingMemory.id} memory={editingMemory as any} onCancel={()=>setEditingMemory(null)} onSaved={()=>{setEditingMemory(null);setNotice('Memory updated');setVersion(v=>v+1)}}/>}
      <details className="manager-disclosure"><summary>Add memory</summary><div className="manager-form">{textarea('memoryContent', 'Fact or decision')}{input('provenance', 'Source or reason')}{input('retention', 'Retention in days', { type: 'number', defaultValue: '30' })}{action('Save memory', () => post('/api/workspace/memory', { content: draft.memoryContent, provenance: draft.provenance, project_id: projectId, retention_days: Number(draft.retention || 30) }, 'Memory saved'))}{action('Export memory', () => mutate(async () => { const value = await request(`/api/workspace/memory/export${scoped(projectId)}`); download('termx-memory.json', value) }, 'Memory exported', false), true)}</div></details></>}

      {section === 'hooks' && <><h2>Lifecycle commands</h2>{emptyList(data.hooks || [], 'No hooks yet. Add a command to run before or after an agent turn.')}
      {(data.hooks || []).filter(matches).map((hook: Row) => <Item key={hook.id} title={hook.event.replaceAll('_', ' ')} meta={`${(data.hookProjects || []).find((project: Row) => project.id === hook.project_id)?.name || 'Project'} · ${hook.enabled ? 'Enabled' : 'Disabled'}`}>
        <details className="manager-item-details"><summary>Command details</summary><p><code>{hook.argv.join(' ')}</code></p><p>Folder: {hook.cwd}</p><p>Timeout: {hook.timeout_s} seconds. Hooks can read and write project files.</p></details>
        {action(hook.enabled ? 'Disable' : 'Enable', () => { if (!hook.enabled && !window.confirm(`Enable this hook?\nCommand: ${hook.argv.join(' ')}\nWorking folder: ${hook.cwd}\nTimeout: ${hook.timeout_s} seconds. This command can read and write project files.`)) return; return post(`/api/workspace/hooks/${hook.id}`, { enabled: !hook.enabled, revision: hook.revision }, 'Hook updated', 'PATCH') }, true)}{action('Remove', () => confirmDelete(`/api/workspace/hooks/${hook.id}`, 'Hook removed'), true)}</Item>)}
      {!!data.unavailable?.length && <p className="manager-help">Hook access is unavailable for: {data.unavailable.join(', ')}.</p>}
      {!!data.hookProjects?.length ? <details className="manager-disclosure"><summary>Add a hook</summary><div className="manager-form">
        <Field label="Run in project"><select value={draft.hookProject || ''} onChange={event => setDraft(current => ({...current, hookProject:event.target.value, hookCwd:undefined}))}><option value="">Choose a project</option>{data.hookProjects.map((project: Row) => <option key={project.id} value={project.id}>{project.name}</option>)}</select></Field>
        {select('hookEvent', 'When to run', [['before_turn','Before turn'],['after_turn','After turn'],['task_failed','Task failed']], 'before_turn')}{textarea('hookArgv', 'Executable and arguments (one per line)', 'python\nscripts/check.py')}
        <details className="manager-item-details"><summary>Folder and timeout</summary><Field label="Working folder"><Input required value={hookCwd} onChange={event => change('hookCwd', event.target.value)} /></Field>{input('hookTimeout', 'Timeout in seconds', { type: 'number', defaultValue: '10' })}</details>
        <p className="manager-help">This command can read and write files in {hookProject?.name || 'the chosen project'}. It runs only in its working folder under the host’s sandbox and timeout policy. Review it before enabling.</p>
        {action('Save disabled hook', () => mutate(() => request('/api/workspace/hooks', body({ project_id: hookProject.id, event: draft.hookEvent || 'before_turn', argv: argvDraft(draft.hookArgv), cwd: hookCwd, timeout_s: Number(draft.hookTimeout || 10), enabled: false, capabilities: [] })), 'Hook saved; enable it after reviewing its command'), false, !validHook)}
      </div></details> : <p className="manager-help">{data.unavailable?.length ? 'Hook access is required before adding a command. Ask a host administrator for access.' : 'Add a project on this host to create a hook. Hooks need a folder where their command can safely run.'}</p>}
      <Disclosure title="Execution history"><Json value={data.runs || []} /></Disclosure></>}

      {section === 'automations' && <ScheduleManager projectId={projectId} sessionId={sessionId} search={search} version={version}/>}

      {section === 'extensions' && <ExtensionManager key={projectId||'global'} data={data} projectId={projectId} search={search} onChanged={()=>setVersion(v=>v+1)}/>}

      {section === 'connections' && <McpManager projectId={projectId} admin={admin} search={search} version={version} onChanged={()=>setVersion(value=>value+1)}/>}

      {section === 'access' && <><h2>Your devices</h2>{emptyList(data.sessions || [], 'No active sessions on this account.')}
      {(data.sessions || []).filter(matches).map((session:Row) => <Item key={session.id} title={session.device_name || 'Unnamed device'} meta={session.revoked ? 'Revoked' : session.id === me.session_id ? 'This session' : `Expires ${date(session.expires)}`}><details className="manager-item-details"><summary>Session details</summary><p>Sign-in: {session.strength}. Expires {date(session.expires)}.</p></details>
        {!session.revoked && action('Revoke session',()=>confirmDelete(`/auth/sessions/${session.id}`,'Session revoked'),true)}</Item>)}
      {me.principal?.id && me.session_id && me.host_id && <Disclosure title="Pair a device"><ManagedPairingForm actor={me as {principal:{id:string;display_name:string;scopes:string[]};session_id:string;host_id:string}}/></Disclosure>}
      {admin && <><Disclosure title="Host connectivity"><LanTlsSettings/><HostServiceSettings/></Disclosure><Disclosure title="People and permissions"><h2>People</h2>{(data.principals || []).filter(matches).map((principal:Row) => <Item key={principal.id} title={principal.display_name} meta={`${principal.role || 'No role'} · ${principal.enabled ? 'Enabled' : 'Disabled'}${principal.trusted_execution ? ' · Trusted host execution' : ''}`}>
        {action('Manage access',()=>{setSelected(principal.id);setDraft({role:principal.role,trusted:!!principal.trusted_execution})},true)}{action('Revoke all devices',()=>confirmDelete(`/auth/admin/principals/${principal.id}/sessions`,'Device sessions revoked'),true)}
      </Item>)}
      {selected && <div className="manager-form">{select('role','Host role',[['admin','Administrator'],['operator','Operator'],['viewer','Viewer']],'viewer')}<label><input type="checkbox" checked={Boolean(draft.trusted)} onChange={e=>change('trusted',e.target.checked)} />Allow trusted host execution (shared OS user)</label>{action('Save role',()=>post(`/auth/admin/principals/${selected}/role`,{role:draft.role || 'viewer',trusted_execution:Boolean(draft.trusted)},'Role updated','PUT'))}
        {input('issuer','Identity issuer')}{input('subject','Stable identity subject')}{action('Bind verified identity',()=>post(`/auth/admin/principals/${selected}/bindings`,{issuer:draft.issuer,subject:draft.subject},'Explicit identity mapping saved'))}
      </div>}
      <details><summary>Add a person</summary><div className="manager-form">{input('personName','Name or local username')}{input('personPassword','Local password (leave empty for SSO)',{type:'password'})}{select('newRole','Initial role',[['viewer','Viewer'],['operator','Operator'],['admin','Administrator']],'viewer')}{action('Create account',()=>post('/auth/admin/principals',{name:draft.personName,password:draft.personPassword || null,role:draft.newRole || 'viewer'},'Account created'))}</div></details>
      <Disclosure title="Groups and resource permissions"><IdentityGroupsManager projects={data.identityProjects||[]} onError={setIdentityError}/></Disclosure></Disclosure>
      <Disclosure title="Session policy"><SessionPolicyManager onError={setIdentityError}/></Disclosure>
      <Disclosure title="Sign-in methods"><h2>Sign-in methods</h2>{(data.adapters || []).map((adapter:Row)=><Item key={adapter.id} title={adapter.label} meta={`${adapter.enabled ? 'Enabled' : 'Disabled'} · ${adapter.health} · v${adapter.version}`}>
        {action('Check health',()=>post(`/auth/admin/adapters/${adapter.id}/health`,{},'Method health checked'),true)}{action(adapter.enabled ? 'Disable and revoke sessions' : 'Enable',()=>post(`/auth/admin/adapters/${adapter.id}`,{enabled:!adapter.enabled,session_policy:'revoke'},'Sign-in method updated','PUT'),true)}</Item>)}
      <details><summary>Configure enterprise identity</summary><div className="manager-form"><p className="manager-help">Advanced administrator setup. Keep issuer, signing trust and security consequences visible when testing a provider.</p>{textarea('authConfig','Advanced provider trust configuration',JSON.stringify(data.configuration?.configuration,null,2))}{input('testAdapter','Adapter ID to test')}{input('authEvidence','Signed assertion test evidence (masked)',{type:'password',placeholder:'{"assertion":"…"}'})}
        {action('Test login and policy',()=>mutate(()=>request('/auth/admin/adapters/configuration/test',body({configuration:jsonDraft('authConfig',data.configuration?.configuration),adapter_id:draft.testAdapter,evidence:jsonDraft('authEvidence',{})})),'Identity login and current policy verified',false),true)}
        {action('Test SSO login',()=>mutate(async()=>{const result=await request<Row>('/auth/admin/adapters/configuration/begin',body({configuration:jsonDraft('authConfig',data.configuration?.configuration),adapter_id:draft.testAdapter}));window.location.assign(result.authorization_url);return result},'SSO test started',false),true)}
        {select('sessionPolicy','Existing provider sessions',[['revoke','Revoke immediately'],['expire','Keep until expiry']],'revoke')}{action('Activate tested configuration',()=>post('/auth/admin/adapters/configuration',{configuration:jsonDraft('authConfig',data.configuration?.configuration),session_policy:draft.sessionPolicy || 'revoke'},'Tested identity configuration activated','PUT'))}<p className="manager-help">Every configured provider needs a current verified login and policy test. Identity bindings use stable issuer and subject, never email equality.</p></div></details></Disclosure>
      <Disclosure title="Audit and recovery"><AuditRetentionManager onError={setIdentityError}/><details><summary>Host recovery and migration</summary><div className="manager-form">{action('Generate one-use owner recovery code',()=>mutate(async()=>{const result=await request<Row>('/auth/admin/recovery-code',body({}));setPreview(result);return result},'Save this recovery credential privately; it is displayed once',false))}{preview?.recovery_code && <output className="manager-recovery">{preview.recovery_code}</output>}{action('End legacy device migration now',()=>post('/auth/admin/migration/end',{},'Legacy credentials no longer accepted'),true)}{action('Inspect access audit',()=>mutate(async()=>{setPreview(await request('/auth/admin/audit'));return null},'Audit loaded',false),true)}{preview && !preview.recovery_code && <Json value={preview} />}</div></details></Disclosure></>}
      </>}
      </>}
    </main>
  </section>;
}
export default Managers;
