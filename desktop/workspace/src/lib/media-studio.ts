import {json, request} from './api';
import {clearMediaIntent, mediaIntent} from './media-intent';
import {transfer} from './transfers';

export type StudioMode = 'image' | 'video' | 'cinema' | 'lipsync' | 'remix';
export type StudioOperation = 'image' | 'video' | 'lipsync' | 'remix' | 'music';
export type StudioImageRole = 'first_frame' | 'reference';
export type StudioImageReference = {image_id: string; image_url?: never} | {image_url: string; image_id?: never};
export type StudioModel = {
  adapter: 'openrouter' | 'kie' | 'elevenlabs';
  id: string;
  label: string;
  kind: 'image' | 'video' | 'audio';
  modes: (StudioMode | 'music')[];
  description: string;
  aspect_ratios: string[];
  resolutions: string[];
  durations: number[];
  supported_music_modes?: ('simple' | 'custom')[];
  supports_custom_lyrics?: boolean;
  supports_instrumental?: boolean;
  supports_negative_styles?: boolean;
  duration_min?: number;
  duration_max?: number;
  custom_duration_max?: number;
  duration_mode?: 'exact' | 'prompt' | 'fixed';
  prompt_max_length?: number;
  lyrics_max_length?: number;
  lyrics_constraints?: 'sections' | 'prompt';
  supports_image?: boolean;
  supports_negative_prompt?: boolean;
  supports_audio?: boolean;
  supports_video?: boolean;
  requires_video_for_remix?: boolean;
  image_roles?: StudioImageRole[];
  image_reference_limits?: {min: number; max: number; app_max: number};
  inline_inputs?: ('image' | 'audio' | 'video')[];
  requires_image?: boolean;
  requires_audio?: boolean;
  requires_video?: boolean;
};
export type StudioAccount = {id: string; name: string; configured: boolean; adapter: 'openrouter' | 'kie' | 'elevenlabs'; capabilities: string[]};
export type StudioDefault = {provider_id: string; model: string; aspect_ratio?: string; resolution?: string; duration?: number};
export type StudioSettings = {revision: number; defaults: Partial<Record<StudioOperation, StudioDefault>>; enabled_models: string[] | null; limits: {daily_generations: number | null; concurrent_generations: number | null; max_video_seconds: number | null; max_music_seconds?: number | null}};
export type StudioCatalog = {settings?: StudioSettings; models: StudioModel[]; accounts: StudioAccount[]; catalog_source?: 'live' | 'snapshot'; catalog_error?: string};
/** Only supported account, model and parameter combinations can initialize a draft. */
export function studioDefaultSelection(catalog: StudioCatalog | null, mode: StudioMode | 'music'): StudioDefault | undefined {
  if (!catalog) return;
  const operation = mode === 'cinema' ? 'image' : mode;
  const models = catalog.models.filter(model => model.modes.includes(operation) && (mode !== 'cinema' || model.kind === 'image'));
  const accounts = catalog.accounts.filter(account => models.some(model => model.adapter === account.adapter && account.capabilities.includes(model.kind)));
  const preferred = catalog.settings?.defaults[operation];
  const preferredAccount = accounts.find(account => account.id === preferred?.provider_id);
  const preferredModel = models.find(model => model.id === preferred?.model && model.adapter === preferredAccount?.adapter && preferredAccount.capabilities.includes(model.kind));
  const account = preferredModel ? preferredAccount : accounts.find(account => account.configured && account.adapter === 'openrouter') || accounts.find(account => account.configured) || accounts.find(account => account.adapter === 'openrouter') || accounts[0];
  const model = preferredModel || models.find(model => model.adapter === account?.adapter && account?.capabilities.includes(model.kind));
  if (!account || !model) return;
  const defaults = preferredModel ? preferred : undefined;
  return {provider_id: account.id, model: model.id,
    ...(model.aspect_ratios.length && {aspect_ratio: model.aspect_ratios.includes(defaults?.aspect_ratio || '') ? defaults!.aspect_ratio : model.aspect_ratios[0]}),
    ...(model.resolutions.length && {resolution: model.resolutions.includes(defaults?.resolution || '') ? defaults!.resolution : model.resolutions[0]}),
    ...(model.durations.length && {duration: model.durations.includes(defaults?.duration || 0) ? defaults!.duration : model.durations[0]}),
  };
}
export type StudioJob = {
  id: string;
  request_id?: string;
  project_id: string | null;
  provider_id: string;
  model: string;
  kind: 'image' | 'video' | 'audio';
  prompt: string;
  status: 'submitting' | 'queued' | 'running' | 'completed' | 'failed' | 'unknown';
  created: number;
  updated: number;
  outputs: string[];
  error?: string;
  parameters?: Partial<Omit<StudioInput, 'provider_id' | 'model' | 'prompt' | 'acknowledge_billing' | 'image_references'>> & {
    music_mode?: 'simple' | 'custom'; lyrics?: string; styles?: string; negative_styles?: string; title?: string; instrumental?: boolean;
    image_references?: (StudioImageReference | {has_image_reference: true})[];
    has_image_reference?: boolean; has_audio_reference?: boolean; has_video_reference?: boolean;
  };
};
export type StudioInput = {
  provider_id: string;
  model: string;
  prompt: string;
  aspect_ratio?: string;
  resolution?: string;
  duration?: number;
  image_url?: string;
  audio_url?: string;
  video_url?: string;
  image_id?: string;
  image_references?: StudioImageReference[];
  audio_id?: string;
  video_id?: string;
  operation?: StudioOperation;
  image_role?: StudioImageRole;
  negative_prompt?: string;
  acknowledge_billing: boolean;
};
export type StudioHistory = {items: StudioJob[]; next_cursor: string | null};
export type StudioHistoryQuery = {cursor?: string; kind?: 'image' | 'video'; status?: StudioJob['status'] | 'active' | 'attention'; q?: string; limit?: number};
export function loadStudioHistory(projectId: string | null, query: StudioHistoryQuery = {}, signal?: AbortSignal) {
  const params = new URLSearchParams();
  if (projectId) params.set('project_id', projectId);
  params.set('limit', String(query.limit || 40));
  if (query.cursor) params.set('cursor', query.cursor);
  if (query.kind) params.set('kind', query.kind);
  if (query.status) params.set('status', query.status);
  if (query.q?.trim()) params.set('q', query.q.trim());
  return request<StudioHistory>('/api/media/studio/history?' + params, {signal});
}
export function mergeStudioJobs(previous: StudioJob[], incoming: StudioJob[]) {
  const byId = new Map(previous.map(job => [job.id, job]));
  const rank = {submitting: 0, queued: 1, running: 2, unknown: 3, failed: 4, completed: 4};
  for (const job of incoming) {
    const prior = byId.get(job.id);
    if (prior && (prior.updated > job.updated ||
        (prior.updated === job.updated && rank[prior.status] >= rank[job.status] && prior.status !== job.status))) continue;
    byId.set(job.id, {...prior, ...job, parameters: job.parameters ?? prior?.parameters});
  }
  return [...byId.values()].sort((a, b) => b.created - a.created || b.id.localeCompare(a.id));
}
export const STUDIO_IMAGE_BYTES = 8 * 1024 * 1024;
export const STUDIO_IMAGES_ENCODED_BYTES = 20 * 1024 * 1024;
export function studioImageLimit(model: StudioModel | undefined) {
  if (!model || !(model.supports_image || model.requires_image)) return 0;
  const limit = model.image_reference_limits?.app_max;
  return limit === undefined ? 1 : Number.isInteger(limit) ? Math.max(0, Math.min(5, limit)) : 0;
}
export async function uploadStudioImage(file: File, projectId: string | null, signal?: AbortSignal) {
  if (!['image/png', 'image/jpeg', 'image/webp'].includes(file.type))
    throw Error('Choose a static PNG, JPEG or WebP image.');
  if (!file.size || file.size > STUDIO_IMAGE_BYTES) throw Error('Choose an image between 1 byte and 8 MiB.');
  const params = new URLSearchParams({name: file.name});
  if (projectId) params.set('project_id', projectId);
  const response = await transfer('/api/media/inputs?' + params, {
    method: 'POST', body: file, headers: {'Content-Type': file.type}, signal,
  });
  const row = JSON.parse(await response.text()) as {id: string; project: string | null; mime: string; kind: string};
  if (!row.id || row.project !== projectId || !['image/png', 'image/jpeg', 'image/webp'].includes(row.mime))
    throw Error('The uploaded reference did not match this workspace. Choose the image again.');
  return {id: row.id, name: file.name};
}

