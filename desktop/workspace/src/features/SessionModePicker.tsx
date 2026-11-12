import {useEffect,useId,useLayoutEffect,useRef,useState} from 'react';
import {createPortal} from 'react-dom';
import {Check,ChevronDown,ClipboardList,MessageCircle,Workflow} from 'lucide-react';
import {Button} from '../components/ui/button';
import {type Session} from '../lib/api';
import {AUTO_CEILING_DEFAULTS,autoBlockedReason,planBlockedReason,type AutoSetting} from '../lib/agentic';

const modes=[
 {id:'ask',name:'Ask',description:'Explore ideas, understand code, and plan your next move.',icon:MessageCircle},
 {id:'plan',name:'Plan',description:'Inspect the code and propose a plan before editing files.',icon:ClipboardList},
 {id:'agent',name:'Agent',description:'Work through a task using the tools available to this agent.',icon:Workflow},
] as const;

export default function SessionModePicker({session,running,onPatch,auto,onAutoChange,visible=true}:{session:Session|null;running:boolean;onPatch:(changes:Record<string,unknown>)=>void;auto?:AutoSetting;onAutoChange?:(next:AutoSetting)=>void;visible?:boolean}){
 const [open,setOpen]=useState(false);
 const shown=open&&visible;
 const root=useRef<HTMLSpanElement>(null),trigger=useRef<HTMLButtonElement>(null),popover=useRef<HTMLDivElement>(null),popoverId=useId(),autoId=useId(),planReasonId=useId();
 // The composer clips its children (overflow:hidden), so the popover is portalled and placed above the chip with fixed coordinates.
 const [place,setPlace]=useState<{bottom:number;left:number;maxHeight?:number}>({bottom:0,left:0});
 useLayoutEffect(()=>{
  if(!shown){
   if(!visible&&popover.current?.contains(document.activeElement)&&document.activeElement instanceof HTMLElement){
    document.activeElement.blur();
    // A disabled field can retain focus in some hosts. Park it outside the hidden portal without stealing an existing Studio focus.
    if(popover.current.contains(document.activeElement)){const body=document.body,tabIndex=body.getAttribute('tabindex');body.tabIndex=-1;body.focus({preventScroll:true});if(tabIndex===null)body.removeAttribute('tabindex');else body.setAttribute('tabindex',tabIndex)}
   }
   return;
  }
  // The list never grows past the room above the chip, so on a short screen it scrolls instead of running off the top.
  const move=()=>{const bounds=trigger.current?.getBoundingClientRect();if(!bounds)return;const bottom=Math.max(8,window.innerHeight-bounds.top+6);setPlace({bottom,left:Math.max(16,Math.min(bounds.left,window.innerWidth-336)),maxHeight:Math.max(120,Math.min(bounds.top-14,560,window.innerHeight*.7))})};
  move();window.addEventListener('resize',move);window.addEventListener('scroll',move,true);
  return()=>{window.removeEventListener('resize',move);window.removeEventListener('scroll',move,true)};
 },[shown,visible]);
 // Closing when the conversation changes is the parent's job: Chat keys this component by session.
 useEffect(()=>{
  if(!shown)return;
  const outside=(event:PointerEvent)=>{const target=event.target as Node;if(!root.current?.contains(target)&&!popover.current?.contains(target))setOpen(false)};
  const escape=(event:KeyboardEvent)=>{if(event.key==='Escape'){event.stopPropagation();setOpen(false);trigger.current?.focus()}};
  document.addEventListener('pointerdown',outside);document.addEventListener('keydown',escape);
  // Focus lands on the mode in force, so arrow keys start from it; the first enabled mode is only the fallback.
  (popover.current?.querySelector<HTMLInputElement>('input[type=radio]:checked:not(:disabled)')??popover.current?.querySelector<HTMLInputElement>('input[type=radio]:not(:disabled)'))?.focus();
  return()=>{document.removeEventListener('pointerdown',outside);document.removeEventListener('keydown',escape)};
 },[shown]);
 const selected=modes.find(mode=>mode.id===session?.mode)||modes[0],Icon=selected.icon;
 const planReason=planBlockedReason(session),autoReason=autoBlockedReason(session);
 // The chip tells the truth about the next turn: Chat sends no `auto` when the conversation cannot run it.
 const autoOn=selected.id==='agent'&&!!auto?.enabled&&!autoReason;
 return <span className="mode-picker" ref={root}>
  <Button ref={trigger} className={'composer-mode-chip'+(autoOn?' is-auto':'')} variant="ghost" size="sm" aria-label="Session mode" aria-haspopup="true" aria-expanded={shown} aria-controls={shown?popoverId:undefined} disabled={!session||!visible} onClick={()=>setOpen(value=>!value)}><Icon size={16}/><span>{selected.name}{autoOn&&' · Auto'}</span><ChevronDown size={16}/></Button>
  {open&&createPortal(<div className="mode-popover" id={popoverId} ref={popover} style={place} hidden={!visible} inert={!visible}>
   <div className="mode-options" role="radiogroup" aria-label="Session mode">{modes.map(mode=>{const ModeIcon=mode.icon,blocked=mode.id==='plan'&&!!planReason;return <label className={'mode-option '+(selected.id===mode.id?'selected':'')} key={mode.id}><input type="radio" name="session-mode" value={mode.id} checked={selected.id===mode.id} disabled={!visible||running||blocked} aria-describedby={blocked?planReasonId:undefined} onChange={()=>onPatch({mode:mode.id})}/><ModeIcon size={16}/><span><strong>{mode.name}</strong><small>{mode.description}</small></span>{selected.id===mode.id&&<Check size={16}/>}</label>})}</div>
   {planReason&&<p className="control-running-note" id={planReasonId}>{planReason}</p>}
   {running&&<p className="control-running-note" role="status">Change mode after the current task finishes.</p>}
   {selected.id==='agent'&&auto&&onAutoChange&&<div className="mode-auto">
    <label className="mode-auto-row"><span><strong id={autoId+'-name'}>Auto</strong><small id={autoId+'-help'}>Approves low-risk tool calls without asking. Risky actions still ask.</small></span><input type="checkbox" role="switch" checked={auto.enabled} disabled={!visible||!!autoReason&&!auto.enabled} aria-labelledby={autoId+'-name'} aria-describedby={autoId+'-help'+(autoReason?' '+autoId+'-reason':'')} onChange={event=>onAutoChange({enabled:event.target.checked,ceilings:{...AUTO_CEILING_DEFAULTS}})}/></label>
    {autoReason&&<p className="control-running-note" id={autoId+'-reason'}>{autoReason}</p>}
   </div>}
  </div>,trigger.current?.closest('.workspace-shell')||document.body)}
 </span>;
}
