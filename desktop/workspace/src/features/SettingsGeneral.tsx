import {useEffect,useRef,useState} from 'react';
import {Button} from '../components/ui/button';
import SettingsIcon from '../components/SettingsIcon';
import {gql} from '../lib/api';
import type {ThemePreference} from '../lib/theme';

export type SettingsGeneralProps={account:string;hostKey?:string;theme:ThemePreference;onTheme:(value:ThemePreference)=>void;onSelect:(category:string)=>void;onSignOut:()=>Promise<void>};

export default function SettingsGeneral({account,hostKey,theme,onTheme,onSelect,onSignOut}:SettingsGeneralProps){
 const identity=hostKey||account;
 const [host,setHost]=useState({identity,name:'Connected host'}),[operation,setOperation]=useState({identity,busy:false,error:''});
 const current=useRef({identity,generation:0,busy:false});
 if(current.current.identity!==identity)current.current={identity,generation:current.current.generation+1,busy:false};
 useEffect(()=>{
  let live=true;
  setHost({identity,name:'Connected host'});setOperation({identity,busy:false,error:''});
  void gql<{machine:{hostname:string}}>('{ machine { hostname } }').then(value=>{if(live&&value.machine?.hostname)setHost({identity,name:value.machine.hostname})}).catch(()=>{});
  return()=>{live=false;current.current.generation++;current.current.busy=false};
 },[identity]);
 const busy=operation.identity===identity&&operation.busy,error=operation.identity===identity?operation.error:'';
 async function logout(){
  if(current.current.busy)return;
  const generation=current.current.generation;current.current.busy=true;setOperation({identity,busy:true,error:''});
  try{await onSignOut()}
  catch(reason){if(generation===current.current.generation)setOperation({identity,busy:false,error:reason instanceof Error?reason.message:'Could not sign out. Try again.'})}
  finally{if(generation===current.current.generation){current.current.busy=false;setOperation(value=>({...value,busy:false}))}}
 }
 return <section className="settings-general" aria-label="General settings">
  <header className="settings-page-heading"><SettingsIcon kind="general" size={36}/><div><h1>General</h1><p>Your everyday controls.</p></div></header>
  <div className="settings-row"><div><h2>Account</h2><p>{account}</p></div><Button variant="outline" disabled={busy} onClick={()=>void logout()}>{busy?'Signing out…':'Sign out'}</Button></div>
  {error&&<p role="alert" className="manager-error">{error}</p>}
  <div className="settings-row"><div><h2>Host</h2><p>{host.identity===identity?host.name:'Connected host'}</p></div><Button variant="ghost" onClick={()=>onSelect('access')}>Manage access</Button></div>
  <div className="settings-row" data-settings-autosave><div><h2>Theme</h2><p>Choose a look or follow your device.</p></div><select aria-label="Theme preference" value={theme} onChange={event=>onTheme(event.target.value as ThemePreference)}><option value="system">System</option><option value="light">Light</option><option value="dark">Dark</option></select></div>
  <div className="settings-row"><div><h2>Models &amp; engines</h2><p>Choose what powers your agents.</p></div><Button variant="ghost" onClick={()=>onSelect('models')}>Manage models</Button></div>
 </section>;
}
