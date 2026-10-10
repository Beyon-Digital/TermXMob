import { nativeWorkspace, nativeRequest, nativeLogin, nativeSignOut, nativeLockState, nativeUnlock, nativeUnlockOidc } from './native';
export class ApiError extends Error {constructor(public status:number,message:string){super(message)}}
export function csrf(){const value=document.cookie.split('; ').find(value=>value.startsWith('termx_csrf='));return value?decodeURIComponent(value.slice('termx_csrf='.length)):'';}
let refreshing:Promise<void>|undefined;
const channel=typeof BroadcastChannel!=='undefined'?new BroadcastChannel('termx-session-state'):undefined;
channel?.addEventListener('message',event=>{if(['signed-out','session-locked','session-unlocked'].includes(event.data))window.dispatchEvent(new Event('termx-'+event.data))});
function locked(){channel?.postMessage('session-locked');window.dispatchEvent(new Event('termx-session-locked'));}
export async function refreshSession(){
 if(refreshing)return refreshing;
 refreshing=(async()=>{
  if(!navigator.locks)throw new ApiError(401,'Session expired. Sign in again; this browser cannot safely coordinate refresh across windows.');
  await navigator.locks.request('termx-session-refresh',async()=>{
   const current=await fetch('/auth/me',{credentials:'same-origin'});
   if(current.ok)return; // Another window already refreshed while this one waited.
   const response=await fetch('/auth/refresh',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-Termx-CSRF':csrf()},body:'{}'});
   if(response.status===423){locked();throw new ApiError(423,'Session locked; unlock required');}
   if(!response.ok){channel?.postMessage('signed-out');window.dispatchEvent(new Event('termx-signed-out'));throw new ApiError(401,'Session expired or revoked. Sign in again.');}
  });
 })().finally(()=>{refreshing=undefined});return refreshing;
}
export async function request<T>(path:string,init:RequestInit={},retry=true):Promise<T>{
 if(!path.startsWith('/')||path.startsWith('//')||path.includes('\\'))throw new Error('API requests must stay on this workspace origin');
 if(nativeWorkspace() && !path.startsWith('/auth/oidc/')){
  try { const response=await nativeRequest<T>(path,init);if(response.status===423)locked();if(response.status<200||response.status>=300){const value=response.body as {detail?:unknown};throw new ApiError(response.status,typeof value?.detail==='string'?value.detail:JSON.stringify(value));}return response.body; }
  catch(error){if(error instanceof ApiError)throw error;const message=error instanceof Error?error.message:String(error);if(/Session locked/.test(message)){locked();throw new ApiError(423,message);}if(/expired|revoked|Sign in required/.test(message)){channel?.postMessage('signed-out');window.dispatchEvent(new Event('termx-signed-out'));throw new ApiError(401,message);}throw new Error(message);}
 }
 const headers=new Headers(init.headers);
 if(init.body&&!headers.has('Content-Type'))headers.set('Content-Type','application/json');
 if(!['GET','HEAD'].includes((init.method||'GET').toUpperCase()))headers.set('X-Termx-CSRF',csrf());
 const controller=new AbortController(),upstream=init.signal;
 let timedOut=false;
 const abort=()=>controller.abort();
 if(upstream?.aborted)controller.abort();else upstream?.addEventListener('abort',abort,{once:true});
 const timer=setTimeout(()=>{timedOut=true;controller.abort()},30000);
 let response:Response,payload:any;
 try{
  response=await fetch(path,{...init,headers,credentials:'same-origin',signal:controller.signal});
  if(response.status!==204){try{payload=await response.json()}catch(error){if(controller.signal.aborted)throw error;if(response.ok)throw new Error('The host returned an invalid response. Refresh and try again.');payload={detail:response.statusText}}}
 }catch(error){if(timedOut)throw new ApiError(504,init.method&&init.method!=='GET'?'The host request timed out. The operation may have completed; refresh its status before retrying.':'The host request timed out. Check the connection and retry.');throw error}
 finally{clearTimeout(timer);upstream?.removeEventListener('abort',abort)}
 if(response.status===401&&retry&&(!path.startsWith('/auth/')||path==='/auth/me')){await refreshSession();return request<T>(path,init,false);}
 if(response.status===423)locked();
 if(!response.ok){const body=payload||{};throw new ApiError(response.status,typeof (body.detail||body.error)==='string'?(body.detail||body.error):JSON.stringify(body.detail||body.error||body));}
 if(response.status===204)return undefined as T;
 return payload as T;
}
export async function gql<T>(query:string,variables:Record<string,unknown>={}):Promise<T>{const body=await request<{data?:T;errors?:{message:string}[]}>('/graphql',{method:'POST',body:JSON.stringify({query,variables})});if(body.errors?.length)throw new Error(body.errors.map(error=>error.message).join('\n'));return body.data!;}
export function json(method:string,data?:unknown):RequestInit{return {method,body:data===undefined?undefined:JSON.stringify(data)}};
export async function signOut(){if(nativeWorkspace())await nativeSignOut();else await request('/auth/logout',json('POST',{}),false);channel?.postMessage('signed-out');window.dispatchEvent(new Event('termx-signed-out'));}
export interface Project{id:string;path:string;name:string}
export interface Task{id:string;status:string;prompt:string;result?:string;error?:string;engine?:string;limits?:Record<string,number>;events?:{type:string;payload:Record<string,unknown>;sequence:number}[];approvals?:{id:string;status:string;kind:string;payload:Record<string,unknown>}[];children?:Task[]}
export interface Turn{id:string;prompt:string;task_id?:string;task?:Task;sequence:number}
export interface Session{reasoning_config?:Record<string,string>;custom_agent_id?:string|null;custom_agent_revision?:string|null;id:string;title:string;project_id?:string;cwd?:string;engine:string;provider_id?:string;model?:string;mode:string;pinned:boolean;archived:boolean;draft_text:string;draft_context?:import('./context').ContextItem[];revision:number;turns?:Turn[];linked_from?:string;updated_at:number;scroll:number;scroll_anchor?:import('./conversation-position').ConversationAnchor|null;extension_ids?:string[];workflow?:string|null;hasEarlier?:boolean;group_id?:string|null;runner_id?:string|null;runner_credential_ref?:string|null;run_limits?:Record<string,number>;latest_status?:string|null;worktree_id?:string|null;worktree_digest?:string|null;worktree_branch?:string|null}
export interface Engine{id:string;name?:string;available?:boolean;capabilities?:Record<string,unknown>}
export interface Provider{id:string;name:string;model:string;capabilities:string[];secret_configured:boolean}

export async function login(username:string,password:string,setup=false,bootstrap=''){
 if(nativeWorkspace())return nativeLogin(username,password,setup);
 await request(setup?'/auth/setup':'/auth/login',{...json('POST',{method:'local-password',username,password,transport:'cookie',device_name:'Desktop workspace'}),headers:bootstrap?{'X-Termx-Passcode':bootstrap}:undefined},false);
 return request('/auth/me');
}

export interface LockState {locked:boolean;principal:{id:string;display_name:string};session_id:string;host_id:string;methods:{id:string;label:string;flow?:'credentials'|'redirect'}[]}
export interface UnlockEvidence {method:string;username?:string;password?:string;assertion?:string}
export async function lockState():Promise<LockState>{return nativeWorkspace()?nativeLockState():request<LockState>('/auth/lock-state',{},false)}
export async function lockWorkspace(){try{await request('/auth/lock',json('POST',{}),false)}catch(error){if(error instanceof ApiError&&error.status===503&&error.message.startsWith('Session locked;'))locked();throw error}locked()}
export async function unlockWorkspace(evidence:UnlockEvidence){
 if(nativeWorkspace())await nativeUnlock(evidence);else await request('/auth/unlock',json('POST',evidence),false);
 channel?.postMessage('session-unlocked');window.dispatchEvent(new Event('termx-session-unlocked'));return request('/auth/me',{},false);
}
export async function beginUnlockOidc(method:string){
 if(nativeWorkspace())return nativeUnlockOidc(method);
 return request<{authorization_url:string}>(`/auth/oidc/${encodeURIComponent(method)}/unlock-begin`,json('POST',{}),false);
}
