import {beforeEach,describe,expect,it,vi} from 'vitest';
const {invoke}=vi.hoisted(()=>({invoke:vi.fn()}));
vi.mock('@tauri-apps/api/core',()=>({invoke,isTauri:()=>true}));
vi.mock('@tauri-apps/api/window',()=>({getCurrentWindow:()=>({label:'main'})}));
vi.mock('@tauri-apps/api/event',()=>({listen:vi.fn()}));
import {nativeBinary} from './native';
beforeEach(()=>invoke.mockReset());
describe('native binary session lock propagation',()=>{
 it('normalizes locked secure-bridge rejection and immediately hides observation',async()=>{invoke.mockRejectedValueOnce('Session locked; unlock required');const locked=vi.fn();window.addEventListener('termx-session-locked',locked);try{await expect(nativeBinary('/api/media/download')).rejects.toMatchObject({status:423});expect(locked).toHaveBeenCalledOnce();expect(invoke.mock.calls[0][1]).toMatchObject({path:'/api/media/download',method:'GET',bodyBase64:null})}finally{window.removeEventListener('termx-session-locked',locked)}});
 it('signals a returned423 without fabricating binary success',async()=>{invoke.mockResolvedValueOnce({status:423,body_base64:btoa('{"detail":"Session locked"}'),content_type:'application/json'});const locked=vi.fn();window.addEventListener('termx-session-locked',locked);try{const response=await nativeBinary('/api/media/download');expect(response.status).toBe(423);expect(locked).toHaveBeenCalledOnce();expect(new TextDecoder().decode(response.body)).toContain('Session locked')}finally{window.removeEventListener('termx-session-locked',locked)}});
 it('preserves independent transfer errors without changing session state',async()=>{invoke.mockRejectedValueOnce('Transfer exceeds 64 MiB');const locked=vi.fn();window.addEventListener('termx-session-locked',locked);try{await expect(nativeBinary('/api/media/upload')).rejects.toBe('Transfer exceeds 64 MiB');expect(locked).not.toHaveBeenCalled()}finally{window.removeEventListener('termx-session-locked',locked)}});
});
