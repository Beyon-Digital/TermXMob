import {useEffect, useRef, useState, type ReactNode} from 'react';
import {AudioLines, MicOff, Square} from 'lucide-react';
import {Button} from '../components/ui/button';
import {nativeWorkspace} from '../lib/native';
import {useSystemReducedMotion} from '../lib/use-system-reduced-motion';
import {LiveVoiceClient, type LiveState} from '../lib/voice/live';
import {hostOf, isLocal, saveVoiceSettings} from '../lib/voice/settings';
import {useVoiceStatus} from '../lib/voice/use-voice-status';
import './composer-voice.css';
import './live-voice.css';

type Phase = 'closed' | 'setup' | 'consent' | 'live' | 'ended';
interface Line {
  role: 'user' | 'assistant';
  text: string;
}
const MAX_LINES = 4;
const LABEL: Record<LiveState, string> = {
  idle: '',
  connecting: 'Connecting…',
  listening: 'Listening',
  thinking: 'Working on it',
  speaking: 'Speaking',
  ended: '',
};

export interface LiveVoiceOptions {
  sessionId?: string;
  /** False while the conversation is hidden or absent: a running session ends. */
  enabled: boolean;
  onOpenSettings?: () => void;
}

/**
 * A live spoken conversation with the agent. `button` goes in the composer toolbar and `panel` above the message
 * box. The always-mounted status region is the only live region; the level meter is decoration and is left out
 * entirely when the system asks for reduced motion.
 */
