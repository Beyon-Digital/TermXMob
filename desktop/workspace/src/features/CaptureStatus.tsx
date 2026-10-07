import {useCallback,useEffect,useState} from 'react';
import {Button} from '../components/ui/button';
import {request,json} from '../lib/api';

type Capture={id:string;kind:'window'|'tab';state:'private'|'recording'|'preview';stop_allowed:boolean};
export default function CaptureStatus({onOpen,onError,enabled=true}:{onOpen:(surface:'browser'|'computer')=>void;onError:(error:unknown)=>void;enabled?:boolean}){
 const [captures,setCaptures]=useState<Capture[]>([]),[unavailable,setUnavailable]=useState(false),[busy,setBusy]=useState(false);
 const refresh=useCallback(async()=>{const value=await request<{captures:Capture[]}>('/api/desktop/recording/status');setCaptures(value.captures);setUnavailable(false)},[]);
 useEffect(()=>{if(!enabled){setCaptures([]);return}let live=true,pending=false;const update=async()=>{if(pending)return;pending=true;try{const value=await request<{captures:Capture[]}>('/api/desktop/recording/status');if(live){setCaptures(value.captures);setUnavailable(false)}}catch{if(live)setUnavailable(true)}finally{pending=false}};void update();const timer=setInterval(()=>void update(),2000);window.addEventListener('termx-capture-changed',update);return()=>{live=false;clearInterval(timer);window.removeEventListener('termx-capture-changed',update)}},[enabled]);
 if(!enabled||(!captures.length&&!unavailable))return null;
 const recording=captures.filter(row=>row.state==='recording').length,privateCount=captures.filter(row=>row.state==='private').length;
 const stop=async()=>{setBusy(true);const stopped:string[]=[];try{for(const row of captures.filter(row=>row.stop_allowed)){await request(row.kind==='window'?`/api/desktop/recording/captures/${row.id}`:`/api/browser/tabs/${row.id}/recording`,row.kind==='window'?{method:'DELETE'}:json('POST',{enabled:false}));stopped.push(row.id)}}catch(error){onError(error)}finally{if(stopped.length)window.dispatchEvent(new CustomEvent('termx-capture-changed',{detail:{stopped}}));try{await refresh()}catch(error){onError(error)}setBusy(false)}};
 return <div className="capture-status" role="group" aria-label="Active capture controls"><span role="status" aria-live="polite">{unavailable?'Capture status unavailable':privateCount?`${privateCount} private · models paused`:recording?`${recording} recording`:`${captures.length} window capture`}</span>{captures.length>0&&<><Button variant="ghost" size="sm" aria-label="Open capture controls" onClick={()=>onOpen(captures.some(row=>row.kind==='window')?'computer':'browser')}>Open</Button><Button variant="ghost" size="sm" disabled={busy||!captures.some(row=>row.stop_allowed)} onClick={()=>void stop()}>Stop capture</Button></>}</div>
}
