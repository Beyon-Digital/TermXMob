import {useState} from 'react';
import {FormField} from '../components/ui/form-field';
import {Input} from '../components/ui/input';
import {Select} from '../components/ui/select';
import {Textarea} from '../components/ui/textarea';
import {json,request} from '../lib/api';
import type {AccessSummary,AdapterConfiguration,AdminPrincipal,AuthAdapter,AuthSession,HostIdentity} from '../lib/host-types';
import LanTlsSettings from './LanTlsSettings';
import HostServiceSettings from './HostServiceSettings';
import {ManagedPairingForm} from './ManagedPairingForm';
import IdentityGroupsManager from './IdentityGroupsManager';
import AuditRetentionManager from './AuditRetentionManager';
import SessionPolicyManager from './SessionPolicyManager';
import {ActionButton,Disclosure,Item,Json,ManagerPage,date,rowMatcher} from './manager-ui';
import {gql,useManagerLoad,useManagerMutation,type Mutation} from './use-manager';
import {useDirtyForm} from './settings-forms';

interface AccessData { access: AccessSummary; sessions: AuthSession[]; methods: unknown; principals?: AdminPrincipal[]; adapters?: AuthAdapter[]; configuration?: AdapterConfiguration; identityProjects?: {id:string;name:string}[] }
interface AuthTestReply { authorization_url: string }
interface RecoveryReply { recovery_code?: string }
const operateScopes=['files-read','files-write','git-read','git-write','agent-view','agent-control','agent-run','terminal-view','terminal-control'];
const viewScopes=['files-read','git-read','agent-view','terminal-view'];

async function loadAccess(admin:boolean):Promise<AccessData>{
  const [access,sessions,methods]=await Promise.all([request<AccessSummary>('/auth/access'),request<AuthSession[]>('/auth/sessions'),request<unknown>('/auth/methods')]);
  const result:AccessData={access,sessions,methods};
  if(admin){
    const [principals,adapters,configuration]=await Promise.all([request<AdminPrincipal[]>('/auth/admin/principals'),request<AuthAdapter[]>('/auth/admin/adapters'),request<AdapterConfiguration>('/auth/admin/adapters/configuration')]);
    const catalog=await gql<{projects:{id:string;name:string}[]}>('{projects{id name}}');
    Object.assign(result,{principals,adapters,configuration,identityProjects:catalog.projects});
  }
  return result;
}

function RoleSelect({label,value,onChange,children}:{label:string;value:string;onChange:(value:string)=>void;children:React.ReactNode}){
  return <FormField label={label}>{control=><Select {...control} value={value} onChange={event=>onChange(event.target.value)}>{children}</Select>}</FormField>;
}

function PrincipalAccess({principal,projectId,mutation}:{principal:AdminPrincipal;projectId:string|null;mutation:Mutation}){
  const roleForm=useDirtyForm('principal-role'),bindForm=useDirtyForm('principal-binding');
  const [role,setRole]=useState(principal.role||'viewer'),[trusted,setTrusted]=useState(!!principal.trusted_execution),[projectRole,setProjectRole]=useState('view');
  const [issuer,setIssuer]=useState(''),[subject,setSubject]=useState('');
  const {busy,post,remove}=mutation;
  return <>
    <form {...roleForm.formProps} className="manager-form" aria-label={`Access for ${principal.display_name}`} onSubmit={event=>{event.preventDefault();void post(`/auth/admin/principals/${principal.id}/role`,{role,trusted_execution:trusted},'Role updated','PUT').then(result=>{if(result!==null)roleForm.markClean()})}}>
      <RoleSelect label="Host role" value={role} onChange={setRole}><option value="admin">Administrator</option><option value="operator">Operator</option><option value="viewer">Viewer</option></RoleSelect>
      <label><input type="checkbox" name="trusted_execution" checked={trusted} onChange={event=>setTrusted(event.target.checked)}/>Allow trusted host execution (shared OS user)</label>
      <ActionButton busy={busy} primary type="submit">Save role</ActionButton>
      {projectId&&<>
        <RoleSelect label="This project" value={projectRole} onChange={setProjectRole}><option value="view">Read files and chats</option><option value="operate">Read, edit and run</option></RoleSelect>
        <ActionButton busy={busy} type="button" onClick={()=>void post(`/auth/admin/principals/${principal.id}/projects/${projectId}`,{scopes:projectRole==='operate'?operateScopes:viewScopes},'Project access granted','PUT')}>Grant project access</ActionButton>
        <ActionButton busy={busy} type="button" onClick={()=>void remove(`/auth/admin/principals/${principal.id}/projects/${projectId}`,'Project access revoked')}>Revoke project access</ActionButton>
      </>}
    </form>
    <form {...bindForm.formProps} className="manager-form" aria-label={`Identity binding for ${principal.display_name}`} onSubmit={event=>{event.preventDefault();void post(`/auth/admin/principals/${principal.id}/bindings`,{issuer,subject},'Explicit identity mapping saved').then(result=>{if(result!==null){setIssuer('');setSubject('');bindForm.markClean()}})}}>
      <FormField label="Identity issuer">{control=><Input {...control} name="issuer" value={issuer} onChange={event=>setIssuer(event.target.value)}/>}</FormField>
      <FormField label="Stable identity subject">{control=><Input {...control} name="subject" value={subject} onChange={event=>setSubject(event.target.value)}/>}</FormField>
      <ActionButton busy={busy} primary type="submit">Bind verified identity</ActionButton>
    </form>
  </>;
}

