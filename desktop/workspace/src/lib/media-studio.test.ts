import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest';
import {request} from './api';
import {clearMediaIntent, mediaIntent} from './media-intent';
import {transfer} from './transfers';
import {buildCinemaPrompt, defaultCinema, isPublicMediaUrl, loadStudioCatalog,
  loadStudioJobs, loadStudioHistory, mergeStudioJobs, refreshStudioJob, studioArtifactId, studioOutputUrl, submitStudioJob, uploadStudioImage, type StudioInput, type StudioJob} from './media-studio';

vi.mock('./api', () => ({request: vi.fn(), json: (method: string, data: unknown) => ({method, body: JSON.stringify(data)})}));
vi.mock('./media-intent', () => ({mediaIntent: vi.fn(), clearMediaIntent: vi.fn()}));
vi.mock('./transfers', () => ({transfer: vi.fn()}));
const input: StudioInput = {provider_id: 'account', model: 'google/image-model', prompt: 'A quiet landscape', acknowledge_billing: true};
beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(mediaIntent).mockResolvedValue({id: 'stable-request', key: 'intent-key'});
});
afterEach(() => vi.restoreAllMocks());

it('scopes catalog and job reads and encodes refresh identities', async () => {
  vi.mocked(request).mockResolvedValue([]);
  await loadStudioCatalog('project with spaces');
  await loadStudioJobs('project with spaces');
  await refreshStudioJob('job/id');
  expect(vi.mocked(request).mock.calls.map(([path]) => path)).toEqual([
    '/api/media/studio/catalog?project_id=project%20with%20spaces',
    '/api/media/studio/jobs?project_id=project%20with%20spaces',
    '/api/media/studio/jobs/job%2Fid/refresh',
  ]);
});
it('uses a persistent billed intent and clears it only after an accepted receipt', async () => {
  const receipt = {id: 'receipt', status: 'submitting'};
  vi.mocked(request).mockResolvedValue(receipt);
  expect(await submitStudioJob('owner', 'project', input)).toEqual(receipt);
  expect(mediaIntent).toHaveBeenCalledWith('owner', 'project', {project_id: 'project', ...input}, 'studio');
  const body = JSON.parse(String(vi.mocked(request).mock.calls[0][1]?.body));
  expect(body).toEqual({request_id: 'stable-request', project_id: 'project', ...input});
  expect(clearMediaIntent).toHaveBeenCalledWith('intent-key', 'stable-request');
});
it('retains the request identity after uncertain transport errors or malformed receipts', async () => {
  vi.mocked(request).mockRejectedValueOnce(Error('Timed out'));
  await expect(submitStudioJob('owner', null, input)).rejects.toThrow('Timed out');
  vi.mocked(request).mockResolvedValueOnce({});
  await expect(submitStudioJob('owner', null, input)).rejects.toThrow('job receipt');
  expect(clearMediaIntent).not.toHaveBeenCalled();
});
it('does not start billing if the workspace locks while obtaining an intent', async () => {
  const controller = new AbortController();
  vi.mocked(mediaIntent).mockImplementation(async () => {
    controller.abort(); return {id: 'stable-request', key: 'intent-key'};
  });
  await expect(submitStudioJob('owner', null, input, controller.signal)).rejects.toThrow('Request cancelled');
  expect(request).not.toHaveBeenCalled();
  expect(clearMediaIntent).not.toHaveBeenCalled();
});
describe('output and reference URL boundaries', () => {
  it('accepts only HTTPS or the exact authenticated artifact export route', () => {
    expect(studioOutputUrl('https://cdn.example.com/image.webp')).toBe('https://cdn.example.com/image.webp');
    expect(studioOutputUrl('/api/media/artifacts/abcd-1234/export?format=original')).toBe('/api/media/artifacts/abcd-1234/export?format=original');
    for (const value of ['javascript:alert(1)', 'data:image/png;base64,a', '//other.example/a', 'https://name:secret@example.com/a', '/api/media/artifacts/id/export?format=original&redirect=evil', '/api/other']) expect(studioOutputUrl(value)).toBeNull();
  });
  it('rejects local or private reference URLs while permitting public HTTPS', () => {
    expect(isPublicMediaUrl('https://media.example.com/audio.mp3')).toBe(true);
    for (const value of ['http://example.com/a', 'https://localhost/a', 'https://host.local/a', 'https://127.0.0.1/a', 'https://10.0.0.1/a', 'https://172.16.0.1/a', 'https://192.168.1.1/a', 'https://[::1]/a', '/api/media/artifacts/a/export?format=original']) expect(isPublicMediaUrl(value)).toBe(false);
  });
});
it('composes camera aesthetics into a still-image prompt without promising an output resolution', () => {
  const prompt = buildCinemaPrompt('A traveler at dawn', {...defaultCinema, focal: '85', aperture: 'f/11'});
  expect(prompt).toContain('A traveler at dawn, cinematic still photograph');
  expect(prompt).toContain('full-frame digital cinema camera');
  expect(prompt).toContain('85mm (classic portrait perspective)');
  expect(prompt).toContain('deep focus clarity');
  expect(prompt).not.toContain('8K resolution');
});

