import {useEffect,useId,useRef,useState,type CSSProperties,type ReactNode} from 'react';
import {LoaderCircle,RotateCcw,Zap} from 'lucide-react';
import {Button} from '../components/ui/button';
import {json,request,type Session} from '../lib/api';
import {captureSessionWrite,serializeSessionWrite} from '../lib/session-writes';
type Option={id:string;name?:string;category?:string;type?:string;currentValue?:string;options?:{value:string;name?:string}[]};
type Configuration={config_options?:Option[];model_configurations?:Record<string,{config_options?:Option[]}>;stale?:boolean;refresh_error?:string|null};
export const effortLabel=(value:string)=>value==='xhigh'?'Extra high':value.replaceAll('_',' ').replaceAll('-',' ').replace(/^./,character=>character.toUpperCase());
type HeaderState={label:string;reset:()=>void;resetDisabled:boolean;busy:boolean};
const rangeKeys=['ArrowLeft','ArrowRight','ArrowUp','ArrowDown','Home','End','PageUp','PageDown'];

/** Range positions are catalogue values, never guessed model capabilities. */
export default function ReasoningPicker({session,onUpdated,onError,disabled=false,renderHeader}:{session:Session;onUpdated:(session:Session)=>void;onError:(error:unknown)=>void;disabled?:boolean;renderHeader?:(state:HeaderState)=>ReactNode}){
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

 const heading=(state:HeaderState)=>renderHeader?renderHeader(state):<div className="effort-popover-heading">{state.busy?<LoaderCircle className="effort-bolt spin" size={16} aria-hidden="true"/>:<Zap className="effort-bolt" size={16} aria-hidden="true"/>}<strong className="effort-current-label">{state.label}</strong><Button className="effort-reset" variant="ghost" size="icon" aria-label="Reset effort" disabled={state.resetDisabled} onClick={state.reset}><RotateCcw size={16}/></Button></div>;
 if(unavailable){
  const stored=Object.values(session.reasoning_config||{}).find(value=>typeof value==='string');
  return <section className="effort-picker" aria-label="Thinking effort" aria-busy={loading||undefined}>
   {heading({label:effortLabel(stored||'Default'),reset:()=>{},resetDisabled:true,busy:loading})}
   {loading&&<span className="model-picker-accessible-title" role="status">Loading effort options</span>}
   <div className="effort-range effort-range-unavailable" aria-hidden="true"><span className="effort-track"/></div>
   {!loading&&(configuration?.stale||configuration?.refresh_error)&&<span className="effort-unavailable" role="status">Effort unavailable</span>}
  </section>;
 }

 const primary=options[0],primaryValues=[{value:''},...(primary.options||[])];
 const primaryIndex=drafts[primary.id]??Math.max(0,primaryValues.findIndex(entry=>entry.value===(session.reasoning_config?.[primary.id]||'')));
 // Keep the primary header in the same DOM position while discovery resolves.
 // A keyboard user can already own its model button during the loading state.
 return <section className="effort-picker" aria-label="Thinking effort" aria-busy={saving||undefined}>
  {heading({label:effortLabel(primaryValues[primaryIndex]?.value||'Default'),reset:()=>void choose(primary,0),resetDisabled:disabled||loading||saving||primaryIndex===0,busy:saving})}
  {options.map((option,optionIndex)=>{
   // Catalogue names can be full descriptions. The API value is the short, exact effort identity.
   const values=[{value:''},...(option.options||[])];
   const stored=session.reasoning_config?.[option.id]||'';
   const index=drafts[option.id]??Math.max(0,values.findIndex(entry=>entry.value===stored));
   const valueLabel=effortLabel(values[index]?.value||'Default');
   const id=identifier+'-'+option.id;
   const commit=()=>void choose(option,draftValues.current[option.id]??index);
   return <div className="effort-control" key={option.id}>
    {optionIndex>0&&<div className="effort-secondary-heading"><label htmlFor={id}>{option.name||'Reasoning effort'}</label><output htmlFor={id}>{valueLabel}</output></div>}
    <div className="effort-range" style={{'--effort-progress':index/(values.length-1)} as CSSProperties} data-disabled={disabled||loading||saving||undefined}>
     <span className="effort-track" aria-hidden="true"><span className="effort-particles"/></span><span className="effort-thumb-position" aria-hidden="true"><span className="effort-thumb"/></span>
     <input id={id} type="range" className="effort-slider" aria-label={option.name||'Reasoning effort'} min={0} max={values.length-1} step={1} value={index} aria-valuetext={valueLabel} disabled={disabled||loading||saving} onChange={event=>{draftValues.current={...draftValues.current,[option.id]:Number(event.target.value)};setDrafts({...draftValues.current})}} onPointerUp={commit} onKeyUp={event=>{if(rangeKeys.includes(event.key))commit()}} onBlur={commit} onPointerCancel={()=>{delete draftValues.current[option.id];setDrafts({...draftValues.current})}}/>
    </div>
   </div>;
  })}
 </section>;
}