function AddPersonForm({mutation}:{mutation:Mutation}){
  const {formProps,markClean}=useDirtyForm('add-person');
  const [name,setName]=useState(''),[password,setPassword]=useState(''),[role,setRole]=useState('viewer');
  const {busy,post}=mutation;
  return <form {...formProps} className="manager-form" aria-label="Add a person" onSubmit={event=>{event.preventDefault();void post('/auth/admin/principals',{name,password:password||null,role},'Account created').then(result=>{if(result!==null){setName('');setPassword('');setRole('viewer');markClean()}})}}>
    <FormField label="Name or local username" required>{control=><Input {...control} name="name" value={name} onChange={event=>setName(event.target.value)}/>}</FormField>
    <FormField label="Local password" hint="Leave empty for SSO.">{control=><Input {...control} name="password" type="password" autoComplete="new-password" value={password} onChange={event=>setPassword(event.target.value)}/>}</FormField>
    <RoleSelect label="Initial role" value={role} onChange={setRole}><option value="viewer">Viewer</option><option value="operator">Operator</option><option value="admin">Administrator</option></RoleSelect>
    <ActionButton busy={busy} primary type="submit">Create account</ActionButton>
  </form>;
}

function EnterpriseIdentityForm({configuration,mutation}:{configuration:unknown;mutation:Mutation}){
  const {formProps,markClean}=useDirtyForm('enterprise-identity');
  const [config,setConfig]=useState(''),[adapter,setAdapter]=useState(''),[evidence,setEvidence]=useState(''),[policy,setPolicy]=useState('revoke');
  const {busy,mutate}=mutation;
  const parsedConfig=()=>JSON.parse(config||JSON.stringify(configuration)),parsedEvidence=()=>JSON.parse(evidence||'{}');
  return <form {...formProps} className="manager-form" aria-label="Configure enterprise identity" onSubmit={event=>{event.preventDefault();void mutate(()=>request('/auth/admin/adapters/configuration',json('PUT',{configuration:parsedConfig(),session_policy:policy})),'Tested identity configuration activated').then(result=>{if(result!==null)markClean()})}}>
    <p className="manager-help">Advanced administrator setup. Keep issuer, signing trust and security consequences visible when testing a provider.</p>
    <FormField label="Advanced provider trust configuration">{control=><Textarea {...control} name="configuration" value={config} placeholder={JSON.stringify(configuration,null,2)} onChange={event=>setConfig(event.target.value)}/>}</FormField>
    <FormField label="Adapter ID to test">{control=><Input {...control} name="adapter_id" value={adapter} onChange={event=>setAdapter(event.target.value)}/>}</FormField>
    <FormField label="Signed assertion test evidence (masked)">{control=><Input {...control} name="evidence" type="password" autoComplete="off" placeholder={'{"assertion":"…"}'} value={evidence} onChange={event=>setEvidence(event.target.value)}/>}</FormField>
    <ActionButton busy={busy} type="button" onClick={()=>void mutate(()=>request('/auth/admin/adapters/configuration/test',json('POST',{configuration:parsedConfig(),adapter_id:adapter,evidence:parsedEvidence()})),'Identity login and current policy verified',{refresh:false})}>Test login and policy</ActionButton>
    <ActionButton busy={busy} type="button" onClick={()=>void mutate(async()=>{const result=await request<AuthTestReply>('/auth/admin/adapters/configuration/begin',json('POST',{configuration:parsedConfig(),adapter_id:adapter}));window.location.assign(result.authorization_url);return result},'SSO test started',{refresh:false})}>Test SSO login</ActionButton>
    <RoleSelect label="Existing provider sessions" value={policy} onChange={setPolicy}><option value="revoke">Revoke immediately</option><option value="expire">Keep until expiry</option></RoleSelect>
    <ActionButton busy={busy} primary type="submit">Activate tested configuration</ActionButton>
    <p className="manager-help">Every configured provider needs a current verified login and policy test. Identity bindings use stable issuer and subject, never email equality.</p>
  </form>;
}

