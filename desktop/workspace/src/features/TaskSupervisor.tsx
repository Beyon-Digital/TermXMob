import {Question,QuestionInput,QuestionSubmit,QuestionPrompt} from '../components/ai-elements/question';
import {useEffect,useRef,useState} from 'react';
import {usePolling} from '../lib/use-polling';
import {Button} from '../components/ui/button';
import {Input} from '../components/ui/input';
import {Select} from '../components/ui/select';
import {Dialog,DialogContent,DialogTitle,DialogDescription} from '../components/ui/dialog';
import {json,request,type Task} from '../lib/api';
import './supporting-panels.css';

type Run=Task&{tree_budget?:{root_task_id:string;max_steps:number;used_steps:number;max_execution_seconds:number;used_execution_seconds:number;active_workers:number};mode?:string;cwd?:string;parent_id?:string;runner_id?:string;execution_location?:string;agent_label?:string;delegated_task?:string;isolation?:{kind:string;path:string};truncated?:boolean;worktree?:{branch?:string;worktree_path?:string};children?:Run[]};
type Member={id:string;agent:string;task:string;instructions:string;mode:'ask'|'agent'};
type TeamResult={ok:boolean;members:{agent:string;ok:boolean;task_id?:string;error?:string}[];pending?:boolean;paused?:boolean;error?:string};
type TeamProgress={base:string;taskId:string;batches:Omit<Member,'id'>[][];index:number;members:TeamResult['members']};
const active=(task:Task)=>['queued','running','planning','awaiting_approval','paused','recovering','recovery_confirmation_required','cancelling'].includes(task.status);
const steerable=(task:Task)=>['running','awaiting_approval'].includes(task.status);
const flatten=(task:Run):Run[]=>[task,...(task.children||[]).flatMap(flatten)];
const newMember=(agent:string):Member=>({id:crypto.randomUUID(),agent,task:'',instructions:'',mode:'ask'});
const initialMembers=():Member[]=>[newMember('Researcher'),newMember('Reviewer')];

