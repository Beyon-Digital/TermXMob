import type {ContextItem} from './context';
// This in-memory registry survives lazy panel replacement; it never stores credentials.
export type DraftAttachment={name:string;mime:string;data:string};
export type PendingSubmission={prompt:string;key:string;context:ContextItem[];attachments:DraftAttachment[];intent?:'send'|'queue'|'interrupt';interruptTask?:string};
export type Draft={text:string;saved:string;dirty:boolean;revision?:number;context?:ContextItem[];savedContext?:ContextItem[];attachments?:DraftAttachment[];submission?:PendingSubmission};
// Plain registries for non-React callers (window journal, redock, lock preservation). React reads them
// through `useSessionDraft`, which re-renders when the store below publishes a change.
export const sessionDrafts=new Map<string,Draft>();
export const draftWrites=new Map<string,Promise<void>>();
export const submittingDrafts=new Set<string>();
export const draftSubmissionOwners=new Map<string,symbol>();
export const consumedTransfers=new Set<string>();
// Shared by retained/replaced chat panels so one Stop action stays in flight.
export const stoppingTasks=new Set<string>();

export type DraftHandoff={seq:number;sessionId:string;draft:Draft|null};
export type DraftStoreState={submission:number;stopping:number;handoff:DraftHandoff|null};
let state:DraftStoreState={submission:0,stopping:0,handoff:null};
const listeners=new Set<() => void>();
const publish=(next:Partial<DraftStoreState>)=>{state={...state,...next};listeners.forEach(listener=>listener())};
/** `useSyncExternalStore` contract: the snapshot only changes identity when something was published. */
export const subscribeDrafts=(listener:()=>void)=>{listeners.add(listener);return()=>{listeners.delete(listener)}};
export const draftStoreState=()=>state;
/** Call after changing a draft's pending submission without going through `beginSubmission` or `endSubmission`. */
export const announceSubmissionChange=()=>publish({submission:state.submission+1});
export function beginSubmission(sessionId:string,owner:symbol){submittingDrafts.add(sessionId);draftSubmissionOwners.set(sessionId,owner);announceSubmissionChange()}
/** Only the owner that began a submission may end it, so a replaced panel cannot clear a newer one. */
export function endSubmission(sessionId:string,owner:symbol){if(draftSubmissionOwners.get(sessionId)===owner){draftSubmissionOwners.delete(sessionId);submittingDrafts.delete(sessionId)}announceSubmissionChange()}
export function setTaskStopping(taskId:string,stopping:boolean){if(stopping)stoppingTasks.add(taskId);else stoppingTasks.delete(taskId);publish({stopping:state.stopping+1})}
/** A draft arrived from another window; mounted conversations for that session adopt it. */
export const publishDraftHandoff=(sessionId:string,draft:Draft|null)=>publish({handoff:{seq:(state.handoff?.seq??0)+1,sessionId,draft}});
if(typeof window!=='undefined')window.addEventListener('termx-signed-out',()=>{sessionDrafts.clear();draftWrites.clear();submittingDrafts.clear();draftSubmissionOwners.clear();consumedTransfers.clear();stoppingTasks.clear();publish({submission:state.submission+1,stopping:state.stopping+1,handoff:null})});
