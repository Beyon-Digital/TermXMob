import {useEffect,useId,useRef,useState} from 'react';
import {Brain,LoaderCircle} from 'lucide-react';
import {json,request,type Session} from '../lib/api';
import {captureSessionWrite,serializeSessionWrite} from '../lib/session-writes';
type Option={id:string;name?:string;category?:string;type?:string;currentValue?:string;options?:{value:string;name?:string}[]};
type Configuration={config_options?:Option[];model_configurations?:Record<string,{config_options?:Option[]}>;stale?:boolean;refresh_error?:string|null};
const label=(value:string)=>value.replaceAll('_',' ').replace(/^./,character=>character.toUpperCase());
const rangeKeys=['ArrowLeft','ArrowRight','ArrowUp','ArrowDown','Home','End','PageUp','PageDown'];

/** Range positions are catalogue values, never guessed model capabilities. */
export default function ReasoningPicker({session,onUpdated,onError,disabled=false}:{session:Session;onUpdated:(session:Session)=>void;onError:(error:unknown)=>void;disabled?:boolean}){
 const identifier=useId();
 const [catalogue,setCatalogue]=useState<{selection:string;configuration:Configuration}|null>(null),[loading,setLoading]=useState(false),[saving,setSaving]=useState(false),[drafts,setDrafts]=useState<Record<string,number>>({});
 const selection=JSON.stringify([session.id,session.engine,session.model,session.provider_id,session.runner_id]);
 const current=useRef({selection,generation:0,operation:0,pending:false});
 const draftValues=useRef<Record<string,number>>({});
 useEffect(()=>()=>{current.current.generation++;current.current.operation++},[]);
 if(current.current.selection!==selection){current.current={selection,generation:current.current.generation+1,operation:current.current.operation+1,pending:false};draftValues.current={}}
 useEffect(()=>{
  let cancelled=false;setSaving(false);setCatalogue(null);setDrafts({});setLoading(false);
  if(session.engine==='internal'||session.runner_id)return;
  setLoading(true);
  request<Configuration>('/api/workspace/sessions/'+session.id+'/reasoning').then(configuration=>{if(!cancelled)setCatalogue({selection,configuration})}).catch(error=>{if(!cancelled)onError(error)}).finally(()=>{if(!cancelled)setLoading(false)});
  return()=>{cancelled=true};
 },[selection]);
 const configuration=catalogue?.selection===selection?catalogue.configuration:null;
 const selected=session.model?configuration?.model_configurations?.[session.model]:configuration;
 const options=(selected?.config_options||[]).filter(option=>['thought_level','reasoning','effort'].includes(option.category||'')&&option.type==='select'&&option.options?.length);
 const unavailable=!options.length||configuration?.stale||!!configuration?.refresh_error;

 async function choose(option:Option,index:number){
  if(disabled||loading||unavailable||current.current.pending)return;
  const values=[{value:'',name:'Default'},...(option.options||[])],value=values[index]?.value;
  if(value===undefined||value===(session.reasoning_config?.[option.id]||''))return;
  const sessionId=session.id,generation=current.current.generation,operation=++current.current.operation;
  const intent=captureSessionWrite();
  const owns=()=>current.current.generation===generation&&current.current.operation===operation;
  current.current.pending=true;setSaving(true);
  try{
   // Draft/scroll saves advance revisions too. Read the current revision, then
   // ensure it still describes the engine/model whose capabilities we showed.
   const result=await serializeSessionWrite(sessionId,async()=>{
    if(!owns())return;
    const latest=await request<Session>('/api/workspace/sessions/'+sessionId+'?turns=false',{signal:intent.signal});
    intent.assertCurrent();
    if(!owns())return;
    if(latest.engine!==session.engine||(latest.model||null)!==(session.model||null)||(latest.runner_id||null)!==(session.runner_id||null)||(latest.provider_id||null)!==(session.provider_id||null))throw new Error('This conversation’s model changed. Reopen Model and effort before choosing an effort level.');
    const reasoning_config={...latest.reasoning_config};
    if(value)reasoning_config[option.id]=value;else delete reasoning_config[option.id];
    return request<Session>('/api/workspace/sessions/'+sessionId,{...json('PATCH',{revision:latest.revision,changes:{reasoning_config}}),signal:intent.signal});
   },intent);
   if(result&&owns())onUpdated(result);
  }catch(error){if(owns()&&intent.isCurrent())onError(error)}finally{
   intent.release();
   if(owns()){current.current.pending=false;setSaving(false);delete draftValues.current[option.id];setDrafts({...draftValues.current})}
  }
 }

 if(unavailable)return <div className="reasoning-default"><Brain size={17}/><div><strong>{loading?'Checking effort options…':configuration?.stale||configuration?.refresh_error?'Effort temporarily unavailable':'Engine default effort'}</strong><p>{configuration?.stale||configuration?.refresh_error?'Refresh this engine in Models & engines to change effort. Your current choice is preserved.':'This engine chooses its own reasoning level.'}</p></div>{loading&&<LoaderCircle size={14} className="spin"/>}</div>;

 return <section className="reasoning-controls" aria-label="Thinking effort">
  {options.map(option=>{
   const values=[{value:'',name:'Default'},...(option.options||[]).map(entry=>({...entry,name:entry.name||label(entry.value)}))];
   const stored=session.reasoning_config?.[option.id]||'';
   const index=drafts[option.id]??Math.max(0,values.findIndex(entry=>entry.value===stored));
   const selectedLabel=values[index]?.name||'Default';
   const defaultLabel=option.options?.find(entry=>entry.value===option.currentValue)?.name||option.currentValue;
   const valueLabel=index===0&&defaultLabel?'Default · '+label(defaultLabel):label(selectedLabel);
   const id=identifier+'-'+option.id;
   const commit=()=>void choose(option,draftValues.current[option.id]??index);
   return <div className="reasoning-control" key={option.id}>
    <div className="reasoning-heading"><label htmlFor={id}>{option.name||'Reasoning effort'}</label><span>{saving?<LoaderCircle size={12} className="spin"/>:<Brain size={13}/>}<output htmlFor={id}>{valueLabel}</output></span></div>
    <input id={id} type="range" className="reasoning-slider" min={0} max={values.length-1} step={1} value={index} aria-valuetext={valueLabel} aria-describedby={identifier+'-hint'} disabled={disabled||loading||saving} onChange={event=>{draftValues.current={...draftValues.current,[option.id]:Number(event.target.value)};setDrafts({...draftValues.current})}} onPointerUp={commit} onKeyUp={event=>{if(rangeKeys.includes(event.key))commit()}} onBlur={commit}/>
    <div className="reasoning-scale" aria-hidden="true">{values.map((value,mark)=><span key={value.value} className={mark===index?'selected':''}>{label(value.name||value.value)}</span>)}</div>
   </div>;
  })}
  <p id={identifier+'-hint'} className="control-description">More effort gives the model more time to reason. Applies to the next turn.</p>
 </section>;
}
