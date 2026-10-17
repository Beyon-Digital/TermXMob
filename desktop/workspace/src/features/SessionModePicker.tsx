import {useEffect,useState} from 'react';
import {Check,ChevronDown,MessageCircle,Workflow} from 'lucide-react';
import {Button} from '../components/ui/button';
import {Dialog,DialogContent,DialogDescription,DialogTitle} from '../components/ui/dialog';
import {type Session} from '../lib/api';

const modes=[{id:'ask',name:'Ask',description:'Explore ideas, understand code, and plan your next move.',icon:MessageCircle},{id:'agent',name:'Agent',description:'Work through a task using the tools available to this agent.',icon:Workflow}] as const;

export default function SessionModePicker({session,running,onPatch}:{session:Session|null;running:boolean;onPatch:(changes:Record<string,unknown>)=>void}){
 const [open,setOpen]=useState(false);
 useEffect(()=>setOpen(false),[session?.id]);
 const selected=modes.find(mode=>mode.id===session?.mode)||modes[0],Icon=selected.icon;
 return <>
  <Button className="composer-mode-chip" variant="ghost" size="sm" aria-label="Session mode" aria-haspopup="dialog" aria-expanded={open} disabled={!session} onClick={()=>setOpen(true)}><Icon size={14}/><span>{selected.name}</span><ChevronDown size={12}/></Button>
  <Dialog open={open} onOpenChange={setOpen}><DialogContent className="chat-control-dialog mode-dialog"><DialogTitle>Choose a mode</DialogTitle><DialogDescription>Set the direction for your next turn.</DialogDescription>
   <div className="mode-options" role="radiogroup" aria-label="Session mode">{modes.map(mode=>{const ModeIcon=mode.icon;return <label className={'mode-option '+(selected.id===mode.id?'selected':'')} key={mode.id}><input type="radio" name="session-mode" value={mode.id} checked={selected.id===mode.id} disabled={running} onChange={()=>{onPatch({mode:mode.id});setOpen(false)}}/><ModeIcon size={20}/><span><strong>{mode.name}</strong><small>{mode.description}</small></span>{selected.id===mode.id&&<Check size={16}/>}</label>})}</div>
   {running&&<p className="control-running-note" role="status">Change mode after the current task finishes.</p>}
  </DialogContent></Dialog>
 </>;
}
