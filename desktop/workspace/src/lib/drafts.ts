// This in-memory registry survives lazy panel replacement; it never stores credentials.
export type Draft={text:string;saved:string;dirty:boolean;revision?:number};
export const sessionDrafts=new Map<string,Draft>();
if(typeof window!=='undefined')window.addEventListener('termx-signed-out',()=>sessionDrafts.clear());
