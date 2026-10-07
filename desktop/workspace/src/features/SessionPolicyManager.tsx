import {useEffect,useRef,useState} from 'react';
import {Button} from '../components/ui/button';
import {Input} from '../components/ui/input';
import {ApiError,json,request} from '../lib/api';
import {useAccessLifecycle} from './useAccessLifecycle';

type Policy={revision:number;idle_ttl_seconds:number;absolute_ttl_seconds:number;access_ttl_seconds:number};
type Result=Policy&{expired_sessions:number};
export default function SessionPolicyManager({onError}:{onError:(error:unknown)=>void}){
 const [policy,setPolicy]=useState<Policy|null>(null),[draft,setDraft]=useState<Policy|null>(null),[version,setVersion]=useState(0),[busy,setBusy]=useState(false),[conflict,setConflict]=useState(false),[status,setStatus]=useState('');
 const dirty=useRef(false),lifecycle=useAccessLifecycle(()=>{setBusy(false);setStatus('')});
 const policyNow=useRef(policy);policyNow.current=policy;
 useEffect(()=>{
  if(!lifecycle.current())return;
  const current=lifecycle.capture(),controller=new AbortController();
  request<Policy>('/auth/admin/session-policy',{signal:controller.signal}).then(value=>{
   if(!current()||controller.signal.aborted)return;
   const changed=policyNow.current?.revision!==value.revision;
   setPolicy(value);if(!dirty.current){setDraft(value);setConflict(false)}else if(changed)setConflict(true);
  }).catch(error=>{if(current()&&!controller.signal.aborted)onError(error)});
  return()=>controller.abort();
 },[onError,version,lifecycle.refresh]);
 async function save(){
  if(!policy||!draft||busy||conflict||!lifecycle.current())return;
  const current=lifecycle.capture();setBusy(true);setStatus('');
  try{
   const value=await request<Result>('/auth/admin/session-policy',json('PUT',{revision:policy.revision,idle_ttl_seconds:draft.idle_ttl_seconds,absolute_ttl_seconds:draft.absolute_ttl_seconds}));
   if(!current())return;dirty.current=false;setPolicy(value);setDraft(value);setStatus(`Session limits saved. ${value.expired_sessions} expired device sessions revoked.`);
  }catch(error){if(current()){if(error instanceof ApiError&&error.status===400&&error.message.includes('changed'))setVersion(value=>value+1);onError(error)}}finally{if(current())setBusy(false)}
 }
 const valid=draft&&Number.isInteger(draft.idle_ttl_seconds)&&Number.isInteger(draft.absolute_ttl_seconds)&&draft.idle_ttl_seconds>=600&&draft.idle_ttl_seconds<=2592000&&draft.absolute_ttl_seconds>=draft.idle_ttl_seconds&&draft.absolute_ttl_seconds<=31536000;
 return <section className="manager-form" aria-label="Device session lifetimes"><h2>Device session lifetimes</h2><p>Shorter limits apply to current devices and can expire this session, control leases and enrolled jobs. Longer limits apply only to new sign-ins and pairings. Existing devices keep their earlier limits; expired sessions stay expired.</p>
  <Button variant="outline" disabled={busy||lifecycle.locked} onClick={()=>setVersion(value=>value+1)}>Refresh session limits</Button>
  {conflict&&policy&&<div role="alert"><p>The current policy is revision {policy.revision}: idle {policy.idle_ttl_seconds} seconds; absolute {policy.absolute_ttl_seconds} seconds. Your draft is preserved.</p><Button disabled={busy||lifecycle.locked} onClick={()=>setConflict(false)}>Keep my draft using the reviewed policy revision</Button><Button variant="outline" disabled={busy||lifecycle.locked} onClick={()=>{dirty.current=false;setDraft(policy);setConflict(false)}}>Use current session limits</Button></div>}
  {draft?<><label className="manager-field">Idle session limit in seconds<Input aria-label="Idle session limit in seconds" type="number" min={600} max={2592000} disabled={busy||lifecycle.locked} value={draft.idle_ttl_seconds} onChange={event=>{dirty.current=true;setDraft({...draft,idle_ttl_seconds:Number(event.target.value)})}}/></label><p className="manager-help">10 minutes–30 days. Activity renews through the managed refresh flow; access tokens last at most {draft.access_ttl_seconds} seconds.</p>
  <label className="manager-field">Absolute session limit in seconds<Input aria-label="Absolute session limit in seconds" type="number" min={draft.idle_ttl_seconds} max={31536000} disabled={busy||lifecycle.locked} value={draft.absolute_ttl_seconds} onChange={event=>{dirty.current=true;setDraft({...draft,absolute_ttl_seconds:Number(event.target.value)})}}/></label><p className="manager-help">Must cover the idle limit; maximum 365 days. Refresh and unlock cannot extend the device’s absolute expiry.</p><Button disabled={busy||lifecycle.locked||conflict||!valid} onClick={()=>void save()}>Save session limits</Button></>:<p role="status">Loading device session limits…</p>}
  {status&&<p role="status">{status}</p>}
 </section>
}
