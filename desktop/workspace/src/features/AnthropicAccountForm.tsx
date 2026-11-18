import {useRef, useState} from 'react';
import {Check} from 'lucide-react';
import {Alert} from '../components/ui/alert';
import {Button} from '../components/ui/button';
import {FormField} from '../components/ui/form-field';
import {Input} from '../components/ui/input';
import {Select} from '../components/ui/select';
import {catalogAge} from '../lib/catalog-age';
import type {ModelInfo, ModelOverride, ModelOverrides, ProviderAccount, ProviderAccountInput} from '../lib/host-types';
import {ANTHROPIC_MODELS, ANTHROPIC_URL, PROVIDER_CHOICES} from '../lib/provider-kinds';
import ProviderModelCatalog from './ProviderModelCatalog';
import {useDirtyForm} from './settings-forms';
import './provider-anthropic.css';

/** Anthropic accounts power chats and agents. Media, voice and realtime need an OpenAI-style account. */
const CAPABILITY_CHOICES = [['shell', 'Shell tools'], ['functions', 'Function tools']];
const DEFAULT_CAPABILITIES = ['shell', 'functions'];

const modelList = (value: string) => [...new Set(value.split(/[\n,]/).map(item => item.trim()).filter(Boolean))];
const validUrl = (value: string) => {
  try {
    return ['http:', 'https:'].includes(new URL(value).protocol);
  } catch {
    return false;
  }
};

type Errors = {name?: string; api_key?: string; base_url?: string; capabilities?: string; max_output_tokens?: string; request_timeout_s?: string};

export interface AnthropicAccountFormProps {
  account?: ProviderAccount;
  /** Carried over when the person switches provider type while adding an account. */
  start?: {name: string};
  busy: boolean;
  onSave: (input: ProviderAccountInput) => Promise<unknown>;
  onRefresh?: () => Promise<unknown>;
  onCancel?: () => void;
  /** Adding only: the person chose another provider type. */
  onKindChange?: (provider: string, name: string) => void;
}

