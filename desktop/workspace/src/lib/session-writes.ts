// Drafts, scroll anchors, and next-turn controls share the same optimistic
// session revision. Keep each local read + write together so those independent
// surfaces cannot race each other. Server/cross-window conflicts still surface;
// a rejected mutation is never blindly replayed.
const pending=new Map<string,Promise<void>>();
const signals=new Set<AbortController>();
let generation=0;
if(typeof window!=='undefined')for(const event of ['termx-signed-out','termx-session-locked'])window.addEventListener(event,()=>{generation++;for(const controller of signals)controller.abort();signals.clear();pending.clear()});

export type SessionWriteIntent={readonly signal:AbortSignal;isCurrent:()=>boolean;assertCurrent:()=>void;release:()=>void};
/** Capture before an intent waits for a debounce, draft flush, or another write. */
export function captureSessionWrite():SessionWriteIntent{
 const epoch=generation;
 let controller:AbortController|undefined;
 return {
  // Debounced intents that never start need no listener/controller cleanup.
  get signal(){if(!controller){controller=new AbortController();if(epoch!==generation)controller.abort();else signals.add(controller)}return controller.signal},
  isCurrent:()=>epoch===generation,
  assertCurrent:()=>{if(epoch!==generation)throw new Error('The workspace locked or the account changed before this conversation could be saved.');},
  release:()=>{if(controller)signals.delete(controller)},
 };
}

export async function serializeSessionWrite<T>(identifier:string,write:(intent:SessionWriteIntent)=>Promise<T>,captured?:SessionWriteIntent):Promise<T>{
 const intent=captured||captureSessionWrite();
 const operation=(pending.get(identifier)||Promise.resolve()).then(()=>{
  intent.assertCurrent();
  return write(intent);
 }).then(result=>{intent.assertCurrent();return result});
 const settled=operation.then(()=>{},()=>{});
 pending.set(identifier,settled);
 try{return await operation}finally{if(pending.get(identifier)===settled)pending.delete(identifier);if(!captured)intent.release()}
}
