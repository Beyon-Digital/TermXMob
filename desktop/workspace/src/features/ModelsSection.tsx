import {useState} from 'react';
import {engineAuthLabel} from '../lib/engine-status';
import {Button} from '../components/ui/button';
import {Dialog,DialogContent,DialogDescription,DialogTitle} from '../components/ui/dialog';
import {FormField} from '../components/ui/form-field';
import {Input} from '../components/ui/input';
import {providerAccount,type AgentProvider,type EngineAuthMethod,type EngineDescriptor,type HostIdentity,type ProviderAccountInput} from '../lib/host-types';
import {EDITABLE_KINDS,isAnthropic,providerKindLabel} from '../lib/provider-kinds';
import ProviderAccountForm from './ProviderAccountForm';
import ChatGPTSignIn from './ChatGPTSignIn';
import ModelFavoritesManager from './ModelFavoritesManager';
import ModelRoutingForm,{type Routing} from './ModelRoutingForm';
import {ActionButton,Item,Json,ManagerPage,rowMatcher} from './manager-ui';
import {gql,useManagerLoad,useManagerMutation} from './use-manager';

interface ModelsData { engines?: EngineDescriptor[]; agent_providers?: AgentProvider[]; acp_registry?: unknown; model_routing?: Routing }
interface ProviderTestReply { status?: string; ok?: boolean; message?: string }
const providerFields='id name kind model models base_url secret_configured key_saved key_optional key_source capabilities model_catalog model_overrides catalog_refreshed_at catalog_error wire limits';
const loadModels=()=>gql<ModelsData>(`{ engines { id label installed version auth_state auth_detail protocol error capabilities } agent_providers { ${providerFields} } acp_registry model_routing }`);
const saveProvider='mutation($input:AgentProviderInput!){save_agent_provider(input:$input){id notice}}';

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

function InstallEngineForm({busy,registry,onRefreshRegistry,onInstall}:{busy:boolean;registry:unknown;onRefreshRegistry:()=>unknown;onInstall:(id:string,allowUnverified:boolean)=>Promise<unknown>}){
  // Installing runs a host action; the typed package id is not a settings draft, so the surface neither
  // marks it unsaved nor submits it from the footer Save.
  const [registryId,setRegistryId]=useState(''),[allowUnverified,setAllowUnverified]=useState(false);
  return <form data-settings-filter className="manager-form" aria-label="Install a compatible engine" onSubmit={event=>{event.preventDefault();void onInstall(registryId,allowUnverified).then(result=>{if(result!==null){setRegistryId('');setAllowUnverified(false)}})}}>
    <FormField label="Registry package ID">{control=><Input {...control} name="registry_id" value={registryId} onChange={event=>setRegistryId(event.target.value)}/>}</FormField>
    <label className="provider-keyless"><input type="checkbox" name="allow_unverified" checked={allowUnverified} disabled={busy} onChange={event=>setAllowUnverified(event.target.checked)}/> <span>Allow a download that has no published checksum</span></label>
    <p className="manager-help">Downloads are checked against the registry's SHA-256 when it publishes one. Without one TermX cannot verify the file, so it refuses unless you allow it here. Package installs never run the package's own install scripts.</p>
    <ActionButton busy={busy} type="button" data-keeps-draft onClick={()=>void onRefreshRegistry()}>Refresh registry</ActionButton>
    <ActionButton busy={busy} primary type="submit">Install engine</ActionButton>
    <Json value={registry}/>
  </form>;
}

