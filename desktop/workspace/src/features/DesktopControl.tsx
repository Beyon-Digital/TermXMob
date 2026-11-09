import {useCallback,useEffect,useRef,useState,type ReactNode} from 'react';
import {Badge} from '../components/ui/badge';
import {Button} from '../components/ui/button';
import {Dialog,DialogContent,DialogDescription,DialogTitle} from '../components/ui/dialog';
import {FormField} from '../components/ui/form-field';
import {Input} from '../components/ui/input';
import {SettingsRow,SettingsSection} from '../components/ui/settings-section';
import {
  installDesktopControl,loadDesktopReady,loadDesktopStatus,megabytes,pathProblem,removeDesktopControl,setDesktopControlPath,type DesktopStatus,
} from './desktop-control-api';
import {useDirtyForm} from './settings-forms';
import './desktop-control.css';

const HELP = 'Lets your agents read and operate other applications through their accessibility controls, in the background. It does not take screenshots.';
const NOT_SET_UP = 'Desktop control is not set up on this computer.';
/** Problems with the program itself. Each has the same remedy: install it again, or remove it. */
const PROGRAM_PROBLEMS = new Set(['hash_mismatch', 'version_mismatch', 'envelope_unsupported', 'not_pinned_build', 'helper_missing', 'spawn_failed', 'configured_invalid']);
const message = (reason: unknown) => reason instanceof Error ? reason.message : String(reason);

function Steps({status}: {status: DesktopStatus}) {
  const who = status.permission.grant_to;
  const subject = who ? <strong>{who}</strong> : <>the application that started TermX (for example Terminal or the TermX app)</>;
  let steps: ReactNode[] = [];
  if (status.platform === 'macos') steps = [
    'Open System Settings and go to Privacy & Security.',
    'Open Accessibility. It is called Device Control and Data Access on macOS 27 and later.',
    <>Switch on {subject}. If System Settings lists agent-desktop separately, switch that on too.</>,
    'Come back here and choose Check again.',
  ];
  else if (status.platform === 'linux') steps = [
    'Run TermX from a graphical desktop session, not from a plain SSH login or a system service.',
    'Install the accessibility service, at-spi2-core (Ubuntu 24.04 or newer).',
    'Chromium and Qt applications may need their accessibility option turned on. Wayland-only sessions cannot scroll or focus.',
    'Choose Check again.',
  ];
  if (!steps.length) return null;
  return <ol className="desktop-control-steps" aria-label="Steps to allow desktop control">{steps.map((step, index) => <li key={index}>{step}</li>)}</ol>;
}

function Facts({label, rows}: {label: string; rows: [string, ReactNode][]}) {
  return <ul className="desktop-control-facts" aria-label={label}>{rows.map(([name, value]) => <li key={name}><span>{name}</span><strong>{value}</strong></li>)}</ul>;
}

