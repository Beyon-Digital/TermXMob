import {snapshotArtifactDrafts} from './artifact-drafts';
import {draftWrites,sessionDrafts,submittingDrafts} from './drafts';
import {json,request,type Session} from './api';
import {snapshotBuffers,recalledTerminal,type WindowState} from './window-state';
const same=(a:unknown,b:unknown)=>JSON.stringify(a||[])===JSON.stringify(b||[]);
export async function flushRedockDraft(sessionId:string){
 if(submittingDrafts.has(sessionId))throw new Error('Wait for the message to finish dispatching before returning this window');
 await draftWrites.get(sessionId);const draft=sessionDrafts.get(sessionId);if(!draft?.dirty)return;
 const current=await request<Session>('/api/workspace/sessions/'+sessionId+'?turns=false');
 if((current.draft_text!==draft.saved&&current.draft_text!==draft.text)||(!same(current.draft_context,draft.savedContext)&&!same(current.draft_context,draft.context)))throw new Error('The conversation has another unsent draft. Resolve it before returning this window. Your local draft remains here.');
 const saved=await request<Session>('/api/workspace/sessions/'+sessionId,json('PATCH',{revision:current.revision,changes:{draft_text:draft.text,draft_context:draft.context||[]}}));
 sessionDrafts.set(sessionId,{...draft,saved:saved.draft_text,savedContext:saved.draft_context||[],dirty:false,revision:saved.revision});
}
export function snapshotWindow(owner:string,sessionId:string):WindowState{return {version:1,sessionId,draft:structuredClone(sessionDrafts.get(sessionId)||null),buffers:snapshotBuffers(owner),artifacts:snapshotArtifactDrafts(owner),terminalId:recalledTerminal(sessionId)||undefined}}
export function acceptTransferredDraft(state:WindowState){const existing=sessionDrafts.get(state.sessionId);if(state.draft&&existing&&((existing.dirty&&(existing.text!==state.draft.text||!same(existing.context,state.draft.context)))||(!!existing.attachments?.length&&!same(existing.attachments,state.draft.attachments))))throw new Error('Main workspace has a different unsent draft. Keep both windows open to review it.');if(state.draft)sessionDrafts.set(state.sessionId,structuredClone(state.draft));window.dispatchEvent(new CustomEvent('termx-draft-handoff',{detail:{sessionId:state.sessionId,draft:state.draft}}))}
