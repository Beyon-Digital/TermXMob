import {afterEach, beforeEach, expect, it, vi} from 'vitest';
import {act, cleanup, fireEvent, render, screen, waitFor} from '@testing-library/react';
import StudioSettings from './StudioSettings';
import {ApiError} from '../lib/api';
import {loadStudioProviders, loadStudioSettings, saveStudioProvider, saveStudioSettings, type StudioSettingsEnvelope} from '../lib/studio-settings';
import type {AgentProvider} from '../lib/host-types';
vi.mock('../lib/studio-settings', async original => ({...await original<typeof import('../lib/studio-settings')>(), loadStudioProviders: vi.fn(), loadStudioSettings: vi.fn(), saveStudioProvider: vi.fn(), saveStudioSettings: vi.fn()}));
const envelope: StudioSettingsEnvelope = {settings: {revision: 2, defaults: {}, enabled_models: null, limits: {daily_generations: null, concurrent_generations: null, max_video_seconds: null}}, usage: {daily_generations: 3, active_generations: 1, reset_at: 1794355200}, catalog: {
  accounts: [{id: 'or', name: 'Shared OpenRouter', configured: true, adapter: 'openrouter', capabilities: ['image', 'video']}, {id: 'kie', name: 'Kie', configured: true, adapter: 'kie', capabilities: ['video']}],
  models: [
    {id: 'still', adapter: 'openrouter', label: 'Still image', kind: 'image', modes: ['image', 'cinema'], description: '', aspect_ratios: ['1:1', '16:9'], resolutions: ['1K', '2K'], durations: []},
    {id: 'film', adapter: 'openrouter', label: 'Film', kind: 'video', modes: ['video', 'remix'], description: '', aspect_ratios: ['16:9'], resolutions: ['720p'], durations: [5, 10]},
    {id: 'talking', adapter: 'kie', label: 'Talking portrait', kind: 'video', modes: ['lipsync'], description: '', aspect_ratios: [], resolutions: ['720p'], durations: []},
  ],
}};
const account: AgentProvider = {id: 'or', name: 'Shared OpenRouter', kind: 'openai-compatible', base_url: 'https://openrouter.ai/api/v1', model: 'chat/model', capabilities: ['shell', 'functions', 'image', 'video', 'future-capability'], secret_configured: true, model_overrides: {'chat/model': {context_window: 90000}}, wire: 'chat'};
beforeEach(() => {vi.clearAllMocks(); vi.mocked(loadStudioSettings).mockResolvedValue(structuredClone(envelope)); vi.mocked(loadStudioProviders).mockResolvedValue([structuredClone(account)]); vi.mocked(saveStudioSettings).mockImplementation(async settings => ({...structuredClone(envelope), settings: {...settings, revision: settings.revision + 1}})); vi.mocked(saveStudioProvider).mockResolvedValue({id: 'or'}); Object.defineProperty(document, 'visibilityState', {configurable: true, value: 'visible'});});
afterEach(() => {cleanup(); vi.restoreAllMocks();});
async function ready() {await screen.findByText('Shared OpenRouter', {selector: 'strong'});}
function tab(name: string) {fireEvent.click(screen.getByRole('tab', {name}));}
it('shows a dedicated provider view and saves supported defaults once per click', async () => {
  const onSaved = vi.fn(); render(<StudioSettings ownerId="owner" visible onSaved={onSaved}/>); await ready();
  expect(screen.getByRole('tab', {name: 'Providers'})).toHaveAttribute('aria-selected', 'true');
  tab('Models & defaults'); fireEvent.change(screen.getByLabelText('Images default provider'), {target: {value: 'or'}});
  fireEvent.change(screen.getByLabelText('Images aspect ratio'), {target: {value: '16:9'}});
  const save = screen.getByRole('button', {name: 'Save Studio settings'}); fireEvent.click(save); fireEvent.click(save);
  await screen.findByText('Studio settings saved.');
  expect(saveStudioSettings).toHaveBeenCalledTimes(1); expect(saveStudioSettings).toHaveBeenCalledWith(expect.objectContaining({defaults: {image: {provider_id: 'or', model: 'still', aspect_ratio: '16:9'}}}), expect.any(AbortSignal)); expect(onSaved).toHaveBeenCalledTimes(1);
});
it('keeps no models enabled and rejects an incompatible saved default without writing', async () => {
  render(<StudioSettings visible/>); await ready(); tab('Models & defaults');
  fireEvent.change(screen.getByLabelText('Images default provider'), {target: {value: 'or'}});
  for (const name of ['Still image', 'Film', 'Talking portrait']) fireEvent.click(screen.getByRole('checkbox', {name: `Enable ${name}`}));
  expect(screen.getByText(/No models are enabled/)).toBeInTheDocument(); fireEvent.click(screen.getByRole('button', {name: 'Save Studio settings'}));
  await screen.findByText(/Images: choose an enabled model/); expect(saveStudioSettings).not.toHaveBeenCalled();
});
it('preserves limits on failure and conflict and waits for explicit reload', async () => {
  vi.mocked(saveStudioSettings).mockRejectedValueOnce(Error('Host offline')).mockRejectedValueOnce(new ApiError(409, 'Revision changed'));
  render(<StudioSettings visible/>); await ready(); tab('Generation limits');
  fireEvent.click(screen.getByRole('checkbox', {name: 'Limit daily generations'})); fireEvent.change(screen.getByLabelText('Daily generations limit'), {target: {value: '7'}});
  fireEvent.click(screen.getByRole('button', {name: 'Save Studio settings'})); await screen.findByText(/Host offline/); expect(screen.getByLabelText('Daily generations limit')).toHaveValue(7);
  fireEvent.click(screen.getByRole('button', {name: 'Save Studio settings'})); await screen.findByText(/settings changed in another window/); expect(screen.getByLabelText('Daily generations limit')).toHaveValue(7);
  expect(screen.getByRole('button', {name: 'Save Studio settings'})).toBeDisabled(); fireEvent.click(screen.getByRole('button', {name: 'Reload saved settings'}));
  await waitFor(() => expect(screen.getByRole('checkbox', {name: 'Limit daily generations'})).not.toBeChecked()); expect(saveStudioSettings).toHaveBeenCalledTimes(2);
});
it('requires whole nonnegative limits and accepts zero as an explicit block', async () => {
  render(<StudioSettings visible/>); await ready(); tab('Generation limits'); fireEvent.click(screen.getByRole('checkbox', {name: 'Limit active generations'}));
  fireEvent.change(screen.getByLabelText('Active generations limit'), {target: {value: '1.5'}}); fireEvent.click(screen.getByRole('button', {name: 'Save Studio settings'})); await screen.findByText(/enter a whole number/); expect(saveStudioSettings).not.toHaveBeenCalled();
  fireEvent.change(screen.getByLabelText('Active generations limit'), {target: {value: '0'}}); fireEvent.click(screen.getByRole('button', {name: 'Save Studio settings'})); await screen.findByText('Studio settings saved.'); expect(vi.mocked(saveStudioSettings).mock.calls[0][0].limits.concurrent_generations).toBe(0);
});
it('edits real provider connections while preserving blank keys and shared chat configuration', async () => {
  const changed = vi.fn(); render(<StudioSettings visible onProvidersChanged={changed}/>); await ready(); fireEvent.click(screen.getByRole('button', {name: 'Edit Shared OpenRouter'}));
  expect(screen.getByLabelText('Replace API key')).toHaveValue(''); fireEvent.change(screen.getByLabelText(/Display name/), {target: {value: 'Updated connection'}}); fireEvent.click(screen.getByRole('button', {name: 'Save provider changes'}));
  await screen.findByText(/Provider saved/); expect(saveStudioProvider).toHaveBeenCalledWith(expect.objectContaining({id: 'or', name: 'Updated connection', api_key: null, model: 'chat/model', model_meta: account.model_overrides, wire: 'chat', capabilities: expect.arrayContaining(['shell', 'functions', 'image', 'video', 'future-capability'])})); expect(changed).toHaveBeenCalledTimes(1);
});
it('retains provider key drafts on failure and ordinary hide but clears them on lock', async () => {
  vi.mocked(saveStudioProvider).mockRejectedValue(Error('Provider save unavailable'));
  const view = render(<StudioSettings ownerId="owner" visible/>); await ready(); fireEvent.click(screen.getByRole('button', {name: 'Add provider'}));
  fireEvent.change(screen.getByLabelText(/Display name/), {target: {value: 'Studio fixture'}}); fireEvent.change(screen.getByLabelText('API key'), {target: {value: 'test-fixture-key'}}); fireEvent.click(screen.getByRole('button', {name: 'Connect provider'}));
  await screen.findByText(/Provider save unavailable/); expect(screen.getByLabelText('API key')).toHaveValue('test-fixture-key');
  const calls = vi.mocked(loadStudioSettings).mock.calls; const priorSignal = calls[calls.length - 1][0]; view.rerender(<StudioSettings ownerId="owner" visible={false}/>); expect(priorSignal?.aborted).toBe(true);
  view.rerender(<StudioSettings ownerId="owner" visible observing={false}/>); expect(screen.getByLabelText('API key')).toHaveValue('test-fixture-key'); expect(screen.getByRole('button', {name: 'Connect provider'})).toBeDisabled();
  view.rerender(<StudioSettings ownerId="owner" visible locked/>); expect(screen.queryByLabelText('API key')).not.toBeInTheDocument();
  view.rerender(<StudioSettings ownerId="owner" visible/>); await ready(); fireEvent.click(screen.getByRole('button', {name: 'Add provider'})); expect(screen.getByLabelText('API key')).toHaveValue('');
});
it('aborts hidden reads, ignores stale owner responses, and exposes permission failure', async () => {
  let resolve!: (value: StudioSettingsEnvelope) => void; vi.mocked(loadStudioSettings).mockReturnValueOnce(new Promise(done => resolve = done));
  const view = render(<StudioSettings ownerId="first" visible/>); const signal = vi.mocked(loadStudioSettings).mock.calls[0][0];
  view.rerender(<StudioSettings ownerId="second" visible/>); await ready(); expect(signal?.aborted).toBe(true);
  await act(async () => resolve({...envelope, usage: {...envelope.usage, daily_generations: 99}})); tab('Generation limits'); expect(screen.queryByText('99')).not.toBeInTheDocument();
  vi.mocked(loadStudioSettings).mockRejectedValueOnce(new ApiError(403, 'AI settings permission required')); view.rerender(<StudioSettings ownerId="third" visible/>); await screen.findByText(/You need permission to manage AI settings/); expect(screen.queryByRole('button', {name: 'Add provider'})).not.toBeInTheDocument();
});

