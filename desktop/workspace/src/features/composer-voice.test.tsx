import {afterEach, beforeEach, expect, it, vi} from 'vitest';
import {act, cleanup, fireEvent, render, screen, waitFor} from '@testing-library/react';
import {expectNoAxeViolations} from '../test-axe';
import {useComposerVoice} from './composer-voice';
import {loadVoiceStatus, saveVoiceSettings, type VoiceStatus} from '../lib/voice/settings';
import {speechEvents, type SpeechEvent} from '../lib/voice/speech-stream';
import {transfer} from '../lib/transfers';
import {claimMicrophone, microphoneHeldBy} from '../lib/voice/microphone';

vi.mock('../lib/voice/settings', () => ({
  loadVoiceStatus: vi.fn(),
  saveVoiceSettings: vi.fn(),
  onVoiceChange: () => () => {},
}));
vi.mock('../lib/voice/speech-stream', () => ({speechEvents: vi.fn()}));
vi.mock('../lib/transfers', () => ({transfer: vi.fn()}));

const load = vi.mocked(loadVoiceStatus);
const save = vi.mocked(saveVoiceSettings);
const events = vi.mocked(speechEvents);
const upload = vi.mocked(transfer);
const body = (value: unknown) => ({text: async () => JSON.stringify(value)}) as Blob;

const settings = {
  engine: 'browser' as const,
  provider: 'speech',
  model: 'tts-1',
  transcribe_model: 'whisper-1',
  voice: '',
  auto_send: false,
  auto_speak: false,
  cloud_fallback: false,
  keep_audio: false,
  consent: {provider: {id: 'speech', at: 1}},
};
function status(overrides: Partial<VoiceStatus['settings']> = {}, configured = true): VoiceStatus {
  return {
    available: true,
    settings: {...settings, ...overrides},
    engines: [],
    stt: {configured, provider: configured ? 'speech' : null},
  };
}

class FakeRecorder {
  static isTypeSupported() {
    return true;
  }
  state = 'inactive';
  mimeType = 'audio/webm';
  ondataavailable: ((event: {data: Blob}) => void) | null = null;
  onstop: (() => void) | null = null;
  start() {
    this.state = 'recording';
  }
  stop() {
    this.state = 'inactive';
    this.ondataavailable?.({data: new Blob(['voice'])});
    this.onstop?.();
  }
}

type Reply = {id: string; text: string} | null;
function Harness(props: {
  reply?: Reply;
  sessionId?: string;
  enabled?: boolean;
  speechSuspended?: boolean;
  onTranscript: (text: string, send: boolean) => void;
  onOpenSettings?: () => void;
}) {
  const voice = useComposerVoice({
    ownerId: 'owner',
    sessionId: props.sessionId ?? 'chat-a',
    enabled: props.enabled ?? true,
    speechSuspended: props.speechSuspended,
    reply: props.reply ?? null,
    onTranscript: props.onTranscript,
    onOpenSettings: props.onOpenSettings,
  });
  return (
    <div>
      {voice.mic}
      {voice.row}
    </div>
  );
}

const onTranscript = vi.fn();
let getUserMedia: ReturnType<typeof vi.fn>;
let spoken: string[];

