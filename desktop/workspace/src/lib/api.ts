import { nativeWorkspace, nativeRequest, nativeLogin, nativeSignOut, nativeLockState, nativeUnlock, nativeUnlockOidc } from './native';
export class ApiError extends Error {constructor(public status:number,message:string){super(message)}}
export function csrf(){const value=document.cookie.split('; ').find(value=>value.startsWith('termx_csrf='));return value?decodeURIComponent(value.slice('termx_csrf='.length)):'';}
let refreshing:Promise<void>|undefined;
const channel=typeof BroadcastChannel!=='undefined'?new BroadcastChannel('termx-session-state'):undefined;
channel?.addEventListener('message',event=>{if(['signed-out','session-locked','session-unlocked'].includes(event.data))window.dispatchEvent(new Event('termx-'+event.data))});
function locked(){channel?.postMessage('session-locked');window.dispatchEvent(new Event('termx-session-locked'));}
function signedOut(){channel?.postMessage('signed-out');window.dispatchEvent(new Event('termx-signed-out'));}
export async function refreshSession(){
 if(refreshing)return refreshing;
 refreshing=(async()=>{
  // The native bridge owns refresh credentials and serializes all windows.
  if(nativeWorkspace()){await request('/auth/me',{},false);return;}
  if(!navigator.locks)throw new ApiError(503,'This browser cannot safely coordinate session refresh across windows. Open the workspace in a browser with secure Web Locks support.');
  await navigator.locks.request('termx-session-refresh',async()=>{
   try{await request('/auth/me',{},false);return;} // Another window may have refreshed while this one waited.
   catch(error){if(!(error instanceof ApiError)||error.status!==401)throw error;}
   // A fresh browser has no refresh proof. Do not turn ordinary sign-in into
   // an invalid-CSRF error by POSTing an empty refresh request. A current
   // authenticated session was checked above, so no valid access is discarded.
   if(!csrf())throw new ApiError(401,'Session expired or revoked. Sign in again.');
   try{await request('/auth/refresh',{method:'POST',body:'{}'},false);}
   catch(error){
    // A timeout, unavailable host, CSRF race or locked session is not proof
    // that the refresh family was revoked. Keep the authenticated UI intact.
    if(error instanceof ApiError&&error.status===401){signedOut();throw new ApiError(401,'Session expired or revoked. Sign in again.');}
    throw error;
   }
  });
 })().finally(()=>{refreshing=undefined});return refreshing;
}
export async function request<T>(path:string,init:RequestInit={},retry=true):Promise<T>{
 if(!path.startsWith('/')||path.startsWith('//')||path.includes('\\'))throw new Error('API requests must stay on this workspace origin');
 if(nativeWorkspace() && !path.startsWith('/auth/oidc/')){
  try { const response=await nativeRequest<T>(path,init);if(response.status===423)locked();if(response.status<200||response.status>=300){const value=response.body as {detail?:unknown};throw new ApiError(response.status,typeof value?.detail==='string'?value.detail:JSON.stringify(value));}return response.body; }
  catch(error){if(error instanceof ApiError)throw error;const message=error instanceof Error?error.message:String(error);if(message==='Session locked; unlock required'){locked();throw new ApiError(423,message);}if(['Sign in required','Session expired or revoked; sign in again','SSO session expired or revoked; sign in again'].includes(message)){signedOut();throw new ApiError(401,message);}throw new Error(message);}
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
type GraphQLFailure={message:string;extensions?:{code?:string;http_status?:number}};
type GraphQLResponse<T>={data?:T|null;errors?:GraphQLFailure[]};
function graphQLStatus(error:GraphQLFailure){return error.extensions?.http_status||({UNAUTHENTICATED:401,FORBIDDEN:403,LOCKED:423,NOT_FOUND:404,CONFLICT:409} as Record<string,number>)[error.extensions?.code||''];}
export async function gql<T>(query:string,variables:Record<string,unknown>={}):Promise<T>{
 const execute=()=>request<GraphQLResponse<T>>('/graphql',{method:'POST',body:JSON.stringify({query,variables})});
 let body=await execute();
 // GraphQL can report a JWT expiring during nested field resolution with
 // HTTP 200. Retry only a read operation; a partially executed mutation
 // must never be replayed merely because one field lost authentication.
 const readOnly=/^(?:\s|#[^\r\n]*(?:\r?\n|$))*(?:query\b|\{)/.test(query);
 if(readOnly&&body.errors?.some(error=>graphQLStatus(error)===401)){
  await refreshSession();body=await execute();
 }
 if(body.errors?.length){
  const status=body.errors.map(graphQLStatus).find(status=>status===423)||body.errors.map(graphQLStatus).find(Boolean);
  if(status===423)locked();
  const message=body.errors.map(error=>error.message).join('\n');
  throw status?new ApiError(status,message):new Error(message);
 }
 if(body.data==null)throw new Error('The host returned no GraphQL data. Refresh and try again.');
 return body.data;
}
export function json(method:string,data?:unknown):RequestInit{return {method,body:data===undefined?undefined:JSON.stringify(data)}};
export async function signOut(){if(nativeWorkspace())await nativeSignOut();else await request('/auth/logout',json('POST',{}),false);signedOut();}
export interface Project{id:string;path:string;name:string}
export interface Task{coordination_report?:{source_session_id?:string;source_task_id?:string;kind:string;label:string;source_status?:string};id:string;status:string;prompt:string;result?:string;error?:string;engine?:string;limits?:Record<string,number>;events?:{type:string;payload:Record<string,unknown>;sequence:number}[];activity_truncated?:boolean;approvals?:{id:string;status:string;actual_status?:string;actionable?:boolean;kind:string;payload:Record<string,unknown>}[];children?:Task[]}
export interface Turn{id:string;prompt:string;task_id?:string;task?:Task;sequence:number}
export interface Session{reasoning_config?:Record<string,string>;custom_agent_id?:string|null;custom_agent_revision?:string|null;id:string;title:string;project_id?:string;cwd?:string;engine:string;provider_id?:string;model?:string;mode:string;pinned:boolean;archived:boolean;draft_text:string;draft_context?:import('./context').ContextItem[];revision:number;turns?:Turn[];linked_from?:string;updated_at:number;scroll:number;scroll_anchor?:import('./conversation-position').ConversationAnchor|null;extension_ids?:string[];workflow?:string|null;hasEarlier?:boolean;group_id?:string|null;runner_id?:string|null;runner_credential_ref?:string|null;run_limits?:Record<string,number>;latest_status?:string|null;worktree_id?:string|null;worktree_digest?:string|null;worktree_branch?:string|null}
export interface Engine{id:string;name?:string;available?:boolean;auth_state?:string|null;auth_detail?:string|null;error?:string|null;capabilities?:Record<string,unknown>}
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
