import {useEffect, useMemo, useRef, useState} from 'react';
import {Badge} from '../components/ui/badge';
import {Button} from '../components/ui/button';
import {Dialog, DialogContent, DialogDescription, DialogTitle} from '../components/ui/dialog';
import {Input} from '../components/ui/input';
import {Select} from '../components/ui/select';
import {SettingsRow, SettingsSection} from '../components/ui/settings-section';
import {usePolling} from '../lib/use-polling';
import {SpeechPlayer, type PlayerState} from '../lib/voice/player';
import {
  downloadWoof,
  hostOf,
  isLocal,
  loadVoiceStatus,
  loadWoofLicense,
  removeWoof,
  saveVoiceSettings,
  type Attribution,
  type VoiceEngineStatus,
  type VoiceSettings,
  type VoiceStatus,
  type WoofLicense,
} from '../lib/voice/settings';
import {useDirtyForm} from './settings-forms';
import {ManagerPage} from './manager-ui';
import {useManagerLoad, useManagerMutation} from './use-manager';
import './voice-section.css';

const SAMPLE = 'This is how your agent will sound when it reads a reply.';
const STATE_LABEL: Record<VoiceEngineStatus['state'], string> = {
  ready: 'Ready',
  not_installed: 'Not downloaded',
  downloading: 'Downloading',
  error: 'Needs attention',
  unavailable: 'Unavailable',
};
const WOOF_VOICES = ['juno', 'mira'];
/** A voice name only means something to the engine it came from. */
const voiceFor = (engine: VoiceSettings['engine'], voice: string) =>
  engine === 'woof' ? (WOOF_VOICES.includes(voice) ? voice : '') : engine === 'provider' && !WOOF_VOICES.includes(voice) ? voice : '';
const megabytes = (bytes?: number) => (bytes ? `${Math.round(bytes / 1_000_000)} MB` : '');

interface Draft {
  engine: VoiceSettings['engine'];
  provider: string;
  model: string;
  transcribe_model: string;
  voice: string;
  auto_send: boolean;
  auto_speak: boolean;
  cloud_fallback: boolean;
  keep_audio: boolean;
  consent_provider: boolean;
  live_provider: string;
  live_model: string;
  live_voice: string;
  live_transcribe_model: string;
}

const toDraft = (settings: VoiceSettings): Draft => ({
  engine: settings.engine,
  provider: settings.provider ?? '',
  model: settings.model,
  transcribe_model: settings.transcribe_model,
  voice: settings.voice,
  auto_send: settings.auto_send,
  auto_speak: settings.auto_speak,
  cloud_fallback: settings.cloud_fallback,
  keep_audio: settings.keep_audio,
  consent_provider: !!settings.provider && settings.consent.provider?.id === settings.provider,
  live_provider: settings.live_provider ?? '',
  live_model: settings.live_model ?? '',
  live_voice: settings.live_voice ?? '',
  live_transcribe_model: settings.live_transcribe_model ?? '',
});