export default function DesktopControl({admin}: {admin: boolean}) {
  const [status, setStatus] = useState<DesktopStatus | null>(null), [error, setError] = useState('');
  const [busy, setBusy] = useState(false), [dialog, setDialog] = useState<'install' | 'remove' | null>(null), [dialogError, setDialogError] = useState('');
  const [ready, setReady] = useState<boolean | null>(null);
  const [path, setPath] = useState(''), [pathError, setPathError] = useState(''), [savingPath, setSavingPath] = useState(false);
  const alive = useRef(true), flight = useRef(false);
  const {formProps, markClean} = useDirtyForm('desktop-control-path');

  const load = useCallback(async (refresh: boolean) => {
    setBusy(true);
    try {
      const next = await loadDesktopStatus(refresh);
      if (alive.current) { setStatus(next); setError(''); }
    } catch (reason) {
      if (alive.current) setError('Desktop control status could not be read. ' + message(reason));
    } finally { if (alive.current) setBusy(false); }
  }, []);
  useEffect(() => {
    alive.current = true;
    if (admin) void load(false);
    else loadDesktopReady().then(value => { if (alive.current) setReady(value); }).catch(() => {});
    return () => { alive.current = false; };
  }, [admin, load]);
  useEffect(() => { if (status?.configured_path) setPath(current => current || status.configured_path!); }, [status?.configured_path]);

  async function act(kind: 'install' | 'remove') {
    if (flight.current || !status) return;
    flight.current = true; setBusy(true); setDialogError('');
    try {
      const next = kind === 'install' ? await installDesktopControl(status.pinned_version) : await removeDesktopControl();
      if (alive.current) { setStatus(next); setError(''); setDialog(null); }
    } catch (reason) {
      if (alive.current) setDialogError(message(reason));
    } finally { flight.current = false; if (alive.current) setBusy(false); }
  }

  async function savePath(value: string) {
    if (savingPath) return;
    const problem = value ? pathProblem(value) : '';
    setPathError(problem);
    if (problem) return;
    setSavingPath(true);
    try {
      const next = await setDesktopControlPath(value.trim());
      if (alive.current) { setStatus(next); setError(''); markClean(); if (!value) setPath(''); }
    } catch (reason) {
      if (alive.current) setPathError(message(reason));
    } finally { if (alive.current) setSavingPath(false); }
  }

  if (!admin) {
    return <SettingsSection title="Desktop control" description={HELP}>
      {ready === false && <li className="settings-row"><p role="status" className="settings-row-description">{NOT_SET_UP} A host admin can set it up under Settings, Tools &amp; connections.</p></li>}
      {ready === true && <li className="settings-row"><p role="status" className="settings-row-description">Desktop control is ready on this computer.</p></li>}
    </SettingsSection>;
  }

  const body = (() => {
    if (error && !status) return <li className="settings-row desktop-control-block">
      <p role="alert">{error}</p>
      <div className="creation-actions"><Button variant="outline" disabled={busy} onClick={() => void load(true)}>Check again</Button></div>
    </li>;
    if (!status) return <li className="settings-row"><p role="status" className="settings-row-description">Checking desktop control…</p></li>;
    if (!status.platform_supported) return <li className="settings-row"><p className="settings-row-description">Desktop control is not available for {status.platform} {status.arch} in this version of TermX.</p></li>;

    const problem = status.issues.find(issue => PROGRAM_PROBLEMS.has(issue.code));
    const needsPermission = status.installed && !problem && !status.ready && ['denied', 'unknown', 'unavailable'].includes(status.permission.state);
    const configured = status.source === 'configured';
    const source = configured ? 'Your own program, not checked' : status.source === 'path' ? 'Copied from your PATH by TermX' : 'Installed by TermX';
    const badge = status.ready ? 'Ready' : !status.installed ? 'Not installed' : 'Needs attention';
    const description = status.ready ? `Version ${status.version ?? status.pinned_version} · ${source}`
      : !status.installed ? `Not installed. TermX can download version ${status.pinned_version} (${megabytes(status.install.size_bytes)}) from ${status.install.host} and check it against a checksum built into this version before running it.`
      : 'Agents cannot control applications until this is fixed.';
    return <>
      <SettingsRow label="Status" description={description}>
        <Badge variant={status.ready ? 'default' : 'outline'}>{badge}</Badge>
      </SettingsRow>
      {error && <li className="settings-row desktop-control-block"><p role="alert">{error}</p></li>}
      {problem && <li className="settings-row desktop-control-block"><p role="alert">{problem.message} {problem.action}</p></li>}
      {needsPermission && <li className="settings-row desktop-control-block">
        <div role="alert">
          <p>{status.permission.guidance || status.issues.find(issue => issue.code === 'permission_denied' || issue.code === 'no_accessibility_bus')?.message || 'This computer has not allowed TermX to read other applications.'}</p>
          <Steps status={status}/>
          {status.platform === 'macos' && <p className="manager-help">The permission belongs to the application that starts TermX, so start TermX from the application that has the permission.</p>}
        </div>
      </li>}
      {configured && status.ready && <li className="settings-row desktop-control-block"><p className="manager-help">An unchecked program runs with the permission you gave TermX. Use this only for a build you made or trust.</p></li>}
      <li className="settings-row desktop-control-block">
        <div className="creation-actions">
          {!status.installed && status.install.available && <Button variant="outline" disabled={busy} onClick={() => { setDialogError(''); setDialog('install'); }}>Install desktop control</Button>}
          {status.installed && (problem || (!status.ready && !needsPermission)) && status.install.available && <Button variant="outline" disabled={busy} onClick={() => { setDialogError(''); setDialog('install'); }}>Reinstall desktop control</Button>}
          {status.installed && <Button variant="ghost" disabled={busy} onClick={() => void load(true)}>Check again</Button>}
          {status.installed && !configured && <Button variant="ghost" disabled={busy} onClick={() => { setDialogError(''); setDialog('remove'); }}>Remove desktop control</Button>}
        </div>
      </li>
      {status.installed && <li className="settings-row desktop-control-block">
        <details className="manager-item-details"><summary>Details</summary>
          <Facts label="Desktop control details" rows={[
            ['Program', status.path || 'Not reported'],
            ['Version', status.version ? `${status.version}${status.envelope_version ? ` (format ${status.envelope_version})` : ''}` : 'Not reported'],
            ['Integrity', status.verified ? 'Checked against the checksum built into this version of TermX' : 'Not checked'],
            ['Helper program', status.helper_ok === null ? 'Not used on this system' : status.helper_ok ? 'OK' : 'Missing'],
            ['Can reach', status.supported_surfaces.length ? status.supported_surfaces.join(', ') : 'Not reported'],
          ]}/>
          {status.platform === 'windows' && <p className="manager-help">Elevated windows cannot be controlled from a normal TermX process. A locked screen or another user’s session is not supported.</p>}
          <p className="manager-help">Reading an application can switch on its accessibility support, which is a change in that application. The text an agent reads is sent to your model provider.</p>
        </details>
      </li>}
      <li className="settings-row desktop-control-block">
        <details className="manager-item-details" open={configured || undefined}><summary>Use a program you built yourself</summary>
          <form {...formProps} noValidate aria-label="Desktop control program path" className="manager-form" onSubmit={event => { event.preventDefault(); void savePath(path); }}>
            <FormField label="Program path" error={pathError}
              hint="The full path to an agent-desktop program. TermX cannot check it against a known checksum, so it runs with the permission you gave TermX. Leave it for the copy TermX installs.">
              {control => <Input {...control} name="path" value={path} disabled={savingPath || !!status.setting_locked} placeholder="/usr/local/bin/agent-desktop"
                onChange={event => { setPath(event.target.value); setPathError(''); }}/>}
            </FormField>
            {status.setting_locked && <p className="manager-help">The TERMX_AGENT_DESKTOP environment variable is set for this host, so it decides which program is used.</p>}
            <div className="creation-actions">
              <Button type="submit" variant="outline" disabled={savingPath || !!status.setting_locked}>Use this path</Button>
              {configured && <Button type="button" variant="ghost" disabled={savingPath || !!status.setting_locked} onClick={() => void savePath('')}>Stop using this path</Button>}
            </div>
          </form>
        </details>
      </li>
    </>;
  })();

  const busyDialog = busy && dialog !== null;
  return <>
    <SettingsSection title="Desktop control" description={HELP}>{body}</SettingsSection>
    <Dialog open={dialog !== null && !!status} onOpenChange={value => { if (!value && !busy) setDialog(null); }}>
      <DialogContent aria-busy={busyDialog}>
        {dialog === 'install' && status && <>
          <DialogTitle>Install desktop control</DialogTitle>
          <DialogDescription>TermX downloads one program and checks it before it runs.</DialogDescription>
          <Facts label="What will be installed" rows={[
            ['Version', status.pinned_version],
            ['Source', <>{status.install.host} (vercel-labs/agent-desktop)</>],
            ['License', 'Apache-2.0'],
            ['Size', megabytes(status.install.size_bytes)],
            ...(status.install.asset ? [['File', status.install.asset] as [string, ReactNode]] : []),
          ]}/>
          <p>Checked against the checksum built into TermX before it runs.</p>
          <p className="manager-help">{status.install.note}</p>
          <p>Agents can then read and operate the applications you allow, in the background. Text read from an application is sent to your model provider.</p>
        </>}
        {dialog === 'remove' && status && <>
          <DialogTitle>Remove desktop control</DialogTitle>
          <DialogDescription>Deletes the downloaded program for version {status.version ?? status.pinned_version}.</DialogDescription>
          <p>Agents cannot control applications until you install it again. Your rules and skills are not changed.</p>
        </>}
        {busyDialog && <p role="status">{dialog === 'install' ? 'Installing…' : 'Removing…'}</p>}
        {dialogError && <p role="alert">{dialogError}</p>}
        <div className="creation-actions">
          <Button disabled={busy} onClick={() => dialog && void act(dialog)}>{dialog === 'remove' ? 'Remove desktop control' : 'Install desktop control'}</Button>
          <Button variant="ghost" disabled={busy} onClick={() => setDialog(null)}>Cancel</Button>
        </div>
      </DialogContent>
    </Dialog>
  </>;
}
