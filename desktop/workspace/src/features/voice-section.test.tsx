import {afterEach, beforeEach, expect, it, vi} from 'vitest';
import {cleanup, fireEvent, render, screen, waitFor, within} from '@testing-library/react';
import {expectNoAxeViolations} from '../test-axe';
import VoiceSection from './VoiceSection';
import {
  downloadWoof,
  loadVoiceStatus,
  loadWoofLicense,
  removeWoof,
  saveVoiceSettings,
  type VoiceEngineStatus,
  type VoiceStatus,
} from '../lib/voice/settings';

vi.mock('../lib/voice/settings', () => ({
  loadVoiceStatus: vi.fn(),
  saveVoiceSettings: vi.fn(),
  loadWoofLicense: vi.fn(),
  downloadWoof: vi.fn(),
  removeWoof: vi.fn(),
  onVoiceChange: () => () => {},
  announceVoiceChange: vi.fn(),
}));
const speak = vi.fn();
const hooksSeen: {onCredit?: (credit: unknown) => void; onNotice?: (notice: string) => void} = {};
vi.mock('../lib/voice/player', () => ({
  SpeechPlayer: class {
    constructor(hooks: typeof hooksSeen) {
      Object.assign(hooksSeen, hooks);
    }
    speak = speak;
    stop = vi.fn();
  },
}));

const load = vi.mocked(loadVoiceStatus);
const save = vi.mocked(saveVoiceSettings);
const license = vi.mocked(loadWoofLicense);
const download = vi.mocked(downloadWoof);
const remove = vi.mocked(removeWoof);

const woof = (overrides: Partial<VoiceEngineStatus> = {}): VoiceEngineStatus => ({
  id: 'woof',
  label: 'Underdog Woof',
  kind: 'local',
  state: 'not_installed',
  detail: 'Not downloaded.',
  installed: false,
  installable: true,
  size_bytes: 40_000_000,
  voices: [
    {id: 'juno', label: 'Juno'},
    {id: 'mira', label: 'Mira'},
  ],
  attribution: {text: 'Voice by Underdog Woof', url: 'https://underdog.ai'},
  license: {name: 'Underdog Non-Commercial License 1.0', sha256: 'a'.repeat(64), noncommercial: true, contact: 'license@conway.tech', url: 'https://example.test/LICENSE'},
  ...overrides,
});
const provider: VoiceEngineStatus = {
  id: 'provider',
  label: 'Cloud or server API',
  kind: 'provider',
  state: 'ready',
  detail: 'Text is sent to the account you choose.',
  installed: true,
  installable: false,
  voices: [{id: 'alloy', label: 'Alloy'}],
  accounts: [
    {id: 'speech', name: 'Speech API', base_url: 'https://api.speech.example/v1'},
    {id: 'local', name: 'Local Kokoro', base_url: 'http://127.0.0.1:8880/v1'},
  ],
};
function status(over: {settings?: Partial<VoiceStatus['settings']>; woof?: Partial<VoiceEngineStatus>} = {}): VoiceStatus {
  return {
    available: true,
    settings: {
      engine: 'browser', provider: null, model: '', transcribe_model: '', voice: '', auto_send: false,
      auto_speak: false, cloud_fallback: false, keep_audio: false, consent: {}, ...over.settings,
    },
    engines: [provider, woof(over.woof)],
    stt: {configured: false, provider: null},
  };
}

beforeEach(() => {
  load.mockReset().mockResolvedValue(status());
  save.mockReset().mockImplementation(async input => status({settings: input as never}));
  license.mockReset().mockResolvedValue({
    revision: 'rev', name: 'Underdog Non-Commercial License 1.0', contact: 'license@conway.tech',
    license: {text: 'LICENSE TEXT: free for non-commercial use', sha256: 'a'.repeat(64)},
    notice: {text: 'NOTICE TEXT: credit Underdog', sha256: 'b'.repeat(64)},
    attribution: {text: 'Voice by Underdog Woof', url: 'https://underdog.ai'},
  });
  download.mockReset().mockResolvedValue(undefined);
  remove.mockReset().mockResolvedValue(undefined);
  speak.mockReset().mockResolvedValue(undefined);
});
afterEach(cleanup);

