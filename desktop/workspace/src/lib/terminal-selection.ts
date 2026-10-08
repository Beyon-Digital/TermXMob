// Per-window selection metadata is not a grant. The fresh host inventory must
// authorize the same PTY before TerminalPanel may reconnect after reload.
const prefix='termx-terminal-selection:';
const valid=(value:unknown):value is string=>typeof value==='string'&&value.length>0&&value.length<=128;
const key=(owner:string,session:string)=>prefix+JSON.stringify([owner,session]);
export function readTerminalSelection(owner:string,session:string){if(!valid(owner)||!valid(session))return '';try{const value=sessionStorage.getItem(key(owner,session));return valid(value)?value:''}catch{return ''}}
export function saveTerminalSelection(owner:string,session:string,id:string){if(!valid(owner)||!valid(session)||!valid(id))return;try{sessionStorage.setItem(key(owner,session),id);const keys=Array.from({length:sessionStorage.length},(_,index)=>sessionStorage.key(index)).filter((value):value is string=>!!value?.startsWith(prefix));for(const old of keys.slice(0,Math.max(0,keys.length-100)))sessionStorage.removeItem(old)}catch{/* The live PTY remains discoverable when window storage is unavailable. */}}
export function clearTerminalSelections(){try{for(let index=sessionStorage.length-1;index>=0;index--){const value=sessionStorage.key(index);if(value?.startsWith(prefix))sessionStorage.removeItem(value)}}catch{/* Storage may be unavailable. */}}
if(typeof window!=='undefined')window.addEventListener('termx-signed-out',clearTerminalSelections);
