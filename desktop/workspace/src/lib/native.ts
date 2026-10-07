import {persistArtifactDrafts,snapshotArtifactDrafts} from './artifact-drafts';
import { invoke, isTauri } from '@tauri-apps/api/core';
import { getCurrentWindow } from '@tauri-apps/api/window';
import { listen, type UnlistenFn } from '@tauri-apps/api/event';

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
export async function detachWorkspace(sessionId: string | null, panel: 'chat' | 'workbench' | 'browser' | 'computer' | 'artifacts') {
  if (nativeWorkspace()) {const owner=await nativeRequest<{principal:{id:string}}>('/auth/me');const label=await invoke<string>('workspace_detach', { sessionId, panel });if(panel==='artifacts')await persistArtifactDrafts(owner.body.principal.id,snapshotArtifactDrafts(owner.body.principal.id),label);return label}
  const url = new URL(window.location.href); url.search = ''; url.searchParams.set('layout', panel);
  if (sessionId) url.searchParams.set('session', sessionId);
  const child = window.open(url.toString(), '_blank', 'popup,width=1100,height=780');
  if (!child) throw new Error('Popup blocked. Allow popups for this workspace or use the in-app split.');
  return 'browser-window';
}

export type NativePanel = 'chat' | 'workbench' | 'browser' | 'computer' | 'artifacts';
export type NativeRedockEvent = { id: string; source: string; ownerId: string; sessionId: string | null; panel: NativePanel; payload: unknown };
export const isDetachedWindow = () => nativeWorkspace() && getCurrentWindow().label.startsWith('workspace-');
export async function listenNativeRedock(handler: (event: NativeRedockEvent) => void): Promise<UnlistenFn> {
  if (!nativeWorkspace()) return () => {};
  return getCurrentWindow().listen<NativeRedockEvent>('termx-native-redock', event => handler(event.payload));
}
export const acceptRedock = (id: string) => invoke<void>('workspace_redock_accept', { id });
export const commitRedock = (id: string) => invoke<void>('workspace_redock_commit', { id });
export const cancelRedock = (id: string) => invoke<void>('workspace_redock_cancel', { id });
export const rejectRedock = cancelRedock;
export async function requestRedock(sessionId: string, panel: NativePanel, payload: unknown): Promise<string> {
  if (!isDetachedWindow()) throw new Error('Only a detached native workspace can return to the main window');
  const accepted = new Set<string>(), cancelled = new Set<string>();
  let pendingId: string | undefined, finish: (() => void) | undefined;
  const releases: UnlistenFn[] = [];
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    // Subscribe before IPC: the main window can safely persist and acknowledge
    // before the request's native return crosses back into this renderer.
    releases.push(await getCurrentWindow().listen<{ id: string }>('termx-native-redock-accepted', event => {
      accepted.add(event.payload.id); if (pendingId === event.payload.id) finish?.();
    }));
    releases.push(await getCurrentWindow().listen<{ id: string }>('termx-native-redock-cancelled', event => {
      cancelled.add(event.payload.id); if (pendingId === event.payload.id) finish?.();
    }));
    pendingId = await invoke<string>('workspace_redock', { sessionId, panel, payload });
    if (!accepted.has(pendingId) && !cancelled.has(pendingId)) {
      await new Promise<void>((resolve, reject) => {
        finish = resolve;
        timer = setTimeout(() => reject(new Error('Main workspace did not acknowledge the handoff; this window remains open')), 45_000);
      });
    }
    if (cancelled.has(pendingId)) throw new Error('Main workspace could not safely accept the handoff; this window remains open');
    return pendingId;
  } catch (error) {
    if (pendingId) await cancelRedock(pendingId).catch(() => {});
    throw error;
  } finally {
    if (timer) clearTimeout(timer);
    releases.forEach(release => release());
  }
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

export const nativeLockState = () => invoke<import('./api').LockState>('workspace_lock_state');
export const nativeUnlock = (evidence:import('./api').UnlockEvidence) => invoke('workspace_unlock',{method:evidence.method,username:evidence.username||'',password:evidence.password||'',assertion:evidence.assertion||''});
export const nativeUnlockOidc = (method:string) => invoke<{authorization_url:string}>('workspace_unlock_oidc',{method});
export async function listenNativeLocks(){
 if(!nativeWorkspace())return()=>{};
 const releases=await Promise.all([listen('termx-session-locked',()=>window.dispatchEvent(new Event('termx-session-locked'))),listen('termx-session-unlocked',()=>window.dispatchEvent(new Event('termx-session-unlocked')))]);
 return()=>releases.forEach(release=>release());
}