const open = async (admin = true) => {
  const view = render(<VoiceSection admin={admin} projectId={null} />);
  await screen.findByRole('radio', {name: /Underdog Woof/});
  return view;
};

it('lists the engines with their state and has no axe violations', async () => {
  const view = await open();
  expect(screen.getByRole('radio', {name: /Browser voice/})).toBeChecked();
  expect(screen.getByText('Not downloaded')).toBeInTheDocument();
  expect(screen.getByRole('button', {name: 'Download'})).toBeInTheDocument();
  await expectNoAxeViolations(view.container);
});

it('shows the license and notice first and only downloads after they are accepted', async () => {
  const view = await open();
  fireEvent.click(screen.getByRole('button', {name: 'Download'}));
  const dialog = await screen.findByRole('dialog', {name: /Download Underdog Woof/});
  expect(await within(dialog).findByText(/LICENSE TEXT/)).toBeInTheDocument();
  expect(within(dialog).getByText(/NOTICE TEXT/)).toBeInTheDocument();
  expect(within(dialog).getByText(/Non-commercial use only/)).toBeInTheDocument();
  const confirm = within(dialog).getByRole('button', {name: 'Download'});
  expect(confirm).toBeDisabled();
  await expectNoAxeViolations(document.body);
  fireEvent.click(within(dialog).getByRole('checkbox', {name: /non-commercial/i}));
  expect(confirm).toBeEnabled();
  fireEvent.click(confirm);
  await waitFor(() => expect(download).toHaveBeenCalledWith('a'.repeat(64)));
  expect(save).not.toHaveBeenCalled();
  view.unmount();
});

it('hides download and remove from people who are not host admins', async () => {
  await open(false);
  expect(screen.queryByRole('button', {name: 'Download'})).not.toBeInTheDocument();
  expect(screen.getByText(/host owner/i)).toBeInTheDocument();
});

it('says why Woof cannot be downloaded when the build has no pinned release', async () => {
  load.mockResolvedValue(status({woof: {state: 'unavailable', installable: false, detail: 'This build has no pinned Woof release, so it cannot be downloaded.'}}));
  await open();
  expect(screen.getByText(/no pinned Woof release/)).toBeInTheDocument();
  expect(screen.queryByRole('button', {name: 'Download'})).not.toBeInTheDocument();
});

it('shows download progress while the host fetches the model', async () => {
  load.mockResolvedValue(status({woof: {state: 'downloading', progress: {done: 20_000_000, total: 40_000_000}}}));
  await open();
  expect(screen.getByRole('progressbar', {name: /Downloading/})).toHaveAttribute('aria-valuenow', '50');
});

it('previews each Woof voice with the credit, and removes the model after confirming', async () => {
  load.mockResolvedValue(status({settings: {engine: 'woof', consent: {woof: {sha256: 'a'.repeat(64), at: 1}}}, woof: {state: 'ready', installed: true}}));
  await open();
  expect(screen.getByRole('link', {name: 'Voice by Underdog Woof'})).toHaveAttribute('href', 'https://underdog.ai');
  fireEvent.click(screen.getByRole('button', {name: 'Preview Mira'}));
  await waitFor(() => expect(speak).toHaveBeenCalledWith(expect.any(String), {engine: 'woof', voice: 'mira', fallback: false}));
  fireEvent.click(screen.getByRole('button', {name: 'Remove'}));
  const dialog = await screen.findByRole('dialog', {name: /Remove Underdog Woof/});
  fireEvent.click(within(dialog).getByRole('button', {name: 'Remove'}));
  await waitFor(() => expect(remove).toHaveBeenCalledOnce());
});

it('asks another owner to accept the license before they can use an installed model', async () => {
  load.mockResolvedValue(status({woof: {state: 'ready', installed: true}}));
  await open();
  fireEvent.click(screen.getByRole('button', {name: 'Accept license'}));
  const dialog = await screen.findByRole('dialog', {name: /Underdog Woof license/});
  fireEvent.click(await within(dialog).findByRole('checkbox', {name: /non-commercial/i}));
  fireEvent.click(within(dialog).getByRole('button', {name: 'Accept license'}));
  await waitFor(() => expect(save).toHaveBeenCalledWith({accept_woof_license: 'a'.repeat(64)}));
});

