import {describe,it,expect,vi,beforeEach,afterEach} from 'vitest';
import {request} from './api';
vi.mock('./native',()=>({nativeWorkspace:()=>false}));
beforeEach(()=>{document.cookie='termx_csrf=csrf-fixture';localStorage.clear()});
afterEach(()=>vi.unstubAllGlobals());
describe('managed cookie API boundary',()=>{
 it('coordinates parallel refresh once and includes CSRF without exposing a bearer',async()=>{let calls=0,refreshes=0;const fetch=vi.fn(async(path:string)=>{if(path==='/auth/me')return new Response('{}',{status:401});if(path==='/auth/refresh'){refreshes++;return new Response('{}',{status:200})}calls++;return new Response(calls<=2?'{}':'{"ok":true}',{status:calls<=2?401:200})});vi.stubGlobal('fetch',fetch);Object.defineProperty(navigator,'locks',{configurable:true,value:{request:async(_name:string,run:()=>unknown)=>run()}});const result=await Promise.all([request('/api/fixture',{method:'POST',body:'{}'}),request('/api/fixture',{method:'POST',body:'{}'})]);expect(result).toEqual([{ok:true},{ok:true}]);expect(refreshes).toBe(1);for(const [,init] of fetch.mock.calls.filter(([path])=>path==='/api/fixture') as unknown as [string,RequestInit][]){expect(init.credentials).toBe('same-origin');expect(new Headers(init.headers).get('X-Termx-CSRF')).toBe('csrf-fixture');expect(new Headers(init.headers).has('Authorization')).toBe(false)}expect(localStorage.length).toBe(0)});
 it('rejects another origin before reading session credentials',async()=>{const fetch=vi.fn();vi.stubGlobal('fetch',fetch);await expect(request('//other.example/private')).rejects.toThrow('workspace origin');expect(fetch).not.toHaveBeenCalled()});
 it('preserves explicit backend authorization failures',async()=>{vi.stubGlobal('fetch',vi.fn(async()=>new Response('{"error":"This origin is not granted"}',{status:403})));await expect(request('/api/fixture')).rejects.toThrow('This origin is not granted')});
});
