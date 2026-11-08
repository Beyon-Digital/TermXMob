import {useEffect, useMemo, useRef, useState, type ReactNode} from 'react';
import {LoaderCircle, Mic, Square} from 'lucide-react';
import {Button} from '../components/ui/button';
import {defaults, loadBindings, protectedSurface} from '../lib/keybindings';
import {formatShortcut} from '../lib/shortcut-label';
import {transfer} from '../lib/transfers';
import {SpeechPlayer, type PlayerState} from '../lib/voice/player';
import {PushToTalk} from '../lib/voice/push-to-talk';
import {saveVoiceSettings, type Attribution, type VoiceSettingsChange, type VoiceStatus} from '../lib/voice/settings';
import {useVoiceRecorder} from '../lib/voice/use-recorder';
import {useVoiceStatus} from '../lib/voice/use-voice-status';
import './composer-voice.css';

export interface ComposerVoiceOptions {
  ownerId?: string;
  sessionId?: string;
  /** False while the conversation is hidden or absent: recording and reading stop. */
  enabled: boolean;
  /** True while a live voice session is open: the realtime model already speaks the reply, so none is read twice. */
  speechSuspended?: boolean;
  /** The newest finished assistant reply; a change to a new id is read aloud when the owner turned that on. */
  reply: {id: string; text: string} | null;
  /** `sendNow` is true only when the owner chose to send after dictation. */
  onTranscript: (text: string, sendNow: boolean) => void;
  onOpenSettings?: () => void;
}

function shortcut(ownerId?: string) {
  try {
    return loadBindings(localStorage.getItem('termx-keybindings:' + (ownerId ?? ''))).voiceTalk;
  } catch {
    return defaults.voiceTalk;
  }
}

/** Names the one thing dictation is still missing, so the hint matches what the person has already done. */
function setupMessage(settings?: VoiceStatus['settings']) {
  if (!settings) return 'Dictation is not set up yet. Choose an audio account in Settings, Voice.';
  if (!settings.provider) return 'Dictation needs an audio account. Choose one in Settings, Voice.';
  if (!settings.transcribe_model) return 'Dictation needs a transcription model. Enter one in Settings, Voice.';
  if (settings.consent?.provider?.id !== settings.provider) return 'Dictation needs your consent to send recordings to this account. Allow it in Settings, Voice.';
  return 'Dictation is not set up yet. Check Settings, Voice.';
}

/**
 * Dictation and spoken replies for the composer. `mic` goes in the toolbar, `row` under the text box. Both read
 * the same state, so the hook returns them together.
 */
