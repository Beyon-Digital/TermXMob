import {useEffect,useRef,useState} from 'react';
import {Button} from '../components/ui/button';
import {Dialog,DialogTrigger,DialogContent,DialogTitle,DialogDescription} from '../components/ui/dialog';
import {Input} from '../components/ui/input';
import {request,json,type Task} from '../lib/api';
type Props={task:Task;approval:NonNullable<Task['approvals']>[number];onDone:()=>void;onError:(error:unknown)=>void};
type Shared={root_task_id:string;used_steps:number;max_steps:number;used_execution_seconds:number;max_execution_seconds:number;version:number};
export default function BudgetApproval({task,approval,onDone,onError}:Props){
 const initial=()=>({...task.limits,...approval.payload.suggested_limits as Record<string,number>});
 const [limits,setLimits]=useState<Record<string,number>>(initial),[busy,setBusy]=useState(false),[open,setOpen]=useState(false),[locked,setLocked]=useState(false);
 const identity=task.id+':'+approval.id,current=useRef(identity),generation=useRef(0),pending=useRef(false);
 current.current=identity;
 useEffect(()=>{generation.current++;pending.current=false;setBusy(false);setOpen(false);setLimits(initial());return()=>{generation.current++}},[identity]);
 useEffect(()=>{const lock=()=>{generation.current++;pending.current=false;setBusy(false);setOpen(false);setLocked(true)},unlock=()=>setLocked(false);window.addEventListener('termx-locked',lock);window.addEventListener('termx-signed-out',lock);window.addEventListener('termx-session-unlocked',unlock);return()=>{window.removeEventListener('termx-locked',lock);window.removeEventListener('termx-signed-out',lock);window.removeEventListener('termx-session-unlocked',unlock)}},[]);
 const shared=approval.payload.tree_budget as Shared|undefined,reuse=approval.payload.reuse_grant===true;
 async function resolve(decision:string){
  if(pending.current||locked)return;
  const origin=current.current,epoch=generation.current;
  pending.current=true;setBusy(true);
  try{await request('/api/workspace/tasks/'+task.id+'/approvals/'+approval.id+'/resolve',json('POST',{decision,limits:decision==='approved'&&!reuse?limits:undefined}));if(origin===current.current&&epoch===generation.current)onDone()}
  catch(error){if(origin===current.current&&epoch===generation.current)onError(error)}
  finally{if(origin===current.current&&epoch===generation.current){pending.current=false;setBusy(false)}}
 }
 return <section className="approval" aria-label="Budget extension"><h4>{reuse?'Continue within the approved shared budget':shared?'Shared task tree budget exhausted':'Run budget exhausted'}</h4><Dialog open={open} onOpenChange={setOpen}><DialogTrigger asChild><Button disabled={locked}>{reuse?'Review approved continuation':'Review budget extension'}</Button></DialogTrigger><DialogContent className="approval-review-dialog"><DialogTitle>{reuse?'Continue within the approved shared budget':shared?'Shared task tree budget exhausted':'Run budget exhausted'}</DialogTitle><DialogDescription>Review this exact pending grant before allowing execution to continue.</DialogDescription>
  <p>{reuse?'The host stopped after this grant was approved. Confirm continuation within that same allowance; this does not reset time or increase the reserved-call ceiling.':shared?'Review the total parent/child tool-call ceiling and fresh summed active execution seconds. Completed effects and reserved calls remain. Each paused task needs an explicit continue decision within the same shared grant.':'Review the total budget for this task before allowing more work.'}</p>
  {shared&&<p>Root {shared.root_task_id} · grant version {shared.version} · {shared.used_steps} / {shared.max_steps} reserved calls · {shared.used_execution_seconds.toFixed(1)} / {shared.max_execution_seconds} active seconds. Human approval waits do not consume this shared time allowance.</p>}
  {!reuse&&Object.entries(limits).map(([key,value])=><label key={key}>{shared&&key==='max_seconds'?'Fresh shared active seconds':shared&&key==='max_steps'?'Total shared tool-call ceiling':key.replaceAll('_',' ')}<Input aria-label={'Extended '+key} type="number" min={shared&&key==='max_steps'?shared.used_steps+1:1} value={value} onChange={event=>setLimits(old=>({...old,[key]:Number(event.target.value)}))}/></label>)}
  <Button disabled={busy||locked} onClick={()=>resolve('approved')}>{reuse?'Continue within approved budget':'Approve budget extension'}</Button><Button variant="outline" disabled={busy||locked} onClick={()=>resolve('denied')}>Stop at current budget</Button>
 </DialogContent></Dialog></section>
}
