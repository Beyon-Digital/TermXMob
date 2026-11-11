import {ApiError, gql, json, request} from './api';
import type {AgentProvider, ProviderAccountInput} from './host-types';
import type {StudioAccount, StudioCatalog, StudioModel, StudioOperation} from './media-studio';

export type StudioDefault = {provider_id: string; model: string; aspect_ratio?: string; resolution?: string; duration?: number};
export type StudioLimitKey = 'daily_generations' | 'concurrent_generations' | 'max_video_seconds' | 'max_music_seconds';
export type StudioSettings = {
  revision: number;
  defaults: Partial<Record<StudioOperation, StudioDefault>>;
  enabled_models: string[] | null;
  limits: Record<Exclude<StudioLimitKey, 'max_music_seconds'>, number | null> & {max_music_seconds?: number | null};
};
export type StudioSettingsEnvelope = {
  settings: StudioSettings;
  usage: {daily_generations: number; active_generations: number; reset_at: number};
  catalog: StudioCatalog;
};
export const studioOperations: {id: StudioOperation; label: string}[] = [
  {id: 'image', label: 'Images'}, {id: 'video', label: 'Video'}, {id: 'lipsync', label: 'Lip sync'}, {id: 'remix', label: 'Remix'}, {id: 'music', label: 'Music'},
];
export const studioLimitFields: {id: StudioLimitKey; label: string; hint: string; unit: string}[] = [
  {id: 'daily_generations', label: 'Daily generations', hint: 'Accepted generations per UTC day.', unit: 'generations'},
  {id: 'concurrent_generations', label: 'Active generations', hint: 'Generations that can be active together.', unit: 'generations'},
  {id: 'max_video_seconds', label: 'Video duration', hint: 'Maximum seconds for each video generation.', unit: 'seconds'},
  {id: 'max_music_seconds', label: 'Music duration', hint: 'Maximum seconds for each music generation.', unit: 'seconds'},
];

export function loadStudioSettings(signal?: AbortSignal) {
  return request<StudioSettingsEnvelope>('/api/media/studio/settings', {signal});
}
export function saveStudioSettings(settings: StudioSettings, signal?: AbortSignal) {
  return request<StudioSettingsEnvelope>('/api/media/studio/settings', {...json('PUT', settings), signal}, false);
}
const providerFields = 'id name kind model models base_url secret_configured capabilities model_catalog model_overrides catalog_refreshed_at catalog_error wire limits';
/** Read the same saved accounts as Agent settings. No credential is returned. */
export async function loadStudioProviders(signal?: AbortSignal) {
  const body = await request<{data?: {agent_providers: AgentProvider[]}; errors?: {message: string; extensions?: {http_status?: number; code?: string}}[]}>(
    '/graphql', {...json('POST', {query: `{agent_providers { ${providerFields} }}`}), signal},
  );
  if (body.errors?.length) {
    const first = body.errors[0];
    const status = first.extensions?.http_status || ({FORBIDDEN: 403, UNAUTHENTICATED: 401, LOCKED: 423} as Record<string, number>)[first.extensions?.code || ''];
    const message = body.errors.map(error => error.message).join('\n');
    throw status ? new ApiError(status, message) : Error(message);
  }
  if (!Array.isArray(body.data?.agent_providers)) throw Error('The host returned no provider accounts. Retry to load them.');
  return body.data.agent_providers;
}
/** Uses the existing host credential store and mutation, shared with Agent settings. */
export async function saveStudioProvider(input: ProviderAccountInput) {
  const value = await gql<{save_agent_provider?: {id: string}}>('mutation($input:AgentProviderInput!){save_agent_provider(input:$input){id}}', {input});
  if (!value.save_agent_provider?.id) throw Error('The host did not confirm the provider save. Reload providers before retrying.');
  return value.save_agent_provider;
}
export const studioModelEnabled = (settings: StudioSettings, model: StudioModel) => settings.enabled_models === null || settings.enabled_models.includes(model.id);
export function studioDefaultModels(catalog: StudioCatalog, settings: StudioSettings, operation: StudioOperation, account?: StudioAccount) {
  return catalog.models.filter(model => model.modes.includes(operation) && studioModelEnabled(settings, model) &&
    (!account || (model.adapter === account.adapter && account.capabilities.includes(model.kind))));
}
export function studioSettingsErrors(settings: StudioSettings, catalog: StudioCatalog) {
  const errors: string[] = [];
  for (const field of studioLimitFields) {
    const value = settings.limits[field.id] ?? null;
    if (value !== null && (!Number.isSafeInteger(value) || value < 0)) errors.push(`${field.label}: enter a whole number of 0 or more, or turn the limit off.`);
  }
  for (const {id, label} of studioOperations) {
    const saved = settings.defaults[id];
    if (!saved) continue;
    const account = catalog.accounts.find(item => item.id === saved.provider_id);
    const model = catalog.models.find(item => item.id === saved.model && item.adapter === account?.adapter);
    if (!account?.configured || !model || !model.modes.includes(id) || !account.capabilities.includes(model.kind) || !studioModelEnabled(settings, model)) {
      errors.push(`${label}: choose an enabled model with a connected, compatible provider, or use Automatic.`);
      continue;
    }
    if (saved.aspect_ratio && !model.aspect_ratios.includes(saved.aspect_ratio)) errors.push(`${label}: choose a supported aspect ratio.`);
    if (saved.resolution && !model.resolutions.includes(saved.resolution)) errors.push(`${label}: choose a supported resolution.`);
    if (saved.duration !== undefined && (id === 'music' ? !Number.isInteger(saved.duration) || saved.duration < (model.duration_min || 3) || saved.duration > (model.duration_max || 600) : !model.durations.includes(saved.duration))) errors.push(`${label}: choose a supported duration.`);
  }
  return errors;
}
/** Match the host's implemented adapter aliases without broadening provider destinations. */
export function isStudioProvider(provider: AgentProvider) {
  if (provider.kind !== 'openai-compatible') return false;
  try {
    const address = provider.base_url || '', url = new URL(address);
    // Keep the supplied path: URL normalizes dot segments that the host rejects.
    const original = /^[a-z][a-z0-9+.-]*:\/\/[^/?#]*([^?#]*)/i.exec(address);
    if (!original || address.includes('\\') || url.protocol !== 'https:' || url.username || url.password || url.search || url.hash || (url.port && url.port !== '443')) return false;
    const path = original[1].replace(/\/+$/, '');
    return url.hostname === 'api.elevenlabs.io' ? ['', '/v1'].includes(path) : ['openrouter.ai', 'api.kie.ai'].includes(url.hostname) && ['', '/v1', '/api/v1'].includes(path);
  } catch { return false; }
}
