import {afterEach, beforeEach, expect, it, vi} from 'vitest';
import {act, cleanup, fireEvent, render, screen, waitFor} from '@testing-library/react';
import {expectNoAxeViolations} from '../test-axe';
import {useLiveVoice} from './live-voice';
import {loadVoiceStatus, saveVoiceSettings, type VoiceStatus} from '../lib/voice/settings';
import type {LiveHooks} from '../lib/voice/live';

vi.mock('../lib/voice/settings', async importOriginal => ({
  ...(await importOriginal<typeof import('../lib/voice/settings')>()),
  loadVoiceStatus: vi.fn(),
  saveVoiceSettings: vi.fn(),
  onVoiceChange: () => () => {},
}));
const fake = vi.hoisted(() => ({
  hooks: null as unknown as LiveHooks,
  start: vi.fn(),
  end: vi.fn(),
  mute: vi.fn(),
  interrupt: vi.fn(),
}));
vi.mock('../lib/voice/live', () => ({
  LiveVoiceClient: class {
    constructor(hooks: LiveHooks) {
      fake.hooks = hooks;
    }
    start = fake.start;
    end = fake.end;
    mute = fake.mute;
    interrupt = fake.interrupt;
  },
}));

const load = vi.mocked(loadVoiceStatus);
const save = vi.mocked(saveVoiceSettings);

function status(live: Partial<NonNullable<VoiceStatus['live']>> = {}, settings: Partial<VoiceStatus['settings']> = {}): VoiceStatus {
  return {
    available: true,
    settings: {
      engine: 'browser', provider: null, model: '', transcribe_model: '', voice: '', auto_send: false, auto_speak: false,
      cloud_fallback: false, keep_audio: false, consent: {}, live_provider: 'rt', live_model: 'gpt-realtime', ...settings,
    },
    engines: [],
    stt: {configured: false, provider: null},
    live: {
      available: true, configured: true, acknowledged: true,
      accounts: [{id: 'rt', name: 'Realtime API', base_url: 'https://api.rt.example/v1'}], ...live,
    },
  };
}

function Harness({sessionId = 's1', enabled = true, onOpenSettings}: {sessionId?: string; enabled?: boolean; onOpenSettings?: () => void}) {
  const live = useLiveVoice({sessionId, enabled, onOpenSettings});
  return (
    <div>
      <div role="toolbar" aria-label="Tools">{live.button}</div>
      {live.panel}
    </div>
  );
}

beforeEach(() => {
  load.mockReset().mockResolvedValue(status());
  save.mockReset().mockResolvedValue(status());
  fake.start.mockReset().mockResolvedValue(undefined);
  fake.end.mockReset();
  fake.mute.mockReset();
  fake.interrupt.mockReset();
  window.matchMedia = ((query: string) => ({matches: false, media: query, addEventListener() {}, removeEventListener() {}})) as never;
});
afterEach(cleanup);

const startButton = () => screen.findByRole('button', {name: 'Live voice'});

it('starts a session for the open conversation when billing was already acknowledged', async () => {
  render(<Harness />);
  fireEvent.click(await startButton());
  await waitFor(() => expect(fake.start).toHaveBeenCalledWith('s1'));
  expect(save).not.toHaveBeenCalled();
  expect(await screen.findByRole('region', {name: 'Live voice'})).toBeInTheDocument();
});

it('asks for the billing acknowledgement first and uses the same verb to confirm', async () => {
  load.mockResolvedValue(status({acknowledged: false}));
  const view = render(<Harness />);
  fireEvent.click(await startButton());
  expect(fake.start).not.toHaveBeenCalled();
  const confirm = screen.getByRole('button', {name: 'Start live voice'});
  expect(confirm).toBeDisabled();
  expect(screen.getByText(/Realtime API \(api\.rt\.example\)/)).toBeInTheDocument();
  expect(screen.getByText(/may charge/i)).toBeInTheDocument();
  await expectNoAxeViolations(view.container);
  fireEvent.click(screen.getByRole('checkbox'));
  expect(confirm).toBeEnabled();
  fireEvent.click(confirm);
  await waitFor(() => expect(save).toHaveBeenCalledWith({consent_live: true}));
  await waitFor(() => expect(fake.start).toHaveBeenCalledWith('s1'));
});

