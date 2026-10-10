import QRCode from 'qrcode';
import {useEffect,useRef,useState} from 'react';
import {Button} from '../components/ui/button';
import {Input} from '../components/ui/input';
import {request,json} from '../lib/api';
import './managed-pairing.css';

type Actor={principal:{id:string;display_name:string;scopes:string[]};session_id:string;host_id:string};
type Proof={ticket:string;host_id:string;scopes:string[];expires_at:number;expires_in:number};
const labels:Record<string,string>={'machine-view':'Machine status','terminal-view':'View terminals','terminal-control':'Control terminals','files-read':'Read files','files-write':'Write files','git-read':'Read Git','git-write':'Change Git','desktop-view':'View computer','desktop-control':'Control computer','network-manage':'Manage network','agent-view':'View chats and tasks','agent-run':'Run agents','agent-control':'Control agents','ai-settings':'AI settings','host-admin':'Host administration'};
export function ManagedPairingForm({actor}:{actor:Actor}){
 const [scopes,setScopes]=useState<string[]>(()=>['machine-view','files-read','agent-view'].filter(scope=>actor.principal.scopes.includes(scope)));
 const [associate,setAssociate]=useState(false),[consent,setConsent]=useState(false),[legacy,setLegacy]=useState('');
 const [proof,setProof]=useState<Proof|null>(null),[busy,setBusy]=useState(false),[error,setError]=useState(''),[now,setNow]=useState(Date.now());
 const [machine,setMachine]=useState(()=>window.location.protocol==='https:'?window.location.origin:''),[copied,setCopied]=useState(false);
 const [qr,setQr]=useState('');
 const live=useRef(true),epoch=useRef(0);
 useEffect(()=>{live.current=true;const changed=()=>{epoch.current++;setProof(null);setLegacy('')};window.addEventListener('termx-signed-out',changed);return()=>{live.current=false;epoch.current++;window.removeEventListener('termx-signed-out',changed)}},[]);
 useEffect(()=>{epoch.current++;setProof(null);setBusy(false);setError('');setCopied(false);setLegacy('');setConsent(false);setAssociate(false);setScopes(['machine-view','files-read','agent-view'].filter(scope=>actor.principal.scopes.includes(scope)))},[actor.principal.id,actor.session_id]);
 useEffect(()=>{if(!proof)return;const timer=setInterval(()=>setNow(Date.now()),1000);return()=>clearInterval(timer)},[proof]);
 const expired=!!proof&&proof.expires_at*1000<=now;
 let pairingLink='';
 try{const address=new URL(machine);if(address.protocol==='https:'&&address.pathname==='/'&&!address.username&&!address.password&&!address.search&&!address.hash&&proof&&!expired){address.hash=new URLSearchParams({termx_pair:proof.ticket,host_id:proof.host_id}).toString();pairingLink=address.href}}catch{/* An incomplete machine address has no usable pairing link. */}
 useEffect(()=>{let live=true;setQr('');if(pairingLink)void QRCode.toDataURL(pairingLink,{width:256,margin:4,errorCorrectionLevel:'M'}).then(value=>{if(live)setQr(value)}).catch(()=>{if(live)setError('QR rendering failed. Use the one-use link or retry.')});return()=>{live=false}},[pairingLink]);
 async function create(){const current=epoch.current;setBusy(true);setError('');try{
  const result=await request<Proof>('/auth/pair/issue',json('POST',{scopes,...(associate?{legacy_token:legacy,associate_legacy:consent}:{})}));
  if(live.current&&current===epoch.current){setProof(result);setLegacy('');setNow(Date.now())}
 }catch(cause){if(live.current&&current===epoch.current)setError(cause instanceof Error?cause.message:String(cause))}finally{if(live.current&&current===epoch.current)setBusy(false)}}
 async function discard(){if(!proof)return;setBusy(true);setError('');try{await request('/auth/pair/revoke',json('POST',{ticket:proof.ticket}));setProof(null)}catch(cause){setError(cause instanceof Error?cause.message:String(cause))}finally{setBusy(false)}}
 return <section className="managed-pairing" aria-label="Managed mobile pairing"><h2>Pair a device with QR</h2><p className="manager-help">Account: {actor.principal.display_name}. This session: {actor.session_id}. Host: {actor.host_id}. The device receives the scopes you choose under this account’s current project and resource permissions.</p>
 <form className="manager-form" aria-label="Create managed pairing ticket" onSubmit={event=>{event.preventDefault();void create()}}>
 <fieldset className="provider-capabilities" disabled={busy||!!proof}><legend>Device permissions</legend>{actor.principal.scopes.map(scope=><label key={scope}><input type="checkbox" checked={scopes.includes(scope)} onChange={event=>setScopes(prior=>event.target.checked?[...prior,scope]:prior.filter(value=>value!==scope))}/>{labels[scope]||scope}</label>)}</fieldset>
 {actor.principal.scopes.includes('host-admin')&&<fieldset className="pairing-legacy" disabled={busy||!!proof}><legend>Existing legacy device</legend><label className="pairing-consent"><input type="checkbox" checked={associate} onChange={event=>{setAssociate(event.target.checked);setConsent(false);setLegacy('')}}/>Associate an existing legacy device with this account</label>{associate&&<><label>Legacy device token<Input type="password" autoComplete="off" value={legacy} onChange={event=>setLegacy(event.target.value)}/></label><label className="pairing-consent"><input type="checkbox" checked={consent} onChange={event=>setConsent(event.target.checked)}/>I explicitly associate this device with {actor.principal.display_name} and revoke its legacy token when pairing completes</label><p className="manager-help">Legacy permissions limit this grant. Existing unclaimed data keeps its current ownership policy. Retirement of legacy access is unchanged.</p></>}</fieldset>}
 <Button type="submit" disabled={busy||!!proof||!scopes.length||(associate&&(!legacy||!consent))}>Create one-use pairing ticket</Button>
 </form>
 {proof&&<div className="manager-form" aria-live="polite"><p>{expired?'Pairing ticket expired':`One-use ticket expires ${new Date(proof.expires_at*1000).toLocaleTimeString()}`}. Scan this code with the device camera to sign in through the browser, or open the one-use link. Use a verified HTTPS machine address.</p><label>Host ID<Input readOnly value={proof.host_id}/></label>{!expired&&<><label>One-use pairing ticket<Input readOnly value={proof.ticket} autoComplete="off"/></label><label>Machine HTTPS address<Input type="url" placeholder="https://machine.example" value={machine} onChange={event=>{setMachine(event.target.value);setCopied(false)}}/></label>{qr&&<img src={qr} width={256} height={256} alt="One-use device sign-in QR code"/>}{pairingLink&&<label>One-use pairing link<Input readOnly value={pairingLink} autoComplete="off"/></label>}<Button type="button" variant="secondary" disabled={!pairingLink} onClick={()=>{if(!navigator.clipboard){setError('Copy is unavailable; select the pairing link and copy it manually.');return}void navigator.clipboard.writeText(pairingLink).then(()=>setCopied(true)).catch(()=>setError('Copy is unavailable; select the pairing link and copy it manually.'))}}>{copied?'Pairing link copied':'Copy one-use pairing link'}</Button></>}<p>Granted permissions: {proof.scopes.map(scope=>labels[scope]||scope).join(', ')}.</p><Button type="button" variant="secondary" disabled={busy} onClick={()=>void discard()}>Revoke and clear pairing ticket</Button></div>}
 {error&&<p role="alert">{error}</p>}<p className="manager-help">Pairing tickets expire after five minutes and work once. Save the resulting mobile session in its OS secure store; revoke it from Your sessions when needed. This form keeps ticket and legacy evidence only in memory.</p></section>
}
