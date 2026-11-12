// Persist only opaque intent IDs and digests, never prompts, files or credentials.
// Each outstanding request keeps its own receipt until its exact acceptance is known.
function canonicalJson(value: unknown): string {
 const normalize=(item: any): any => {
  if(item&&typeof item==='object'&&!Array.isArray(item)){
   if(typeof item.toJSON==='function')return normalize(item.toJSON());
   return Object.fromEntries(Object.keys(item).sort().filter(key=>item[key]!==undefined).map(key=>[key,normalize(item[key])]));
  }
  return Array.isArray(item)?item.map(normalize):item;
 };
 return JSON.stringify(normalize(value));
}
async function hash(value:string){return Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(value))),byte=>byte.toString(16).padStart(2,'0')).join('')}
function readIntent(key:string){
 const raw=localStorage.getItem(key);
 if(!raw)return null;
 try{const item=JSON.parse(raw);return item&&typeof item.id==='string'&&/^[A-Za-z0-9_-]{8,128}$/.test(item.id)&&typeof item.digest==='string'?item:null}catch{return null}
}
export async function mediaIntent(owner:string|undefined,project:string|null,args:Record<string,unknown>,kind='media'){
 const serialized=JSON.stringify(args),canonical=canonicalJson(args),digest=await hash(canonical);
 const legacyDigest=serialized===canonical?digest:await hash(serialized);
 const base=owner?'termx-'+kind+'-intent:'+owner+':'+(project||'unassigned'):undefined;
 const key=base?base+':'+digest:undefined;
 const obtain=()=>{
  if(key&&base){
   const prior=readIntent(key);
   if(prior?.digest===digest)return {id:prior.id as string,key};
   // Carry forward an outstanding receipt from the former single-slot format.
   const legacy=readIntent(base);
   if(legacy&&(legacy.digest===digest||legacy.digest===legacyDigest)){
    localStorage.setItem(key,JSON.stringify({id:legacy.id,digest}));
    const current=readIntent(base);if(current?.id===legacy.id&&current.digest===legacy.digest)localStorage.removeItem(base);
    return {id:legacy.id as string,key};
   }
  }
  const id=crypto.randomUUID();if(key)localStorage.setItem(key,JSON.stringify({id,digest}));return {id,key};
 };
 // Share the old scope lock so migration and multiple request kinds cannot race.
 return base&&navigator.locks?navigator.locks.request(base,obtain):obtain();
}
export function clearMediaIntent(key:string|undefined,id:string){
 if(!key)return;
 const erase=()=>{try{const prior=readIntent(key);if(prior?.id===id)localStorage.removeItem(key)}catch{}};
 const suffix=key.slice(key.lastIndexOf(':')+1),base=/^[a-f0-9]{64}$/.test(suffix)?key.slice(0,key.lastIndexOf(':')):key;
 if(navigator.locks)void navigator.locks.request(base,erase).catch(()=>{});else erase();
}
