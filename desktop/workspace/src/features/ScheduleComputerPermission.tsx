import {useEffect,useRef,useState,type ReactNode} from 'react';
import {Button} from '../components/ui/button';
import {FormField} from '../components/ui/form-field';
import {Input} from '../components/ui/input';

/** What the person has chosen so far. `valid` is false while a choice is missing, so the caller can say what to fix. */
export type ComputerDraft = {enabled: boolean; apps: string[]; hours: number; seconds: number; valid: boolean};
export type ComputerPermission = {apps: string[]; expires_at: number; max_seconds: number};
export const noComputer: ComputerDraft = {enabled: false, apps: [], hours: 24, seconds: 300, valid: true};

/** The permission as the host stores it. `now` is when the person started the request, so a retry sends the same expiry. */
export function computerBody(draft: ComputerDraft, now = Date.now() / 1000): ComputerPermission | null {
  if (!draft.enabled || !draft.valid) return null;
  return {apps: draft.apps, expires_at: Math.floor(now + draft.hours * 3600), max_seconds: draft.seconds};
}

const split = (text: string) => text.split(/[,\n]/).map(item => item.trim()).filter(Boolean);

type Fields = 'apps' | 'hours' | 'seconds';

/**
 * Off by default. A schedule that controls the computer unattended can only act in the applications named here, for the
 * time given, and the bot's own permissions must already allow computer control. Apps come from the bot's active
 * skills or can be typed; the host checks every name again. `alwaysOn` leaves out the switch, for a form that only exists
 * to set the permission. Problems show once a field has been visited, or all at once when `showErrors` is set.
 */
