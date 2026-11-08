import {request as baseRequest} from '../lib/api';
import SettingsActions from './SettingsActions';
import {createContext,useCallback,useContext,useEffect,useMemo,useRef,useState,type MouseEvent,type ReactNode,type RefObject} from 'react';
import {Button} from '../components/ui/button';
import {Dialog,DialogContent,DialogDescription,DialogTitle} from '../components/ui/dialog';
import {settingsCategories,settingsGroups,canonicalSettingsCategory} from '../lib/settings-categories';
import SettingsIcon from '../components/SettingsIcon';
import {SETTINGS_LEAVE,SettingsFormsContext,type SettingsFormsRegistry} from './settings-forms';
import {useBotsNeedYouCount} from './bot-attention';
import {needsYouCount} from '../lib/bot-schedules';
import './settings-surface.css';

// Managers that have not moved to `useDirtyForm` still report saves through this context. It stays
// until each of them owns a form; new code should use `useDirtyForm` instead.
const SaveContext=createContext<()=>()=>void>(()=>()=>{});
export const useSettingsSave=()=>useContext(SaveContext);
function commitsSettings(path:string,init?:RequestInit){
 if(['GET','HEAD','OPTIONS'].includes((init?.method||'GET').toUpperCase()))return false;
 // These POSTs inspect or exercise a draft; they never persist that form.
 if(/(?:^|\/)(?:[^/]*-)?(?:validate|preview|test|evaluate|begin|probe|inspect|export|call)(?:[/?#]|$)/.test(path))return false;
 if(path.split('?')[0]==='/graphql'){
  try{return typeof init?.body==='string'&&/^\s*mutation\b/.test(JSON.parse(init.body).query||'')}catch{return false}
 }
 return true;
}
export function useSettingsRequest(){const beginSave=useSettingsSave();return async function request<T=unknown>(...args:Parameters<typeof baseRequest<T>>){const saved=beginSave();const result=await baseRequest<T>(...args);if(commitsSettings(String(args[0]),args[1]))saved();return result}}

export function SettingsNavigation({category,onSelect,waiting:given}:{category:string;onSelect:(id:string)=>void;waiting?:number}){
 const selected=canonicalSettingsCategory(category);
 // Not a notification centre: one count on the Bots entry, so a bot waiting for an answer is noticed from any settings page.
 const own=useBotsNeedYouCount(given===undefined),waiting=given??own;
 function follow(event:MouseEvent<HTMLAnchorElement>,id:string){
  if(event.defaultPrevented||event.button!==0||event.metaKey||event.ctrlKey||event.shiftKey||event.altKey)return;
  event.preventDefault();onSelect(id);
 }
 return <nav className="settings-categories" aria-label="Settings categories">
  {settingsGroups.map(group=><div className="settings-nav-group" key={group}>
   <p className="settings-nav-heading">{group}</p>
   <ul>{settingsCategories.filter(row=>row.group===group).map(({id,label})=><li key={id}><a href={'/manage/'+id} aria-current={id===selected?'page':undefined} onClick={event=>follow(event,id)}><SettingsIcon kind={id}/><span>{label}</span>{id==='bots'&&waiting>0&&<><span className="visually-hidden">, {needsYouCount(waiting)}</span><span className="settings-nav-count" aria-hidden="true">{waiting}</span></>}</a></li>)}</ul>
  </div>)}
 </nav>
}

type Scope=HTMLFormElement|'page';
const scopeOf=(node:EventTarget|null):Scope=>node instanceof Element?(node.closest('form')??'page'):'page';

export default function SettingsSurface({category,onSelect,onClose,children,context,scopeKey,waiting}:{waiting?:number;scopeKey?:string;context?:ReactNode;category:string;onSelect:(id:string)=>void;onClose:()=>void;children:ReactNode}){
 const [surface,setSurface]=useState<HTMLDivElement|null>(null);
 const opener=useRef(document.activeElement instanceof HTMLElement?document.activeElement:null);
 useEffect(()=>{const viewport=window.visualViewport;if(!viewport||!surface)return;let frame=0;const fit=()=>{const node=surface;if(!node)return;node.style.setProperty('--settings-viewport-height',viewport.height+'px');node.style.setProperty('--settings-viewport-top','calc('+viewport.offsetTop+'px + max(16px, env(safe-area-inset-top)))');node.style.setProperty('--settings-viewport-bottom','auto');cancelAnimationFrame(frame);frame=requestAnimationFrame(()=>{const focused=document.activeElement;if(window.matchMedia('(max-width:700px)').matches&&focused instanceof HTMLElement&&node.contains(focused)&&focused.matches('input,textarea,select,[contenteditable=true]'))focused.scrollIntoView?.({block:'nearest',inline:'nearest'})})};fit();viewport.addEventListener('resize',fit);viewport.addEventListener('scroll',fit);return()=>{cancelAnimationFrame(frame);viewport.removeEventListener('resize',fit);viewport.removeEventListener('scroll',fit)}},[surface]);

 // Forms that use `useDirtyForm` report themselves here; `edits` covers the forms that do not yet.
 const [forms,setForms]=useState<ReadonlyMap<string,RefObject<HTMLFormElement|null>>>(()=>new Map());
 const registry=useMemo<SettingsFormsRegistry>(()=>({report(key,form){setForms(prior=>{if(form?prior.get(key)===form:!prior.has(key))return prior;const next=new Map(prior);if(form)next.set(key,form);else next.delete(key);return next})}}),[]);
 const edits=useRef(new Map<Scope,number>()),revision=useRef(0),action=useRef<Scope>('page');
 const [legacyDirty,setLegacyDirty]=useState(false),[epoch,setEpoch]=useState(0),[pending,setPending]=useState<{proceed?:()=>void}|null>(null);
 const dirty=forms.size>0||legacyDirty,dirtyNow=useRef(false);
 useEffect(()=>{dirtyNow.current=dirty},[dirty]);
 const clearEdits=useCallback(()=>{edits.current.clear();dirtyNow.current=false;setLegacyDirty(false)},[]);
 const beginSave=useCallback(()=>{const scope=action.current,version=edits.current.get(scope);return()=>{if(edits.current.get(scope)===version)edits.current.delete(scope);for(const key of edits.current.keys())if(key!=='page'&&!key.isConnected)edits.current.delete(key);setLegacyDirty(edits.current.size>0)}},[]);

 const askDiscard=useCallback((proceed?:()=>void)=>setPending({proceed}),[]);
 function discard(){const next=pending?.proceed;setPending(null);clearEdits();setEpoch(value=>value+1);next?.()}
 // Returns true when the caller may carry on now.
 const guarded=useCallback((proceed?:()=>void)=>{if(!dirtyNow.current)return true;askDiscard(proceed);return false},[askDiscard]);
 useEffect(()=>{
  const leave=(event:BeforeUnloadEvent)=>{if(dirtyNow.current){event.preventDefault();event.returnValue=''}};
  const guard=(event:Event)=>{const proceed=(event as CustomEvent<{proceed?:()=>void}>).detail?.proceed;if(!guarded(proceed))event.preventDefault()};
  window.addEventListener('beforeunload',leave);window.addEventListener(SETTINGS_LEAVE,guard);
  return()=>{window.removeEventListener('beforeunload',leave);window.removeEventListener(SETTINGS_LEAVE,guard)};
 },[guarded]);
 useEffect(()=>{clearEdits();surface?.querySelector('.settings-content')?.scrollTo?.(0,0)},[category,scopeKey]);
 function select(id:string){if(id===category)return;if(guarded(()=>onSelect(id)))onSelect(id)}
 function save(){for(const form of forms.values())form.current?.requestSubmit()}
 return <Dialog open onOpenChange={open=>{if(!open&&guarded(onClose))onClose()}}><DialogContent ref={setSurface} className="settings-surface" onCloseAutoFocus={event=>{if(!opener.current?.isConnected){const target=document.querySelector<HTMLElement>('[aria-label="Show sidebar"]');if(target){event.preventDefault();target.focus()}}}} onPointerDownOutside={event=>event.preventDefault()}>
  <header className="settings-title"><div className="settings-title-copy"><DialogTitle>Settings</DialogTitle><DialogDescription>Configure your account, this host and workspace capabilities.</DialogDescription></div>{context}</header>
  <SettingsNavigation category={category} onSelect={select} waiting={waiting}/>
  <SettingsFormsContext.Provider value={registry}><SaveContext.Provider value={beginSave}>
   <div className="settings-content" onClickCapture={event=>{action.current=scopeOf(event.target)}} onSubmitCapture={event=>{action.current=scopeOf(event.target)}} onChangeCapture={event=>{
    const target=event.target as HTMLElement;
    // A dialog opened from a settings page renders in a portal. React still bubbles its events to this
    // wrapper, but those edits belong to the dialog and are not unsaved changes of the page.
    if(!event.currentTarget.contains(target))return;
    if(target.closest('[data-settings-filter],[data-settings-autosave],form[data-settings-form]'))return;
    edits.current.set(scopeOf(target),++revision.current);dirtyNow.current=true;setLegacyDirty(true);
   }}><SettingsActions key={`${category}:${scopeKey??''}:${epoch}`}>{children}</SettingsActions></div>
  </SaveContext.Provider></SettingsFormsContext.Provider>
  <footer className="settings-footer" data-dirty={dirty}>
   <p role="status" className="settings-draft-status">{dirty?'Unsaved changes':''}</p>
   {dirty&&<div className="settings-footer-actions"><Button variant="outline" onClick={()=>askDiscard()}>Discard</Button>{forms.size>0&&<Button onClick={save}>Save</Button>}</div>}
  </footer>
  <Dialog open={!!pending} onOpenChange={open=>{if(!open)setPending(null)}}><DialogContent><DialogTitle>Discard unsaved changes?</DialogTitle><DialogDescription>Your edits on this page have not been saved and will be lost.</DialogDescription><div className="settings-footer-actions"><Button variant="outline" onClick={()=>setPending(null)}>Keep editing</Button><Button variant="destructive" onClick={discard}>Discard changes</Button></div></DialogContent></Dialog>
 </DialogContent></Dialog>
}
