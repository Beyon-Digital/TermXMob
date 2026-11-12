import {lazy,Suspense,useEffect,useState} from 'react';
import {Button} from '../components/ui/button';
import {SettingsRow,SettingsSection} from '../components/ui/settings-section';
import {Select} from '../components/ui/select';
import {gql} from '../lib/api';
import type {ThemePreference} from '../lib/theme';
import type {Bindings} from '../lib/keybindings';

const Keybindings=lazy(()=>import('./Keybindings'));

export type SettingsGeneralProps={account:string;theme:ThemePreference;onTheme:(value:ThemePreference)=>void;bindings:Bindings;onBindings:(value:Bindings)=>void;onSignOut:()=>Promise<void>};

/** The Preferences page. Mount it with `key` set to the account and host so a different identity starts clean. */
export default function SettingsGeneral({account,theme,onTheme,bindings,onBindings,onSignOut}:SettingsGeneralProps){
 const [host,setHost]=useState('Connected host'),[busy,setBusy]=useState(false),[error,setError]=useState('');
 useEffect(()=>{
  let live=true;
  void gql<{machine:{hostname:string}}>('{ machine { hostname } }').then(value=>{if(live&&value.machine?.hostname)setHost(value.machine.hostname)}).catch(()=>{});
  return()=>{live=false};
 },[]);
 async function logout(){
  if(busy)return;
  setBusy(true);setError('');
  try{await onSignOut()}
  catch(reason){setError(reason instanceof Error?reason.message:'Could not sign out. Try again.')}
  finally{setBusy(false)}
 }
 return <section className="settings-general" aria-labelledby="preferences-heading">
  <header className="settings-page-heading"><div><h2 id="preferences-heading">General</h2><p>Your account, appearance and keyboard shortcuts.</p></div></header>
  <SettingsSection level={3} title="Account">
   <SettingsRow label="Signed in as" description={account}><Button variant="outline" disabled={busy} onClick={()=>void logout()}>{busy?'Signing out…':'Sign out'}</Button></SettingsRow>
   <SettingsRow label="Host" description={host}>{null}</SettingsRow>
  </SettingsSection>
  {error&&<p role="alert" className="manager-error">{error}</p>}
  <div className="settings-appearance" data-settings-autosave>
   <SettingsSection level={3} title="Appearance">
    <SettingsRow label="Theme" description="System follows your device appearance.">{control=><Select {...control} aria-label="Theme preference" value={theme} onChange={event=>onTheme(event.target.value as ThemePreference)}><option value="system">System</option><option value="light">Light</option><option value="dark">Dark</option></Select>}</SettingsRow>
   </SettingsSection>
  </div>
  <section className="settings-block" aria-labelledby="preferences-shortcuts" data-settings-autosave>
   <h3 id="preferences-shortcuts">Keyboard shortcuts</h3>
   <Suspense fallback={<p className="settings-block-help">Loading shortcuts…</p>}><Keybindings bindings={bindings} onChange={onBindings}/></Suspense>
  </section>
 </section>;
}
