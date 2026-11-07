import {useRef,useState} from 'react';
import {Button} from '../components/ui/button';
import {FormField} from '../components/ui/form-field';
import {Input} from '../components/ui/input';
import {Select} from '../components/ui/select';
import {Textarea} from '../components/ui/textarea';
import McpPresetBindings,{type McpBinding} from './McpPresetBindings';
import type {AgentPreset,AgentPresetInput} from '../lib/host-types';
import {useDirtyForm} from './settings-forms';

const budgets = [
  ['max_steps', 'Maximum steps', 256, 100000],
  ['max_seconds', 'Maximum run seconds', 14400, 604800],
  ['shell_timeout_s', 'Shell timeout seconds', 120, 86400],
  ['max_parallel_subagents', 'Parallel subagents', 3, 8],
  ['max_subagents_total', 'Total subagents', 8, 32],
] as const;
type BudgetKey = typeof budgets[number][0];
const toolChoices = [['read_file','Read project files'],['search_files','Search project files'],['write_file','Write project files'],['run_shell','Run shell commands'],['mcp_catalog','Inspect connected tools'],['mcp_call','Use connected tools']] as const;
/** Lists are held as raw lines while typing, so a trailing newline survives; `clean` normalises them on save. */
interface PresetDraft {
  name: string; description: string; instructions: string; engine: string; model: string;
  tools: string[]; toolsets: string[]; deny_tools: string[]; limits: Record<BudgetKey, number | null>;
  approval_mode: string; sandbox_profile: string; enabled: boolean; mcp_connections: McpBinding[]; allTools: boolean;
}
const clean = (lines: string[]) => [...new Set(lines.map(item => item.trim()).filter(Boolean))];
const budgetError = (value: number | null, max: number) => value !== null && Number.isInteger(value) && value >= 1 && value <= max ? '' : `Enter a whole number from 1 to ${max.toLocaleString('en-US')}.`;

function TextField({label, value, onChange, error, multiline = false, required = false, disabled}: {label: string; value: string; onChange: (value: string) => void; error?: string; multiline?: boolean; required?: boolean; disabled: boolean}) {
  return <FormField label={label} error={error}>{control => multiline
    ? <Textarea {...control} value={value} disabled={disabled} onChange={event => onChange(event.target.value)}/>
    : <Input {...control} aria-required={required || undefined} value={value} disabled={disabled} onChange={event => onChange(event.target.value)}/>}</FormField>;
}
function ListField({label, value, onChange, disabled}: {label: string; value: string[]; onChange: (value: string[]) => void; disabled: boolean}) {
  return <TextField multiline label={label} value={value.join('\n')} disabled={disabled} onChange={text => onChange(text.split('\n'))}/>;
}

