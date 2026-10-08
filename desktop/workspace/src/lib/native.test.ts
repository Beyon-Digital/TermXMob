import { beforeEach, expect, it, vi } from 'vitest';
import { nativeRequest, detachWorkspace } from './native';
import { invoke, isTauri } from '@tauri-apps/api/core';
vi.mock('@tauri-apps/api/core',()=>({invoke:vi.fn(),isTauri:vi.fn()}));
beforeEach(()=>{vi.mocked(invoke).mockReset();vi.mocked(isTauri).mockReturnValue(true)});
it('adopts native SSO only through Rust before reading the current identity',async()=>{
 vi.mocked(invoke).mockResolvedValue({status:200,body:{principal:{id:'owner'}}});
 await nativeRequest('/auth/me');
 expect(vi.mocked(invoke).mock.calls.map(([command])=>command)).toEqual(['workspace_resume_sso','workspace_request']);
 expect(invoke).toHaveBeenLastCalledWith('workspace_request',{path:'/auth/me',method:'GET',data:null});
});
it('detaches with a session identifier and never adds a credential URL parameter',async()=>{
 vi.mocked(isTauri).mockReturnValue(false);const open=vi.spyOn(window,'open').mockReturnValue({} as Window);
 await detachWorkspace('session-id','workbench');
 const url=new URL(open.mock.calls[0][0] as string);expect(url.searchParams.get('session')).toBe('session-id');expect(url.searchParams.get('layout')).toBe('workbench');expect(url.searchParams.has('k')).toBe(false);expect(url.searchParams.has('access_token')).toBe(false);open.mockRestore();
});
it('surfaces browser popup denial without pretending detach succeeded',async()=>{
 vi.mocked(isTauri).mockReturnValue(false);const open=vi.spyOn(window,'open').mockReturnValue(null);
 await expect(detachWorkspace(null,'browser')).rejects.toThrow('Popup blocked');open.mockRestore();
});
