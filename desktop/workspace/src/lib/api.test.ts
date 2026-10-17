import {describe,it,expect,vi,beforeEach,afterEach} from 'vitest';
import {gql,request,unlockWorkspace} from './api';
vi.mock('./native',()=>({nativeWorkspace:()=>false}));
beforeEach(()=>{document.cookie='termx_csrf=csrf-fixture';localStorage.clear()});
afterEach(()=>vi.unstubAllGlobals());
describe('managed cookie API boundary',()=>{
 it('coordinates parallel refresh once and includes CSRF without exposing a bearer',async()=>{let calls=0,refreshes=0;const fetch=vi.fn(async(path:string)=>{if(path==='/auth/me')return new Response('{}',{status:401});if(path==='/auth/refresh'){refreshes++;return new Response('{}',{status:200})}calls++;return new Response(calls<=2?'{}':'{"ok":true}',{status:calls<=2?401:200})});vi.stubGlobal('fetch',fetch);Object.defineProperty(navigator,'locks',{configurable:true,value:{request:async(_name:string,run:()=>unknown)=>run()}});const result=await Promise.all([request('/api/fixture',{method:'POST',body:'{}'}),request('/api/fixture',{method:'POST',body:'{}'})]);expect(result).toEqual([{ok:true},{ok:true}]);expect(refreshes).toBe(1);for(const [,init] of fetch.mock.calls.filter(([path])=>path==='/api/fixture') as unknown as [string,RequestInit][]){expect(init.credentials).toBe('same-origin');expect(new Headers(init.headers).get('X-Termx-CSRF')).toBe('csrf-fixture');expect(new Headers(init.headers).has('Authorization')).toBe(false)}expect(localStorage.length).toBe(0)});
 it('retains the family on locked refresh and signals lock without signout',async()=>{const locked=vi.fn(),signedOut=vi.fn();window.addEventListener('termx-session-locked',locked);window.addEventListener('termx-signed-out',signedOut);try{vi.stubGlobal('fetch',vi.fn(async(path:string)=>new Response('{"detail":"Session locked; unlock required"}',{status:path==='/auth/refresh'?423:401})));Object.defineProperty(navigator,'locks',{configurable:true,value:{request:async(_name:string,run:()=>unknown)=>run()}});await expect(request('/api/fixture')).rejects.toMatchObject({status:423});expect(locked).toHaveBeenCalledOnce();expect(signedOut).not.toHaveBeenCalled();expect(localStorage.length).toBe(0)}finally{window.removeEventListener('termx-session-locked',locked);window.removeEventListener('termx-signed-out',signedOut)}});
 it('signals direct423 and reauthenticates through explicit unlock',async()=>{const event=vi.fn();window.addEventListener('termx-session-locked',event);const fetch=vi.fn(async(path:string)=>new Response(path==='/auth/me'?'{"session_id":"same"}':'{}',{status:path==='/api/locked'?423:200}));vi.stubGlobal('fetch',fetch);try{await expect(request('/api/locked',{},false)).rejects.toMatchObject({status:423});expect(event).toHaveBeenCalledOnce();expect(await unlockWorkspace({method:'local-password',username:'owner',password:'fixture'})).toEqual({session_id:'same'});const [,init]=fetch.mock.calls.find(([path])=>path==='/auth/unlock')! as unknown as [string,RequestInit];expect(JSON.parse(String(init.body))).toEqual({method:'local-password',username:'owner',password:'fixture'});expect(new Headers(init.headers).get('X-Termx-CSRF')).toBe('csrf-fixture')}finally{window.removeEventListener('termx-session-locked',event)}});
 it('rejects another origin before reading session credentials',async()=>{const fetch=vi.fn();vi.stubGlobal('fetch',fetch);await expect(request('//other.example/private')).rejects.toThrow('workspace origin');expect(fetch).not.toHaveBeenCalled()});
 it('preserves explicit backend authorization failures',async()=>{vi.stubGlobal('fetch',vi.fn(async()=>new Response('{"error":"This origin is not granted"}',{status:403})));await expect(request('/api/fixture')).rejects.toThrow('This origin is not granted')});
});

it('bounds a stalled mutation without repeating it and explains the uncertain result',async()=>{
 vi.useFakeTimers();
 try{
  const fetch=vi.fn((_path:string,init:RequestInit)=>new Promise<Response>((_,reject)=>init.signal!.addEventListener('abort',()=>reject(new DOMException('Aborted','AbortError')))));
  vi.stubGlobal('fetch',fetch);
  const outcome=request('/api/actions',{method:'POST',body:'{}'}).catch(error=>error);
  await vi.advanceTimersByTimeAsync(30001);
  expect(await outcome).toMatchObject({status:504,message:expect.stringContaining('may have completed')});
  expect(fetch).toHaveBeenCalledTimes(1);
 }finally{vi.useRealTimers()}
});
it('rejects a backslash-normalized foreign origin',async()=>{
 const fetch=vi.fn();vi.stubGlobal('fetch',fetch);
 await expect(request('/\\other.example/private')).rejects.toThrow('workspace origin');
 expect(fetch).not.toHaveBeenCalled();
});

