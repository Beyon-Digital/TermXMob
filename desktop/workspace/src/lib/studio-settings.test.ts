import {beforeEach, expect, it, vi} from 'vitest';
import {gql, request} from './api';
import {isStudioProvider, loadStudioSettings, loadStudioProviders, saveStudioProvider, saveStudioSettings, studioDefaultModels, studioModelEnabled, studioSettingsErrors, type StudioSettings} from './studio-settings';
import type {StudioCatalog} from './media-studio';
vi.mock('./api', async original => ({...await original<typeof import('./api')>(), request: vi.fn(), gql: vi.fn()}));
const settings: StudioSettings = {revision: 3, defaults: {}, enabled_models: null, limits: {daily_generations: null, concurrent_generations: null, max_video_seconds: null}};
const catalog: StudioCatalog = {accounts: [{id: 'or', name: 'OpenRouter', configured: true, adapter: 'openrouter', capabilities: ['image']}], models: [
  {id: 'still', adapter: 'openrouter', label: 'Still', kind: 'image', modes: ['image', 'cinema'], description: '', aspect_ratios: ['1:1'], resolutions: ['1K'], durations: []},
  {id: 'talking', adapter: 'kie', label: 'Talking', kind: 'video', modes: ['lipsync'], description: '', aspect_ratios: [], resolutions: ['720p'], durations: []},
]};
beforeEach(() => vi.clearAllMocks());
it('sends only settings to the authenticated endpoint without replaying a save', async () => {
  const signal = new AbortController().signal;
  await loadStudioSettings(signal); await saveStudioSettings(settings, signal);
  expect(request).toHaveBeenNthCalledWith(1, '/api/media/studio/settings', {signal});
  expect(request).toHaveBeenNthCalledWith(2, '/api/media/studio/settings', {method: 'PUT', body: JSON.stringify(settings), signal}, false);
});
it('reads saved provider metadata without requesting secrets and uses the existing credential mutation', async () => {
  vi.mocked(request).mockResolvedValue({data: {agent_providers: []}});
  await loadStudioProviders();
  const body = JSON.parse(String(vi.mocked(request).mock.calls[0][1]?.body));
  expect(body.query).toContain('secret_configured'); expect(body.query).not.toContain('api_key');
  vi.mocked(gql).mockResolvedValue({save_agent_provider: {id: 'or'}});
  const input = {id: 'or', name: 'OR', kind: 'openai-compatible', base_url: 'https://openrouter.ai/api/v1', model: '', capabilities: ['image'], api_key: null};
  await saveStudioProvider(input); expect(gql).toHaveBeenCalledWith(expect.stringContaining('save_agent_provider'), {input});
});
it('distinguishes no enabled models from automatically including new models', () => {
  expect(studioModelEnabled(settings, catalog.models[0])).toBe(true);
  expect(studioModelEnabled({...settings, enabled_models: []}, catalog.models[0])).toBe(false);
  expect(studioDefaultModels(catalog, settings, 'image', catalog.accounts[0])).toEqual([catalog.models[0]]);
  expect(studioDefaultModels(catalog, settings, 'lipsync', catalog.accounts[0])).toEqual([]);
});
it('validates supported defaults, disabled models and whole nonnegative limits', () => {
  expect(studioSettingsErrors({...settings, limits: {daily_generations: 0, concurrent_generations: 2, max_video_seconds: 10}, defaults: {image: {provider_id: 'or', model: 'still', aspect_ratio: '1:1', resolution: '1K'}}}, catalog)).toEqual([]);
  const errors = studioSettingsErrors({...settings, enabled_models: [], defaults: {image: {provider_id: 'or', model: 'still'}}, limits: {...settings.limits, daily_generations: 1.5}}, catalog);
  expect(errors.join(' ')).toMatch(/whole number/); expect(errors.join(' ')).toMatch(/enabled model/);
  expect(studioSettingsErrors({...settings, defaults: {image: {provider_id: 'or', model: 'still', resolution: '8K', duration: 8}}}, catalog).join(' ')).toMatch(/supported resolution/);
});
it('recognizes the same provider origin and path aliases as the host', () => {
  const row = {id: 'or', name: 'OR', kind: 'openai-compatible'};
  for (const host of ['openrouter.ai', 'api.kie.ai']) {
    for (const path of ['', '/', '/v1', '/v1/', '/api/v1', '/api/v1/']) {
      expect(isStudioProvider({...row, base_url: `https://${host}${path}`})).toBe(true);
      expect(isStudioProvider({...row, base_url: `https://${host}:443${path}`})).toBe(true);
    }
  }
});
it('rejects unsupported kinds and unsafe or unimplemented provider addresses', () => {
  const row = {id: 'or', name: 'OR', kind: 'openai-compatible'};
  for (const base_url of ['https://api.openai.com/v1', 'https://openrouter.ai.attacker.example/api/v1', 'http://api.kie.ai', 'https://api.kie.ai/other', 'https://openrouter.ai:8443/v1', 'https://user:secret@openrouter.ai/api/v1', 'https://api.kie.ai?key=fixture', 'https://openrouter.ai/api/v1#fragment', 'https://openrouter.ai/api/../v1']) expect(isStudioProvider({...row, base_url})).toBe(false);
  for (const kind of ['openai', 'other', '']) expect(isStudioProvider({...row, kind, base_url: 'https://openrouter.ai/api/v1'})).toBe(false);
});

it('supports exact ElevenLabs music origins, defaults and the optional music duration cap',()=>{
 const provider={id:'music',name:'Music',kind:'openai-compatible'};
 for(const path of ['', '/', '/v1', '/v1/'])expect(isStudioProvider({...provider,base_url:'https://api.elevenlabs.io'+path})).toBe(true);
 for(const address of ['https://api.elevenlabs.io/api/v1','https://api.elevenlabs.io.attacker.example','http://api.elevenlabs.io','https://user:key@api.elevenlabs.io'])expect(isStudioProvider({...provider,base_url:address})).toBe(false);
 const music:StudioCatalog={accounts:[{id:'music',name:'Music',adapter:'elevenlabs',configured:true,capabilities:['audio']}],models:[{id:'elevenlabs/music_v2_5',label:'Eleven Music 2.5',kind:'audio',adapter:'elevenlabs',modes:['music'],description:'',aspect_ratios:[],resolutions:[],durations:[30,60],duration_min:3,duration_max:600}]};
 expect(studioSettingsErrors({...settings,defaults:{music:{provider_id:'music',model:'elevenlabs/music_v2_5',duration:35}},limits:{...settings.limits,max_music_seconds:90}},music)).toEqual([]);
 expect(studioSettingsErrors({...settings,defaults:{music:{provider_id:'music',model:'elevenlabs/music_v2_5',duration:601}}},music).join(' ')).toContain('supported duration');
});
