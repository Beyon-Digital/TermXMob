import {afterEach, beforeEach, expect, it, vi} from 'vitest';
import {cleanup, fireEvent, render, screen, waitFor} from '@testing-library/react';
import Chat from './Chat';
import {draftWrites, sessionDrafts, submittingDrafts, draftSubmissionOwners} from '../lib/drafts';
import {gql, request, type Session} from '../lib/api';
import {transfer} from '../lib/transfers';

vi.mock('../lib/api', () => ({
  gql: vi.fn(),
  request: vi.fn(),
  json: (method: string, value: unknown) => ({method, body: JSON.stringify(value)}),
}));
vi.mock('../lib/transfers', () => ({transfer: vi.fn()}));

const providers = [{id: 'fixture', name: 'Fixture account', model: 'm', secret_configured: true, capabilities: ['functions']}];
const session: Session = {
  id: 'voice-chat', title: 'Voice chat', engine: 'internal', mode: 'ask', pinned: false, archived: false,
  draft_text: '', revision: 1, updated_at: 1, scroll: 0, turns: [], provider_id: 'fixture', model: 'm',
} as Session;

function voiceStatus(autoSend: boolean) {
  return {
    voice_status: {
      available: true,
      settings: {
        engine: 'browser', provider: 'speech', model: 'tts', transcribe_model: 'whisper', voice: '', auto_send: autoSend,
        auto_speak: false, cloud_fallback: false, keep_audio: false, consent: {provider: {id: 'speech', at: 1}},
      },
      engines: [],
      stt: {configured: true, provider: 'speech'},
    },
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

beforeEach(() => {
  vi.mocked(request).mockImplementation(async (path: string) => (path.includes('available-extensions') ? ({extensions: []} as never) : ({...session} as never)));
  vi.mocked(transfer).mockResolvedValue({text: async () => JSON.stringify({text: 'check the build', artifact_id: null})} as Blob);
  vi.stubGlobal('navigator', {mediaDevices: {getUserMedia: vi.fn().mockResolvedValue({getTracks: () => [{stop: vi.fn()}]})}});
  vi.stubGlobal('MediaRecorder', FakeRecorder);
});
afterEach(() => {
  cleanup();
  sessionDrafts.clear();
  draftWrites.clear();
  submittingDrafts.clear();
  draftSubmissionOwners.clear();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

const props = {engines: [], providers, onSelect: () => {}, onRefresh: () => {}, onError: vi.fn()};

async function dictate() {
  const start = await screen.findByRole('button', {name: 'Start dictation'});
  await waitFor(() => expect(start).toBeEnabled());
  fireEvent.click(start);
  fireEvent.click(await screen.findByRole('button', {name: 'Stop dictation'}));
}

it('puts the transcript in the message box for review without sending it', async () => {
  vi.mocked(gql).mockResolvedValue(voiceStatus(false) as never);
  render(<Chat session={session} {...props} />);
  await dictate();
  await waitFor(() => expect(screen.getByLabelText('Message')).toHaveValue('check the build'));
  expect(vi.mocked(request).mock.calls.some(([path]) => String(path).endsWith('/turns'))).toBe(false);
});

it('sends the transcript only when the owner chose send after dictation', async () => {
  vi.mocked(gql).mockResolvedValue(voiceStatus(true) as never);
  render(<Chat session={session} {...props} />);
  await dictate();
  await waitFor(() => {
    const call = vi.mocked(request).mock.calls.find(([path]) => String(path).endsWith('/turns'));
    expect(call).toBeTruthy();
    expect(JSON.parse(String(call![1]?.body)).prompt).toBe('check the build');
  });
});

it('keeps the composer quiet when voice is not set up', async () => {
  vi.mocked(gql).mockResolvedValue({voice_status: {...voiceStatus(false).voice_status, stt: {configured: false, provider: null}}} as never);
  render(<Chat session={session} {...props} onOpenVoiceSettings={() => {}} />);
  const start = await screen.findByRole('button', {name: 'Start dictation'});
  await waitFor(() => expect(start).toBeEnabled());
  expect(screen.queryByRole('checkbox', {name: 'Send after dictation'})).not.toBeInTheDocument();
});