export default function ModelsSection({admin,me,projectId,onCapabilitiesChanged}:{admin:boolean;me:HostIdentity;projectId:string|null;onCapabilitiesChanged?:()=>void}){
  const {data,loading,error:loadError,reload}=useManagerLoad(loadModels);
  const {busy,error,notice,mutate,setNotice,report}=useManagerMutation(reload,onCapabilitiesChanged);
  const [search,setSearch]=useState(''),[preview,setPreview]=useState<unknown>(null);
  const [editing,setEditing]=useState<AgentProvider|null>(null),[testing,setTesting]=useState<AgentProvider|null>(null);
  const providers=data?.agent_providers||[],engines=data?.engines||[];
  const matches=rowMatcher(search,me.session_id);
  const saveAccount=(message:string)=>(input:ProviderAccountInput)=>mutate(async()=>{
    const saved=await gql<{save_agent_provider?:{id:string;notice?:string|null}}>(saveProvider,{input:{...input}});
    // Saving never contacted the provider, so a typo'd key looked saved. When a key was just entered, load the model
    // list now: a wrong key or address fails here, with a reason, while the form is still open to correct.
    const id=saved.save_agent_provider?.id;
    if(id&&input.api_key){
      const listed=await gql<{refresh_provider_models?:{catalog_error?:string|null}}>('mutation($id:String!){refresh_provider_models(provider_id:$id){id catalog_error}}',{id});
      const failure=listed.refresh_provider_models?.catalog_error;
      if(failure)throw new Error(`Saved, but the model list could not be loaded: ${failure}. Check the API key and the server address.`);
    }
    return saved;
  },message).then(result=>{
    // The host says so when a save changed something that was not asked for, such as dropping the key with the server address.
    const note=(result as {save_agent_provider?:{notice?:string|null}}|null)?.save_agent_provider?.notice;
    if(note)setNotice(`${message}. ${note}`);
    return result;
  });
  const refreshModels=(provider:AgentProvider)=>mutate(async()=>{
    const result=await gql<{refresh_provider_models?:{catalog_error?:string|null}}>('mutation($id:String!){refresh_provider_models(provider_id:$id){id model catalog_error}}',{id:provider.id});
    // The host stores a failed discovery on the account instead of raising, so the mutation itself still succeeds.
    const failure=result.refresh_provider_models?.catalog_error;
    if(failure)throw new Error(`Could not refresh the model list: ${failure}. Check the API key and the server address, then try again.`);
    return result;
  },'Model catalog refreshed');
  const hasCredential=providers.some(provider=>provider.secret_configured);
  const signOut=(provider:AgentProvider)=>mutate(()=>gql<{sign_out_chatgpt?:{message?:string}}>('mutation($id:String!){sign_out_chatgpt(account_id:$id)}',{id:provider.id}),'Signed out of ChatGPT.').then(result=>{
    // The host says whether ChatGPT confirmed the revocation; a local-only sign-out must not read as a clean one.
    const message=result?.sign_out_chatgpt?.message;if(message)setNotice(message);return result;
  });
  return <ManagerPage section="models" projectId={projectId} load={{data,loading,busy,error:error||loadError,notice,reload}} search={{label:"Models & engines",value:search,onChange:setSearch,count:providers.length+engines.length}}>
    <>
      <ChatGPTSignIn busy={busy} onStart={run=>mutate(run,'ChatGPT sign-in started. Finish it in the browser on the host.',{refresh:false})} onFinished={({ok,message})=>{if(ok){setNotice(message);reload()}else report(new Error(message))}}>{(startSignIn,signingIn)=><>
      <h3>Provider accounts</h3>
      {providers.filter(matches).map(provider=><Item key={provider.id} title={provider.name||provider.id} meta={`${providerKindLabel(provider.kind)} · ${provider.kind==='chatgpt'?(provider.secret_configured?'Signed in':'Signed out'):provider.key_optional&&!provider.key_saved?'No key needed':provider.secret_configured?'Credential configured':'Credential required'} · ${provider.model||'All discovered models'}`}>
        {provider.kind!=='chatgpt'||provider.secret_configured?<ActionButton busy={busy} onClick={()=>setTesting(provider)}>Test connection</ActionButton>:null}
        {admin&&provider.kind==='chatgpt'&&(provider.secret_configured
          ?<ActionButton busy={busy} onClick={()=>void signOut(provider)}>Sign out</ActionButton>
          :<ActionButton busy={busy||signingIn} onClick={()=>startSignIn(provider.id)}>Sign in again</ActionButton>)}
        {admin&&EDITABLE_KINDS.includes(provider.kind)&&<ActionButton busy={busy} onClick={()=>setEditing(provider)}>Edit account</ActionButton>}
        {admin&&provider.kind!=='chatgpt'&&<ActionButton busy={busy} onClick={()=>void mutate(()=>gql('mutation($id:String!){delete_agent_provider(provider_id:$id){ok}}',{id:provider.id}),'Provider removed')}>Remove account</ActionButton>}
        {provider.catalog_error&&<p className="manager-error" role="status">Model list failed: {provider.catalog_error}</p>}
      </Item>)}
      {admin&&editing&&<ProviderAccountForm key={editing.id} account={providerAccount(providers.find(row=>row.id===editing.id)||editing)} busy={busy} onCancel={()=>setEditing(null)} onRefresh={()=>refreshModels(editing)} onSave={async input=>{const result=await saveAccount('Provider account updated')(input);if(result!==null)setEditing(null);return result}}/>}
      {admin&&<details key={hasCredential?'has-account':'first-account'} open={!hasCredential}><summary>Add provider account</summary><ProviderAccountForm busy={busy} onSave={saveAccount('Provider account saved')}/></details>}
      {admin&&<div className="manager-item-actions"><ActionButton busy={busy||signingIn} onClick={()=>startSignIn(null)}>Sign in with ChatGPT</ActionButton><small className="manager-help">Use a ChatGPT plan instead of an API key. Sign-in opens in a browser on the host machine.</small></div>}
      </>}</ChatGPTSignIn>
      <ModelRoutingForm providers={providers} routing={data?.model_routing||{}} disabled={!admin} onSave={routes=>mutate(()=>gql('mutation($input:ModelRoutingInput!){set_model_routing(input:$input)}',{input:{routing:routes}}),'Model routing saved')}/>
      <h3>Installed engines</h3>
      {engines.filter(matches).map(engine=><Item key={engine.id} title={engine.label} meta={`${engine.installed?'Installed':'Needs installation'} · ${engineAuthLabel(engine.auth_state)}${engine.version?` · ${engine.version}`:''}`}>
        {engine.error&&<p className="manager-error" role="status">{engine.error}</p>}
        {engine.auth_detail&&!engine.error&&<p className="manager-help">{engine.auth_detail}</p>}
        <ActionButton busy={busy} onClick={()=>setPreview(engine)}>Inspect capabilities</ActionButton>
        {engine.id==='internal'
          ?<small className="manager-help engine-models-hint" id="internal-engine-models-hint">The TermX agent has no model list of its own. Its models come from your provider accounts. Open Edit account on a provider account and use Refresh models there.</small>
          :<ActionButton busy={busy} onClick={()=>void mutate(async()=>{await gql('mutation($id:String!){refresh_engine_catalogue(engine_id:$id)}',{id:engine.id});const value=await gql('query($id:String!){engine_models(engine_id:$id)}',{id:engine.id});setPreview(value);return value},'Engine checked and model catalog refreshed')}>Refresh models</ActionButton>}
        <EngineAuthentication engine={engine} busy={busy} canAuthenticate={(me.principal?.scopes||[]).includes('agent-run')} onAuthenticate={method=>mutate(async()=>{const result=await gql<{authenticate_engine?:{ok?:boolean;message?:string}}>('mutation($id:String!,$method:String!){authenticate_engine(engine_id:$id,method_id:$method)}',{id:engine.id,method});if(result.authenticate_engine?.ok!==true)throw new Error(result.authenticate_engine?.message||'The engine did not confirm sign-in.');return result},'Engine sign-in completed')}/>
      </Item>)}
      {Boolean(preview)&&<details open><summary>Selected engine capabilities/catalog</summary><Json value={preview}/></details>}
      {admin&&<details><summary>Install a compatible engine</summary><InstallEngineForm busy={busy} registry={data?.acp_registry} onRefreshRegistry={()=>mutate(()=>gql('mutation{refresh_acp_registry}'),'Registry refreshed')} onInstall={(id,allow)=>mutate(async()=>{
          const reply=await gql<{install_acp_runner?:{installed?:boolean;error?:string|null}}>('mutation($id:String!,$allow:Boolean!){install_acp_runner(registry_id:$id,allow_unverified:$allow)}',{id,allow});
          // Discovery runs after the engine is registered, so a broken install still succeeds as a request; say so.
          const result=reply.install_acp_runner;
          if(result?.error||result?.installed===false)throw new Error(`The engine was added, but TermX could not start it${result.error?`: ${result.error}`:'.'} Check that its runtime is installed on the host, then use Refresh models on it.`);
          return reply;
        },'Engine installed')}/></details>}
      {me.principal?.id&&<details><summary>Model favorites</summary><ModelFavoritesManager key={me.principal.id+':'+engines.map(engine=>engine.id+engine.installed).join()} ownerId={me.principal.id} engines={engines} accounts={providers} search={search}/></details>}
      <p className="manager-help">Choose an engine or model in each chat composer. Account changes do not retarget running sessions. API-key providers and subscription engines use separate billing.</p>
    </>
    <Dialog open={!!testing} onOpenChange={open=>{if(!open)setTesting(null)}}>{testing&&<DialogContent className="provider-test-dialog"><DialogTitle>Test {testing.name||testing.id}?</DialogTitle><DialogDescription>{isAnthropic(testing.kind)?'This asks Anthropic to list the models your key can use. No model runs, so nothing is billed.':'This sends a request to your provider to check the connection. Provider charges may apply.'}</DialogDescription><dl><dt>Destination</dt><dd>{testing.base_url||'Configured provider endpoint'}</dd><dt>Model</dt><dd>{(isAnthropic(testing.kind)?testing.model?.split(/[\n,]/)[0]:testing.model)||'All discovered models'}</dd><dt>{isAnthropic(testing.kind)?'Request':'Test message'}</dt><dd>{isAnthropic(testing.kind)?'List models':'Reply with only OK.'}</dd></dl><div className="manager-item-actions"><Button variant="secondary" onClick={()=>setTesting(null)}>Cancel test</Button><Button disabled={busy} onClick={()=>{const provider=testing;setTesting(null);void mutate(async()=>{const result=await gql<{test_agent_provider?:ProviderTestReply}>('mutation($id:String!){test_agent_provider(provider_id:$id)}',{id:provider.id});const response=result.test_agent_provider;if(response?.status!=='connected'&&response?.ok!==true)throw new Error(response?.message||'The provider did not confirm the test request.');return result},'Provider connection tested',{refresh:false})}}>Send test request</Button></div></DialogContent>}</Dialog>
  </ManagerPage>;
}
