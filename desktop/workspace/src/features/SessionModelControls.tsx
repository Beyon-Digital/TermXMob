import {useCallback,useEffect,useLayoutEffect,useRef,useState,type KeyboardEvent} from 'react';
import * as Dialog from '@radix-ui/react-dialog';
import {Check,ChevronDown,ChevronLeft,ChevronRight,LoaderCircle,RotateCcw,Star,Zap} from 'lucide-react';
import {Button} from '../components/ui/button';
import {useNavigationLayerRef} from '../components/ui/navigation-layer';
import {type Session,type Engine,type Provider} from '../lib/api';
import './session-controls.css';
import './chat-composer.css';
import './session-model-controls.css';
import ReasoningPicker,{effortLabel} from './ReasoningPicker';
import {chatModels} from '../lib/model-info';
import {observeClosedState} from '../lib/disclosure-motion';

export type SessionModelControlProps={session:Session|null;engines:Engine[];providers:Provider[];runnerAccounts:{provider_id:string;credential_ref:string;name:string;models:string[]}[];models:string[];favorites:string[];favoriteKey:string;running:boolean;onFavorite:()=>void;onEngine:(engine:string)=>void;onPatch:(changes:Record<string,unknown>)=>void;onRefresh:()=>void;onError:(error:unknown)=>void;onConfigureAgent?:()=>void;coordinating?:boolean;visible?:boolean;ownerId?:string};
type PickerView='effort'|'models'|'accounts';
type Choice={id:string;label:string;detail?:string;accessibleLabel?:string};

/** The list navigates locally; only activation writes a selection. */
function ChoiceList({label,choices,selected,disabled,onChoose}:{label:string;choices:Choice[];selected:string;disabled:boolean;onChoose:(id:string)=>void}){
 const list=useRef<HTMLDivElement>(null),search=useRef({text:'',time:0});
 const [active,setActive]=useState(selected);
 useLayoutEffect(()=>{(list.current?.querySelector<HTMLButtonElement>('[aria-selected=true]:not(:disabled)')??list.current?.querySelector<HTMLButtonElement>('button:not(:disabled)')??list.current)?.focus({preventScroll:true})},[]);
 const navigate=(event:KeyboardEvent<HTMLDivElement>)=>{
  const rows=Array.from(list.current?.querySelectorAll<HTMLButtonElement>('[role=option]:not(:disabled)')||[]);
  if(!rows.length)return;
  const index=rows.indexOf(document.activeElement as HTMLButtonElement);
  let next:number|undefined;
  if(event.key==='ArrowDown')next=(index+1)%rows.length;
  else if(event.key==='ArrowUp')next=(index-1+rows.length)%rows.length;
  else if(event.key==='Home')next=0;
  else if(event.key==='End')next=rows.length-1;
  else if(event.key.length===1&&event.key.trim()&&!event.ctrlKey&&!event.metaKey&&!event.altKey){
   const time=Date.now();search.current={text:(time-search.current.time<700?search.current.text:'')+event.key.toLowerCase(),time};
   const match=rows.findIndex((_,offset)=>rows[(Math.max(index,0)+1+offset)%rows.length].getAttribute('aria-label')?.toLowerCase().startsWith(search.current.text));
   if(match>=0)next=(Math.max(index,0)+1+match)%rows.length;
  }
  if(next===undefined)return;
  event.preventDefault();rows[next].focus({preventScroll:true});rows[next].scrollIntoView?.({block:'nearest'});
 };
 return <div ref={list} role="listbox" aria-label={label} aria-disabled={disabled||undefined} tabIndex={disabled?0:-1} className="model-menu-list" onKeyDown={navigate}>
  {choices.map(choice=><button key={choice.id} type="button" role="option" aria-label={choice.accessibleLabel||choice.label} aria-selected={selected===choice.id} disabled={disabled} tabIndex={active===choice.id?0:-1} className="model-menu-option" onFocus={()=>setActive(choice.id)} onClick={()=>onChoose(choice.id)}><span><span>{choice.label}</span>{choice.detail&&<small>{choice.detail}</small>}</span>{selected===choice.id&&<Check size={16} aria-hidden="true"/>}</button>)}
 </div>;
}