function LicenseDialog({
  mode,
  onClose,
  onConfirm,
  onCloseAutoFocus,
}: {
  mode: 'download' | 'accept';
  onClose: () => void;
  onConfirm: (sha256: string) => Promise<void>;
  onCloseAutoFocus: (event: Event) => void;
}) {
  const [texts, setTexts] = useState<WoofLicense | null>(null);
  const [error, setError] = useState('');
  const [agreed, setAgreed] = useState(false);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    let cancelled = false;
    loadWoofLicense()
      .then(value => !cancelled && setTexts(value))
      .catch(failure => !cancelled && setError(failure instanceof Error ? failure.message : String(failure)));
    return () => {
      cancelled = true;
    };
  }, []);
  const verb = mode === 'download' ? 'Download' : 'Accept license';
  async function confirm() {
    if (!texts) return;
    setBusy(true);
    setError('');
    try {
      await onConfirm(texts.license.sha256);
      onClose();
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : String(failure));
    } finally {
      setBusy(false);
    }
  }
  return (
    <Dialog open onOpenChange={open => !open && !busy && onClose()}>
      <DialogContent className="voice-license-dialog" onCloseAutoFocus={onCloseAutoFocus}>
        <DialogTitle>{mode === 'download' ? 'Download Underdog Woof' : 'Underdog Woof license'}</DialogTitle>
        <DialogDescription>
          Woof is free for non-commercial use with credit. Read the license and notice, then accept to continue.
        </DialogDescription>
        {error && (
          <p className="inline-error" role="alert">
            {error}
          </p>
        )}
        {!texts && !error && <p role="status">Fetching the license from the pinned release…</p>}
        {texts && (
          <>
            <ul className="voice-license-points">
              <li>Non-commercial use only. A company, a paid product or a hosted service needs a license from {texts.contact}.</li>
              <li>Show “Voice by Underdog Woof” with a link wherever its audio plays. TermX does this for you.</li>
              <li>Do not use Woof or its audio to train another speech model.</li>
            </ul>
            <pre className="voice-license-text" role="region" tabIndex={0} aria-label="License text">
              {texts.license.text}
            </pre>
            <pre className="voice-license-text" role="region" tabIndex={0} aria-label="Notice text">
              {texts.notice.text}
            </pre>
            <label className="voice-agree">
              <input type="checkbox" checked={agreed} onChange={event => setAgreed(event.target.checked)} />
              <span>I have read the license and will use Woof only for non-commercial purposes.</span>
            </label>
          </>
        )}
        <div className="voice-dialog-actions">
          <Button type="button" variant="ghost" onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button type="button" disabled={!texts || !agreed || busy} onClick={() => void confirm()}>
            {verb}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}

