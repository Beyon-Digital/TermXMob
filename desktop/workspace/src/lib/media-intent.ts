// Persist only opaque intent IDs and a digest, never prompts, files or credentials.
// Reusing the ID survives reloads and multiple windows. The host is authoritative.
export async function mediaIntent(owner:string|undefined,project:string|null,args:Record<string,unknown>,kind='media'){
 const encoded=new TextEncoder().encode(JSON.stringify(args));
 const digest=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',encoded)),byte=>byte.toString(16).padStart(2,'0')).join('');
 const key=owner?'termx-'+kind+'-intent:'+owner+':'+(project||'unassigned'):undefined;
 const obtain=()=>{
  if(key){try{const prior=JSON.parse(localStorage.getItem(key)||'null');if(prior?.digest===digest&&typeof prior.id==='string')return {id:prior.id as string,key}}catch{}}
  const id=crypto.randomUUID();if(key)localStorage.setItem(key,JSON.stringify({id,digest}));return {id,key};
 };
 return key&&navigator.locks? navigator.locks.request(key,obtain):obtain();
}
export function clearMediaIntent(key:string|undefined,id:string){
 if(!key)return;
 try{const prior=JSON.parse(localStorage.getItem(key)||'null');if(prior?.id===id)localStorage.removeItem(key)}catch{}
}
