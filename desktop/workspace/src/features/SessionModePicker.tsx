import {useEffect,useId,useLayoutEffect,useRef,useState} from 'react';
import {createPortal} from 'react-dom';
import {Check,ChevronDown,ClipboardList,MessageCircle,Workflow} from 'lucide-react';
import {Button} from '../components/ui/button';
import {Collapsible,CollapsibleContent,CollapsibleTrigger} from '../components/ui/collapsible';
import {FormField} from '../components/ui/form-field';
import {Input} from '../components/ui/input';
import {type Session} from '../lib/api';
import {AUTO_CEILING_FIELDS,autoBlockedReason,planBlockedReason,type AutoCeilingKey,type AutoSetting} from '../lib/agentic';

const modes=[
 {id:'ask',name:'Ask',description:'Explore ideas, understand code, and plan your next move.',icon:MessageCircle},
 {id:'plan',name:'Plan',description:'Read the code, then propose a plan for you to approve before anything changes.',icon:ClipboardList},
 {id:'agent',name:'Agent',description:'Work through a task using the tools available to this agent.',icon:Workflow},
] as const;

function limitProblem(key:AutoCeilingKey,text:string){
 const value=Number(text);
 if(text.trim()===''||!Number.isFinite(value)||value<=0)return key==='cost_usd'?'Enter an amount above zero.':'Enter a whole number above zero.';
 return key!=='cost_usd'&&!Number.isInteger(value)?'Enter a whole number above zero.':'';
}

// A limit is sent when the field loses focus or Enter is pressed, not on every keystroke, so typing 200 is one change.
// Closing the popover (Escape, a click outside) unmounts the fields first, so any valid draft is sent as they go.
// Once committed, the draft is dropped and the field follows the value Chat holds, which reverts if the host refuses it.
function AutoLimits({auto,onChange}:{auto:AutoSetting;onChange:(next:AutoSetting)=>void}){
 const [drafts,setDrafts]=useState<Partial<Record<AutoCeilingKey,string>>>({});
 // The refs mirror state so a commit and an unmount in the same event never send a value twice.
 const pending=useRef<Partial<Record<AutoCeilingKey,string>>>({}),held=useRef({auto,onChange});held.current={auto,onChange};
 function edit(next:Partial<Record<AutoCeilingKey,string>>){pending.current=next;setDrafts(next)}
 function send(keys:AutoCeilingKey[]){
  const {auto:current,onChange:emit}=held.current,ceilings={...current.ceilings};let changed=false;
  for(const key of keys){
   const text=pending.current[key];if(text===undefined||limitProblem(key,text))continue;
   const {[key]:_sent,...rest}=pending.current;pending.current=rest;
   if(Number(text)!==ceilings[key]){ceilings[key]=Number(text);changed=true}
  }
  setDrafts(pending.current);
  if(changed)emit({...current,ceilings});
 }
 useEffect(()=>()=>{
  const {auto:current,onChange:emit}=held.current,ceilings={...current.ceilings};let changed=false;
  for(const field of AUTO_CEILING_FIELDS){const text=pending.current[field.key];if(text!==undefined&&!limitProblem(field.key,text)&&Number(text)!==ceilings[field.key]){ceilings[field.key]=Number(text);changed=true}}
  pending.current={};if(changed)emit({...current,ceilings});
 },[]);
 return <div className="mode-auto-limits">
  {AUTO_CEILING_FIELDS.map(field=>{const text=drafts[field.key]??String(auto.ceilings[field.key]),problem=drafts[field.key]===undefined?'':limitProblem(field.key,text);
   return <FormField key={field.key} label={field.label} hint={field.hint} error={problem}>{control=>
    <Input {...control} type="number" inputMode="decimal" min={field.key==='cost_usd'?0.01:1} step={field.step} value={text} onChange={event=>edit({...pending.current,[field.key]:event.target.value})} onBlur={()=>send([field.key])} onKeyDown={event=>{if(event.key==='Enter'){event.preventDefault();send([field.key])}}}/>}</FormField>})}
  <p className="mode-auto-note">Auto stops and asks you when any limit is reached.</p>
 </div>;
}

