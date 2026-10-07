import {validateArtifactDrafts,type ArtifactDraft} from './artifact-drafts';
import type {Draft} from './drafts';
import {isTauri} from '@tauri-apps/api/core';
import {getCurrentWindow} from '@tauri-apps/api/window';
export type TransferBuffer={key:string;project_id:string;path:string;content:string;revision:string;editable:boolean;dirty:boolean;root?:string;workspaceSession?:string;worktree?:string|null};
export type WindowState={version:1;sessionId:string;draft:Draft|null;buffers:TransferBuffer[];artifacts?:ArtifactDraft[];terminalId?:string};
const current=new Map<string,TransferBuffer[]>();
const terminals=new Map<string,string>();
export function rememberTerminal(sessionId:string,id:string){if(id)terminals.set(sessionId,id)}
export function recalledTerminal(sessionId:string){return terminals.get(sessionId)||''}
let database:Promise<IDBDatabase>|undefined;
let frozen=false;
const writes=new Set<Promise<void>>();
export async function freezeWindowBuffers(){frozen=true;await Promise.all(writes)}
export function unfreezeWindowBuffers(){frozen=false}
export function windowSlot(){if(isTauri())return getCurrentWindow().label;if(!window.name.startsWith('termx-workspace-'))window.name='termx-workspace-'+crypto.randomUUID();return window.name}
function db(){return database??=new Promise<IDBDatabase>((resolve,reject)=>{const request=indexedDB.open('termx-workspace-buffer-state',2);request.onupgradeneeded=()=>{if(!request.result.objectStoreNames.contains('slots'))request.result.createObjectStore('slots')};request.onsuccess=()=>resolve(request.result);request.onerror=()=>reject(request.error)})}
export function snapshotBuffers(owner:string){return structuredClone(current.get(owner)||[])}
export function transferBuffers(buffers:TransferBuffer[]){return buffers.map(({key,project_id,path,content,revision,editable,dirty,root,workspaceSession,worktree})=>({key,project_id,path,content,revision,editable,dirty,root,workspaceSession,worktree}))}
export function publishBuffers(owner:string,buffers:TransferBuffer[]){current.set(owner,transferBuffers(buffers))}
export async function persistBuffers(owner:string,buffers:TransferBuffer[]){if(frozen)return;const write=(async()=>{const database=await db();await new Promise<void>((resolve,reject)=>{const tx=database.transaction('slots','readwrite');tx.objectStore('slots').put(transferBuffers(buffers.filter(buffer=>buffer.dirty)),JSON.stringify([owner,windowSlot()]));tx.oncomplete=()=>resolve();tx.onerror=()=>reject(tx.error);tx.onabort=()=>reject(tx.error)})})();writes.add(write);try{await write}finally{writes.delete(write)}}
export async function dropWindowBuffers(owner:string){const database=await db();await new Promise<void>((resolve,reject)=>{const tx=database.transaction('slots','readwrite');tx.objectStore('slots').delete(JSON.stringify([owner,windowSlot()]));tx.oncomplete=()=>resolve();tx.onerror=()=>reject(tx.error)})}
export async function restoreBuffers(owner:string){const database=await db();return new Promise<TransferBuffer[]>((resolve,reject)=>{const request=database.transaction('slots','readonly').objectStore('slots').get(JSON.stringify([owner,windowSlot()]));request.onsuccess=()=>resolve(Array.isArray(request.result)?request.result:[]);request.onerror=()=>reject(request.error)})}
export function mergeBuffers(existing:TransferBuffer[],incoming:TransferBuffer[],handoff:string):TransferBuffer[]{
 const rows=[...existing];for(const buffer of incoming){const index=rows.findIndex(row=>row.key===buffer.key);if(index<0)rows.push(buffer);else if(rows[index].dirty&&rows[index].content!==buffer.content){const key=buffer.key+':handoff:'+handoff;if(!rows.some(row=>row.key===key))rows.push({...buffer,key})}else if(!rows[index].dirty)rows[index]=buffer}return rows;
}
export function validateWindowState(value:unknown):WindowState{
 if(!value||typeof value!=='object'||JSON.stringify(value).length>16*1024*1024)throw new Error('Invalid workspace handoff');
 const state=value as WindowState;if(state.version!==1||typeof state.sessionId!=='string'||state.sessionId.length>128||!Array.isArray(state.buffers)||state.buffers.length>100)throw new Error('Invalid workspace handoff');
 for(const buffer of state.buffers){if(!buffer||typeof buffer.key!=='string'||typeof buffer.project_id!=='string'||typeof buffer.path!=='string'||buffer.path.startsWith('/')||buffer.path.includes('\\')||buffer.path.split('/').includes('..')||typeof buffer.content!=='string'||buffer.content.length>2*1024*1024||typeof buffer.revision!=='string'||typeof buffer.dirty!=='boolean'||typeof buffer.editable!=='boolean')throw new Error('Invalid editor buffer handoff')}
 if(state.draft&&(typeof state.draft.text!=='string'||typeof state.draft.saved!=='string'||state.draft.text.length>64000||!Array.isArray(state.draft.context||[])||(state.draft.context||[]).length>16))throw new Error('Invalid conversation draft handoff');
 for(const item of state.draft?.attachments||[]){if(typeof item.name!=='string'||item.name.length>255||!['image/png','image/jpeg','image/webp'].includes(item.mime)||typeof item.data!=='string'||item.data.length>2800000)throw new Error('Invalid image attachment handoff')}
 if((state.draft?.attachments||[]).length>4)throw new Error('Invalid image attachment handoff');
 if(state.artifacts!==undefined)state.artifacts=validateArtifactDrafts(state.artifacts);return structuredClone(state);
}
export async function receiveBuffers(owner:string,incoming:TransferBuffer[],handoff:string){const existing=mergeBuffers(await restoreBuffers(owner),snapshotBuffers(owner),'restored');const merged=mergeBuffers(existing,incoming,handoff);await persistBuffers(owner,merged);publishBuffers(owner,merged);window.dispatchEvent(new CustomEvent('termx-buffer-handoff',{detail:{owner,buffers:merged}}))}
if(typeof window!=='undefined')window.addEventListener('termx-signed-out',()=>{current.clear();terminals.clear()});
