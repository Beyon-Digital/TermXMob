import {createContext,useCallback,useContext,useLayoutEffect,useMemo,useRef,useState,type ReactNode} from 'react';

export type StudioDraftState={dirty:boolean;busy:boolean;reason?:string};
export type StudioProjectActivity=StudioDraftState&{ownerId?:string;projectId:string|null};
const empty:StudioDraftState={dirty:false,busy:false};
const same=(a:StudioDraftState,b:StudioDraftState)=>a.dirty===b.dirty&&a.busy===b.busy&&a.reason===b.reason;
const Context=createContext<{report:(source:string,value:StudioDraftState)=>void;readOnly:boolean}|null>(null);

/** Track only draft metadata. Draft content and credentials never leave its editor. */
export function StudioDraftBoundary({ownerId,projectId,readOnly=false,onActivity,children}:{ownerId?:string;projectId:string|null;readOnly?:boolean;onActivity?:(value:StudioProjectActivity)=>void;children:ReactNode}){
 const scope=JSON.stringify([ownerId,projectId]),scopeNow=useRef(scope);scopeNow.current=scope;
 const [rows,setRows]=useState<Record<string,{scope:string;state:StudioDraftState}>>({});
 const report=useCallback((source:string,state:StudioDraftState)=>{if(scopeNow.current!==scope)return;setRows(previous=>previous[source]?.scope===scope&&same(previous[source].state,state)?previous:{...previous,[source]:{scope,state}})},[scope]);
 const current=Object.values(rows).filter(row=>row.scope===scope).map(row=>row.state),dirty=current.some(row=>row.dirty),busy=current.some(row=>row.busy),reason=current.find(row=>row.busy)?.reason;
 useLayoutEffect(()=>{onActivity?.({ownerId,projectId,dirty,busy,reason})},[ownerId,projectId,dirty,busy,reason,onActivity]);
 const value=useMemo(()=>({report,readOnly}),[report,readOnly]);
 return <Context.Provider value={value}>{children}</Context.Provider>;
}
export function useStudioDraftState(source:string,dirty:boolean,busy:boolean,reason?:string){
 const report=useContext(Context)?.report;
 useLayoutEffect(()=>{report?.(source,{dirty,busy,reason})},[report,source,dirty,busy,reason]);
 useLayoutEffect(()=>()=>report?.(source,empty),[report,source]);
}
export const useStudioProjectReadOnly=()=>useContext(Context)?.readOnly||false;

export function useStudioProjectGuard(ownerId:string|undefined,projectId:string|null,locked:boolean){
 const identity=JSON.stringify([ownerId,projectId]),identityNow=useRef(identity);identityNow.current=identity;
 const lockedNow=useRef(locked);lockedNow.current=locked;
 const [activity,setActivity]=useState<StudioProjectActivity>({ownerId,projectId,...empty}),activityNow=useRef(activity);activityNow.current=activity;
 const [pending,setPending]=useState<{target:string;scope:string;resolve:(value:boolean)=>void}|null>(null),pendingNow=useRef(pending);pendingNow.current=pending;
 const receive=useCallback((value:StudioProjectActivity)=>{if(JSON.stringify([value.ownerId,value.projectId])!==identityNow.current)return;activityNow.current=value;setActivity(old=>old.ownerId===value.ownerId&&old.projectId===value.projectId&&same(old,value)?old:value)},[]);
 const decide=useCallback((allow:boolean)=>{const ticket=pendingNow.current;if(!ticket)return;pendingNow.current=null;setPending(null);ticket.resolve(allow&&ticket.scope===identityNow.current&&!lockedNow.current&&!activityNow.current.busy)},[]);
 useLayoutEffect(()=>{decide(false);const value={ownerId,projectId,...empty};activityNow.current=value;setActivity(value)},[identity,locked,decide]);
 useLayoutEffect(()=>()=>{pendingNow.current?.resolve(false);pendingNow.current=null},[]);
 const permit=useCallback((target:string)=>{if(lockedNow.current)return Promise.resolve(false);const value=activityNow.current;if(target===(value.projectId||''))return Promise.resolve(true);if(value.busy)return Promise.reject(new Error(value.reason||'Finish the current Studio request before changing projects.'));if(!value.dirty)return Promise.resolve(true);if(pendingNow.current)return Promise.resolve(false);return new Promise<boolean>(resolve=>{const ticket={target,scope:identityNow.current,resolve};pendingNow.current=ticket;setPending(ticket)})},[]);
 return {activity,receive,permit,pending,decide};
}
