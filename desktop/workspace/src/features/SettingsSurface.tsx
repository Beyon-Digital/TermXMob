import {request as baseRequest} from '../lib/api';
import SettingsActions from './SettingsActions';
import {createContext,useCallback,useContext,useEffect,useRef,useState,type ReactNode} from 'react';
import {Dialog,DialogContent,DialogDescription,DialogTitle} from '../components/ui/dialog';
import {settingsCategories,canonicalSettingsCategory} from '../lib/settings-categories';
import './settings-surface.css';
const SaveContext=createContext<()=>()=>void>(()=>()=>{});
export const useSettingsSave=()=>useContext(SaveContext);
export function useSettingsRequest(){const beginSave=useSettingsSave();return async function request<T=unknown>(...args:Parameters<typeof baseRequest<T>>){const saved=beginSave();const result=await baseRequest<T>(...args);if(!['GET','HEAD','OPTIONS'].includes((args[1]?.method||'GET').toUpperCase()))saved();return result}}

export function SettingsNavigation({category,onSelect}:{category:string;onSelect:(id:string)=>void}){
 const selected=canonicalSettingsCategory(category);
 return <nav className="manager-nav settings-categories" aria-label="Settings categories">{settingsCategories.map(({id,label})=><button key={id} aria-current={id===selected?'page':undefined} className={id===selected?'selected':''} onClick={()=>onSelect(id)} onKeyDown={event=>{
  const buttons=Array.from(event.currentTarget.parentElement!.querySelectorAll('button')),index=buttons.indexOf(event.currentTarget);
  const next=event.key==='Home'?0:event.key==='End'?buttons.length-1:['ArrowRight','ArrowDown'].includes(event.key)?(index+1)%buttons.length:['ArrowLeft','ArrowUp'].includes(event.key)?(index+buttons.length-1)%buttons.length:-1;
  if(next>=0){event.preventDefault();buttons[next].focus();buttons[next].scrollIntoView?.({block:'nearest',inline:'nearest'})}
 }}>{label}</button>)}</nav>
}
export default function SettingsSurface({category,onSelect,onClose,children,context,scopeKey}:{scopeKey?:string;context?:ReactNode;category:string;onSelect:(id:string)=>void;onClose:()=>void;children:ReactNode}){
 const [surface,setSurface]=useState<HTMLDivElement|null>(null);
 const opener=useRef(document.activeElement instanceof HTMLElement?document.activeElement:null);
 useEffect(()=>{const viewport=window.visualViewport;if(!viewport||!surface)return;let frame=0;const fit=()=>{const node=surface;if(!node)return;node.style.setProperty('--settings-viewport-height',viewport.height+'px');node.style.setProperty('--settings-viewport-top','calc('+viewport.offsetTop+'px + max(16px, env(safe-area-inset-top)))');node.style.setProperty('--settings-viewport-bottom','auto');cancelAnimationFrame(frame);frame=requestAnimationFrame(()=>{const focused=document.activeElement;if(window.matchMedia('(max-width:700px)').matches&&focused instanceof HTMLElement&&node.contains(focused)&&focused.matches('input,textarea,select,[contenteditable=true]'))focused.scrollIntoView?.({block:'nearest',inline:'nearest'})})};fit();viewport.addEventListener('resize',fit);viewport.addEventListener('scroll',fit);return()=>{cancelAnimationFrame(frame);viewport.removeEventListener('resize',fit);viewport.removeEventListener('scroll',fit)}},[surface]);
 const [dirty,setDirty]=useState(false),revision=useRef(0),dirtyNow=useRef(false),edits=useRef(new Map<Element,number>()),action=useRef<Element|null>(null);dirtyNow.current=dirty;
 const reset=()=>{edits.current.clear();dirtyNow.current=false;setDirty(false)};
 const discard=useCallback(()=>{for(const node of edits.current.keys())if(!node.isConnected)edits.current.delete(node);if(!edits.current.size){dirtyNow.current=false;setDirty(false);return true}return window.confirm('Discard unsaved settings changes?')},[]);
 const beginSave=useCallback(()=>{const scope=action.current,snapshot=new Map([...edits.current].filter(([node])=>scope?.contains(node)));return()=>{for(const [node,version] of snapshot)if(edits.current.get(node)===version)edits.current.delete(node);for(const node of edits.current.keys())if(!node.isConnected)edits.current.delete(node);dirtyNow.current=!!edits.current.size;setDirty(dirtyNow.current)}},[]);
 useEffect(()=>{const leave=(event:BeforeUnloadEvent)=>{if(dirtyNow.current){event.preventDefault();event.returnValue=''}};const guard=(event:Event)=>{if(!discard())event.preventDefault()};window.addEventListener('beforeunload',leave);window.addEventListener('termx-settings-leave',guard);return()=>{window.removeEventListener('beforeunload',leave);window.removeEventListener('termx-settings-leave',guard)}},[discard]);
 // Keep category content mounted until a guarded route transition is accepted.
 useEffect(()=>{revision.current++;reset()},[category,scopeKey]);
 function select(id:string){if(id===category)return;if(discard()){reset();onSelect(id)}}
 return <Dialog open onOpenChange={open=>{if(!open&&discard()){reset();onClose()}}}><DialogContent ref={setSurface} className="settings-surface" onCloseAutoFocus={event=>{if(!opener.current?.isConnected){const target=document.querySelector<HTMLElement>('[aria-label="Show sidebar"]');if(target){event.preventDefault();target.focus()}}}} onPointerDownOutside={event=>event.preventDefault()}>
  <header className="settings-title"><DialogTitle>Settings</DialogTitle><DialogDescription>Configure your account, this host and workspace capabilities.</DialogDescription>{context}</header>
  <SettingsNavigation category={category} onSelect={select}/>
  <SaveContext.Provider value={beginSave}><div className="settings-content" onClickCapture={event=>{const target=event.target as HTMLElement;action.current=target.closest('form,.manager-form,.creation-form,.safety-config')||target.closest('section')}} onSubmitCapture={event=>{action.current=event.target as HTMLElement}} onChangeCapture={event=>{const target=event.target as HTMLElement;if(target.closest('[aria-label="Search settings"], [data-settings-filter], [data-settings-autosave], [aria-label="Theme preference"]'))return;revision.current++;const scope=target.closest('form,.manager-form,.creation-form,.safety-config')||target.closest('section')||target;edits.current.set(scope,revision.current);dirtyNow.current=true;setDirty(true)}}><SettingsActions>{children}</SettingsActions></div></SaveContext.Provider>
  {dirty&&<span className="settings-draft-status" role="status">Unsaved settings changes</span>}
 </DialogContent></Dialog>
}
