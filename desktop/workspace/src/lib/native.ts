import { invoke, isTauri } from '@tauri-apps/api/core';

export const nativeWorkspace = () => isTauri();
export async function nativeLogin(username: string, password: string, setup = false) {
  return invoke<{ principal: { id: string; display_name: string; scopes: string[] }; session_id: string }>('workspace_login', { username, password, setup });
}
export async function nativeRequest<T>(path: string, init: RequestInit = {}): Promise<{ status: number; body: T }> {
  if (path === '/auth/me') await invoke('workspace_resume_sso');
  if (init.body && typeof init.body !== 'string') throw new Error('Use authenticated binary transfer for this request');
  return invoke('workspace_request', { path, method: (init.method || 'GET').toUpperCase(), data: init.body ? JSON.parse(init.body as string) : null });
}
export async function nativeSignOut() { await invoke('workspace_logout') }
export async function detachWorkspace(sessionId: string | null, panel: 'chat' | 'workbench' | 'browser' | 'computer') {
  if (nativeWorkspace()) return invoke<string>('workspace_detach', { sessionId, panel });
  const url = new URL(window.location.href); url.search = ''; url.searchParams.set('layout', panel);
  if (sessionId) url.searchParams.set('session', sessionId);
  const child = window.open(url.toString(), '_blank', 'popup,width=1100,height=780');
  if (!child) throw new Error('Popup blocked. Allow popups for this workspace or use the in-app split.');
  return 'browser-window';
}

export async function nativeBinary(path:string,init:RequestInit={}):Promise<{status:number;body:Uint8Array;mime:string}>{
 const method=(init.method||'GET').toUpperCase();
 const encoded=new Request(new URL(path,location.origin),{...init,method});
 const bytes=init.body?new Uint8Array(await encoded.arrayBuffer()):null;
 if(bytes&&bytes.byteLength>64*1024*1024)throw new Error('Transfer exceeds 64 MiB');
 let source='';if(bytes)for(let offset=0;offset<bytes.length;offset+=8192)source+=String.fromCharCode(...bytes.subarray(offset,offset+8192));
 const result=await invoke<{status:number;body_base64:string;content_type:string}>('workspace_binary',{path,method,bodyBase64:bytes?btoa(source):null,contentType:encoded.headers.get('Content-Type')||'application/octet-stream'});
 const raw=atob(result.body_base64);const body=new Uint8Array(raw.length);for(let index=0;index<raw.length;index++)body[index]=raw.charCodeAt(index);
 return {status:result.status,body,mime:result.content_type};
}