it('names the provider in the consent text and clears consent when the account changes', async () => {
  await open();
  fireEvent.change(screen.getByRole('combobox', {name: 'Audio account'}), {target: {value: 'speech'}});
  const consent = screen.getByRole('checkbox', {name: /Speech API/});
  expect(consent).not.toBeChecked();
  expect(consent.closest('label')).toHaveTextContent(/api\.speech\.example/);
  fireEvent.click(consent);
  fireEvent.change(screen.getByRole('combobox', {name: 'Audio account'}), {target: {value: 'local'}});
  const local = screen.getByRole('checkbox', {name: /Local Kokoro/});
  expect(local).not.toBeChecked();
  expect(local.closest('label')).toHaveTextContent(/runs on this device/i);
});

it('saves the draft from the form with the consent the owner ticked', async () => {
  await open();
  fireEvent.change(screen.getByRole('combobox', {name: 'Audio account'}), {target: {value: 'speech'}});
  fireEvent.click(screen.getByRole('checkbox', {name: /Speech API/}));
  fireEvent.change(screen.getByRole('textbox', {name: 'Speech model'}), {target: {value: 'tts-1'}});
  fireEvent.change(screen.getByRole('textbox', {name: 'Transcription model'}), {target: {value: 'whisper-1'}});
  fireEvent.click(screen.getByRole('radio', {name: /Cloud or server API/}));
  fireEvent.click(screen.getByRole('checkbox', {name: 'Keep recordings'}));
  fireEvent.submit(screen.getByRole('form', {name: 'Voice settings'}));
  await waitFor(() => expect(save).toHaveBeenCalled());
  expect(save.mock.calls[0][0]).toMatchObject({
    engine: 'provider', provider: 'speech', model: 'tts-1', transcribe_model: 'whisper-1',
    consent_provider: true, keep_audio: true, auto_send: false, auto_speak: false, cloud_fallback: false,
  });
});

it('drops a voice that belongs to the engine the owner switched away from', async () => {
  load.mockResolvedValue(status({settings: {engine: 'woof', voice: 'mira'}, woof: {state: 'ready', installed: true}}));
  await open();
  fireEvent.click(screen.getByRole('radio', {name: /Cloud or server API/}));
  expect(screen.getByRole('combobox', {name: 'Voice'})).toHaveValue('');
  fireEvent.change(screen.getByRole('combobox', {name: 'Voice'}), {target: {value: 'alloy'}});
  fireEvent.click(screen.getByRole('radio', {name: /Underdog Woof/}));
  fireEvent.submit(screen.getByRole('form', {name: 'Voice settings'}));
  await waitFor(() => expect(save).toHaveBeenCalled());
  expect(save.mock.calls[0][0]).toMatchObject({engine: 'woof', voice: ''});
});

it('keeps a Woof voice when the owner picks Woof again', async () => {
  load.mockResolvedValue(status({settings: {engine: 'browser', voice: 'mira'}, woof: {state: 'ready', installed: true}}));
  await open();
  fireEvent.click(screen.getByRole('radio', {name: /Underdog Woof/}));
  fireEvent.submit(screen.getByRole('form', {name: 'Voice settings'}));
  await waitFor(() => expect(save).toHaveBeenCalled());
  expect(save.mock.calls[0][0]).toMatchObject({engine: 'woof', voice: 'mira'});
});

it('tells the owner what failed and how to retry when settings cannot load', async () => {
  load.mockRejectedValue(new Error('Managed sign-in required'));
  render(<VoiceSection admin projectId={null} />);
  expect(await screen.findByRole('alert')).toHaveTextContent(/Managed sign-in required/);
  expect(screen.getByRole('button', {name: 'Retry'})).toBeInTheDocument();
});

it('previews the browser voice without asking the host', async () => {
  await open();
  fireEvent.click(screen.getByRole('button', {name: 'Preview browser voice'}));
  await waitFor(() => expect(speak).toHaveBeenCalledWith(expect.any(String), {engine: 'browser', fallback: false}));
});