export default function TaskSupervisor({taskId,onClose,onError,visible=true}:{taskId:string;onClose:()=>void;onError:(error:unknown)=>void;visible?:boolean}){
 const [root,setRoot]=useState<Run|null>(null),[selected,setSelected]=useState(''),[message,setMessage]=useState(''),[confirmRecovery,setConfirmRecovery]=useState(false),[busy,setBusy]=useState(false),[creating,setCreating]=useState(false);
 const [loadError,setLoadError]=useState(''),[refresh,setRefresh]=useState(0),[building,setBuilding]=useState(false),[members,setMembers]=useState(initialMembers),[teamResult,setTeamResult]=useState<TeamResult|null>(null);
 const requestId=useRef(crypto.randomUUID()),currentTask=useRef(taskId),generation=useRef(0),mutation=useRef(false),teamMutation=useRef(false),teamProgress=useRef<TeamProgress|null>(null),visibleNow=useRef(visible);visibleNow.current=visible;
 if(currentTask.current!==taskId){currentTask.current=taskId;generation.current++;mutation.current=false;teamMutation.current=false;teamProgress.current=null}
 useEffect(()=>()=>{generation.current++},[]);
 useEffect(()=>{setBusy(false);setCreating(false);setLoadError('');setRoot(null);setSelected('');setMessage('');setConfirmRecovery(false);setBuilding(false);setMembers(initialMembers());setTeamResult(null);teamProgress.current=null;requestId.current=crypto.randomUUID()},[taskId]);
 usePolling(signal=>request<Run>('/api/workspace/tasks/'+encodeURIComponent(taskId),{signal}),{enabled:visible,interval:1500,maxInterval:6000,resetKey:taskId+':'+refresh,onResult:value=>{setRoot(value);setLoadError('')},onError:error=>setLoadError(error instanceof Error?error.message:'Unable to load the task tree')});
 const tasks=root?flatten(root):[],chosen=tasks.find(task=>task.id===selected)||root;
 const workers=root?.children||[],liveWorkers=workers.filter(active).length;
 const canBuild=!!root&&!root.parent_id&&!root.runner_id&&root.engine==='internal'&&root.mode!=='ask'&&['running','planning','awaiting_approval','paused'].includes(root.status);
 const validTeam=members.length>0&&members.every(member=>member.agent.trim()&&member.task.trim())&&new Set(members.map(member=>member.agent.trim().toLowerCase())).size===members.length;
 function editMember(index:number,changes:Partial<Member>){setMembers(old=>old.map((member,i)=>i===index?{...member,...changes}:member));requestId.current=crypto.randomUUID();teamProgress.current=null;setTeamResult(null)}
 async function control(task:Run,action:'cancel'|'steer'|'retry'|'recover'){
  if(!visible||mutation.current)return;const origin=generation.current;mutation.current=true;setBusy(true);
  try{await request('/api/workspace/tasks/'+encodeURIComponent(task.id)+'/'+action,json('POST',action==='steer'?{message:message.trim()}:action==='retry'?{request_id:crypto.randomUUID()}:action==='recover'?{confirm:confirmRecovery}:{}));if(generation.current===origin){if(action==='steer')setMessage('');setRefresh(value=>value+1)}}catch(error){if(generation.current===origin)onError(error)}finally{if(generation.current===origin){mutation.current=false;setBusy(false)}}
 }
 async function createTeam(resume=false){
  if(!visible||!root||!canBuild||(!resume&&(!validTeam||teamResult)))return;if(teamMutation.current)return;
  const origin=generation.current;
  let progress=resume?teamProgress.current:null;
  if(resume&&(!progress||progress.taskId!==root.id))return;
  if(!progress){const snapshot=members.map(({id:_id,...member})=>({...member,agent:member.agent.trim(),task:member.task.trim()}));progress={base:requestId.current,taskId:root.id,batches:[],index:0,members:[]};for(let index=0;index<snapshot.length;index+=8)progress.batches.push(snapshot.slice(index,index+8));teamProgress.current=progress}
  teamMutation.current=true;setCreating(true);setTeamResult(null);
  try{
   while(progress.index<progress.batches.length){
    if(generation.current!==origin)return;
    if(!visibleNow.current){setTeamResult({ok:false,paused:true,members:[...progress.members]});return}
    const batch=progress.batches[progress.index];
    const result=await request<TeamResult>('/api/workspace/tasks/'+encodeURIComponent(progress.taskId)+'/swarm',json('POST',{request_id:progress.base+':'+progress.index,members:batch}));
    if(generation.current!==origin)return;
    if(!result||typeof result.ok!=='boolean'||!Array.isArray(result.members)||result.ok&&result.members.length!==batch.length)throw new Error('The host did not confirm every worker in this batch.');
    progress.members.push(...result.members);progress.index++;setRefresh(value=>value+1);
    if(!result.ok||result.members.some(member=>!member.ok)){setTeamResult({ok:false,members:[...progress.members]});teamProgress.current=null;return}
   }
   setTeamResult({ok:true,members:[...progress.members]});teamProgress.current=null;
  }catch(error){if(generation.current===origin){setTeamResult({ok:false,pending:true,error:error instanceof Error?error.message:'The latest batch has no confirmed result.',members:[...progress.members]});setRefresh(value=>value+1);if(visibleNow.current)onError(error)}}
  finally{if(generation.current===origin){teamMutation.current=false;setCreating(false)}}
 }

 function tree(task:Run,depth=0):React.ReactNode{return <div key={task.id}><button className={'supervision-row '+(chosen?.id===task.id?'selected':'')} aria-pressed={chosen?.id===task.id} style={{paddingLeft:12+depth*16}} onClick={()=>{setSelected(task.id);setMessage('');setConfirmRecovery(false)}}><span className={'status-dot '+task.status} aria-hidden="true"/><strong title={task.delegated_task||task.prompt}>{task.agent_label?task.agent_label+' · ':''}{(task.delegated_task||task.prompt).slice(0,100)}</strong><small>{task.status.replaceAll('_',' ')}</small></button>{task.truncated&&<p role="status">More tasks exist than this view can display.</p>}{(task.children||[]).map(child=>tree(child,depth+1))}</div>}
 return <Dialog open={visible} onOpenChange={open=>{if(!open&&visible)onClose()}}><DialogContent className="wide-dialog task-supervisor-dialog" onCloseAutoFocus={event=>{if(!visible)event.preventDefault()}}><DialogTitle>Task supervision</DialogTitle><DialogDescription>Build a team and inspect each worker. Steering targets the selected task; cancelling a parent also cancels its descendants.</DialogDescription>
  {loadError&&<p role="alert" className="inline-error">{loadError} <Button variant="outline" onClick={()=>setRefresh(value=>value+1)}>Reload task tree</Button></p>}
  {root?<><section className="swarm-summary" aria-label="Agent team"><strong>{workers.length} workers · {liveWorkers} active</strong>{!canBuild&&<small>Team assembly requires an active local internal task in Agent mode.</small>}{canBuild&&<Button disabled={busy} variant="outline" onClick={()=>{if(building){setBuilding(false);return}if(!creating&&!teamResult?.pending&&!teamResult?.paused){setMembers(initialMembers());setTeamResult(null);teamProgress.current=null;requestId.current=crypto.randomUUID()}setBuilding(true)}}>{building?'Close team builder':'Build agent team'}</Button>}</section>
   {building&&<form className="swarm-builder" onSubmit={event=>{event.preventDefault();void createTeam()}}><h3>Build agent team</h3><p>Workers inherit the coordinator’s provider and model. Read-only workers inspect its folder; workers that edit use isolated checkouts or snapshots. Collect results in the coordinator and review edits before applying them.</p>
    <fieldset disabled={busy||creating||!canBuild}><legend>Team members</legend>{members.map((member,index)=><section className="swarm-member" key={member.id}><label>Worker name<Input aria-label={'Worker '+(index+1)+' name'} maxLength={100} value={member.agent} onChange={event=>editMember(index,{agent:event.target.value})}/></label><label>Task<textarea className="input" aria-label={'Worker '+(index+1)+' task'} rows={3} maxLength={64000} value={member.task} onChange={event=>editMember(index,{task:event.target.value})}/></label><label>Access<Select aria-label={'Worker '+(index+1)+' access'} value={member.mode} onChange={event=>editMember(index,{mode:event.target.value as Member['mode']})}><option value="ask">Read only</option><option value="agent">Edit in isolated workspace</option></Select></label><details><summary>Worker instructions</summary><textarea className="input" aria-label={'Worker '+(index+1)+' instructions'} rows={2} maxLength={64000} value={member.instructions} onChange={event=>editMember(index,{instructions:event.target.value})}/></details><Button type="button" variant="ghost" disabled={members.length===1} aria-label={'Remove worker '+(index+1)} onClick={()=>{setMembers(old=>old.filter((_,i)=>i!==index));requestId.current=crypto.randomUUID();teamProgress.current=null;setTeamResult(null)}}>Remove</Button></section>)}</fieldset>

    <Button type="button" variant="outline" disabled={busy||creating||!canBuild} onClick={()=>{setMembers(old=>[...old,newMember('Worker '+(old.length+1))]);requestId.current=crypto.randomUUID();teamProgress.current=null;setTeamResult(null)}}>Add worker</Button><Button type="submit" disabled={busy||creating||!canBuild||!validTeam||!!teamResult}>{creating?'Creating workers…':'Create team'}</Button>
    {teamResult&&<div role="status"><p>{teamResult.ok?'Team created. Select a worker to inspect or steer it.':teamResult.pending?'Latest batch unconfirmed. Review the workers before retrying.':teamResult.paused?'Team creation paused.':'Some workers could not start. Inspect the created workers before trying again.'}</p>{teamResult.error&&<p className="inline-error" role="alert">{teamResult.error}</p>}{teamResult.members.map(member=><p key={member.task_id||member.agent}>{member.agent}: {member.task_id?<button type="button" onClick={()=>{setSelected(member.task_id!);setBuilding(false)}}>Inspect worker</button>:member.error}</p>)}{(teamResult.pending||teamResult.paused)&&<Button variant="outline" disabled={busy||creating||!canBuild} onClick={()=>void createTeam(true)}>{teamResult.pending?'Retry pending batch':'Continue team creation'}</Button>}</div>}
   </form>}
   <div className="supervision-grid"><nav className="supervision-tree" aria-label="Task tree">{tree(root)}</nav><div className="supervision-detail">{chosen&&<><h3>{chosen.agent_label||'Coordinator'}</h3><code>{chosen.id}</code><p>{chosen.status.replaceAll('_',' ')} · {chosen.engine||'internal'}</p><p>{chosen.delegated_task||chosen.prompt}</p><label>Execution location<code>{chosen.execution_location||chosen.worktree?.worktree_path||chosen.cwd||'No directory'}</code></label>{chosen.worktree?.branch&&<label>Branch<code>{chosen.worktree.branch}</code></label>}{chosen.isolation&&<p>{chosen.isolation.kind} workspace · edits require review</p>}
    {chosen.error&&<p className="inline-error">{chosen.error}</p>}{chosen.result&&<details><summary>Task outcome</summary><pre>{chosen.result}</pre></details>}
    {!active(chosen)&&!chosen.parent_id&&<><p className="retry-disclosure">Retry starts a new turn with the original prompt and images, using this conversation's current model, tools and execution location.</p><Button disabled={busy} onClick={()=>void control(chosen,'retry')}>Retry as a new turn</Button></>}
    {steerable(chosen)&&<Question disabled={busy} value={{text:message,selectedValues:[]}} onValueChange={value=>setMessage(value.text)} onSubmit={()=>control(chosen,'steer')}><QuestionPrompt>Instruction for this task</QuestionPrompt><QuestionInput aria-label="Steer selected task" maxLength={64000} placeholder="Add a clarification or answer for this task"/><QuestionSubmit variant={building?'outline':'default'} disabled={!message.trim()||busy}>Steer task</QuestionSubmit></Question>}
    {chosen.status==='recovery_confirmation_required'&&chosen.engine==='internal'&&!chosen.runner_id&&<section aria-label="Confirm task recovery"><p>An unfinished action may already have run. Recovery can repeat it. Inspect the task before continuing.</p><label><input type="checkbox" checked={confirmRecovery} disabled={busy} onChange={event=>setConfirmRecovery(event.target.checked)}/> I understand recovery may repeat an unfinished action</label><Button disabled={busy||!confirmRecovery} onClick={()=>void control(chosen,'recover')}>Confirm and recover task</Button></section>}
    {active(chosen)&&<Button className="supervision-cancel" variant="outline" disabled={busy||chosen.status==='cancelling'} onClick={()=>void control(chosen,'cancel')}>{chosen.status==='cancelling'?'Cancelling…':'Cancel task and descendants'}</Button>}
   </>}</div></div></>:!loadError&&<p role="status">Loading task tree…</p>}
 </DialogContent></Dialog>
}
