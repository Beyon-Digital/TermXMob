import './cloud-machines.css';
import {useEffect,useState} from 'react';
import {Cloud,KeyRound} from 'lucide-react';
import {Button} from '../components/ui/button';
import {Input} from '../components/ui/input';
import {Select} from '../components/ui/select';
import {json,request} from '../lib/api';

export type CloudAccount={id:string;name:string;provider:'aws'|'azure'|'gcp';identity:{account:string;principal:string};authenticated:boolean};
const names={aws:'Amazon Web Services',azure:'Microsoft Azure',gcp:'Google Cloud'};
export default function CloudAccounts({search=''}:{search?:string}){
 const [accounts,setAccounts]=useState<CloudAccount[]>([]),[provider,setProvider]=useState<CloudAccount['provider']>('aws'),[name,setName]=useState(''),[fields,setFields]=useState<Record<string,string>>({}),[gcp,setGcp]=useState(''),[error,setError]=useState(''),[notice,setNotice]=useState(''),[busy,setBusy]=useState(false),[editing,setEditing]=useState<CloudAccount|null>(null),[secure,setSecure]=useState(true);
 async function load(){const value=await request<{accounts:CloudAccount[];secure_storage:boolean}>('/api/cloud/accounts');setAccounts(value.accounts);setSecure(value.secure_storage)}
 useEffect(()=>{let live=true;request<{accounts:CloudAccount[];secure_storage:boolean}>('/api/cloud/accounts').then(value=>{if(live){setAccounts(value.accounts);setSecure(value.secure_storage)}}).catch(reason=>{if(live)setError(String(reason))});return()=>{live=false}},[]);
 async function act(action:()=>Promise<unknown>){setBusy(true);setError('');setNotice('');try{await action();await load()}catch(reason){setError(reason instanceof Error?reason.message:String(reason))}finally{setBusy(false)}}
 const field=(key:string,label:string,secret=false,required=true)=><label key={key}>{label}<Input required={required} type={secret?'password':'text'} autoComplete="off" value={fields[key]||''} onChange={event=>setFields(old=>({...old,[key]:event.target.value}))}/></label>;
 return <section aria-label="Cloud accounts" className="cloud-accounts"><p>Authenticate once, then choose machine, storage and networking configurations in Runners. Credentials stay in this host’s secure credential store.</p>
  {!secure&&<p role="alert" className="inline-error">Secure credential storage is unavailable on this host. Enable the OS keychain or Secret Service to save provider authentication.</p>}
  {error&&<p role="alert" className="inline-error">{error}</p>}{notice&&<p role="status">{notice}</p>}
  {accounts.filter(account=>(account.name+' '+account.provider+' '+account.identity.account).toLowerCase().includes(search.toLowerCase())).map(account=><article className="manager-item" key={account.id}><div><h3><Cloud size={16}/> {account.name}</h3><p>{names[account.provider]} · {account.identity.account} · {account.authenticated?'Authenticated':'Credentials unavailable'}</p></div><div className="creation-actions"><Button variant="secondary" disabled={busy} onClick={()=>{setEditing(account);setProvider(account.provider);setName(account.name);setFields({});setGcp('')}}>Update authentication</Button><Button variant="ghost" disabled={busy} onClick={()=>void act(()=>request('/api/cloud/accounts/'+account.id,{method:'DELETE'}))}>Remove account</Button></div></article>)}
  <form className="creation-form" onSubmit={event=>{event.preventDefault();void act(async()=>{let credentials:Record<string,unknown>=fields;if(provider==='gcp'){try{credentials=JSON.parse(gcp)}catch{throw new Error('Paste a valid service-account JSON key')}}await request('/api/cloud/accounts'+(editing?'/'+editing.id:''),json(editing?'PUT':'POST',{name,provider,credentials}));setFields({});setGcp('');setName('');setEditing(null);setNotice('Authentication verified and saved. Open Runners to create a cloud machine.')})}}>
   <h3><KeyRound size={17}/> {editing?'Update account authentication':'Connect a cloud account'}</h3>
   <label>Account name<Input required value={name} onChange={event=>setName(event.target.value)} placeholder="Development infrastructure"/></label>
   <label>Cloud provider<Select aria-label="Cloud provider" value={provider} disabled={!!editing||busy} onChange={event=>{setProvider(event.target.value as CloudAccount['provider']);setFields({});setGcp('')}}>{Object.entries(names).map(([id,label])=><option key={id} value={id}>{label}</option>)}</Select></label>
   {provider==='aws'&&<><p>Use an IAM access key with EC2 provisioning and STS identity permissions. Temporary credentials also need the session token.</p>{field('access_key_id','AWS access key ID')}{field('secret_access_key','AWS secret access key',true)}{field('session_token','AWS session token',true,false)}</>}
   {provider==='azure'&&<><p>Use a service principal with Contributor access to the selected subscription, including its compute and network resources.</p>{field('tenant_id','Azure tenant ID')}{field('subscription_id','Azure subscription ID')}{field('client_id','Azure application client ID')}{field('client_secret','Azure client secret',true)}</>}
   {provider==='gcp'&&<><p>Use a service-account JSON key for a project with Compute Engine enabled and Compute Admin permissions. No cloud service-account key is sent to a runner.</p><label>GCP service-account JSON<textarea required autoComplete="off" spellCheck={false} value={gcp} onChange={event=>setGcp(event.target.value)} placeholder="Paste the service-account JSON key"/></label></>}
   <div className="creation-actions"><Button type="submit" disabled={busy||!secure}>{busy?'Verifying authentication…':'Verify and save account'}</Button>{editing&&<Button variant="ghost" type="button" disabled={busy} onClick={()=>{setEditing(null);setFields({});setGcp('');setName('')}}>Cancel update</Button>}</div>
  </form>
 </section>;
}