export function useLiveVoice({sessionId, enabled, onOpenSettings}: LiveVoiceOptions) {
  const {status, setStatus} = useVoiceStatus(enabled && !nativeWorkspace());
  const reduced = useSystemReducedMotion();
  const [phase, setPhase] = useState<Phase>('closed');
  const [state, setState] = useState<LiveState>('idle');
  const [level, setLevel] = useState(0);
  const [muted, setMuted] = useState(false);
  const [lines, setLines] = useState<Line[]>([]);
  const [notice, setNotice] = useState('');
  const [message, setMessage] = useState('');
  const [agreed, setAgreed] = useState(false);
  const [starting, setStarting] = useState(false);
  const client = useRef<LiveVoiceClient | null>(null);
  const panelRef = useRef<HTMLElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const firstRender = useRef(true);
  const [armed, setArmed] = useState(false);
  const session = useRef(sessionId);
  session.current = sessionId;

  const live = status?.live;
  const settings = status?.settings;
  const account = live?.accounts.find(row => row.id === settings?.live_provider);
  const host = hostOf(account?.base_url);
  const ready = !!live?.configured;
  const acknowledged = !!live?.acknowledged;

  function stop() {
    client.current?.end('user');
  }
  /** Ends the session without reporting it: the panel it would report to is gone or belongs to another conversation. */
  const detach = useRef(() => {});
  detach.current = () => {
    const old = client.current;
    client.current = null;
    old?.end('user');
  };
  useEffect(() => {
    if (!enabled) detach.current();
  }, [enabled]);
  useEffect(() => () => detach.current(), []);
  const lastSession = useRef(sessionId);
  useEffect(() => {
    if (lastSession.current !== sessionId) {
      lastSession.current = sessionId;
      detach.current();
      setPhase('closed');
    }
  }, [sessionId]);

  async function begin() {
    const target = session.current;
    if (!target || starting) return;
    setStarting(true);
    setNotice('');
    setMessage('');
    try {
      if (!acknowledged) setStatus(await saveVoiceSettings({consent_live: true}));
      setLines([]);
      setMuted(false);
      setState('connecting');
      setPhase('live');
      const next = new LiveVoiceClient({
        onState: setState,
        onLevel: setLevel,
        onTranscript: (role, text) => text && setLines(old => [...old, {role, text}].slice(-MAX_LINES)),
        onNotice: setNotice,
        onEnded: (_reason, detail) => {
          if (client.current !== next) return;
          client.current = null;
          setMessage(detail);
          setPhase('ended');
        },
      });
      client.current = next;
      await next.start(target);
    } catch (failure) {
      setMessage(failure instanceof Error ? failure.message : String(failure));
      setPhase('ended');
    } finally {
      setStarting(false);
    }
  }

  // Keyboard users keep their place: a new panel takes focus, and a panel that goes away hands it back to the toolbar button.
  // Focus is only moved when it was on the panel, on the button, or lost; typing in the composer is never interrupted.
  useEffect(() => {
    if (firstRender.current) {
      firstRender.current = false;
      return;
    }
    const panel = panelRef.current;
    const active = document.activeElement;
    const lost = !active || active === document.body || active === buttonRef.current || !!panel?.contains(active);
    if (!lost) return;
    if (phase === 'setup' || phase === 'consent' || phase === 'ended') {
      panel?.querySelector<HTMLElement>('input:not(:disabled), button:not(:disabled)')?.focus();
    } else {
      buttonRef.current?.focus();
    }
  }, [phase]);

  function press() {
    setArmed(true);
    if (phase === 'live') return stop();
    if (!ready) return setPhase(phase === 'setup' ? 'closed' : 'setup');
    if (acknowledged) return void begin();
    setAgreed(false);
    setPhase(phase === 'consent' ? 'closed' : 'consent');
  }

  const active = phase === 'live';
  const button: ReactNode = nativeWorkspace() ? null : (
    <Button
      type="button"
      size="icon"
      variant="ghost"
      className={active ? 'live-voice-button is-live' : 'live-voice-button'}
      ref={buttonRef}
      aria-label="Live voice"
      aria-pressed={active}
      title={active ? 'Live voice is on. Press to end it.' : 'Talk with the agent out loud'}
      disabled={!enabled || !status}
      onClick={press}
    >
      {active ? <Square size={14} /> : <AudioLines size={17} />}
    </Button>
  );

  const statusText = muted && state === 'listening' ? 'Muted. The microphone is off.' : LABEL[state];
  let body: ReactNode = null;
  if (phase === 'setup') {
    body = (
      <>
        <p className="live-voice-text">
          Live voice needs an API account that supports realtime audio, plus a model name. Choose them in Settings, Voice.
        </p>
        <div className="live-voice-actions">
          {onOpenSettings && (
            <Button type="button" size="sm" variant="secondary" onClick={onOpenSettings}>
              Open voice settings
            </Button>
          )}
          <Button type="button" size="sm" variant="ghost" onClick={() => setPhase('closed')}>
            Close
          </Button>
        </div>
      </>
    );
  } else if (phase === 'consent') {
    const where = host && isLocal(host.hostname) ? `${account?.name} on this device (${host.host})` : `${account?.name ?? 'your account'} (${host?.host ?? 'unknown host'})`;
    body = (
      <>
        <label className="live-voice-consent">
          <input type="checkbox" checked={agreed} onChange={event => setAgreed(event.target.checked)} />
          <span>
            Send my voice and the agent's answers to {where} while live voice runs. The answers can include file contents and
            command output. The provider may charge for this. TermX does not keep the audio.
          </span>
        </label>
        <div className="live-voice-actions">
          <Button type="button" size="sm" disabled={!agreed || starting} onClick={() => void begin()}>
            Start live voice
          </Button>
          <Button type="button" size="sm" variant="ghost" onClick={() => setPhase('closed')}>
            Cancel
          </Button>
        </div>
      </>
    );
  } else if (phase === 'live') {
    const talking = state === 'speaking' || state === 'thinking';
    body = (
      <>
        <div className="live-voice-line">
          {!reduced && (
            <span className="voice-meter" aria-hidden="true">
              <span style={{transform: `scaleX(${Math.max(0.04, muted ? 0 : level)})`}} />
            </span>
          )}
          <p className="live-voice-status" aria-hidden="true">
            {statusText}
          </p>
        </div>
        {lines.length > 0 && (
          <ul className="live-voice-lines" aria-label="Live transcript">
            {lines.map((line, index) => (
              <li key={index}>
                <span>{line.role === 'user' ? 'You' : 'Agent'}</span> {line.text}
              </li>
            ))}
          </ul>
        )}
        {notice && (
          <p className="live-voice-notice" role="alert">
            {notice}
          </p>
        )}
        <div className="live-voice-actions">
          <Button
            type="button"
            size="sm"
            variant="secondary"
            aria-pressed={muted}
            onClick={() => {
              client.current?.mute(!muted);
              setMuted(!muted);
            }}
          >
            <MicOff size={14} />
            Mute microphone
          </Button>
          <Button type="button" size="sm" variant="secondary" disabled={!talking} onClick={() => client.current?.interrupt()}>
            Interrupt
          </Button>
          <Button type="button" size="sm" variant="ghost" onClick={stop}>
            End live voice
          </Button>
        </div>
        <p className="live-voice-hint">Ends after 10 minutes, or after 60 seconds without speech. Approvals still happen in the app.</p>
      </>
    );
  } else if (phase === 'ended') {
    body = (
      <>
        <p className="live-voice-status" aria-hidden="true">
          {message}
        </p>
        <div className="live-voice-actions">
          <Button type="button" size="sm" disabled={starting || !ready} onClick={() => void begin()}>
            Start live voice
          </Button>
          <Button type="button" size="sm" variant="ghost" onClick={() => setPhase('closed')}>
            Close
          </Button>
        </div>
      </>
    );
  }
  // Once the person has pressed the button, one status region stays mounted so each message after the first is announced.
  // It is not mounted before that, so an idle chat has no extra live region.
  const announcement = phase === 'live' ? statusText : phase === 'ended' ? message : '';
  const panel: ReactNode = (
    <div className="live-voice-host">
      {armed && (
        <p role="status" className="live-voice-announce">
          {announcement}
        </p>
      )}
      {body && (
        <section className="live-voice-panel" aria-label="Live voice" ref={panelRef}>
          {body}
        </section>
      )}
    </div>
  );
  return {button, panel, active};
}