beforeEach(() => {
  localStorage.clear();
  onTranscript.mockReset();
  load.mockReset().mockResolvedValue(status());
  save.mockReset();
  events.mockReset();
  upload.mockReset().mockResolvedValue(body({text: 'hello world', artifact_id: null}));
  getUserMedia = vi.fn().mockResolvedValue({getTracks: () => [{stop: vi.fn()}]});
  vi.stubGlobal('navigator', {mediaDevices: {getUserMedia}});
  vi.stubGlobal('MediaRecorder', FakeRecorder);
  spoken = [];
  vi.stubGlobal('SpeechSynthesisUtterance', class {
    onend: (() => void) | null = null;
    onerror: (() => void) | null = null;
    constructor(public text: string) {}
  });
  vi.stubGlobal('speechSynthesis', {
    speak: (utterance: {text: string; onend: () => void}) => {
      spoken.push(utterance.text);
      queueMicrotask(() => utterance.onend());
    },
    cancel: vi.fn(),
  });
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

async function ready() {
  const button = await screen.findByRole('button', {name: 'Start dictation'});
  await waitFor(() => expect(button).toBeEnabled());
  return button;
}

it('says how to set up dictation instead of asking for the microphone when no account is chosen', async () => {
  load.mockResolvedValue(status({consent: {}, provider: null}, false));
  const open = vi.fn();
  const view = render(<Harness onTranscript={onTranscript} onOpenSettings={open} />);
  fireEvent.click(await ready());
  expect(getUserMedia).not.toHaveBeenCalled();
  // The hint is announced: it lives in the status region.
  expect(await screen.findByRole('status')).toHaveTextContent(/needs an audio account/i);
  fireEvent.click(screen.getByRole('button', {name: 'Open voice settings'}));
  expect(open).toHaveBeenCalledOnce();
  await expectNoAxeViolations(view.container);
});

it.each([
  [{provider: 'speech', transcribe_model: '', consent: {provider: {id: 'speech', at: 1}}}, /transcription model/i],
  [{provider: 'speech', transcribe_model: 'whisper-1', consent: {}}, /consent to send recordings/i],
])('names the missing consent or model instead of the account when only that is missing', async (override, message) => {
  load.mockResolvedValue(status(override as never, false));
  render(<Harness onTranscript={onTranscript} />);
  fireEvent.click(await ready());
  expect(await screen.findByRole('status')).toHaveTextContent(message);
});

it('stops and discards a recording on Escape', async () => {
  render(<Harness onTranscript={onTranscript} />);
  fireEvent.click(await ready());
  await screen.findByRole('button', {name: 'Stop dictation'});
  fireEvent.keyDown(window, {key: 'Escape'});
  expect(await screen.findByRole('button', {name: 'Start dictation'})).toBeEnabled();
  expect(upload).not.toHaveBeenCalled();
  expect(onTranscript).not.toHaveBeenCalled();
});

it('stops a dictation in progress when live voice takes the microphone, and says so', async () => {
  render(<Harness onTranscript={onTranscript} />);
  fireEvent.click(await ready());
  await screen.findByRole('button', {name: 'Stop dictation'});
  let claim: ReturnType<typeof claimMicrophone> = null;
  act(() => {
    claim = claimMicrophone('live', () => {});
  });
  expect(claim).not.toBeNull();
  expect(await screen.findByRole('button', {name: 'Start dictation'})).toBeEnabled();
  expect(screen.getByRole('status')).toHaveTextContent(/live voice/i);
  expect(upload).not.toHaveBeenCalled();
  expect(onTranscript).not.toHaveBeenCalled();
  (claim as unknown as {release: () => void}).release();
});

it('refuses to start dictation while live voice holds the microphone, then works again once it is released', async () => {
  const live = claimMicrophone('live', () => {})!;
  render(<Harness onTranscript={onTranscript} />);
  fireEvent.click(await ready());
  expect(getUserMedia).not.toHaveBeenCalled();
  expect(await screen.findByRole('status')).toHaveTextContent(/end live voice to dictate/i);
  act(() => live.release());
  fireEvent.click(screen.getByRole('button', {name: 'Start dictation'}));
  await screen.findByRole('button', {name: 'Stop dictation'});
  expect(getUserMedia).toHaveBeenCalledOnce();
});

it('gives the microphone back once a dictation has finished', async () => {
  render(<Harness onTranscript={onTranscript} />);
  fireEvent.click(await ready());
  fireEvent.click(await screen.findByRole('button', {name: 'Stop dictation'}));
  await waitFor(() => expect(onTranscript).toHaveBeenCalled());
  expect(microphoneHeldBy()).toBeNull();
});

it('does not read the reply aloud while a live voice session is open', async () => {
  load.mockResolvedValue(status({auto_speak: true}));
  const view = render(<Harness reply={{id: 'task-1', text: 'Old.'}} speechSuspended onTranscript={onTranscript} />);
  await ready();
  view.rerender(<Harness reply={{id: 'task-2', text: 'Live reply.'}} speechSuspended onTranscript={onTranscript} />);
  await new Promise(done => setTimeout(done, 30));
  expect(spoken).toEqual([]);
});

it('keeps the microphone button focusable while the recording is transcribed', async () => {
  let finish: (value: unknown) => void = () => {};
  upload.mockImplementation(() => new Promise(resolve => (finish = resolve)) as never);
  render(<Harness onTranscript={onTranscript} />);
  fireEvent.click(await ready());
  const stop = await screen.findByRole('button', {name: 'Stop dictation'});
  stop.focus();
  fireEvent.click(stop);
  const busy = await screen.findByRole('button', {name: 'Transcribing'});
  expect(busy).toHaveAttribute('aria-disabled', 'true');
  expect(busy).not.toBeDisabled();
  expect(busy).toHaveFocus();
  fireEvent.click(busy);
  expect(getUserMedia).toHaveBeenCalledOnce();  // pressing it while it works starts nothing
  await act(async () => finish({text: async () => JSON.stringify({text: 'done'})}));
  await waitFor(() => expect(onTranscript).toHaveBeenCalledWith('done', false));
});

it('records, shows the level, transcribes once and hands the text to the composer', async () => {
  const view = render(<Harness onTranscript={onTranscript} />);
  fireEvent.click(await ready());
  const stop = await screen.findByRole('button', {name: 'Stop dictation'});
  expect(stop).not.toHaveAttribute('aria-pressed');
  expect(screen.getByRole('status')).toHaveTextContent(/recording/i);
  await expectNoAxeViolations(view.container);
  fireEvent.click(stop);
  await waitFor(() => expect(onTranscript).toHaveBeenCalledExactlyOnceWith('hello world', false));
  expect(upload).toHaveBeenCalledOnce();
  expect(upload.mock.calls[0][0]).toBe('/api/voice/transcribe');
  expect(upload.mock.calls[0][1]?.headers).toEqual({'Content-Type': 'audio/webm'});
  expect(screen.getByRole('button', {name: 'Start dictation'})).toBeEnabled();
});

it('sends straight away only when the owner turned on send after dictation', async () => {
  load.mockResolvedValue(status({auto_send: true}));
  render(<Harness onTranscript={onTranscript} />);
  fireEvent.click(await ready());
  fireEvent.click(await screen.findByRole('button', {name: 'Stop dictation'}));
  await waitFor(() => expect(onTranscript).toHaveBeenCalledWith('hello world', true));
});

it('shows a failed transcription with what to do next and lets the owner try again', async () => {
  upload.mockRejectedValueOnce(new Error('Choose an API account and agree to send audio to it first'));
  render(<Harness onTranscript={onTranscript} />);
  fireEvent.click(await ready());
  fireEvent.click(await screen.findByRole('button', {name: 'Stop dictation'}));
  expect(await screen.findByRole('alert')).toHaveTextContent(/agree to send audio/i);
  expect(onTranscript).not.toHaveBeenCalled();
  expect(screen.getByRole('button', {name: 'Start dictation'})).toBeEnabled();
});

it('discards a transcript that arrives after the conversation changed or the workspace locked', async () => {
  let finish!: (value: Blob) => void;
  upload.mockImplementationOnce(() => new Promise<Blob>(resolve => (finish = resolve)));
  const view = render(<Harness onTranscript={onTranscript} />);
  fireEvent.click(await ready());
  fireEvent.click(await screen.findByRole('button', {name: 'Stop dictation'}));
  await waitFor(() => expect(upload).toHaveBeenCalled());
  view.rerender(<Harness onTranscript={onTranscript} sessionId="chat-b" />);
  await act(async () => finish(body({text: 'late', artifact_id: null})));
  expect(onTranscript).not.toHaveBeenCalled();
  expect(await screen.findByText(/switched conversations/i)).toBeInTheDocument();
});

it('records while the shortcut is held and stops when it is released', async () => {
  render(<Harness onTranscript={onTranscript} />);
  await ready();
  fireEvent.keyDown(window, {key: 'M', ctrlKey: true, shiftKey: true});
  await screen.findByRole('button', {name: 'Stop dictation'});
  await new Promise(done => setTimeout(done, 400));
  fireEvent.keyUp(window, {key: 'm'});
  await waitFor(() => expect(onTranscript).toHaveBeenCalledWith('hello world', false));
});

it('uses a rebound shortcut from the saved keyboard settings', async () => {
  localStorage.setItem('termx-keybindings:owner', JSON.stringify({voiceTalk: 'Mod+Shift+K'}));
  render(<Harness onTranscript={onTranscript} />);
  await ready();
  fireEvent.keyDown(window, {key: 'M', ctrlKey: true, shiftKey: true});
  expect(getUserMedia).not.toHaveBeenCalled();
  fireEvent.keyDown(window, {key: 'K', ctrlKey: true, shiftKey: true});
  await waitFor(() => expect(getUserMedia).toHaveBeenCalledOnce());
});

it('reads a new reply aloud only when the owner turned that on, and never the existing history', async () => {
  load.mockResolvedValue(status({auto_speak: true}));
  const view = render(<Harness reply={{id: 'task-1', text: 'Old reply.'}} onTranscript={onTranscript} />);
  await ready();
  await new Promise(done => setTimeout(done, 20));
  expect(spoken).toEqual([]);
  view.rerender(<Harness reply={{id: 'task-2', text: 'New reply.'}} onTranscript={onTranscript} />);
  await waitFor(() => expect(spoken).toEqual(['New reply.']));
});

it('does not read replies aloud when the setting is off or the conversation changes', async () => {
  const view = render(<Harness reply={{id: 'task-1', text: 'Old.'}} onTranscript={onTranscript} />);
  await ready();
  view.rerender(<Harness reply={{id: 'task-2', text: 'Quiet.'}} onTranscript={onTranscript} />);
  await new Promise(done => setTimeout(done, 20));
  expect(spoken).toEqual([]);
  cleanup();
  load.mockResolvedValue(status({auto_speak: true}));
  const other = render(<Harness reply={{id: 'task-1', text: 'A.'}} onTranscript={onTranscript} />);
  await ready();
  other.rerender(<Harness sessionId="chat-b" reply={{id: 'task-9', text: 'B reply from another chat.'}} onTranscript={onTranscript} />);
  await new Promise(done => setTimeout(done, 20));
  expect(spoken).toEqual([]);
});

it('shows the Woof credit with a link while it speaks and removes it afterwards', async () => {
  load.mockResolvedValue(status({auto_speak: true, engine: 'woof'}));
  let release!: () => void;
  events.mockImplementation(async function* () {
    yield {
      event: 'start',
      engine: 'woof',
      voice: 'juno',
      sample_rate: 24000,
      attribution: {text: 'Voice by Underdog Woof', url: 'https://underdog.ai'},
    } as SpeechEvent;
    await new Promise<void>(done => (release = done));
    yield {event: 'end'} as SpeechEvent;
  });
  vi.stubGlobal('AudioContext', class {
    currentTime = 0;
    destination = {};
    resume() {}
    close() {
      return Promise.resolve();
    }
  });
  const view = render(<Harness reply={null} onTranscript={onTranscript} />);
  await ready();
  view.rerender(<Harness reply={{id: 't', text: 'Hello from Woof.'}} onTranscript={onTranscript} />);
  const credit = await screen.findByRole('link', {name: 'Voice by Underdog Woof'});
  expect(credit).toHaveAttribute('href', 'https://underdog.ai');
  expect(credit).toHaveAttribute('rel', expect.stringContaining('noopener'));
  await expectNoAxeViolations(view.container);
  expect(screen.getByRole('button', {name: 'Stop reading'})).toBeInTheDocument();
  await act(async () => release());
  await waitFor(() => expect(screen.queryByRole('link', {name: 'Voice by Underdog Woof'})).not.toBeInTheDocument());
});

it('saves the two toggles for the owner', async () => {
  save.mockResolvedValue(status({auto_speak: true}));
  render(<Harness onTranscript={onTranscript} />);
  await ready();
  fireEvent.click(screen.getByRole('checkbox', {name: 'Read replies aloud'}));
  await waitFor(() => expect(save).toHaveBeenCalledWith({auto_speak: true}));
  expect(screen.getByRole('checkbox', {name: 'Read replies aloud'})).toBeChecked();
  fireEvent.click(screen.getByRole('checkbox', {name: 'Send after dictation'}));
  await waitFor(() => expect(save).toHaveBeenCalledWith({auto_send: true}));
});

it('stops reading and releases the microphone when the workspace locks', async () => {
  const stop = vi.fn();
  getUserMedia.mockResolvedValue({getTracks: () => [{stop}]});
  render(<Harness onTranscript={onTranscript} />);
  fireEvent.click(await ready());
  await screen.findByRole('button', {name: 'Stop dictation'});
  fireEvent(window, new Event('termx-locked'));
  await waitFor(() => expect(screen.getByRole('button', {name: 'Start dictation'})).toBeInTheDocument());
  expect(stop).toHaveBeenCalled();
  expect(upload).not.toHaveBeenCalled();
});
