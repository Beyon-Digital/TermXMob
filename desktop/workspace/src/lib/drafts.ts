import type {ContextItem} from './context';
// This in-memory registry survives lazy panel replacement; it never stores credentials.
export type DraftAttachment={name:string;mime:string;data:string};
export type PendingSubmission={prompt:string;key:string;context:ContextItem[];attachments:DraftAttachment[];intent?:'send'|'queue'|'interrupt';interruptTask?:string};
export type Draft={text:string;saved:string;dirty:boolean;revision?:number;context?:ContextItem[];savedContext?:ContextItem[];attachments?:DraftAttachment[];submission?:PendingSubmission};
export const sessionDrafts=new Map<string,Draft>();
export const draftWrites=new Map<string,Promise<void>>();
export const submittingDrafts=new Set<string>();
export const draftSubmissionOwners=new Map<string,symbol>();
export const consumedTransfers=new Set<string>();
if(typeof window!=='undefined')window.addEventListener('termx-signed-out',()=>{sessionDrafts.clear();draftWrites.clear();submittingDrafts.clear();draftSubmissionOwners.clear();consumedTransfers.clear()});
