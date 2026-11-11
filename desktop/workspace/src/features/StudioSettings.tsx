import {useCallback, useEffect, useId, useRef, useState, type KeyboardEvent} from 'react';
import {Check, KeyRound, Plus, RefreshCw} from 'lucide-react';
import {Button} from '../components/ui/button';
import {FormField} from '../components/ui/form-field';
import {Input} from '../components/ui/input';
import {Select} from '../components/ui/select';
import {ApiError} from '../lib/api';
import type {AgentProvider, ProviderAccountInput} from '../lib/host-types';
import type {StudioCatalog, StudioOperation} from '../lib/media-studio';
import {
  isStudioProvider, loadStudioProviders, loadStudioSettings, saveStudioProvider, saveStudioSettings,
  studioDefaultModels, studioLimitFields, studioModelEnabled, studioOperations, studioSettingsErrors,
  type StudioDefault, type StudioLimitKey, type StudioSettings as Settings, type StudioSettingsEnvelope,
} from '../lib/studio-settings';
import './studio-settings.css';

type Tab = 'providers' | 'models' | 'limits';
const tabs: {id: Tab; label: string}[] = [{id: 'providers', label: 'Providers'}, {id: 'models', label: 'Models & defaults'}, {id: 'limits', label: 'Generation limits'}];
const providerChoices = [
  {id: 'openrouter', label: 'OpenRouter', url: 'https://openrouter.ai/api/v1'},
  {id: 'kie', label: 'Kie.ai', url: 'https://api.kie.ai'},
  {id: 'elevenlabs', label: 'ElevenLabs Music', url: 'https://api.elevenlabs.io'},
];
const providerMedia = (id: string) => id === 'elevenlabs' ? ['audio'] : id === 'openrouter' ? ['image', 'video', 'audio'] : ['image', 'video'];
const messageOf = (reason: unknown) => reason instanceof Error ? reason.message : 'The host could not complete the request. Try again.';
const copy = <T,>(value: T): T => structuredClone(value);