export function loadStudioCatalog(projectId: string | null, signal?: AbortSignal) {
  return request<StudioCatalog>('/api/media/studio/catalog' +
    (projectId ? '?project_id=' + encodeURIComponent(projectId) : ''), {signal});
}
export function loadStudioJobs(projectId: string | null, signal?: AbortSignal) {
  return request<StudioJob[]>(
    '/api/media/studio/jobs' + (projectId ? '?project_id=' + encodeURIComponent(projectId) : ''),
    {signal},
  );
}
export function refreshStudioJob(id: string, signal?: AbortSignal) {
  return request<StudioJob>('/api/media/studio/jobs/' + encodeURIComponent(id) + '/refresh', {
    ...json('POST', {}), signal,
  });
}
export async function submitStudioJob(
  ownerId: string | undefined,
  projectId: string | null,
  input: StudioInput,
  signal?: AbortSignal,
) {
  const args = {project_id: projectId, ...input};
  const intent = await mediaIntent(ownerId, projectId, args, 'studio');
  // Do not start a billed request if the workspace changed or locked while hashing.
  if (signal?.aborted) throw new DOMException('Request cancelled', 'AbortError');
  const job = await request<StudioJob>('/api/media/studio/jobs', {
    ...json('POST', {request_id: intent.id, ...args}), signal,
  });
  if (!job || typeof job.id !== 'string' || !job.id) {
    throw Error('The host did not return a job receipt. Refresh your library before retrying.');
  }
  // A failed/unknown job with a receipt was still accepted. A transport error was not proven so.
  clearMediaIntent(intent.key, intent.id);
  return job;
}

