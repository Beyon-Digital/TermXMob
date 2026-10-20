import {invoke} from '@tauri-apps/api/core';
import {listen} from '@tauri-apps/api/event';
import {nativeWorkspace} from './native';

export type CompanionCapabilities={desktop:boolean;backend:string;reason?:string};
export async function companionCapabilities():Promise<CompanionCapabilities>{
 if(!nativeWorkspace())return {desktop:false,backend:'in-app',reason:'Your characters live inside this workspace. Open the desktop app to let them explore your screen.'};
 return invoke<CompanionCapabilities>('companion_capabilities');
}
export async function companionHome(){if(nativeWorkspace())await invoke('companion_home');window.dispatchEvent(new Event('termx-companion-home'))}
export async function listenCompanionOpen(handler:(sessionId:string)=>void){
 if(!nativeWorkspace())return ()=>{};
 return listen<{session_id:string}>('termx-companion-open-session',event=>{if(event.payload.session_id)handler(event.payload.session_id)});
}
export async function listenCompanionCapabilities(handler:(value:CompanionCapabilities)=>void){
 if(!nativeWorkspace())return ()=>{};
 return listen<CompanionCapabilities>('termx-companion-capabilities',event=>{handler(event.payload);window.dispatchEvent(new CustomEvent('termx-companion-capabilities',{detail:event.payload}))});
}
