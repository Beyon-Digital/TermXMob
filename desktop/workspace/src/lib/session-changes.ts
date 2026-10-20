import {useCallback,useEffect,useRef,useState} from 'react';
import {request,type Project,type Session} from './api';

export interface SessionChangeFile{path:string;previous_path?:string;status:'added'|'modified'|'deleted'|'renamed'|'conflict';index:string;worktree:string;staged:boolean;untracked:boolean;additions:number|null;deletions:number|null;binary:boolean;truncated?:boolean;reviewable:boolean}
export interface SessionChanges{repo:boolean;branch:string|null;head:string|null;files:SessionChangeFile[];additions:number;deletions:number;counts_complete:boolean;truncated:boolean}
export interface SessionChangesSource{data:SessionChanges|null;loading:boolean;error:string|null;refresh:()=>Promise<void>;available:boolean;unavailableReason:string|null;scope:string}
export interface SessionChangesOptions{ownerId?:string;project:Project|null;session:Session|null;visible?:boolean}
export function sessionChangesPath(session:Session,diffPath?:string){const params=new URLSearchParams();if(session.worktree_id)params.set('worktree_id',session.worktree_id);if(diffPath!==undefined)params.set('path',diffPath);const query=params.toString();return '/api/workspace/sessions/'+encodeURIComponent(session.id)+'/changes'+(diffPath!==undefined?'/diff':'')+(query?'?'+query:'')}

/** One checkout-bound source can feed both the chat summary and review panel. */
export function useSessionChanges({ownerId,project,session,visible=true}:SessionChangesOptions):SessionChangesSource{
 const scope=JSON.stringify([ownerId,project?.id,session?.id,session?.cwd,session?.worktree_id,session?.worktree_digest,session?.runner_id]);
 const unavailableReason=!session?'Choose a conversation to review its changes.':!project||session.project_id!==project.id?'Choose a project conversation to review changes.':session.runner_id?'Cloud changes belong to the runner workspace. Local checkout review is unavailable.':null;
 const available=!unavailableReason;
 const [snapshot,setSnapshot]=useState<{scope:string;data:SessionChanges}|null>(null),[failure,setFailure]=useState<{scope:string;message:string}|null>(null),[loading,setLoading]=useState(false),[paused,setPaused]=useState(false),[foreground,setForeground]=useState(()=>document.visibilityState!=='hidden');
 const currentScope=useRef(scope),flight=useRef<AbortController|null>(null),serial=useRef(0);currentScope.current=scope;
 const refresh=useCallback(async()=>{
  if(!available||!session||!visible||paused||!foreground)return;
  flight.current?.abort();const controller=new AbortController();flight.current=controller;const run=++serial.current;setLoading(true);
  try{const data=await request<SessionChanges>(sessionChangesPath(session),{signal:controller.signal});if(controller.signal.aborted||currentScope.current!==scope||serial.current!==run)return;setSnapshot({scope,data});setFailure(null)}
  catch(error){if(controller.signal.aborted||currentScope.current!==scope||serial.current!==run)return;setSnapshot(null);setFailure({scope,message:error instanceof Error?error.message:String(error)})}
  finally{if(currentScope.current===scope&&serial.current===run){setLoading(false);flight.current=null}}
 },[available,session?.id,session?.worktree_id,scope,visible,paused,foreground]);
 useEffect(()=>{const visibility=()=>setForeground(document.visibilityState!=='hidden');const lock=()=>{setPaused(true);flight.current?.abort();serial.current++;setLoading(false);setSnapshot(null)};const unlock=()=>setPaused(false);document.addEventListener('visibilitychange',visibility);window.addEventListener('termx-session-locked',lock);window.addEventListener('termx-locked',lock);window.addEventListener('termx-signed-out',lock);window.addEventListener('termx-session-unlocked',unlock);return()=>{document.removeEventListener('visibilitychange',visibility);window.removeEventListener('termx-session-locked',lock);window.removeEventListener('termx-locked',lock);window.removeEventListener('termx-signed-out',lock);window.removeEventListener('termx-session-unlocked',unlock)}},[]);
 useEffect(()=>{setPaused(false)},[ownerId]);
 useEffect(()=>{let stopped=false,timer:ReturnType<typeof setTimeout>|undefined;const poll=async()=>{await refresh();if(!stopped&&available&&visible&&!paused&&foreground)timer=setTimeout(()=>void poll(),5000)};void poll();return()=>{stopped=true;clearTimeout(timer);flight.current?.abort();serial.current++}},[refresh,available,visible,paused,foreground]);
 return {scope,available:available&&!paused,unavailableReason:paused?'Unlock the workspace to review changes.':unavailableReason,data:!paused&&snapshot?.scope===scope?snapshot.data:null,loading:available&&visible&&!paused&&loading,error:failure?.scope===scope?failure.message:null,refresh};
}
