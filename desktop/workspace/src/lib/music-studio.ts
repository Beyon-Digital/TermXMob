import {json, request} from './api';
import {mediaIntent, clearMediaIntent} from './media-intent';
import {studioArtifactId, type StudioCatalog, type StudioJob, type StudioModel} from './media-studio';

export type MusicMode = 'simple' | 'custom';
export type MusicDraft = {mode: MusicMode; prompt: string; lyrics: string; styles: string; negative_styles: string; title: string; instrumental: boolean; duration: number};
export type MusicInput = MusicDraft & {provider_id: string; model: string; acknowledge_billing: boolean};
export type MusicModel = StudioModel & {kind: 'audio'};
export type MusicJob = StudioJob & {kind: 'audio'; request_id?: string};
export type MusicHistory = {items: MusicJob[]; next_cursor: string | null};
export const defaultMusicDraft: MusicDraft = {mode: 'simple', prompt: '', lyrics: '', styles: '', negative_styles: '', title: '', instrumental: false, duration: 60};
export const musicModels = (catalog: StudioCatalog | null) => (catalog?.models || []).filter((model): model is MusicModel => model.kind === 'audio' && model.modes.includes('music'));
export const musicTitle = (job: MusicJob) => job.parameters?.title || job.prompt.slice(0, 70) || 'Untitled track';
export const musicStatus = (job: MusicJob) => ({submitting: 'Submitting', queued: 'Queued', running: 'Generating', completed: 'Completed', failed: 'Failed', unknown: 'Needs review'}[job.status] || 'Needs review');
export const musicOutput = (job: MusicJob) => job.status === 'completed' ? job.outputs.find(url => !!studioArtifactId(url)) || null : null;
export function musicDuration(seconds: number) {const value = Math.max(0, Math.floor(Number.isFinite(seconds) ? seconds : 0)); return Math.floor(value / 60) + ':' + String(value % 60).padStart(2, '0');}
export function musicSupportsMode(model: MusicModel | undefined, mode: MusicMode) {
 return !!model && (model.supported_music_modes || ['simple']).includes(mode) && (mode !== 'custom' || model.supports_custom_lyrics === true);
}
export function musicDurationBounds(model: MusicModel | undefined, mode: MusicMode) {
 const min = model?.duration_min ?? model?.durations[0] ?? 3;
 const max = model?.duration_max ?? model?.durations.at(-1) ?? 600;
 return {min, max: mode === 'custom' ? Math.min(max, model?.custom_duration_max ?? max) : max};
}
export function musicModelDuration(draft: MusicDraft, model: MusicModel) {
 const {min, max} = musicDurationBounds(model, draft.mode);
 return Math.min(max, Math.max(min, Number.isFinite(draft.duration) ? Math.round(draft.duration) : min));
}
export const musicLyricsConstraints = (model: MusicModel | undefined) => model?.lyrics_constraints || (model?.adapter === 'elevenlabs' ? 'sections' : 'prompt');
export const musicTextLimits = (model: MusicModel | undefined) => ({prompt: model?.prompt_max_length ?? 4100, lyrics: model?.lyrics_max_length ?? 12000});
export function musicDraftErrors(draft: MusicDraft, model?: MusicModel, maxMusicSeconds?: number | null) {
 const errors: string[] = [];
 if (!model) return ['Choose a supported music model.'];
 const {min, max} = musicDurationBounds(model, draft.mode), limits = musicTextLimits(model);
 if (!musicSupportsMode(model, draft.mode)) errors.push(`This model does not support ${draft.mode === 'custom' ? 'Advanced' : 'Simple'} mode.`);
 if (!Number.isInteger(draft.duration) || draft.duration < min || draft.duration > max) errors.push(min === max ? `This model creates ${min}-second tracks.` : `Choose a duration from ${min} to ${max} seconds.`);
 if (maxMusicSeconds !== undefined && maxMusicSeconds !== null && (model.duration_mode === 'prompt' ? model.duration_max ?? max : draft.duration) > maxMusicSeconds) errors.push(model.duration_mode === 'prompt' || model.duration_mode === 'fixed' ? `This model can exceed your ${maxMusicSeconds}-second Music limit. Choose another model or update Studio settings.` : `Choose a duration within your ${maxMusicSeconds}-second Music limit.`);
 if (draft.mode === 'simple' && draft.prompt.trim().length < 3) errors.push('Describe the music you want to create.');
 if (draft.mode === 'simple' && draft.lyrics.trim()) errors.push('Choose Advanced mode to use lyrics.');
 if (draft.mode === 'custom' && !draft.instrumental && draft.prompt.trim()) errors.push('Use Styles for custom music directions.');
 if (draft.mode === 'custom' && !draft.instrumental && !draft.lyrics.trim()) errors.push(model.supports_instrumental ? 'Add lyrics or leave lyrics blank for instrumental music.' : 'Add lyrics for this model.');
 if (draft.instrumental && model.supports_instrumental !== true) errors.push('This model does not support instrumental music.');
 if (draft.mode === 'custom' && draft.instrumental && draft.lyrics.trim()) errors.push('Remove lyrics before creating instrumental music.');
 if (draft.mode === 'custom' && draft.instrumental && (draft.styles + draft.prompt).trim().length < 3) errors.push('Describe a style for your instrumental track.');
 if (draft.negative_styles.trim() && model.supports_negative_styles !== true) errors.push('This model does not support excluded styles.');
 const providerPrompt = [draft.prompt, ...(draft.styles.trim() ? ['Styles: ' + draft.styles] : []), ...(draft.negative_styles.trim() ? ['Avoid these styles: ' + draft.negative_styles] : [])].filter(Boolean).join('\n\n');
 if (((draft.mode === 'simple' || draft.instrumental) && providerPrompt.length > limits.prompt) || draft.prompt.length > limits.prompt || draft.lyrics.length > limits.lyrics || draft.styles.length > 1000 || draft.negative_styles.length > 1000 || draft.title.length > 200) errors.push('Shorten the text to the limits shown in the editor.');
 if (draft.mode === 'custom' && !draft.instrumental && musicLyricsConstraints(model) === 'sections') {
  const markers = [...draft.lyrics.matchAll(/^\[([^\]\r\n]*)\]/gm)];
  if (markers.some(marker => !marker[1].trim() || marker[1].length > 100)) errors.push('Section names must contain 1 to 100 characters.');
  const boundaries = [0, ...markers.map(marker => marker.index).filter(index => index > 0), draft.lyrics.length];
  const sections = boundaries.slice(0, -1).map((start, index) => draft.lyrics.slice(start, boundaries[index + 1]));
  if (sections.length > 30) errors.push('Use at most 30 lyric sections.');
  if (draft.duration < sections.length * 3) errors.push('Allow at least 3 seconds for each lyric section.');
  if (sections.some(section => {const lines = section.replace(/\r\n/g, '\n').split('\n'); if (lines.at(-1) === '') lines.pop(); return lines.length > 30 || lines.some(line => line.length > 200);})) errors.push('Each lyric section supports at most 30 lines and 200 characters per line.');
  if (draft.styles.split(',').filter(value => value.trim()).length > 50 || draft.negative_styles.split(',').filter(value => value.trim()).length > 50) errors.push('Choose at most 50 styles and 50 excluded styles.');
 }
 return errors;
}
export function musicDraftFromJob(job: MusicJob): MusicDraft {
 const value = job.parameters || {};
 return {mode: value.music_mode === 'custom' ? 'custom' : 'simple', prompt: job.prompt || '', lyrics: value.lyrics || '', styles: value.styles || '', negative_styles: value.negative_styles || '', title: value.title || '', instrumental: !!value.instrumental, duration: value.duration || 60};
}
export function mergeMusicJobs(previous: MusicJob[], incoming: MusicJob[]) {
 const rank = {submitting: 0, queued: 1, running: 2, unknown: 3, failed: 4, completed: 4};
 const rows = new Map(previous.map(job => [job.id, job]));
 for (const job of incoming) {const old = rows.get(job.id); if (!old || job.updated > old.updated || (job.updated === old.updated && rank[job.status] >= rank[old.status])) rows.set(job.id, {...old, ...job});}
 return [...rows.values()];
}
export async function loadMusicJobs(project: string | null, signal?: AbortSignal) {
 const rows = await request<StudioJob[]>('/api/media/studio/jobs' + (project ? '?project_id=' + encodeURIComponent(project) : ''), {signal});
 return rows.filter((job): job is MusicJob => job.kind === 'audio' && job.project_id === project);
}
export async function loadMusicHistory(project: string | null, query = '', cursor?: string | null, signal?: AbortSignal) {
 const params = new URLSearchParams({kind: 'audio', limit: '40'}); if (project) params.set('project_id', project); if (query.trim()) params.set('q', query.trim()); if (cursor) params.set('cursor', cursor);
 const result = await request<{items: StudioJob[]; next_cursor: string | null}>('/api/media/studio/history?' + params, {signal});
 return {...result, items: result.items.filter((job): job is MusicJob => job.kind === 'audio' && job.project_id === project)};
}
export async function refreshMusicJob(id: string, signal?: AbortSignal) {return request<MusicJob>('/api/media/studio/jobs/' + encodeURIComponent(id) + '/refresh', {...json('POST', {}), signal});}
export async function submitMusic(owner: string | undefined, project: string | null, input: MusicInput, signal?: AbortSignal, onIntent?: (id: string) => void, canSubmit?: () => boolean) {
 if (!input.acknowledge_billing) throw Error('Review and confirm provider charges before creating music.');
 const args = {project_id: project, ...input};
 const intent = await mediaIntent(owner, project, args, 'studio-music');
 if (signal?.aborted || canSubmit && !canSubmit()) throw new DOMException('Music request cancelled before dispatch', 'AbortError');
 onIntent?.(intent.id);
 if (signal?.aborted || canSubmit && !canSubmit()) throw new DOMException('Music request cancelled before dispatch', 'AbortError');
 const job = await request<MusicJob>('/api/media/studio/music/jobs', {...json('POST', {request_id: intent.id, ...args}), signal});
 if (!job?.id || job.kind !== 'audio' || job.project_id !== project || job.request_id !== intent.id) throw Error('The host returned no matching music receipt. Refresh your songs before trying again.');
 clearMediaIntent(intent.key, intent.id);
 return job;
}
