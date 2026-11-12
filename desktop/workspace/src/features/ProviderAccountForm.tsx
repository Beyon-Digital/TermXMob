import {useRef,useState} from 'react';
import {Button} from '../components/ui/button';
import {Alert} from '../components/ui/alert';
import {FormField} from '../components/ui/form-field';
import {Input} from '../components/ui/input';
import {Select} from '../components/ui/select';
import {catalogAge} from '../lib/catalog-age';
import type {ModelOverride,ModelOverrides,ProviderAccount,ProviderAccountInput} from '../lib/host-types';
import ProviderModelCatalog from './ProviderModelCatalog';
import {useDirtyForm} from './settings-forms';

const choices=[['shell','Shell tools'],['functions','Function tools'],['image','Image generation and editing'],['video','Video generation'],['audio','Audio transcription and speech'],['realtime','Live voice conversation']];
const providers=[
 {id:'openai',label:'OpenAI',url:'https://api.openai.com/v1'},
 {id:'openrouter',label:'OpenRouter',url:'https://openrouter.ai/api/v1'},
 {id:'kie',label:'Kie.ai media adapter',url:'https://api.kie.ai'},
 {id:'custom',label:'Another compatible provider',url:''},
];
const providerFor=(url:string)=>providers.find(provider=>provider.url===url)?.id||'custom';
const modelList=(value:string)=>[...new Set(value.split(/[\n,]/).map(item=>item.trim()).filter(Boolean))];
const validUrl=(value:string)=>{try{return ['http:','https:'].includes(new URL(value).protocol)}catch{return false}};
type Errors={name?:string;base_url?:string;capabilities?:string;max_output_tokens?:string;request_timeout_s?:string};

