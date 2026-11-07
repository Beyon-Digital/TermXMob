import {useState} from 'react';
import {engineAuthLabel} from '../lib/engine-status';
import {Button} from '../components/ui/button';
import {Dialog,DialogContent,DialogDescription,DialogTitle} from '../components/ui/dialog';
import {FormField} from '../components/ui/form-field';
import {Input} from '../components/ui/input';
import {providerAccount,type AgentProvider,type EngineAuthMethod,type EngineDescriptor,type HostIdentity,type ProviderAccountInput} from '../lib/host-types';
import ProviderAccountForm from './ProviderAccountForm';
import ModelFavoritesManager from './ModelFavoritesManager';
import {ActionButton,Item,Json,ManagerPage,rowMatcher} from './manager-ui';
import {gql,useManagerLoad,useManagerMutation} from './use-manager';

interface ModelsData { engines?: EngineDescriptor[]; agent_providers?: AgentProvider[]; acp_registry?: unknown }
interface ProviderTestReply { status?: string; ok?: boolean; message?: string }
const providerFields='id name kind model base_url secret_configured capabilities model_catalog model_overrides catalog_refreshed_at catalog_error';
const loadModels=()=>gql<ModelsData>(`{ engines { id label installed version auth_state auth_detail protocol error capabilities } agent_providers { ${providerFields} } acp_registry }`);
const saveProvider='mutation($input:AgentProviderInput!){save_agent_provider(input:$input){id}}';

function EngineAuthentication({engine,busy,canAuthenticate,onAuthenticate}:{engine:EngineDescriptor;busy:boolean;canAuthenticate:boolean;onAuthenticate:(methodId:string)=>unknown}){
  if(!engine.installed)return null;
  // Only ACP currently exposes authenticate_engine. Other adapters may report
  // authentication state, but that does not make a sign-in RPC available.
  const advertised:unknown[]=engine.protocol?.name==='acp'&&Array.isArray(engine.protocol.auth_methods)?engine.protocol.auth_methods:[];
  const seen=new Set<string>();
  const methods=advertised.filter((method):method is EngineAuthMethod=>{const candidate=method as Partial<EngineAuthMethod>|null;if(!candidate||typeof candidate.id!=='string'||!candidate.id.trim()||seen.has(candidate.id))return false;seen.add(candidate.id);return true});
  if(methods.length){
    if(!canAuthenticate)return <p className="manager-help">Ask the host owner to connect this engine.</p>;
    return <div className="manager-item-actions" role="group" aria-label={`Sign-in methods for ${engine.label}`}>{methods.map(method=>{
      const label=[method.name,method.label].find(value=>typeof value==='string'&&value.trim())||'Connect account';
      return <Button key={method.id} variant="secondary" disabled={busy} onClick={()=>onAuthenticate(method.id)}>{label}</Button>;
    })}</div>;
  }
  if(['authenticated','not_required'].includes(engine.auth_state??''))return null;
  const native=engine.protocol?.name==='codex-app-server'?'Codex CLI':engine.protocol?.name==='claude-agent-sdk'?'Claude CLI':null;
  return <p className="manager-help">{native?`Manage sign-in in the ${native} on this host, then refresh to check the account.`:'This engine has no sign-in method available in TermX. Manage its account on this host, then refresh.'}</p>;
}

function InstallEngineForm({busy,registry,onRefreshRegistry,onInstall}:{busy:boolean;registry:unknown;onRefreshRegistry:()=>unknown;onInstall:(id:string)=>Promise<unknown>}){
  // Installing runs a host action; the typed package id is not a settings draft, so the surface neither
  // marks it unsaved nor submits it from the footer Save.
  const [registryId,setRegistryId]=useState('');
  return <form data-settings-filter className="manager-form" aria-label="Install a compatible engine" onSubmit={event=>{event.preventDefault();void onInstall(registryId).then(result=>{if(result!==null)setRegistryId('')})}}>
    <FormField label="Registry package ID">{control=><Input {...control} name="registry_id" value={registryId} onChange={event=>setRegistryId(event.target.value)}/>}</FormField>
    <ActionButton busy={busy} type="button" data-keeps-draft onClick={()=>void onRefreshRegistry()}>Refresh registry</ActionButton>
    <ActionButton busy={busy} primary type="submit">Install engine</ActionButton>
    <Json value={registry}/>
  </form>;
}

