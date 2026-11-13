import {useEffect, useRef} from 'react';
import {request} from './api';
import {transfer} from './transfers';
import type {OutputColumn, SpecWarning} from './query-spec';

export type DataRole = 'owner' | 'editor' | 'viewer';
export type DataRecord = {id:string; revision:number; created_at?:string|number; updated_at?:string|number; workspace_id?:string};
export type DataWorkspace = DataRecord & {name:string; role:DataRole};
export type DataMember = DataRecord & {principal_id:string; username?:string; display_name?:string; role:DataRole};
export type DataEngineField = {name:string; label:string; type:'text'|'number'|'password'|'boolean'|'file'; required?:boolean; default?:string|number|boolean};
export type DataEngine = {id:string; label:string; family?:string; language?:string; status:string; ready:boolean; reason?:string; fields?:DataEngineField[]; capabilities:Record<string,boolean>};
export type DataConnection = DataRecord & {name:string; engine:string; config:Record<string,string|number|boolean|null>; shared_read?:boolean; created_by?:string; credential_configured?:boolean; password_configured?:boolean; source_generation?:number; credential_generation?:number; read_policy?:{verified?:boolean}};
export type DataQuery = DataRecord & {name:string; connection_id:string; sql:string; parameters?:Record<string,unknown>};
export type DataResult = {columns:{name:string; type?:string}[]; rows:unknown[][]; row_count:number; truncated:boolean; notice?:string};
export type DataJob = DataRecord & {status:'queued'|'running'|'completed'|'failed'|'cancelled'|'unknown'; connection_id:string; result?:DataResult; error?:string;
  view_id?:string; view_revision?:number; mode?:'rows'|'count'; output?:{columns:OutputColumn[]; warnings:SpecWarning[]}; duration_ms?:number|null};
export type DataSnapshot = {workspace:DataWorkspace; members:DataMember[]; connections:DataConnection[]; queries:DataQuery[]; reports:unknown[]; dashboards:unknown[]; activity:unknown[]};
export type DataBootstrap = {workspaces:DataWorkspace[]; engines:DataEngine[]; capabilities:Record<string,unknown>};
export type DataView = 'database'|'bi'|'team';
export type DataQueryDraft = Pick<DataQuery,'connection_id'|'sql'> & Partial<Pick<DataQuery,'id'|'name'|'parameters'>> & {parameter_text?:string};

export function dataPath(workspaceId?:string, suffix=''){return '/api/data'+(workspaceId?'/workspaces/'+encodeURIComponent(workspaceId):'')+suffix;}
/** Reuse the managed session bridge, CSRF, lock handling and native transport. Mutations are never silently replayed. */
export function dataRequest<T>(path:string,init:RequestInit={}):Promise<T>{
 if(!path.startsWith('/api/data/')&&path!=='/api/data')throw new Error('Invalid Data Studio request');
 return request<T>(path,init,['GET','HEAD'].includes((init.method||'GET').toUpperCase()));
}
export function dataBody(method:string,body?:unknown):RequestInit{return {method,body:body===undefined?undefined:JSON.stringify(body)};}
export function dataError(reason:unknown){return reason instanceof Error?reason.message:String(reason);}
export function dataRequestId(){return crypto.randomUUID();}
export function dataJobActive(job:DataJob|null|undefined){return !!job&&['queued','running'].includes(job.status);}
export function dataCell(value:unknown){if(value===null||value===undefined)return 'NULL';return typeof value==='object'?JSON.stringify(value):String(value);}

/** Every asynchronous result is scoped to this mounted identity/workspace/view. */
export function useDataScope(){
 const state=useRef({alive:true,controllers:new Set<AbortController>()});
 useEffect(()=>{state.current.alive=true;return()=>{state.current.alive=false;state.current.controllers.forEach(controller=>controller.abort());state.current.controllers.clear();};},[]);
 return {current:()=>state.current.alive,controller:()=>{const controller=new AbortController();state.current.controllers.add(controller);return controller;},release:(controller:AbortController)=>state.current.controllers.delete(controller)};
}
export async function dataDownload(workspaceId:string,jobId:string,signal:AbortSignal,current:()=>boolean,options:{path?:string;filename?:string}={}){
 const blob=await transfer(options.path||dataPath(workspaceId,'/jobs/'+encodeURIComponent(jobId)+'/export.csv'),{signal});
 if(!current()||signal.aborted)return;
 const url=URL.createObjectURL(blob),anchor=document.createElement('a');anchor.href=url;anchor.download=options.filename||'query-results.csv';anchor.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
}
const selectionKey=(ownerId?:string)=>'termx.data.selection.'+(ownerId||'managed');
export function readDataSelection(ownerId?:string):{workspaceId:string;view:DataView;connectionId:string}{
 let saved:Record<string,unknown>={};try{saved=JSON.parse(localStorage.getItem(selectionKey(ownerId))||'{}')}catch{}
 const params=new URLSearchParams(window.location.search),view=params.get('data_view')||saved.view;
 return {workspaceId:params.get('data_workspace')||String(saved.workspaceId||''),view:view==='bi'||view==='team'?view:'database',connectionId:params.get('data_source')||String(saved.connectionId||'')};
}
/** Persist identifiers only. Source profiles, SQL, results and secrets never enter browser storage. */
export function rememberDataSelection(ownerId:string|undefined,selection:{workspaceId:string;view:DataView;connectionId:string}){
 try{localStorage.setItem(selectionKey(ownerId),JSON.stringify(selection))}catch{}
 const url=new URL(window.location.href);url.searchParams.set('workspace','data');
 for(const [key,value] of Object.entries({data_workspace:selection.workspaceId,data_view:selection.view,data_source:selection.connectionId})){if(value)url.searchParams.set(key,value);else url.searchParams.delete(key)}
 window.history.replaceState(window.history.state,'','/'+url.search+url.hash);
}