export default function SessionModePicker({session,running,onPatch,auto,onAutoChange}:{session:Session|null;running:boolean;onPatch:(changes:Record<string,unknown>)=>void;auto?:AutoSetting;onAutoChange?:(next:AutoSetting)=>void}){
 const [open,setOpen]=useState(false);
 const root=useRef<HTMLSpanElement>(null),trigger=useRef<HTMLButtonElement>(null),popover=useRef<HTMLDivElement>(null),popoverId=useId(),autoId=useId(),planReasonId=useId();
 // The composer clips its children (overflow:hidden), so the popover is portalled and placed above the chip with fixed coordinates.
 const [place,setPlace]=useState<{bottom:number;left:number}>({bottom:0,left:0});
 useLayoutEffect(()=>{
  if(!open)return;
  const move=()=>{const bounds=trigger.current?.getBoundingClientRect();if(!bounds)return;setPlace({bottom:Math.max(8,window.innerHeight-bounds.top+6),left:Math.max(16,Math.min(bounds.left,window.innerWidth-336))})};
  move();window.addEventListener('resize',move);window.addEventListener('scroll',move,true);
  return()=>{window.removeEventListener('resize',move);window.removeEventListener('scroll',move,true)};
 },[open]);
 // Closing when the conversation changes is the parent's job: Chat keys this component by session.
 useEffect(()=>{
  if(!open)return;
  const outside=(event:PointerEvent)=>{const target=event.target as Node;if(!root.current?.contains(target)&&!popover.current?.contains(target))setOpen(false)};
  const escape=(event:KeyboardEvent)=>{if(event.key==='Escape'){event.stopPropagation();setOpen(false);trigger.current?.focus()}};
  document.addEventListener('pointerdown',outside);document.addEventListener('keydown',escape);
  popover.current?.querySelector<HTMLInputElement>('input[type=radio]:checked:not(:disabled), input[type=radio]:not(:disabled)')?.focus();
  return()=>{document.removeEventListener('pointerdown',outside);document.removeEventListener('keydown',escape)};
 },[open]);
 const selected=modes.find(mode=>mode.id===session?.mode)||modes[0],Icon=selected.icon;
 const planReason=planBlockedReason(session),autoReason=autoBlockedReason(session);
 // The chip tells the truth about the next turn: Chat sends no `auto` when the conversation cannot run it.
 const autoOn=selected.id==='agent'&&!!auto?.enabled&&!autoReason;
 return <span className="mode-picker" ref={root}>
  <Button ref={trigger} className={'composer-mode-chip'+(autoOn?' is-auto':'')} variant="ghost" size="sm" aria-label="Session mode" aria-haspopup="true" aria-expanded={open} aria-controls={open?popoverId:undefined} disabled={!session} onClick={()=>setOpen(value=>!value)}><Icon size={14}/><span>{selected.name}{autoOn&&' · Auto'}</span><ChevronDown size={12}/></Button>
  {open&&createPortal(<div className="mode-popover" id={popoverId} ref={popover} style={place}>
   <div className="mode-options" role="radiogroup" aria-label="Session mode">{modes.map(mode=>{const ModeIcon=mode.icon,blocked=mode.id==='plan'&&!!planReason;return <label className={'mode-option '+(selected.id===mode.id?'selected':'')} key={mode.id}><input type="radio" name="session-mode" value={mode.id} checked={selected.id===mode.id} disabled={running||blocked} aria-describedby={blocked?planReasonId:undefined} onChange={()=>onPatch({mode:mode.id})}/><ModeIcon size={18}/><span><strong>{mode.name}</strong><small>{mode.description}</small></span>{selected.id===mode.id&&<Check size={16}/>}</label>})}</div>
   {planReason&&<p className="control-running-note" id={planReasonId}>{planReason}</p>}
   {running&&<p className="control-running-note" role="status">Change mode after the current task finishes.</p>}
   {selected.id==='agent'&&auto&&onAutoChange&&<div className="mode-auto">
    <label className="mode-auto-row"><span><strong id={autoId+'-name'}>Auto</strong><small id={autoId+'-help'}>Approves low-risk tool calls without asking. Risky actions still ask.</small></span><input type="checkbox" role="switch" checked={auto.enabled} disabled={!!autoReason&&!auto.enabled} aria-labelledby={autoId+'-name'} aria-describedby={autoId+'-help'+(autoReason?' '+autoId+'-reason':'')} onChange={event=>onAutoChange({...auto,enabled:event.target.checked})}/></label>
    {autoReason&&<p className="control-running-note" id={autoId+'-reason'}>{autoReason}</p>}
    {!autoReason&&<Collapsible><CollapsibleTrigger asChild><Button className="mode-auto-limits-toggle" variant="ghost" size="sm">Auto limits<ChevronDown size={12}/></Button></CollapsibleTrigger><CollapsibleContent><AutoLimits auto={auto} onChange={onAutoChange}/></CollapsibleContent></Collapsible>}
   </div>}
  </div>,trigger.current?.closest('.workspace-shell')||document.body)}
 </span>;
}
