import {useMemo} from 'react';
import {Code2,FileDiff,Globe,TerminalSquare,LoaderCircle} from 'lucide-react';
import {Button} from '../components/ui/button';
import {taskState} from '../lib/task-presentation';
import {toolActivities} from '../lib/tool-activity';
import type {Task} from '../lib/api';

export type ConversationSurface='review'|'browser'|'terminal'|'editor';
export type ConversationChanges={files:number;additions?:number;deletions?:number;loading?:boolean};
export default function ConversationSurfaceHeader({task,changes,onOpenSurface,onReviewTask}:{task?:Task;changes?:ConversationChanges;onOpenSurface?:(surface:ConversationSurface)=>void;onReviewTask:()=>void}){
 const state=taskState(task?.status),events=task?.events;
 const activity=useMemo(()=>toolActivities(events||[]).at(-1),[events]);
 const needsReview=!!task&&['awaiting_approval','paused','recovery_confirmation_required','failed'].includes(task.status);
 return <header className="conversation-surface-header" aria-label="Conversation tools">
  <div className={'conversation-task-state '+(task?state.tone:'')} role={task?'status':undefined}>
   {state.active?<LoaderCircle size={12} className="spin"/>:task?<span className={'status-dot '+task.status}/>:null}
   <span>{task?state.label:'Conversation'}</span>{state.active&&activity&&<small title={activity.title}>{activity.title}</small>}
  </div>
  {needsReview&&<Button size="sm" variant="ghost" className="conversation-task-review" aria-label="Review current task" onClick={onReviewTask}>{task?.status==='failed'?'Review error':'Review task'}</Button>}
  <span className="flex-spacer"/>
  {onOpenSurface&&<nav aria-label="Conversation views">
   <Button className="conversation-changes-button" variant="ghost" size="sm" aria-label="Review changes" title="Review changes" onClick={()=>onOpenSurface('review')}>
    {changes?.loading?<LoaderCircle size={13} className="spin"/>:<FileDiff size={13}/>}<span>{changes&&changes.files>0?`${changes.files} file${changes.files===1?'':'s'}`:'Review changes'}</span>
    {changes&&changes.additions!==undefined&&changes.deletions!==undefined&&changes.files>0&&<small><span className="changes-additions">+{changes.additions}</span><span className="changes-deletions">−{changes.deletions}</span></small>}
   </Button>
   <Button variant="ghost" size="icon" aria-label="Open browser" title="Open browser" onClick={()=>onOpenSurface('browser')}><Globe size={15}/></Button>
   <Button variant="ghost" size="icon" aria-label="Open terminal" title="Open terminal" onClick={()=>onOpenSurface('terminal')}><TerminalSquare size={15}/></Button>
   <Button variant="ghost" size="icon" aria-label="Open editor" title="Open editor" onClick={()=>onOpenSurface('editor')}><Code2 size={15}/></Button>
  </nav>}
 </header>;
}