// Keep the source studio's operation names while routing every operation through
// the authenticated host. Provider endpoints, credentials and polling stay server-side.
export function createStudioClient(ownerId: string | undefined, projectId: string | null) {
  const generate = (params: StudioInput, signal?: AbortSignal) =>
    submitStudioJob(ownerId, projectId, params, signal);
  return {
    generateImage: generate,
    generateVideo: generate,
    processLipSync: generate,
    remixVideo: generate,
    pollForResult: refreshStudioJob,
  };
}

export const activeStudioJob = (job: StudioJob) =>
  ['submitting', 'queued', 'running'].includes(job.status);

/** Outputs are provider-hosted; never allow executable or local URL schemes. */
export function studioOutputUrl(value: string): string | null {
  if (/^\/api\/media\/artifacts\/[a-zA-Z0-9_-]+\/export\?format=original$/.test(value)) return value;
  try {
    const url = new URL(value);
    return url.protocol === 'https:' && !url.username && !url.password ? url.href : null;
  } catch { return null; }
}
export function studioArtifactId(value: string) {
  return /^\/api\/media\/artifacts\/([a-zA-Z0-9_-]+)\/export\?format=original$/.exec(value)?.[1] || null;
}

/** The provider must be able to fetch references without a local session or VPN. */
export function isPublicMediaUrl(value: string) {
  const safe = studioOutputUrl(value);
  if (!safe || safe.startsWith('/')) return false;
  const host = new URL(safe).hostname.toLowerCase();
  if (!host.includes('.') || host.endsWith('.local') || host.endsWith('.localhost') ||
      host === 'localhost' || host.startsWith('[')) return false;
  const ipv4 = host.split('.').map(Number);
  if (ipv4.length === 4 && ipv4.every(n => Number.isInteger(n) && n >= 0 && n <= 255)) {
    const [a, b] = ipv4;
    if (a === 0 || a === 10 || a === 127 || a >= 224 ||
        (a === 169 && b === 254) || (a === 172 && b >= 16 && b <= 31) ||
        (a === 192 && b === 168) || (a === 100 && b >= 64 && b <= 127) ||
        (a === 198 && (b === 18 || b === 19))) return false;
  }
  return true;
}

