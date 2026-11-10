import {useEffect, useRef, useState} from 'react';
import {Button} from '../components/ui/button';
import {Input} from '../components/ui/input';
import {Select} from '../components/ui/select';
import {gql, json, request, type Provider} from '../lib/api';
import {transfer} from '../lib/transfers';
import {MAX_RECORDING_BYTES} from '../lib/voice/recorder';
import {useVoiceRecorder} from '../lib/voice/use-recorder';
import {clearMediaIntent, mediaIntent} from '../lib/media-intent';
import './voice.css';
type VoiceArtifact = {id: string; content?: string; mime?: string};
const MAX_AUDIO = MAX_RECORDING_BYTES;
export default function VoiceControls({
  sessionId,
  projectId,
  ownerId,
  reply,
  onTranscript,
  visible = true,
}: {
  sessionId: string;
  projectId: string | null;
  ownerId?: string;
  reply: string;
  onTranscript: (text: string) => void;
  visible?: boolean;
}) {
  const [accounts, setAccounts] = useState<Provider[]>([]),
    [provider, setProvider] = useState(''),
    [transcribeModel, setTranscribeModel] = useState(''),
    [speechModel, setSpeechModel] = useState(''),
    [voice, setVoice] = useState('alloy'),
    [consent, setConsent] = useState(false),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(''),
    [audio, setAudio] = useState<VoiceArtifact | null>(null),
    [transcript, setTranscript] = useState(''),
    [playback, setPlayback] = useState('');
  const host = useRef<HTMLElement | null>(null);
  const epoch = useRef(0),
    playbackUrl = useRef('');
  useEffect(() => {
    ++epoch.current;
    const locked = () => {
      ++epoch.current;
      setBusy(false);
      setConsent(false);
      host.current?.querySelectorAll('audio').forEach(player => player.pause());
      if (playbackUrl.current) {
        URL.revokeObjectURL(playbackUrl.current);
        playbackUrl.current = '';
        setPlayback('');
      }
    };
    window.addEventListener('termx-locked', locked);
    return () => {
      window.removeEventListener('termx-locked', locked);
      ++epoch.current;
      if (playbackUrl.current) URL.revokeObjectURL(playbackUrl.current);
    };
  }, [sessionId]);
  useEffect(() => {
    if (!visible) return;
    let cancelled = false;
    const generation = epoch.current;
    gql<{agent_providers: Provider[]}>('{agent_providers{id name model capabilities secret_configured}}')
      .then(data => {
        if (!cancelled && epoch.current === generation)
          setAccounts(data.agent_providers.filter(row => row.secret_configured && row.capabilities.includes('audio')));
      })
      .catch(error => {
        if (!cancelled && epoch.current === generation) setError(String(error));
      });
    return () => {
      cancelled = true;
    };
  }, [sessionId, visible]);
  async function act(operation: () => Promise<void>) {
    const generation = epoch.current;
    setBusy(true);
    setError('');
    try {
      await operation();
    } catch (error) {
      if (epoch.current === generation) setError(error instanceof Error ? error.message : String(error));
    } finally {
      if (epoch.current === generation) setBusy(false);
    }
  }
  async function upload(blob: Blob) {
    if (!blob.size || blob.size > MAX_AUDIO) throw new Error('Choose a nonempty audio recording up to 10 MB.');
    const generation = epoch.current;
    const result = await transfer(
      '/api/media/inputs?name=' +
        encodeURIComponent('Voice input') +
        (projectId ? '&project_id=' + encodeURIComponent(projectId) : ''),
      {method: 'POST', body: blob, headers: {'Content-Type': blob.type || 'audio/webm'}},
    );
    const value = JSON.parse(await result.text()) as VoiceArtifact;
    if (epoch.current === generation) {
      setAudio(value);
      setTranscript('');
    }
  }
  const mic = useVoiceRecorder({
    enabled: visible,
    observe: host,
    onRecording: blob => void act(() => upload(blob)),
    onError: setError,
  });
  const recording = mic.phase === 'recording',
    capturePending = mic.phase === 'requesting';
  function capture() {
    if (recording) return mic.stop();
    setError('');
    void mic.start();
  }
  async function generate(operation: 'transcribe' | 'speak') {
    if (!consent || !provider) throw new Error('Choose an audio account and consent before running its capability.');
    const generation = epoch.current;
    const args = {
      project_id: projectId,
      operation,
      provider_id: provider,
      model: operation === 'transcribe' ? transcribeModel : speechModel,
      prompt: operation === 'speak' ? reply : '',
      voice,
      input_id: operation === 'transcribe' ? audio?.id : undefined,
      acknowledge_billing: true,
    };
    const intent = await mediaIntent(ownerId, projectId, args, 'voice-' + sessionId + '-' + operation);
    if (epoch.current !== generation) return;
    const output = await request<VoiceArtifact>('/api/media/generate', json('POST', {request_id: intent.id, ...args}));
    clearMediaIntent(intent.key, intent.id);
    if (epoch.current !== generation) return;
    if (operation === 'transcribe') {
      if (typeof output.content !== 'string')
        throw new Error('The transcription adapter did not return a text transcript.');
      setTranscript(output.content);
    } else {
      const blob = await transfer('/api/media/artifacts/' + output.id + '/export?format=original');
      if (epoch.current !== generation) return;
      if (playbackUrl.current) URL.revokeObjectURL(playbackUrl.current);
      playbackUrl.current = URL.createObjectURL(blob);
      setPlayback(playbackUrl.current);
    }
  }
  return (
    <section ref={host} className="voice-controls" aria-label="Voice controls">
      {recording && (
        <div className="voice-actions" role="status">
          <strong>Microphone recording · stops after 3 minutes</strong>
          <Button type="button" variant="outline" onClick={mic.stop}>
            Stop microphone
          </Button>
        </div>
      )}
      <>
        {capturePending && (
          <div className="voice-actions" role="status">
            <span>Waiting for microphone permission…</span>
            <Button type="button" variant="outline" onClick={mic.cancel}>
              Cancel microphone request
            </Button>
          </div>
        )}
      </>
      <details>
        <summary>Voice mode · dictation and spoken replies</summary>
        <p>
          Record or upload audio, review the transcript, then add it to this chat. Provider actions use the selected
          audio account.
        </p>
        {error && (
          <p className="inline-error" role="alert">
            {error}
          </p>
        )}
        <div className="voice-fields">
          <label>
            Audio account
            <Select
              aria-label="Voice account"
              value={provider}
              disabled={busy || recording || capturePending}
              onChange={event => {
                setProvider(event.target.value);
                setConsent(false);
              }}
            >
              <option value="">Choose an audio account</option>
              {accounts.map(account => (
                <option key={account.id} value={account.id}>
                  {account.name}
                </option>
              ))}
            </Select>
          </label>
          <label>
            Transcription model
            <Input
              aria-label="Transcription model"
              value={transcribeModel}
              disabled={busy}
              onChange={event => setTranscribeModel(event.target.value)}
            />
          </label>
          <label>
            Speech model
            <Input
              aria-label="Speech model"
              value={speechModel}
              disabled={busy}
              onChange={event => setSpeechModel(event.target.value)}
            />
          </label>
          <label>
            Speech voice
            <Input
              aria-label="Speech voice"
              value={voice}
              disabled={busy}
              onChange={event => setVoice(event.target.value)}
            />
          </label>
        </div>
        <label className="voice-consent">
          <span>
            <input
              type="checkbox"
              checked={consent}
              disabled={busy}
              onChange={event => setConsent(event.target.checked)}
            />{' '}
            Use this audio account; provider charges may apply.
          </span>
        </label>
        {!accounts.length && <p>Configure an entitled API account with audio capability in Models & engines.</p>}
        <div className="voice-actions">
          <Button
            type="button"
            variant="outline"
            disabled={busy || recording || capturePending || !visible}
            onClick={capture}
          >
            Record dictation
          </Button>
          <label>
            Upload audio
            <input
              type="file"
              accept="audio/*"
              disabled={busy || recording || capturePending}
              onChange={event => {
                const file = event.target.files?.[0];
                if (file) void act(() => upload(file));
                event.target.value = '';
              }}
            />
          </label>
          {audio && (
            <Button
              type="button"
              disabled={busy || recording || capturePending || !consent || !provider || !transcribeModel.trim()}
              onClick={() => void act(() => generate('transcribe'))}
            >
              Transcribe audio
            </Button>
          )}
        </div>
        {transcript && (
          <div className="voice-transcript">
            <label>
              Review transcript
              <textarea
                aria-label="Review voice transcript"
                value={transcript}
                onChange={event => setTranscript(event.target.value)}
              />
            </label>
            <Button
              type="button"
              disabled={!transcript.trim()}
              onClick={() => {
                onTranscript(transcript);
                setTranscript('');
              }}
            >
              Add transcript to composer
            </Button>
          </div>
        )}
        <div className="voice-actions">
          <Button
            type="button"
            variant="outline"
            disabled={
              busy || recording || capturePending || !consent || !provider || !speechModel.trim() || !reply.trim()
            }
            onClick={() => void act(() => generate('speak'))}
          >
            Read latest reply aloud
          </Button>
          {playback && <audio controls src={playback} aria-label="Spoken assistant reply" />}
        </div>
        {busy && (
          <p role="status">
            Audio operation in progress. An uncertain request retains its original request key for recovery.
          </p>
        )}
      </details>
    </section>
  );
}
