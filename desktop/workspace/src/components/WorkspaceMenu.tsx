import {useLayoutEffect,useId,useRef,useState,type ReactNode} from 'react';
import {createPortal} from 'react-dom';
import {MoreHorizontal} from 'lucide-react';
import {Button} from './ui/button';
import './workspace-menu.css';

/** A small, keyboard-accessible command popover that can escape dock overflow. */
export default function WorkspaceMenu({label,children,icon}:{label:string;children:ReactNode;icon?:ReactNode}){
 const [open,setOpen]=useState(false),[position,setPosition]=useState({top:0,left:0});
 const trigger=useRef<HTMLButtonElement>(null),panel=useRef<HTMLDivElement>(null),id=useId();
 useLayoutEffect(()=>{
  if(!open)return;
  const place=()=>{const bounds=trigger.current?.getBoundingClientRect();if(!bounds)return;setPosition({top:Math.min(bounds.bottom+4,Math.max(8,window.innerHeight-(panel.current?.offsetHeight||260)-8)),left:Math.max(8,Math.min(bounds.right-244,window.innerWidth-252))})};
  place();panel.current?.querySelector<HTMLElement>('button:not(:disabled),select:not(:disabled),input:not(:disabled)')?.focus();
  const outside=(event:PointerEvent)=>{if(!panel.current?.contains(event.target as Node)&&!trigger.current?.contains(event.target as Node))setOpen(false)};
  const focus=(event:FocusEvent)=>{if(!panel.current?.contains(event.target as Node)&&!trigger.current?.contains(event.target as Node))setOpen(false)};
  const escape=(event:KeyboardEvent)=>{if(event.key==='Escape'){event.preventDefault();event.stopPropagation();setOpen(false);trigger.current?.focus()}};
  document.addEventListener('pointerdown',outside);document.addEventListener('focusin',focus);document.addEventListener('keydown',escape,true);window.addEventListener('resize',place);
  return()=>{document.removeEventListener('pointerdown',outside);document.removeEventListener('focusin',focus);document.removeEventListener('keydown',escape,true);window.removeEventListener('resize',place)};
 },[open]);
 return <><Button ref={trigger} variant="ghost" size="icon" aria-label={label} title={label} aria-expanded={open} aria-controls={open?id:undefined} aria-haspopup="dialog" onClick={()=>setOpen(value=>!value)}>{icon||<MoreHorizontal size={16}/>}</Button>{open&&createPortal(<div ref={panel} id={id} className="workspace-command-menu" role="dialog" aria-label={label} style={position} onClick={event=>{if((event.target as HTMLElement).closest('button[data-close-menu]')){setOpen(false);trigger.current?.focus()}}}>{children}</div>,trigger.current?.closest('.workspace-shell')||document.body)}</>
}
