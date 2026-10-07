import { useRef, useState } from 'react';
import { Button } from '../components/ui/button';
import { Input } from '../components/ui/input';
import McpPresetBindings,{type McpBinding} from './McpPresetBindings';

type Preset = Record<string, any>;
const budgets = [
  ['max_steps', 'Maximum steps', 256, 100000],
  ['max_seconds', 'Maximum run seconds', 14400, 604800],
  ['shell_timeout_s', 'Shell timeout seconds', 120, 86400],
  ['max_parallel_subagents', 'Parallel subagents', 3, 8],
  ['max_subagents_total', 'Total subagents', 8, 32],
] as const;
const split = (value: string) => [...new Set(value.split(/[\n,]/).map(item => item.trim()).filter(Boolean))];
export function AgentPresetEditor({ preset, onSave, onCancel }: {
  preset?: Preset; onSave: (input: Preset) => Promise<unknown>; onCancel?: () => void;
}) {
  const [original] = useState(preset);
  const [draft, setDraft] = useState<Preset>({ name:preset?.name || '', description:preset?.description || '',
    instructions:preset?.instructions || '', engine:preset?.engine || 'inherit', model:preset?.model || '',
    tools:(preset?.tools || []).join('\n'), toolsets:(preset?.toolsets || []).join('\n'),
    deny_tools:(preset?.deny_tools || []).join('\n'), approval_mode:preset?.approval_mode || 'standard',
    sandbox_profile:preset?.sandbox_profile || 'agent', enabled:preset?.enabled ?? true,
    mcp_connections:structuredClone(preset?.mcp_connections||[]),
    allTools:preset ? preset.tools_mode === 'all' : false,
    ...Object.fromEntries(budgets.map(([key,,fallback]) => [key,preset?.limits?.[key] ?? fallback])) });
  const [busy, setBusy] = useState(false), [error, setError] = useState('');
  const pending = useRef(false);
  const change = (key:string, value:unknown) => setDraft(prior=>({...prior,[key]:value}));
  const valid = draft.name.trim() && budgets.every(([key,,,max])=>Number.isInteger(Number(draft[key])) && Number(draft[key])>=1 && Number(draft[key])<=max);
  async function save(event:React.FormEvent) {
    event.preventDefault(); if(pending.current || !valid)return;
    pending.current=true;setBusy(true);setError('');
    try {
      const input:Preset={name:draft.name.trim(),description:draft.description,instructions:draft.instructions,
        engine:draft.engine.trim() || 'inherit',model:draft.model.trim(),
        mcp_connections:draft.mcp_connections,toolsets:split(draft.toolsets),deny_tools:split(draft.deny_tools),
        limits:Object.fromEntries(budgets.map(([key])=>[key,Number(draft[key])])),
        approval_mode:draft.approval_mode,sandbox_profile:draft.sandbox_profile,enabled:draft.enabled};
      if(!draft.allTools)input.tools=split(draft.tools);
      if(original) {
        if(!original.file_revision)throw new Error('This preset has no editable file revision. Refresh its source before editing');
        input.revision=original.file_revision;
        input.tools_mode=draft.allTools?'all':'explicit';
      }
      const result=await onSave(input);
      if(result===null)setError('Preset could not be saved. Your draft is kept; inspect the host error above. For a revision conflict, cancel and reopen the current version.');
    } catch(exception){setError(`${exception instanceof Error?exception.message:String(exception)}. Your draft is kept.`)}
    finally {pending.current=false;setBusy(false)}
  }
  const field=(key:string,label:string,multiline=false)=><label className="manager-field"><span>{label}</span>{multiline?<textarea aria-label={label} value={draft[key]} disabled={busy} onChange={event=>change(key,event.target.value)}/>:<Input aria-label={label} value={draft[key]} required={key==='name'} disabled={busy} onChange={event=>change(key,event.target.value)}/>}</label>;
  return <form className="manager-form" aria-label={preset?'Edit agent preset':'Create agent preset'} onSubmit={save}>
    <p className="manager-help">Presets configure future runs. Saving never retargets a running task or grants tool authority. {original && `Source revision: ${original.file_revision || 'unavailable'}.`}</p>
    {field('name','Name')}{field('description','Purpose')}{field('instructions','Instructions',true)}{field('engine','Default engine for future sessions')}{field('model','Default model for future sessions')}
    <fieldset className="provider-capabilities"><legend>Tools</legend><label><input type="checkbox" checked={draft.allTools} disabled={busy} onChange={event=>change('allTools',event.target.checked)}/>Inherit all host-authorized tools</label>
      <p className="manager-help">Inherited tools may include future host-authorized tools. An explicit empty list permits no tools. Host policy and denied tool IDs still apply.</p>
      {!draft.allTools && field('tools','Allowed tool IDs (one per line)',true)}{field('toolsets','Toolset IDs (one per line)',true)}{field('deny_tools','Denied tool IDs (one per line)',true)}
    </fieldset>
    <McpPresetBindings value={draft.mcp_connections as McpBinding[]} disabled={busy} onChange={bindings=>{change('mcp_connections',bindings);if(bindings.length&&!draft.allTools)change('tools',[...new Set([...split(draft.tools),'mcp_catalog','mcp_call'])].join('\n'))}}/>
    <fieldset className="provider-capabilities"><legend>Run budget</legend>{budgets.map(([key,label,,max])=><label key={key} className="manager-field"><span>{label}</span><Input type="number" min={1} max={max} step={1} required disabled={busy} value={draft[key]} onChange={event=>change(key,event.target.value)}/></label>)}</fieldset>
    <label className="manager-field"><span>Approval preference</span><select disabled={busy} value={draft.approval_mode} onChange={event=>change('approval_mode',event.target.value)}><option value="standard">Ask when required</option><option value="remember">Use remembered consent</option><option value="autonomous">Autonomous within host policy</option></select></label>
    <label className="manager-field"><span>Sandbox preference</span><select disabled={busy} value={draft.sandbox_profile} onChange={event=>change('sandbox_profile',event.target.value)}><option value="agent">Agent</option><option value="workspace">Workspace</option><option value="host">Host</option></select></label>
    <label className="manager-choice"><input type="checkbox" checked={draft.enabled} disabled={busy} onChange={event=>change('enabled',event.target.checked)}/>Available for future sessions</label>
    {error && <p role="alert" className="manager-error">{error}</p>}
    <div className="manager-item-actions">{onCancel && <Button type="button" variant="secondary" disabled={busy} onClick={onCancel}>Cancel preset edit</Button>}<Button type="submit" disabled={busy || !valid}>{busy?'Saving preset…':preset?'Save preset changes':'Save preset'}</Button></div>
  </form>;
}