export default function ScheduleComputerPermission({suggestions, suggestionsFailed = false, grantHours, limitNote, disabled, initial, alwaysOn = false, showErrors = false, focusOnMount = false, onChange}: {
  suggestions: string[]; suggestionsFailed?: boolean; grantHours: number; limitNote: string; disabled: boolean; initial?: Partial<ComputerPermission>;
  alwaysOn?: boolean; showErrors?: boolean; focusOnMount?: boolean; onChange: (draft: ComputerDraft) => void;
}) {
  const root = useRef<HTMLFieldSetElement>(null);
  const [enabled, setEnabledState] = useState(alwaysOn);
  const [picked, setPicked] = useState<string[]>(() => (initial?.apps || []).filter(app => suggestions.includes(app)));
  const [typed, setTyped] = useState(() => (initial?.apps || []).filter(app => !suggestions.includes(app)).join(', '));
  const [hours, setHours] = useState('24'), [seconds, setSeconds] = useState('300');
  const [touched, setTouched] = useState<Record<Fields, boolean>>({apps: false, hours: false, seconds: false});
  const touch = (field: Fields) => setTouched(current => current[field] ? current : {...current, [field]: true});
  const apps = [...new Set([...picked, ...split(typed)])];
  const hoursNumber = Number(hours), secondsNumber = Number(seconds);
  const maxHours = Math.max(1, Math.floor(Number.isFinite(grantHours) ? grantHours : 24));
  const hoursOk = hours.trim() !== '' && hoursNumber > 0 && hoursNumber <= maxHours;
  const secondsOk = Number.isInteger(secondsNumber) && secondsNumber >= 10 && secondsNumber <= 3600;
  const appsOk = apps.length >= 1 && apps.length <= 20 && !apps.includes('*');
  const valid = !enabled || (appsOk && hoursOk && secondsOk);
  const problem = (field: Fields, ok: boolean, text: string) => ok || !(showErrors || touched[field]) ? undefined : text;
  useEffect(() => onChange({enabled, apps, hours: hoursNumber, seconds: secondsNumber, valid}),
    // `apps` is rebuilt on each render; its content is what matters.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [enabled, apps.join('\n'), hoursNumber, secondsNumber, valid]);
  useEffect(() => {
    if (focusOnMount) root.current?.querySelector<HTMLElement>('input:not([disabled])')?.focus();
  }, [focusOnMount]);
  return <fieldset className="schedule-computer" aria-label="Computer control" ref={root}>
    {!alwaysOn && <label>
      <input type="checkbox" checked={enabled} disabled={disabled} onChange={event => setEnabledState(event.target.checked)} />
      Let this schedule control apps on this computer
    </label>}
    {enabled && <>
      <p className="manager-help">This lets the bot control only these apps while the schedule runs. It works in the background, so the app does not need to be in front. Scheduled runs cannot send, delete or enter passwords. The permission ends when the time below runs out.</p>
      {suggestions.length > 0 && <div role="group" aria-label="Apps from this bot’s skills" className="schedule-computer-apps">
        {suggestions.map(app => <label key={app}>
          <input type="checkbox" checked={picked.includes(app)} disabled={disabled}
            onChange={event => { touch('apps'); setPicked(current => event.target.checked ? [...current, app] : current.filter(item => item !== app)); }} />
          {app}
        </label>)}
      </div>}
      <FormField label={suggestions.length ? 'Other apps' : 'Apps'}
        hint={<>Exact app names or bundle IDs, separated by commas. Up to 20. No wildcards.{!suggestions.length && (suggestionsFailed ? ' Could not read this bot’s skills, so app suggestions are unavailable. Type the apps it should control.' : ' None of this bot’s active skills name apps, so type the ones it should control.')}</>}
        error={problem('apps', appsOk, 'Choose or type at least one app. Up to 20, and no wildcards.')}>
        {control => <Input {...control} value={typed} disabled={disabled} placeholder="Notes, com.apple.Safari"
          onChange={event => setTyped(event.target.value)} onBlur={() => touch('apps')} />}
      </FormField>
      <div className="schedule-fields">
        <FormField label="Computer control expires after (hours)"
          error={problem('hours', hoursOk, `Choose 1 to ${maxHours} hours, ${limitNote}. It has to outlast the first run.`)}>
          {control => <Input {...control} type="number" min="1" max={maxHours} value={hours} disabled={disabled}
            onChange={event => setHours(event.target.value)} onBlur={() => touch('hours')} />}
        </FormField>
        <FormField label="Computer seconds per run" error={problem('seconds', secondsOk, 'Choose a whole number from 10 to 3,600 seconds per run.')}>
          {control => <Input {...control} type="number" min="10" max="3600" value={seconds} disabled={disabled}
            onChange={event => setSeconds(event.target.value)} onBlur={() => touch('seconds')} />}
        </FormField>
      </div>
    </>}
  </fieldset>;
}

const when = (seconds: number) => new Date(seconds * 1000).toLocaleString();

/**
 * A saved schedule's computer permission: what it is, and the two ways to change it. Each change is its own save.
 * Focus stays inside the panel: opening the form focuses its first field, and finishing or cancelling returns to the heading.
 * The form stays open, with what was typed, when a save does not go through.
 */
export function ComputerPermissionPanel({permission, suggestions, suggestionsFailed, grantHours, allowed, blocked, busy, onSave, onRemove}: {
  permission: ComputerPermission | null | undefined; suggestions: string[]; suggestionsFailed?: boolean; grantHours: number; allowed: boolean; blocked: ReactNode; busy: boolean;
  onSave: (permission: ComputerPermission) => Promise<boolean>; onRemove: () => Promise<boolean>;
}) {
  const [changing, setChanging] = useState(false), [attempted, setAttempted] = useState(false);
  const [draft, setDraft] = useState<ComputerDraft>(noComputer);
  const heading = useRef<HTMLHeadingElement>(null);
  const leave = () => { setChanging(false); setAttempted(false); heading.current?.focus(); };
  async function save() {
    const body = computerBody(draft);
    if (!body) { setAttempted(true); return; }
    if (await onSave(body)) leave();
  }
  async function remove() {
    if (await onRemove()) heading.current?.focus();
  }
  return <section aria-label="Computer control for this schedule" className="schedule-computer-panel">
    <h4 tabIndex={-1} ref={heading}>Computer control</h4>
    {permission
      ? <p>Can control only {permission.apps.join(', ')} until {when(permission.expires_at)}, for up to {permission.max_seconds} seconds per run.</p>
      : <p>Off. This schedule cannot control the computer.</p>}
    {!changing && <div className="schedule-actions">
      {allowed
        ? <Button variant="secondary" disabled={busy} onClick={() => setChanging(true)}>{permission ? 'Change computer permission' : 'Allow computer control'}</Button>
        : <div>{blocked}</div>}
      {permission && <Button variant="secondary" disabled={busy} onClick={() => void remove()}>Remove computer permission</Button>}
    </div>}
    {changing && <>
      <ScheduleComputerPermission alwaysOn focusOnMount showErrors={attempted} suggestions={suggestions} suggestionsFailed={suggestionsFailed} grantHours={grantHours}
        limitNote="all the time this schedule’s authorization has left" disabled={busy} initial={{apps: permission?.apps}} onChange={setDraft} />
      <div className="schedule-actions">
        <Button variant="ghost" disabled={busy} onClick={leave}>Cancel</Button>
        <Button disabled={busy} onClick={() => void save()}>Save computer permission</Button>
      </div>
    </>}
  </section>;
}
