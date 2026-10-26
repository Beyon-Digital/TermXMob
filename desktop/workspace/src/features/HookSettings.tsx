import {useEffect,useRef,useState} from 'react';
import {Button} from '../components/ui/button';
import {Input} from '../components/ui/input';
import {Select} from '../components/ui/select';
import {Dialog,DialogContent,DialogDescription,DialogTitle} from '../components/ui/dialog';
import {gql,json,request} from '../lib/api';
import {useSettingsSave} from './SettingsSurface';

type Project={id:string;name:string;path:string};
type Actor={principal:{id:string;scopes:string[];policy_version?:number};session_id?:string};
type Access={role:string|null;projects:{project_id:string;scopes:string[];expires?:number|null}[]};
type Hook={id:string;project_id:string;revision:number;event:string;argv:string[];cwd:string;timeout_s:number;capabilities:string[];enabled:boolean};
type Run={id:string;project_id:string;hook_id:string;event:string;created_at:number;exit_code?:number|null;timed_out?:boolean;error?:string;output?:string};
type CreateBody=Pick<Hook,'project_id'|'event'|'argv'|'cwd'|'timeout_s'|'capabilities'|'enabled'>;
type Catalog={actor:Actor;access:Access;projects:Project[];hooks:Hook[];runs:Run[];available:string[];unavailable:string[];shown:number};
type CreateReceipt={body:CreateBody;signature:string;before:Set<string>;state:'pending'|'uncertain'|'saved';id?:string};
type Operation={state:'pending'|'uncertain';kind:'enable'|'disable'|'remove';revision:number;enabled?:boolean};
type Review={kind:'create';body:CreateBody;project:Project;saved:()=>void}|{kind:'enable'|'remove';hook:Hook;project:Project;saved:()=>void};
const PAGE_SIZE=20;
const events=[['before_turn','Before a turn'],['after_turn','After a successful turn'],['task_failed','After a failed turn']];
// The hook API has no idempotency key. Keep uncertain writes blocked across a
// panel remount; only a fresh matching host record can confirm creation.
const creations=new Map<string,CreateReceipt>(),operations=new Map<string,Operation>();
const changed=()=>window.dispatchEvent(new Event('termx-hook-write'));
const signature=(body:CreateBody)=>JSON.stringify({project_id:body.project_id,event:body.event,argv:body.argv,cwd:body.cwd,timeout_s:body.timeout_s,capabilities:[...new Set(['files-read','files-write',...body.capabilities])].sort(),enabled:body.enabled});
const commandBody=(hook:Hook):CreateBody=>({project_id:hook.project_id,event:hook.event,argv:hook.argv,cwd:hook.cwd,timeout_s:hook.timeout_s,capabilities:hook.capabilities,enabled:hook.enabled});
const uncertain=(error:unknown)=>{const status=(error as {status?:number})?.status;return !status||status>=500||status===408};
const message=(error:unknown)=>error instanceof Error?error.message:'The host could not complete this request.';
function allowed(actor:Actor,access:Access,project:string,scopes:string[]){
 const grant=access.projects.find(row=>row.project_id===project&&(row.expires==null||row.expires>Date.now()/1000));
 return scopes.every(scope=>actor.principal.scopes.includes(scope)&&(['owner','admin'].includes(access.role||'')||!!grant?.scopes.includes(scope)));
}
function argumentsFor(value:string){
 const argv:unknown=value.trim().startsWith('[')?JSON.parse(value):value.split('\n').map(part=>part.trim()).filter(Boolean);
 if(!Array.isArray(argv)||!argv.length||argv.length>32||argv.some(arg=>typeof arg!=='string'||arg.length>4096)||!argv[0].trim())throw Error('Enter an executable and up to 31 arguments, one per line or as a JSON array.');
 return argv as string[];
}