export function useComposerVoice({ownerId, sessionId, enabled, speechSuspended = false, reply, onTranscript, onOpenSettings}: ComposerVoiceOptions) {
  const {status, setStatus, error: statusError} = useVoiceStatus(enabled);
  const settings = status?.settings;
  const configured = !!status?.stt.configured;
  const [transcribing, setTranscribing] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [setupHint, setSetupHint] = useState(false);
  const [speech, setSpeech] = useState<PlayerState>('idle');
  const [credit, setCredit] = useState<Attribution | null>(null);
  const epoch = useRef(0);
  const sessionNow = useRef(sessionId);
  sessionNow.current = sessionId;
  const latest = useRef({settings, onTranscript});
  latest.current = {settings, onTranscript};
  const player = useMemo(() => new SpeechPlayer({onState: setSpeech, onCredit: setCredit, onNotice: setNotice}), []);

  async function transcribe(blob: Blob) {
    const generation = epoch.current;
    const session = sessionNow.current;
    setTranscribing(true);
    setError('');
    setNotice('');
    try {
      const response = await transfer('/api/voice/transcribe', {
        method: 'POST',
        body: blob,
        headers: {'Content-Type': (blob.type || 'audio/webm').split(';')[0]},
      });
      const text = String((JSON.parse(await response.text()) as {text?: string}).text ?? '').trim();
      if (generation !== epoch.current) return;
      if (session !== sessionNow.current) {
        setNotice('Dictation finished after you switched conversations, so it was not added.');
      } else if (!text) {
        setNotice('No speech was heard. Try again closer to the microphone.');
      } else {
        latest.current.onTranscript(text, !!latest.current.settings?.auto_send);
      }
    } catch (failure) {
      if (generation === epoch.current) setError(failure instanceof Error ? failure.message : String(failure));
    } finally {
      if (generation === epoch.current) setTranscribing(false);
    }
  }

  const mic = useVoiceRecorder({enabled, onRecording: blob => void transcribe(blob), onError: setError});
  const recording = mic.phase === 'recording';
  const requesting = mic.phase === 'requesting';

  function startRecording() {
    if (mic.phase !== 'idle' || transcribing) return;
    if (!configured) return setSetupHint(true);
    setSetupHint(false);
    setError('');
    setNotice('');
    player.stop();
    void mic.start();
  }
  function press() {
    if (recording) mic.stop();
    else if (requesting) mic.cancel();
    else startRecording();
  }

  const actions = useRef({startRecording, stop: mic.stop, recording});
  actions.current = {startRecording, stop: mic.stop, recording};
  useEffect(() => {
    if (!enabled) return;
    const talk = new PushToTalk({
      binding: () => shortcut(ownerId),
      isRecording: () => actions.current.recording,
      start: () => actions.current.startRecording(),
      stop: () => actions.current.stop(),
    });
    const handle = (method: 'keydown' | 'keyup') => (event: KeyboardEvent) => {
      if (event.isComposing || protectedSurface(event.target)) return;
      if (talk[method](event)) event.preventDefault();
    };
    const down = handle('keydown');
    const up = handle('keyup');
    window.addEventListener('keydown', down);
    window.addEventListener('keyup', up);
    const lost = () => talk.blur();
    window.addEventListener('blur', lost);
    return () => {
      window.removeEventListener('keydown', down);
      window.removeEventListener('keyup', up);
      window.removeEventListener('blur', lost);
    };
  }, [enabled, ownerId]);

  useEffect(() => {
    if (!enabled || speechSuspended) player.stop();
  }, [enabled, speechSuspended, player]);
  // Escape ends a recording and drops it, so the microphone is never left on after the key the person pressed to get out.
  useEffect(() => {
    if (!recording && !requesting) return;
    const escape = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !event.defaultPrevented) mic.cancel();
    };
    window.addEventListener('keydown', escape);
    return () => window.removeEventListener('keydown', escape);
  }, [recording, requesting, mic]);
  useEffect(() => {
    const stopAll = () => {
      epoch.current++;
      player.stop();
      setTranscribing(false);
      setError('');
      setNotice('');
    };
    window.addEventListener('termx-locked', stopAll);
    return () => {
      window.removeEventListener('termx-locked', stopAll);
      epoch.current++;
      player.stop();
    };
  }, [player]);

  // The reply on screen when a conversation opens is history; only a newer one is read aloud.
  const seen = useRef<{session?: string; id: string | null}>({id: null});
  useEffect(() => {
    const id = reply?.id ?? null;
    if (seen.current.session !== sessionId) {
      seen.current = {session: sessionId, id};
      return;
    }
    if (id === seen.current.id) return;
    seen.current.id = id;
    if (!id || !reply?.text || !settings?.auto_speak || speechSuspended) return;
    void player.speak(reply.text, {
      engine: settings.engine === 'browser' ? 'browser' : undefined,
      voice: settings.voice || undefined,
    });
  }, [reply?.id, reply?.text, sessionId, settings?.auto_speak, settings?.engine, settings?.voice, speechSuspended, player]);

  async function toggle(change: VoiceSettingsChange) {
    const previous = status;
    setStatus(old => (old ? ({...old, settings: {...old.settings, ...change}} as VoiceStatus) : old));
    try {
      setStatus(await saveVoiceSettings(change));
    } catch (failure) {
      setStatus(previous);
      setError(failure instanceof Error ? failure.message : String(failure));
    }
  }

  const loading = enabled && !status && !statusError;
  const label = recording ? 'Stop dictation' : requesting ? 'Cancel microphone request' : transcribing ? 'Transcribing' : 'Start dictation';
  const mine = formatShortcut(shortcut(ownerId));
  const button: ReactNode = (
    <Button
      type="button"
      size="icon"
      variant="ghost"
      className={recording ? 'composer-mic is-recording' : 'composer-mic'}
      aria-label={label}
      title={recording ? 'Stop dictation' : `Dictate. Hold ${mine} to talk, or tap it to start and stop.`}
      // Not `disabled` while transcribing: a keyboard user who just stopped dictation would lose focus.
      aria-disabled={transcribing || undefined}
      disabled={!enabled || loading}
      onClick={press}
    >
      {transcribing ? <LoaderCircle className="spin" size={16} /> : recording ? <Square size={14} /> : <Mic size={17} />}
    </Button>
  );

  const showToggles = !!settings && (configured || settings.auto_speak || settings.engine !== 'browser');
  const hint = setupHint && !configured ? setupMessage(settings) : '';
  const statusText = recording
    ? 'Recording. Press the microphone button or release the shortcut to stop.'
    : requesting
      ? 'Waiting for microphone permission…'
      : transcribing
        ? 'Transcribing…'
        : speech === 'loading'
          ? 'Preparing the spoken reply…'
          : speech === 'speaking'
            ? 'Reading the reply aloud.'
            : notice || hint;
  const visible = showToggles || setupHint || !!error || !!statusText;
  const row: ReactNode = visible ? (
    <div className="composer-voice-row">
      <div className="composer-voice-line">
        {recording && (
          <span className="voice-meter" aria-hidden="true">
            <span style={{transform: `scaleX(${Math.max(0.04, mic.level)})`}} />
          </span>
        )}
        <span role="status" className="composer-voice-status">
          {statusText}
        </span>
        {speech !== 'idle' && (
          <Button type="button" size="sm" variant="ghost" onClick={() => player.stop()}>
            Stop reading
          </Button>
        )}
        {credit && (
          <a className="voice-credit" href={credit.url} target="_blank" rel="noopener noreferrer">
            {credit.text}
          </a>
        )}
      </div>
      {error && (
        <p className="composer-voice-error" role="alert">
          {error}
        </p>
      )}
      {hint && onOpenSettings && (
        <p className="composer-voice-hint">
          <Button type="button" size="sm" variant="ghost" onClick={onOpenSettings}>
            Open voice settings
          </Button>
        </p>
      )}
      {showToggles && settings && (
        <div className="composer-voice-toggles">
          <label>
            <input type="checkbox" checked={settings.auto_send} onChange={event => void toggle({auto_send: event.target.checked})} />
            Send after dictation
          </label>
          <label>
            <input type="checkbox" checked={settings.auto_speak} onChange={event => void toggle({auto_speak: event.target.checked})} />
            Read replies aloud
          </label>
        </div>
      )}
    </div>
  ) : null;

  return {mic: button, row};
}
