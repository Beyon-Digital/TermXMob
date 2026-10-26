import {useEffect,useRef,useState} from 'react';
import {Button} from '../components/ui/button';
import {Select} from '../components/ui/select';
import type {Engine,Provider,Session} from '../lib/api';
import './chat-connection-setup.css';

export function configuredModels(provider?:Provider){
 return [...new Set((provider?.model||'').split(/[\n,]/).map(model=>model.trim()).filter(Boolean))];
}

/** Configuration readiness is local metadata, never proof of provider access. */
export function chatConnectionProblem(session:Session|null,providers:Provider[],engines:Engine[]){
 if(!session||session.runner_id)return null;
 if(session.engine!=='internal'){
  const engine=engines.find(engine=>engine.id===session.engine);
  return !engine||engine.available===false||['unauthenticated','expired'].includes(engine.auth_state||'')?'engine':null;
 }
 const account=providers.find(provider=>provider.id===session.provider_id);
 if(!account?.secret_configured)return 'account';
 const models=configuredModels(account);
 return !models.length||!!session.model&&!models.includes(session.model)?'model':null;
}

type Props={session:Session;providers:Provider[];engines:Engine[];enabled:boolean;onConfigureAgent?:()=>void;onRefresh?:()=>void;onConnect:(changes:Record<string,unknown>)=>Promise<boolean>};

export default function ChatConnectionSetup({session,providers,engines,enabled,onConfigureAgent,onRefresh,onConnect}:Props){
 const [selection,setSelection]=useState({providerId:'',model:''}),[busy,setBusy]=useState(false),[error,setError]=useState(''),[saved,setSaved]=useState(false);
 const current=useRef({generation:0,busy:false});
 useEffect(()=>()=>{current.current.generation++;current.current.busy=false},[]);
 const problem=chatConnectionProblem(session,providers,engines);
 const engine=engines.find(value=>value.id===session.engine),signInRequired=['unauthenticated','expired'].includes(engine?.auth_state||'');
 const connected=providers.filter(provider=>provider.secret_configured);
 const account=connected.find(provider=>provider.id===selection.providerId)||connected.find(provider=>provider.id===session.provider_id)||connected[0];
 const models=configuredModels(account);
 const model=account?.id===selection.providerId&&models.includes(selection.model)?selection.model:account?.id===session.provider_id&&session.model&&models.includes(session.model)?session.model:models[0]||'';
 if(!problem)return null;
 function choose(providerId:string,nextModel?:string){setSelection({providerId,model:nextModel??configuredModels(connected.find(provider=>provider.id===providerId))[0]??''});setSaved(false);setError('')}
 async function connect(){
  if(!enabled||current.current.busy||saved||!account||!model)return;
  current.current.busy=true;const generation=current.current.generation;setBusy(true);setError('');
  try{
   const confirmed=await onConnect({provider_id:account.id,model,reasoning_config:{}});
   if(generation===current.current.generation){setSaved(confirmed);if(!confirmed)setError('The connection was not saved. Your draft is still here; review the error and try again.')}
  }catch(reason){if(generation===current.current.generation)setError(reason instanceof Error?reason.message:'The connection could not be saved. Your draft is still here.')}
  finally{if(generation===current.current.generation){current.current.busy=false;setBusy(false)}}
 }
 return <section className="chat-connection-setup" aria-label="Connect this chat" aria-busy={busy}>
  <div className="chat-connection-heading"><h3>{problem==='engine'?'Reconnect this engine':'Connect this chat'}</h3><p>{problem==='engine'?(signInRequired?'This engine reports that sign-in is required. Sign in on this host through Models & engines, then refresh the chat.':'This engine is not available in the current catalog. Check its installation and sign-in, then refresh the chat.'):problem==='model'?'Choose a model configured on your account. Your conversation and draft stay here.':'Choose a connected account and model. Your conversation and draft stay here.'}</p></div>
  {problem==='engine'&&engine?.auth_detail&&<p className="chat-connection-note">{engine.auth_detail}</p>}
  {problem==='engine'&&engine?.error&&<p className="chat-connection-note">Last engine check: {engine.error}</p>}
  {problem!=='engine'&&connected.length>0&&<form onSubmit={event=>{event.preventDefault();void connect()}}>
   <label>Account<Select aria-label="Chat account" value={account?.id||''} disabled={!enabled||busy} onChange={event=>choose(event.target.value)}>{connected.map(provider=><option key={provider.id} value={provider.id}>{provider.name}</option>)}</Select></label>
   <label>Model<Select aria-label="Chat model" value={model} disabled={!enabled||busy||!models.length} onChange={event=>choose(account!.id,event.target.value)}>{!models.length&&<option value="">No model configured</option>}{models.map(value=><option key={value} value={value}>{value}</option>)}</Select></label>
   <Button type="submit" size="sm" disabled={!enabled||busy||saved||!model}>{busy?'Saving connection…':saved?'Connection saved':'Use for this chat'}</Button>
  </form>}
  {problem!=='engine'&&!connected.length&&<p className="chat-connection-note">Add a model account in Models &amp; engines, then return here to connect it. Saving an account does not send a model request.</p>}
  {problem!=='engine'&&account&&!models.length&&<p className="chat-connection-note">Add a default model to {account.name} in Models &amp; engines, or choose another account.</p>}
  {saved&&<div><p className="chat-connection-note" role="status">Connection saved. Refreshing this chat…</p>{onRefresh&&<Button type="button" size="sm" variant="ghost" disabled={!enabled||busy} onClick={onRefresh}>Refresh chat</Button>}</div>}
  {error&&<p className="chat-connection-error" role="alert">{error}</p>}
  {onConfigureAgent&&<Button type="button" size="sm" variant="ghost" disabled={!enabled||busy} onClick={onConfigureAgent}>{connected.length?'Manage connections':'Set up connection'}</Button>}
 </section>;
}