export function AgentPresetEditor({ preset, onSave, onCancel, engines=[] }: {
  engines?: {id: string; label: string}[];
  preset?: AgentPreset; onSave: (input: AgentPresetInput) => Promise<unknown>; onCancel?: () => void;
}) {
  const {formProps, markClean} = useDirtyForm('agent-preset');
  const [original] = useState(preset);
  const [draft, setDraft] = useState<PresetDraft>(() => ({
    name: preset?.name || '', description: preset?.description || '', instructions: preset?.instructions || '', engine: preset?.engine || 'inherit', model: preset?.model || '',
    tools: [...(preset?.tools || [])], toolsets: [...(preset?.toolsets || [])], deny_tools: [...(preset?.deny_tools || [])],
    approval_mode: preset?.approval_mode || 'standard', sandbox_profile: preset?.sandbox_profile || 'agent', enabled: preset?.enabled ?? true,
    mcp_connections: structuredClone(preset?.mcp_connections || []) as McpBinding[], allTools: preset ? preset.tools_mode === 'all' : false,
    limits: Object.fromEntries(budgets.map(([key,,fallback]) => [key, preset?.limits?.[key] ?? fallback])) as Record<BudgetKey, number | null>,
  }));
  const [busy, setBusy] = useState(false), [error, setError] = useState(''), [attempted, setAttempted] = useState(false), [touched, setTouched] = useState<ReadonlySet<BudgetKey>>(new Set());
  const pending = useRef(false);
  const change = <K extends keyof PresetDraft>(key: K, value: PresetDraft[K]) => setDraft(prior => ({...prior, [key]: value}));
  const setLimit = (key: BudgetKey, value: number | null) => { setDraft(prior => ({...prior, limits: {...prior.limits, [key]: value}})); setTouched(prior => new Set(prior).add(key)) };
  const nameError = draft.name.trim() ? '' : 'Enter a name for this preset.';
  const limitErrors = Object.fromEntries(budgets.map(([key,,,max]) => [key, budgetError(draft.limits[key], max)])) as Record<BudgetKey, string>;
  const valid = !nameError && budgets.every(([key]) => !limitErrors[key]);
  async function save(event: React.FormEvent) {
    event.preventDefault(); setAttempted(true);
    if (pending.current || !valid) return;
    pending.current = true; setBusy(true); setError('');
    try {
      const input: AgentPresetInput = {name: draft.name.trim(), description: draft.description, instructions: draft.instructions,
        engine: draft.engine.trim() || 'inherit', model: draft.model.trim(),
        mcp_connections: draft.mcp_connections, toolsets: clean(draft.toolsets), deny_tools: clean(draft.deny_tools),
        limits: Object.fromEntries(budgets.map(([key]) => [key, draft.limits[key] as number])),
        approval_mode: draft.approval_mode, sandbox_profile: draft.sandbox_profile, enabled: draft.enabled};
      if (!draft.allTools) input.tools = clean(draft.tools);
      if (original) {
        if (!original.file_revision) throw new Error('This preset has no editable file revision. Refresh its source before editing');
        input.revision = original.file_revision;
        input.tools_mode = draft.allTools ? 'all' : 'explicit';
      }
      const result = await onSave(input);
      if (result === null) setError('Preset could not be saved. Your draft is kept; inspect the host error above. For a revision conflict, cancel and reopen the current version.');
      else markClean();
    } catch (exception) { setError(`${exception instanceof Error ? exception.message : String(exception)}. Your draft is kept.`) }
    finally { pending.current = false; setBusy(false) }
  }
  const toggleTool = (id: string, on: boolean) => change('tools', on ? [...clean(draft.tools), id] : clean(draft.tools).filter(value => value !== id));
  return <form {...formProps} noValidate className="manager-form" aria-label={preset ? 'Edit agent preset' : 'Create agent preset'} onSubmit={save}>
    <p className="manager-help">Presets configure future runs. Saving never retargets a running task or grants tool authority. {original && `Source revision: ${original.file_revision || 'unavailable'}.`}</p>
    <TextField label="Name" required value={draft.name} disabled={busy} error={attempted ? nameError : ''} onChange={value => change('name', value)}/>
    <TextField label="Purpose" value={draft.description} disabled={busy} onChange={value => change('description', value)}/>
    <TextField multiline label="Instructions" value={draft.instructions} disabled={busy} onChange={value => change('instructions', value)}/>
    <FormField label="Default engine for future sessions">{control => <Select {...control} value={draft.engine} disabled={busy} onChange={event => change('engine', event.target.value)}><option value="inherit">Use the session’s engine</option>{engines.map(engine => <option key={engine.id} value={engine.id}>{engine.label}</option>)}{draft.engine !== 'inherit' && !engines.some(engine => engine.id === draft.engine) && <option value={draft.engine}>Current configured engine · {draft.engine}</option>}</Select>}</FormField>
    <details><summary>Advanced · default model name</summary><TextField label="Default model for future sessions" value={draft.model} disabled={busy} onChange={value => change('model', value)}/></details>
    <fieldset className="provider-capabilities"><legend>Tools</legend><label><input type="checkbox" checked={draft.allTools} disabled={busy} onChange={event => change('allTools', event.target.checked)}/>Inherit all host-authorized tools</label>
      <p className="manager-help">Inherited tools may include future host-authorized tools. An explicit empty list permits no tools. Host policy and denied tool IDs still apply.</p>
      {!draft.allTools && <div className="settings-tool-choices">{toolChoices.map(([id, label]) => <label key={id}><input type="checkbox" checked={clean(draft.tools).includes(id)} disabled={busy} onChange={event => toggleTool(id, event.target.checked)}/>{label}</label>)}</div>}
      <details><summary>Advanced · exact tool names and toolsets</summary>{!draft.allTools && <ListField label="Allowed tool IDs (one per line)" value={draft.tools} disabled={busy} onChange={value => change('tools', value)}/>}<ListField label="Toolset IDs (one per line)" value={draft.toolsets} disabled={busy} onChange={value => change('toolsets', value)}/><ListField label="Denied tool IDs (one per line)" value={draft.deny_tools} disabled={busy} onChange={value => change('deny_tools', value)}/></details>
    </fieldset>
    <McpPresetBindings value={draft.mcp_connections} disabled={busy} onChange={bindings => { change('mcp_connections', bindings); if (bindings.length && !draft.allTools) change('tools', clean([...draft.tools, 'mcp_catalog', 'mcp_call'])) }}/>
    <fieldset className="provider-capabilities"><legend>Run budget</legend>{budgets.map(([key, label,, max]) => <FormField key={key} label={label} error={(attempted || touched.has(key)) ? limitErrors[key] : ''}>{control => <Input {...control} type="number" inputMode="numeric" step={1} aria-required="true" disabled={busy} value={draft.limits[key] ?? ''} onChange={event => setLimit(key, event.target.value === '' ? null : Number(event.target.value))}/>}</FormField>)}</fieldset>
    <FormField label="Approval preference">{control => <Select {...control} disabled={busy} value={draft.approval_mode} onChange={event => change('approval_mode', event.target.value)}><option value="standard">Ask when required</option><option value="remember">Use remembered consent</option><option value="autonomous">Autonomous within host policy</option></Select>}</FormField>
    <FormField label="Sandbox preference">{control => <Select {...control} disabled={busy} value={draft.sandbox_profile} onChange={event => change('sandbox_profile', event.target.value)}><option value="agent">Agent</option><option value="workspace">Workspace</option><option value="host">Host</option></Select>}</FormField>
    <label className="manager-choice"><input type="checkbox" checked={draft.enabled} disabled={busy} onChange={event => change('enabled', event.target.checked)}/>Available for future sessions</label>
    {error && <p role="alert" className="manager-error">{error}</p>}
    <div className="manager-item-actions">{onCancel && <Button type="button" variant="secondary" disabled={busy} onClick={onCancel}>Cancel preset edit</Button>}<Button type="submit" disabled={busy}>{busy ? 'Saving preset…' : preset ? 'Save preset changes' : 'Save preset'}</Button></div>
  </form>;
}