function RemoveDialog({
  onClose,
  onConfirm,
  onCloseAutoFocus,
}: {
  onClose: () => void;
  onConfirm: () => Promise<void>;
  onCloseAutoFocus: (event: Event) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  return (
    <Dialog open onOpenChange={open => !open && !busy && onClose()}>
      <DialogContent className="voice-license-dialog" onCloseAutoFocus={onCloseAutoFocus}>
        <DialogTitle>Remove Underdog Woof</DialogTitle>
        <DialogDescription>
          This deletes the downloaded model from this host. You can download it again later.
        </DialogDescription>
        {error && (
          <p className="inline-error" role="alert">
            {error}
          </p>
        )}
        <div className="voice-dialog-actions">
          <Button type="button" variant="ghost" onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button
            type="button"
            variant="destructive"
            disabled={busy}
            onClick={async () => {
              setBusy(true);
              setError('');
              try {
                await onConfirm();
                onClose();
              } catch (failure) {
                setError(failure instanceof Error ? failure.message : String(failure));
              } finally {
                setBusy(false);
              }
            }}
          >
            Remove
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}

function VoiceForm({
  status,
  admin,
  busy,
  save,
  run,
}: {
  status: VoiceStatus;
  admin: boolean;
  busy: boolean;
  save: (changes: Parameters<typeof saveVoiceSettings>[0]) => Promise<unknown>;
  run: (action: () => Promise<unknown>, notice: string) => Promise<unknown>;
}) {
  const {dirty, formProps, markClean} = useDirtyForm('voice');
  const [draft, setDraft] = useState(() => toDraft(status.settings));
  const key = JSON.stringify(status.settings);
  const [seen, setSeen] = useState(key);
  if (seen !== key && !dirty) {
    setSeen(key);
    setDraft(toDraft(status.settings));
  }
  const [dialog, setDialog] = useState<'download' | 'accept' | 'remove' | null>(null);
  const opener = useRef<HTMLElement | null>(null);
  const woofRadio = useRef<HTMLInputElement>(null);
  const pendingFocus = useRef(false);
  const openDialog = (kind: 'download' | 'accept' | 'remove', from: HTMLElement) => {
    opener.current = from;
    pendingFocus.current = false;
    setDialog(kind);
  };
  // Download, Accept license and Remove disappear once they succeed, so focus goes to the engine they belong to.
  const restoreFocus = (event: Event) => {
    event.preventDefault();
    (opener.current?.isConnected ? opener.current : woofRadio.current)?.focus();
    pendingFocus.current = true;
  };
  // The refreshed status can remove the opener after the dialog has closed; then focus would fall to the page body.
  useEffect(() => {
    if (!pendingFocus.current || !opener.current || opener.current.isConnected) return;
    pendingFocus.current = false;
    if (document.activeElement === document.body || document.activeElement === null) woofRadio.current?.focus();
  }, [status]);
  // Unlike `run`, rejects with the real reason, so a dialog can stay open and show it.
  async function attempt(action: () => Promise<unknown>, notice: string) {
    let failure: unknown = null;
    const result = await run(async () => {
      try {
        return await action();
      } catch (caught) {
        failure = caught;
        throw caught;
      }
    }, notice);
    if (result === null) throw failure ?? new Error('That did not finish. Try again.');
  }
  const [previewState, setPreviewState] = useState<PlayerState>('idle');
  const [credit, setCredit] = useState<Attribution | null>(null);
  const [previewNotice, setPreviewNotice] = useState('');
  const player = useMemo(
    () => new SpeechPlayer({onState: setPreviewState, onCredit: setCredit, onNotice: setPreviewNotice}),
    [],
  );
  useEffect(() => () => player.stop(), [player]);

  const provider = status.engines.find(engine => engine.id === 'provider');
  const woof = status.engines.find(engine => engine.id === 'woof');
  const accounts = provider?.accounts ?? [];
  const account = accounts.find(row => row.id === draft.provider);
  const host = hostOf(account?.base_url);
  const liveAccounts = status.live?.accounts ?? [];
  const liveAccount = liveAccounts.find(row => row.id === draft.live_provider);
  const liveHost = hostOf(liveAccount?.base_url);
  const set = (change: Partial<Draft>) => setDraft(prior => ({...prior, ...change}));
  const woofAccepted = !!status.settings.consent.woof && status.settings.consent.woof.sha256 === woof?.license?.sha256;
  const preview = (engine: 'browser' | 'woof' | 'provider', voice?: string) => {
    setPreviewNotice('');
    void player.speak(SAMPLE, voice ? {engine, voice, fallback: false} : {engine, fallback: false});
  };

  async function submit() {
    const result = await save({
      engine: draft.engine,
      provider: draft.provider,
      model: draft.model,
      transcribe_model: draft.transcribe_model,
      voice: draft.voice,
      auto_send: draft.auto_send,
      auto_speak: draft.auto_speak,
      cloud_fallback: draft.cloud_fallback,
      keep_audio: draft.keep_audio,
      ...(draft.provider ? {consent_provider: draft.consent_provider} : {}),
      live_provider: draft.live_provider,
      live_model: draft.live_model,
      live_voice: draft.live_voice,
      live_transcribe_model: draft.live_transcribe_model,
    });
    if (result !== null) markClean();
  }

  const downloading = woof?.state === 'downloading';
  const percent = woof?.progress?.total ? Math.round((woof.progress.done / woof.progress.total) * 100) : 0;

  return (
    <>
    <form
      {...formProps}
      noValidate
      className="voice-settings"
      aria-label="Voice settings"
      onSubmit={event => {
        event.preventDefault();
        void submit();
      }}
    >
      <SettingsSection
        level={3}
        title="Audio account"
        description="Dictation and cloud voices use one API account that supports audio. A local server such as Whisper or Kokoro counts."
      >
        <SettingsRow label="Audio account" description={accounts.length ? undefined : 'Connect an API account with audio support in Models & engines first.'}>
          {control => (
            <Select
              {...control}
              name="provider"
              value={draft.provider}
              disabled={busy || !accounts.length}
              onChange={event => set({provider: event.target.value, consent_provider: false})}
            >
              <option value="">No account</option>
              {accounts.map(row => (
                <option key={row.id} value={row.id}>
                  {row.name}
                </option>
              ))}
            </Select>
          )}
        </SettingsRow>
        {account && (
          <li className="settings-row voice-consent">
            <label>
              <input
                type="checkbox"
                name="consent_provider"
                checked={draft.consent_provider}
                onChange={event => set({consent_provider: event.target.checked})}
              />
              <span>
                {host && isLocal(host.hostname)
                  ? `Send text and recordings to ${account.name}, which runs on this device (${host.host}).`
                  : `Send text and recordings to ${account.name} (${host?.host ?? 'unknown host'}). The provider may charge for this.`}
              </span>
            </label>
          </li>
        )}
        <SettingsRow label="Transcription model" description="Turns your recording into text. Needed for dictation.">
          {control => (
            <Input
              {...control}
              name="transcribe_model"
              value={draft.transcribe_model}
              disabled={busy || !draft.provider}
              onChange={event => set({transcribe_model: event.target.value})}
            />
          )}
        </SettingsRow>
        <SettingsRow label="Speech model" description="Reads replies aloud when the cloud engine is chosen.">
          {control => (
            <Input
              {...control}
              name="model"
              value={draft.model}
              disabled={busy || !draft.provider}
              onChange={event => set({model: event.target.value})}
            />
          )}
        </SettingsRow>
      </SettingsSection>

      <SettingsSection
        level={3}
        title="Spoken replies"
        description="Choose what reads replies aloud. Preview before you save."
      >
        <li className="settings-row voice-engines-row">
          <fieldset className="voice-engines">
            <legend>Voice engine</legend>
            <div className="voice-engine">
              <label className="voice-engine-choice">
                <input
                  type="radio"
                  name="engine"
                  value="browser"
                  checked={draft.engine === 'browser'}
                  onChange={() => setDraft(prior => ({...prior, engine: 'browser', voice: voiceFor('browser', prior.voice)}))}
                />
                <span>Browser voice</span>
                <Badge variant="secondary">Ready</Badge>
              </label>
              <p>Uses your browser’s built-in voice. Nothing leaves this device through TermX.</p>
              <div className="voice-engine-actions">
                <Button type="button" size="sm" variant="secondary" onClick={() => preview('browser')}>
                  Preview browser voice
                </Button>
              </div>
            </div>

            <div className="voice-engine">
              <label className="voice-engine-choice">
                <input
                  type="radio"
                  name="engine"
                  value="provider"
                  checked={draft.engine === 'provider'}
                  onChange={() => setDraft(prior => ({...prior, engine: 'provider', voice: voiceFor('provider', prior.voice)}))}
                />
                <span>Cloud or server API</span>
                <Badge variant="secondary">{provider ? STATE_LABEL[provider.state] : 'Unavailable'}</Badge>
              </label>
              <p>
                {account
                  ? host && isLocal(host.hostname)
                    ? `Text stays on this device and goes to ${account.name}.`
                    : `Text goes to ${account.name}. Choose it above and tick the box to allow that.`
                  : 'Choose an audio account above to use this engine.'}
              </p>
              <div className="voice-engine-actions">
                <Button
                  type="button"
                  size="sm"
                  variant="secondary"
                  disabled={!status.settings.provider}
                  onClick={() => preview('provider', draft.voice || undefined)}
                >
                  Preview API voice
                </Button>
              </div>
            </div>

            <div className="voice-engine">
              <label className="voice-engine-choice">
                <input
                  ref={woofRadio}
                  type="radio"
                  name="engine"
                  value="woof"
                  checked={draft.engine === 'woof'}
                  onChange={() => setDraft(prior => ({...prior, engine: 'woof', voice: voiceFor('woof', prior.voice)}))}
                />
                <span>Underdog Woof</span>
                <Badge variant="secondary">{woof ? STATE_LABEL[woof.state] : 'Unavailable'}</Badge>
              </label>
              <p>
                {woof?.state === 'ready'
                  ? 'Runs on this device. Nothing you say or read leaves it. Non-commercial use only.'
                  : (woof?.detail ?? 'Not available on this host.')}
                {woof?.installable && !woof.installed && woof.size_bytes ? ` About ${megabytes(woof.size_bytes)}.` : ''}
              </p>
              {downloading && (
                <div
                  className="voice-progress"
                  role="progressbar"
                  aria-label="Downloading Underdog Woof"
                  aria-valuemin={0}
                  aria-valuemax={100}
                  aria-valuenow={percent}
                >
                  <span style={{width: `${percent}%`}} />
                </div>
              )}
              <div className="voice-engine-actions">
                {woof?.installable && !woof.installed && !downloading && admin && (
                  <Button type="button" size="sm" disabled={busy} onClick={event => openDialog('download', event.currentTarget)}>
                    Download
                  </Button>
                )}
                {woof?.installable && !woof.installed && !downloading && !admin && (
                  <span className="voice-note">Ask the host owner to download Woof.</span>
                )}
                {woof?.installed && !woofAccepted && (
                  <Button type="button" size="sm" disabled={busy} onClick={event => openDialog('accept', event.currentTarget)}>
                    Accept license
                  </Button>
                )}
                {woof?.state === 'ready' &&
                  woofAccepted &&
                  woof.voices.map(voice => (
                    <Button
                      key={voice.id}
                      type="button"
                      size="sm"
                      variant="secondary"
                      onClick={() => preview('woof', voice.id)}
                    >
                      {`Preview ${voice.label}`}
                    </Button>
                  ))}
                {woof?.installed && admin && !downloading && (
                  <Button type="button" size="sm" variant="ghost" disabled={busy} onClick={event => openDialog('remove', event.currentTarget)}>
                    Remove
                  </Button>
                )}
              </div>
              {woof?.installed && woofAccepted && <p className="voice-note">License accepted for non-commercial use.</p>}
              {woof?.installed && woof.attribution && (
                <p className="voice-note">
                  <a href={woof.attribution.url} target="_blank" rel="noopener noreferrer">
                    {woof.attribution.text}
                  </a>
                </p>
              )}
            </div>
          </fieldset>
          <div role="status" className="voice-preview-status">
            {previewState === 'loading' ? 'Preparing the preview…' : previewState === 'speaking' ? 'Playing the preview.' : previewNotice}
            {credit && (
              <>
                {' '}
                <a href={credit.url} target="_blank" rel="noopener noreferrer">
                  {credit.text}
                </a>
              </>
            )}
          </div>
        </li>
        <SettingsRow
          label="Voice"
          description={
            draft.engine === 'woof'
              ? 'Juno or Mira.'
              : draft.engine === 'provider'
                ? 'A voice name your server offers, such as alloy.'
                : 'The browser voice is chosen in your system settings.'
          }
        >
          {control =>
            draft.engine === 'woof' ? (
              <Select {...control} name="voice" value={draft.voice || 'juno'} onChange={event => set({voice: event.target.value})}>
                {(woof?.voices ?? []).map(voice => (
                  <option key={voice.id} value={voice.id}>
                    {voice.label}
                  </option>
                ))}
              </Select>
            ) : (
              <Input
                {...control}
                name="voice"
                list="voice-suggestions"
                value={draft.voice}
                disabled={draft.engine === 'browser'}
                onChange={event => set({voice: event.target.value})}
              />
            )
          }
        </SettingsRow>
        <datalist id="voice-suggestions">
          {(provider?.voices ?? []).map(voice => (
            <option key={voice.id} value={voice.id} />
          ))}
        </datalist>
        <SettingsRow label="Read replies aloud" description="Speak each new reply when it finishes. Replies already on screen are not read.">
          {control => (
            <input
              {...control}
              type="checkbox"
              name="auto_speak"
              checked={draft.auto_speak}
              onChange={event => set({auto_speak: event.target.checked})}
            />
          )}
        </SettingsRow>
        <SettingsRow
          label="Fall back to the audio account"
          description="If Woof cannot speak, send the text to your audio account instead. Off keeps Woof text on this device."
        >
          {control => (
            <input
              {...control}
              type="checkbox"
              name="cloud_fallback"
              checked={draft.cloud_fallback}
              disabled={draft.engine !== 'woof' || !draft.provider}
              onChange={event => set({cloud_fallback: event.target.checked})}
            />
          )}
        </SettingsRow>
      </SettingsSection>

      <SettingsSection
        level={3}
        title="Live conversation"
        description="Talk with the agent out loud from the message box. TermX relays your voice to the account below and keeps no audio."
      >
        <SettingsRow
          label="Live voice account"
          description={liveAccounts.length ? undefined : 'No API account declares live voice yet. Tick "Live voice conversation" on an account in Models & engines. OpenAI and xAI accounts work.'}
        >
          {control => (
            <Select
              {...control}
              name="live_provider"
              value={draft.live_provider}
              disabled={busy || !liveAccounts.length}
              onChange={event => set({live_provider: event.target.value})}
            >
              <option value="">No account</option>
              {liveAccounts.map(row => (
                <option key={row.id} value={row.id}>
                  {row.name}
                </option>
              ))}
            </Select>
          )}
        </SettingsRow>
        {liveAccount && (
          <li className="settings-row">
            <p className="voice-note">
              {liveHost && isLocal(liveHost.hostname)
                ? `Live voice runs on ${liveAccount.name} on this device (${liveHost.host}).`
                : `Your voice and the replies are billed by ${liveAccount.name} (${liveHost?.host ?? 'unknown host'}). You are asked to agree the first time you start a session.`}
            </p>
          </li>
        )}
        <SettingsRow label="Live voice model" description="The realtime model name, for example gpt-realtime.">
          {control => (
            <Input
              {...control}
              name="live_model"
              value={draft.live_model}
              disabled={busy || !draft.live_provider}
              onChange={event => set({live_model: event.target.value})}
            />
          )}
        </SettingsRow>
        <SettingsRow label="Live voice name" description="Optional. Leave empty for the provider's default voice.">
          {control => (
            <Input
              {...control}
              name="live_voice"
              value={draft.live_voice}
              disabled={busy || !draft.live_provider}
              onChange={event => set({live_voice: event.target.value})}
            />
          )}
        </SettingsRow>
        <SettingsRow label="Live transcription model" description="Optional. Shows what you said in the live panel.">
          {control => (
            <Input
              {...control}
              name="live_transcribe_model"
              value={draft.live_transcribe_model}
              disabled={busy || !draft.live_provider}
              onChange={event => set({live_transcribe_model: event.target.value})}
            />
          )}
        </SettingsRow>
      </SettingsSection>

      <SettingsSection level={3} title="Dictation" description="The microphone button and the hold-to-talk shortcut sit in the message box.">
        <SettingsRow label="Send after dictation" description="Send the message as soon as it is transcribed, without review.">
          {control => (
            <input
              {...control}
              type="checkbox"
              name="auto_send"
              checked={draft.auto_send}
              onChange={event => set({auto_send: event.target.checked})}
            />
          )}
        </SettingsRow>
        <SettingsRow label="Keep recordings" description="Off: the recording goes to your audio account for transcription and is then discarded.">
          {control => (
            <input
              {...control}
              type="checkbox"
              name="keep_audio"
              checked={draft.keep_audio}
              onChange={event => set({keep_audio: event.target.checked})}
            />
          )}
        </SettingsRow>
      </SettingsSection>
    </form>
      {dialog === 'download' && (
        <LicenseDialog
          mode="download"
          onClose={() => setDialog(null)}
          onCloseAutoFocus={restoreFocus}
          onConfirm={sha => attempt(() => downloadWoof(sha), 'Download started. This page shows its progress.')}
        />
      )}
      {dialog === 'accept' && (
        <LicenseDialog
          mode="accept"
          onClose={() => setDialog(null)}
          onCloseAutoFocus={restoreFocus}
          onConfirm={sha => attempt(() => saveVoiceSettings({accept_woof_license: sha}), 'License accepted')}
        />
      )}
      {dialog === 'remove' && (
        <RemoveDialog
          onClose={() => setDialog(null)}
          onCloseAutoFocus={restoreFocus}
          onConfirm={() => attempt(() => removeWoof(), 'Underdog Woof removed')}
        />
      )}
    </>
  );
}

export default function VoiceSection({admin, projectId}: {admin: boolean; projectId: string | null}) {
  const {data, loading, error: loadError, reload} = useManagerLoad(loadVoiceStatus);
  const mutation = useManagerMutation(reload);
  const {busy, error, notice, mutate} = mutation;
  const [live, setLive] = useState<VoiceStatus | null>(null);
  const latest = live ?? data;
  const downloading = !!latest?.engines.some(engine => engine.state === 'downloading');
  const dataRef = useRef(data);
  if (dataRef.current !== data) {
    dataRef.current = data;
    setLive(null);
  }
  usePolling(() => loadVoiceStatus(), {
    interval: 1500,
    enabled: downloading,
    backoff: false,
    immediate: false,
    onResult: setLive,
  });
  return (
    <ManagerPage
      section="voice"
      projectId={projectId}
      load={{data: latest, loading, busy, error: error || loadError, notice, reload}}
    >
      {latest && (
        <VoiceForm
          status={latest}
          admin={admin}
          busy={busy}
          save={changes => mutate(() => saveVoiceSettings(changes), 'Voice settings saved')}
          run={(action, message) => mutate(action, message)}
        />
      )}
    </ManagerPage>
  );
}
