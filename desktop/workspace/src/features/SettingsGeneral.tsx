import {useEffect,useState} from 'react';
import {Button} from '../components/ui/button';
import SettingsIcon from '../components/SettingsIcon';
import {gql} from '../lib/api';
import type {ThemePreference} from '../lib/theme';

export default function SettingsGeneral({account,theme,onTheme,onSelect,onSignOut}:{account:string;theme:ThemePreference;onTheme:(value:ThemePreference)=>void;onSelect:(category:string)=>void;onSignOut:()=>Promise<void>}){
 const [host,setHost]=useState('Connected host'),[busy,setBusy]=useState(false),[error,setError]=useState('');
 useEffect(()=>{let live=true;gql<{machine:{hostname:string}}>('{ machine { hostname } }').then(value=>{if(live&&value.machine?.hostname)setHost(value.machine.hostname)}).catch(()=>{});return()=>{live=false}},[]);
 async function logout(){setBusy(true);setError('');try{await onSignOut()}catch(reason){setError(reason instanceof Error?reason.message:'Could not sign out. Try again.')}finally{setBusy(false)}}
 return <section className="settings-general" aria-label="General settings">
  <header className="settings-page-heading"><SettingsIcon kind="general" size={44}/><div><h1>General</h1><p>Your everyday controls.</p></div></header>
  <div className="settings-row"><div><h2>Account</h2><p>{account}</p></div><Button variant="outline" disabled={busy} onClick={()=>void logout()}>{busy?'Signing out…':'Sign out'}</Button></div>
  {error&&<p role="alert" className="manager-error">{error}</p>}
  <div className="settings-row"><div><h2>Host</h2><p>{host}</p></div><Button variant="ghost" onClick={()=>onSelect('access')}>Manage access</Button></div>
  <div className="settings-row" data-settings-autosave><div><h2>Theme</h2><p>Choose a look or follow your device.</p></div><select aria-label="Theme preference" value={theme} onChange={event=>onTheme(event.target.value as ThemePreference)}><option value="system">System</option><option value="light">Light</option><option value="dark">Dark</option></select></div>
  <div className="settings-row"><div><h2>Models &amp; engines</h2><p>Choose what powers your workers.</p></div><Button variant="ghost" onClick={()=>onSelect('models')}>Manage models</Button></div>
 </section>
}