/** Studio edits only the connection and media declarations; shared chat model configuration is preserved. */
function StudioProviderEditor({account, disabled, onSave, onCancel}: {
  account?: AgentProvider; disabled: boolean; onSave: (input: ProviderAccountInput) => Promise<void>; onCancel: () => void;
}) {
  const [provider, setProvider] = useState(account?.base_url?.includes('api.elevenlabs.io') ? 'elevenlabs' : account?.base_url?.includes('api.kie.ai') ? 'kie' : 'openrouter');
  const [name, setName] = useState(account?.name || ''), [secret, setSecret] = useState(''), [showSecret, setShowSecret] = useState(false);
  const [media, setMedia] = useState<string[]>(account ? (account.capabilities || []).filter(item => providerMedia(provider).includes(item)) : ['image', 'video']);
  const [error, setError] = useState(''), [busy, setBusy] = useState(false);
  const generatedId = useRef(''), pending = useRef(false), mounted = useRef(true);
  useEffect(() => {mounted.current = true; return () => {mounted.current = false;};}, []);
  const choice = providerChoices.find(item => item.id === provider)!;
  const managedCapabilities = providerMedia(provider);
  async function save() {
    if (disabled || pending.current) return;
    if (!name.trim()) {setError('Enter a display name.'); return;}
    if (!account && !secret.trim()) {setError('Enter an API key to connect this provider.'); return;}
    if (!media.length) {setError('Allow at least one supported media capability.'); return;}
    if (!generatedId.current) generatedId.current = `${provider}-${crypto.randomUUID().slice(0, 8)}`;
    const input: ProviderAccountInput = {
      id: account?.id || generatedId.current, name: name.trim(), kind: account?.kind || 'openai-compatible',
      base_url: account?.base_url || choice.url, model: account?.model || '', api_key: secret || null,
      capabilities: [...(account?.capabilities || []).filter(item => !managedCapabilities.includes(item)), ...media],
      ...(!account ? {wire: 'responses' as const} : account.wire === 'chat' || account.wire === 'responses' ? {wire: account.wire} : {}),
    };
    if (account?.model_overrides) input.model_meta = copy(account.model_overrides);
    pending.current = true; setBusy(true); setError('');
    try {await onSave(input); if (mounted.current) {setSecret(''); setShowSecret(false);}}
    catch (reason) {if (mounted.current) setError(`${messageOf(reason)} Your entries are still here for retry.`);}
    finally {pending.current = false; if (mounted.current) setBusy(false);}
  }
  return <form className="studio-settings-provider-editor" aria-label={account ? 'Edit Studio provider' : 'Add Studio provider'} aria-busy={busy} noValidate onSubmit={event => {event.preventDefault(); void save();}}>
    <div className="studio-settings-section-heading"><h3>{account ? `Edit ${account.name || account.id}` : 'Connect a provider'}</h3><p>Your host stores the API key in the existing provider credential store.</p></div>
    <div className="studio-settings-grid">
      {!account && <FormField label="Provider">{control => <Select {...control} value={provider} disabled={disabled || busy} onChange={event => {setProvider(event.target.value); setSecret(''); setMedia(event.target.value === 'elevenlabs' ? ['audio'] : ['image', 'video']);}}>{providerChoices.map(item => <option key={item.id} value={item.id}>{item.label}</option>)}</Select>}</FormField>}
      <FormField label="Display name" required>{control => <Input {...control} value={name} disabled={disabled || busy} placeholder={`${choice.label} Studio`} onChange={event => setName(event.target.value)}/>}</FormField>
      <FormField label={account ? 'Replace API key' : 'API key'} hint={account ? 'Leave blank to keep the saved key.' : 'Use a key from your provider account.'}>{control => <span className="studio-settings-provider-secret"><Input {...control} type={showSecret ? 'text' : 'password'} autoComplete="new-password" spellCheck={false} value={secret} disabled={disabled || busy} onChange={event => setSecret(event.target.value)}/><Button type="button" variant="ghost" size="sm" disabled={disabled || busy} onClick={() => setShowSecret(value => !value)}>{showSecret ? 'Hide key' : 'Show key'}</Button></span>}</FormField>
    </div>
    <p className="studio-settings-help">Requests go to <strong>{account?.base_url || choice.url}</strong>.</p>
    <fieldset className="studio-settings-field"><legend>Media capabilities</legend>{providerMedia(provider).map(id => [id, id === 'audio' ? 'Allow music generation' : `Allow ${id} generation`]).map(([id, label]) => <label key={id}><input type="checkbox" checked={media.includes(id)} disabled={disabled || busy} onChange={event => setMedia(prior => event.target.checked ? [...prior, id] : prior.filter(item => item !== id))}/>{label}</label>)}</fieldset>
    <p className="studio-settings-help">Shared with Agent.</p>
    {error && <p className="studio-settings-feedback" data-tone="error" role="alert">{error}</p>}
    <div className="studio-settings-actions"><Button type="submit" disabled={disabled || busy}>{busy ? 'Saving provider…' : account ? 'Save provider changes' : 'Connect provider'}</Button><Button type="button" variant="secondary" disabled={busy} onClick={onCancel}>Cancel</Button></div>
  </form>;
}