// Adapted from vloop-studio's camera prompt builder. These are aesthetic prompt
// instructions; the selected image model determines actual output resolution.
export const cinemaCameras: Record<string, string> = {
  'Modular 8K Digital': 'modular 8K digital cinema camera',
  'Full-Frame Cine Digital': 'full-frame digital cinema camera',
  'Grand Format 70mm Film': 'grand format 70mm film camera',
  'Studio Digital S35': 'Super 35 studio digital camera',
  'Classic 16mm Film': 'classic 16mm film camera',
  'Premium Large Format Digital': 'premium large-format digital cinema camera',
};
export const cinemaLenses: Record<string, string> = {
  'Premium Modern Prime': 'premium modern prime lens',
  'Compact Anamorphic': 'compact anamorphic lens',
  'Classic Anamorphic': 'classic anamorphic lens',
  '70s Cinema Prime': '1970s cinema prime lens',
  'Warm Cinema Prime': 'warm-toned cinema prime lens',
  'Vintage Prime': 'vintage prime lens',
  'Swirl Bokeh Portrait': 'swirl bokeh portrait lens',
  'Extreme Macro': 'extreme macro lens',
  'Creative Tilt Lens': 'creative tilt lens effect',
  'Halation Diffusion': 'halation diffusion filter',
  'Clinical Sharp Prime': 'ultra-sharp clinical prime lens',
};
export const cinemaFocals: Record<string, string> = {
  '8': 'ultra-wide perspective', '14': 'wide-angle perspective',
  '24': 'wide-angle dynamic perspective', '35': 'natural cinematic perspective',
  '50': 'standard portrait perspective', '85': 'classic portrait perspective',
};
export const cinemaApertures: Record<string, string> = {
  'f/1.4': 'shallow depth of field, creamy bokeh',
  'f/4': 'balanced depth of field',
  'f/11': 'deep focus clarity, sharp foreground to background',
};
export type CinemaSettings = {camera: string; lens: string; focal: string; aperture: string};
export const defaultCinema: CinemaSettings = {
  camera: 'Full-Frame Cine Digital', lens: 'Premium Modern Prime', focal: '35', aperture: 'f/1.4',
};
export function buildCinemaPrompt(prompt: string, settings: CinemaSettings) {
  return [
    prompt.trim(), 'cinematic still photograph',
    'shot on a ' + (cinemaCameras[settings.camera] || settings.camera),
    'using a ' + (cinemaLenses[settings.lens] || settings.lens) + ' at ' + settings.focal +
      'mm (' + (cinemaFocals[settings.focal] || 'natural perspective') + ')',
    'aperture ' + settings.aperture, cinemaApertures[settings.aperture],
    'cinematic lighting', 'natural color science', 'high dynamic range',
    'professional photography', 'ultra-detailed',
  ].filter(Boolean).join(', ');
}

export const studioPresets = [
  {id: 'portrait', label: 'Portrait', category: 'Light & character', prompt: 'An intimate editorial portrait in a sunlit studio, soft window light, natural skin texture, a quiet expression, warm neutral palette.'},
  {id: 'product', label: 'Product', category: 'Form & detail', prompt: 'A sculptural ceramic object on a limestone plinth, soft directional studio light, precise material detail, warm ivory background, minimalist editorial composition.'},
  {id: 'landscape', label: 'Landscape', category: 'Scale & atmosphere', prompt: 'A solitary cabin beside an alpine lake at dawn, drifting mist, layered mountain silhouettes, muted forest greens, wide composition and tranquil atmosphere.'},
  {id: 'cinematic', label: 'Cinematic', category: 'Mood & story', prompt: 'A lone traveler at a rain-soaked train station at blue hour, warm light spilling from the windows, subtle film grain, rich shadows, a moment before departure.'},
];

// Source vloop camera/restyle presets are prompt text, not model capabilities.
export const studioMotionPresets = [
  {label: 'Static', prompt: 'Locked-off static camera, no camera movement.'},
  {label: 'Dolly in', prompt: 'Slow, smooth dolly-in toward the subject.'},
  {label: 'Orbit', prompt: 'Camera orbits smoothly around the subject in a full circle.'},
  {label: 'Tracking', prompt: 'Tracking shot following alongside the subject as it moves.'},
  {label: 'Handheld', prompt: 'Handheld camera with natural, subtle shake.'},
  {label: 'Aerial', prompt: 'High aerial drone shot slowly gliding over the scene.'},
];
export const studioRestylePresets = [
  {label: 'Cel animation', prompt: 'hand-painted 2D cel animation'},
  {label: 'Claymation', prompt: 'claymation with handmade texture'},
  {label: 'Watercolour', prompt: 'soft watercolour painting'},
  {label: 'Film noir', prompt: 'black-and-white film noir with hard shadows'},
];
