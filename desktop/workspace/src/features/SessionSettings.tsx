import {useEffect,useState} from 'react';
import {Bot,Check,ChevronRight,Cpu,Mic,Settings2,Wrench} from 'lucide-react';
import {Button} from '../components/ui/button';
import {Select} from '../components/ui/select';
import {Dialog,DialogContent,DialogDescription,DialogTitle} from '../components/ui/dialog';
import PresetPicker,{type AgentPreset} from './PresetPicker';
import SessionAccess from './SessionAccess';
import VoiceControls from './VoiceControls';
import {request,type Session,type Engine,type Provider} from '../lib/api';
import './chat-composer.css';

type Runner={id:string;project:string;status:string;configuration:{image:string;name?:string;kind?:string;root?:string}};
type RunnerAccount={provider_id:string;credential_ref:string;name:string;models:string[]};
type Extension={id:string;description:string;version:string};

// Keep Balanced aligned with termx.agent.limits.DEFAULT_LIMITS. A profile is
// applied only when selected; opening settings never replaces existing limits.
export const DEFAULT_RUN_BUDGET={max_steps:256,max_seconds:14400,shell_timeout_s:120,max_parallel_subagents:3,max_subagents_total:8};
export const RUN_PROFILES=[
 {id:'quick',name:'Quick',description:'Small, focused tasks.',limits:{max_steps:64,max_seconds:1800,shell_timeout_s:120,max_parallel_subagents:1,max_subagents_total:3}},
 {id:'balanced',name:'Balanced',description:'Everyday work with room to delegate.',limits:DEFAULT_RUN_BUDGET},
 {id:'extended',name:'Extended',description:'Larger tasks with more time and capacity.',limits:{max_steps:1024,max_seconds:28800,shell_timeout_s:180,max_parallel_subagents:4,max_subagents_total:16}},
] as const;
const sections=[{id:'agent',label:'Agent',icon:Bot},{id:'tools',label:'Tools',icon:Wrench},{id:'execution',label:'Runtime',icon:Cpu},{id:'voice',label:'Voice',icon:Mic}] as const;
type Section=typeof sections[number]['id'];

export type SessionSettingsProps={open:boolean;onOpenChange:(open:boolean)=>void;session:Session|null;running:boolean;turnsCount:number;ownerId?:string;visible?:boolean;
 runnerOptions:Runner[];runnerAccounts:RunnerAccount[];
 engines:Engine[];providers:Provider[];models:string[];favorites:string[];favoriteKey:string;
 onFavorite:()=>void;onEngine:(engine:string)=>void;
 patch:(changes:Record<string,unknown>)=>Promise<void>;onError:(error:unknown)=>void;onRefresh:()=>void;
 reply:string;onTranscript:(text:string)=>void;onConversationSettings:()=>void};