function Defaults({catalog, settings, disabled, onChange}: {catalog: StudioCatalog; settings: Settings; disabled: boolean; onChange: (operation: StudioOperation, value?: StudioDefault) => void}) {
  return <div className="studio-settings-defaults">{studioOperations.map(({id, label}) => {
    const value = settings.defaults[id], account = catalog.accounts.find(item => item.id === value?.provider_id);
    const accounts = catalog.accounts.filter(item => item.configured && studioDefaultModels(catalog, settings, id, item).length);
    const models = studioDefaultModels(catalog, settings, id, account);
    const model = catalog.models.find(item => item.id === value?.model && item.adapter === account?.adapter);
    return <fieldset className="studio-settings-default" key={id} disabled={disabled}><legend>{label}</legend><div className="studio-settings-grid">
      <FormField label={`${label} default provider`}>{control => <Select {...control} value={value?.provider_id || ''} onChange={event => {
        const selected = catalog.accounts.find(item => item.id === event.target.value);
        const first = selected && studioDefaultModels(catalog, settings, id, selected)[0];
        onChange(id, selected && first ? {provider_id: selected.id, model: first.id} : undefined);
      }}><option value="">Automatic</option>{value && !accounts.some(item => item.id === value.provider_id) && <option value={value.provider_id}>Unavailable provider</option>}{accounts.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</Select>}</FormField>
      {value && <FormField label={`${label} default model`}>{control => <Select {...control} value={value.model} onChange={event => onChange(id, {provider_id: value.provider_id, model: event.target.value})}>{!models.some(item => item.id === value.model) && <option value={value.model}>Unavailable model · {value.model}</option>}{models.map(item => <option key={item.adapter + ':' + item.id} value={item.id}>{item.label}</option>)}</Select>}</FormField>}
      {value && model && model.aspect_ratios.length > 0 && <FormField label={`${label} aspect ratio`}>{control => <Select {...control} value={value.aspect_ratio || ''} onChange={event => {const next = {...value}; if (event.target.value) next.aspect_ratio = event.target.value; else delete next.aspect_ratio; onChange(id, next);}}><option value="">Automatic</option>{model.aspect_ratios.map(item => <option key={item}>{item}</option>)}</Select>}</FormField>}
      {value && model && model.resolutions.length > 0 && <FormField label={`${label} resolution`}>{control => <Select {...control} value={value.resolution || ''} onChange={event => {const next = {...value}; if (event.target.value) next.resolution = event.target.value; else delete next.resolution; onChange(id, next);}}><option value="">Automatic</option>{model.resolutions.map(item => <option key={item}>{item}</option>)}</Select>}</FormField>}
      {value && model && id === 'music' && <FormField label={model.duration_mode === 'prompt' ? 'Music duration target' : 'Music duration'}>{control => <Input {...control} type="number" min={model.duration_min || 3} max={model.duration_max || 600} step="1" value={value.duration ?? ''} placeholder="Automatic" onChange={event => {const next = {...value}; if (event.target.value) next.duration = Number(event.target.value); else delete next.duration; onChange(id, next);}}/>}</FormField>}
      {value && model && id !== 'music' && model.durations.length > 0 && <FormField label={`${label} duration`}>{control => <Select {...control} value={value.duration ?? ''} onChange={event => {const next = {...value}; if (event.target.value) next.duration = Number(event.target.value); else delete next.duration; onChange(id, next);}}><option value="">Automatic</option>{model.durations.map(item => <option key={item} value={item}>{item} seconds</option>)}</Select>}</FormField>}
    </div>{!accounts.length && <p className="studio-settings-help">No connected provider has an enabled model for {label.toLowerCase()}.</p>}</fieldset>;
  })}</div>;
}

export type StudioSettingsProps = {ownerId?: string; visible: boolean; observing?: boolean; locked?: boolean; onSaved?: () => void; onProvidersChanged?: () => void};
export default function StudioSettings({ownerId, visible, observing = true, locked = false, onSaved, onProvidersChanged}: StudioSettingsProps) {
  const tabId = useId();
  const [tab, setTab] = useState<Tab>('providers'), [envelope, setEnvelope] = useState<StudioSettingsEnvelope | null>(null), [draft, setDraft] = useState<Settings | null>(null);
  const [providers, setProviders] = useState<AgentProvider[]>([]), [editor, setEditor] = useState<AgentProvider | 'new' | null>(null);
  const [loading, setLoading] = useState(true), [saving, setSaving] = useState(false), [providerSaving, setProviderSaving] = useState(false);
  const [loadError, setLoadError] = useState(''), [providerError, setProviderError] = useState(''), [saveError, setSaveError] = useState(''), [notice, setNotice] = useState('');
  const [denied, setDenied] = useState(false), [conflict, setConflict] = useState(false), [search, setSearch] = useState(''), [version, setVersion] = useState(0);
  const [sessionLocked, setSessionLocked] = useState(false), [pageVisible, setPageVisible] = useState(document.visibilityState !== 'hidden');
  const currentDraft = useRef(draft), currentEnvelope = useRef(envelope), dirty = useRef(false), discardOnReload = useRef(false);
  const pendingSave = useRef(false), pendingProvider = useRef(false), lifetime = useRef(0), writeController = useRef<AbortController | null>(null);
  const active = visible && observing && pageVisible && !locked && !sessionLocked;
  const canWrite = active && !denied && !!envelope && !loading && !saving && !providerSaving;
  const writable = useRef(canWrite); writable.current = canWrite;
  currentDraft.current = draft; currentEnvelope.current = envelope;
  dirty.current = !!draft && !!envelope && JSON.stringify(draft) !== JSON.stringify(envelope.settings);

  const clearSensitive = useCallback(() => {lifetime.current++; writable.current = false; writeController.current?.abort(); setEditor(null); setProviderSaving(false); setSaving(false); pendingSave.current = false; pendingProvider.current = false;}, []);
  useEffect(() => {
    const pause = () => {setSessionLocked(true); clearSensitive();};
    const unlock = () => {setSessionLocked(false); setVersion(value => value + 1);};
    const visibility = () => setPageVisible(document.visibilityState !== 'hidden');
    window.addEventListener('termx-locked', pause); window.addEventListener('termx-session-locked', pause); window.addEventListener('termx-signed-out', pause); window.addEventListener('termx-session-unlocked', unlock);
    document.addEventListener('visibilitychange', visibility);
    return () => {lifetime.current++; writeController.current?.abort(); window.removeEventListener('termx-locked', pause); window.removeEventListener('termx-session-locked', pause); window.removeEventListener('termx-signed-out', pause); window.removeEventListener('termx-session-unlocked', unlock); document.removeEventListener('visibilitychange', visibility);};
  }, [clearSensitive]);
  useEffect(() => {if (locked) clearSensitive();}, [locked, clearSensitive]);
  useEffect(() => {
    clearSensitive(); setEnvelope(null); setDraft(null); setProviders([]); setLoadError(''); setProviderError(''); setSaveError(''); setNotice(''); setDenied(false); setConflict(false); setSearch(''); setTab('providers');
    currentDraft.current = null; currentEnvelope.current = null; dirty.current = false; discardOnReload.current = false;
  }, [ownerId, clearSensitive]);
  useEffect(() => {
    if (!active) return;
    const controller = new AbortController(), epoch = lifetime.current;
    setLoading(true); setLoadError(''); setProviderError('');
    void Promise.allSettled([loadStudioSettings(controller.signal), loadStudioProviders(controller.signal)]).then(([settingsResult, providerResult]) => {
      if (controller.signal.aborted || lifetime.current !== epoch) return;
      if (settingsResult.status === 'fulfilled') {
        const next = settingsResult.value;
        if (!currentDraft.current || !dirty.current || discardOnReload.current) {setDraft(copy(next.settings)); setConflict(false); setSaveError('');}
        setEnvelope(next); setDenied(false); discardOnReload.current = false;
      } else {setLoadError(messageOf(settingsResult.reason)); if (settingsResult.reason instanceof ApiError && settingsResult.reason.status === 403) {setDenied(true); setEditor(null);}}
      if (providerResult.status === 'fulfilled') setProviders(providerResult.value);
      else setProviderError(messageOf(providerResult.reason));
      setLoading(false);
    });
    return () => controller.abort();
  }, [ownerId, active, version]);

  function change(next: (prior: Settings) => Settings) {if (!canWrite) return; setDraft(prior => prior && next(prior)); setNotice(''); setSaveError('');}
  async function save() {
    if (!writable.current || !draft || !envelope || pendingSave.current || conflict) return;
    const errors = studioSettingsErrors(draft, envelope.catalog);
    if (errors.length) {setSaveError(errors.join(' ')); return;}
    pendingSave.current = true; setSaving(true); setSaveError(''); setNotice('');
    const epoch = lifetime.current, controller = new AbortController(); writeController.current = controller;
    try {
      const next = await saveStudioSettings(copy(draft), controller.signal);
      if (lifetime.current !== epoch || controller.signal.aborted) return;
      setEnvelope(next); setDraft(copy(next.settings)); setConflict(false); setNotice('Studio settings saved.'); onSaved?.();
    } catch (reason) {
      if (lifetime.current !== epoch || controller.signal.aborted) return;
      if (reason instanceof ApiError && reason.status === 409) {setConflict(true); setSaveError('These settings changed in another window. Your draft is still here. Reload saved settings to review the latest version.');}
      else {setSaveError(messageOf(reason) + ' Your draft is still here.'); if (reason instanceof ApiError && reason.status === 403) setDenied(true);}
    } finally {if (lifetime.current === epoch) {pendingSave.current = false; setSaving(false); writeController.current = null;}}
  }
  async function saveProvider(input: ProviderAccountInput) {
    if (!writable.current || pendingProvider.current) throw Error('Studio settings are paused. Return to this view to save.');
    const epoch = lifetime.current; pendingProvider.current = true; setProviderSaving(true); setNotice('');
    try {
      await saveStudioProvider(input);
      if (lifetime.current !== epoch) return;
      setEditor(null); setNotice('Provider saved. The connection is shared with Agent.'); setVersion(value => value + 1); onProvidersChanged?.();
    } finally {if (lifetime.current === epoch) {pendingProvider.current = false; setProviderSaving(false);}}
  }
  function reload() {discardOnReload.current = true; setVersion(value => value + 1);}
  function tabKey(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    let next = index;
    if (event.key === 'ArrowRight') next = (index + 1) % tabs.length;
    else if (event.key === 'ArrowLeft') next = (index + tabs.length - 1) % tabs.length;
    else if (event.key === 'Home') next = 0;
    else if (event.key === 'End') next = tabs.length - 1;
    else return;
    event.preventDefault(); setTab(tabs[next].id); document.getElementById(`${tabId}-${tabs[next].id}`)?.focus({preventScroll: true});
  }
  const catalog = envelope?.catalog, compatibleProviders = providers.filter(isStudioProvider);
  const shownModels = (catalog?.models || []).filter(model => `${model.label} ${model.id} ${model.adapter}`.toLowerCase().includes(search.trim().toLowerCase()));
  const selectedCount = draft && catalog ? catalog.models.filter(model => studioModelEnabled(draft, model)).length : 0;
  const disabled = !canWrite;
  const lockedNow = locked || sessionLocked;
  return <section className="studio-settings" aria-label="Studio settings" hidden={!visible} inert={!visible || lockedNow ? true : undefined}>
    <header><h1>Studio settings</h1><p>Connect providers, choose models and set optional generation limits.</p></header>
    {lockedNow ? <p className="studio-settings-empty">Studio settings locked. Unlock the workspace to continue.</p> : <>
      <div className="studio-settings-tabs" role="tablist" aria-label="Studio settings sections">{tabs.map((item, index) => <button key={item.id} type="button" role="tab" id={`${tabId}-${item.id}`} aria-controls={`${tabId}-${item.id}-panel`} aria-selected={tab === item.id} tabIndex={tab === item.id ? 0 : -1} onKeyDown={event => tabKey(event, index)} onFocus={event => event.currentTarget.scrollIntoView?.({block: 'nearest', inline: 'nearest', behavior: 'instant'})} onClick={event => {setTab(item.id); event.currentTarget.scrollIntoView?.({block: 'nearest', inline: 'nearest', behavior: 'instant'});}}>{item.label}</button>)}</div>
      <div className="studio-settings-content">
        {loadError && <div className="studio-settings-feedback" data-tone="error" role="alert"><p>{denied ? 'You need permission to manage AI settings on this host. Your draft is read-only.' : 'Studio settings could not be loaded.'} {loadError}</p><Button type="button" variant="secondary" disabled={!active || loading} onClick={() => setVersion(value => value + 1)}>Retry loading settings</Button></div>}
        {loading && !envelope && <p className="studio-settings-loading" role="status">Loading Studio settings…</p>}
        {notice && <p className="studio-settings-feedback" data-tone="success" role="status"><Check size={14} aria-hidden="true"/>{notice}</p>}
        {envelope && draft && <>
          <section role="tabpanel" id={`${tabId}-providers-panel`} aria-labelledby={`${tabId}-providers`} hidden={tab !== 'providers'} className="studio-settings-section">
            <div className="studio-settings-section-heading"><div><h2>Provider connections</h2><p>Studio uses your saved OpenRouter, Kie.ai and ElevenLabs API accounts. Connections are shared with Agent.</p></div><Button type="button" variant="secondary" disabled={disabled || !!editor} onClick={() => {setEditor('new'); setNotice('');}}><Plus size={14} aria-hidden="true"/>Add provider</Button></div>
            {providerError && <p className="studio-settings-feedback" data-tone="error" role="alert">Provider accounts could not be loaded. {providerError} <Button type="button" variant="ghost" disabled={!active || loading} onClick={() => setVersion(value => value + 1)}>Retry providers</Button></p>}
            {!compatibleProviders.length && !providerError && !loading && <p className="studio-settings-empty">Connect your first provider to make compatible models available in Studio.</p>}
            <div className="studio-settings-providers" role="group" aria-label="Connected Studio providers">{compatibleProviders.map(provider => <div className="studio-settings-provider" key={provider.id}><span className="studio-settings-provider-mark"><KeyRound size={17} aria-hidden="true"/></span><div className="studio-settings-provider-copy"><strong>{provider.name || provider.id}</strong><small>{provider.base_url?.includes('api.elevenlabs.io') ? 'ElevenLabs API' : provider.base_url?.includes('api.kie.ai') ? 'Kie.ai' : 'OpenRouter'} · Shared with Agent</small></div><span className="studio-settings-provider-status">{provider.secret_configured ? 'Key saved' : 'Key needed'}</span><Button type="button" variant="ghost" size="sm" disabled={disabled || !!editor} onClick={() => setEditor(provider)} aria-label={`Edit ${provider.name || provider.id}`}>Edit</Button></div>)}</div>
            {editor && <StudioProviderEditor key={editor === 'new' ? 'new' : editor.id} account={editor === 'new' ? undefined : editor} disabled={disabled} onSave={saveProvider} onCancel={() => setEditor(null)}/>}
            <p className="studio-settings-help">Other Agent providers remain available in Agent settings. Studio only lists adapters that support its generation workflow.</p>
          </section>
          <section role="tabpanel" id={`${tabId}-models-panel`} aria-labelledby={`${tabId}-models`} hidden={tab !== 'models'} className="studio-settings-section">
            <div className="studio-settings-section-heading"><h2>Available models</h2><p>Choose which discovered models Studio can use. This does not change Agent model choices.</p></div>
            {catalog && (catalog.catalog_source === 'snapshot' || catalog.catalog_error) && (catalog.models.length ? <div className="studio-settings-catalog-note"><p><strong>{catalog.catalog_source === 'snapshot' ? 'Saved model catalog.' : 'Model catalog.'}</strong> Live model availability could not be verified.</p>{catalog.catalog_error && <details><summary>Availability details</summary><p>{catalog.catalog_error}</p></details>}</div> : <p className="studio-settings-feedback" data-tone="error" role="alert">The model catalog is unavailable. {catalog.catalog_error || 'Live model availability could not be verified.'}</p>)}
            <label className="studio-settings-model-policy"><span><strong>Automatically enable new models</strong><small>New models become available when the host discovers them.</small></span><input type="checkbox" aria-label="Automatically enable new models" checked={draft.enabled_models === null} disabled={disabled} onChange={event => change(prior => ({...prior, enabled_models: event.target.checked ? null : (catalog?.models || []).map(model => model.id)}))}/></label>
            <div className="studio-settings-model-search"><Input type="search" aria-label="Search Studio models" placeholder="Search models" value={search} onChange={event => setSearch(event.target.value)}/><small className="studio-settings-model-meta">{selectedCount} of {catalog?.models.length || 0} enabled</small></div>
            <div className="studio-settings-model-list">{shownModels.map(model => <label key={model.adapter + ':' + model.id}><input type="checkbox" aria-label={`Enable ${model.label}`} checked={studioModelEnabled(draft, model)} disabled={disabled} onChange={event => change(prior => {
              const ids = prior.enabled_models === null ? (catalog?.models || []).map(item => item.id) : prior.enabled_models;
              return {...prior, enabled_models: event.target.checked ? [...new Set([...ids, model.id])] : ids.filter(id => id !== model.id)};
            })}/><span><strong>{model.label}</strong><small>{model.adapter === 'elevenlabs' ? 'ElevenLabs API' : model.adapter === 'kie' ? 'Kie.ai' : 'OpenRouter'} · {model.modes.filter(mode => mode !== 'cinema').join(', ')} · {model.id}</small></span></label>)}</div>
            {!shownModels.length && <p className="studio-settings-empty">{search ? 'No models match your search.' : 'No supported models were returned by the host.'}</p>}
            {selectedCount === 0 && <p className="studio-settings-help">No models are enabled. Generation will be unavailable until you enable a model.</p>}
            <div className="studio-settings-section-heading studio-settings-defaults-heading"><h2>Generation defaults</h2><p>Start each creation mode with these choices. Automatic lets Studio choose a compatible option.</p></div>
            {catalog && <Defaults catalog={catalog} settings={draft} disabled={disabled} onChange={(operation, value) => change(prior => {const defaults = {...prior.defaults}; if (value) defaults[operation] = value; else delete defaults[operation]; return {...prior, defaults};})}/>}
          </section>
          <section role="tabpanel" id={`${tabId}-limits-panel`} aria-labelledby={`${tabId}-limits`} hidden={tab !== 'limits'} className="studio-settings-section">
            <div className="studio-settings-section-heading"><h2>Generation limits</h2><p>Optional local limits for your Studio jobs across projects on this host. Off means no local cap.</p></div>
            <div className="studio-settings-usage" role="group" aria-label="Current Studio usage"><span><strong>{envelope.usage.daily_generations}</strong><small>accepted today</small></span><span><strong>{envelope.usage.active_generations}</strong><small>active generations</small></span></div>
            <div className="studio-settings-limit-list" role="group" aria-label="Studio generation limits">{studioLimitFields.map(field => <div className="studio-settings-limit" key={field.id}><label htmlFor={`${tabId}-${field.id}-enabled`}><strong>{field.label}</strong><small>{field.hint}</small></label><div className="studio-settings-limit-control">{draft.limits[field.id] != null && <Input type="number" min="0" step="1" inputMode="numeric" aria-label={`${field.label} limit`} aria-invalid={!Number.isSafeInteger(draft.limits[field.id]) || (draft.limits[field.id] ?? 0) < 0 || undefined} disabled={disabled} value={Number.isFinite(draft.limits[field.id]) ? draft.limits[field.id]! : ''} onChange={event => change(prior => ({...prior, limits: {...prior.limits, [field.id]: event.target.value === '' ? NaN : Number(event.target.value)}}))}/>}<input id={`${tabId}-${field.id}-enabled`} type="checkbox" aria-label={`Limit ${field.label.toLowerCase()}`} checked={draft.limits[field.id] != null} disabled={disabled} onChange={event => change(prior => ({...prior, limits: {...prior.limits, [field.id]: event.target.checked ? ({daily_generations: 20, concurrent_generations: 2, max_video_seconds: 10, max_music_seconds: 180} as Record<StudioLimitKey, number>)[field.id] : null}}))}/></div></div>)}</div>
            <p className="studio-settings-help">Set a limit to 0 to block that category.</p>
            <details className="studio-settings-detail"><summary>How Studio limits are counted</summary><p>These caps apply to your Studio Create, Cinema, Lip sync, Remix, Factory and Music jobs across all projects on this host. Agent generation and requests made directly to a provider are outside these caps.</p><p>The daily count includes accepted jobs even if they fail or their result is unknown, and resets at midnight UTC{envelope.usage.reset_at ? ` (next reset: ${new Date(envelope.usage.reset_at * 1000).toLocaleString(undefined, {timeZone: 'UTC', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit'})} UTC)` : ''}. Unknown jobs count as active. A video with an unknown duration cannot start while a duration cap is enabled.</p></details>
          </section>
          {tab !== 'providers' && <footer className="studio-settings-footer"><div>{saveError && <p className="studio-settings-feedback" data-tone="error" role="alert">{saveError}</p>}{denied && !loadError && <p className="studio-settings-feedback" data-tone="error" role="alert">Your draft is read-only. Permission to manage AI settings is required.</p>}<span className="studio-settings-muted">{dirty.current ? 'Unsaved changes' : 'Changes apply to Studio generation.'}</span></div><div className="studio-settings-actions">{(conflict || saveError) && <Button type="button" variant="secondary" disabled={!active || loading || saving} onClick={reload}><RefreshCw size={14} aria-hidden="true"/>Reload saved settings</Button>}<Button type="button" disabled={disabled || !dirty.current || conflict} onClick={() => void save()}>{saving ? 'Saving…' : 'Save Studio settings'}</Button></div></footer>}
        </>}
      </div>
    </>}
  </section>;
}
