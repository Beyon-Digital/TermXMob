import {beforeEach,afterEach,expect,it,vi} from 'vitest';
import {transfer} from './transfers';
vi.mock('./native',()=>({nativeWorkspace:()=>false}));
beforeEach(()=>{document.cookie='termx_csrf=original';Object.defineProperty(navigator,'locks',{configurable:true,value:{request:async(_name:string,run:()=>unknown)=>run()}})});
afterEach(()=>vi.unstubAllGlobals());
it('refreshes an expired upload once, rebuilding CSRF and preserving its binary body',async()=>{
 const body=new Blob(['exact upload']);let uploads=0;const fetch=vi.fn(async(path:string)=>{
  if(path==='/auth/me')return new Response('{}',{status:401});
  if(path==='/auth/refresh'){document.cookie='termx_csrf=rotated';return new Response('{}')}
  uploads++;return new Response(uploads===1?'{}':'done',{status:uploads===1?401:200});
 });vi.stubGlobal('fetch',fetch);
 await transfer('/api/media/input',{method:'POST',body});
 const calls=fetch.mock.calls.filter(([path])=>path==='/api/media/input') as unknown as [string,RequestInit][];
 expect(calls).toHaveLength(2);expect(calls[1][1].body).toBe(body);
 expect(new Headers(calls[1][1].headers).get('X-Termx-CSRF')).toBe('rotated');
 expect(new Headers(calls[1][1].headers).has('Authorization')).toBe(false);
});
it('never repeats a denied or interrupted binary effect',async()=>{
 const fetch=vi.fn(async()=>new Response('{"detail":"Revoked"}',{status:403}));vi.stubGlobal('fetch',fetch);
 await expect(transfer('/api/media/input',{method:'POST',body:new Blob(['data'])})).rejects.toThrow('Revoked');
 expect(fetch).toHaveBeenCalledTimes(1);
});
