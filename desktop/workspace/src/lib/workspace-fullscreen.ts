import {invoke} from '@tauri-apps/api/core';
import {nativeWorkspace} from './native';
export async function workspaceFullscreen(toggle=false):Promise<boolean>{
 if(nativeWorkspace())return invoke<boolean>('workspace_fullscreen',{toggle});
 if(toggle){if(document.fullscreenElement)await document.exitFullscreen();else{if(!document.documentElement.requestFullscreen)throw Error('Full screen is unavailable here. Use Open in full view to expand this tab.');await document.documentElement.requestFullscreen()}}
 return !!document.fullscreenElement;
}
