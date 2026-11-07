import {afterEach, beforeEach, expect, it, vi} from 'vitest';
import {act, cleanup, fireEvent, render, screen, waitFor, within} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {webcrypto} from 'node:crypto';
import Characters from './Characters';
import {gql, request, type Session} from '../lib/api';
import {download, transfer} from '../lib/transfers';
import {expectNoAxeViolations} from '../test-axe';
import type {CharacterLibrary, CharacterManifest, CharacterOperation} from '../lib/characters';

vi.mock('../lib/api', () => ({gql: vi.fn(), request: vi.fn(), json: (method: string, data: unknown) => ({method, body: JSON.stringify(data)})}));
vi.mock('../lib/transfers', () => ({transfer: vi.fn(), download: vi.fn()}));
vi.mock('../lib/companion-native', () => ({
  companionCapabilities: async () => ({desktop: false, backend: 'in-app'}),
  companionHome: vi.fn(),
}));
vi.mock('../components/CharacterSprite', async () => {
  const {useEffect} = await import('react');
  const manifest: CharacterManifest = {
    version: 1,
    id: 'robot',
    name: 'Sprout',
    revision: 1,
    logical_size: {width: 96, height: 112},
    foot_anchor: {x: 48, y: 104},
    interaction_bounds: {x: 0, y: 0, width: 96, height: 112},
    atlases: [],
    states: {idle: 'idle', waiting: 'waiting'},
    clips: {
      idle: {loop: true, frames: [0, 1, 2].map(() => ({atlas: 0, x: 0, y: 0, width: 96, height: 112, duration_ms: 140}))},
      waiting: {loop: true, frames: [{atlas: 0, x: 0, y: 0, width: 96, height: 112, duration_ms: 140}]},
    },
  };
  return {
    default: ({onManifest, state, packId, revision}: {onManifest?: (value: CharacterManifest) => void; state?: string; packId: string; revision: number}) => {
      useEffect(() => {
        if (!onManifest) return;
        void Promise.resolve().then(() => onManifest(manifest));
      }, [onManifest, packId, revision]);
      return <span role="img" aria-label={'Preview ' + (state ?? 'idle')} />;
    },
  };
});

const api = vi.mocked(request);
const graph = vi.mocked(gql);
const send = vi.mocked(transfer);
const chats = [{id: 'chat-A', title: 'Build a little world', archived: false}] as Session[];
let library: CharacterLibrary;
let failLibrary: Error | null;

