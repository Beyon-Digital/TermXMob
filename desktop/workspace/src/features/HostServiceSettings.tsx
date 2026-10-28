import {useEffect,useRef,useState} from 'react';
import {Button} from '../components/ui/button';
import {Input} from '../components/ui/input';
import {Dialog,DialogContent,DialogDescription,DialogTitle} from '../components/ui/dialog';
import {gql,json,request} from '../lib/api';
import {useSettingsSave} from './SettingsSurface';
type Status={backend:string;installed:boolean;running:boolean;ready:boolean;managed:boolean;url?:string;port?:number|null;last_error?:string|null;supported:boolean;requires_login:boolean;tunnel_profile_id?:string|null};
type TunnelProfile={id:string;name:string;provider:string;kind:string};
type Action='install'|'start'|'stop'|'uninstall';
export default function HostServiceSettings(){
 const beginSave=useSettingsSave();
 const [status,setStatus]=useState<Status|null>(null),[error,setError]=useState(''),[notice,setNotice]=useState(''),[busy,setBusy]=useState(false),[review,setReview]=useState<Action|null>(null),[port,setPort]=useState('8787');const [tunnels,setTunnels]=useState<TunnelProfile[]>([]),[tunnel,setTunnel]=useState(''),[tunnelError,setTunnelError]=useState('');const alive=useRef(true),flight=useRef(false);
 async function load(){const result=await request<Status>('/api/host/service');if(alive.current){setStatus(result);if(result.port)setPort(String(result.port));setError('')}}
 useEffect(()=>{alive.current=true;void load().catch(reason=>{if(alive.current)setError(reason instanceof Error?reason.message:String(reason))});return()=>{alive.current=false}},[]);
 useEffect(()=>{let live=true;gql<{tunnels:{profiles:TunnelProfile[]}}>('{ tunnels { profiles { id name provider kind } } }').then(value=>{if(live)setTunnels(value.tunnels.profiles)}).catch(()=>{if(live)setTunnelError('Tunnel profiles are unavailable. You can install a local service without a tunnel.')});return()=>{live=false}},[]);
 async function run(action?:Action){if(flight.current)return;const saved=action==='install'?beginSave():undefined;flight.current=true;setBusy(true);setError('');setNotice('');try{if(action){await request('/api/host/service/'+action,json('POST',action==='install'?{port:Number(port),host:'127.0.0.1',...(tunnel?{tunnel_profile_id:tunnel}:{})}:{}));saved?.();if(alive.current){setReview(null);setNotice(action==='stop'||action==='uninstall'?'Host shutdown requested. The connection may close; saved schedules remain.':action+' requested. Refresh status to verify readiness.')}}if(!action||!['stop','uninstall'].includes(action))await load()}catch(reason){if(alive.current)setError(reason instanceof Error?reason.message:String(reason))}finally{flight.current=false;if(alive.current)setBusy(false)}}
 const selectedTunnel=tunnels.find(profile=>profile.id===tunnel);
 const validPort=Number.isInteger(Number(port))&&Number(port)>=1&&Number(port)<=65535;
 return <section aria-label="Background host service" className="settings-form-section">
  <h2>Background service</h2><p className="manager-help">Keep this host available when the desktop closes. The machine must stay awake.</p>
  {error&&<p role="alert">{error}</p>}{notice&&<p role="status">{notice}</p>}
  {status?<>
   <p role="status">{status.ready?'Ready':status.running?'Running · waiting for readiness':status.installed?'Installed · stopped':'Not installed'}</p>
   {status.last_error&&<p role="alert">{status.last_error}</p>}{!status.supported&&<p>Background service management is unavailable on this host.</p>}
   {status.installed&&<div className="creation-actions">
    {!status.running&&<Button variant="outline" disabled={busy||!status.supported} onClick={()=>setReview('start')}>Start background service</Button>}
    {status.running&&<Button variant="outline" disabled={busy||!status.supported} onClick={()=>setReview('stop')}>Stop background service</Button>}
   </div>}
   <details className="manager-disclosure"><summary>{status.installed?'Installation options':'Set up background service'}</summary><div className="manager-form">
    <label className="manager-field">Installation port<Input aria-label="Installation port" type="number" min="1" max="65535" value={port} disabled={busy} onChange={event=>setPort(event.target.value)}/></label>
    <label className="manager-field">Restore a tunnel after restart<select aria-label="Background service tunnel profile" value={tunnel} disabled={busy} onChange={event=>setTunnel(event.target.value)}><option value="">None · local service only</option>{tunnels.map(profile=><option key={profile.id} value={profile.id}>{profile.name} · {profile.provider} · {profile.kind}</option>)}</select></label>
    {tunnelError&&<p role="status">{tunnelError}</p>}
    <p className="manager-help">The service listens on this machine’s loopback address. Selecting a tunnel allows it to start after a host restart. Quick tunnel addresses can change.</p>
    <Button variant="outline" disabled={busy||!status.supported||!validPort||!!tunnel&&!selectedTunnel} onClick={()=>setReview('install')}>Install background service</Button>
   </div></details>
   <details className="manager-item-details"><summary>Service details</summary><p>{status.backend} · {status.managed?'Managed service':'Desktop host'}</p>{status.url&&<p>Host address: {status.url}</p>}{status.tunnel_profile_id&&<p>Installed service tunnel: {tunnels.find(profile=>profile.id===status.tunnel_profile_id)?.name||status.tunnel_profile_id}</p>}<p className="manager-help">Remote control requires a signed-in session. Desktop-bound broker permissions are unavailable after the desktop exits.</p>{status.installed&&<Button variant="outline" disabled={busy||!status.supported} onClick={()=>setReview('uninstall')}>Uninstall background service</Button>}</details>
  </>:!error&&<p role="status">Loading host service status…</p>}
  <Button variant="ghost" disabled={busy} onClick={()=>void run()}>Refresh host service status</Button>
  <Dialog open={!!review} onOpenChange={value=>{if(!value&&!busy)setReview(null)}}><DialogContent>
   <DialogTitle>Confirm {review} background service</DialogTitle>
   <DialogDescription>{review==='stop'||review==='uninstall'?'This can disconnect all clients and pause scheduled work until the host starts again. Saved work and schedules are retained.':'Set up the host on this machine to serve authenticated clients independently of the desktop. The machine must stay awake. Desktop-bound broker permissions are unavailable after the desktop exits.'}</DialogDescription>
   <p>{review==='install'?'Exact service address: http://127.0.0.1:'+port:review==='start'?'Start the installed service at '+(status?.url||'its saved local address')+'.':'Action: '+review+' the background service on this host.'}</p>
   {review==='install'&&<p>{selectedTunnel?'Restore tunnel '+selectedTunnel.name+' · '+selectedTunnel.provider+' · '+selectedTunnel.kind+' · exact profile '+selectedTunnel.id+' after host restarts.':'No tunnel will start automatically. The service remains local.'}</p>}
   {review==='start'&&status?.tunnel_profile_id&&<p>Start the saved tunnel profile: {status.tunnel_profile_id}.</p>}
   <Button disabled={busy} onClick={()=>review&&void run(review)}>Confirm {review} background service</Button><Button variant="ghost" disabled={busy} onClick={()=>setReview(null)}>Cancel</Button>
  </DialogContent></Dialog>
 </section>
}
