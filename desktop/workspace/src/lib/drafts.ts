import type {ContextItem} from './context';
// This in-memory registry survives lazy panel replacement; it never stores credentials.
export type Draft={text:string;saved:string;dirty:boolean;revision?:number;context?:ContextItem[];savedContext?:ContextItem[]};
export const sessionDrafts=new Map<string,Draft>();
export const consumedTransfers=new Set<string>();
if(typeof window!=='undefined')window.addEventListener('termx-signed-out',()=>{sessionDrafts.clear();consumedTransfers.clear()});