export default function SessionSettings({open,onOpenChange,session,running,turnsCount,ownerId,visible=true,runnerOptions,runnerAccounts,engines,onEngine,patch,onError,reply,onTranscript,onConversationSettings}:SessionSettingsProps){
 const [section,setSection]=useState<Section>('agent'),[savingProfile,setSavingProfile]=useState<string|null>(null);
 useEffect(()=>{setSection('agent');setSavingProfile(null)},[session?.id,open]);
 const [presets,setPresets]=useState<AgentPreset[]>([]),[presetsLoading,setPresetsLoading]=useState(false);
 useEffect(()=>{setPresets([]);if(!open||!session||!visible)return;setPresetsLoading(true);let cancelled=false;const query=new URLSearchParams({engine:session.engine});if(session.project_id)query.set('project_id',session.project_id);if(session.runner_id)query.set('runner_id',session.runner_id);request<{presets:AgentPreset[]}>('/api/workspace/presets?'+query).then(value=>{if(!cancelled)setPresets(value.presets||[])}).catch(error=>{if(!cancelled)onError(error)}).finally(()=>{if(!cancelled)setPresetsLoading(false)});return()=>{cancelled=true}},[open,session?.id,session?.engine,session?.runner_id,session?.custom_agent_revision,visible]);
 const [extensions,setExtensions]=useState<Extension[]>([]),[extensionsLoading,setExtensionsLoading]=useState(false);
 useEffect(()=>{setExtensions([]);if(!open||!session||!visible||section!=='tools')return;let cancelled=false;setExtensionsLoading(true);request<{extensions:Extension[]}>('/api/workspace/available-extensions'+(session.project_id?'?project_id='+encodeURIComponent(session.project_id):'')).then(value=>{if(!cancelled)setExtensions(value.extensions||[])}).catch(error=>{if(!cancelled)onError(error)}).finally(()=>{if(!cancelled)setExtensionsLoading(false)});return()=>{cancelled=true}},[open,session?.id,session?.project_id,visible,section]);
 if(!open||!session)return null;
 const limits={...DEFAULT_RUN_BUDGET,...session.run_limits};
 const profile=RUN_PROFILES.find(item=>Object.entries(item.limits).every(([key,value])=>limits[key as keyof typeof limits]===value));
 const engineChoices=[{id:'internal',name:'TermX Agent'},...engines.filter(item=>item.id!=='internal')];
 if(!engineChoices.some(item=>item.id===session.engine))engineChoices.push({id:session.engine,name:session.engine});
 const presetLocked=running||(session.engine!=='internal'&&!!turnsCount);
 return <Dialog open onOpenChange={onOpenChange}><DialogContent className="chat-control-dialog session-settings-dialog">
  <DialogTitle>Session settings</DialogTitle><DialogDescription>Agent, tools, and runtime settings for this conversation.</DialogDescription>
  <nav className="session-settings-nav" aria-label="Session settings sections">{sections.map(item=>{const Icon=item.icon;return <button key={item.id} type="button" aria-current={section===item.id?'page':undefined} onClick={()=>setSection(item.id)}><Icon size={15}/>{item.label}</button>})}</nav>
  <div className="session-settings-content">
   {section==='agent'&&<section className="session-settings-pane" aria-label="Agent settings">
    <label>Engine<Select aria-label="Session engine" value={session.engine} disabled={running} onChange={event=>{onOpenChange(false);onEngine(event.target.value)}}>{engineChoices.map(item=><option key={item.id} value={item.id}>{item.name||item.id}</option>)}</Select></label>
    {turnsCount>0&&<p className="control-description">Switching engines creates a linked conversation with the context you choose.</p>}
    <div className="agent-preset-field"><PresetPicker loading={presetsLoading} presets={presets} value={session.custom_agent_id} disabled={presetLocked} onChange={id=>void patch({custom_agent_id:id})}/>
     {session.custom_agent_id&&<Button size="sm" variant="ghost" disabled={presetLocked} onClick={()=>void patch({custom_agent_id:session.custom_agent_id})}>Refresh preset binding</Button>}
     {session.engine!=='internal'&&!!turnsCount&&<p className="control-description">This conversation keeps its current agent preset.</p>}
    </div>
    <fieldset className="run-profile-field"><legend>Run style</legend><p className="control-description">{profile?'Choose how much room the agent has to work.':'Your existing custom limits are preserved. Choose a style to replace them.'}</p>
     <div className="run-profile-options">{RUN_PROFILES.map(item=><label className={'run-profile-option '+(profile?.id===item.id?'selected':'')} key={item.id}><input type="radio" name="run-profile" value={item.id} checked={profile?.id===item.id} disabled={running||!!savingProfile} onChange={async()=>{setSavingProfile(item.id);try{await patch({run_limits:{...item.limits}})}catch(error){onError(error)}finally{setSavingProfile(null)}}}/><span><strong>{item.name}{item.id==='balanced'&&<small>Default</small>}</strong><small>{savingProfile===item.id?'Saving…':item.description}</small></span>{profile?.id===item.id&&<Check size={16}/>}</label>)}</div>
    </fieldset>
   </section>}
   {section==='tools'&&<section className="session-settings-pane" aria-label="Tools and access">
    <h3>Tools and skills</h3><p className="control-description">Choose skills for this conversation. Your account and project permissions still apply.</p>
    {session.engine==='claude'&&<label>Workflow<Select aria-label="Session tool workflow" disabled={running||!!turnsCount} value={session.workflow||'coding'} onChange={event=>void patch({workflow:event.target.value==='browser'?'browser':null})}><option value="coding">Coding tools</option><option value="browser">Browser only</option></Select></label>}
    {session.workflow==='browser'&&<p className="control-description">Hand a tab to this conversation from Browser when you start a task.</p>}
    {extensionsLoading?<p className="control-description" role="status">Loading available skills…</p>:extensions.length?<div className="session-extension-list">{extensions.map(extension=><label className="session-extension" key={extension.id}><input type="checkbox" disabled={running} checked={session.extension_ids?.includes(extension.id)||false} onChange={event=>void patch({extension_ids:event.target.checked?[...(session.extension_ids||[]),extension.id]:(session.extension_ids||[]).filter(id=>id!==extension.id)})}/><span><strong>{extension.id}</strong><small>{extension.description||extension.version}</small></span></label>)}</div>:<p className="control-description">The engine’s default tools are available. Add custom skills in Managers.</p>}
    <SessionAccess session={session} onError={onError}/>
   </section>}
   {section==='execution'&&<section className="session-settings-pane" aria-label="Execution settings">
    <h3>Where your agent runs</h3>
    {session.engine==='internal'?<><label>Location<Select aria-label="Session execution location" value={session.runner_id||''} disabled={running} onChange={event=>{if(!event.target.value){void patch({runner_id:null,runner_credential_ref:null});return}const account=runnerAccounts.find(account=>account.provider_id===session.provider_id&&account.models.includes(session.model||''));if(!account){onError(new Error('Select a configured API provider and an explicit model before choosing a cloud runner.'));return}void patch({runner_id:event.target.value,runner_credential_ref:account.credential_ref})}}><option value="">This machine</option>{runnerOptions.filter(runner=>runner.status==='ready'&&runner.project===(session.project_id||'')).map(runner=><option key={runner.id} value={runner.id}>{runner.configuration.name||runner.configuration.image||runner.id}</option>)}</Select></label><p className="control-description runtime-location">{session.runner_id?(runnerOptions.find(row=>row.id===session.runner_id)?.configuration.root||'/workspace'):session.cwd||'Current project folder'}</p>{!runnerOptions.length&&<p className="control-description">Connect a machine in Runners to work remotely.</p>}</>:<p className="control-description">{engineChoices.find(item=>item.id===session.engine)?.name||session.engine} runs on this machine using its connected account.</p>}
   </section>}
   {section==='voice'&&<section className="session-settings-pane" aria-label="Voice settings"><h3>Voice</h3><VoiceControls key={session.id} sessionId={session.id} projectId={session.project_id||null} ownerId={ownerId} visible={visible&&open} reply={reply} onTranscript={onTranscript}/></section>}
  </div>
  {running&&<p className="control-running-note" role="status">The current task keeps its settings. Make changes after it finishes.</p>}
  <button className="conversation-settings-link" type="button" onClick={onConversationSettings}><Settings2 size={15}/><span>Conversation settings<small>Rename or move to another sidebar group</small></span><ChevronRight size={15}/></button>
 </DialogContent></Dialog>;
}
