import {afterEach,expect,it,vi} from 'vitest';
import {invoke} from '@tauri-apps/api/core';
import {nativeWorkspace} from './native';
import {workspaceFullscreen} from './workspace-fullscreen';
vi.mock('@tauri-apps/api/core',()=>({invoke:vi.fn()}));vi.mock('./native',()=>({nativeWorkspace:vi.fn()}));
afterEach(()=>{vi.resetAllMocks();vi.restoreAllMocks()});
it('uses the trusted native workspace command for desktop full screen',async()=>{vi.mocked(nativeWorkspace).mockReturnValue(true);vi.mocked(invoke).mockResolvedValue(true);expect(await workspaceFullscreen(true)).toBe(true);expect(invoke).toHaveBeenCalledWith('workspace_fullscreen',{toggle:true});await workspaceFullscreen();expect(invoke).toHaveBeenLastCalledWith('workspace_fullscreen',{toggle:false})});
it('enters and exits the browser Fullscreen API from an explicit action',async()=>{vi.mocked(nativeWorkspace).mockReturnValue(false);let current:Element|null=null;Object.defineProperty(document,'fullscreenElement',{configurable:true,get:()=>current});const enter=vi.fn(async()=>{current=document.documentElement}),exit=vi.fn(async()=>{current=null});Object.defineProperty(document.documentElement,'requestFullscreen',{configurable:true,value:enter});Object.defineProperty(document,'exitFullscreen',{configurable:true,value:exit});expect(await workspaceFullscreen(true)).toBe(true);expect(await workspaceFullscreen(true)).toBe(false);expect(enter).toHaveBeenCalledOnce();expect(exit).toHaveBeenCalledOnce();expect(invoke).not.toHaveBeenCalled()});
