import {useCallback, useEffect, useRef, useState} from 'react';
import {Button} from '../components/ui/button';
import {gql} from './use-manager';
import {useDirtyForm} from './settings-forms';
import './provider-anthropic.css';

export type HolderMode = 'auto' | 'held' | 'direct';

export interface HostStatus {
  version: string;
  pid: number;
  uptime_s: number;
  service: {backend: string; supported: boolean; installed: boolean; managed: boolean; state: string};
  holder: {
    mode: 'held' | 'direct'; requested: string; setting: string; env_override: boolean; reason: string | null; running: boolean; pid: number | null;
    sessions: number; draining: boolean; direct_terminals: number;
  };
  keep_awake: {enabled: boolean; active: boolean; supported: boolean; backend: string | null; reasons: string[]; error: string | null};
  heartbeat: {at: number | null; age_s: number | null; interval_s: number; file: string; error: string | null};
}

const FIELDS = `version pid uptime_s
  service { backend supported installed managed state }
  holder { mode requested setting env_override reason running pid sessions draining direct_terminals }
  keep_awake { enabled active supported backend reasons error }
  heartbeat { at age_s interval_s file error }`;
const STATUS_QUERY = `{ host_status { ${FIELDS} } }`;
const UPDATE = `mutation($input: HostSettingsInput!) { update_host_settings(input: $input) { ${FIELDS} } }`;

const HOLDER_OPTIONS: {value: HolderMode; label: string; help: string}[] = [
  {value: 'auto', label: 'Automatic', help: 'Keep terminals running when the helper can start. Otherwise run them directly and say why.'},
  {value: 'held', label: 'Keep running', help: 'Terminals keep running after the app or service stops. If the helper cannot start, new terminals do not open.'},
  {value: 'direct', label: 'End with the host', help: 'Terminals end when the app or service stops.'},
];

const asMode = (value: string): HolderMode => (value === 'held' || value === 'direct' ? value : 'auto');
const plural = (count: number, word: string) => `${count} ${word}${count === 1 ? '' : 's'}`;

const SERVICE_TEXT: Record<string, string> = {
  running_managed: 'Installed, and this host is running as the service',
  installed_not_managing: 'Installed, but this host was started another way',
  not_installed: 'Not installed',
};

function serviceText(status: HostStatus) {
  if (!status.service.supported) return 'Background service management is unavailable on this computer';
  return SERVICE_TEXT[status.service.state] ?? status.service.state;
}

function holderText(status: HostStatus) {
  const {holder} = status;
  if (holder.mode === 'held') return 'Terminals keep running after the app or service stops';
  return `Terminals end with this host${holder.direct_terminals ? `, ${plural(holder.direct_terminals, 'terminal')} running now` : ''}`;
}

function awakeText(status: HostStatus) {
  const awake = status.keep_awake;
  if (!awake.supported) return 'This computer offers no way to stay awake from TermX';
  if (awake.active) return `Awake now. ${awake.reasons.join('. ')}`;
  return awake.enabled ? 'On, and holding the computer awake' : 'Off. The computer can sleep when it is idle';
}

function heartbeatText(status: HostStatus) {
  const {heartbeat} = status;
  if (heartbeat.error) return `Could not be written: ${heartbeat.error}`;
  if (heartbeat.age_s == null) return 'Not written yet';
  return `Updated ${Math.round(heartbeat.age_s)} seconds ago, every ${Math.round(heartbeat.interval_s)} seconds`;
}

/** Why the controls are not available to this person. */
export const HOST_ADMIN_REASON = 'Changing always-on settings needs the host administrator permission. Ask the host owner to change them.';

function Facts({status}: {status: HostStatus}) {
  const {holder} = status;
  return (
    <dl className="settings-facts" aria-label="Host status">
      <dt>Background service</dt>
      <dd>{serviceText(status)}</dd>
      <dt>Terminals</dt>
      <dd>{holderText(status)}</dd>
      {holder.reason && (
        <>
          <dt>Why</dt>
          <dd>{holder.reason}</dd>
        </>
      )}
      {holder.running && (
        <>
          <dt>Terminal helper</dt>
          <dd>Process {holder.pid ?? 'unknown'} holding {plural(holder.sessions, 'terminal')}{holder.draining ? ', finishing up before it stops' : ''}</dd>
        </>
      )}
      <dt>Keep awake</dt>
      <dd>{awakeText(status)}{status.keep_awake.error ? `. ${status.keep_awake.error}` : ''}</dd>
      <dt>Health file</dt>
      <dd><code>{status.heartbeat.file}</code> in the TermX config folder. {heartbeatText(status)}</dd>
    </dl>
  );
}

