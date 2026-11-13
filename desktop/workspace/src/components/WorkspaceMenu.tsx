import {useCallback,useLayoutEffect,useRef,useState,type ReactNode,type KeyboardEvent} from 'react';
import * as Dialog from '@radix-ui/react-dialog';
import {MoreHorizontal} from 'lucide-react';
import {Button} from './ui/button';
import {useNavigationLayerRef} from './ui/navigation-layer';
import {observeClosedState} from '../lib/disclosure-motion';
import './workspace-menu.css';

/** A button-anchored command surface; Radix owns dismissal and focus scope. */
export default function WorkspaceMenu({label,children,icon}:{label:string;children:ReactNode;icon?:ReactNode}){
 const [open,setOpen]=useState(false),[position,setPosition]=useState({top:0,left:0});
 const trigger=useRef<HTMLButtonElement>(null),panel=useRef<HTMLDivElement>(null),sourceActive=useRef(true),observer=useRef<MutationObserver|null>(null),resize=useRef<ResizeObserver|null>(null);
 const layer=useNavigationLayerRef<HTMLDivElement>();
 const active=()=>!!trigger.current?.isConnected&&!trigger.current.closest('[hidden],[inert],[aria-hidden="true"]');
 const place=useCallback(()=>{
  const bounds=trigger.current?.getBoundingClientRect(),node=panel.current;if(!bounds||!node)return;
  const width=node.offsetWidth||244,height=node.offsetHeight||260;
  const below=bounds.bottom+8,above=bounds.top-height-8;
  setPosition({top:Math.max(8,Math.min(below+height<=window.innerHeight-8?below:above,window.innerHeight-height-8)),left:Math.max(8,Math.min(bounds.right-width,window.innerWidth-width-8))});
 },[]);
 const attach=useCallback((node:HTMLDivElement|null)=>{
  if(panel.current)resize.current?.unobserve(panel.current);
  observer.current?.disconnect();panel.current=node;layer(node);
  observer.current=node?observeClosedState(node,()=>{if(sourceActive.current&&active())trigger.current?.focus({preventScroll:true})}):null;
  // A Radix portal may attach after the open effect. Measure it before its
  // first paint instead of displaying a position based on fallback dimensions.
  if(node){place();resize.current?.observe(node)}
 },[layer,place]);
 useLayoutEffect(()=>{
  if(!open)return;
  place();window.addEventListener('resize',place);window.addEventListener('scroll',place,true);
  resize.current=typeof ResizeObserver==='undefined'?null:new ResizeObserver(place);
  if(trigger.current)resize.current?.observe(trigger.current);if(panel.current)resize.current?.observe(panel.current);
  return()=>{window.removeEventListener('resize',place);window.removeEventListener('scroll',place,true);resize.current?.disconnect();resize.current=null};
 },[open,place]);
 // Hiding a mounted source invalidates the portal and its focus return target.
 useLayoutEffect(()=>{sourceActive.current=active();if(open&&!sourceActive.current)setOpen(false)});
 const navigate=(event:KeyboardEvent<HTMLDivElement>)=>{
  if((event.target as HTMLElement).closest('select,input,textarea'))return;
  const controls=Array.from(panel.current?.querySelectorAll<HTMLButtonElement>('button:not(:disabled)')||[]).filter(node=>!node.closest('[hidden],[inert]'));
  if(!controls.length)return;
  const current=controls.indexOf(document.activeElement as HTMLButtonElement);
  const next=event.key==='ArrowDown'?(current+1)%controls.length:event.key==='ArrowUp'?(current-1+controls.length)%controls.length:event.key==='Home'?0:event.key==='End'?controls.length-1:null;
  if(next===null)return;event.preventDefault();controls[next].focus({preventScroll:true});controls[next].scrollIntoView?.({block:'nearest'});
 };
 return <Dialog.Root modal={false} open={open} onOpenChange={value=>{sourceActive.current=active();setOpen(value&&sourceActive.current)}}>
  <Dialog.Trigger asChild><Button ref={trigger} variant="ghost" size="icon" aria-label={label} title={label}>{icon||<MoreHorizontal size={16}/>}</Button></Dialog.Trigger>
  <Dialog.Portal container={trigger.current?.closest('.workspace-shell')||undefined}><Dialog.Content ref={attach} className="workspace-command-menu" style={position} aria-describedby={undefined} onOpenAutoFocus={event=>{event.preventDefault();(panel.current?.querySelector<HTMLElement>('button:not(:disabled),select:not(:disabled),input:not(:disabled)')??panel.current)?.focus({preventScroll:true})}} onCloseAutoFocus={event=>event.preventDefault()} onEscapeKeyDown={()=>{if(sourceActive.current&&active())trigger.current?.focus({preventScroll:true})}} onKeyDown={navigate} onClick={event=>{if((event.target as HTMLElement).closest('button[data-close-menu]')){if(sourceActive.current&&active()&&panel.current?.contains(document.activeElement))trigger.current?.focus({preventScroll:true});setOpen(false)}}}>
   <Dialog.Title className="workspace-menu-accessible-title">{label}</Dialog.Title>
   {children}
  </Dialog.Content></Dialog.Portal>
 </Dialog.Root>;
}
