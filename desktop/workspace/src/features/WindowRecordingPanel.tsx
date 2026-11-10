import {useEffect,useRef,useState} from 'react';
import {Lock,Monitor,Shield} from 'lucide-react';
import {Button} from '../components/ui/button';
import {Input} from '../components/ui/input';
import {request,json} from '../lib/api';

interface WindowTarget{id:string;pid:number;app:string;title:string;width:number;height:number}
interface Capture{id:string;private:boolean;window:WindowTarget;expires_at:number}
const base='/api/desktop/recording';
/** A private preview of one application window for a person. Agents never see it: they control applications through desktop control. */
export default function WindowRecordingPanel({onError}:{onError:(error:unknown)=>void}){
 const [targets,setTargets]=useState<WindowTarget[]>([]),[selected,setSelected]=useState(''),[capture,setCapture]=useState<Capture|null>(null),[image,setImage]=useState(''),[reason,setReason]=useState(''),[text,setText]=useState(''),[busy,setBusy]=useState(false),[redactions,setRedactions]=useState('[]');
 const consents=useRef<string[]>([]),alive=useRef(true),flight=useRef(false),captureEpoch=useRef(0);
 useEffect(()=>{const stopped=(event:Event)=>{const ids=(event as CustomEvent<{stopped?:string[]}>).detail?.stopped||[];if(capture&&ids.includes(capture.id)){captureEpoch.current++;setCapture(null);setImage('')}consents.current=consents.current.filter(id=>!ids.includes(id))};window.addEventListener('termx-capture-changed',stopped);return()=>window.removeEventListener('termx-capture-changed',stopped)},[capture?.id]);
 const stopCapture=async(id:string)=>{await request(base+'/captures/'+id,json('DELETE'));consents.current=consents.current.filter(value=>value!==id)};
 const startCapture=async(window_id:string)=>{const row=await request<Capture>(base+'/captures',json('POST',{window_id}));if(!alive.current){await stopCapture(row.id);return null}consents.current.push(row.id);return row};
 const run=async(work:()=>Promise<void>)=>{if(flight.current)return;flight.current=true;setBusy(true);try{await work()}catch(error){if(alive.current)onError(error)}finally{flight.current=false;if(alive.current)setBusy(false)}};
 const refresh=async()=>{const data=await request<{windows:WindowTarget[];reason:string}>(base+'/windows');setTargets(data.windows);setReason(data.reason)};
 useEffect(()=>{void refresh().catch(onError)},[]);
 useEffect(()=>{if(!capture)return;let stopped=false;let pending=false;const update=async()=>{if(pending)return;pending=true;try{const data=await request<{image:string}>(base+'/captures/'+capture.id+'/preview');if(!stopped)setImage(data.image)}catch(error){if(!stopped){setImage('');onError(error)}}finally{pending=false}};void update();const timer=setInterval(()=>{void update()},2000);return()=>{stopped=true;clearInterval(timer)}},[capture?.id,capture?.private,redactions]);
 useEffect(()=>{alive.current=true;return()=>{alive.current=false;for(const id of consents.current)void request(base+'/captures/'+id,json('DELETE')).catch(()=>{});consents.current=[]}},[]);
 const configure=async(data:unknown)=>{if(!capture)return;const epoch=captureEpoch.current;const updated=await request<Capture>(base+'/captures/'+capture.id,json('PATCH',data));if(alive.current&&captureEpoch.current===epoch)setCapture(updated)};
 const input=async(event:unknown)=>{if(!capture)return;await request(base+'/captures/'+capture.id+'/input',json('POST',{event}));};
 return <section className="window-recording-panel" aria-label="Window preview">
  <header><Monitor size={16}/><strong>Window preview</strong><span className="flex-spacer"/>{capture&&<small>{capture.private?<><Lock size={12}/> Private · models paused</>:<><Shield size={12}/> Human preview</>}</small>}</header>
  <p>Select one application window to see it here. Only you see this preview, and consent expires after 10 minutes. Text you send is passed to the window and is not saved.</p>
  <div className="window-recording-actions"><select aria-label="Application window" value={selected} onChange={event=>setSelected(event.target.value)}><option value="">Choose an application window</option>{targets.map(target=><option key={target.id} value={target.id}>{target.app} · {target.title||'Untitled window'} · {target.width} × {target.height}</option>)}</select><Button variant="outline" disabled={busy} onClick={()=>void run(refresh)}>Refresh windows</Button><Button disabled={busy||!selected||!!capture} onClick={()=>void run(async()=>setCapture(await startCapture(selected)))}>Start window capture</Button></div>
  {reason&&<p role="status">{reason}</p>}
  {capture&&<><div className="window-recording-actions"><Button variant="outline" disabled={busy} onClick={()=>void run(()=>configure({private:!capture.private}))}>{capture.private?'End private mode':'Private mode'}</Button><Button variant="ghost" disabled={busy} onClick={()=>void run(async()=>{await stopCapture(capture.id);setCapture(null);setImage('')})}>Stop capture</Button></div>
   {image&&<img className="window-recording-preview" src={image} alt={capture.private?'Private application window, visible only to you':'Selected application window; click to send a click to it'} onClick={event=>{const rect=event.currentTarget.getBoundingClientRect();void run(()=>input({type:'pointer',action:'click',x:(event.clientX-rect.left)/rect.width,y:(event.clientY-rect.top)/rect.height,button:1}))}}/>}
   <div className="window-recording-actions"><Input aria-label="Window text" value={text} onChange={event=>setText(event.target.value)} placeholder="Text to send to the selected window"/><Button disabled={busy||!text} onClick={()=>void run(async()=>{await input({type:'text',data:text});setText('')})}>Type text</Button></div>
   <label>Redaction regions (normalized x, y, width, height)<textarea aria-label="Window redaction regions" value={redactions} onChange={event=>setRedactions(event.target.value)}/></label><Button variant="outline" disabled={busy} onClick={()=>void run(()=>configure({redactions:JSON.parse(redactions)}))}>Apply redactions</Button>
  </>}
 </section>
}
