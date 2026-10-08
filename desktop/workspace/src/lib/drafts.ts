import type {ContextItem} from './context';
// This in-memory registry survives lazy panel replacement; it never stores credentials.
export type DraftAttachment={name:string;mime:string;data:string};
export type Draft={text:string;saved:string;dirty:boolean;revision?:number;context?:ContextItem[];savedContext?:ContextItem[];attachments?:DraftAttachment[]};
export const sessionDrafts=new Map<string,Draft>();
export const draftWrites=new Map<string,Promise<void>>();
export const submittingDrafts=new Set<string>();
export const consumedTransfers=new Set<string>();
if(typeof window!=='undefined')window.addEventListener('termx-signed-out',()=>{sessionDrafts.clear();draftWrites.clear();submittingDrafts.clear();consumedTransfers.clear()});
