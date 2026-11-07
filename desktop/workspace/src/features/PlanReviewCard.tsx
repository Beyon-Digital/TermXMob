import {useEffect,useRef,useState} from 'react';
import {Confirmation,ConfirmationRequest,ConfirmationTitle} from '../components/ai-elements/confirmation';
import {Button} from '../components/ui/button';
import {FormField} from '../components/ui/form-field';
import {Textarea} from '../components/ui/textarea';
import {resolvePlan} from '../lib/agentic';
import type {Task} from '../lib/api';
import ConversationResponse from './ConversationResponse';
import './plan-review-card.css';

type Approval=NonNullable<Task['approvals']>[number];
const planOf=(approval:Pick<Approval,'payload'>)=>(approval.payload.plan&&typeof approval.payload.plan==='object'?approval.payload.plan:{}) as {summary?:unknown;markdown?:unknown};
/** Plan-mode plans carry markdown and can be revised. One-shot Agent plans keep the general approval card. */
export function isRevisablePlan(approval:Pick<Approval,'kind'|'payload'>){
 return approval.kind==='plan'&&typeof planOf(approval).markdown==='string';
}
const MAX_FEEDBACK=10000;

export default function PlanReviewCard({taskId,approval,onDone,onError,onOpenFile}:{taskId:string;approval:Pick<Approval,'id'|'payload'>;onDone:()=>void;onError:(error:unknown)=>void;onOpenFile?:(path:string,line?:number)=>void}){
 const [pending,setPending]=useState<''|'approved'|'revise'>(''),[locked,setLocked]=useState(false),[revising,setRevising]=useState(false),[feedback,setFeedback]=useState(''),[problem,setProblem]=useState(''),[error,setError]=useState(''),[done,setDone]=useState('');
 // ConversationMessage keys this card by approval id, so a new approval is a fresh instance.
 const active=useRef({generation:0,busy:false}),box=useRef<HTMLTextAreaElement>(null),requestButton=useRef<HTMLButtonElement>(null),opened=useRef(false);
 useEffect(()=>{
  const lock=()=>{active.current.generation++;active.current.busy=false;setPending('');setLocked(true)},unlock=()=>setLocked(false);
  window.addEventListener('termx-locked',lock);window.addEventListener('termx-signed-out',lock);window.addEventListener('termx-session-unlocked',unlock);
  return()=>{active.current.generation++;window.removeEventListener('termx-locked',lock);window.removeEventListener('termx-signed-out',lock);window.removeEventListener('termx-session-unlocked',unlock)};
 },[]);
 useEffect(()=>{if(revising)box.current?.focus();else if(opened.current)requestButton.current?.focus()},[revising]);
 const plan=planOf(approval),markdown=typeof plan.markdown==='string'?plan.markdown:'';
 async function decide(decision:'approved'|'revise'){
  if(active.current.busy||locked||done)return;
  const text=feedback.trim();
  if(decision==='revise'&&!text){setProblem('Say what should change, or approve the plan as it is.');return}
  const generation=active.current.generation;active.current.busy=true;setPending(decision);setError('');setProblem('');
  try{
   await resolvePlan(taskId,approval.id,decision,decision==='revise'?text:undefined);
   if(generation===active.current.generation){setDone(decision==='approved'?'Plan approved. The agent is starting work.':'Feedback sent. The agent is revising the plan.');onDone()}
  }catch(failure){if(generation===active.current.generation){setError(failure instanceof Error?failure.message:'The decision could not be saved. Try again.');onError(failure)}}
  finally{if(generation===active.current.generation){active.current.busy=false;setPending('')}}
 }
 return <Confirmation className="approval plan-review" aria-label="Plan review" state="approval-requested" approval={{id:approval.id}}><ConfirmationRequest>
  <ConfirmationTitle>Plan ready for review</ConfirmationTitle>
  <div className="plan-review-body" role="region" tabIndex={0} aria-label="Proposed plan"><ConversationResponse mode="static" onOpenFile={onOpenFile}>{markdown}</ConversationResponse></div>
  {revising&&<form className="plan-review-feedback" onSubmit={event=>{event.preventDefault();void decide('revise')}}>
   <FormField label="What should change?" hint="The agent keeps what it learned and revises the plan." error={problem}>{control=>
    <Textarea {...control} ref={box} className="input" rows={4} maxLength={MAX_FEEDBACK} value={feedback} disabled={!!pending||!!done} onChange={event=>{setFeedback(event.target.value);if(problem)setProblem('')}}/>}</FormField>
   <div className="plan-review-actions"><Button type="submit" disabled={!!pending||locked||!!done}>{pending==='revise'?'Sending…':'Send feedback'}</Button><Button type="button" variant="ghost" disabled={!!pending||!!done} onClick={()=>{opened.current=true;setRevising(false);setProblem('')}}>Keep reviewing</Button></div>
  </form>}
  {error&&<p role="alert" className="inline-error">{error}</p>}
  <p role="status" className="plan-review-status">{done}</p>
  {!revising&&<div className="plan-review-actions"><Button disabled={!!pending||locked||!!done} onClick={()=>void decide('approved')}>{pending==='approved'?'Approving…':'Approve plan'}</Button><Button ref={requestButton} variant="outline" disabled={!!pending||locked||!!done} onClick={()=>{opened.current=true;setRevising(true)}}>Request changes</Button></div>}
 </ConfirmationRequest></Confirmation>
}
