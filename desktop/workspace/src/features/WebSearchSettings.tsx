import {useCallback, useEffect, useRef, useState} from 'react';
import {Button} from '../components/ui/button';
import {FormField} from '../components/ui/form-field';
import {Input} from '../components/ui/input';
import {useDirtyForm} from './settings-forms';
import {gql} from './use-manager';
import './provider-anthropic.css';

export interface WebSearchStatus {
  available: boolean;
  provider: string;
  /** `environment` (set on the host), `credential_store` (saved here) or null (no key). The key itself never arrives. */
  source: string | null;
  store_available: boolean;
  custom_endpoint: boolean;
  error: string | null;
}

const FIELDS = 'available provider source store_available custom_endpoint error';
const STATUS_QUERY = `{ web_search_status { ${FIELDS} } }`;
const SET_KEY = `mutation($key: String!) { set_web_search_key(key: $key) { ${FIELDS} } }`;
const CLEAR_KEY = `mutation { clear_web_search_key { ${FIELDS} } }`;

const PROVIDERS: Record<string, string> = {brave: 'Brave Search'};
const providerName = (id: string) => PROVIDERS[id] ?? id;

export const WEB_SEARCH_ADMIN_REASON = 'Only a host administrator can change web search. Ask the host owner to set it up.';

function keyText(status: WebSearchStatus) {
  if (status.source === 'environment') return 'Set on the host with TERMX_AI_WEB_SEARCH_API_KEY';
  if (status.source === 'credential_store') return 'Saved in this computer’s credential store';
  return 'No key yet';
}

function KeyForm({status, onChanged}: {status: WebSearchStatus; onChanged: (next: WebSearchStatus) => void}) {
  const {formProps, markClean} = useDirtyForm('web-search-key');
  const saved = status.source === 'credential_store';
  const [replacing, setReplacing] = useState(!saved);
  const [key, setKey] = useState(''), [show, setShow] = useState(false);
  const [problem, setProblem] = useState(''), [error, setError] = useState(''), [busy, setBusy] = useState(false);
  const flight = useRef(false);
  const provider = providerName(status.provider);

  async function save() {
    if (flight.current) return;
    if (!key.trim()) {
      setProblem(saved ? 'Enter the new key, or choose Keep saved key.' : `Enter your ${provider} API key. Create one in the ${provider} dashboard.`);
      return;
    }
    if (/\s/.test(key.trim())) {
      setProblem('The key cannot contain spaces or line breaks. Paste it again without them.');
      return;
    }
    flight.current = true;
    setBusy(true);
    setProblem('');
    setError('');
    try {
      const result = await gql<{set_web_search_key: WebSearchStatus}>(SET_KEY, {key: key.trim()});
      setKey('');
      setShow(false);
      setReplacing(false);
      markClean();
      onChanged(result.set_web_search_key);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'The host could not save the key. Check that credential storage is unlocked and try again.');
    } finally {
      flight.current = false;
      setBusy(false);
    }
  }

  async function remove() {
    if (flight.current) return;
    flight.current = true;
    setBusy(true);
    setError('');
    try {
      const result = await gql<{clear_web_search_key: WebSearchStatus}>(CLEAR_KEY);
      markClean();
      onChanged(result.clear_web_search_key);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'The host could not remove the key. Try again.');
    } finally {
      flight.current = false;
      setBusy(false);
    }
  }

  return (
    <form
      {...formProps}
      noValidate
      className="manager-form"
      aria-label="Web search key"
      aria-busy={busy}
      onSubmit={event => {
        event.preventDefault();
        void save();
      }}
    >
      {saved && !replacing ? (
        <div className="form-field">
          <span className="form-field-label" id="web-search-key-label">{provider} API key</span>
          <div className="key-saved" role="group" aria-labelledby="web-search-key-label">
            <span className="key-saved-state">Key saved</span>
            <Button type="button" variant="secondary" disabled={busy} onClick={() => setReplacing(true)}>Replace key</Button>
            <Button type="button" variant="secondary" disabled={busy} onClick={() => void remove()}>Remove key</Button>
          </div>
          <p className="form-field-hint">The host keeps the key and never shows it again.</p>
        </div>
      ) : (
        <FormField
          label={saved ? `New ${provider} API key` : `${provider} API key`}
          error={problem}
          hint="Stored in this computer’s credential store, never in chat history."
        >
          {control => (
            <span className="secret-input">
              <Input
                {...control}
                name="web_search_key"
                type={show ? 'text' : 'password'}
                autoComplete="new-password"
                value={key}
                disabled={busy}
                onChange={event => {
                  setKey(event.target.value);
                  setProblem('');
                }}
              />
              <Button type="button" variant="ghost" size="sm" onClick={() => setShow(value => !value)}>{show ? 'Hide key' : 'Show key'}</Button>
              {saved && (
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  onClick={() => {
                    setReplacing(false);
                    setKey('');
                    setProblem('');
                  }}
                >
                  Keep saved key
                </Button>
              )}
            </span>
          )}
        </FormField>
      )}
      {error && <p role="alert" className="inline-error">{error}</p>}
    </form>
  );
}

/**
 * Web search for agents: whether the host offers the tool and the API key it uses. `admin` is the host-admin
 * permission; without it nothing is read or changed. The key never comes back from the host.
 */
export default function WebSearchSettings({admin}: {admin: boolean}) {
  const [status, setStatus] = useState<WebSearchStatus | null>(null), [error, setError] = useState('');
  const load = useCallback(async () => {
    try {
      setStatus((await gql<{web_search_status: WebSearchStatus}>(STATUS_QUERY)).web_search_status);
      setError('');
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Web search status is unavailable. Check the connection and try again.');
    }
  }, []);
  useEffect(() => {
    if (admin) void load();
  }, [admin, load]);

  if (!admin) return <p className="manager-help" role="status">{WEB_SEARCH_ADMIN_REASON}</p>;
  return (
    <section className="settings-form-section" aria-label="Web search">
      <p className="manager-help">Agents ask before they search the web or open a website. You approve each website once and the host remembers it. Search sends the query to {status ? providerName(status.provider) : 'the search provider'}.</p>
      {error && (
        <p role="alert" className="inline-error">
          {error} <Button type="button" variant="ghost" size="sm" onClick={() => void load()}>Retry</Button>
        </p>
      )}
      {!status && !error && <p role="status" className="manager-help">Loading web search status…</p>}
      {status && (
        <>
          <dl className="settings-facts" aria-label="Web search status">
            <dt>Status</dt>
            <dd>{status.available ? 'Available to agents' : 'Not available. Add a key to turn it on'}</dd>
            <dt>Provider</dt>
            <dd>{providerName(status.provider)}{status.custom_endpoint ? ' (custom address set on the host)' : ''}</dd>
            <dt>Key</dt>
            <dd>{keyText(status)}</dd>
          </dl>
          {status.error && <p role="alert" className="inline-error">{status.error}</p>}
          {status.source === 'environment' ? (
            <p className="settings-reason">The key comes from the host environment. Unset TERMX_AI_WEB_SEARCH_API_KEY on the host and restart it to change the key here.</p>
          ) : !status.store_available ? (
            <p className="settings-reason">Secure credential storage is unavailable on this computer. Set TERMX_AI_WEB_SEARCH_API_KEY on the host instead.</p>
          ) : (
            <KeyForm key={status.source ?? 'none'} status={status} onChanged={setStatus} />
          )}
        </>
      )}
    </section>
  );
}
