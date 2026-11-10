import {useState} from 'react';
import {Select} from '../components/ui/select';
import {SettingsRow,SettingsSection} from '../components/ui/settings-section';
import {formatPrice,formatTokens,modelInfo,modelLabel,providerModels} from '../lib/model-info';
import type {AgentProvider} from '../lib/host-types';
import {useDirtyForm} from './settings-forms';
import './model-routing.css';

export interface RouteTarget { provider_id: string; model: string }
export interface Route extends RouteTarget { fallbacks: RouteTarget[] }
export type Routing = Record<string, Route | undefined>;

const roles = [
 {id:'orchestrator',label:'Orchestrator',description:'Coordinates the work and hands tasks to workers.'},
 {id:'worker',label:'Worker',description:'Does the delegated tasks in sub-agents.'},
 {id:'planner',label:'Planner',description:'Writes the plan when a task starts in Plan mode.'},
 {id:'summariser',label:'Summariser',description:'Condenses the conversation when the context window fills up.'},
] as const;

interface Pick { provider_id: string; model: string; fallback: string }
const encode = (target?: RouteTarget) => target ? JSON.stringify([target.provider_id, target.model]) : '';
const decode = (value: string): RouteTarget => { const [provider_id, model] = JSON.parse(value) as [string, string]; return {provider_id, model} };

function RoleRow({role, pick, extraFallbacks, providers, disabled, onChange}: {role: typeof roles[number]; pick: Pick; extraFallbacks: number; providers: AgentProvider[]; disabled: boolean; onChange: (next: Pick) => void}) {
 const account = providers.find(row => row.id === pick.provider_id);
 const models = account ? providerModels(account) : [];
 if (pick.model && !models.includes(pick.model)) models.push(pick.model);
 const choices: {value: string; label: string; group: string}[] = [];
 for (const row of providers) for (const id of providerModels(row)) {
  if (row.id === pick.provider_id && id === pick.model) continue;
  choices.push({value: encode({provider_id: row.id, model: id}), label: modelLabel(id, modelInfo(row, id)), group: row.name || row.id});
 }
 if (pick.fallback && !choices.some(choice => choice.value === pick.fallback)) {
  const target = decode(pick.fallback), known = providers.find(row => row.id === target.provider_id);
  choices.push({value: pick.fallback, label: `${target.model || 'Provider default'} (no longer available)`, group: known?.name || `Unavailable account (${target.provider_id})`});
 }
 const groups = [...new Set(choices.map(choice => choice.group))];
 const info = account && pick.model ? modelInfo(account, pick.model) : null;
 return <SettingsRow label={role.label} description={role.description}>
  <div className="model-routing-controls">
   <label className="model-routing-field">Provider
    <Select aria-label={`${role.label} provider`} name={`${role.id}.provider`} value={pick.provider_id} disabled={disabled} onChange={event => onChange({provider_id: event.target.value, model: '', fallback: event.target.value ? pick.fallback : ''})}>
     <option value="">Session default</option>
     {pick.provider_id && !account && <option value={pick.provider_id}>Unavailable account ({pick.provider_id})</option>}
     {providers.map(row => <option key={row.id} value={row.id}>{row.name || row.id}</option>)}
    </Select>
   </label>
   <label className="model-routing-field">Model
    <Select aria-label={`${role.label} model`} name={`${role.id}.model`} value={pick.model} disabled={disabled || !pick.provider_id} onChange={event => onChange({...pick, model: event.target.value, fallback: pick.fallback === encode({provider_id: pick.provider_id, model: event.target.value}) ? '' : pick.fallback})}>
     <option value="">{pick.provider_id ? 'Provider default' : 'Session default'}</option>
     {models.map(id => <option key={id} value={id}>{id}</option>)}
    </Select>
   </label>
   <label className="model-routing-field">Fallback
    <Select aria-label={`${role.label} fallback`} name={`${role.id}.fallback`} value={pick.fallback} disabled={disabled || !pick.provider_id} onChange={event => onChange({...pick, fallback: event.target.value})}>
     <option value="">No fallback</option>
     {groups.map(group => <optgroup key={group} label={group}>{choices.filter(choice => choice.group === group).map(choice => <option key={choice.value} value={choice.value}>{choice.label}</option>)}</optgroup>)}
    </Select>
   </label>
   {info && <p className="model-routing-meta">Context window {formatTokens(info.context_window)} · Input {formatPrice(info.price_in)} · Output {formatPrice(info.price_out)}</p>}
   {pick.fallback && extraFallbacks > 0 && <p className="model-routing-meta">{extraFallbacks === 1 ? '1 more fallback' : `${extraFallbacks} more fallbacks`} set through the API {extraFallbacks === 1 ? 'is' : 'are'} kept when you save.</p>}
  </div>
 </SettingsRow>;
}

const startingPicks = (routing: Routing): Record<string, Pick> => Object.fromEntries(roles.map(({id}) => {
 const route = routing[id];
 return [id, {provider_id: route?.provider_id ?? '', model: route?.model ?? '', fallback: encode(route?.fallbacks?.[0])}];
}));

/**
 * Which model each agent role uses, and the model to try first if that one fails. A role left on
 * "Session default" has no route, so it is left out of what is saved. Further fallbacks set through
 * the API are kept as they are.
 */
export default function ModelRoutingForm({providers, routing, disabled, onSave}: {providers: AgentProvider[]; routing: Routing; disabled: boolean; onSave: (routing: Record<string, Route>) => Promise<unknown>}) {
 const {dirty, formProps, markClean} = useDirtyForm('model-routing');
 const [picks, setPicks] = useState(() => startingPicks(routing));
 // Take in a newer routing from the host (another admin, a Refresh) unless the person has unsaved picks.
 const routingKey = JSON.stringify(routing), [seen, setSeen] = useState(routingKey);
 if (seen !== routingKey && !dirty) { setSeen(routingKey); setPicks(startingPicks(routing)) }
 const hasModels = providers.some(row => providerModels(row).length > 0);
 const extraFallbacks = (id: string) => Math.max((routing[id]?.fallbacks?.length ?? 0) - 1, 0);
 async function save() {
  const next: Record<string, Route> = {};
  for (const {id} of roles) {
   const pick = picks[id];
   if (!pick.provider_id) continue;
   const kept = pick.fallback ? (routing[id]?.fallbacks ?? []).slice(1) : [];
   next[id] = {provider_id: pick.provider_id, model: pick.model, fallbacks: pick.fallback ? [decode(pick.fallback), ...kept] : []};
  }
  // A failed save returns null. The picks stay as they are, so the footer Save can retry.
  if (await onSave(next) !== null) markClean();
 }
 return <form {...formProps} noValidate className={hasModels ? 'model-routing' : 'model-routing model-routing-empty'} aria-label="Model routing" onSubmit={event => { event.preventDefault(); void save() }}>
  <SettingsSection level={3} title="Model routing" description={hasModels
   ? 'Choose the model each part of the agent uses. A role left on Session default uses the model of the chat it runs in. The fallback is tried if the first model fails.'
   : disabled
    ? 'No models are listed yet. Ask the host owner to add a provider account and choose Refresh models on it.'
    : 'No models are listed yet. Add a provider account above, then choose Refresh models on it to load what it offers.'}>
   {hasModels && roles.map(role => <RoleRow key={role.id} role={role} pick={picks[role.id]} extraFallbacks={extraFallbacks(role.id)} providers={providers} disabled={disabled} onChange={next => setPicks(prior => ({...prior, [role.id]: next}))}/>)}
  </SettingsSection>
 </form>;
}