it('sends scope-bound server history queries with cursors and literal search text', async () => {
  vi.mocked(request).mockResolvedValue({items: [], next_cursor: null});
  await loadStudioHistory('project', {cursor: 'opaque+/=', kind: 'image', status: 'attention', q: '  10%_portrait  ', limit: 40});
  const url = new URL(vi.mocked(request).mock.calls[0][0], 'https://termx.example');
  expect(Object.fromEntries(url.searchParams)).toEqual({project_id: 'project', limit: '40', cursor: 'opaque+/=', kind: 'image', status: 'attention', q: '10%_portrait'});
});
it('preserves older loaded jobs while replacing refreshed identities', () => {
  const base: StudioJob = {id: 'older', project_id: null, provider_id: 'a', model: 'm', kind: 'image', prompt: '', status: 'queued', created: 1, updated: 1, outputs: []};
  const rows = mergeStudioJobs([base, {...base, id: 'newer', created: 2}], [{...base, id: 'newer', created: 2, status: 'completed'}]);
  expect(rows.map(row => [row.id, row.status])).toEqual([['newer', 'completed'], ['older', 'queued']]);
});
it('uploads a supported image as raw bytes to the scoped host without billing', async () => {
  const file = new File(['image'], 'portrait.png', {type: 'image/png'});
  vi.mocked(transfer).mockResolvedValue({text: async () => JSON.stringify({id: 'owned-image', project: 'project', mime: 'image/png', kind: 'input'})} as Blob);
  expect(await uploadStudioImage(file, 'project')).toEqual({id: 'owned-image', name: 'portrait.png'});
  expect(transfer).toHaveBeenCalledWith('/api/media/inputs?name=portrait.png&project_id=project', expect.objectContaining({method: 'POST', body: file, headers: {'Content-Type': 'image/png'}}));
  expect(mediaIntent).not.toHaveBeenCalled();
});
it('rejects excessive or unsupported local images before upload and guards returned scope', async () => {
  const file = new File(['x'], 'huge.png', {type: 'image/png'});
  Object.defineProperty(file, 'size', {value: 8 * 1024 * 1024 + 1});
  await expect(uploadStudioImage(file, null)).rejects.toThrow('8 MiB');
  await expect(uploadStudioImage(new File(['x'], 'anim.gif', {type: 'image/gif'}), null)).rejects.toThrow('static PNG');
  expect(transfer).not.toHaveBeenCalled();
  vi.mocked(transfer).mockResolvedValue({text: async () => JSON.stringify({id: 'wrong', project: 'other', mime: 'image/png'})} as Blob);
  await expect(uploadStudioImage(new File(['x'], 'valid.png', {type: 'image/png'}), 'project')).rejects.toThrow('did not match');
});
it('extracts only owned artifact output identities for local handoffs', () => {
  expect(studioArtifactId('/api/media/artifacts/owned-id/export?format=original')).toBe('owned-id');
  expect(studioArtifactId('https://other.example/api/media/artifacts/id/export?format=original')).toBeNull();
  expect(studioArtifactId('/api/media/artifacts/id/export?format=original&extra=1')).toBeNull();
});

it('does not downgrade newer or terminal job receipts when stale snapshots arrive', () => {
  const base: StudioJob = {id: 'receipt', project_id: null, provider_id: 'a', model: 'm', kind: 'image', prompt: '', status: 'completed', created: 1, updated: 3, outputs: ['https://example.com/output.png']};
  expect(mergeStudioJobs([base], [{...base, status: 'queued', updated: 2, outputs: []}])[0]).toEqual(base);
  expect(mergeStudioJobs([base], [{...base, status: 'running', updated: 3, outputs: []}])[0]).toEqual(base);
});

it('ignores incompatible Studio defaults and parameter values', async () => {
  const {studioDefaultSelection} = await import('./media-studio');
  const catalog = {accounts: [{id: 'or', name: 'Studio', configured: true, adapter: 'openrouter' as const, capabilities: ['image']}], models: [{id: 'still', label: 'Still', adapter: 'openrouter' as const, kind: 'image' as const, modes: ['image' as const], description: '', aspect_ratios: ['1:1'], resolutions: ['1K'], durations: []}], settings: {revision: 1, enabled_models: null, defaults: {image: {provider_id: 'missing', model: 'disabled', aspect_ratio: '100:1', resolution: '8K', duration: 99}}, limits: {daily_generations: null, concurrent_generations: null, max_video_seconds: null}}};
  expect(studioDefaultSelection(catalog, 'image')).toEqual({provider_id: 'or', model: 'still', aspect_ratio: '1:1', resolution: '1K'});
  expect(studioDefaultSelection(catalog, 'cinema')).toEqual(studioDefaultSelection(catalog, 'image'));
  expect(studioDefaultSelection(catalog, 'video')).toBeUndefined();
});