function HostForm({status, onChanged}: {status: HostStatus; onChanged: (next: HostStatus) => void}) {
  const {formProps, markClean} = useDirtyForm('host-always-on');
  const [keepAwake, setKeepAwake] = useState(status.keep_awake.enabled);
  const [holder, setHolder] = useState<HolderMode>(asMode(status.holder.setting));
  const [error, setError] = useState(''), [saving, setSaving] = useState(false);
  const flight = useRef(false);
  const supported = status.keep_awake.supported;

  async function save() {
    if (flight.current) return;
    const input: {keep_awake?: boolean; pty_holder?: HolderMode} = {};
    if (keepAwake !== status.keep_awake.enabled) input.keep_awake = keepAwake;
    if (holder !== asMode(status.holder.setting)) input.pty_holder = holder;
    if (!Object.keys(input).length) {
      markClean();
      return;
    }
    flight.current = true;
    setSaving(true);
    setError('');
    try {
      const result = await gql<{update_host_settings: HostStatus}>(UPDATE, {input});
      markClean();
      onChanged(result.update_host_settings);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'The host did not accept the settings. Check the connection and try again.');
    } finally {
      flight.current = false;
      setSaving(false);
    }
  }

  return (
    <form
      {...formProps}
      noValidate
      className="manager-form"
      aria-label="Always-on host settings"
      aria-busy={saving}
      onSubmit={event => {
        event.preventDefault();
        void save();
      }}
    >
      <div className="form-field">
        <label className="settings-radio-option" htmlFor="host-keep-awake">
          <input id="host-keep-awake" type="checkbox" name="keep_awake" checked={keepAwake} disabled={saving || !supported} onChange={event => setKeepAwake(event.target.checked)} />
          <span>
            Keep this computer awake
            <small>Stops the computer from sleeping while TermX runs, even when no task is running. The display can still sleep.</small>
          </span>
        </label>
        {!supported && <p className="settings-reason">This computer offers no way to stay awake from TermX, so the setting is unavailable.</p>}
      </div>
      <fieldset className="settings-radio-group" disabled={saving}>
        <legend>Terminals when the app or service stops</legend>
        {HOLDER_OPTIONS.map(option => (
          <label className="settings-radio-option" key={option.value}>
            <input type="radio" name="pty_holder" value={option.value} checked={holder === option.value} onChange={() => setHolder(option.value)} />
            <span>
              {option.label}
              <small>{option.help}</small>
            </span>
          </label>
        ))}
        <p className="settings-reason">A change applies to terminals you open next. Terminals already running keep their mode.</p>
        {status.holder.env_override && (
          <p className="settings-reason">TERMX_PTY_HOLDER is set on the host and overrides this choice. Unset it and restart the host to use the saved choice.</p>
        )}
      </fieldset>
      {error && <p role="alert" className="inline-error">{error}</p>}
    </form>
  );
}

/**
 * The always-on controls for this host: keep awake and where terminals live, with the host's own account of what is
 * in effect and why. `admin` is the host-admin permission; without it nothing is read or changed.
 */
export default function HostAlwaysOnSettings({admin}: {admin: boolean}) {
  const [status, setStatus] = useState<HostStatus | null>(null), [error, setError] = useState(''), [version, setVersion] = useState(0);
  const load = useCallback(async () => {
    try {
      setStatus((await gql<{host_status: HostStatus}>(STATUS_QUERY)).host_status);
      setError('');
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Host status is unavailable. Check the connection and refresh.');
    }
  }, []);
  useEffect(() => {
    if (admin) void load();
  }, [admin, load, version]);

  if (!admin) return <p className="manager-help" role="status">{HOST_ADMIN_REASON}</p>;
  return (
    <section className="settings-form-section" aria-label="Always-on host">
      <p className="manager-help">Settings that keep work running on this computer when nobody is at the keyboard.</p>
      {error && <p role="alert" className="inline-error">{error}</p>}
      {!status && !error && <p role="status" className="manager-help">Loading host status…</p>}
      {status && (
        <>
          <Facts status={status} />
          <HostForm key={`${status.keep_awake.enabled}:${status.holder.setting}`} status={status} onChanged={setStatus} />
        </>
      )}
      <div className="manager-item-actions">
        <Button type="button" variant="ghost" data-keeps-draft onClick={() => setVersion(value => value + 1)}>Refresh host status</Button>
      </div>
    </section>
  );
}
