import {useEffect,useState} from 'react';
import {Button} from '../components/ui/button';
import {Select} from '../components/ui/select';
import {Dialog,DialogContent,DialogDescription,DialogTitle} from '../components/ui/dialog';
import PresetPicker,{type AgentPreset} from './PresetPicker';
import SessionModelControls from './SessionModelControls';
import VoiceControls from './VoiceControls';
import {request,type Session,type Engine,type Provider,type Project} from '../lib/api';

type Runner={id:string;project:string;status:string;configuration:{image:string;name?:string;kind?:string;root?:string}};
type RunnerAccount={provider_id:string;credential_ref:string;name:string;models:string[]};
type Extension={id:string;description:string;version:string};
type ReasoningOption={id:string;name?:string;category?:string;type?:string;currentValue?:string;options?:{value:string;name?:string}[]};
type ReasoningConfig={config_options?:ReasoningOption[];model_configurations?:Record<string,{config_options?:ReasoningOption[]}>;stale?:boolean;refresh_error?:string|null};

const BUDGET_FIELDS:{key:string;label:string;min:number;max:number;step:number;format:(value:number)=>string}[]=[
 {key:'max_steps',label:'Steps',min:16,max:1024,step:8,format:value=>value+' steps'},
 {key:'max_seconds',label:'Wall clock',min:300,max:86400,step:300,format:value=>value>=3600?Math.round(value/360)/10+' h':Math.round(value/60)+' min'},
 {key:'shell_timeout_s',label:'Shell timeout',min:10,max:1800,step:10,format:value=>value+' s'},
 {key:'max_parallel_subagents',label:'Parallel sub-agents',min:1,max:16,step:1,format:value=>String(value)},
 {key:'max_subagents_total',label:'Total sub-agents',min:1,max:64,step:1,format:value=>String(value)},
];

export type SessionSettingsProps={open:boolean;onOpenChange:(open:boolean)=>void;session:Session|null;running:boolean;turnsCount:number;ownerId?:string;visible?:boolean;
 runnerOptions:Runner[];runnerAccounts:RunnerAccount[];
 engines:Engine[];providers:Provider[];models:string[];favorites:string[];favoriteKey:string;
 onFavorite:()=>void;onEngine:(engine:string)=>void;
 patch:(changes:Record<string,unknown>)=>Promise<void>;onError:(error:unknown)=>void;onRefresh:()=>void;
 reply:string;onTranscript:(text:string)=>void;onConversationSettings:()=>void};