/** Scope changes discard an open picker and any uncommitted slider position. */
export default function SessionModelControls(props:SessionModelControlProps){return <SessionModelControlsFor key={JSON.stringify([props.ownerId,props.session?.id])} {...props}/>}

function SessionModelControlsFor({session,providers,runnerAccounts,models,favorites,favoriteKey,running,onFavorite,onPatch,onRefresh,onError,onConfigureAgent,visible=true}:SessionModelControlProps){
 const hasEffort=session?.engine!=='internal'&&!session?.runner_id,firstView:PickerView=hasEffort?'effort':'models';
 const [open,setOpen]=useState(false),[view,setView]=useState<PickerView>(firstView),[locked,setLocked]=useState(false),[inputMethod,setInputMethod]=useState<'pointer'|'keyboard'>('pointer');
 const trigger=useRef<HTMLButtonElement>(null),content=useRef<HTMLDivElement>(null),scopeActive=useRef(visible);scopeActive.current=visible&&!locked;
 const layer=useNavigationLayerRef<HTMLDivElement>(),observer=useRef<MutationObserver|null>(null),focusModelOnReturn=useRef(false);
 const attach=useCallback((node:HTMLDivElement|null)=>{
  observer.current?.disconnect();content.current=node;layer(node);
  observer.current=node?observeClosedState(node,()=>{if(scopeActive.current&&trigger.current?.isConnected)trigger.current.focus({preventScroll:true})}):null;
 },[layer]);
 const shown=open&&visible&&!locked;
 const [place,setPlace]=useState({bottom:0,left:16,maxHeight:560});
 useEffect(()=>{if(!visible){setOpen(false);setView(firstView)}},[visible,firstView]);
 useEffect(()=>{setOpen(false);setView(firstView)},[session?.engine,session?.runner_id,firstView]);
 useEffect(()=>{
  const close=()=>{scopeActive.current=false;setLocked(true);setOpen(false);setView(firstView)};
  const unlock=()=>setLocked(false);
  for(const name of ['termx-locked','termx-session-locked','termx-signed-out'])window.addEventListener(name,close);
  window.addEventListener('termx-session-unlocked',unlock);
  return()=>{for(const name of ['termx-locked','termx-session-locked','termx-signed-out'])window.removeEventListener(name,close);window.removeEventListener('termx-session-unlocked',unlock)};
 },[firstView]);
 useLayoutEffect(()=>{
  if(!shown)return;
  const anchor=trigger.current;
  const move=()=>{
   const bounds=anchor?.getBoundingClientRect();if(!bounds)return;
   const width=Math.min(256,window.innerWidth-32),bottom=Math.max(8,Math.min(window.innerHeight-88,window.innerHeight-bounds.top+8));
   setPlace({bottom,left:Math.max(16,Math.min(bounds.right-width,window.innerWidth-width-16)),maxHeight:Math.max(80,window.innerHeight-bottom-8)});
  };
  move();window.addEventListener('resize',move);window.addEventListener('scroll',move,true);
  const resize=typeof ResizeObserver==='undefined'?null:new ResizeObserver(move);if(anchor)resize?.observe(anchor);
  return()=>{window.removeEventListener('resize',move);window.removeEventListener('scroll',move,true);resize?.disconnect()};
 },[shown]);
 useLayoutEffect(()=>{if(shown&&view==='effort'&&focusModelOnReturn.current){focusModelOnReturn.current=false;content.current?.querySelector<HTMLButtonElement>('.effort-model-choice')?.focus({preventScroll:true})}},[shown,view]);
 const showView=(next:PickerView)=>{focusModelOnReturn.current=next==='effort';setView(next)};
 const account=providers.find(item=>item.id===session?.provider_id);
 const connectionMissing=session?.engine==='internal'&&!session.runner_id&&!account?.secret_configured;
 const accounts=session?.runner_id?runnerAccounts.map(item=>({id:item.provider_id,name:item.name})):providers.map(item=>({id:item.id,name:item.name}));
 if(session?.provider_id&&!accounts.some(item=>item.id===session.provider_id))accounts.push({id:session.provider_id,name:'Unavailable account ('+session.provider_id+')'});
 const accountName=accounts.find(item=>item.id===session?.provider_id)?.name||'Choose provider';
 const choices=[...new Set([...(session?.model?[session.model]:[]),...models])].sort((a,b)=>Number(favorites.includes(JSON.stringify([session?.engine,session?.provider_id,b])))-Number(favorites.includes(JSON.stringify([session?.engine,session?.provider_id,a]))));
 const effort=Object.values(session?.reasoning_config||{}).find(value=>typeof value==='string');
 // An unbound conversation can choose a real model directly; opening this list never selects an account.
 const showProviderModels=!hasEffort&&(session?.runner_id?!runnerAccounts.some(item=>item.provider_id===session.provider_id):!account?.secret_configured);
 const providerModels=showProviderModels?(session?.runner_id?runnerAccounts:providers.filter(item=>item.secret_configured).map(item=>({provider_id:item.id,name:item.name,models:chatModels(item),credential_ref:undefined}))).flatMap(item=>[...new Set(item.models)].map(model=>({id:JSON.stringify([item.provider_id,model]),label:model,detail:item.name,accessibleLabel:model+' · '+item.name,providerId:item.provider_id,model,credentialRef:item.credential_ref}))):[];
 const selectedModelId=showProviderModels?JSON.stringify([session?.provider_id,session?.model]):session?.model||'';
 const savedModel=showProviderModels&&session?.model&&!providerModels.some(item=>item.id===selectedModelId)?{id:selectedModelId,label:session.model,detail:session.provider_id?accountName:undefined}:null;
 const availableModels=showProviderModels?[...(savedModel?[savedModel]:[]),...providerModels]:choices.map(model=>({id:model,label:model}));
 const emptyModels=!hasEffort&&!availableModels.length;
 const chooseModel=(value:string)=>{
  if(!session||running||!scopeActive.current)return;
  if(showProviderModels){
   if(value!==selectedModelId){
    const selected=providerModels.find(item=>item.id===value);if(!selected)return;
    onPatch({provider_id:selected.providerId,model:selected.model,reasoning_config:{},...(session.runner_id?{runner_credential_ref:selected.credentialRef}: {})});
   }
  }else if(value!==(session.model||''))onPatch({model:value||null,reasoning_config:{}});
  if(hasEffort)showView('effort');else{setOpen(false);setView(firstView)}
 };
 const chooseAccount=(providerId:string)=>{
  if(!session||running||!scopeActive.current)return;
  if(providerId!==(session.provider_id||'')){
   const selected=runnerAccounts.find(item=>item.provider_id===providerId);
   onPatch(session.runner_id?{reasoning_config:{},provider_id:providerId,runner_credential_ref:selected?.credential_ref||null,model:selected?.models[0]||null}:{provider_id:providerId,model:chatModels(providers.find(item=>item.id===providerId))[0]||null,reasoning_config:{}});
  }
  showView('models');
 };
 const selectingEffort=shown&&view==='effort';
 return <Dialog.Root modal={false} open={shown} onOpenChange={value=>{setOpen(value);setView(firstView)}}>
  <Dialog.Trigger asChild><Button ref={trigger} className="session-model-chip composer-model-chip" variant="ghost" size="sm" aria-label="Model and effort" title={session?.model||'Default model'} disabled={!session||!visible||locked} onPointerDown={()=>setInputMethod('pointer')} onKeyDown={()=>setInputMethod('keyboard')}><span>{selectingEffort?'Select effort':session?.model||'Default model'}</span>{hasEffort&&!selectingEffort&&effort&&<small className="model-effort-label">{effortLabel(effort)}</small>}<ChevronDown size={16}/></Button></Dialog.Trigger>
  {visible&&!locked&&<Dialog.Portal container={trigger.current?.closest('.workspace-shell')||undefined}><Dialog.Content ref={attach} className="session-model-popover" data-view={view} data-input-method={inputMethod} style={{...place,maxHeight:Math.min(place.maxHeight,view==='effort'?560:310)}} aria-describedby={undefined} onPointerDownCapture={()=>setInputMethod('pointer')} onKeyDownCapture={()=>setInputMethod('keyboard')} onOpenAutoFocus={event=>{
   event.preventDefault();if(inputMethod==='pointer')content.current?.focus({preventScroll:true});else(content.current?.querySelector<HTMLInputElement>('input[type=range]:not(:disabled)')??content.current?.querySelector<HTMLButtonElement>('button:not(:disabled)'))?.focus({preventScroll:true});
  }} onCloseAutoFocus={event=>{if(!scopeActive.current)event.preventDefault()}} onEscapeKeyDown={event=>{if(view==='accounts'||hasEffort&&view==='models'){event.preventDefault();showView(view==='accounts'?'models':'effort')}}}>
   <Dialog.Title className="model-picker-accessible-title">Model and effort</Dialog.Title>
   {hasEffort&&<div className="effort-view" hidden={view!=='effort'}>
    {session&&<ReasoningPicker session={session} onUpdated={()=>onRefresh()} onError={onError} disabled={running||!scopeActive.current} renderHeader={({label,reset,resetDisabled,busy})=><div className="effort-popover-heading">
     {busy?<LoaderCircle className="effort-bolt spin" size={16} aria-hidden="true"/>:<Zap className="effort-bolt" size={16} aria-hidden="true"/>}
     <strong key={label} className="effort-current-label" aria-live="polite">{label}</strong>
     <Button className="effort-model-choice" variant="ghost" size="sm" aria-label="Choose model" title={session.model||'Default model'} onClick={()=>showView('models')}><span>{session.model||'Default model'}</span><ChevronRight size={16}/></Button>
     <Button className="effort-reset" variant="ghost" size="icon" aria-label="Reset effort" title="Reset effort" disabled={resetDisabled} aria-busy={busy||undefined} onClick={reset}><RotateCcw size={16}/></Button>
    </div>}/>}
   </div>}
   {view==='models'&&<div className="model-menu-view" key="models">
    <div className="model-menu-heading"><span>Select model</span><div className="model-menu-actions">
     <Button className="model-favorite" size="icon" variant="ghost" disabled={!session?.model} aria-label="Favorite selected model" aria-pressed={favorites.includes(favoriteKey)} onClick={onFavorite}><Star size={16} fill={favorites.includes(favoriteKey)?'currentColor':'none'}/></Button>
     {hasEffort&&<Button className="model-menu-back" variant="ghost" size="icon" aria-label="Back to effort" onClick={()=>showView('effort')}><ChevronLeft size={16}/></Button>}
    </div></div>
    <ChoiceList key={JSON.stringify([session?.engine,session?.runner_id,session?.provider_id,showProviderModels])} label="Session model" choices={showProviderModels||emptyModels?availableModels:[{id:'',label:'Default',detail:session?.engine!=='internal'?'Recommended set of models':undefined},...availableModels]} selected={selectedModelId} disabled={running} onChoose={chooseModel}/>
    {emptyModels&&<p className="model-menu-empty" role="status">No models configured</p>}
    {session?.engine==='internal'&&<div className="model-menu-footer"><Button className="model-account-choice" variant="ghost" size="sm" aria-label="Choose account" title={accountName} disabled={running} onClick={()=>showView('accounts')}><span>{accountName}</span><ChevronRight size={16}/></Button></div>}
    {(connectionMissing||emptyModels&&!session?.runner_id)&&onConfigureAgent&&<Button className="effort-connections" size="sm" variant="ghost" onClick={()=>{setOpen(false);setView(firstView);onConfigureAgent()}}>Manage connections</Button>}
   </div>}
   {view==='accounts'&&<div className="model-menu-view" key="accounts">
    <div className="model-menu-heading"><span>Select account</span><Button className="model-menu-back" variant="ghost" size="icon" aria-label="Back to models" onClick={()=>showView('models')}><ChevronLeft size={16}/></Button></div>
    <ChoiceList label="Session provider" choices={[{id:'',label:'Choose provider'},...accounts.map(item=>({id:item.id,label:item.name}))]} selected={session?.provider_id||''} disabled={running} onChoose={chooseAccount}/>
   </div>}
  </Dialog.Content></Dialog.Portal>}
 </Dialog.Root>;
}