it('keeps usable saved catalogs quiet and names the usage group', async () => {
  vi.mocked(loadStudioSettings).mockResolvedValue({...structuredClone(envelope), catalog: {...structuredClone(envelope.catalog), catalog_source: 'snapshot', catalog_error: 'Discovery returned 503.'}});
  render(<StudioSettings visible/>); await ready(); tab('Models & defaults');
  expect(screen.getAllByText('Saved model catalog.')).toHaveLength(1);
  const details = screen.getByText('Discovery returned 503.').closest('details');
  expect(details).not.toHaveAttribute('open'); expect(details?.closest('[role="alert"]')).toBeNull();
  fireEvent.click(screen.getByText('Availability details')); expect(details).toHaveAttribute('open');
  tab('Generation limits'); expect(screen.getByRole('group', {name: 'Current Studio usage'})).toHaveTextContent('accepted today');
});
it('reveals focused and clicked tabs instantly while keeping arrow navigation', async () => {
  const reveal = vi.fn(); Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', {configurable: true, value: reveal});
  render(<StudioSettings visible/>); await ready();
  const first = screen.getByRole('tab', {name: 'Providers'}), last = screen.getByRole('tab', {name: 'Generation limits'});
  act(() => first.focus()); fireEvent.keyDown(first, {key: 'End'});
  expect(last).toHaveFocus(); expect(last).toHaveAttribute('aria-selected', 'true');
  expect(reveal).toHaveBeenLastCalledWith({block: 'nearest', inline: 'nearest', behavior: 'instant'});
  reveal.mockClear(); fireEvent.click(first); expect(reveal).toHaveBeenCalledWith({block: 'nearest', inline: 'nearest', behavior: 'instant'});
  delete (HTMLElement.prototype as {scrollIntoView?: unknown}).scrollIntoView;
});