export default function AnthropicAccountForm({account, start, busy, onSave, onRefresh, onCancel, onKindChange}: AnthropicAccountFormProps) {
  const {formProps, markClean} = useDirtyForm('provider-account');
  const keySaved = account?.secret_configured === true;
  const [id, setId] = useState(account?.id || ''), [name, setName] = useState(account?.name || start?.name || '');
  const [url, setUrl] = useState(account?.base_url && account.base_url !== ANTHROPIC_URL ? account.base_url : '');
  const [secret, setSecret] = useState(''), [showSecret, setShowSecret] = useState(false), [replacing, setReplacing] = useState(!keySaved);
  const [included, setIncluded] = useState(() => {
    const saved = modelList(account?.model || '');
    return saved.length ? saved : [...ANTHROPIC_MODELS];
  });
  const [overrides, setOverrides] = useState<ModelOverrides>(account?.model_overrides || {});
  const [capabilities, setCapabilities] = useState<string[]>(account?.capabilities?.length ? account.capabilities : DEFAULT_CAPABILITIES);
  const [maxOutput, setMaxOutput] = useState(String(account?.limits?.max_output_tokens ?? ''));
  const [requestTimeout, setRequestTimeout] = useState(String(account?.limits?.request_timeout_s ?? ''));
  const [saving, setSaving] = useState(false), [error, setError] = useState(''), [errors, setErrors] = useState<Errors>({});
  const pending = useRef(false), generatedId = useRef('');
  const disabled = busy || saving;
  const discovered = account?.model_catalog || {};
  const hasCatalog = Object.keys(discovered).length > 0;
  // Until discovery has run, the seeded models are all there is to show, and they are not "missing" from a list.
  const shown: Record<string, ModelInfo> = hasCatalog ? discovered : Object.fromEntries(included.map(model => [model, {}]));
  const clearError = (field: keyof Errors) => setErrors(prior => ({...prior, [field]: undefined}));

  function toggleModel(model: string, checked: boolean) {
    setIncluded(prior => {
      if (checked) return prior.includes(model) ? prior : [...prior, model];
      return prior.length > 1 ? prior.filter(item => item !== model) : prior;
    });
  }

  function chooseDefault(model: string) {
    setIncluded(prior => [model, ...prior.filter(item => item !== model)]);
  }

  function override(model: string, field: keyof ModelOverride, value: number | undefined) {
    setOverrides(prior => {
      const row = {...prior[model]};
      if (value === undefined || Number.isNaN(value)) delete row[field];
      else row[field] = value;
      const next = {...prior};
      if (Object.keys(row).length) next[model] = row;
      else delete next[model];
      return next;
    });
  }

  function validate() {
    const found: Errors = {};
    if (!name.trim()) found.name = 'Enter a display name.';
    if (replacing && !secret.trim()) {
      found.api_key = keySaved
        ? 'Enter the new key, or choose Keep saved key.'
        : 'Enter your Anthropic API key. Create one in the Anthropic Console under API keys.';
    } else if (/\s/.test(secret.trim())) {
      found.api_key = 'The key cannot contain spaces or line breaks. Paste it again without them.';
    }
    if (url.trim() && !validUrl(url.trim())) found.base_url = 'Enter a full address that starts with https:// or http://, or leave it blank.';
    if (!capabilities.some(item => CAPABILITY_CHOICES.some(([value]) => value === item))) found.capabilities = 'Declare at least one capability.';
    if (maxOutput && (!Number.isSafeInteger(Number(maxOutput)) || Number(maxOutput) <= 0)) found.max_output_tokens = 'Enter a positive whole number or leave blank.';
    if (requestTimeout && (!Number.isFinite(Number(requestTimeout)) || Number(requestTimeout) <= 0)) found.request_timeout_s = 'Enter a positive number or leave blank.';
    setErrors(found);
    return !Object.keys(found).length;
  }

  async function save() {
    if (pending.current || !validate()) return;
    pending.current = true;
    setSaving(true);
    setError('');
    if (!generatedId.current) generatedId.current = `anthropic-${crypto.randomUUID().slice(0, 8)}`;
    const input: ProviderAccountInput = {
      id: id.trim() || generatedId.current,
      name: name.trim(),
      kind: 'anthropic',
      base_url: url.trim() || ANTHROPIC_URL,
      model: included.join('\n'),
      capabilities,
      api_key: replacing ? secret.trim() : null,
      wire: 'messages',
      limits: {max_output_tokens: maxOutput ? Number(maxOutput) : null, request_timeout_s: requestTimeout ? Number(requestTimeout) : null},
    };
    if (Object.keys(overrides).length || Object.keys(account?.model_overrides || {}).length) input.model_meta = overrides;
    try {
      const result = await onSave(input);
      // A failed save returns null. Keep what was typed, including the key, so it can be retried.
      if (result === null) {
        setError('The host did not accept the account, so nothing changed. What you entered is still here. The reason is shown above.');
        return;
      }
      setSecret('');
      setShowSecret(false);
      markClean();
      if (account) setReplacing(false);
      else {
        generatedId.current = '';
        setId('');
        setName('');
        setUrl('');
        setMaxOutput('');
        setRequestTimeout('');
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'The account could not be saved. Review the settings and try again.');
    } finally {
      pending.current = false;
      setSaving(false);
    }
  }

  const headingId = `${account?.id || 'new'}-anthropic-models`;
  return (
    <form
      {...formProps}
      noValidate
      className="manager-form provider-account-form anthropic-account-form"
      aria-label={account ? 'Edit provider account' : 'Add provider account'}
      aria-busy={disabled}
      onSubmit={event => {
        event.preventDefault();
        void save();
      }}
    >
      {!account && (
        <>
          <p className="manager-help">Connect an API account for chats and agents. Already use a signed-in coding agent? Choose it under Installed engines instead.</p>
          <FormField label="Provider">
            {control => (
              <Select {...control} name="provider" value="anthropic" disabled={disabled} onChange={event => onKindChange?.(event.target.value, name)}>
                {PROVIDER_CHOICES.map(row => <option key={row.id} value={row.id}>{row.label}</option>)}
              </Select>
            )}
          </FormField>
        </>
      )}
      <FormField label="Display name" error={errors.name}>
        {control => (
          <Input
            {...control}
            name="name"
            aria-required="true"
            value={name}
            placeholder="Anthropic (Claude)"
            disabled={disabled}
            onChange={event => {
              setName(event.target.value);
              clearError('name');
            }}
          />
        )}
      </FormField>

      {keySaved && !replacing ? (
        <div className="form-field">
          <span className="form-field-label" id="anthropic-key-label">API key</span>
          <div className="key-saved" role="group" aria-labelledby="anthropic-key-label">
            <span className="key-saved-state"><Check size={16} strokeWidth={1.5} aria-hidden="true" />Key saved</span>
            <Button type="button" variant="secondary" disabled={disabled} onClick={() => setReplacing(true)}>Replace key</Button>
          </div>
          <p className="form-field-hint">The host keeps the key and never shows it again.</p>
        </div>
      ) : (
        <FormField
          label={keySaved ? 'New API key' : 'API key'}
          error={errors.api_key}
          hint="Stored in this computer's credential store, never in chat history. Anthropic keys start with sk-ant-."
        >
          {control => (
            <span className="secret-input">
              <Input
                {...control}
                name="api_key"
                type={showSecret ? 'text' : 'password'}
                autoComplete="new-password"
                value={secret}
                disabled={disabled}
                onChange={event => {
                  setSecret(event.target.value);
                  clearError('api_key');
                }}
              />
              <Button type="button" variant="ghost" size="sm" onClick={() => setShowSecret(value => !value)}>{showSecret ? 'Hide key' : 'Show key'}</Button>
              {keySaved && (
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  onClick={() => {
                    setReplacing(false);
                    setSecret('');
                    clearError('api_key');
                  }}
                >
                  Keep saved key
                </Button>
              )}
            </span>
          )}
        </FormField>
      )}

      <section className="provider-models" aria-labelledby={headingId}>
        <div className="provider-models-header">
          <h4 id={headingId}>Models</h4>
          {account && onRefresh && keySaved && (
            <Button type="button" variant="secondary" data-keeps-draft disabled={disabled} onClick={() => void onRefresh()}>Refresh models</Button>
          )}
        </div>
        {account ? (
          <>
            <Alert>{catalogAge(account.catalog_refreshed_at)}</Alert>
            {account.catalog_error && (
              <Alert role="alert">
                {hasCatalog ? 'The last refresh failed, so the previous catalog is shown. ' : 'The last refresh failed. '}
                {account.catalog_error}
              </Alert>
            )}
          </>
        ) : (
          <p className="manager-help">These Claude models are offered until you refresh. After you save, Refresh models lists everything your key can use.</p>
        )}
        <FormField label="Default model" hint="Chats start on this model. Choose another in the chat's model menu.">
          {control => (
            <Select {...control} name="default_model" value={included[0]} disabled={disabled} onChange={event => chooseDefault(event.target.value)}>
              {included.map(model => <option key={model} value={model}>{model}</option>)}
            </Select>
          )}
        </FormField>
        <p className="manager-help">Anthropic's model list has no prices, so cost shows as unpriced. Enter a price under Override values to track it.</p>
        <ProviderModelCatalog
          catalog={shown}
          included={included}
          overrides={overrides}
          disabled={disabled}
          unpriced
          defaultModel={included[0]}
          onInclude={toggleModel}
          onOverride={override}
        />
      </section>

      <details className="provider-limits">
        <summary>Limits</summary>
        <p className="manager-help">Leave blank for no limit.</p>
        <div className="provider-limit-fields">
          <FormField label="Maximum output tokens" error={errors.max_output_tokens}>
            {control => (
              <Input
                {...control}
                name="max_output_tokens"
                type="number"
                min="1"
                step="1"
                value={maxOutput}
                disabled={disabled}
                onChange={event => {
                  setMaxOutput(event.target.value);
                  clearError('max_output_tokens');
                }}
              />
            )}
          </FormField>
          <FormField label="Request timeout (seconds)" error={errors.request_timeout_s}>
            {control => (
              <Input
                {...control}
                name="request_timeout_s"
                type="number"
                min="0"
                step="any"
                value={requestTimeout}
                disabled={disabled}
                onChange={event => {
                  setRequestTimeout(event.target.value);
                  clearError('request_timeout_s');
                }}
              />
            )}
          </FormField>
        </div>
      </details>

      <details>
        <summary>Advanced account options</summary>
        <FormField
          label="API address (optional)"
          hint={`Leave blank to use ${ANTHROPIC_URL}. Set it only for a gateway. An address ending in /v1 is cleaned up for you.`}
          error={errors.base_url}
        >
          {control => (
            <Input
              {...control}
              name="base_url"
              inputMode="url"
              value={url}
              disabled={disabled}
              onChange={event => {
                setUrl(event.target.value);
                clearError('base_url');
              }}
            />
          )}
        </FormField>
        <FormField label="Internal account key" hint="Generated automatically.">
          {control => <Input {...control} name="id" value={id} disabled={disabled || !!account} onChange={event => setId(event.target.value)} />}
        </FormField>
        <fieldset className="provider-capabilities">
          <legend>Declared account capabilities</legend>
          {CAPABILITY_CHOICES.map(([value, label]) => (
            <label key={value}>
              <span>
                <input
                  type="checkbox"
                  name="capabilities"
                  value={value}
                  checked={capabilities.includes(value)}
                  disabled={disabled}
                  onChange={event => {
                    setCapabilities(prior => (event.target.checked ? [...prior, value] : prior.filter(item => item !== value)));
                    clearError('capabilities');
                  }}
                />
                {label}
              </span>
            </label>
          ))}
          {errors.capabilities && <p className="form-field-error" role="alert">{errors.capabilities}</p>}
        </fieldset>
        <p className="manager-help">Requests use the Messages API with adaptive thinking. The request format is fixed for Anthropic accounts.</p>
      </details>

      {error && <p role="alert" className="inline-error">{error}</p>}
      <div className="manager-item-actions">
        <Button type="submit" disabled={disabled}>{saving ? 'Saving account…' : account ? 'Save account changes' : 'Save account'}</Button>
        {onCancel && <Button type="button" variant="secondary" disabled={disabled} onClick={onCancel}>Cancel account edit</Button>}
      </div>
    </form>
  );
}