export default function AccessSection({admin,me,projectId}:{admin:boolean;me:HostIdentity;projectId:string|null}){
  const {data,loading,error:loadError,reload}=useManagerLoad(()=>loadAccess(admin));
  const mutation=useManagerMutation(reload);
  const {busy,error,notice,mutate,post,remove,report}=mutation;
  const [search,setSearch]=useState(''),[selected,setSelected]=useState<AdminPrincipal|null>(null),[preview,setPreview]=useState<unknown>(null);
  const matches=rowMatcher(search,me.session_id);
  const recoveryCode=typeof preview==='object'&&preview!==null&&'recovery_code' in preview&&typeof preview.recovery_code==='string'?preview.recovery_code:undefined;
  const count=(data?.sessions?.length||0)+(data?.principals?.length||0)+(data?.adapters?.length||0);
  return <ManagerPage section="access" projectId={projectId} load={{data,loading,busy,error:error||loadError,notice,reload}} search={{label:"Access & sessions",value:search,onChange:setSearch,count:count}}>
    <>
      {admin&&<Disclosure title="Host connectivity"><LanTlsSettings/><HostServiceSettings/></Disclosure>}
      {me.principal?.id&&me.session_id&&me.host_id&&<Disclosure title="Pair a device"><ManagedPairingForm actor={me as {principal:{id:string;display_name:string;scopes:string[]};session_id:string;host_id:string}}/></Disclosure>}
      <h3>Your sessions</h3>
      <p className="manager-help">{data?.access?.role||'Member'} · {data?.access?.runtime_boundary||'Host policy'} · Managed application permissions apply to each resource. Trusted host execution shares the host OS user.</p>
      {(data?.sessions||[]).filter(matches).map(session=><Item key={session.id} title={session.device_name||'Unnamed device'} meta={`${session.strength} · expires ${date(session.expires)}${session.revoked?' · Revoked':session.id===me.session_id?' · This session':''}`}>
        {!session.revoked&&<ActionButton busy={busy} onClick={()=>void remove(`/auth/sessions/${session.id}`,'Session revoked')}>Revoke session</ActionButton>}
      </Item>)}
      {admin&&<>
        <h3>People and project access</h3>
        {(data?.principals||[]).filter(matches).map(principal=><Item key={principal.id} title={principal.display_name} meta={`${principal.role||'No role'} · ${principal.enabled?'Enabled':'Disabled'}${principal.trusted_execution?' · Trusted host execution':''}`}>
          <ActionButton busy={busy} onClick={()=>setSelected(principal)}>Manage access</ActionButton>
          <ActionButton busy={busy} onClick={()=>void remove(`/auth/admin/principals/${principal.id}/sessions`,'Device sessions revoked')}>Revoke all devices</ActionButton>
        </Item>)}
        {selected&&<PrincipalAccess key={selected.id} principal={selected} projectId={projectId} mutation={mutation}/>}
        <details><summary>Add a person</summary><AddPersonForm mutation={mutation}/></details>
        <Disclosure title="Groups and resource permissions"><IdentityGroupsManager projects={data?.identityProjects||[]} onError={report}/></Disclosure>
        <Disclosure title="Audit retention"><AuditRetentionManager onError={report}/></Disclosure>
        <Disclosure title="Session policy"><SessionPolicyManager onError={report}/></Disclosure>
        <h3>Sign-in methods</h3>
        {(data?.adapters||[]).map(adapter=><Item key={adapter.id} title={adapter.label} meta={`${adapter.enabled?'Enabled':'Disabled'} · ${adapter.health} · v${adapter.version}`}>
          <ActionButton busy={busy} onClick={()=>void post(`/auth/admin/adapters/${adapter.id}/health`,{},'Method health checked')}>Check health</ActionButton>
          <ActionButton busy={busy} onClick={()=>void post(`/auth/admin/adapters/${adapter.id}`,{enabled:!adapter.enabled,session_policy:'revoke'},'Sign-in method updated','PUT')}>{adapter.enabled?'Disable and revoke sessions':'Enable'}</ActionButton>
        </Item>)}
        <details><summary>Configure enterprise identity</summary><EnterpriseIdentityForm configuration={data?.configuration?.configuration} mutation={mutation}/></details>
        <details><summary>Host recovery and migration</summary><div className="manager-form">
          <ActionButton busy={busy} primary onClick={()=>void mutate(async()=>{const result=await request<RecoveryReply>('/auth/admin/recovery-code',json('POST',{}));setPreview(result);return result},'Save this recovery credential privately; it is displayed once',{refresh:false})}>Generate one-use owner recovery code</ActionButton>
          {recoveryCode&&<output className="manager-recovery">{recoveryCode}</output>}
          <ActionButton busy={busy} onClick={()=>void post('/auth/admin/migration/end',{},'Legacy credentials no longer accepted')}>End legacy device migration now</ActionButton>
          <ActionButton busy={busy} onClick={()=>void mutate(async()=>{setPreview(await request('/auth/admin/audit'));return null},'Audit loaded',{refresh:false})}>Inspect access audit</ActionButton>
          {Boolean(preview)&&!recoveryCode&&<Json value={preview}/>}
        </div></details>
      </>}
    </>
  </ManagerPage>;
}