it('connects an ElevenLabs music account with the real audio declaration only',async()=>{
 render(<StudioSettings ownerId="owner" visible/>);await ready();fireEvent.click(screen.getByRole('button',{name:'Add provider'}));
 fireEvent.change(screen.getByLabelText('Provider'),{target:{value:'elevenlabs'}});fireEvent.change(screen.getByLabelText(/Display name/),{target:{value:'Music account'}});fireEvent.change(screen.getByLabelText('API key'),{target:{value:'fixture-not-a-real-key'}});
 expect(screen.getByRole('checkbox',{name:'Allow music generation'})).toBeChecked();expect(screen.queryByRole('checkbox',{name:'Allow image generation'})).not.toBeInTheDocument();
 fireEvent.click(screen.getByRole('button',{name:'Connect provider'}));await waitFor(()=>expect(saveStudioProvider).toHaveBeenCalledOnce());expect(vi.mocked(saveStudioProvider).mock.calls[0][0]).toMatchObject({name:'Music account',base_url:'https://api.elevenlabs.io',capabilities:['audio'],api_key:'fixture-not-a-real-key'});
});
it('saves a real music default and duration cap while older missing caps start off',async()=>{
 const next=structuredClone(envelope);next.catalog.accounts.push({id:'music',name:'Music account',configured:true,adapter:'elevenlabs',capabilities:['audio']});next.catalog.models.push({id:'elevenlabs/music_v2_5',label:'Eleven Music 2.5',kind:'audio',adapter:'elevenlabs',modes:['music'],description:'',aspect_ratios:[],resolutions:[],durations:[30,60],duration_min:3,duration_max:600});vi.mocked(loadStudioSettings).mockResolvedValue(next);
 render(<StudioSettings ownerId="owner" visible/>);await ready();tab('Models & defaults');fireEvent.change(screen.getByLabelText('Music default provider'),{target:{value:'music'}});fireEvent.change(screen.getByLabelText('Music duration'),{target:{value:'35'}});
 tab('Generation limits');expect(screen.getByRole('checkbox',{name:'Limit music duration'})).not.toBeChecked();fireEvent.click(screen.getByRole('checkbox',{name:'Limit music duration'}));fireEvent.change(screen.getByLabelText('Music duration limit'),{target:{value:'90'}});fireEvent.click(screen.getByRole('button',{name:'Save Studio settings'}));await waitFor(()=>expect(saveStudioSettings).toHaveBeenCalledOnce());expect(vi.mocked(saveStudioSettings).mock.calls[0][0]).toMatchObject({defaults:{music:{provider_id:'music',model:'elevenlabs/music_v2_5',duration:35}},limits:{max_music_seconds:90}});
});


it('offers music for an existing OpenRouter account without silently enabling it or changing shared settings', async () => {
 render(<StudioSettings visible/>); await ready();
 fireEvent.click(screen.getByRole('button', {name: 'Edit Shared OpenRouter'}));
 const music = screen.getByRole('checkbox', {name: 'Allow music generation'});
 expect(music).not.toBeChecked();
 fireEvent.click(music); fireEvent.click(screen.getByRole('button', {name: 'Save provider changes'}));
 await waitFor(() => expect(saveStudioProvider).toHaveBeenCalledOnce());
 expect(saveStudioProvider).toHaveBeenCalledWith(expect.objectContaining({
  id:'or', api_key:null, model:'chat/model', wire:'chat', model_meta:account.model_overrides,
  capabilities:expect.arrayContaining(['shell','functions','image','video','audio','future-capability']),
 }));
});
