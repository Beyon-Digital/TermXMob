import {useEffect,useId,useRef,useState,type ReactNode} from 'react';
import {Check,ChevronDown,MessageCircle,Workflow} from 'lucide-react';
import {Button} from '../components/ui/button';
import {type Session} from '../lib/api';

// Add a mode here (for example Plan) and it appears in the popover. `footer` is the slot for
// controls that belong to one mode, such as the Auto switch under Agent.
const modes=[{id:'ask',name:'Ask',description:'Explore ideas, understand code, and plan your next move.',icon:MessageCircle},{id:'agent',name:'Agent',description:'Work through a task using the tools available to this agent.',icon:Workflow}] as const;

export default function SessionModePicker({session,running,onPatch,footer}:{session:Session|null;running:boolean;onPatch:(changes:Record<string,unknown>)=>void;footer?:ReactNode}){
 const [open,setOpen]=useState(false);
 const root=useRef<HTMLSpanElement>(null),trigger=useRef<HTMLButtonElement>(null),popoverId=useId();
 // Closing when the conversation changes is the parent's job: Chat keys this component by session.
 useEffect(()=>{
  if(!open)return;
  const outside=(event:PointerEvent)=>{if(!root.current?.contains(event.target as Node))setOpen(false)};
  const escape=(event:KeyboardEvent)=>{if(event.key==='Escape'){event.stopPropagation();setOpen(false);trigger.current?.focus()}};
  document.addEventListener('pointerdown',outside);document.addEventListener('keydown',escape);
  root.current?.querySelector<HTMLInputElement>('input:checked:not(:disabled), input:not(:disabled)')?.focus();
  return()=>{document.removeEventListener('pointerdown',outside);document.removeEventListener('keydown',escape)};
 },[open]);
 const selected=modes.find(mode=>mode.id===session?.mode)||modes[0],Icon=selected.icon;
 return <span className="mode-picker" ref={root}>
  <Button ref={trigger} className="composer-mode-chip" variant="ghost" size="sm" aria-label="Session mode" aria-haspopup="true" aria-expanded={open} aria-controls={open?popoverId:undefined} disabled={!session} onClick={()=>setOpen(value=>!value)}><Icon size={14}/><span>{selected.name}</span><ChevronDown size={12}/></Button>
  {open&&<div className="mode-popover" id={popoverId}>
   <div className="mode-options" role="radiogroup" aria-label="Session mode">{modes.map(mode=>{const ModeIcon=mode.icon;return <label className={'mode-option '+(selected.id===mode.id?'selected':'')} key={mode.id}><input type="radio" name="session-mode" value={mode.id} checked={selected.id===mode.id} disabled={running} onChange={()=>{onPatch({mode:mode.id});setOpen(false);trigger.current?.focus()}}/><ModeIcon size={18}/><span><strong>{mode.name}</strong><small>{mode.description}</small></span>{selected.id===mode.id&&<Check size={16}/>}</label>})}</div>
   {running&&<p className="control-running-note" role="status">Change mode after the current task finishes.</p>}
   {footer}
  </div>}
 </span>;
}
