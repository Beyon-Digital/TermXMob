import {afterEach,expect,it,vi} from 'vitest';
import {transfer} from './transfers';
import {nativeWorkspace,nativeBinary} from './native';
import {ApiError} from './api';
vi.mock('./native',()=>({nativeWorkspace:vi.fn(()=>false),nativeBinary:vi.fn()}));
vi.mock('./api',()=>({csrf:()=>'',refreshSession:vi.fn(),ApiError:class extends Error{constructor(public status:number,message:string){super(message)}}}));
afterEach(()=>{vi.restoreAllMocks();vi.unstubAllGlobals();vi.mocked(nativeWorkspace).mockReturnValue(false)});
it('signals locked observation for browser binary transfers without refreshing or consuming output',async()=>{const locked=vi.fn();window.addEventListener('termx-session-locked',locked,{once:true});vi.stubGlobal('fetch',vi.fn(async()=>new Response('',{status:423})));await expect(transfer('/api/media/inputs',{method:'POST',body:new Blob(['audio'])})).rejects.toMatchObject({status:423});expect(locked).toHaveBeenCalledOnce();expect(fetch).toHaveBeenCalledTimes(1)});
it('signals locked native binary responses as the same423 rather than signing out the enrolled device',async()=>{vi.mocked(nativeWorkspace).mockReturnValue(true);vi.mocked(nativeBinary).mockResolvedValue({status:423,body:new Uint8Array(),mime:'application/json'});const locked=vi.fn();window.addEventListener('termx-session-locked',locked,{once:true});await expect(transfer('/api/media/artifacts/a/export?format=original')).rejects.toBeInstanceOf(ApiError);expect(locked).toHaveBeenCalledOnce();expect(nativeBinary).toHaveBeenCalledOnce()});