describe('refresh failure recovery',()=>{
 it.each([403,429,500,503])('keeps the workspace signed in when refresh returns HTTP %s',async(status)=>{
  const signedOut=vi.fn();window.addEventListener('termx-signed-out',signedOut);
  vi.stubGlobal('fetch',vi.fn(async(path:string)=>new Response('{}',{status:path==='/auth/refresh'?status:401})));
  try{await expect(request('/api/fixture')).rejects.toMatchObject({status});expect(signedOut).not.toHaveBeenCalled();}
  finally{window.removeEventListener('termx-signed-out',signedOut);}
 });
 it('does not rotate a refresh credential after a transient current-session failure',async()=>{
  const fetch=vi.fn(async(path:string)=>new Response('{}',{status:path==='/auth/me'?503:401}));vi.stubGlobal('fetch',fetch);
  await expect(request('/api/fixture')).rejects.toMatchObject({status:503});
  expect(fetch.mock.calls.map(([path])=>path)).toEqual(['/api/fixture','/auth/me']);
 });
 it('locks without rotating when another window already locked the current session',async()=>{
  const locked=vi.fn(),signedOut=vi.fn();window.addEventListener('termx-session-locked',locked);window.addEventListener('termx-signed-out',signedOut);
  const fetch=vi.fn(async(path:string)=>new Response('{}',{status:path==='/auth/me'?423:401}));vi.stubGlobal('fetch',fetch);
  try{await expect(request('/api/fixture')).rejects.toMatchObject({status:423});expect(locked).toHaveBeenCalledOnce();expect(signedOut).not.toHaveBeenCalled();expect(fetch).toHaveBeenCalledTimes(2);}
  finally{window.removeEventListener('termx-session-locked',locked);window.removeEventListener('termx-signed-out',signedOut);}
 });
 it('signs out only when the host rejects the retained refresh credential',async()=>{
  const signedOut=vi.fn();window.addEventListener('termx-signed-out',signedOut);vi.stubGlobal('fetch',vi.fn(async()=>new Response('{}',{status:401})));
  try{await expect(request('/api/fixture')).rejects.toMatchObject({status:401});expect(signedOut).toHaveBeenCalledOnce();}
  finally{window.removeEventListener('termx-signed-out',signedOut);}
 });
 it('bounds a stalled refresh without clearing the workspace or replaying the request',async()=>{
  vi.useFakeTimers();const signedOut=vi.fn();window.addEventListener('termx-signed-out',signedOut);
  try{
   const fetch=vi.fn((path:string,init:RequestInit)=>path==='/auth/refresh'?new Promise<Response>((_,reject)=>init.signal!.addEventListener('abort',()=>reject(new DOMException('Aborted','AbortError')))):Promise.resolve(new Response('{}',{status:401})));vi.stubGlobal('fetch',fetch);
   const outcome=request('/api/fixture').catch(error=>error);await vi.advanceTimersByTimeAsync(30001);
   expect(await outcome).toMatchObject({status:504});expect(signedOut).not.toHaveBeenCalled();expect(fetch.mock.calls.filter(([path])=>path==='/api/fixture')).toHaveLength(1);
  }finally{window.removeEventListener('termx-signed-out',signedOut);vi.useRealTimers();}
 });
});

describe('GraphQL session recovery',()=>{
 const denied={errors:[{message:'Managed sign-in required',extensions:{code:'UNAUTHENTICATED',http_status:401}}]};
 it('recovers a read whose access credential expires during nested turn resolution',async()=>{
  let attempts=0;const fetch=vi.fn(async(path:string)=>{
   if(path==='/auth/me')return new Response('{}',{status:401});
   if(path==='/auth/refresh')return new Response('{}');
   return new Response(JSON.stringify(attempts++?{data:{nodes:[{task:{id:'task'}}]}}:denied));
  });vi.stubGlobal('fetch',fetch);
  expect(await gql('# Tail poll\n query($ids:[ID!]!){nodes(ids:$ids){... on WorkspaceTurnNode{task}}}',{ids:['turn']})).toEqual({nodes:[{task:{id:'task'}}]});
  expect(fetch.mock.calls.map(([path])=>path)).toEqual(['/graphql','/auth/me','/auth/refresh','/graphql']);
 });
 it('never replays a mutation after a field reports lost authentication',async()=>{
  const fetch=vi.fn(async()=>new Response(JSON.stringify(denied)));vi.stubGlobal('fetch',fetch);
  await expect(gql('mutation { create_session { id } }')).rejects.toMatchObject({status:401});expect(fetch).toHaveBeenCalledOnce();
 });
 it('preserves permission errors without refreshing the account',async()=>{
  const fetch=vi.fn(async()=>new Response(JSON.stringify({errors:[{message:'Project access denied',extensions:{code:'FORBIDDEN',http_status:403}}]})));vi.stubGlobal('fetch',fetch);
  await expect(gql('{ workspace_turns(session_id:"private"){edges{node{id}}} }')).rejects.toMatchObject({status:403,message:'Project access denied'});expect(fetch).toHaveBeenCalledOnce();
 });
 it('bounds recovery to one replay when a read still lacks authentication',async()=>{
  const fetch=vi.fn(async(path:string)=>new Response(JSON.stringify(path==='/graphql'?denied:{})));vi.stubGlobal('fetch',fetch);
  await expect(gql('{ workspace_sessions { edges { node { id } } } }')).rejects.toMatchObject({status:401});
  expect(fetch.mock.calls.map(([path])=>path)).toEqual(['/graphql','/auth/me','/graphql']);
 });
});