it('says what to do when no realtime account is set up', async () => {
  load.mockResolvedValue(status({configured: false, acknowledged: false}, {live_provider: null, live_model: ''}));
  const open = vi.fn();
  render(<Harness onOpenSettings={open} />);
  fireEvent.click(await startButton());
  expect(screen.getByText(/needs an API account that supports realtime audio/i)).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', {name: 'Open voice settings'}));
  expect(open).toHaveBeenCalled();
  expect(fake.start).not.toHaveBeenCalled();
});

async function live() {
  const view = render(<Harness />);
  fireEvent.click(await startButton());
  await waitFor(() => expect(fake.start).toHaveBeenCalled());
  return view;
}

it('shows the state in one status line and has no axe violations while live', async () => {
  const view = await live();
  act(() => fake.hooks.onState('listening'));
  expect(screen.getByRole('status')).toHaveTextContent('Listening');
  act(() => fake.hooks.onState('speaking'));
  expect(screen.getByRole('status')).toHaveTextContent('Speaking');
  act(() => fake.hooks.onTranscript('user', 'run the tests'));
  act(() => fake.hooks.onTranscript('assistant', 'Running them now.'));
  expect(screen.getByRole('list', {name: 'Live transcript'})).toHaveTextContent('run the tests');
  expect(view.container.querySelectorAll('[aria-live]')).toHaveLength(0);
  expect(screen.getAllByRole('status')).toHaveLength(1);
  await expectNoAxeViolations(view.container);
});

it('draws a decorative meter that follows the level', async () => {
  const view = await live();
  act(() => fake.hooks.onState('listening'));
  act(() => fake.hooks.onLevel(0.5));
  const bar = view.container.querySelector('.voice-meter > span') as HTMLElement;
  expect(bar.style.transform).toBe('scaleX(0.5)');
  expect(view.container.querySelector('.voice-meter')).toHaveAttribute('aria-hidden', 'true');
});

it('leaves the meter out when the system asks for reduced motion', async () => {
  window.matchMedia = ((query: string) => ({matches: true, media: query, addEventListener() {}, removeEventListener() {}})) as never;
  const view = await live();
  expect(view.container.querySelector('.voice-meter')).toBeNull();
  expect(screen.getByRole('status')).toBeInTheDocument();
});

it('mutes, unmutes and says so', async () => {
  await live();
  act(() => fake.hooks.onState('listening'));
  const mute = screen.getByRole('button', {name: 'Mute microphone'});
  expect(mute).toHaveAttribute('aria-pressed', 'false');
  fireEvent.click(mute);
  expect(fake.mute).toHaveBeenLastCalledWith(true);
  expect(screen.getByRole('status')).toHaveTextContent('Muted');
  expect(screen.getByRole('button', {name: 'Mute microphone'})).toHaveAttribute('aria-pressed', 'true');
  fireEvent.click(screen.getByRole('button', {name: 'Mute microphone'}));
  expect(fake.mute).toHaveBeenLastCalledWith(false);
});

it('adds no live region until used, then keeps one mounted so later messages are announced', async () => {
  render(<Harness />);
  const button = await startButton();
  expect(screen.queryByRole('status')).toBeNull();
  fireEvent.click(button);
  await waitFor(() => expect(fake.start).toHaveBeenCalled());
  const region = screen.getByRole('status');
  act(() => fake.hooks.onState('listening'));
  expect(region).toHaveTextContent('Listening');
  act(() => fake.hooks.onEnded('idle', 'The session ended after 60 seconds without speech.'));
  expect(screen.getByRole('status')).toBe(region);
  expect(region).toHaveTextContent('60 seconds without speech');
});