export default function HookSettings({search='',version=0}:{search?:string;version?:number}){
 const beginSave=useSettingsSave();
 const [catalog,setCatalog]=useState<Catalog|null>(null),[loading,setLoading]=useState(true),[error,setError]=useState(''),[notice,setNotice]=useState('');
 const [pageSize,setPageSize]=useState(PAGE_SIZE),[reload,setReload]=useState(0),[,setWriteVersion]=useState(0),[review,setReview]=useState<Review|null>(null),[working,setWorking]=useState(false),[locked,setLocked]=useState(false),[createOpen,setCreateOpen]=useState(false);
 const [draft,setDraft]=useState({project:'',event:'before_turn',argv:'',cwd:'',timeout:'10'});
 const live=useRef({epoch:0,busy:false,owner:''}),controllers=useRef(new Set<AbortController>());
 useEffect(()=>{
  const write=()=>setWriteVersion(value=>value+1);
  const stop=(event:Event)=>{live.current.epoch++;live.current.busy=false;for(const controller of controllers.current)controller.abort();controllers.current.clear();setCatalog(null);setReview(null);setWorking(false);setError('');setNotice('');setLocked(true);if(event.type==='termx-signed-out'){live.current.owner='';setDraft({project:'',event:'before_turn',argv:'',cwd:'',timeout:'10'})}};
  const unlock=()=>{setLocked(false);setReload(value=>value+1)};
  window.addEventListener('termx-hook-write',write);window.addEventListener('termx-session-locked',stop);window.addEventListener('termx-signed-out',stop);window.addEventListener('termx-session-unlocked',unlock);
  return()=>{live.current.epoch++;for(const controller of controllers.current)controller.abort();window.removeEventListener('termx-hook-write',write);window.removeEventListener('termx-session-locked',stop);window.removeEventListener('termx-signed-out',stop);window.removeEventListener('termx-session-unlocked',unlock)};
 },[]);
 useEffect(()=>{
  if(locked)return;
  const controller=new AbortController();controllers.current.add(controller);const epoch=live.current.epoch;
  const fresh=()=>!controller.signal.aborted&&epoch===live.current.epoch;
  setLoading(true);setError('');setCatalog(null);
  void(async()=>{
   try{
    const actor=await request<Actor>('/auth/me',{signal:controller.signal});
    const access=await request<Access>('/auth/access',{signal:controller.signal});
    const response=await gql<{projects:Project[]}>('{projects{id name path}}');
    if(!fresh())return;
    if(live.current.owner&&live.current.owner!==actor.principal.id){setDraft({project:'',event:'before_turn',argv:'',cwd:'',timeout:'10'});setReview(null)}
    live.current.owner=actor.principal.id;
    const projects=response.projects,hooks:Hook[]=[],runs:Run[]=[],available:string[]=[],unavailable:string[]=[];
    const shown=Math.min(pageSize,projects.length);
    // Reads are sequential and the first page is bounded. Loading more is an
    // explicit user action, and failures never retain a former source payload.
    for(let index=0;index<shown;index++){
     if(!fresh())return;
     const project=projects[index],query='?project_id='+encodeURIComponent(project.id);
     try{
      const current=await request<{hooks:Hook[]}>('/api/workspace/hooks'+query,{signal:controller.signal});
      const history=await request<{runs:Run[]}>('/api/workspace/hook-runs'+query,{signal:controller.signal});
      if(!Array.isArray(current.hooks)||!Array.isArray(history.runs))throw Error('The host returned an invalid hook inventory.');
      hooks.push(...current.hooks.filter(hook=>hook.project_id===project.id));runs.push(...history.runs.filter(run=>run.project_id===project.id));available.push(project.id);
     }catch(reason){
      if(!fresh())return;unavailable.push(project.id);
      const status=(reason as {status?:number})?.status;
      if(!status||status>=500){unavailable.push(...projects.slice(index+1,shown).map(row=>row.id));break}
     }
    }
    if(!fresh())return;
    const currentActor=await request<Actor>('/auth/me',{signal:controller.signal});
    if(!fresh())return;
    if(currentActor.principal.id!==actor.principal.id||currentActor.session_id!==actor.session_id||currentActor.principal.policy_version!==actor.principal.policy_version)throw Error('Your account or project authority changed while loading hooks. Check host state again.');
    const receipt=creations.get(actor.principal.id);
    if(receipt?.state==='uncertain'){
     const matches=hooks.filter(hook=>!receipt.before.has(hook.id)&&signature(commandBody(hook))===receipt.signature&&!hook.enabled);
     if(matches.length===1){receipt.state='saved';receipt.id=matches[0].id;setNotice('The host confirms this hook was saved disabled.');changed()}
    }
    for(const [key,operation] of operations){
     if(!key.startsWith(actor.principal.id+':')||operation.state!=='uncertain')continue;
     const id=key.slice(actor.principal.id.length+1),hook=hooks.find(row=>row.id===id);
     if((operation.kind==='remove'&&!hook&&available.length===shown&&!unavailable.length)||(hook&&hook.revision>operation.revision&&hook.enabled===operation.enabled))operations.delete(key);
    }
    setCatalog({actor,access,projects,hooks,runs,available,unavailable,shown});
   }catch(reason){if(fresh())setError(message(reason))}finally{controllers.current.delete(controller);if(fresh())setLoading(false)}
  })();
  return()=>{controller.abort();controllers.current.delete(controller)};
 },[version,reload,pageSize,locked]);
 const owner=catalog?.actor.principal.id||'',receipt=owner?creations.get(owner):undefined;
 const project=catalog?.projects.find(row=>row.id===draft.project);
 const can=(id:string,scopes:string[])=>!!catalog&&catalog.available.includes(id)&&allowed(catalog.actor,catalog.access,id,scopes);
 const canCreate=!!project&&can(project.id,['agent-control','files-read','files-write']);
 let body:CreateBody|null=null;
 try{if(draft.project&&draft.cwd.trim()&&Number.isInteger(Number(draft.timeout))&&Number(draft.timeout)>=1&&Number(draft.timeout)<=60)body={project_id:draft.project,event:draft.event,argv:argumentsFor(draft.argv),cwd:draft.cwd.trim(),timeout_s:Number(draft.timeout),enabled:false,capabilities:[]}}catch{/* A readable error is shown on form submission; validation never dispatches. */}
 const duplicate=!!body&&receipt?.state==='saved'&&receipt.signature===signature(body);
 const blockedCreate=receipt?.state==='pending'||receipt?.state==='uncertain';
 function createReview(){
  if(!catalog||loading||working||blockedCreate||duplicate||!canCreate||!project)return;
  try{if(!body){argumentsFor(draft.argv);throw Error('Choose a project, working folder and a timeout between 1 and 60 seconds.')}setError('');setReview({kind:'create',body,project:{...project},saved:beginSave()})}catch(reason){setError(message(reason))}
 }
 async function apply(action:Review|{kind:'disable';hook:Hook;project:Project;saved:()=>void}){
  if(!catalog||loading||live.current.busy||locked)return;
  const actorId=catalog.actor.principal.id,epoch=live.current.epoch,key=action.kind==='create'?'':actorId+':'+action.hook.id;
  if(action.kind==='create'&&(creations.get(actorId)?.state==='pending'||creations.get(actorId)?.state==='uncertain'))return;
  if(key&&operations.has(key))return;
  const controller=new AbortController();controllers.current.add(controller);live.current.busy=true;setWorking(true);setError('');setNotice('');
  const createAttempt:CreateReceipt|null=action.kind==='create'?{body:action.body,signature:signature(action.body),before:new Set(catalog.hooks.map(hook=>hook.id)),state:'pending'}:null;
  if(createAttempt)creations.set(actorId,createAttempt);
  if(key)operations.set(key,{kind:action.kind as Operation['kind'],state:'pending',revision:action.kind==='create'?0:action.hook.revision,enabled:action.kind==='enable'});
  changed();
  const fresh=()=>epoch===live.current.epoch&&!controller.signal.aborted;
  let dispatched=false;
  try{
   const actor=await request<Actor>('/auth/me',{signal:controller.signal}),access=await request<Access>('/auth/access',{signal:controller.signal});
   const {projects}=await gql<{projects:Project[]}>('{projects{id name path}}');
   if(!fresh()||actor.principal.id!==actorId)throw Error('The signed-in account changed. Review the current hook before continuing.');
   if(!projects.some(row=>row.id===action.project.id&&row.path===action.project.path))throw Error('The project target changed. Reload hooks and review its current folder.');
   const required=action.kind==='remove'||action.kind==='disable'?['agent-control']:['agent-control','files-read','files-write',...(action.kind==='enable'?action.hook.capabilities:[])];
   if(!allowed(actor,access,action.project.id,required))throw Error('Your current account cannot make this change in the selected project.');
   const current=await request<{hooks:Hook[]}>('/api/workspace/hooks?project_id='+encodeURIComponent(action.project.id),{signal:controller.signal});
   if(!fresh())return;
   if(action.kind==='create'){
    const attempt=createAttempt!;attempt.before=new Set(current.hooks.map(hook=>hook.id));dispatched=true;
    const saved=await request<Hook>('/api/workspace/hooks',{...json('POST',action.body),signal:controller.signal});
    if(typeof saved?.id!=='string'||signature(commandBody(saved))!==attempt.signature)throw Error('The host did not confirm the exact saved hook.');
    attempt.state='saved';attempt.id=saved.id;action.saved();
   }else{
    const hook=current.hooks.find(row=>row.id===action.hook.id);
    if(!hook||hook.revision!==action.hook.revision||signature(commandBody(hook))!==signature(commandBody(action.hook)))throw Error('This hook changed. Reload and review its current command before trying again.');
    dispatched=true;
    await request('/api/workspace/hooks/'+encodeURIComponent(hook.id),{...(action.kind==='remove'?{method:'DELETE'}:json('PATCH',{enabled:action.kind==='enable',revision:hook.revision})),signal:controller.signal});
    operations.delete(key);if(action.kind==='remove')action.saved();
   }
   if(fresh()){setReview(null);setNotice(action.kind==='create'?'Hook saved disabled. Review it again before enabling lifecycle execution.':action.kind==='enable'?'Hook enabled for future lifecycle events.':action.kind==='disable'?'Hook disabled for future lifecycle events.':'Hook removed. A command already running may finish.');setReload(value=>value+1)}
  }catch(reason){
   if(dispatched&&uncertain(reason)){if(action.kind==='create'){const pending=creations.get(actorId);if(pending)pending.state='uncertain'}else{const pending=operations.get(key);if(pending)pending.state='uncertain'}}
   else{if(action.kind==='create'&&creations.get(actorId)===createAttempt)creations.delete(actorId);if(key)operations.delete(key)}
   if(fresh()){if(dispatched&&uncertain(reason))setReview(null);setError(dispatched&&uncertain(reason)?'The host did not confirm this change. Check the host state before any new attempt; this request will not be repeated.':message(reason))}
  }finally{changed();controllers.current.delete(controller);if(fresh()){live.current.busy=false;setWorking(false)}}
 }
 const terms=search.toLowerCase().trim().split(/\s+/).filter(Boolean);
 const matches=(value:string)=>terms.every(term=>value.toLowerCase().includes(term));
 const hooks=(catalog?.hooks||[]).filter(hook=>matches([catalog?.projects.find(row=>row.id===hook.project_id)?.name,hook.event,...hook.argv,hook.cwd,hook.enabled?'enabled':'disabled'].join(' ')));
 const runs=(catalog?.runs||[]).filter(run=>matches([catalog?.projects.find(row=>row.id===run.project_id)?.name,run.event,run.output,run.error,run.exit_code].join(' ')));
 const reviewedHook=review?.kind==='create'?review.body:review?.hook;
 return <section aria-label="Lifecycle hooks">
  <h2>Lifecycle hooks</h2><p className="manager-help">Run an explicitly configured command before a turn, after success, or after failure in its selected project folder. Saving a hook does not run it.</p>
  <Button variant="outline" size="sm" disabled={loading||working||locked} onClick={()=>setReload(value=>value+1)}>Check host state</Button>
  {locked?<p role="status">Unlock this workspace to inspect hooks.</p>:loading?<p role="status">Loading authorized projects and hooks…</p>:error&&!catalog?<p role="alert" className="manager-error">{error}</p>:null}
  {error&&catalog&&!review&&<p role="alert" className="manager-error">{error}</p>}{notice&&<p role="status">{notice}</p>}
  {catalog&&<>
   {catalog.unavailable.length>0&&<p role="alert">Hooks are unavailable for {catalog.projects.filter(row=>catalog.unavailable.includes(row.id)).map(row=>row.name).join(', ')}. Other authorized projects remain available. Check host state to retry.</p>}
   {catalog.shown<catalog.projects.length&&<p>Showing {catalog.shown} of {catalog.projects.length} authorized projects. <Button variant="ghost" size="sm" disabled={working} onClick={()=>setPageSize(value=>value+PAGE_SIZE)}>Load more projects</Button></p>}
   {!catalog.projects.length&&<p>No projects are available to this account. Add a project or ask the host owner for project access.</p>}
   {!hooks.length&&<p>{search.trim()?'No hooks match this search.':catalog.available.length?'No hooks in the loaded projects. Create one below when you need lifecycle automation.':'No hook inventories are currently available.'}</p>}
   {hooks.map(hook=>{const target=catalog.projects.find(row=>row.id===hook.project_id)!,operation=operations.get(owner+':'+hook.id);return <article className="manager-item" key={hook.id} aria-label={'Hook in '+target.name}>
    <div><h3>{events.find(([value])=>value===hook.event)?.[1]||hook.event}</h3><p>{target.name} · {hook.enabled?'Enabled':'Disabled'} · {hook.timeout_s}s timeout</p><p>Folder: {hook.cwd}</p><pre className="manager-json" aria-label="Hook command">{JSON.stringify(hook.argv)}</pre>{!can(hook.project_id,['agent-control'])&&<p>Read only in this project.</p>}{operation?.state==='uncertain'&&<p role="status">Change outcome is uncertain. Check host state; no request is being repeated.</p>}</div>
    <div className="manager-item-actions"><Button variant="secondary" disabled={working||!!operation||!can(hook.project_id,hook.enabled?['agent-control']:['agent-control','files-read','files-write',...hook.capabilities])} onClick={()=>hook.enabled?void apply({kind:'disable',hook,project:target,saved:()=>{}}):setReview({kind:'enable',hook,project:target,saved:()=>{}})}>{hook.enabled?'Disable':'Enable'}</Button><Button variant="ghost" data-reviewed-action disabled={working||!!operation||!can(hook.project_id,['agent-control'])} onClick={()=>setReview({kind:'remove',hook,project:target,saved:beginSave()})}>Remove hook</Button></div>
   </article>})}
   <details open={createOpen} onToggle={event=>setCreateOpen(event.currentTarget.open)}><summary>Add a hook</summary><form className="manager-form" aria-label="Create lifecycle hook" onSubmit={event=>{event.preventDefault();createReview()}}>
    <label>Run in project<Select aria-label="Run in project" value={draft.project} disabled={working||blockedCreate} onChange={event=>{const target=catalog.projects.find(row=>row.id===event.target.value);setDraft(value=>({...value,project:event.target.value,cwd:target?.path||''}))}}><option value="">Choose an explicit project</option>{catalog.projects.map(row=><option key={row.id} value={row.id} disabled={!can(row.id,['agent-control','files-read','files-write'])}>{row.name}{!can(row.id,['agent-control','files-read','files-write'])?' · Read only or not loaded':''}</option>)}</Select></label>
    <label>When to run<Select aria-label="When to run" value={draft.event} disabled={working||blockedCreate} onChange={event=>setDraft(value=>({...value,event:event.target.value}))}>{events.map(([value,label])=><option key={value} value={value}>{label}</option>)}</Select></label>
    <label>Executable and arguments (one per line)<textarea required value={draft.argv} disabled={working||blockedCreate} placeholder={'python\nscripts/check.py'} onChange={event=>setDraft(value=>({...value,argv:event.target.value}))}/></label>
    <label>Working folder<Input required value={draft.cwd} disabled={working||blockedCreate} onChange={event=>setDraft(value=>({...value,cwd:event.target.value}))}/></label>
    <label>Timeout in seconds<Input type="number" min={1} max={60} required value={draft.timeout} disabled={working||blockedCreate} onChange={event=>setDraft(value=>({...value,timeout:event.target.value}))}/></label>
    <p className="manager-help">Creation requires control, file read and file write permission in this project. New hooks start disabled. The host checks the actual folder and current authority for every save and lifecycle run.</p>
    <Button type="submit" disabled={working||blockedCreate||duplicate||!canCreate||!body}>{duplicate?'Hook saved':blockedCreate?'Check pending save outcome':'Review disabled hook'}</Button>
   </form></details>
   {blockedCreate&&<p role="status">A hook save is {receipt?.state==='pending'?'pending':'unconfirmed'}. Check host state to inspect its outcome before creating another hook. No save is retried automatically.</p>}
   <details><summary>Execution history</summary>{!runs.length?<p>{search.trim()?'No hook runs match this search.':'No hook runs in the loaded projects.'}</p>:runs.sort((a,b)=>b.created_at-a.created_at).slice(0,100).map(run=><article key={run.id}><h3>{catalog.projects.find(project=>project.id===run.project_id)?.name} · {events.find(([value])=>value===run.event)?.[1]||run.event}</h3><p>{new Date(run.created_at*1000).toLocaleString()} · {run.timed_out?'Timed out':run.error?'Failed':run.exit_code===0?'Succeeded':'Exit '+String(run.exit_code??'unknown')}</p>{run.error&&<p>{run.error}</p>}{run.output&&<pre className="manager-json">{run.output}</pre>}</article>)}{runs.length>100&&<p>Showing the latest 100 matching runs.</p>}</details>
  </>}
  <Dialog open={!!review} onOpenChange={open=>{if(!working&&!open)setReview(null)}}><DialogContent data-reviewed-action><DialogTitle>{review?.kind==='create'?'Save this disabled hook?':review?.kind==='enable'?'Enable this lifecycle hook?':'Remove this lifecycle hook?'}</DialogTitle><DialogDescription>{review?.kind==='remove'?'Removes future lifecycle runs. A command already running may finish.':'This command can read and write project files as the host OS user when its lifecycle event occurs. Review the exact target and arguments; chat tool approvals do not approve each hook invocation.'}</DialogDescription>{review&&reviewedHook&&<><p>Project: {review.project.name}</p><p>Folder: {reviewedHook.cwd}</p><p>Event: {events.find(([value])=>value===reviewedHook.event)?.[1]||reviewedHook.event} · Timeout: {reviewedHook.timeout_s}s</p><pre className="manager-json" aria-label="Exact hook command">{JSON.stringify(reviewedHook.argv,null,2)}</pre><p>{reviewedHook.capabilities.includes('network-manage')?'Network access is configured for this hook.':'Network access is not granted.'} {review.kind==='create'?'It will be saved disabled.':'No command runs when you confirm this settings change.'}</p></>}{error&&<p role="alert">{error}</p>}<div className="manager-item-actions"><Button variant="outline" disabled={working} onClick={()=>setReview(null)}>Cancel review</Button><Button disabled={working||loading||!catalog||locked} onClick={()=>{if(review)void apply(review)}}>{working?'Saving…':review?.kind==='create'?'Save disabled hook':review?.kind==='enable'?'Confirm enable':'Confirm removal'}</Button></div></DialogContent></Dialog>
 </section>;
}