export default function ModelsSection({admin,me,projectId,onCapabilitiesChanged}:{admin:boolean;me:HostIdentity;projectId:string|null;onCapabilitiesChanged?:()=>void}){
  const {data,loading,error:loadError,reload}=useManagerLoad(loadModels);
  const {busy,error,notice,mutate}=useManagerMutation(reload,onCapabilitiesChanged);
  const [search,setSearch]=useState(''),[preview,setPreview]=useState<unknown>(null);
  const [editing,setEditing]=useState<AgentProvider|null>(null),[testing,setTesting]=useState<AgentProvider|null>(null);
  const providers=data?.agent_providers||[],engines=data?.engines||[];
  const matches=rowMatcher(search,me.session_id);
  const saveAccount=(message:string)=>(input:ProviderAccountInput)=>mutate(()=>gql(saveProvider,{input:{...input}}),message);
  const refreshModels=(provider:AgentProvider)=>mutate(()=>gql('mutation($id:String!){refresh_provider_models(provider_id:$id)}',{id:provider.id}),'Model catalog refreshed');
  const hasCredential=providers.some(provider=>provider.secret_configured);
  return <ManagerPage section="models" projectId={projectId} load={{data,loading,busy,error:error||loadError,notice,reload}} search={{label:"Models & engines",value:search,onChange:setSearch,count:providers.length+engines.length}}>
    <>
      <h3>Provider accounts</h3>
      {providers.filter(matches).map(provider=><Item key={provider.id} title={provider.name||provider.id} meta={`${provider.kind} · ${provider.secret_configured?'Credential configured':'Credential required'} · ${provider.model||'All discovered models'}`}>
        <ActionButton busy={busy} onClick={()=>setTesting(provider)}>Test connection</ActionButton>
        {admin&&['openai','openai-compatible'].includes(provider.kind)&&<ActionButton busy={busy} onClick={()=>setEditing(provider)}>Edit account</ActionButton>}
        {admin&&<ActionButton busy={busy} onClick={()=>void mutate(()=>gql('mutation($id:String!){delete_agent_provider(provider_id:$id){ok}}',{id:provider.id}),'Provider removed')}>Remove account</ActionButton>}
      </Item>)}
      {admin&&editing&&<ProviderAccountForm key={editing.id} account={providerAccount(providers.find(row=>row.id===editing.id)||editing)} busy={busy} onCancel={()=>setEditing(null)} onRefresh={()=>refreshModels(editing)} onSave={async input=>{const result=await saveAccount('Provider account updated')(input);if(result!==null)setEditing(null);return result}}/>}
      {admin&&<details key={hasCredential?'has-account':'first-account'} open={!hasCredential}><summary>Add provider account</summary><ProviderAccountForm busy={busy} onSave={saveAccount('Provider account saved')}/></details>}
      <h3>Installed engines</h3>
      {engines.filter(matches).map(engine=><Item key={engine.id} title={engine.label} meta={`${engine.installed?'Installed':'Needs installation'} · ${engineAuthLabel(engine.auth_state)}${engine.version?` · ${engine.version}`:''}`}>
        <ActionButton busy={busy} onClick={()=>setPreview(engine)}>Inspect capabilities</ActionButton>
        <ActionButton busy={busy} onClick={()=>void mutate(async()=>{const value=await gql('query($id:String!){engine_models(engine_id:$id)}',{id:engine.id});setPreview(value);return value},'Model catalog refreshed',{refresh:false})}>Refresh models</ActionButton>
        <EngineAuthentication engine={engine} busy={busy} canAuthenticate={(me.principal?.scopes||[]).includes('agent-run')} onAuthenticate={method=>mutate(async()=>{const result=await gql<{authenticate_engine?:{ok?:boolean;message?:string}}>('mutation($id:String!,$method:String!){authenticate_engine(engine_id:$id,method_id:$method)}',{id:engine.id,method});if(result.authenticate_engine?.ok!==true)throw new Error(result.authenticate_engine?.message||'The engine did not confirm sign-in.');return result},'Engine sign-in completed')}/>
      </Item>)}
      {Boolean(preview)&&<details open><summary>Selected engine capabilities/catalog</summary><Json value={preview}/></details>}
      {admin&&<details><summary>Install a compatible engine</summary><InstallEngineForm busy={busy} registry={data?.acp_registry} onRefreshRegistry={()=>mutate(()=>gql('mutation{refresh_acp_registry}'),'Registry refreshed')} onInstall={id=>mutate(()=>gql('mutation($id:String!){install_acp_runner(registry_id:$id)}',{id}),'Engine installed')}/></details>}
      {me.principal?.id&&<details><summary>Model favorites</summary><ModelFavoritesManager key={me.principal.id+':'+engines.map(engine=>engine.id+engine.installed).join()} ownerId={me.principal.id} engines={engines} accounts={providers} search={search}/></details>}
      <p className="manager-help">Choose an engine or model in each chat composer. Account changes do not retarget running sessions. API-key providers and subscription engines use separate billing.</p>
    </>
    <Dialog open={!!testing} onOpenChange={open=>{if(!open)setTesting(null)}}>{testing&&<DialogContent className="provider-test-dialog"><DialogTitle>Test {testing.name||testing.id}?</DialogTitle><DialogDescription>This sends a request to your provider to check the connection. Provider charges may apply.</DialogDescription><dl><dt>Destination</dt><dd>{testing.base_url||'Configured provider endpoint'}</dd><dt>Model</dt><dd>{testing.model||'All discovered models'}</dd><dt>Test message</dt><dd>Reply with only OK.</dd></dl><div className="manager-item-actions"><Button variant="secondary" onClick={()=>setTesting(null)}>Cancel test</Button><Button disabled={busy} onClick={()=>{const provider=testing;setTesting(null);void mutate(async()=>{const result=await gql<{test_agent_provider?:ProviderTestReply}>('mutation($id:String!){test_agent_provider(provider_id:$id)}',{id:provider.id});const response=result.test_agent_provider;if(response?.status!=='connected'&&response?.ok!==true)throw new Error(response?.message||'The provider did not confirm the test request.');return result},'Provider connection tested',{refresh:false})}}>Send test request</Button></div></DialogContent>}</Dialog>
  </ManagerPage>;
}