export default function SessionSettings({open,onOpenChange,session,running,turnsCount,ownerId,visible=true,runnerOptions,runnerAccounts,engines,providers,models,favorites,favoriteKey,onFavorite,onEngine,patch,onError,onRefresh,reply,onTranscript,onConversationSettings}:SessionSettingsProps){
 const [compact,setCompact]=useState(()=>typeof window!=='undefined'&&window.matchMedia?window.matchMedia('(max-width:850px)').matches:false);
 useEffect(()=>{if(typeof window==='undefined'||!window.matchMedia)return;const media=window.matchMedia('(max-width:850px)'),listener=(event:MediaQueryListEvent)=>setCompact(event.matches);setCompact(media.matches);media.addEventListener('change',listener);return()=>media.removeEventListener('change',listener)},[]);
 const [presets,setPresets]=useState<AgentPreset[]>([]),[presetsLoading,setPresetsLoading]=useState(false);
 useEffect(()=>{setPresets([]);if(!open||!session||!visible)return;setPresetsLoading(true);let cancelled=false;const query=new URLSearchParams({engine:session.engine});if(session.project_id)query.set('project_id',session.project_id);if(session.runner_id)query.set('runner_id',session.runner_id);request<{presets:AgentPreset[]}>('/api/workspace/presets?'+query).then(value=>{if(!cancelled)setPresets(value.presets||[])}).catch(error=>{if(!cancelled)onError(error)}).finally(()=>{if(!cancelled)setPresetsLoading(false)});return()=>{cancelled=true}},[open,session?.id,session?.engine,session?.runner_id,session?.custom_agent_revision,visible]);
 const [extensions,setExtensions]=useState<Extension[]>([]);
 useEffect(()=>{if(!open||!session)return;request<{extensions:Extension[]}>('/api/workspace/available-extensions'+(session.project_id?'?project_id='+encodeURIComponent(session.project_id):'')).then(value=>setExtensions(value.extensions)).catch(onError)},[open,session?.id]);
 const [runBudget,setRunBudget]=useState<Record<string,number>>({max_steps:256,max_seconds:14400,shell_timeout_s:120,max_parallel_subagents:3,max_subagents_total:8});
 useEffect(()=>{if(session?.run_limits)setRunBudget({...session.run_limits})},[session?.id,JSON.stringify(session?.run_limits)]);
 const [reasoning,setReasoning]=useState<ReasoningConfig|null>(null),[reasoningLoading,setReasoningLoading]=useState(false),[effortDraft,setEffortDraft]=useState<Record<string,number>>({});
 const effortSelection=JSON.stringify([session?.id,session?.engine,session?.model,session?.runner_id]);
 useEffect(()=>{let cancelled=false;setReasoning(null);setEffortDraft({});if(!open||!session||session.engine==='internal'||session.runner_id)return;setReasoningLoading(true);request<ReasoningConfig>('/api/workspace/sessions/'+session.id+'/reasoning').then(value=>{if(!cancelled)setReasoning(value)}).catch(error=>{if(!cancelled)onError(error)}).finally(()=>{if(!cancelled)setReasoningLoading(false)});return()=>{cancelled=true}},[effortSelection,open]);
 const selectedCatalogue=session?.model?reasoning?.model_configurations?.[session.model]:reasoning;
 const effortOptions=(selectedCatalogue?.config_options||[]).filter(option=>['thought_level','reasoning','effort'].includes(option.category||'')&&option.type==='select'&&option.options?.length);
 async function commitEffort(option:ReasoningOption,index:number){if(!session)return;const values=(option.options||[]).map(entry=>entry.value),value=values[index];setEffortDraft(old=>{const next={...old};delete next[option.id];return next});if(!value||value===session.reasoning_config?.[option.id])return;await patch({reasoning_config:{...session.reasoning_config,[option.id]:value}})}
 if(!open||!session)return null;
 const body=<div className="settings-body">
  <div className="settings-group"><button className="settings-line" onClick={onConversationSettings}><span>Conversation</span><small>Rename, move between projects</small></button></div>
  <section className="settings-group"><h3>Model</h3>
   <div className="settings-field"><SessionModelControls session={session} engines={engines} providers={providers} runnerAccounts={runnerAccounts} models={models} favorites={favorites} favoriteKey={favoriteKey} running={running} onFavorite={onFavorite} onEngine={onEngine} onPatch={patch} onRefresh={onRefresh} onError={onError}/></div>
   {effortOptions.map(option=>{const values=(option.options||[]).map(entry=>entry.value);const stored=session.reasoning_config?.[option.id];const fallback=option.currentValue&&values.includes(option.currentValue)?option.currentValue:values[0];const index=effortDraft[option.id]??Math.max(0,values.indexOf(stored||fallback));const label=option.options?.[index]?.name||values[index];return <div className="effort-field" key={option.id}><label className="settings-label"><span>{option.name||'Reasoning effort'}</span><output>{stored?label:'Default · '+label}</output></label><input type="range" className="effort-range" min={0} max={values.length-1} step={1} value={index} disabled={running||reasoningLoading} aria-label={(option.name||'Reasoning effort')+' level'} onChange={event=>setEffortDraft(old=>({...old,[option.id]:Number(event.target.value)}))} onPointerUp={()=>void commitEffort(option,index)} onKeyUp={event=>{if(['ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(event.key))void commitEffort(option,index)}} onBlur={()=>void commitEffort(option,index)}/><div className="effort-marks">{values.map((value,mark)=><span key={value} className={mark===index?'on':''}>{option.options?.[mark]?.name||value}</span>)}</div></div>})}
  </section>
  <section className="settings-group"><h3>Agent</h3>
   <div className="settings-field"><span className="settings-label"><span>Preset</span><output>{presets.find(row=>row.id===session.custom_agent_id)?.name||(session.custom_agent_id?'Unavailable selected preset':'Default agent')}</output></span><PresetPicker loading={presetsLoading} presets={presets} value={session.custom_agent_id} disabled={running||(session.engine!=='internal'&&!!turnsCount)} onChange={id=>void patch({custom_agent_id:id})}/>
   {session.custom_agent_id&&<Button size="sm" variant="outline" disabled={running||(session.engine!=='internal'&&!!turnsCount)} onClick={()=>void patch({custom_agent_id:session.custom_agent_id})}>Refresh preset binding</Button>}
   {session.engine!=='internal'&&!!turnsCount&&<p>This native conversation keeps its preset. Create a new conversation to choose another.</p>}</div>
   {(extensions.length>0||session.engine==='claude')&&<div className="settings-field"><span className="settings-label"><span>Session tools</span><output>{session.workflow==='browser'?'Browser only':(session.extension_ids?.length?session.extension_ids.length+' skills':'All tools')}</output></span>
   {session.engine==='claude'&&<label>Tool workflow<Select aria-label="Session tool workflow" disabled={running||!!turnsCount} value={session.workflow||'coding'} onChange={event=>patch({workflow:event.target.value==='browser'?'browser':null})}><option value="coding">Coding tools</option><option value="browser">Browser only</option></Select></label>}
   {session.workflow==='browser'&&<p>Start a task, then hand it a tab from Browser. This dedicated conversation uses only controlled browser tools.</p>}
   {extensions.map(extension=><label className="settings-check" key={extension.id}><input type="checkbox" disabled={running} checked={session.extension_ids?.includes(extension.id)||false} onChange={event=>patch({extension_ids:event.target.checked?[...(session.extension_ids||[]),extension.id]:(session.extension_ids||[]).filter(id=>id!==extension.id)})}/>{extension.id} <small>{extension.version} · {extension.description}</small></label>)}</div>}
  </section>
  {session.engine==='internal'&&<section className="settings-group"><h3>Execution</h3>
   <div className="settings-field"><label className="settings-label"><span>Location</span></label><Select aria-label="Session execution location" value={session.runner_id||''} disabled={running} onChange={event=>{if(!event.target.value){void patch({runner_id:null,runner_credential_ref:null});return}const account=runnerAccounts.find(account=>account.provider_id===session.provider_id&&account.models.includes(session.model||''));if(!account){onError(new Error('Select a configured API provider and an explicit model before choosing a cloud runner.'));return}void patch({runner_id:event.target.value,runner_credential_ref:account.credential_ref})}}><option value="">This machine · {session.cwd}</option>{runnerOptions.filter(runner=>runner.status==='ready'&&runner.project===(session.project_id||'')).map(runner=><option key={runner.id} value={runner.id}>{runner.configuration.name||runner.configuration.image||runner.id} · {runner.configuration.root||'/workspace'}</option>)}</Select>
   {session.runner_id&&<p>Explicit account: {session.runner_credential_ref}. The internal agent runs on the selected target at {runnerOptions.find(row=>row.id===session.runner_id)?.configuration.root||'/workspace'}. {runnerOptions.find(row=>row.id===session.runner_id)?.configuration.kind==='machine'?'Commands use the machine’s SSH account permissions.':'Execution is inside an isolated container.'} Host hooks and native engines require local execution.</p>}
   {!runnerOptions.length&&<p>Connect a machine or enroll an isolated container in Runners before selecting cloud execution.</p>}</div>
  </section>}
  <section className="settings-group"><h3>Run budget</h3>
   {BUDGET_FIELDS.map(field=><label className="effort-field" key={field.key}><span className="settings-label"><span>{field.label}</span><output>{field.format(runBudget[field.key]??field.min)}</output></span><input type="range" className="effort-range" min={field.min} max={field.max} step={field.step} value={runBudget[field.key]??field.min} disabled={running} aria-label={'Run '+field.key} onChange={event=>setRunBudget(old=>({...old,[field.key]:Number(event.target.value)}))}/></label>)}
   <Button size="sm" variant="outline" disabled={running} onClick={()=>patch({run_limits:runBudget})}>Save run budget</Button>
  </section>
  <section className="settings-group"><h3>Voice</h3>
   <VoiceControls key={session.id} sessionId={session.id} projectId={session.project_id||null} ownerId={ownerId} visible={visible&&open} reply={reply} onTranscript={onTranscript}/>
  </section>
 </div>;
 if(compact)return <><button className="sheet-scrim" aria-label="Close session settings" onClick={()=>onOpenChange(false)}/><div className="settings-sheet" role="dialog" aria-modal="true" aria-label="Session settings"><button className="sheet-grab" aria-label="Close session settings" onClick={()=>onOpenChange(false)}><span/></button><h2>Session settings</h2><div className="sheet-scroll">{body}</div></div></>;
 return <Dialog open onOpenChange={onOpenChange}><DialogContent className="settings-dialog"><DialogTitle>Session settings</DialogTitle><DialogDescription>Engine, model, tools and budget for this conversation. Applies to the next turn.</DialogDescription>{body}</DialogContent></Dialog>;
}