export default function ProviderAccountForm({account,onSave,onRefresh,onCancel,busy}:{account?:ProviderAccount;onSave:(input:ProviderAccountInput)=>Promise<unknown>;onRefresh?:()=>Promise<unknown>;onCancel?:()=>void;busy:boolean}){
 const {formProps,markClean}=useDirtyForm('provider-account');
 const [id,setId]=useState(account?.id||''),[name,setName]=useState(account?.name||''),[url,setUrl]=useState(account?.base_url||providers[0].url),[secret,setSecret]=useState(''),[showSecret,setShowSecret]=useState(false),[capabilities,setCapabilities]=useState<string[]>(account?.capabilities||['shell','functions']);
 const [included,setIncluded]=useState(()=>modelList(account?.model||'')),[overrides,setOverrides]=useState<ModelOverrides>(account?.model_overrides||{});
 const [wire,setWire]=useState<'responses'|'chat'>(account?.wire==='chat'?'chat':'responses');
 const [maxOutput,setMaxOutput]=useState(String(account?.limits?.max_output_tokens??'')),[requestTimeout,setRequestTimeout]=useState(String(account?.limits?.request_timeout_s??''));
 const [provider,setProvider]=useState(providerFor(account?.base_url||providers[0].url)),[saving,setSaving]=useState(false),[error,setError]=useState(''),[errors,setErrors]=useState<Errors>({});
 const pending=useRef(false),generatedId=useRef('');
 const disabled=busy||saving;
 const catalog=account?.model_catalog||{};
 const clearError=(field:keyof Errors)=>setErrors(prior=>({...prior,[field]:undefined}));
 function chooseProvider(value:string){const selected=providers.find(row=>row.id===value)!;setProvider(value);setUrl(selected.url);setSecret('');if(value==='kie')setCapabilities(['image','video']);else if(value==='openrouter')setCapabilities(['shell','functions','image','video']);else if(provider==='kie')setCapabilities(['shell','functions']);clearError('base_url')}
 function include(model:string,checked:boolean){
  const known=[...new Set([...Object.keys(catalog),...included])],current=included.length?included:known;
  const next=checked?[...new Set([...current,model])]:current.filter(item=>item!==model);
  setIncluded(next.length===known.length?[]:next);
 }
 function override(model:string,field:keyof ModelOverride,value:number|undefined){
  setOverrides(prior=>{const row={...prior[model]};if(value===undefined||Number.isNaN(value))delete row[field];else row[field]=value;const next={...prior};if(Object.keys(row).length)next[model]=row;else delete next[model];return next});
 }
 function validate(){
  const found:Errors={};
  if(!name.trim())found.name='Enter a display name.';
  if(!url.trim())found.base_url='Enter the API address your provider supplied.';
  else if(!validUrl(url.trim()))found.base_url='Enter a full address that starts with https:// or http://.';
  // A stored 'computer' capability from an older account has no checkbox any more. It is kept on save (the host owns removing it) and does not count here.
  if(!capabilities.some(item=>choices.some(([value])=>value===item)))found.capabilities='Declare at least one capability.';
  if(maxOutput&&(!Number.isSafeInteger(Number(maxOutput))||Number(maxOutput)<=0))found.max_output_tokens='Enter a positive whole number or leave blank.';
  if(requestTimeout&&(!Number.isFinite(Number(requestTimeout))||Number(requestTimeout)<=0))found.request_timeout_s='Enter a positive number or leave blank.';
  setErrors(found);return !Object.keys(found).length;
 }
 async function save(){
  if(pending.current||!validate())return;
  pending.current=true;setSaving(true);setError('');
  if(!generatedId.current)generatedId.current=name.trim().toLowerCase().replace(/[^a-z0-9]+/g,'-').replace(/^-|-$/g,'')+'-'+crypto.randomUUID().slice(0,8);
  const input:ProviderAccountInput={id:id.trim()||generatedId.current,name:name.trim(),kind:account?.kind||'openai-compatible',base_url:url.trim(),model:included.join('\n'),capabilities,api_key:secret||null,wire,limits:{max_output_tokens:maxOutput?Number(maxOutput):null,request_timeout_s:requestTimeout?Number(requestTimeout):null}};
  if(Object.keys(overrides).length||Object.keys(account?.model_overrides||{}).length)input.model_meta=overrides;
  try{
   const result=await onSave(input);
   // A failed save returns null. Keep what was typed, including the key, so it can be retried.
   if(result===null){setError(secret?'The host did not accept the account, so nothing changed. What you entered, including the new API key, is still here. The reason is shown above.':'The host did not accept the account, so nothing changed. What you entered is still here. The reason is shown above.');return}
   setSecret('');setShowSecret(false);markClean();
   if(!account){generatedId.current='';setId('');setName('');setMaxOutput('');setRequestTimeout('')}
  }
  catch(reason){setError(reason instanceof Error?reason.message:'The account could not be saved. Review the settings and try again.')}
  finally{pending.current=false;setSaving(false)}
 }
 return <form {...formProps} noValidate className="manager-form provider-account-form" aria-label={account?'Edit provider account':'Add provider account'} aria-busy={disabled} onSubmit={event=>{event.preventDefault();void save()}}>
  {!account&&<><p className="manager-help">Connect an API account for chats or the visual media studio. Already use a signed-in coding agent? Choose it under Installed engines instead.</p>
   <FormField label="Provider">{control=><Select {...control} name="provider" value={provider} disabled={disabled} onChange={event=>chooseProvider(event.target.value)}>{providers.map(row=><option key={row.id} value={row.id}>{row.label}</option>)}</Select>}</FormField></>}
  {provider==='openrouter'&&<p className="manager-help">The media studio discovers image and video models through OpenRouter. Declare the media capabilities you want available below; generation still requires your consent.</p>}
  {provider==='kie'&&<p className="manager-help">Use this media adapter for supported studio operations such as portrait lip sync. This account does not provide chat completions.</p>}
  <FormField label="Display name" error={errors.name}>{control=><Input {...control} name="name" aria-required="true" value={name} placeholder={providers.find(row=>row.id===provider)?.label} disabled={disabled} onChange={event=>{setName(event.target.value);clearError('name')}}/>}</FormField>
  {(provider==='custom'||account)&&<FormField label="Provider API address" error={errors.base_url}>{control=><Input {...control} name="base_url" inputMode="url" aria-required="true" value={url} placeholder="https://provider.example/v1" disabled={disabled} onChange={event=>{setUrl(event.target.value);clearError('base_url')}}/>}</FormField>}
  <FormField label={account?'Replace API key':'API key'} hint={account?'Leave blank to keep the current key.':undefined}>{control=><span className="secret-input"><Input {...control} name="api_key" type={showSecret?'text':'password'} autoComplete="new-password" value={secret} disabled={disabled} onChange={event=>setSecret(event.target.value)}/><Button type="button" variant="ghost" size="sm" onClick={()=>setShowSecret(value=>!value)}>{showSecret?'Hide key':'Show key'}</Button></span>}</FormField>
  <p className="manager-help provider-destination">{url?<>Requests use <strong>{url}</strong>. Your host keeps the key; it is never added to chat history.</>:'Enter the API address supplied by your provider.'}</p>
  <section className="provider-models" aria-labelledby={`${account?.id||'new'}-models`}>
   <div className="provider-models-header"><h4 id={`${account?.id||'new'}-models`}>Models</h4>{account&&onRefresh&&<Button type="button" variant="secondary" data-keeps-draft disabled={disabled} onClick={()=>void onRefresh()}>Refresh models</Button>}</div>
   {account?<>
    <Alert>{catalogAge(account.catalog_refreshed_at)}</Alert>
    {account.catalog_error&&<Alert role="alert">{Object.keys(catalog).length?'The last refresh failed, so the previous catalog is shown. ':'The last refresh failed. '}{account.catalog_error}</Alert>}
    <p className="manager-help">Checked models appear in chat. With every model checked, new models join automatically. Values you enter replace what the provider reports.</p>
    <ProviderModelCatalog catalog={catalog} included={included} overrides={overrides} disabled={disabled} onInclude={include} onOverride={override}/>
   </>:<p className="manager-help">Save the account, then choose Refresh models to see what the provider offers.</p>}
  </section>
  <details className="provider-limits"><summary>Limits</summary>
   <p className="manager-help">Leave blank for no limit.</p>
   <div className="provider-limit-fields">
   <FormField label="Maximum output tokens" error={errors.max_output_tokens}>{control=><Input {...control} name="max_output_tokens" type="number" min="1" step="1" placeholder="No limit" value={maxOutput} disabled={disabled} onChange={event=>{setMaxOutput(event.target.value);clearError('max_output_tokens')}}/>}</FormField>
   <FormField label="Request timeout (seconds)" error={errors.request_timeout_s}>{control=><Input {...control} name="request_timeout_s" type="number" min="0" step="any" placeholder="No limit" value={requestTimeout} disabled={disabled} onChange={event=>{setRequestTimeout(event.target.value);clearError('request_timeout_s')}}/>}</FormField>
   </div>
  </details>
  <details><summary>Advanced account options</summary>
   <FormField label="Internal account key" hint="Generated automatically.">{control=><Input {...control} name="id" value={id} disabled={disabled||!!account} onChange={event=>setId(event.target.value)}/>}</FormField>
   <FormField label="Request format" hint="Most providers use Responses. Choose Chat Completions for a compatible server that only offers /chat/completions.">{control=><Select {...control} name="wire" value={wire} disabled={disabled} onChange={event=>setWire(event.target.value==='chat'?'chat':'responses')}><option value="responses">Responses</option><option value="chat">Chat Completions</option></Select>}</FormField>
   <fieldset className="provider-capabilities"><legend>Declared account capabilities</legend>{choices.map(([value,label])=><label key={value}><span><input type="checkbox" name="capabilities" value={value} checked={capabilities.includes(value)} disabled={disabled} aria-describedby={value==='functions'?'provider-functions-hint':undefined} onChange={event=>{setCapabilities(prior=>event.target.checked?[...prior,value]:prior.filter(item=>item!==value));clearError('capabilities')}}/>{label}</span></label>)}
    <p className="manager-help" id="provider-functions-hint"><strong>Function tools.</strong> Needed for tools, including desktop control. Any model that can call tools works; it does not need to see images.</p>
    {errors.capabilities&&<p className="form-field-error" role="alert">{errors.capabilities}</p>}</fieldset>
   <p className="manager-help">These declarations make the account available for matching operations. Verify each model and your account entitlement; declaring a capability does not prove provider support or authorize charges.</p>
  </details>
  {error&&<p role="alert" className="inline-error">{error}</p>}
  <div className="manager-item-actions"><Button type="submit" disabled={disabled}>{saving?'Saving account…':account?'Save account changes':'Save account'}</Button>{onCancel&&<Button type="button" variant="secondary" disabled={disabled} onClick={onCancel}>Cancel account edit</Button>}</div>
 </form>;
}
