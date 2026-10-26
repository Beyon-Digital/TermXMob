import {useRef,useState} from 'react';
import {Button} from '../components/ui/button';
import {Input} from '../components/ui/input';

type Account={id:string;name:string;kind:string;base_url:string;model:string;capabilities:string[]};
const choices=[['shell','Shell tools'],['functions','Function tools'],['computer','Computer tools'],['image','Image generation and editing'],['audio','Audio transcription and speech']];
const providers=[
 {id:'openai',label:'OpenAI',url:'https://api.openai.com/v1'},
 {id:'openrouter',label:'OpenRouter',url:'https://openrouter.ai/api/v1'},
 {id:'custom',label:'Another compatible provider',url:''},
];
function providerFor(url:string){return providers.find(provider=>provider.url===url)?.id||'custom'}

export default function ProviderAccountForm({account,onSave,onCancel,busy}:{account?:Account;onSave:(input:Account&{api_key:string|null})=>Promise<unknown>;onCancel?:()=>void;busy:boolean}){
 const [id,setId]=useState(account?.id||''),[name,setName]=useState(account?.name||''),[url,setUrl]=useState(account?.base_url||providers[0].url),[model,setModel]=useState(account?.model||''),[secret,setSecret]=useState(''),[capabilities,setCapabilities]=useState<string[]>(account?.capabilities||['shell','functions']);
 const [provider,setProvider]=useState(providerFor(account?.base_url||providers[0].url)),[saving,setSaving]=useState(false),[error,setError]=useState('');
 const pending=useRef(false),generatedId=useRef('');
 const disabled=busy||saving;
 function chooseProvider(value:string){const selected=providers.find(row=>row.id===value)!;setProvider(value);setUrl(selected.url);setSecret('');setError('')}
 async function save(){
  if(pending.current)return;
  pending.current=true;setSaving(true);setError('');
  if(!generatedId.current)generatedId.current=name.trim().toLowerCase().replace(/[^a-z0-9]+/g,'-').replace(/^-|-$/g,'')+'-'+crypto.randomUUID().slice(0,8);
  const apiKey=secret||null;setSecret('');
  try{const result=await onSave({id:id.trim()||generatedId.current,name:name.trim(),kind:account?.kind||'openai-compatible',base_url:url.trim(),model:model.trim(),capabilities,api_key:apiKey});if(result!==null&&!account){generatedId.current='';setId('');setName('')}}
  catch(reason){setError(reason instanceof Error?reason.message:'The account could not be saved. Review the settings and try again.')}
  finally{pending.current=false;setSaving(false)}
 }
 return <form className="manager-form provider-account-form" aria-label={account?'Edit provider account':'Add provider account'} aria-busy={disabled} onSubmit={event=>{event.preventDefault();void save()}}>
  {!account&&<><p className="manager-help">Connect an API account for your chats. Already use a signed-in coding agent? Choose it under Installed engines instead.</p><label className="manager-field"><span>Provider</span><select value={provider} disabled={disabled} onChange={event=>chooseProvider(event.target.value)}>{providers.map(row=><option key={row.id} value={row.id}>{row.label}</option>)}</select></label></>}
  <label>Display name<Input required value={name} placeholder={providers.find(row=>row.id===provider)?.label} disabled={disabled} onChange={event=>setName(event.target.value)}/></label>
  {(provider==='custom'||account)&&<label>Provider API address<Input required type="url" value={url} placeholder="https://provider.example/v1" disabled={disabled} onChange={event=>setUrl(event.target.value)}/></label>}
  <label>{account?'Replace API key (leave blank to keep current key)':'API key'}<Input type="password" autoComplete="new-password" value={secret} disabled={disabled} onChange={event=>setSecret(event.target.value)}/></label>
  <p className="manager-help provider-destination">{url?<>Requests use <strong>{url}</strong>. Your host keeps the key; it is never added to chat history.</>:'Enter the API address supplied by your provider.'}</p>
  <label>Default model for future sessions<Input value={model} placeholder="Choose a model in each chat" disabled={disabled} onChange={event=>setModel(event.target.value)}/></label>
  <details><summary>Advanced account options</summary><label>Internal account key (generated automatically)<Input value={id} disabled={disabled||!!account} onChange={event=>setId(event.target.value)}/></label>
   <fieldset className="provider-capabilities"><legend>Declared account capabilities</legend>{choices.map(([value,label])=><label key={value}><span><input type="checkbox" checked={capabilities.includes(value)} disabled={disabled} onChange={event=>setCapabilities(prior=>event.target.checked?[...prior,value]:prior.filter(item=>item!==value))}/>{label}</span></label>)}</fieldset>
   <p className="manager-help">These declarations make the account available for matching operations. Verify each model and your account entitlement; declaring a capability does not prove provider support or authorize charges.</p>
  </details>
  {error&&<p role="alert" className="inline-error">{error}</p>}
  <div className="manager-item-actions"><Button type="submit" disabled={disabled||!name.trim()||!url.trim()||!capabilities.length}>{saving?'Saving account…':account?'Save account changes':'Save account'}</Button>{onCancel&&<Button type="button" variant="secondary" disabled={disabled} onClick={onCancel}>Cancel account edit</Button>}</div>
 </form>;
}
