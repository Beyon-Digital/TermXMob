import {useEffect,useRef,useState} from 'react';
import {Button} from '../components/ui/button';
import {Dialog,DialogTrigger,DialogContent,DialogTitle} from '../components/ui/dialog';
import {request,json,type Task} from '../lib/api';
type Props={task:Task;approval:NonNullable<Task['approvals']>[number];onDone:()=>void;onError:(error:unknown)=>void};
/** Older paused tasks keep their exact continuation decision after run quotas are removed. */
export default function BudgetApproval({task,approval,onDone,onError}:Props){
 const [busy,setBusy]=useState(false),[open,setOpen]=useState(false),[locked,setLocked]=useState(false);
 const identity=task.id+':'+approval.id,current=useRef(identity),generation=useRef(0),pending=useRef(false);current.current=identity;
 useEffect(()=>{generation.current++;pending.current=false;setBusy(false);setOpen(false);return()=>{generation.current++}},[identity]);
 useEffect(()=>{const lock=()=>{generation.current++;pending.current=false;setBusy(false);setOpen(false);setLocked(true)},unlock=()=>setLocked(false);window.addEventListener('termx-locked',lock);window.addEventListener('termx-signed-out',lock);window.addEventListener('termx-session-unlocked',unlock);return()=>{window.removeEventListener('termx-locked',lock);window.removeEventListener('termx-signed-out',lock);window.removeEventListener('termx-session-unlocked',unlock)}},[]);
 async function resolve(decision:'approved'|'denied'){
  if(pending.current||locked)return;const origin=current.current,epoch=generation.current;pending.current=true;setBusy(true);
  try{await request('/api/workspace/tasks/'+encodeURIComponent(task.id)+'/approvals/'+encodeURIComponent(approval.id)+'/resolve',json('POST',{decision}));if(origin===current.current&&epoch===generation.current)onDone()}
  catch(error){if(origin===current.current&&epoch===generation.current)onError(error)}
  finally{if(origin===current.current&&epoch===generation.current){pending.current=false;setBusy(false)}}
 }
 return <section className="approval" aria-label="Task continuation"><h4>Task paused</h4><Dialog open={open} onOpenChange={setOpen}><DialogTrigger asChild><Button disabled={locked}>Review continuation</Button></DialogTrigger><DialogContent className="approval-review-dialog" aria-describedby={undefined}><DialogTitle>Continue task</DialogTitle><p>{task.prompt}</p><div className="manager-item-actions"><Button disabled={busy||locked} onClick={()=>void resolve('approved')}>Continue task</Button><Button variant="outline" disabled={busy||locked} onClick={()=>void resolve('denied')}>Stop task</Button></div></DialogContent></Dialog></section>;
}
