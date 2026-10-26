import {conversationMessages,latestExecutionTask} from '../lib/conversation-messages';
import ChatWelcome from './ChatWelcome';
import ChatConnectionSetup,{chatConnectionProblem} from './ChatConnectionSetup';
import {Attachments,Attachment,AttachmentPreview,AttachmentInfo,AttachmentRemove} from '../components/ai-elements/attachments';
import {conversationAnchor,anchorCorrection,type ConversationAnchor} from '../lib/conversation-position';
import {persistWindowDraft} from '../lib/window-journal';
import ConversationMessage from './ConversationMessage';
import PromptQueue from './PromptQueue';
import SessionModelControls from './SessionModelControls';
import SessionModePicker from './SessionModePicker';
import './chat-composer.css';
import {useModelFavorites} from '../lib/model-favorites';
import SessionSettings from './SessionSettings';
import {sessionDrafts,consumedTransfers,draftWrites,submittingDrafts,draftSubmissionOwners} from '../lib/drafts';
import {captureSessionWrite,serializeSessionWrite,type SessionWriteIntent} from '../lib/session-writes';
import {taskIsActive} from '../lib/tool-activity';
import {contextLabel,contextDescription,sameContext,type ContextItem,type IncomingContext} from '../lib/context';
import ForkContextDialog from './ForkContextDialog';
import TaskSupervisor from './TaskSupervisor';
import {useEffect,useMemo,useRef,useState} from 'react';
import {useVirtualizer} from '@tanstack/react-virtual';
import {Square,GitFork,LoaderCircle,X,SlidersHorizontal,MoreHorizontal,Plus,ArrowUp,ListPlus} from 'lucide-react';
import {Button} from '../components/ui/button';
import {Select} from '../components/ui/select';
import {Dialog,DialogContent,DialogDescription,DialogTitle} from '../components/ui/dialog';
import ConversationSurfaceHeader,{type ConversationSurface,type ConversationChanges} from './ConversationSurfaceHeader';
import {PromptInputBody,PromptInputTextarea,PromptInputSubmit,PromptInputTools} from '../components/ai-elements/prompt-input';
import {gql,json,request,type Session,type Engine,type Provider,type Task,type Project} from '../lib/api';
import './chat-session.css';