it('moves focus into the consent panel and back to the toolbar button when it closes', async () => {
  load.mockResolvedValue(status({acknowledged: false}));
  render(<Harness />);
  const toolbarButton = await startButton();
  toolbarButton.focus();
  fireEvent.click(toolbarButton);
  expect(screen.getByRole('checkbox')).toHaveFocus();
  fireEvent.click(screen.getByRole('button', {name: 'Cancel'}));
  expect(toolbarButton).toHaveFocus();
});

it('moves focus into the setup panel and back when it is closed', async () => {
  load.mockResolvedValue(status({configured: false, acknowledged: false}, {live_provider: null, live_model: ''}));
  render(<Harness onOpenSettings={() => {}} />);
  const toolbarButton = await startButton();
  toolbarButton.focus();
  fireEvent.click(toolbarButton);
  expect(screen.getByRole('button', {name: 'Open voice settings'})).toHaveFocus();
  fireEvent.click(screen.getByRole('button', {name: 'Close'}));
  expect(toolbarButton).toHaveFocus();
});

it('puts focus on the way to start again when a session ends under the keyboard', async () => {
  await live();
  const end = screen.getByRole('button', {name: 'End live voice'});
  end.focus();
  fireEvent.click(end);
  act(() => fake.hooks.onEnded('user', 'The session ended.'));
  expect(screen.getByRole('button', {name: 'Start live voice'})).toHaveFocus();
});

it('interrupts only while the agent is talking or working', async () => {
  await live();
  act(() => fake.hooks.onState('listening'));
  expect(screen.getByRole('button', {name: 'Interrupt'})).toBeDisabled();
  act(() => fake.hooks.onState('speaking'));
  fireEvent.click(screen.getByRole('button', {name: 'Interrupt'}));
  expect(fake.interrupt).toHaveBeenCalled();
});

it('ends the session from the panel and shows the host sentence with a way to start again', async () => {
  await live();
  fireEvent.click(screen.getByRole('button', {name: 'End live voice'}));
  expect(fake.end).toHaveBeenCalled();
  act(() => fake.hooks.onEnded('idle', 'The session ended after 60 seconds without speech.'));
  expect(screen.getByRole('status')).toHaveTextContent('60 seconds without speech');
  expect(screen.getByRole('button', {name: 'Start live voice'})).toBeInTheDocument();
});

it('shows provider notices as alerts without ending the session', async () => {
  await live();
  act(() => fake.hooks.onNotice('rate limited'));
  expect(screen.getByRole('alert')).toHaveTextContent('rate limited');
  expect(screen.getByRole('button', {name: 'Mute microphone'})).toBeInTheDocument();
});

it('ends the session when the conversation is hidden or switched', async () => {
  const view = await live();
  fake.end.mockClear();
  view.rerender(<Harness sessionId="s2" />);
  expect(fake.end).toHaveBeenCalled();
});

it('ends the session when the conversation is hidden', async () => {
  const view = await live();
  fake.end.mockClear();
  view.rerender(<Harness enabled={false} />);
  await waitFor(() => expect(fake.end).toHaveBeenCalled());
});

it('ignores the old session ending after the conversation was switched', async () => {
  const view = await live();
  const old = fake.hooks;
  view.rerender(<Harness sessionId="s2" />);
  act(() => old.onEnded('user', 'The session ended.'));
  expect(screen.queryByRole('region', {name: 'Live voice'})).not.toBeInTheDocument();
  expect(screen.queryByText('The session ended.')).not.toBeInTheDocument();
});

it('names the agent answers in the billing acknowledgement', async () => {
  load.mockResolvedValue(status({acknowledged: false}));
  render(<Harness />);
  fireEvent.click(await startButton());
  expect(screen.getByText(/the agent's answers/i)).toHaveTextContent(/file contents/i);
});

it('ends the session when the component unmounts', async () => {
  const view = await live();
  fake.end.mockClear();
  view.unmount();
  expect(fake.end).toHaveBeenCalled();
});