function png(width = 64, height = 64, name = 'sheet.png') {
  const bytes = new Uint8Array(33);
  bytes.set([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);
  const view = new DataView(bytes.buffer);
  view.setUint32(8, 13);
  bytes.set([0x49, 0x48, 0x44, 0x52], 12);
  view.setUint32(16, width);
  view.setUint32(20, height);
  return new File([bytes], name, {type: 'image/png'});
}

/** Choosing a file validates it asynchronously; wait for that before the next step. */
const settleFileCheck = () => act(async () => new Promise(resolve => setTimeout(resolve, 50)));
const calls = (path: string) => api.mock.calls.filter(([called]) => called === path);
const bodyOf = (path: string) => JSON.parse(String(calls(path)[0]?.[1]?.body));
const operation = (change: Partial<CharacterOperation>): CharacterOperation => ({id: 'operation-one', status: 'running', phase: 'reference', ...change});

beforeEach(() => {
  // Media intents persist in localStorage; use an in-memory one so the suite does not depend on the host runtime providing it.
  const store = new Map<string, string>();
  vi.stubGlobal('localStorage', {
    getItem: (key: string) => store.get(key) ?? null,
    setItem: (key: string, value: string) => void store.set(key, String(value)),
    removeItem: (key: string) => void store.delete(key),
    clear: () => store.clear(),
  });
  vi.stubGlobal('crypto', webcrypto);
  api.mockReset();
  graph.mockReset();
  send.mockReset();
  failLibrary = null;
  library = {
    packs: [
      {id: 'robot', name: 'Sprout', revision: 1, builtin: true, accepted: true, accepted_revision: 1},
      {id: 'custom', name: 'Pebble', revision: 2, builtin: false, accepted: false, accepted_revision: 1, fallback_clips: ['waiting', 'thinking']},
    ],
    preferences: {enabled: false, size: 96, activity: 'calm', reduced_motion: false, pinned: [], hidden: [], assignments: {}},
    operations: [],
  };
  graph.mockResolvedValue({
    agent_providers: [
      {id: 'images', name: 'Image API', capabilities: ['image'], secret_configured: true},
      {id: 'chat', name: 'Chat API', capabilities: ['chat'], secret_configured: true},
    ],
  });
  api.mockImplementation(async (path, init) => {
    if (path === '/api/characters') {
      if (failLibrary) throw failLibrary;
      return structuredClone(library);
    }
    if (path === '/api/companions/preferences') {
      Object.assign(library.preferences, JSON.parse(String(init?.body)));
      return structuredClone(library.preferences);
    }
    if (path.endsWith('/accept')) return {id: 'custom', accepted: true};
    if (path === '/api/characters/generate') return {id: 'operation-one', status: 'queued', phase: 'reference'};
    return {};
  });
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  Object.defineProperty(document, 'hidden', {configurable: true, value: false});
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

async function openCreateDialog() {
  render(<Characters ownerId="owner" sessions={chats} />);
  await screen.findByRole('button', {name: /Sprout/});
  fireEvent.click(screen.getByRole('button', {name: 'Create a character'}));
  await screen.findByRole('option', {name: 'Image API'});
}

function fillCreateForm() {
  fireEvent.change(screen.getByLabelText(/Character name/), {target: {value: 'Mochi'}});
  fireEvent.change(screen.getByLabelText(/Character description/), {target: {value: 'A round silver robot with blue ears'}});
  fireEvent.change(screen.getByLabelText(/Image account/), {target: {value: 'images'}});
  fireEvent.change(screen.getByLabelText(/Image model/), {target: {value: 'image-model'}});
  fireEvent.click(screen.getByRole('checkbox', {name: /I agree to charges/}));
}

it('starts with roaming disabled and persists explicit host preferences', async () => {
  render(<Characters ownerId="owner" sessions={chats} />);
  const enable = await screen.findByRole('checkbox', {name: /Let characters explore/});
  expect(enable).not.toBeChecked();
  fireEvent.click(enable);
  await waitFor(() => expect(api).toHaveBeenCalledWith('/api/companions/preferences', expect.objectContaining({method: 'PATCH', body: JSON.stringify({enabled: true})})));
  expect(await screen.findByRole('checkbox', {name: /Let characters explore/})).toBeChecked();
});

it('keeps controls enabled and focused while a preference saves, and applies rapid changes in order', async () => {
  let release!: () => void;
  const gate = new Promise<void>(resolve => (release = resolve));
  const original = api.getMockImplementation();
  api.mockImplementation(async (path, init) => {
    if (path === '/api/companions/preferences' && !calls(path).length) await gate;
    return original?.(path, init);
  });
  render(<Characters ownerId="owner" sessions={chats} />);
  const enable = await screen.findByRole('checkbox', {name: /Let characters explore/});
  enable.focus();
  fireEvent.click(enable);
  await waitFor(() => expect(calls('/api/companions/preferences')).toHaveLength(1));
  expect(enable).toBeEnabled();
  expect(enable).toHaveFocus();
  fireEvent.change(screen.getByLabelText('Size'), {target: {value: '128'}});
  release();
  await waitFor(() => expect(calls('/api/companions/preferences')).toHaveLength(2));
  expect(bodyOf('/api/companions/preferences')).toEqual({enabled: true});
  expect(JSON.parse(String(calls('/api/companions/preferences')[1][1]?.body))).toEqual({size: 128});
  expect(library.preferences).toMatchObject({enabled: true, size: 128});
});

it('treats two quick clicks on the same checkbox as a toggle and a toggle back', async () => {
  let release!: () => void;
  const gate = new Promise<void>(resolve => (release = resolve));
  const original = api.getMockImplementation();
  api.mockImplementation(async (path, init) => {
    if (path === '/api/companions/preferences' && !calls(path).length) await gate;
    return original?.(path, init);
  });
  render(<Characters ownerId="owner" sessions={chats} />);
  const enable = await screen.findByRole('checkbox', {name: /Let characters explore/});
  fireEvent.click(enable);
  fireEvent.click(enable);
  release();
  await waitFor(() => expect(calls('/api/companions/preferences')).toHaveLength(2));
  expect(JSON.parse(String(calls('/api/companions/preferences')[0][1]?.body))).toEqual({enabled: true});
  expect(JSON.parse(String(calls('/api/companions/preferences')[1][1]?.body))).toEqual({enabled: false});
  await waitFor(() => expect(library.preferences.enabled).toBe(false));
});

it('does not let a library fetched before a save overwrite the saved preferences', async () => {
  let release!: () => void;
  const gate = new Promise<void>(resolve => (release = resolve));
  const original = api.getMockImplementation();
  let reads = 0;
  api.mockImplementation(async (path, init) => {
    if (path === '/api/characters' && ++reads === 2) {
      const before = structuredClone(library);
      await gate;
      return before;
    }
    return original?.(path, init);
  });
  render(<Characters ownerId="owner" sessions={chats} />);
  const pin = await screen.findByRole('checkbox', {name: 'Pin Build a little world'});
  act(() => void window.dispatchEvent(new Event('termx-companions-changed')));
  await waitFor(() => expect(reads).toBe(2));
  fireEvent.click(pin);
  await waitFor(() => expect(library.preferences.pinned).toEqual(['chat-A']));
  release();
  await act(async () => void (await gate));
  expect(await screen.findByRole('checkbox', {name: 'Pin Build a little world'})).toBeChecked();
  expect(screen.getByRole('checkbox', {name: 'Pin Build a little world'})).toBeChecked();
});

it('shows a failed preference save beside the preferences and retries it', async () => {
  const original = api.getMockImplementation();
  let fail = true;
  api.mockImplementation(async (path, init) => {
    if (path === '/api/companions/preferences' && fail) throw new Error('Host is busy');
    return original?.(path, init);
  });
  render(<Characters ownerId="owner" sessions={chats} />);
  fireEvent.click(await screen.findByRole('checkbox', {name: /Let characters explore/}));
  const preferences = screen.getByRole('region', {name: 'Preferences'});
  expect(await within(preferences).findByText('Host is busy')).toBeInTheDocument();
  expect(screen.getByRole('region', {name: 'Library'})).not.toHaveTextContent('Host is busy');
  fail = false;
  fireEvent.click(within(preferences).getByRole('button', {name: 'Retry'}));
  await waitFor(() => expect(screen.getByRole('checkbox', {name: /Let characters explore/})).toBeChecked());
  expect(within(preferences).queryByText('Host is busy')).not.toBeInTheDocument();
});

it('lets users inspect draft fallback poses before explicitly activating a revision', async () => {
  render(<Characters ownerId="owner" sessions={chats} />);
  fireEvent.click(await screen.findByRole('button', {name: /Pebble/}));
  expect(await screen.findByText(/Shared poses: waiting, thinking/)).toBeInTheDocument();
  expect(calls('/api/characters/packs/custom/accept')).toHaveLength(0);
  await waitFor(() => expect(screen.getByRole('button', {name: 'Use this revision'})).toBeEnabled());
  fireEvent.click(screen.getByRole('button', {name: 'Use this revision'}));
  await waitFor(() => expect(api).toHaveBeenCalledWith('/api/characters/packs/custom/accept', expect.objectContaining({body: JSON.stringify({revision: 2})})));
});

it('shows a load failure with Retry and recovers', async () => {
  failLibrary = new Error('Host unreachable');
  render(<Characters ownerId="owner" sessions={chats} />);
  expect(await screen.findByText(/Host unreachable/)).toBeInTheDocument();
  expect(screen.queryByText('Loading your characters…')).not.toBeInTheDocument();
  failLibrary = null;
  fireEvent.click(screen.getByRole('button', {name: 'Retry'}));
  expect(await screen.findByRole('button', {name: /Sprout/})).toBeInTheDocument();
  expect(screen.queryByText(/Host unreachable/)).not.toBeInTheDocument();
});

it('rejects a malformed library reply instead of rendering it', async () => {
  const original = api.getMockImplementation();
  api.mockImplementation(async (path, init) => (path === '/api/characters' ? {packs: [{id: 7}]} : original?.(path, init)));
  render(<Characters ownerId="owner" sessions={chats} />);
  expect(await screen.findByText(/unexpected character library/)).toBeInTheDocument();
});

it('requires explicit provider, model and billing consent before generation', async () => {
  await openCreateDialog();
  expect(screen.queryByRole('option', {name: 'Chat API'})).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText(/Character name/), {target: {value: 'Mochi'}});
  fireEvent.change(screen.getByLabelText(/Character description/), {target: {value: 'A round silver robot with blue ears'}});
  fireEvent.change(screen.getByLabelText(/Image account/), {target: {value: 'images'}});
  expect(screen.getByRole('button', {name: 'Generate reference'})).toBeDisabled();
  fireEvent.change(screen.getByLabelText(/Image model/), {target: {value: 'image-model'}});
  fireEvent.click(screen.getByRole('checkbox', {name: /I agree to charges/}));
  fireEvent.click(screen.getByRole('button', {name: 'Generate reference'}));
  await waitFor(() => expect(api).toHaveBeenCalledWith('/api/characters/generate', expect.objectContaining({method: 'POST'})));
  expect(bodyOf('/api/characters/generate')).toMatchObject({phase: 'reference', name: 'Mochi', provider_id: 'images', model: 'image-model', acknowledge_billing: true});
  expect(bodyOf('/api/characters/generate').request_id).toMatch(/^[\w-]{36}$/);
});

it('keeps the create form open with an inline error when generation fails, and does not offer Retry for a billed request', async () => {
  const original = api.getMockImplementation();
  api.mockImplementation(async (path, init) => {
    if (path === '/api/characters/generate') throw new Error('Provider rejected the key');
    return original?.(path, init);
  });
  await openCreateDialog();
  fillCreateForm();
  fireEvent.click(screen.getByRole('button', {name: 'Generate reference'}));
  const dialog = screen.getByRole('dialog');
  expect(await within(dialog).findByText('Provider rejected the key')).toBeInTheDocument();
  expect(within(dialog).queryByRole('button', {name: 'Retry'})).not.toBeInTheDocument();
  expect(within(dialog).getByLabelText(/Character description/)).toHaveValue('A round silver robot with blue ears');
});

it('keeps the create draft when the dialog is closed and reopened', async () => {
  await openCreateDialog();
  fireEvent.change(screen.getByLabelText(/Character name/), {target: {value: 'Mochi'}});
  fireEvent.keyDown(screen.getByRole('dialog'), {key: 'Escape'});
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  fireEvent.click(screen.getByRole('button', {name: 'Create a character'}));
  expect(await screen.findByLabelText(/Character name/)).toHaveValue('Mochi');
});

it('empties the build sheet when its dialog closes, so what is shown matches what is uploaded', async () => {
  render(<Characters ownerId="owner" sessions={chats} />);
  await screen.findByRole('button', {name: /Sprout/});
  fireEvent.click(screen.getByRole('button', {name: 'Build from artwork'}));
  fireEvent.change(await screen.findByLabelText(/Animation sheet/), {target: {files: [png(256, 128)]}});
  fireEvent.change(screen.getByLabelText(/Artwork character name/), {target: {value: 'Mochi'}});
  await settleFileCheck();
  fireEvent.keyDown(screen.getByRole('dialog'), {key: 'Escape'});
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  fireEvent.click(screen.getByRole('button', {name: 'Build from artwork'}));
  expect(await screen.findByLabelText(/Artwork character name/)).toHaveValue('Mochi');
  fireEvent.click(screen.getByRole('button', {name: 'Build animation'}));
  expect(screen.getByLabelText(/Animation sheet/)).toHaveAccessibleDescription(/Choose a PNG sprite sheet\./);
  expect(send).not.toHaveBeenCalled();
});

it('assigns only accepted packs and updates the actual conversation identity', async () => {
  render(<Characters ownerId="owner" sessions={chats} />);
  const assignment = await screen.findByLabelText('Character for Build a little world');
  expect(within(assignment).queryByRole('option', {name: 'Pebble'})).toBeInTheDocument();
  fireEvent.change(assignment, {target: {value: 'custom'}});
  await waitFor(() => expect(api).toHaveBeenCalledWith('/api/companions/preferences', expect.objectContaining({body: JSON.stringify({assignments: {'chat-A': 'custom'}})})));
});

it('does not dispatch a generation intent after its settings have unmounted', async () => {
  const view = render(<Characters ownerId="owner" sessions={chats} />);
  await screen.findByRole('button', {name: /Sprout/});
  fireEvent.click(screen.getByRole('button', {name: 'Create a character'}));
  await screen.findByRole('option', {name: 'Image API'});
  fillCreateForm();
  let finish!: (value: ArrayBuffer) => void;
  vi.spyOn(crypto.subtle, 'digest').mockImplementation(() => new Promise(resolve => (finish = resolve)));
  fireEvent.click(screen.getByRole('button', {name: 'Generate reference'}));
  await waitFor(() => expect(finish).toBeTypeOf('function'));
  view.unmount();
  await act(async () => finish(new ArrayBuffer(32)));
  expect(calls('/api/characters/generate')).toHaveLength(0);
});

it('replaces the scrubbed frame in a draft without activating it', async () => {
  send.mockResolvedValue({text: async () => JSON.stringify({id: 'frame-art'})} as Blob);
  render(<Characters ownerId="owner" sessions={chats} />);
  fireEvent.click(await screen.findByRole('button', {name: /Pebble/}));
  await waitFor(() => expect(screen.getByRole('button', {name: 'Use this revision'})).toBeEnabled());
  fireEvent.click(screen.getByRole('button', {name: /Timing and frames/}));
  const input = await screen.findByLabelText('Replace frame 1');
  expect(input).toBeDisabled();
  fireEvent.click(screen.getByRole('button', {name: 'Pause preview'}));
  fireEvent.change(input, {target: {files: [png(32, 32, 'frame.png')]}});
  await waitFor(() => expect(api).toHaveBeenCalledWith('/api/characters/packs/custom/frames/0', expect.objectContaining({body: JSON.stringify({clip: 'idle', artifact_id: 'frame-art'})})));
  expect(calls('/api/characters/packs/custom/accept')).toHaveLength(0);
});

it('rejects a frame that is not a PNG before uploading anything', async () => {
  render(<Characters ownerId="owner" sessions={chats} />);
  fireEvent.click(await screen.findByRole('button', {name: /Pebble/}));
  await waitFor(() => expect(screen.getByRole('button', {name: 'Use this revision'})).toBeEnabled());
  fireEvent.click(screen.getByRole('button', {name: /Timing and frames/}));
  fireEvent.click(screen.getByRole('button', {name: 'Pause preview'}));
  fireEvent.change(await screen.findByLabelText('Replace frame 1'), {target: {files: [new File(['not an image'], 'frame.png', {type: 'image/png'})]}});
  expect(await screen.findByText('Choose a PNG image.')).toBeInTheDocument();
  expect(send).not.toHaveBeenCalled();
});

it('rejects empty and out-of-range timing fields with a message beside the field', async () => {
  render(<Characters ownerId="owner" sessions={chats} />);
  fireEvent.click(await screen.findByRole('button', {name: /Pebble/}));
  await waitFor(() => expect(screen.getByRole('button', {name: 'Use this revision'})).toBeEnabled());
  fireEvent.click(screen.getByRole('button', {name: /Timing and frames/}));
  const duration = await screen.findByLabelText(/Frame duration/);
  fireEvent.change(duration, {target: {value: ''}});
  fireEvent.click(screen.getByRole('button', {name: 'Save timing'}));
  expect(duration).toHaveAccessibleDescription('Enter a whole number from 20 to 5000.');
  expect(calls('/api/characters/packs/custom/clip')).toHaveLength(0);
  fireEvent.change(duration, {target: {value: '200'}});
  fireEvent.click(screen.getByRole('button', {name: 'Save timing'}));
  await waitFor(() => expect(bodyOf('/api/characters/packs/custom/clip')).toEqual({clip: 'idle', duration_ms: 200, foot_anchor: {x: 48, y: 104}}));
});

it('keeps unsaved timing edits when the library refreshes, and drops them when another pack is chosen', async () => {
  vi.useFakeTimers({shouldAdvanceTime: true});
  library.operations = [operation({})];
  render(<Characters ownerId="owner" sessions={chats} />);
  fireEvent.click(await screen.findByRole('button', {name: /Pebble/}));
  await waitFor(() => expect(screen.getByRole('button', {name: 'Use this revision'})).toBeEnabled());
  fireEvent.click(screen.getByRole('button', {name: /Timing and frames/}));
  fireEvent.change(await screen.findByLabelText(/Frame duration/), {target: {value: '333'}});
  const before = calls('/api/characters').length;
  await act(async () => vi.advanceTimersByTimeAsync(2600));
  expect(calls('/api/characters').length).toBeGreaterThan(before);
  expect(screen.getByLabelText(/Frame duration/)).toHaveValue(333);
  fireEvent.click(screen.getByRole('button', {name: /Sprout/}));
  fireEvent.click(screen.getByRole('button', {name: /Pebble/}));
  fireEvent.click(await screen.findByRole('button', {name: /Timing and frames/}));
  expect(await screen.findByLabelText(/Frame duration/)).toHaveValue(140);
});

it('imports a pack, selects the new draft and reports a rejected reply', async () => {
  send.mockResolvedValueOnce({text: async () => JSON.stringify({id: 'imported', name: 'Imported', revision: 1, builtin: false, accepted: false})} as Blob);
  render(<Characters ownerId="owner" sessions={chats} />);
  await screen.findByRole('button', {name: /Sprout/});
  const pack = new File([new Uint8Array([0x50, 0x4b, 3, 4])], 'friend.termxpet');
  fireEvent.change(screen.getByLabelText(/Import character pack/), {target: {files: [pack]}});
  await waitFor(() => expect(send).toHaveBeenCalledWith('/api/characters/import', expect.objectContaining({method: 'POST'})));
  expect(await screen.findByText(/Pack imported as a draft/)).toBeInTheDocument();
  send.mockResolvedValueOnce({text: async () => '<html>proxy error</html>'} as Blob);
  fireEvent.change(screen.getByLabelText(/Import character pack/), {target: {files: [pack]}});
  const library = screen.getByRole('region', {name: 'Library'});
  expect(await within(library).findByText(/unexpected character pack reply/)).toBeInTheDocument();
});

it('refuses an oversized or wrong-type pack before uploading', async () => {
  render(<Characters ownerId="owner" sessions={chats} />);
  await screen.findByRole('button', {name: /Sprout/});
  const huge = new File([new Uint8Array([0x50, 0x4b])], 'huge.termxpet');
  Object.defineProperty(huge, 'size', {value: 33 * 1024 * 1024});
  fireEvent.change(screen.getByLabelText(/Import character pack/), {target: {files: [huge]}});
  expect(await screen.findByText('That file is larger than 32 MiB.')).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText(/Import character pack/), {target: {files: [new File(['hi'], 'notes.txt')]}});
  expect(await screen.findByText('Choose a .termxpet or .zip character pack.')).toBeInTheDocument();
  expect(send).not.toHaveBeenCalled();
});

it('exports the previewed revision and reports a failed export', async () => {
  vi.mocked(download).mockRejectedValueOnce(new Error('Export denied'));
  render(<Characters ownerId="owner" sessions={chats} />);
  fireEvent.click(await screen.findByRole('button', {name: /Pebble/}));
  fireEvent.click(await screen.findByRole('button', {name: 'Export character pack'}));
  expect(await screen.findByText('Export denied')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', {name: 'Retry'}));
  await waitFor(() => expect(download).toHaveBeenLastCalledWith('/api/characters/packs/custom/export?revision=2', 'Pebble-v2.termxpet'));
});

it('builds from a sheet, validates numeric fields, and keeps the dialog open on a build error', async () => {
  send.mockResolvedValue({text: async () => JSON.stringify({id: 'sheet-art'})} as Blob);
  const original = api.getMockImplementation();
  let fail = true;
  api.mockImplementation(async (path, init) => {
    if (path === '/api/characters/build') {
      if (fail) throw new Error('Sheet is not a grid');
      return {id: 'built', name: 'Mochi', revision: 1, builtin: false, accepted: false};
    }
    return original?.(path, init);
  });
  render(<Characters ownerId="owner" sessions={chats} />);
  await screen.findByRole('button', {name: /Sprout/});
  fireEvent.click(screen.getByRole('button', {name: 'Build from artwork'}));
  fireEvent.change(await screen.findByLabelText(/Artwork character name/), {target: {value: 'Mochi'}});
  fireEvent.change(screen.getByLabelText(/Animation sheet/), {target: {files: [png(256, 128)]}});
  await settleFileCheck();
  fireEvent.change(screen.getByLabelText(/Columns/), {target: {value: ''}});
  fireEvent.click(screen.getByRole('button', {name: 'Build animation'}));
  expect(screen.getByLabelText(/Columns/)).toHaveAccessibleDescription('Enter a whole number from 1 to 16.');
  expect(calls('/api/characters/build')).toHaveLength(0);
  fireEvent.change(screen.getByLabelText(/Columns/), {target: {value: '4'}});
  await waitFor(() => expect(screen.getByLabelText(/Animation sheet/)).toHaveAccessibleDescription(/up to 16 MiB/));
  fireEvent.click(screen.getByRole('button', {name: 'Build animation'}));
  const dialog = screen.getByRole('dialog');
  expect(await within(dialog).findByText('Sheet is not a grid')).toBeInTheDocument();
  fail = false;
  fireEvent.click(within(dialog).getByRole('button', {name: 'Retry'}));
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  expect(bodyOf('/api/characters/build')).toMatchObject({artifact_id: 'sheet-art', name: 'Mochi', columns: 4, rows: 2, duration_ms: 140, clip: 'idle'});
  expect(await screen.findByText(/Sheet compiled into a draft/)).toBeInTheDocument();
});

it('rejects a sprite sheet that is too large in pixels', async () => {
  render(<Characters ownerId="owner" sessions={chats} />);
  await screen.findByRole('button', {name: /Sprout/});
  fireEvent.click(screen.getByRole('button', {name: 'Build from artwork'}));
  fireEvent.change(await screen.findByLabelText(/Animation sheet/), {target: {files: [png(5000, 100)]}});
  expect(await screen.findByText(/5000 by 100 pixels/)).toBeInTheDocument();
});

it('polls only while an operation is running, and stops when it finishes', async () => {
  vi.useFakeTimers({shouldAdvanceTime: true});
  render(<Characters ownerId="owner" sessions={chats} />);
  await screen.findByRole('button', {name: /Sprout/});
  const idle = calls('/api/characters').length;
  await act(async () => vi.advanceTimersByTimeAsync(10_000));
  expect(calls('/api/characters')).toHaveLength(idle);

  library.operations = [operation({})];
  act(() => void window.dispatchEvent(new Event('termx-companions-changed')));
  await waitFor(() => expect(calls('/api/characters').length).toBe(idle + 1));
  await act(async () => vi.advanceTimersByTimeAsync(5100));
  expect(calls('/api/characters').length).toBeGreaterThanOrEqual(idle + 3);

  library.operations = [operation({status: 'completed', phase: 'reference_ready', artifact_id: 'ref-art'})];
  await act(async () => vi.advanceTimersByTimeAsync(2600));
  const done = calls('/api/characters').length;
  await act(async () => vi.advanceTimersByTimeAsync(10_000));
  expect(calls('/api/characters')).toHaveLength(done);
});

it('does not poll while the tab is hidden and refetches when it becomes visible', async () => {
  vi.useFakeTimers({shouldAdvanceTime: true});
  library.operations = [operation({})];
  render(<Characters ownerId="owner" sessions={chats} />);
  await screen.findByRole('button', {name: /Sprout/});
  Object.defineProperty(document, 'hidden', {configurable: true, value: true});
  act(() => void document.dispatchEvent(new Event('visibilitychange')));
  const hidden = calls('/api/characters').length;
  await act(async () => vi.advanceTimersByTimeAsync(10_000));
  expect(calls('/api/characters')).toHaveLength(hidden);
  Object.defineProperty(document, 'hidden', {configurable: true, value: false});
  act(() => void document.dispatchEvent(new Event('visibilitychange')));
  await waitFor(() => expect(calls('/api/characters').length).toBeGreaterThan(hidden));
});

it('refetches an idle library when the tab becomes visible or the host reports a change', async () => {
  render(<Characters ownerId="owner" sessions={chats} />);
  await screen.findByRole('button', {name: /Sprout/});
  const before = calls('/api/characters').length;
  library.packs.push({id: 'new', name: 'Fresh', revision: 1, builtin: false, accepted: false});
  act(() => void document.dispatchEvent(new Event('visibilitychange')));
  expect(await screen.findByRole('button', {name: /Fresh/})).toBeInTheDocument();
  expect(calls('/api/characters').length).toBeGreaterThan(before);
});

it('announces a finished reference once, preselects it, and cancels a running operation', async () => {
  const user = userEvent.setup();
  await openCreateDialog();
  fillCreateForm();
  library.operations = [operation({status: 'queued'})];
  await user.click(screen.getByRole('button', {name: 'Generate reference'}));
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  expect(await screen.findByText(/Character creation started/)).toBeInTheDocument();
  const progress = await screen.findByRole('group', {name: 'Character creation progress'});
  await user.click(within(progress).getByRole('button', {name: 'Cancel creation'}));
  await waitFor(() => expect(api).toHaveBeenCalledWith('/api/characters/operations/operation-one/cancel', expect.objectContaining({method: 'POST'})));
  expect(await screen.findByText(/Cancellation requested/)).toBeInTheDocument();
});

it('selects the finished reference in the create form when its operation completes', async () => {
  vi.useFakeTimers({shouldAdvanceTime: true});
  await openCreateDialog();
  fillCreateForm();
  library.operations = [operation({status: 'queued'})];
  fireEvent.click(screen.getByRole('button', {name: 'Generate reference'}));
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  send.mockResolvedValue(new Blob(['x']));
  vi.stubGlobal('URL', Object.assign(URL, {createObjectURL: vi.fn(() => 'blob:ref'), revokeObjectURL: vi.fn()}));
  library.operations = [operation({status: 'completed', phase: 'reference_ready', artifact_id: 'ref-art'})];
  await act(async () => vi.advanceTimersByTimeAsync(2600));
  expect(await screen.findByText(/Reference ready/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', {name: 'Create a character'}));
  expect(await screen.findByLabelText('Character reference')).toHaveValue('ref-art');
  expect(await screen.findByAltText('Canonical character reference')).toBeInTheDocument();
});

it('has no accessibility violations in the studio or its dialogs', async () => {
  const view = render(<Characters ownerId="owner" sessions={chats} />);
  await screen.findByRole('button', {name: /Sprout/});
  await expectNoAxeViolations(view.container);
  fireEvent.click(screen.getByRole('button', {name: 'Create a character'}));
  await expectNoAxeViolations(await screen.findByRole('dialog'));
  fireEvent.keyDown(screen.getByRole('dialog'), {key: 'Escape'});
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  fireEvent.click(screen.getByRole('button', {name: 'Build from artwork'}));
  await expectNoAxeViolations(await screen.findByRole('dialog'));
});
