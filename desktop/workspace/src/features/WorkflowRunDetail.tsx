import {useEffect, useRef, useState} from 'react';
import {ArrowLeft, Check, ChevronRight, Circle, CircleAlert, LoaderCircle, Square} from 'lucide-react';
import {Button} from '../components/ui/button';
import {Collapsible, CollapsibleContent, CollapsibleTrigger} from '../components/ui/collapsible';
import {json, request} from '../lib/api';
import {usePolling} from '../lib/use-polling';
import {workflowAccessLost, workflowError, workflowStatus, type WorkflowRun} from '../lib/workflows';
import {StructuredToolResult} from './ContentPreview';

type Props={runId:string;ownerId:string;projectId:string|null;visible:boolean;onBack:()=>void;backLabel?:string;onOpenSession:(id:string)=>void;onAgain:(run:WorkflowRun)=>void;onAccessLost:(reason:unknown)=>void};
const date=(value?:number|null)=>value?new Date(value*1000).toLocaleString():'—';
function Status({status}:{status:string}){const state=workflowStatus(status);return <span className={'workflow-state '+state.tone}>{status==='running'||status==='cancelling'?<LoaderCircle size={16} className="spin" aria-hidden="true"/>:status==='completed'?<Check size={16} aria-hidden="true"/>:state.tone==='failed'||state.tone==='attention'?<CircleAlert size={16} aria-hidden="true"/>:<Circle size={16} aria-hidden="true"/>}{state.label}</span>}

export default function WorkflowRunDetail({runId,ownerId,projectId,visible,onBack,backLabel='History',onOpenSession,onAgain,onAccessLost}:Props){
 const scope=ownerId+':'+(projectId??'')+':'+runId;
 const [saved,setSaved]=useState<{scope:string;run:WorkflowRun}|null>(null),[error,setError]=useState(''),[busy,setBusy]=useState(false),[refresh,setRefresh]=useState(0);
 const identity=useRef(scope),epoch=useRef(0),flight=useRef(false),live=useRef(visible);live.current=visible;
 if(identity.current!==scope){identity.current=scope;epoch.current++;flight.current=false}
 const run=saved?.scope===scope?saved.run:null;
 useEffect(()=>{setSaved(null);setError('');setBusy(false)},[scope]);
 useEffect(()=>{if(!visible){epoch.current++;flight.current=false;setBusy(false)}},[visible]);
 useEffect(()=>()=>{epoch.current++},[]);
 usePolling(signal=>request<WorkflowRun>('/api/workspace/workflows/runs/'+encodeURIComponent(runId),{signal}),{enabled:visible,interval:1500,maxInterval:6000,resetKey:scope+':'+refresh,onResult:value=>{setSaved(previous=>previous?.scope===scope&&previous.run.status==='cancelling'&&!workflowStatus(value.status).terminal&&value.status!=='cancelling'?previous:{scope,run:value});setError('')},onError:reason=>{if(workflowAccessLost(reason)){setSaved(null);onAccessLost(reason)}else setError(workflowError(reason))}});
 async function cancel(){
  if(!visible||flight.current||!run?.allowed_actions.includes('cancel'))return;flight.current=true;setBusy(true);setError('');const current=epoch.current;
  try{const value=await request<WorkflowRun>('/api/workspace/workflows/runs/'+encodeURIComponent(runId)+'/cancel',json('POST',{}));if(current===epoch.current&&live.current){setSaved({scope,run:value});setRefresh(value=>value+1)}}
  catch(reason){if(current===epoch.current&&live.current){if(workflowAccessLost(reason)){setSaved(null);onAccessLost(reason)}else setError(workflowError(reason))}}
  finally{if(current===epoch.current){flight.current=false;setBusy(false)}}
 }
 return <section className="workflow-run-detail" aria-label="Workflow run">
  <Button variant="ghost" className="workflow-back" onClick={onBack}><ArrowLeft size={16} aria-hidden="true"/>{backLabel}</Button>
  {error&&<div className="workflow-error" role="alert"><span>{error}</span><Button variant="ghost" onClick={()=>setRefresh(value=>value+1)}>Retry</Button></div>}
  {!run&&!error&&<p className="workflow-loading" role="status">Loading run…</p>}
  {run&&<><header className="workflow-detail-heading"><div><h1>{run.name}</h1><div className="workflow-run-meta"><Status status={run.status}/><span>{date(run.created_at)}</span>{run.workflow_revision&&<span>Version {run.workflow_revision}</span>}</div></div><div className="workflow-actions">{run.allowed_actions.includes('cancel')&&<Button variant="outline" disabled={busy||run.status==='cancelling'} onClick={()=>void cancel()}><Square size={16} aria-hidden="true"/>{busy||run.status==='cancelling'?'Stopping…':'Stop'}</Button>}{workflowStatus(run.status).terminal&&run.allowed_actions.includes('retry')&&<Button onClick={()=>onAgain(run)}>Run again</Button>}</div></header>
  {run.error&&<p className="workflow-error" role="alert">{workflowError(run.error)}</p>}
  {run.status==='needs_attention'&&<p className="workflow-attention">This run was interrupted. Review its activity before starting another run.</p>}
  {run.calls_truncated&&<p className="workflow-activity-meta">Recent activity{typeof run.call_count==='number'?` · ${run.calls.length} of ${run.call_count} entries`:null}</p>}
  <ol className="workflow-calls" aria-label="Workflow activity">{run.calls.map(call=><li key={call.id}><Collapsible className="workflow-call"><CollapsibleTrigger className="workflow-call-trigger"><span className="workflow-call-name">{call.name||call.operation}</span><Status status={call.status}/><ChevronRight size={16} className="disclosure-chevron" aria-hidden="true"/></CollapsibleTrigger><CollapsibleContent className="workflow-call-content">{call.error&&<p className="workflow-error" role="alert">{workflowError(call.error)}</p>}{call.output!==undefined&&call.output!==null&&<StructuredToolResult value={call.output} taskId={call.task_id||undefined} kind="workflow" complete={workflowStatus(call.status).terminal} visible={visible}/>}<div className="workflow-call-meta">{call.started_at&&<span>{date(call.started_at)}</span>}{call.session_id&&<Button variant="ghost" onClick={()=>onOpenSession(call.session_id!)}>{call.status==='awaiting_approval'?'Review approval':'Open agent'}</Button>}</div></CollapsibleContent></Collapsible>{call.status==='awaiting_approval'&&call.session_id&&<div className="workflow-call-approval"><span>Agent approval needed</span><Button variant="outline" onClick={()=>onOpenSession(call.session_id!)}>Review</Button></div>}</li>)}</ol>
  {!run.calls.length&&<p className="workflow-empty-inline">{workflowStatus(run.status).active?'Waiting for workflow activity.':'No agent calls were made.'}</p>}
  {run.result!==undefined&&run.result!==null&&<section className="workflow-result"><h2>Result</h2><StructuredToolResult value={run.result} kind="workflow" complete={workflowStatus(run.status).terminal} visible={visible}/></section>}
  <details className="workflow-advanced"><summary>Inputs</summary><StructuredToolResult value={run.args} kind="workflow" visible={visible}/></details>
  {run.snapshot&&<details className="workflow-advanced"><summary>Saved source</summary><pre className="workflow-saved-source" tabIndex={0} aria-label="Saved workflow source">{run.snapshot.source}</pre></details>}
  </>}
 </section>;
}