// Shared by retained/replaced chat panels so one Stop action stays in flight.
const stoppingTasks=new Set<string>();
const active=(task?:Task)=>!!task&&(taskIsActive(task.status)||['awaiting_approval','paused','recovery_confirmation_required'].includes(task.status));
export default function Chat({session,engines,providers,onSelect,onRefresh,onError,incoming,onHistory,projects=[],ownerId,visible=true,onOpenSurface,onOpenFile,changes,onConfigureAgent,coordinating=false}:{coordinating?:boolean;onConfigureAgent?:()=>void;onOpenSurface?:(surface:ConversationSurface)=>void;onOpenFile?:(path:string,line?:number)=>void;changes?:ConversationChanges;visible?:boolean;ownerId?:string;projects?:Project[];onHistory?:()=>void;incoming?:IncomingContext|null;session:Session|null;engines:Engine[];providers:Provider[];onSelect:(id:string)=>void;onRefresh:()=>void;onError:(error:unknown)=>void}){
 const [context,setContext]=useState<ContextItem[]>([]);
 const [draft,setDraft]=useState(''),[submissionChange,setSubmissionChange]=useState(0),[modelCatalogue,setModelCatalogue]=useState<{selection:string;models:string[]}>({selection:'',models:[]}),[attachments,setAttachments]=useState<{name:string;mime:string;data:string}[]>([]),[supervised,setSupervised]=useState(''),[renameOpen,setRenameOpen]=useState(false),[name,setName]=useState(''),[group,setGroup]=useState('');
 const [forkEngine,setForkEngine]=useState(''),[forkOpen,setForkOpen]=useState(false),[pendingPrompt,setPendingPrompt]=useState<import('../lib/drafts').PendingSubmission|null>(null);
 const [approvalFocus,setApprovalFocus]=useState<{sessionId:string;taskId:string;messageId:string}|null>(null);
 const [,refreshCancellation]=useState(0),[actionError,setActionError]=useState<{sessionId:string;message:string;kind?:'connection'}|null>(null);
 const sending=!!session&&submittingDrafts.has(session.id);
 const [queueChange,setQueueChange]=useState(0),[awayFromLatest,setAwayFromLatest]=useState(false),[interruptOpen,setInterruptOpen]=useState(false),[optionsOpen,setOptionsOpen]=useState(false),[runActionsOpen,setRunActionsOpen]=useState(false);
 const scroller=useRef<HTMLDivElement>(null),fileInput=useRef<HTMLInputElement>(null),composer=useRef<HTMLTextAreaElement>(null),lastSaved=useRef(''),dirty=useRef(false),sessionId=useRef<string|null>(null),drafts=useRef(sessionDrafts);
 const {favorites,toggle:toggleFavorite}=useModelFavorites(ownerId);
 const modelSelection=JSON.stringify([session?.engine,session?.provider_id,session?.runner_id]);
 const models=modelCatalogue.selection===modelSelection?modelCatalogue.models:[];
 useEffect(()=>{const element=composer.current;if(!element)return;element.style.height='auto';element.style.height=Math.max(48,element.scrollHeight)+'px'},[draft,session?.id,visible]);
 const favoriteKey=JSON.stringify([session?.engine,session?.provider_id,session?.model]);
 function favorite(){if(session?.model)try{toggleFavorite(favoriteKey)}catch(error){onError(error)}}
 const [runnerOptions,setRunnerOptions]=useState<{id:string;project:string;status:string;configuration:{image:string;name?:string;kind?:string;root?:string}}[]>([]),[runnerAccounts,setRunnerAccounts]=useState<{provider_id:string;credential_ref:string;name:string;models:string[]}[]>([]);
 useEffect(()=>{if(!session||session.engine!=='internal'||!visible)return;let cancelled=false;Promise.all([request<{id:string;project:string;status:string;configuration:{image:string;name?:string;kind?:string;root?:string}}[]>('/api/runners'),request<{accounts:{provider_id:string;credential_ref:string;name:string;models:string[]}[]}>('/api/runner-agents/capabilities')]).then(([runners,accounts])=>{if(!Array.isArray(runners))throw new Error('Unexpected runner catalog response');if(!cancelled){setRunnerOptions(runners);setRunnerAccounts(accounts.accounts||[])}}).catch(()=>{if(!cancelled){setRunnerOptions([]);setRunnerAccounts([]);if(optionsOpen)onError(new Error('Runner options could not be loaded. Check access and reopen Session options to retry.'))}});return()=>{cancelled=true}},[session?.id,session?.engine,optionsOpen,visible]);
 const offsets=useRef(new Map<string,number>()),anchors=useRef(new Map<string,ConversationAnchor|null>());
 const turns=session?.turns||[];
 useEffect(()=>{if(!session?.id)return;const id=session.id,element=scroller.current;if(!element)return;let timer:ReturnType<typeof setTimeout>|undefined,frame=0,restoring=true,attempts=0;const anchor=anchors.current.get(id)??session.scroll_anchor;const offset=offsets.current.get(id)??session.scroll;const restore=()=>{if(!restoring)return;if(attempts++===0){const index=anchor?messages.findIndex(message=>message.id===anchor.id):-1;if(index>=0)virtual.scrollToIndex(index,{align:'start'});else if(offsets.current.has(id)||offset>0)virtual.scrollToOffset(offset);else if(messages.length)virtual.scrollToIndex(messages.length-1,{align:'end'})}else if(anchor){const correction=anchorCorrection(element,anchor);if(correction!==null&&Math.abs(correction)>1)virtual.scrollToOffset(element.scrollTop+correction)}if(attempts<24)frame=requestAnimationFrame(restore);else{restoring=false;setAwayFromLatest(element.scrollHeight-element.scrollTop-element.clientHeight>350)}};frame=requestAnimationFrame(restore);const cancelRestore=()=>{restoring=false;cancelAnimationFrame(frame)};const save=()=>{setAwayFromLatest(element.scrollHeight-element.scrollTop-element.clientHeight>350);if(restoring)return;const offset=element.scrollTop,anchor=conversationAnchor(element);offsets.current.set(id,offset);anchors.current.set(id,anchor);clearTimeout(timer);const intent=captureSessionWrite();timer=setTimeout(async()=>{try{await serializeSessionWrite(id,async()=>{const current=await request<Session>('/api/workspace/sessions/'+id+'?turns=false',{signal:intent.signal});intent.assertCurrent();await request('/api/workspace/sessions/'+id,{...json('PATCH',{revision:current.revision,changes:{scroll:offset,scroll_anchor:anchor}}),signal:intent.signal})},intent)}catch(error){if(intent.isCurrent())onError(error)}finally{intent.release()}},700)};element.addEventListener('scroll',save,{passive:true});for(const event of ['wheel','pointerdown','touchstart','keydown'])element.addEventListener(event,cancelRestore,{passive:true});return()=>{cancelAnimationFrame(frame);clearTimeout(timer);offsets.current.set(id,element.scrollTop);anchors.current.set(id,conversationAnchor(element));element.removeEventListener('scroll',save);for(const event of ['wheel','pointerdown','touchstart','keydown'])element.removeEventListener(event,cancelRestore)}},[session?.id]);
 const messages=useMemo(()=>conversationMessages(turns),[turns]);
 const virtual=useVirtualizer({count:messages.length,getScrollElement:()=>scroller.current,estimateSize:()=>140,overscan:8,initialRect:{width:800,height:600},getItemKey:index=>messages[index].id});
 const latest=latestExecutionTask(turns),running=active(latest);
 const connectionProblem=chatConnectionProblem(session,providers,engines),connectionBlocked=coordinating&&!!connectionProblem;
 useEffect(()=>{if(!connectionBlocked)setActionError(value=>value?.kind==='connection'?null:value)},[connectionBlocked]);
 useEffect(()=>{setRunActionsOpen(false);setActionError(null)},[session?.id,running]);
 useEffect(()=>{const update=()=>{setSubmissionChange(value=>value+1);const value=sessionId.current?drafts.current.get(sessionId.current):undefined;setPendingPrompt(value?.submission||null);if(value){setDraft(value.text);setContext(value.context||[]);setAttachments(value.attachments||[]);lastSaved.current=value.saved;dirty.current=value.dirty}};window.addEventListener('termx-draft-submission',update);return()=>window.removeEventListener('termx-draft-submission',update)},[]);
 useEffect(()=>{const update=()=>refreshCancellation(value=>value+1);window.addEventListener('termx-task-cancellation',update);return()=>window.removeEventListener('termx-task-cancellation',update)},[]);
 const stopping=!!latest&&(latest.status==='cancelling'||stoppingTasks.has(latest.id));
 async function stopTask(){
  if(!session||!latest||stoppingTasks.has(latest.id)||latest.status==='cancelling')return;
  const id=latest.id,origin=session.id;stoppingTasks.add(id);window.dispatchEvent(new Event('termx-task-cancellation'));setActionError(null);
  try{await request('/api/workspace/tasks/'+encodeURIComponent(id)+'/cancel',json('POST',{}));onRefresh()}
  catch(error){if(sessionId.current===origin)setActionError({sessionId:origin,message:error instanceof Error?error.message:'The task could not be stopped. Check its status before trying again.'});onError(error)}
  finally{stoppingTasks.delete(id);window.dispatchEvent(new Event('termx-task-cancellation'))}
 }
 // The approval can be outside the mounted virtual rows. Wait for its actual
 // review button after scrolling; focus never submits an approval decision.
 useEffect(()=>{
  if(!approvalFocus)return;
  const target=messages.find(message=>message.id===approvalFocus.messageId&&message.task?.id===approvalFocus.taskId);
  if(session?.id!==approvalFocus.sessionId||!target?.task?.approvals?.some(approval=>approval.status==='pending')){setApprovalFocus(null);return}
  let frame=0,attempts=0,cancelled=false;
  const focusApproval=()=>{
   if(cancelled)return;
   const row=Array.from(scroller.current?.querySelectorAll<HTMLElement>('[data-message-id]')||[]).find(element=>element.dataset.messageId===approvalFocus.messageId);
   const button=row?.querySelector<HTMLButtonElement>('.approval button:not(:disabled)');
   if(button){button.scrollIntoView?.({block:'nearest'});button.focus({preventScroll:true});setApprovalFocus(null);return}
   if(++attempts<30)frame=requestAnimationFrame(focusApproval);else setApprovalFocus(null);
  };
  frame=requestAnimationFrame(focusApproval);
  return()=>{cancelled=true;cancelAnimationFrame(frame)};
 },[approvalFocus,session?.id,messages]);
 function reviewCurrentTask(){
  if(!latest||!session)return;
  const index=messages.findIndex(message=>message.task?.id===latest.id);
  if(latest.approvals?.some(approval=>approval.status==='pending')&&index>=0){setApprovalFocus({sessionId:session.id,taskId:latest.id,messageId:messages[index].id});virtual.scrollToIndex(index,{align:'end'});setAwayFromLatest(false)}
  else setSupervised(latest.id);
 }

 // Remote metadata is not a local edit. Compare cached text/context with its
 // own saved baseline so a clean main window can accept a detached draft.
 useEffect(()=>{if(session?.id!==sessionId.current){const previous=sessionId.current;if(previous){const old=drafts.current.get(previous);if(old?.dirty)void saveDraft(previous,old.text,old.saved)}sessionId.current=session?.id||null;const cached=session?.id?drafts.current.get(session.id):undefined;setDraft(cached?cached.text:session?.draft_text||'');setContext(cached?cached.context||[]:session?.draft_context||[]);lastSaved.current=cached?.saved??session?.draft_text??'';dirty.current=!!cached&&(cached.dirty||cached.text!==cached.saved||!sameContext(cached.context,cached.savedContext));if(cached&&dirty.current)drafts.current.set(session!.id,{...cached,dirty:true});setAttachments(cached?.attachments||[]);setPendingPrompt(cached?.submission||null)}else if(!dirty.current){const cached=session?.id?drafts.current.get(session.id):undefined;if(cached&&(session?.draft_text!==cached.saved||!sameContext(session?.draft_context,cached.savedContext))){setDraft(cached.text);setContext(cached.context||[]);lastSaved.current=cached.saved;dirty.current=cached.dirty||cached.text!==cached.saved||!sameContext(cached.context,cached.savedContext);if(dirty.current)drafts.current.set(session!.id,{...cached,dirty:true})}else{setDraft(session?.draft_text||'');setContext(session?.draft_context||[]);lastSaved.current=session?.draft_text||''}}},[session?.id,session?.draft_text,JSON.stringify(session?.draft_context)]);
 useEffect(()=>{const receive=(event:Event)=>{const value=(event as CustomEvent<{sessionId:string;draft:import('../lib/drafts').Draft|null}>).detail;if(value.sessionId!==sessionId.current||!value.draft)return;setDraft(value.draft.text);setContext(value.draft.context||[]);setAttachments(value.draft.attachments||[]);setPendingPrompt(value.draft.submission||null);lastSaved.current=value.draft.saved;dirty.current=value.draft.dirty};window.addEventListener('termx-draft-handoff',receive);return()=>window.removeEventListener('termx-draft-handoff',receive)},[]);
 useEffect(()=>{if(ownerId&&session?.id){const value=drafts.current.get(session.id);if(value)void persistWindowDraft(ownerId,session.id,value).catch(onError)}},[ownerId,session?.id,draft,JSON.stringify(context),JSON.stringify(attachments),submissionChange]);
 function editAttachments(next:import('../lib/drafts').DraftAttachment[],baseText=draft,baseContext=context){setAttachments(next);if(session?.id){const old=drafts.current.get(session.id);drafts.current.set(session.id,{text:baseText,saved:lastSaved.current,context:baseContext,...old,attachments:next,dirty:old?.dirty||false})}}
 function editDraft(text:string,nextContext:ContextItem[]=context){setDraft(text);setContext(nextContext);dirty.current=true;if(session?.id){const old=drafts.current.get(session.id);drafts.current.set(session.id,{...old,text,context:nextContext,saved:old?.saved??lastSaved.current,savedContext:old?.savedContext??session.draft_context??[],dirty:true,revision:session.revision})}}
 useEffect(()=>{if(!incoming||!session?.id||(incoming.sessionId&&incoming.sessionId!==session.id)||consumedTransfers.has(incoming.id))return;const existing=drafts.current.get(session.id),baseText=existing?.text??session.draft_text??draft,baseContext=existing?.context??session.draft_context??context,baseImages=existing?.attachments||attachments;const selected=[...baseContext,...(incoming.context||[])];if(selected.length>16||new TextEncoder().encode(JSON.stringify(selected)).length>256000){onError(new Error('Browser context exceeds the 16-item or 256 KB draft limit. Remove a context item first.'));return}consumedTransfers.add(incoming.id);if(incoming.text||incoming.context?.length)editDraft(baseText+(incoming.text?(baseText?'\n\n':'')+incoming.text:''),selected);const safe=incoming.attachments.filter((item):item is {name:string;mime:string;data:string}=>!!item&&typeof item==='object'&&'data' in item&&'mime' in item&&'name' in item);if(safe.length)editAttachments([...baseImages,...safe].slice(0,4),baseText,selected)},[incoming?.id,session?.id]);

 useEffect(()=>{if(!session)return;const accept=(models:string[])=>setModelCatalogue({selection:modelSelection,models});if(session.runner_id){accept(runnerAccounts.find(account=>account.provider_id===session.provider_id)?.models||[])}else if(session.engine==='internal'){const provider=providers.find(p=>p.id===session.provider_id);accept((provider?.model||'').split(/[\n,]/).map(m=>m.trim()).filter(Boolean))}else{let cancelled=false;gql<{engine_models:string[]}>('query($id:String!){engine_models(engine_id:$id)}',{id:session.engine}).then(data=>{if(!cancelled)accept(data.engine_models)}).catch(error=>{if(!cancelled)onError(error)});return ()=>{cancelled=true}}},[modelSelection,providers,runnerAccounts]);
 async function saveDraft(identifier:string,text:string,previous:string,intent=captureSessionWrite()){
  try{
   if(!intent.isCurrent()||submittingDrafts.has(identifier))return;
   const existing=draftWrites.get(identifier);
   if(existing){await existing;if(!intent.isCurrent()||submittingDrafts.has(identifier))return;return await saveDraft(identifier,text,drafts.current.get(identifier)?.saved??previous,intent)}
   const flight=writeDraft(identifier,text,intent);draftWrites.set(identifier,flight);
   try{await flight}finally{if(draftWrites.get(identifier)===flight)draftWrites.delete(identifier)}
  }finally{intent.release()}
 }
 async function writeDraft(identifier:string,text:string,intent:SessionWriteIntent){
  try{await serializeSessionWrite(identifier,async()=>{
   const local=drafts.current.get(identifier);if(!local?.dirty||local.text!==text)return;
   const selected=local.context||[],baseline=local.saved;
   const current=await request<Session>('/api/workspace/sessions/'+identifier+'?turns=false',{signal:intent.signal});
   intent.assertCurrent();
   if((current.draft_text!==baseline&&current.draft_text!==text)||(!sameContext(current.draft_context,local.savedContext)&&!sameContext(current.draft_context,selected)))throw new Error('This draft changed in another window. Your local draft is preserved; copy it before switching conversations.');
   const saved=await request<Session>('/api/workspace/sessions/'+identifier,{...json('PATCH',{revision:current.revision,changes:{draft_text:text,draft_context:selected}}),signal:intent.signal});
   intent.assertCurrent();
   const latest=drafts.current.get(identifier);
   if(latest){
    // This PATCH is our acknowledged write, even if the user has typed ahead.
    // Advance only its saved base; preserve every newer local draft field.
    const stillDirty=latest.text!==saved.draft_text||!sameContext(latest.context,saved.draft_context);
    drafts.current.set(identifier,{...latest,saved:saved.draft_text,savedContext:saved.draft_context||[],dirty:stillDirty,revision:saved.revision});
    if(sessionId.current===identifier){lastSaved.current=saved.draft_text;dirty.current=stillDirty}
   }
  },intent)}catch(error){if(intent.isCurrent())onError(error)}
 }
 useEffect(()=>{if(!session||!dirty.current)return;const identifier=session.id,text=draft,previous=lastSaved.current;const intent=captureSessionWrite();const timer=setTimeout(()=>saveDraft(identifier,text,previous,intent),500);return ()=>clearTimeout(timer)},[draft,JSON.stringify(context),session?.id]);

 useEffect(()=>{if(scroller.current&&messages.length&&scroller.current.scrollHeight-scroller.current.scrollTop-scroller.current.clientHeight<350)virtual.scrollToIndex(messages.length-1,{align:'end'})},[messages.length,latest?.result]);
 async function patch(changes:Record<string,unknown>){
  if(!session)return false;const identifier=session.id,intent=captureSessionWrite();
  try{
   await serializeSessionWrite(identifier,async()=>{
    const current=await request<Session>('/api/workspace/sessions/'+identifier+'?turns=false',{signal:intent.signal});
    intent.assertCurrent();
    await request('/api/workspace/sessions/'+identifier,{...json('PATCH',{revision:current.revision,changes}),signal:intent.signal});
   },intent);
   onRefresh();return true;
  }catch(error){if(intent.isCurrent())onError(error);return false}finally{intent.release()}
 }
 async function send(intent:'send'|'queue'|'interrupt'='send'){
  if(!session||(!draft.trim()&&!pendingPrompt)||submittingDrafts.has(session.id))return;
  if(connectionBlocked){setActionError({sessionId:session.id,kind:'connection',message:'Connect an account and model before sending. Your draft is still here.'});return}
  const writeIntent=captureSessionWrite(),submission=Symbol();
  const identifier=session.id,prompt=pendingPrompt?.prompt||draft,key=pendingPrompt?.key||crypto.randomUUID(),selected=pendingPrompt?.context||context,selectedImages=pendingPrompt?.attachments||attachments.map(image=>({...image})),operation=pendingPrompt?.intent||intent,interruptTask=pendingPrompt?.interruptTask||(operation==='interrupt'?latest?.id:undefined);
  const pending={prompt,key,context:selected,attachments:selectedImages,intent:operation,interruptTask};
  setPendingPrompt(pending);
  const previous=drafts.current.get(identifier);drafts.current.set(identifier,{text:draft,saved:lastSaved.current,dirty:dirty.current,context,attachments,...previous,submission:pending});
  try{
   submittingDrafts.add(identifier);draftSubmissionOwners.set(identifier,submission);window.dispatchEvent(new Event('termx-draft-submission'));
   if(ownerId)await persistWindowDraft(ownerId,identifier,drafts.current.get(identifier)!);
   await draftWrites.get(identifier);writeIntent.assertCurrent();
   await serializeSessionWrite(identifier,()=>request('/api/workspace/sessions/'+identifier+(operation==='send'?'/turns':'/queue'),{...json('POST',{prompt,request_id:key,attachments:selectedImages.length?selectedImages:undefined,context:selected,...(interruptTask?{interrupt_task_id:interruptTask}:{})}),signal:writeIntent.signal}),writeIntent);
   if(operation!=='send')setQueueChange(value=>value+1);
   const local=drafts.current.get(identifier);
   if(!local||(local.text===prompt&&sameContext(local.context,selected)&&JSON.stringify(local.attachments||[])===JSON.stringify(selectedImages))){
    drafts.current.set(identifier,{text:'',saved:'',context:[],savedContext:[],dirty:false});
    if(sessionId.current===identifier){setDraft('');setContext([]);lastSaved.current='';dirty.current=false;setAttachments([]);setPendingPrompt(null)}
   }else{
    const current=await request<Session>('/api/workspace/sessions/'+identifier+'?turns=false',{signal:writeIntent.signal});writeIntent.assertCurrent();
    if((current.draft_text===''||current.draft_text===prompt)&&(sameContext(current.draft_context,[])||sameContext(current.draft_context,selected))){
     const latest=drafts.current.get(identifier);
     if(latest){drafts.current.set(identifier,{...latest,saved:current.draft_text,savedContext:current.draft_context||[]});if(sessionId.current===identifier)lastSaved.current=current.draft_text}
    }
   }
   const remaining=drafts.current.get(identifier);if(remaining){const {submission:_submission,...rest}=remaining;drafts.current.set(identifier,rest)}
   if(sessionId.current===identifier)setPendingPrompt(null);onRefresh();
  }catch(error){if(writeIntent.isCurrent())onError(error)}finally{
   if(draftSubmissionOwners.get(identifier)===submission){draftSubmissionOwners.delete(identifier);submittingDrafts.delete(identifier)}
   if(writeIntent.isCurrent()){const latest=drafts.current.get(identifier);if(latest?.dirty)void saveDraft(identifier,latest.text,latest.saved)}
   writeIntent.release();window.dispatchEvent(new Event('termx-draft-submission'));
  }
 }
 async function upload(files:FileList|File[]|null){if(!files)return;try{const next:{name:string;mime:string;data:string}[]=[];if(attachments.length+files.length>4)throw new Error('Attach at most four images per turn');for(const file of Array.from(files)){if(!['image/png','image/jpeg','image/webp'].includes(file.type))throw new Error('This session attachment path supports PNG, JPEG and WebP. Use project file context for code.');if(file.size>2_000_000)throw new Error('Image exceeds the 2 MB session attachment limit');const data=await new Promise<string>((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(String(reader.result).split(',')[1]);reader.onerror=reject;reader.readAsDataURL(file)});next.push({name:file.name,mime:file.type,data})}editAttachments([...attachments,...next])}catch(error){onError(error)}}
 async function chooseEngine(engine:string){if(!session||engine===session.engine)return;setForkEngine(engine);setForkOpen(true)}
 return <section className="chat-region coding-session" aria-label="Conversation">
  {session&&(latest||!!changes?.files)&&<ConversationSurfaceHeader task={latest} changes={changes} onOpenSurface={onOpenSurface} onReviewTask={reviewCurrentTask}/>}
  <div className="conversation-scroll" ref={scroller} tabIndex={0} role="log" aria-label="Conversation messages" aria-live="polite">
   {session?.hasEarlier&&<Button className="history-button" variant="ghost" onClick={onHistory}>Load earlier messages</Button>}
   {!messages.length?<ChatWelcome session={session} engines={engines} providers={providers} onPrompt={text=>{editDraft(text);composer.current?.focus()}} onConfigureAgent={onConfigureAgent} hideConnectionHelp/>:<div className="message-list" style={{height:virtual.getTotalSize()}}>
    {virtual.getVirtualItems().map(item=>{
     const message=messages[item.index];
     return <div key={message.id} data-index={item.index} data-message-id={message.id} ref={virtual.measureElement} className="message-row" style={{transform:`translateY(${item.start}px)`}}>
      <ConversationMessage role={message.role} text={message.text} task={message.task} onSupervise={setSupervised} onRefresh={onRefresh} onError={onError} visible={visible} onOpenFile={onOpenFile}/>
     </div>;
    })}
   </div>}
  </div>
  {awayFromLatest&&messages.length>0&&<Button className="jump-latest" variant="secondary" size="sm" onClick={()=>{virtual.scrollToIndex(messages.length-1,{align:'end'});setAwayFromLatest(false);scroller.current?.focus()}}>Jump to latest</Button>}
  <div className="composer-container">
   {session&&connectionProblem&&!running&&<ChatConnectionSetup key={(ownerId||'')+':'+session.id} session={session} providers={providers} engines={engines} enabled={visible&&!sending} onConfigureAgent={onConfigureAgent} onRefresh={onRefresh} onConnect={patch}/>}
   {session?.linked_from&&<div className="session-context"><span title="This conversation is a linked fork"><GitFork size={12}/> Linked fork</span></div>}
   {session&&<PromptQueue visible={visible} sessionId={session.id} changed={queueChange} onError={onError} onRefresh={onRefresh} onTask={setSupervised}/>}
   <PromptInputBody className="composer">
    {context.length>0&&<section className="composer-context" aria-label="Selected browser context">{context.map((item,index)=><div className="context-chip" key={index}><details><summary>{contextLabel(item)}</summary><p>{contextDescription(item)}</p><p>Shares this snapshot or file reference. Browser control requires a separate handoff.</p><pre role="region" tabIndex={0} aria-label="Exact selected context">{JSON.stringify(item,null,2)}</pre></details><Button size="icon" variant="ghost" aria-label={'Remove context '+contextLabel(item)} onClick={()=>editDraft(draft,context.filter((_,i)=>i!==index))}><X size={12}/></Button></div>)}</section>}
    {attachments.length>0&&<Attachments variant="inline" className="attachment-row">{attachments.map((item,index)=><Attachment className="ai-attachment" key={index} data={{id:String(index),type:'file',mediaType:item.mime,filename:item.name,url:'data:'+item.mime+';base64,'+item.data}} onRemove={()=>editAttachments(attachments.filter((_,i)=>i!==index))}><AttachmentPreview className="ai-attachment-preview"/><AttachmentInfo/><AttachmentRemove className="ai-attachment-remove" label={'Remove '+item.name}/></Attachment>)}</Attachments>}
    {pendingPrompt&&!sending&&<div className="composer-retry" role="status"><span>A previous send needs retry. Retry uses the same request; your newer draft stays separate.</span><Button size="sm" variant="ghost" onClick={()=>void send(pendingPrompt.intent)}>Retry send</Button></div>}
    {actionError?.sessionId===session?.id&&<p className="composer-action-error" role="alert">{actionError?.message}</p>}
    <PromptInputTextarea onFiles={files=>void upload(files)} onRemoveLast={attachments.length?()=>editAttachments(attachments.slice(0,-1)):undefined} ref={composer} aria-label="Message" placeholder={running?'Add a follow-up…':'Ask anything, or describe a change…'} value={draft} disabled={!session} onChange={e=>editDraft(e.target.value)} onKeyDown={e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.nativeEvent.isComposing){e.preventDefault();void send(running?'queue':'send')}}}/>
    <PromptInputTools>
     <input type="file" hidden ref={fileInput} accept="image/png,image/jpeg,image/webp" multiple onChange={e=>{const files=Array.from(e.target.files||[]);e.target.value='';void upload(files)}}/>
     <Button size="icon" variant="ghost" aria-label="Attach image" title="Attach image" disabled={!session} onClick={()=>fileInput.current?.click()}><Plus size={17}/></Button>
     <SessionModePicker session={session} running={running} onPatch={patch}/>
     <SessionModelControls coordinating={coordinating} session={session} engines={engines} providers={providers} runnerAccounts={runnerAccounts} models={models} favorites={favorites} favoriteKey={favoriteKey} running={running} onFavorite={favorite} onEngine={chooseEngine} onPatch={patch} onRefresh={onRefresh} onError={onError} onConfigureAgent={onConfigureAgent}/>
     <span className="flex-spacer"/>
     {running&&draft.trim()&&!pendingPrompt&&<Button className="composer-queue" size="sm" variant="ghost" aria-label="Queue follow-up" title="Queue follow-up after this task" disabled={sending||connectionBlocked} onClick={()=>void send('queue')}><ListPlus size={16}/><span>Queue</span></Button>}
     {running&&<Button size="icon" variant="ghost" aria-label="Running task actions" title="Queue, interrupt, or steer" aria-haspopup="dialog" aria-expanded={runActionsOpen} onClick={()=>setRunActionsOpen(true)}><MoreHorizontal size={17}/></Button>}
     <Button size="icon" variant="ghost" aria-label="Session options" title="Session options" aria-haspopup="dialog" aria-expanded={optionsOpen} disabled={!session} onClick={()=>setOptionsOpen(true)}><SlidersHorizontal size={15}/></Button>
     <PromptInputSubmit className="composer-send" status={running?'streaming':sending?'submitted':'ready'} aria-label={running?'Stop task':'Send message'} title={stopping?'Stopping task…':running?'Stop task':pendingPrompt?'Retry pending send':'Send message'} type="button" disabled={!session||stopping||(!running&&(connectionBlocked||(!draft.trim()&&!pendingPrompt)||sending))} onStop={running?()=>void stopTask():undefined} onClick={()=>void send()}>{stopping?<LoaderCircle className="spin" size={16}/>:running?<Square size={14}/>:sending?<LoaderCircle className="spin" size={17}/>:<ArrowUp size={17}/>}</PromptInputSubmit>
    </PromptInputTools>
   </PromptInputBody>
   <p className="composer-hint">{stopping?'Stopping task and its delegated agents…':sending?'Sending your message…':connectionBlocked&&!running?'Connect an account and model to send. Your draft stays here.':pendingPrompt?'Retry the pending message before sending your newer draft.':running?'Enter to queue a follow-up · Shift + Enter for a new line':'Enter to send · Shift + Enter for a new line'}</p>
  </div>
  <Dialog open={runActionsOpen&&running} onOpenChange={setRunActionsOpen}><DialogContent className="running-task-dialog"><DialogTitle>While the agent works</DialogTitle><DialogDescription>Choose how to use your follow-up.</DialogDescription><div className="running-task-actions">
   <Button variant="ghost" aria-label="Queue next" disabled={sending||connectionBlocked||!!pendingPrompt||!draft.trim()} onClick={()=>{setRunActionsOpen(false);void send('queue')}}><span>Queue next<small>Run your draft as a new turn after this task finishes.</small></span></Button>
   <Button variant="ghost" aria-label="Interrupt current task" disabled={sending||connectionBlocked||!draft.trim()||!!pendingPrompt} onClick={()=>{setRunActionsOpen(false);setInterruptOpen(true)}}><span>Interrupt current task<small>Stop this task, then queue your draft. You will review this first.</small></span></Button>
   <Button variant="ghost" aria-label="Steer current task" onClick={()=>{setRunActionsOpen(false);if(latest)setSupervised(latest.id)}}><span>Steer current task<small>Add an instruction at the task’s next safe step.</small></span></Button>
  </div></DialogContent></Dialog>
  <Dialog open={interruptOpen} onOpenChange={setInterruptOpen}><DialogContent><DialogTitle>Interrupt current task</DialogTitle><DialogDescription>Stop this task and its child tasks, then queue your current draft as the next turn. Effects already performed are preserved; this does not undo them.</DialogDescription><p>{draft}</p><Button disabled={sending||connectionBlocked||!running||!draft.trim()} onClick={()=>{setInterruptOpen(false);void send('interrupt')}}>Stop current task and queue draft</Button></DialogContent></Dialog>
  {supervised&&<TaskSupervisor taskId={supervised} onClose={()=>setSupervised('')} onError={onError}/>}
  <SessionSettings coordinating={coordinating} open={optionsOpen} onOpenChange={setOptionsOpen} session={session} running={running} turnsCount={turns.length} ownerId={ownerId} visible={visible} runnerOptions={runnerOptions} runnerAccounts={runnerAccounts} engines={engines} providers={providers} models={models} favorites={favorites} favoriteKey={favoriteKey} onFavorite={favorite} onEngine={chooseEngine} patch={patch} onError={onError} onRefresh={onRefresh} reply={latest?.result||''} onTranscript={text=>editDraft(draft+(draft?'\n\n':'')+text)} onConversationSettings={()=>{if(!session)return;setName(session.title);setGroup((session.group_id===undefined?session.project_id:session.group_id)||'');setRenameOpen(true);setOptionsOpen(false)}}/>
  <Dialog open={renameOpen} onOpenChange={setRenameOpen}><DialogContent><DialogTitle>Conversation settings</DialogTitle><DialogDescription>Moving the sidebar group keeps the execution folder and project permissions.</DialogDescription><label>Name<input className="input" value={name} onChange={event=>setName(event.target.value)} maxLength={200}/></label><label>Sidebar group<Select value={group} onChange={event=>setGroup(event.target.value)}><option value="">Unassigned</option>{projects.map(project=><option key={project.id} value={project.id}>{project.name}</option>)}</Select></label><Button onClick={async()=>{await patch({title:name,group_id:group||null});setRenameOpen(false)}}>Save settings</Button></DialogContent></Dialog>
  {forkOpen&&session&&<ForkContextDialog key={session.id+':'+forkEngine} session={session} engine={forkEngine} onClose={()=>setForkOpen(false)} onCreated={id=>{setForkOpen(false);onSelect(id);onRefresh()}} onError={onError} onHistory={onHistory}/>}
 </section>
}
