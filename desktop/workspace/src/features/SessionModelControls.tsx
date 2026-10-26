import {useEffect,useState} from 'react';
import {ChevronDown,Star} from 'lucide-react';
import {Button} from '../components/ui/button';
import {Select} from '../components/ui/select';
import {Dialog,DialogContent,DialogDescription,DialogTitle} from '../components/ui/dialog';
import {type Session,type Engine,type Provider} from '../lib/api';
import './session-controls.css';
import './chat-composer.css';
import ReasoningPicker from './ReasoningPicker';
import {configuredModels} from './ChatConnectionSetup';

export type SessionModelControlProps={session:Session|null;engines:Engine[];providers:Provider[];runnerAccounts:{provider_id:string;credential_ref:string;name:string;models:string[]}[];models:string[];favorites:string[];favoriteKey:string;running:boolean;onFavorite:()=>void;onEngine:(engine:string)=>void;onPatch:(changes:Record<string,unknown>)=>void;onRefresh:()=>void;onError:(error:unknown)=>void;onConfigureAgent?:()=>void;coordinating?:boolean};

export default function SessionModelControls({session,engines,providers,runnerAccounts,models,favorites,favoriteKey,running,onFavorite,onPatch,onRefresh,onError,onConfigureAgent,coordinating=false}:SessionModelControlProps){
 const [open,setOpen]=useState(false);
 useEffect(()=>setOpen(false),[session?.id]);
 const runnerAccount=runnerAccounts.find(item=>item.provider_id===session?.provider_id);
 const account=providers.find(item=>item.id===session?.provider_id);
 const engine=engines.find(item=>item.id===session?.engine);
 const engineName=engine?.name||'This engine';
 const engineAccount=!engine||engine.available===false?'This engine is unavailable on this host. Check its installation in Models & engines.':['unauthenticated','expired'].includes(engine.auth_state||'')?'Sign in to '+engineName+' on this host, then refresh Models & engines.':engine.auth_state==='authenticated'?engineName+' reports that it is signed in on this host.':engine.auth_state==='not_required'?engineName+' reports that sign-in is not required.':'Uses '+engineName+' on this host. Its account status has not been verified by TermX.';
 const connectionMissing=session?.engine==='internal'&&!session.runner_id&&!account?.secret_configured;
 const accounts=session?.runner_id?runnerAccounts.map(item=>({id:item.provider_id,name:item.name})):providers.map(item=>({id:item.id,name:item.name}));
 if(session?.provider_id&&!accounts.some(item=>item.id===session.provider_id))accounts.push({id:session.provider_id,name:'Unavailable account ('+session.provider_id+')'});
 const choices=[...new Set([...(session?.model?[session.model]:[]),...models])].sort((a,b)=>Number(favorites.includes(JSON.stringify([session?.engine,session?.provider_id,b])))-Number(favorites.includes(JSON.stringify([session?.engine,session?.provider_id,a]))));
 const effort=Object.values(session?.reasoning_config||{}).filter(value=>typeof value==='string').join(', ').replaceAll('_',' ');
 return <>
  <Button className="session-model-chip composer-model-chip" variant="ghost" size="sm" aria-label="Model and effort" title={session?.model||'Default model'} disabled={!session} aria-haspopup="dialog" aria-expanded={open} onClick={()=>setOpen(true)}><span>{session?.model||'Default model'}</span>{effort&&<small className="model-effort-label">{effort}</small>}<ChevronDown size={12}/></Button>
  <Dialog open={open} onOpenChange={setOpen}><DialogContent className="session-model-settings chat-control-dialog">
   <DialogTitle>Model and effort</DialogTitle><DialogDescription>Model and thinking effort for your next turn.</DialogDescription>
   {coordinating&&<p className="control-description">Your main agent runs on TermX Agent and can delegate work to installed engines. Each child chat keeps its engine, approvals, and supported limits.</p>}
   {connectionMissing&&<div className="model-connection-notice"><p>{account?'This account needs a connection before it can respond.':'Choose a connected account to use your agent.'}</p>{onConfigureAgent&&<Button size="sm" variant="outline" onClick={()=>{setOpen(false);onConfigureAgent()}}>Manage connections</Button>}</div>}
   <div className="model-selection-row"><label>Model<Select aria-label="Session model" value={session?.model||''} disabled={!session||running} onChange={event=>onPatch({model:event.target.value||null,reasoning_config:{}})}><option value="">Default model</option>{choices.map(model=><option key={model} value={model}>{favorites.includes(JSON.stringify([session?.engine,session?.provider_id,model]))?'★ ':''}{model}</option>)}</Select></label><Button className="model-favorite" size="icon" variant="ghost" disabled={!session?.model} aria-label="Favorite selected model" aria-pressed={favorites.includes(favoriteKey)} onClick={onFavorite}><Star size={17} fill={favorites.includes(favoriteKey)?'currentColor':'none'}/></Button></div>
   {!choices.length&&<p className="control-description">{session?.engine==='internal'?'The connected account supplies the available models.':'No model list is available. The engine will use its default model.'}</p>}
   {session&&<ReasoningPicker session={session} onUpdated={()=>onRefresh()} onError={onError} disabled={running}/>}
   {session?.engine==='internal'&&<div className="model-account-field"><label>Account<Select aria-label="Session provider" value={session.provider_id||''} disabled={running} onChange={event=>{
    const selected=runnerAccounts.find(item=>item.provider_id===event.target.value);
    onPatch(session.runner_id?{reasoning_config:{},provider_id:event.target.value,runner_credential_ref:selected?.credential_ref||null,model:selected?.models[0]||null}:{provider_id:event.target.value,model:configuredModels(providers.find(item=>item.id===event.target.value))[0]||null,reasoning_config:{}});
   }}><option value="">Choose provider</option>{accounts.map(item=><option key={item.id} value={item.id}>{item.name}</option>)}</Select></label><p className="control-description">{session.runner_id?(runnerAccount?runnerAccount.name+' · Connected runner account':'Choose a connected runner account.'):account?(account.secret_configured?'Connected account.':'Connect this account in Models & engines.'):!providers.length?'Add an account in Models & engines to choose its models.':'Choose an account for this conversation.'}</p></div>}
   {session?.engine!=='internal'&&<><p className="control-description">{engineAccount}</p>{engine?.auth_detail&&<p className="control-description">{engine.auth_detail}</p>}{engine?.error&&<p className="control-description">Last engine check: {engine.error}</p>}</>}
   {running&&<p className="control-running-note" role="status">The current task keeps its settings. You can change them when it finishes.</p>}
  </DialogContent></Dialog>
 </>;
}
