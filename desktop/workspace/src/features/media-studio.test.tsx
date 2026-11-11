import {afterEach, beforeEach, expect, it, vi} from 'vitest';
import {act, cleanup, fireEvent, render, screen, waitFor, within} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {useState} from 'react';
import MediaStudio from './MediaStudio';
import {loadStudioCatalog, loadStudioJobs, loadStudioHistory, refreshStudioJob, submitStudioJob, uploadStudioImage, type StudioCatalog, type StudioJob} from '../lib/media-studio';
import {transfer} from '../lib/transfers';

vi.mock('../lib/media-studio', async importOriginal => ({...await importOriginal<typeof import('../lib/media-studio')>(),
  loadStudioCatalog: vi.fn(), loadStudioJobs: vi.fn(), loadStudioHistory: vi.fn(), uploadStudioImage: vi.fn(), refreshStudioJob: vi.fn(), submitStudioJob: vi.fn(),
  createStudioClient: (owner: string | undefined, project: string | null) => {
    const generate = (input: unknown, signal?: AbortSignal) => vi.mocked(submitStudioJob)(owner, project, input as Parameters<typeof submitStudioJob>[2], signal);
    return {generateImage: generate, generateVideo: generate, processLipSync: generate, remixVideo: generate, pollForResult: refreshStudioJob};
  },
}));
vi.mock('./StudioSubjects', () => ({default: ({onSelect, onSelectMany, visible}: {onSelectMany?: (ids: string[], name: string, description: string) => void; onSelect: (id: string, name: string, description: string) => void; visible: boolean}) => {
  const [draft, setDraft] = useState('');
  return visible ? <><textarea aria-label="Fixture saved asset draft" value={draft} onChange={event => setDraft(event.target.value)}/><button type="button" onClick={() => onSelect('saved-portrait', 'Aster product', 'Matte ceramic finish')}>Choose fixture subject</button>{onSelectMany && <button type="button" onClick={() => onSelectMany(['subject-front', 'subject-side'], 'Aster set', 'Matte ceramic finish')}>Use fixture reference set</button>}</> : null;
}}));
vi.mock('../lib/transfers', () => ({transfer: vi.fn()}));
vi.mock('./StudioPricing', () => ({default: () => <p>Published rates are checked separately.</p>}));
const catalog: StudioCatalog = {
  catalog_source: 'live',
  accounts: [{id: 'or', name: 'OpenRouter studio', configured: true, adapter: 'openrouter', capabilities: ['image', 'video']},
    {id: 'kie', name: 'Kie studio', configured: true, adapter: 'kie', capabilities: ['image', 'video']}],
  models: [
    {id: 'google/still', adapter: 'openrouter', label: 'Google still', kind: 'image', modes: ['image', 'cinema'], description: 'A real image model', aspect_ratios: ['1:1', '16:9'], resolutions: ['1K', '2K'], durations: []},
    {id: 'google/video', adapter: 'openrouter', label: 'Google video', kind: 'video', modes: ['video'], description: 'A real video model', aspect_ratios: ['16:9'], resolutions: ['720p'], durations: [5, 10]},
    {id: 'talking', adapter: 'kie', label: 'Talking portrait', kind: 'video', modes: ['lipsync'], description: 'Portrait lip sync', aspect_ratios: [], resolutions: ['720p'], durations: [], requires_image: true, requires_audio: true},
  ],
};
const job: StudioJob = {id: 'job', project_id: 'project', provider_id: 'or', model: 'google/still', kind: 'image', prompt: 'A quiet landscape', status: 'queued', created: 1, updated: 1, outputs: []};
beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(loadStudioCatalog).mockResolvedValue(structuredClone(catalog));
  vi.mocked(loadStudioJobs).mockResolvedValue([]);
  vi.mocked(loadStudioHistory).mockImplementation(async () => ({items: await loadStudioJobs('project'), next_cursor: null}));
  vi.mocked(submitStudioJob).mockResolvedValue(job);
  vi.mocked(refreshStudioJob).mockResolvedValue(job);
  Object.defineProperty(document, 'visibilityState', {configurable: true, value: 'visible'});
});
afterEach(() => {cleanup(); vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllGlobals();});
function closePanel() {
  const dialog = screen.queryByRole('dialog');
  const close = dialog && within(dialog).queryByRole('button', {name: /^Close /});
  if (close) fireEvent.click(close);
}
function openTool(name: string) {
  closePanel();
  fireEvent.click(screen.getByRole('button', {name: 'Tools'}));
  fireEvent.click(within(screen.getByRole('dialog', {name: 'Studio tools'})).getByRole('button', {name: new RegExp('^' + name)}));
}
function openReferences(source: 'upload' | 'saved' | 'recent' = 'upload') {
  closePanel();
  const trigger = screen.getByRole('button', {name: /^Reference(?:Required|\d|$| )/});
  if (!trigger.hasAttribute('disabled')) {
    fireEvent.click(trigger);
    if (source !== 'upload') fireEvent.click(screen.getByRole('tab', {name: source === 'saved' ? 'Saved sets' : 'Recent images'}));
  }
}
function generateDraft(button: HTMLElement) {
  fireEvent.click(button);
}
async function ready(openSecondary = true) {
  await screen.findByRole('option', {name: 'Google still'});
  if (openSecondary) {
    const trigger = screen.queryByRole('button', {name: /^Reference(?:Required|\d|$| )/});
    if (trigger && !trigger.hasAttribute('disabled')) openReferences();
  }
}

it('submits the supported provider model once from an explicit Generate click without a charge review', async () => {
  render(<MediaStudio projectId="project" ownerId="owner"/>);
  await ready();
  const generate = screen.getByRole('button', {name: 'Generate image'});
  fireEvent.change(screen.getByLabelText('Prompt'), {target: {value: 'A quiet landscape'}});
  expect(generate).toBeEnabled();
  expect(submitStudioJob).not.toHaveBeenCalled();
  closePanel();
  fireEvent.click(generate); fireEvent.click(generate);
  await screen.findByText('Job received. Track its status in your results.');
  expect(submitStudioJob).toHaveBeenCalledTimes(1);
  expect(submitStudioJob).toHaveBeenCalledWith('owner', 'project', expect.objectContaining({provider_id: 'or', model: 'google/still', prompt: 'A quiet landscape', acknowledge_billing: true}), expect.any(AbortSignal));
  expect(screen.getByRole('article', {name: 'Google still · Queued'})).toBeInTheDocument();
  expect(screen.queryByText('Completed')).not.toBeInTheDocument();
  expect(screen.queryByRole('dialog', {name: 'Review generation'})).not.toBeInTheDocument();
  expect(screen.queryByText(/provider charges|approve charges|charge review/i)).not.toBeInTheDocument();
});
it('builds Cinema still prompts from accessible camera controls', async () => {
  render(<MediaStudio projectId="project"/>); await ready();
  openTool('Cinema');
  fireEvent.click(screen.getByText('Camera & lens'));
  fireEvent.change(screen.getByLabelText('Focal length'), {target: {value: '85'}});
  fireEvent.change(screen.getByLabelText('Your scene'), {target: {value: 'A traveler at dawn'}});
  closePanel();
  generateDraft(screen.getByRole('button', {name: 'Generate still image'}));
  await waitFor(() => expect(submitStudioJob).toHaveBeenCalled());
  expect(vi.mocked(submitStudioJob).mock.calls[0][2].prompt).toContain('85mm (classic portrait perspective)');
  expect(vi.mocked(submitStudioJob).mock.calls[0][2].prompt).toContain('cinematic still photograph');
  expect(vi.mocked(submitStudioJob).mock.calls[0][2]).not.toHaveProperty('duration');
});
it('requires public image and audio references for lip sync without inventing an upload flow', async () => {
  render(<MediaStudio projectId="project"/>); await ready();
  openTool('Lip sync');
  expect(screen.getByLabelText('Provider account')).toHaveValue('kie');
  expect(screen.queryByLabelText('Duration')).not.toBeInTheDocument();
  expect(screen.queryByLabelText('Upload image reference')).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText('Image URL'), {target: {value: 'https://localhost/photo.jpg'}});
  fireEvent.change(screen.getByLabelText('Audio URL'), {target: {value: 'https://media.example.com/audio.mp3'}});
  closePanel();
  generateDraft(screen.getByRole('button', {name: 'Generate lip sync'}));
  await screen.findByText('Enter a public HTTPS URL for your image reference.');
  expect(submitStudioJob).not.toHaveBeenCalled();
});
it('preserves the prompt on a submission error and offers recoverable feedback', async () => {
  vi.mocked(submitStudioJob).mockRejectedValue(Error('Provider unavailable'));
  render(<MediaStudio projectId="project"/>); await ready();
  fireEvent.change(screen.getByLabelText('Prompt'), {target: {value: 'Keep my prompt'}});
  closePanel();
  generateDraft(screen.getByRole('button', {name: 'Generate image'}));
  expect(await screen.findByRole('alert')).toHaveTextContent('Provider unavailable');
  expect(screen.getByLabelText('Prompt')).toHaveValue('Keep my prompt');
  expect(screen.queryByRole('article')).not.toBeInTheDocument();
});
it('clears account data immediately on project/owner changes and ignores stale reads', async () => {
  let resolve!: (rows: StudioJob[]) => void;
  vi.mocked(loadStudioJobs).mockReturnValueOnce(new Promise(done => resolve = done));
  const view = render(<MediaStudio projectId="project" ownerId="first"/>);
  view.rerender(<MediaStudio projectId="next" ownerId="second"/>);
  await ready();
  await act(async () => resolve([job]));
  expect(screen.queryByText('A quiet landscape')).not.toBeInTheDocument();
  expect(loadStudioCatalog).toHaveBeenLastCalledWith('next', expect.any(AbortSignal));
});
it('discards an accepted generation response after locking and hides prior output', async () => {
  let resolve!: (value: StudioJob) => void;
  vi.mocked(submitStudioJob).mockReturnValueOnce(new Promise(done => resolve = done));
  render(<MediaStudio projectId="project"/>); await ready();
  fireEvent.change(screen.getByLabelText('Prompt'), {target: {value: 'A quiet landscape'}});
  closePanel();
  generateDraft(screen.getByRole('button', {name: 'Generate image'}));
  fireEvent(window, new Event('termx-locked'));
  await act(async () => resolve(job));
  expect(screen.getByText('Studio locked')).toBeInTheDocument();
  expect(screen.queryByText('A quiet landscape')).not.toBeInTheDocument();
  expect(screen.queryByRole('article')).not.toBeInTheDocument();
});
it('polls only visible active jobs and pauses checks when the studio is hidden', async () => {
  vi.mocked(loadStudioJobs).mockResolvedValue([job, {...job, id: 'unknown', status: 'unknown'}]);
  vi.useFakeTimers();
  const view = render(<MediaStudio projectId="project"/>);
  await act(async () => {await Promise.resolve();});
  await act(async () => {await vi.advanceTimersByTimeAsync(8000);});
  expect(refreshStudioJob).toHaveBeenCalledTimes(1);
  expect(refreshStudioJob).toHaveBeenCalledWith('job', expect.any(AbortSignal));
  view.rerender(<MediaStudio projectId="project" visible={false}/>);
  await act(async () => {await vi.advanceTimersByTimeAsync(16000);});
  expect(refreshStudioJob).toHaveBeenCalledTimes(1);
  expect(screen.queryByRole('region', {name: 'Media studio'})).not.toBeInTheDocument();
});
it('shows unconfigured and saved-catalog states and does not imply entitlement', async () => {
  vi.mocked(loadStudioCatalog).mockResolvedValue({...catalog, catalog_source: 'snapshot', accounts: catalog.accounts.map(item => ({...item, configured: false}))});
  const configure = vi.fn();
  render(<MediaStudio projectId="project" onConfigureProvider={configure}/>); await ready();
  expect(screen.getByText(/Saved model catalog/)).toBeInTheDocument();
  expect(screen.getByRole('button', {name: 'Generate image'})).toBeDisabled();
  fireEvent.click(screen.getByRole('button', {name: /Configure provider/}));
  expect(configure).toHaveBeenCalled();
});
it('loads authenticated output through transfers and revokes its preview on lock', async () => {
  const create = vi.fn(() => 'blob:studio-output');
  const revoke = vi.fn();
  Object.defineProperty(URL, 'createObjectURL', {configurable: true, value: create});
  Object.defineProperty(URL, 'revokeObjectURL', {configurable: true, value: revoke});
  vi.mocked(transfer).mockResolvedValue(new Blob(['image'], {type: 'image/png'}));
  vi.mocked(loadStudioJobs).mockResolvedValue([{...job, status: 'completed', outputs: ['/api/media/artifacts/artifact/export?format=original']}]);
  render(<MediaStudio projectId="project"/>); await ready();
  const image = await screen.findByRole('img', {name: 'Generation job, output 1'});
  await waitFor(() => expect(image).toHaveAttribute('src', 'blob:studio-output'));
  expect(transfer).toHaveBeenCalledWith('/api/media/artifacts/artifact/export?format=original', expect.objectContaining({signal: expect.any(AbortSignal)}));
  expect(screen.getByRole('link', {name: /Download Generation/})).toHaveAttribute('href', 'blob:studio-output');
  fireEvent(window, new Event('termx-session-locked'));
  expect(revoke).toHaveBeenCalledWith('blob:studio-output');
  expect(screen.queryByRole('img')).not.toBeInTheDocument();
});
it('provides manual status checks for failed and unknown history without resubmitting', async () => {
  vi.mocked(loadStudioJobs).mockResolvedValue([{...job, status: 'unknown'}, {...job, id: 'failed', status: 'failed', error: 'Provider rejected the request'}]);
  render(<MediaStudio projectId="project"/>); await ready();
  fireEvent.click(screen.getByRole('button', {name: 'Library'}));
  fireEvent.click(screen.getByRole('button', {name: 'Needs attention'}));
  const unknown = await screen.findByRole('article', {name: /Status unknown/});
  fireEvent.click(within(unknown).getByRole('button', {name: 'Check status'}));
  await waitFor(() => expect(refreshStudioJob).toHaveBeenCalled());
  expect(submitStudioJob).not.toHaveBeenCalled();
  expect(screen.getByText('Provider rejected the request')).toBeInTheDocument();
});

it('stops background polling at its five-minute bound until a manual refresh', async () => {
  vi.mocked(loadStudioJobs).mockResolvedValue([job]);
  vi.useFakeTimers();
  render(<MediaStudio projectId="project"/>);
  await act(async () => {await Promise.resolve();});
  await act(async () => {await vi.advanceTimersByTimeAsync(8000);});
  expect(refreshStudioJob).toHaveBeenCalledTimes(1);
  vi.setSystemTime(Date.now() + 5 * 60 * 1000);
  await act(async () => {await vi.advanceTimersByTimeAsync(8000);});
  expect(refreshStudioJob).toHaveBeenCalledTimes(1);
  expect(screen.getByText('Automatic checks paused. Refresh to check your jobs.')).toBeInTheDocument();
});
it('offers optional image references only on models that support them', async () => {
  vi.mocked(loadStudioCatalog).mockResolvedValue({...catalog, models: catalog.models.map(model => ({...model, supports_image: model.kind === 'image'}))});
  render(<MediaStudio projectId="project"/>); await ready();
  fireEvent.change(screen.getByLabelText(/Image URL/), {target: {value: 'https://media.example.com/photo.jpg'}});
  fireEvent.change(screen.getByLabelText('Prompt'), {target: {value: 'Reframe this portrait'}});
  closePanel();
  generateDraft(screen.getByRole('button', {name: 'Generate image'}));
  await waitFor(() => expect(submitStudioJob).toHaveBeenCalled());
  expect(vi.mocked(submitStudioJob).mock.calls[0][2].image_url).toBe('https://media.example.com/photo.jpg');
});

it('prefers configured OpenRouter while preserving an explicit compatible account across modes', async () => {
  vi.mocked(loadStudioCatalog).mockResolvedValue({...catalog, accounts: [...catalog.accounts].reverse(), models: [...catalog.models, {...catalog.models[0], adapter: 'kie'}]});
  render(<MediaStudio projectId="project"/>); await ready();
  expect(screen.getByLabelText('Provider account')).toHaveValue('or');
  fireEvent.change(screen.getByLabelText('Provider account'), {target: {value: 'kie'}});
  openTool('Cinema');
  expect(screen.getByLabelText('Provider account')).toHaveValue('kie');
});

function advancedCatalog(): StudioCatalog {
  return {...structuredClone(catalog), models: catalog.models.map(model => ({...model,
    ...(model.adapter === 'openrouter' && {supports_image: true, inline_inputs: ['image'] as const as ['image'], supports_negative_prompt: true}),
    ...(model.kind === 'video' && model.adapter === 'openrouter' && {image_roles: ['first_frame', 'reference'] as ['first_frame', 'reference']}),
  }))};
}
it('paginates server history and keeps older pages while a current job is refreshed', async () => {
  const older = {...job, id: 'older', prompt: 'An older saved generation', status: 'completed' as const, created: 0};
  vi.mocked(loadStudioHistory).mockResolvedValueOnce({items: [job], next_cursor: 'next-page'}).mockResolvedValueOnce({items: [older], next_cursor: null});
  vi.mocked(refreshStudioJob).mockResolvedValue({...job, status: 'completed'});
  render(<MediaStudio projectId="project"/>); await ready();
  fireEvent.click(screen.getByRole('button', {name: 'Library'}));
  await screen.findByRole('article', {name: 'Google still · Queued'});
  fireEvent.click(screen.getByRole('button', {name: 'Load more generations'}));
  await screen.findByText('An older saved generation');
  expect(loadStudioHistory).toHaveBeenLastCalledWith('project', expect.objectContaining({cursor: 'next-page', limit: 40}), expect.any(AbortSignal));
  fireEvent.click(screen.getByRole('button', {name: 'Check status'}));
  await waitFor(() => expect(screen.queryByRole('article', {name: 'Google still · Queued'})).not.toBeInTheDocument());
  expect(screen.getByText('An older saved generation')).toBeInTheDocument();
  expect(screen.getAllByRole('article')).toHaveLength(2);
});
it('sends search and status filters to the server and ignores stale search responses', async () => {
  let resolve!: (value: {items: StudioJob[]; next_cursor: null}) => void;
  vi.mocked(loadStudioHistory).mockReturnValueOnce(new Promise(done => resolve = done))
    .mockResolvedValue({items: [{...job, id: 'searched', prompt: 'Matching new query'}], next_cursor: null});
  render(<MediaStudio projectId="project"/>); await ready();
  fireEvent.click(screen.getByRole('button', {name: 'Library'}));
  await waitFor(() => expect(loadStudioHistory).toHaveBeenCalledTimes(1));
  fireEvent.change(screen.getByLabelText('Search generations'), {target: {value: 'new query'}});
  await screen.findByText('Matching new query');
  await act(async () => resolve({items: [{...job, prompt: 'Stale result'}], next_cursor: null}));
  expect(screen.queryByText('Stale result')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', {name: 'Needs attention'}));
  await waitFor(() => expect(loadStudioHistory).toHaveBeenLastCalledWith('project', expect.objectContaining({q: 'new query', status: 'attention'}), expect.any(AbortSignal)));
  expect(vi.mocked(loadStudioHistory).mock.calls.at(-1)?.[1]).not.toHaveProperty('cursor');
});
it('removes refreshed completed jobs from an active-filtered library', async () => {
  vi.mocked(loadStudioHistory).mockResolvedValue({items: [job], next_cursor: 'older'});
  vi.mocked(refreshStudioJob).mockResolvedValue({...job, status: 'completed'});
  render(<MediaStudio projectId="project"/>); await ready();
  fireEvent.click(screen.getByRole('button', {name: 'Library'}));
  fireEvent.click(screen.getByRole('button', {name: 'In progress'}));
  await screen.findByRole('article', {name: 'Google still · Queued'});
  fireEvent.click(screen.getByRole('button', {name: 'Check status'}));
  await waitFor(() => expect(screen.queryByRole('article')).not.toBeInTheDocument());
  expect(screen.getByRole('button', {name: 'Load more generations'})).toBeInTheDocument();
});
it('restores reusable settings as an unapproved draft and asks for omitted temporary references', async () => {
  vi.mocked(loadStudioCatalog).mockResolvedValue(advancedCatalog());
  vi.mocked(loadStudioJobs).mockResolvedValue([{...job, status: 'completed', parameters: {operation: 'image', resolution: '2K', aspect_ratio: '16:9', negative_prompt: 'no logos', has_image_reference: true}}]);
  render(<MediaStudio projectId="project"/>); await ready();
  fireEvent.click(screen.getByRole('button', {name: 'Regenerate'}));
  expect(screen.getByLabelText('Prompt')).toHaveValue(job.prompt);
  expect(screen.getByLabelText('Resolution')).toHaveValue('2K');
  expect(screen.getByLabelText('Negative prompt')).toHaveValue('no logos');
  expect(screen.queryByRole('dialog', {name: 'Review generation'})).not.toBeInTheDocument();
  expect(screen.getByRole('status')).toHaveTextContent('Re-enter the image reference');
  expect(submitStudioJob).not.toHaveBeenCalled();
});
it('hands owned image outputs to edit and animation drafts without submitting or exposing an authenticated URL as public', async () => {
  vi.mocked(loadStudioCatalog).mockResolvedValue(advancedCatalog());
  vi.mocked(transfer).mockResolvedValue(new Blob(['image'], {type: 'image/png'}));
  vi.mocked(loadStudioJobs).mockResolvedValue([{...job, status: 'completed', outputs: ['/api/media/artifacts/owned-output/export?format=original']}]);
  render(<MediaStudio projectId="project"/>); await ready();
  fireEvent.click(screen.getByRole('button', {name: 'Edit image'}));
  expect(screen.getByLabelText('Image URL')).toHaveValue('');
  expect(screen.getByLabelText('Image URL')).toBeDisabled();
  expect(screen.getByLabelText('Remove image reference')).toBeInTheDocument();
  expect(screen.queryByRole('dialog', {name: 'Review generation'})).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', {name: 'Animate'}));
  expect(screen.getByRole('tab', {name: 'Video'})).toHaveAttribute('aria-selected', 'true');
  expect(screen.getByLabelText('Image reference role')).toHaveValue('first_frame');
  expect(submitStudioJob).not.toHaveBeenCalled();
});
it('uploads an image reference only on declared inline models and submits its owned ID', async () => {
  vi.mocked(loadStudioCatalog).mockResolvedValue(advancedCatalog());
  vi.mocked(uploadStudioImage).mockResolvedValue({id: 'owned-upload', name: 'portrait.png'});
  render(<MediaStudio projectId="project"/>); await ready();
  fireEvent.change(screen.getByLabelText('Upload image reference'), {target: {files: [new File(['image'], 'portrait.png', {type: 'image/png'})]}});
  await screen.findByText('Ready in this workspace');
  fireEvent.change(screen.getByLabelText('Prompt'), {target: {value: 'Reframe this image'}});
  closePanel();
  generateDraft(screen.getByRole('button', {name: 'Generate image'}));
  await waitFor(() => expect(submitStudioJob).toHaveBeenCalled());
  expect(vi.mocked(submitStudioJob).mock.calls[0][2]).toMatchObject({image_id: 'owned-upload', operation: 'image'});
  expect(vi.mocked(submitStudioJob).mock.calls[0][2]).not.toHaveProperty('image_url');
});
it('aborts a hidden upload and discards its late reference receipt', async () => {
  let resolve!: (value: {id: string; name: string}) => void;
  vi.mocked(loadStudioCatalog).mockResolvedValue(advancedCatalog());
  vi.mocked(uploadStudioImage).mockReturnValue(new Promise(done => resolve = done));
  const view = render(<MediaStudio projectId="project"/>); await ready();
  fireEvent.change(screen.getByLabelText('Upload image reference'), {target: {files: [new File(['image'], 'portrait.png', {type: 'image/png'})]}});
  const signal = vi.mocked(uploadStudioImage).mock.calls[0][2];
  view.rerender(<MediaStudio projectId="project" visible={false}/>);
  expect(signal?.aborted).toBe(true);
  await act(async () => resolve({id: 'late-upload', name: 'portrait.png'}));
  view.rerender(<MediaStudio projectId="project"/>); await ready();
  expect(screen.queryByLabelText('Remove image reference')).not.toBeInTheDocument();
  expect(screen.queryByText('portrait.png')).not.toBeInTheDocument();
});
it('exposes only verified Remix models and sends source-video settings after consent', async () => {
  const advanced = advancedCatalog();
  advanced.models.push({id: 'verified/editor', adapter: 'openrouter', label: 'Verified video editor', kind: 'video', modes: ['remix'], description: 'A verified source-video editor', aspect_ratios: [], resolutions: ['720p'], durations: [], requires_video: true, supports_negative_prompt: true});
  vi.mocked(loadStudioCatalog).mockResolvedValue(advanced);
  render(<MediaStudio projectId="project"/>); await ready();
  fireEvent.click(screen.getByRole('tab', {name: 'Video'}));
  openTool('Remix video');
  expect(screen.getByLabelText('Model')).toHaveValue('verified/editor');
  expect(screen.queryByLabelText('Duration')).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText('Video URL'), {target: {value: 'https://media.example.com/source.mp4'}});
  fireEvent.change(screen.getByLabelText('Prompt'), {target: {value: 'Change the light to golden hour'}});
  fireEvent.change(screen.getByLabelText('Negative prompt'), {target: {value: 'no added objects'}});
  closePanel();
  generateDraft(screen.getByRole('button', {name: 'Generate remix'}));
  await waitFor(() => expect(submitStudioJob).toHaveBeenCalled());
  expect(vi.mocked(submitStudioJob).mock.calls[0][2]).toMatchObject({operation: 'remix', video_url: 'https://media.example.com/source.mp4', negative_prompt: 'no added objects'});
});
it('opens Content factory as a Video tool without submitting a generation', async () => {
  render(<MediaStudio projectId="project"/>); await ready();
  fireEvent.click(screen.getByRole('tab', {name: 'Video'}));
  openTool('Content factory');
  expect(screen.getByRole('region', {name: 'Content factory'})).toBeInTheDocument();
  expect(screen.queryByRole('form', {name: 'Generation composer'})).not.toBeInTheDocument();
  expect(submitStudioJob).not.toHaveBeenCalled();
});

it('keeps history visible when the already selected filter is clicked', async () => {
  vi.mocked(loadStudioHistory).mockResolvedValue({items: [job], next_cursor: null});
  render(<MediaStudio projectId="project"/>); await ready();
  fireEvent.click(screen.getByRole('button', {name: 'Library'}));
  await screen.findByRole('article', {name: 'Google still · Queued'});
  const calls = vi.mocked(loadStudioHistory).mock.calls.length;
  fireEvent.click(screen.getByRole('button', {name: 'All'}));
  expect(screen.getByRole('article', {name: 'Google still · Queued'})).toBeInTheDocument();
  expect(screen.queryByText('Loading history…')).not.toBeInTheDocument();
  expect(loadStudioHistory).toHaveBeenCalledTimes(calls);
});

it('aborts reference uploads when the document becomes hidden', async () => {
  let resolve!: (value: {id: string; name: string}) => void;
  vi.mocked(loadStudioCatalog).mockResolvedValue(advancedCatalog());
  vi.mocked(uploadStudioImage).mockReturnValue(new Promise(done => resolve = done));
  render(<MediaStudio projectId="project"/>); await ready();
  fireEvent.change(screen.getByLabelText('Upload image reference'), {target: {files: [new File(['image'], 'portrait.png', {type: 'image/png'})]}});
  const signal = vi.mocked(uploadStudioImage).mock.calls[0][2];
  Object.defineProperty(document, 'visibilityState', {configurable: true, value: 'hidden'});
  fireEvent(document, new Event('visibilitychange'));
  expect(signal?.aborted).toBe(true);
  await act(async () => resolve({id: 'late-hidden-upload', name: 'portrait.png'}));
  expect(screen.queryByText('Ready in this workspace')).not.toBeInTheDocument();
});

it('does not block a text-only video model on an image reference from another mode', async () => {
  const advanced = advancedCatalog();
  advanced.models = advanced.models.map(model => model.id === 'google/video' ? catalog.models[1] : model);
  vi.mocked(loadStudioCatalog).mockResolvedValue(advanced);
  vi.mocked(uploadStudioImage).mockResolvedValue({id: 'owned-upload', name: 'portrait.png'});
  render(<MediaStudio projectId="project"/>); await ready();
  fireEvent.change(screen.getByLabelText('Upload image reference'), {target: {files: [new File(['image'], 'portrait.png', {type: 'image/png'})]}});
  await screen.findByText('Ready in this workspace');
  fireEvent.click(screen.getByRole('tab', {name: 'Video'}));
  expect(screen.queryByRole('group', {name: 'Selected reference media'})).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText('Prompt'), {target: {value: 'A landscape in motion'}});
  closePanel();
  generateDraft(screen.getAllByRole('button', {name: 'Generate video'}).find(button => button.getAttribute('type') === 'submit')!);
  await waitFor(() => expect(submitStudioJob).toHaveBeenCalled());
  expect(vi.mocked(submitStudioJob).mock.calls[0][2]).toMatchObject({model: 'google/video', operation: 'video'});
  expect(vi.mocked(submitStudioJob).mock.calls[0][2]).not.toHaveProperty('image_id');
});

it('uses a saved subject as one owned reference without changing the prompt or approving charges', async () => {
  vi.mocked(loadStudioCatalog).mockResolvedValue(advancedCatalog());
  render(<MediaStudio projectId="project"/>); await ready();
  fireEvent.change(screen.getByLabelText('Prompt'), {target: {value: 'Keep this exact direction'}});
  closePanel();
  openReferences('saved');
  fireEvent.click(await screen.findByRole('button', {name: 'Choose fixture subject'}));
  expect(screen.getByLabelText('Prompt')).toHaveValue('Keep this exact direction');
  expect(screen.getByLabelText('Image URL')).toBeDisabled();
  expect(screen.queryByRole('dialog', {name: 'Review generation'})).not.toBeInTheDocument();
  expect(within(screen.getByRole('group', {name: 'Selected reference media'})).getByText('Aster product')).toBeVisible();
  expect(screen.getByRole('status')).toHaveTextContent('Subject notes: Matte ceramic finish');
  expect(submitStudioJob).not.toHaveBeenCalled();
});

it('does not let an older history response downgrade an already completed current job', async () => {
  vi.mocked(loadStudioJobs).mockResolvedValue([{...job, status: 'completed', updated: 5}]);
  vi.mocked(loadStudioHistory).mockResolvedValue({items: [{...job, status: 'queued', updated: 1}], next_cursor: null});
  render(<MediaStudio projectId="project"/>); await ready();
  fireEvent.click(screen.getByRole('button', {name: 'Library'}));
  await screen.findByRole('article', {name: 'Google still · Completed'});
  expect(screen.queryByRole('article', {name: 'Google still · Queued'})).not.toBeInTheDocument();
});

function previewObserver() {
  const watched = new Map<Element, IntersectionObserverCallback>();
  vi.stubGlobal('IntersectionObserver', class {
    callback: IntersectionObserverCallback;
    constructor(callback: IntersectionObserverCallback) {this.callback = callback;}
    observe(element: Element) {watched.set(element, this.callback);}
    unobserve(element: Element) {watched.delete(element);}
    disconnect() {for (const [element, callback] of watched) if (callback === this.callback) watched.delete(element);}
  });
  return {
    watched,
    visible(element: Element, visible: boolean) {
      watched.get(element)?.([{target: element, isIntersecting: visible, intersectionRatio: visible ? 1 : 0} as IntersectionObserverEntry], {} as IntersectionObserver);
    },
  };
}
it('defers authenticated gallery previews until their output enters the viewport', async () => {
  const observer = previewObserver();
  const create = vi.fn(() => 'blob:lazy-preview');
  Object.defineProperty(URL, 'createObjectURL', {configurable: true, value: create});
  Object.defineProperty(URL, 'revokeObjectURL', {configurable: true, value: vi.fn()});
  vi.mocked(transfer).mockResolvedValue(new Blob(['image'], {type: 'image/png'}));
  vi.mocked(loadStudioJobs).mockResolvedValue(Array.from({length: 40}, (_, index) => ({...job, id: 'preview-' + index, status: 'completed', outputs: ['/api/media/artifacts/preview-' + index + '/export?format=original']})));
  render(<MediaStudio projectId="project"/>); await ready(false);
  fireEvent.click(screen.getByRole('button', {name: 'Library'}));
  await waitFor(() => expect(screen.getAllByRole('article')).toHaveLength(40));
  expect(screen.getAllByRole('article')).toHaveLength(40);
  expect(transfer).not.toHaveBeenCalled();
  expect(create).not.toHaveBeenCalled();
  expect(observer.watched.size).toBe(40);
});
it('loads an entering preview once and releases its blob when it leaves or locks', async () => {
  const observer = previewObserver(), revoke = vi.fn();
  const create = vi.fn().mockReturnValueOnce('blob:visible-one').mockReturnValueOnce('blob:visible-two');
  Object.defineProperty(URL, 'createObjectURL', {configurable: true, value: create});
  Object.defineProperty(URL, 'revokeObjectURL', {configurable: true, value: revoke});
  vi.mocked(transfer).mockResolvedValue(new Blob(['image'], {type: 'image/png'}));
  vi.mocked(loadStudioJobs).mockResolvedValue([{...job, status: 'completed', outputs: ['/api/media/artifacts/owned/export?format=original']}]);
  render(<MediaStudio projectId="project"/>); await ready();
  expect(screen.getByRole('link', {name: 'Download Generation job, output 1'})).toHaveAttribute('href', '/api/media/artifacts/owned/export?format=original');
  const element = [...observer.watched.keys()][0];
  act(() => {observer.visible(element, true); observer.visible(element, true);});
  await screen.findByRole('img', {name: 'Generation job, output 1'});
  expect(transfer).toHaveBeenCalledTimes(1);
  act(() => observer.visible(element, false));
  expect(revoke).toHaveBeenCalledWith('blob:visible-one');
  expect(screen.queryByRole('img', {name: 'Generation job, output 1'})).not.toBeInTheDocument();
  act(() => observer.visible(element, true));
  await waitFor(() => expect(create).toHaveBeenCalledTimes(2));
  fireEvent(window, new Event('termx-locked'));
  expect(revoke).toHaveBeenCalledWith('blob:visible-two');
  expect(observer.watched.size).toBe(0);
});
it('bounds concurrent preview transfers and drops aborted queued or native-late receipts', async () => {
  const observer = previewObserver();
  const pending: Array<{resolve: (blob: Blob) => void; signal: AbortSignal}> = [];
  const create = vi.fn(() => 'blob:unexpected');
  Object.defineProperty(URL, 'createObjectURL', {configurable: true, value: create});
  vi.mocked(transfer).mockImplementation((_path, init) => new Promise(resolve => pending.push({resolve, signal: init?.signal as AbortSignal})));
  vi.mocked(loadStudioJobs).mockResolvedValue(Array.from({length: 6}, (_, index) => ({...job, id: 'bounded-' + index, status: 'completed', outputs: ['/api/media/artifacts/bounded-' + index + '/export?format=original']})));
  const view = render(<MediaStudio projectId="project"/>); await ready(false);
  fireEvent.click(screen.getByRole('button', {name: 'Library'}));
  await waitFor(() => expect(screen.getAllByRole('article')).toHaveLength(6));
  const elements = [...observer.watched.keys()];
  act(() => elements.forEach(element => observer.visible(element, true)));
  expect(transfer).toHaveBeenCalledTimes(3);
  act(() => observer.visible(elements[0], false));
  expect(pending[0].signal.aborted).toBe(true);
  await act(async () => pending[0].resolve(new Blob(['late'])));
  expect(create).not.toHaveBeenCalled();
  expect(transfer).toHaveBeenCalledTimes(4);
  view.unmount();
  await act(async () => pending.slice(1).forEach(item => item.resolve(new Blob(['late']))));
  expect(create).not.toHaveBeenCalled();
  expect(transfer).toHaveBeenCalledTimes(4);
});
it('does not assign offscreen video metadata sources and removes them on exit', async () => {
  const observer = previewObserver();
  vi.mocked(loadStudioJobs).mockResolvedValue([{...job, kind: 'video', status: 'completed', outputs: ['https://media.example.com/output.mp4']}]);
  render(<MediaStudio projectId="project"/>); await ready();
  fireEvent.click(screen.getByRole('tab', {name: 'Video'}));
  const element = [...observer.watched.keys()][0];
  expect(document.querySelector('video')).toBeNull();
  act(() => observer.visible(element, true));
  expect(screen.getByLabelText('Generation job, output 1')).toHaveAttribute('src', 'https://media.example.com/output.mp4');
  act(() => observer.visible(element, false));
  expect(document.querySelector('video')).toBeNull();
  expect(transfer).not.toHaveBeenCalled();
});
it('keeps the no-IntersectionObserver fallback to three initial outputs', async () => {
  vi.stubGlobal('IntersectionObserver', undefined);
  vi.mocked(transfer).mockResolvedValue(new Blob(['image'], {type: 'image/png'}));
  vi.mocked(loadStudioJobs).mockResolvedValue(Array.from({length: 20}, (_, index) => ({...job, id: 'fallback-' + index, status: 'completed', outputs: ['/api/media/artifacts/fallback-' + index + '/export?format=original']})));
  render(<MediaStudio projectId="project" section="library"/>);
  await waitFor(() => expect(screen.getAllByRole('article')).toHaveLength(20));
  await waitFor(() => expect(transfer).toHaveBeenCalledTimes(3));
  expect(screen.getAllByRole('link', {name: /Download Generation/})).toHaveLength(20);
});
it('downloads an offscreen authenticated output only on a direct user click', async () => {
  previewObserver();
  const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
  const create = vi.fn(() => 'blob:manual-download'), revoke = vi.fn();
  Object.defineProperty(URL, 'createObjectURL', {configurable: true, value: create});
  Object.defineProperty(URL, 'revokeObjectURL', {configurable: true, value: revoke});
  vi.mocked(transfer).mockResolvedValue(new Blob(['image'], {type: 'image/png'}));
  vi.mocked(loadStudioJobs).mockResolvedValue([{...job, status: 'completed', outputs: ['/api/media/artifacts/download/export?format=original']}]);
  render(<MediaStudio projectId="project"/>); await ready();
  expect(transfer).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('link', {name: 'Download Generation job, output 1'}));
  await waitFor(() => expect(click).toHaveBeenCalledTimes(1));
  expect(transfer).toHaveBeenCalledWith('/api/media/artifacts/download/export?format=original', expect.objectContaining({signal: expect.any(AbortSignal)}));
  expect(screen.queryByRole('img', {name: 'Generation job, output 1'})).not.toBeInTheDocument();
  fireEvent(window, new Event('termx-session-locked'));
  expect(revoke).toHaveBeenCalledWith('blob:manual-download');
});

function compositionCatalog(max = 3): StudioCatalog {
  const result = advancedCatalog();
  result.models = result.models.map(model => model.kind === 'image' ? {...model, image_reference_limits: {min: 0, max, app_max: max}} : model);
  return result;
}
it('submits two ordered image references without legacy scalar fields and supports reorder/remove', async () => {
  const reactError = vi.spyOn(console, 'error');
  vi.mocked(loadStudioCatalog).mockResolvedValue(compositionCatalog());
  render(<MediaStudio projectId="project"/>); await ready();
  fireEvent.change(screen.getByLabelText('Image URL'), {target: {value: 'https://media.example.com/front.png'}});
  fireEvent.change(screen.getByLabelText('Additional image URL'), {target: {value: 'https://media.example.com/side.png'}});
  fireEvent.click(screen.getByRole('button', {name: 'Add image URL'}));
  fireEvent.click(screen.getByRole('button', {name: 'Move image 2 up'}));
  fireEvent.change(screen.getByLabelText('Prompt'), {target: {value: 'Compose both views of this product'}});
  closePanel();
  generateDraft(screen.getByRole('button', {name: 'Generate image'}));
  await waitFor(() => expect(submitStudioJob).toHaveBeenCalled());
  const input = vi.mocked(submitStudioJob).mock.calls[0][2];
  expect(input.image_references).toEqual([{image_url: 'https://media.example.com/side.png'}, {image_url: 'https://media.example.com/front.png'}]);
  expect(input).not.toHaveProperty('image_url'); expect(input).not.toHaveProperty('image_id');
  openReferences();
  fireEvent.click(screen.getByRole('button', {name: 'Remove image 2'}));
  expect(screen.queryByRole('button', {name: 'Remove image 2'})).not.toBeInTheDocument();
  expect(screen.getByLabelText('Image URL')).toHaveValue('https://media.example.com/side.png');
  expect(reactError).not.toHaveBeenCalled();
});
it('limits composition additions without uploading or generating beyond model capacity', async () => {
  vi.mocked(loadStudioCatalog).mockResolvedValue(compositionCatalog(2));
  render(<MediaStudio projectId="project"/>); await ready();
  fireEvent.change(screen.getByLabelText('Image URL'), {target: {value: 'https://media.example.com/one.png'}});
  fireEvent.change(screen.getByLabelText('Additional image URL'), {target: {value: 'https://media.example.com/two.png'}});
  fireEvent.click(screen.getByRole('button', {name: 'Add image URL'}));
  expect(screen.getByRole('button', {name: 'Add image URL'})).toBeDisabled();
  expect(screen.getByLabelText('Upload additional image reference')).toBeDisabled();
  expect(uploadStudioImage).not.toHaveBeenCalled(); expect(submitStudioJob).not.toHaveBeenCalled();
});
it('rejects a known over-budget local composition before uploading another image', async () => {
  vi.mocked(loadStudioCatalog).mockResolvedValue(compositionCatalog());
  vi.mocked(uploadStudioImage).mockResolvedValue({id: 'large-first', name: 'large.png'});
  const large = new File(['image'], 'large.png', {type: 'image/png'});
  Object.defineProperty(large, 'size', {value: 8 * 1024 * 1024});
  render(<MediaStudio projectId="project"/>); await ready();
  fireEvent.change(screen.getByLabelText('Upload image reference'), {target: {files: [large]}});
  await screen.findByText('Ready in this workspace');
  fireEvent.change(screen.getByLabelText('Upload additional image reference'), {target: {files: [large]}});
  expect(await screen.findByRole('alert')).toHaveTextContent('20 MiB combined');
  expect(uploadStudioImage).toHaveBeenCalledTimes(1); expect(submitStudioJob).not.toHaveBeenCalled();
});
it('selects a saved reference set in order without approving or starting a generation', async () => {
  vi.mocked(loadStudioCatalog).mockResolvedValue(compositionCatalog());
  render(<MediaStudio projectId="project"/>); await ready();
  fireEvent.change(screen.getByLabelText('Prompt'), {target: {value: 'Preserve this direction'}});
  closePanel();
  openReferences('saved');
  fireEvent.click(await screen.findByRole('button', {name: 'Use fixture reference set'}));
  expect(screen.getByLabelText('Prompt')).toHaveValue('Preserve this direction');
  expect(screen.queryByRole('dialog', {name: 'Review generation'})).not.toBeInTheDocument(); expect(submitStudioJob).not.toHaveBeenCalled();
  closePanel(); generateDraft(screen.getByRole('button', {name: 'Generate image'}));
  await waitFor(() => expect(submitStudioJob).toHaveBeenCalled());
  expect(vi.mocked(submitStudioJob).mock.calls[0][2].image_references).toEqual([{image_id: 'subject-front'}, {image_id: 'subject-side'}]);
});
it('preserves a missing temporary reference slot on reuse and requires re-entry', async () => {
  vi.mocked(loadStudioCatalog).mockResolvedValue(compositionCatalog());
  vi.mocked(loadStudioJobs).mockResolvedValue([{...job, status: 'completed', parameters: {operation: 'image', has_image_reference: true, image_references: [{image_id: 'saved-first'}, {has_image_reference: true}, {image_url: 'https://media.example.com/third.png'}]}}]);
  render(<MediaStudio projectId="project"/>); await ready();
  fireEvent.click(screen.getByRole('button', {name: 'Regenerate'}));
  closePanel(); generateDraft(screen.getByRole('button', {name: 'Generate image'}));
  expect(await screen.findByRole('alert')).toHaveTextContent('Re-enter every missing temporary image reference');
  expect(submitStudioJob).not.toHaveBeenCalled();
  fireEvent.change(screen.getByLabelText('Missing image reference 2'), {target: {value: 'https://media.example.com/re-entered.png'}});
  closePanel(); generateDraft(screen.getByRole('button', {name: 'Generate image'}));
  await waitFor(() => expect(submitStudioJob).toHaveBeenCalled());
  expect(vi.mocked(submitStudioJob).mock.calls[0][2].image_references).toEqual([{image_id: 'saved-first'}, {image_url: 'https://media.example.com/re-entered.png'}, {image_url: 'https://media.example.com/third.png'}]);
});
it('scrubs all composition references on an owner change', async () => {
  vi.mocked(loadStudioCatalog).mockResolvedValue(compositionCatalog());
  const view = render(<MediaStudio projectId="project" ownerId="first"/>); await ready();
  openReferences('saved');
  fireEvent.click(await screen.findByRole('button', {name: 'Use fixture reference set'}));
  expect(screen.getByText('Aster set · photo 2')).toBeInTheDocument();
  view.rerender(<MediaStudio projectId="project" ownerId="second"/>); await ready();
  expect(screen.queryByText('Aster set · photo 2')).not.toBeInTheDocument();
  expect(screen.getByLabelText('Image URL')).toHaveValue('');
  expect(screen.queryByRole('dialog', {name: 'Review generation'})).not.toBeInTheDocument();
});


it('keeps the compact compose flow visible and opens focused settings and reference panels without generating', async () => {
  vi.mocked(loadStudioCatalog).mockResolvedValue(advancedCatalog());
  const user = userEvent.setup();
  render(<MediaStudio projectId="project"/>); await ready(false);
  expect(screen.getByRole('form', {name: 'Generation composer'})).toBeVisible();
  expect(screen.getByLabelText('Prompt')).toBeVisible();
  expect(screen.getByLabelText('Model')).toBeVisible();
  expect(screen.queryByRole('checkbox', {name: 'I approve provider charges for this generation.'})).not.toBeInTheDocument();
  expect(screen.getByLabelText('Provider account')).not.toBeVisible();
  expect(screen.getByLabelText('Image URL')).not.toBeVisible();
  await user.click(screen.getByRole('button', {name: 'Generation settings'}));
  expect(screen.getByRole('dialog', {name: 'Generation settings'})).toBeVisible();
  expect(screen.getByLabelText('Provider account')).toBeVisible();
  expect(screen.getByLabelText('Negative prompt')).toBeVisible();
  await user.click(screen.getByRole('button', {name: 'Close Generation settings'}));
  await waitFor(() => expect(screen.getByRole('button', {name: 'Generation settings'})).toHaveFocus());
  openReferences();
  expect(screen.getByLabelText('Image URL')).toBeVisible();
  const upload = screen.getByRole('tab', {name: 'Upload or URL'});
  upload.focus(); fireEvent.keyDown(upload, {key: 'End'});
  expect(screen.getByRole('tab', {name: 'Recent images'})).toHaveFocus();
  expect(screen.getByRole('tab', {name: 'Recent images'})).toHaveAttribute('aria-selected', 'true');
  closePanel();
  expect(screen.queryByRole('dialog', {name: 'Review generation'})).not.toBeInTheDocument();
  expect(submitStudioJob).not.toHaveBeenCalled();
});

it('keeps the reference composer hierarchy and original mode-specific tiles compact', async () => {
  render(<MediaStudio projectId="project"/>); await ready(false);
  const composer = screen.getByRole('form', {name: 'Generation composer'});
  const main = composer.querySelector('.studio-dock-main')!;
  expect(composer.firstElementChild).toBe(screen.getByRole('tablist', {name: 'Studio modes'}));
  expect(main.firstElementChild).toHaveClass('studio-dock-tools');
  expect(main.querySelectorAll('.studio-tool-tile')).toHaveLength(3);
  expect(main.querySelectorAll('.studio-dock-bottom')).toHaveLength(1);
  expect(main.querySelector('.studio-dock-bottom')).toContainElement(screen.getByLabelText('Model'));
  expect(main.querySelector('.studio-dock-bottom')).toContainElement(screen.getByLabelText('Resolution'));
  expect(screen.queryByRole('button', {name: 'Rates'})).not.toBeInTheDocument();
  expect(screen.queryByText(/review.*charges|approve.*charges/i)).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('tab', {name: 'Video'}));
  expect(main.querySelectorAll('.studio-tool-tile')).toHaveLength(5);
  expect(main.querySelector('.studio-dock-bottom')).toContainElement(screen.getByLabelText('Duration'));
  expect(submitStudioJob).not.toHaveBeenCalled();
});

it('reveals the full tool tile on keyboard focus without starting a generation', async () => {
  const user = userEvent.setup();
  render(<MediaStudio projectId="project"/>); await ready(false);
  const style = screen.getByRole('button', {name: 'Style'});
  const scrollIntoView = vi.fn();
  Object.defineProperty(style, 'scrollIntoView', {configurable: true, value: scrollIntoView});
  screen.getByRole('button', {name: 'Camera'}).focus();
  await user.tab();
  expect(style).toHaveFocus();
  expect(scrollIntoView).toHaveBeenCalledOnce();
  expect(scrollIntoView).toHaveBeenCalledWith({block: 'nearest', inline: 'nearest', behavior: 'auto'});
  expect(scrollIntoView.mock.contexts[0]).toBe(style);
  screen.getByLabelText('Prompt').focus();
  expect(scrollIntoView).toHaveBeenCalledOnce();
  expect(submitStudioJob).not.toHaveBeenCalled();
});

it('reveals and focuses required reference controls when generation needs missing media', async () => {
  render(<MediaStudio projectId="project"/>); await ready(false);
  openTool('Lip sync');
  expect(screen.getByLabelText('Image URL')).toBeVisible();
  expect(screen.getByLabelText('Audio URL')).toBeVisible();
  closePanel();
  closePanel();
  generateDraft(screen.getByRole('button', {name: 'Generate lip sync'}));
  expect(screen.getByRole('dialog', {name: 'Reference media'})).toBeVisible();
  expect(screen.getByLabelText('Image URL')).toBeVisible();
  expect(screen.getByLabelText('Image URL')).toHaveFocus();
  expect(screen.getByRole('alert')).toHaveTextContent('Enter a public HTTPS URL for your image reference.');
  expect(submitStudioJob).not.toHaveBeenCalled();
});

it('keeps selected references and model-capability warnings visible when their picker is closed', async () => {
  const models = compositionCatalog(3);
  models.models.push({...models.models[0], id: 'single-image', label: 'Single image', image_reference_limits: {min: 0, max: 1, app_max: 1}});
  vi.mocked(loadStudioCatalog).mockResolvedValue(models);
  render(<MediaStudio projectId="project"/>); await ready(false);
  openReferences('saved');
  fireEvent.click(await screen.findByRole('button', {name: 'Use fixture reference set'}));
  closePanel();
  const selected = screen.getByRole('group', {name: 'Selected reference media'});
  expect(within(selected).getByText('1. Aster set · photo 1')).toBeVisible();
  expect(within(selected).getByText('2. Aster set · photo 2')).toBeVisible();
  fireEvent.change(screen.getByLabelText('Model'), {target: {value: 'single-image'}});
  expect(screen.getByText('This model accepts 1 images. Remove extra references or choose a different model.')).toBeVisible();
  expect(screen.queryByRole('dialog', {name: 'Review generation'})).not.toBeInTheDocument();
  expect(submitStudioJob).not.toHaveBeenCalled();
});

it('selects one large take on Create and leaves the complete gallery to Library', async () => {
  vi.mocked(loadStudioJobs).mockResolvedValue([{...job, id: 'older', created: 1, status: 'unknown'}, {...job, id: 'newer', created: 2, status: 'failed'}]);
  render(<MediaStudio projectId="project"/>); await ready(false);
  expect(screen.getAllByRole('article')).toHaveLength(1);
  expect(screen.getByRole('article', {name: 'Google still · Failed'})).toBeVisible();
  fireEvent.click(within(screen.getByRole('navigation', {name: 'Recent takes'})).getByRole('button', {name: /Status unknown/}));
  expect(screen.getByRole('article', {name: 'Google still · Status unknown'})).toBeVisible();
  fireEvent.click(screen.getByRole('button', {name: 'Library'}));
  await waitFor(() => expect(screen.getAllByRole('article')).toHaveLength(2));
});

it('closes contextual panels on controlled sidebar section changes while retaining the prompt', async () => {
  const view = render(<MediaStudio projectId="project" section="create" onSectionChange={vi.fn()}/>); await ready(false);
  fireEvent.change(screen.getByLabelText('Prompt'), {target: {value: 'Retain the draft'}});
  closePanel();
  fireEvent.click(screen.getByRole('button', {name: 'Generation settings'}));
  view.rerender(<MediaStudio projectId="project" section="library" onSectionChange={vi.fn()}/>);
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  view.rerender(<MediaStudio projectId="project" section="create" onSectionChange={vi.fn()}/>);
  expect(screen.getByLabelText('Prompt')).toHaveValue('Retain the draft');
  expect(screen.queryByRole('dialog', {name: 'Review generation'})).not.toBeInTheDocument();
  expect(submitStudioJob).not.toHaveBeenCalled();
});

it('preserves completed ordered image references across Studio and document visibility while revoking local previews', async () => {
  vi.mocked(loadStudioCatalog).mockResolvedValue(compositionCatalog());
  vi.mocked(uploadStudioImage).mockResolvedValue({id: 'owned-primary', name: 'primary.png'});
  const revoke = vi.spyOn(URL, 'revokeObjectURL');
  const view = render(<MediaStudio projectId="project" ownerId="owner"/>); await ready();
  fireEvent.change(screen.getByLabelText('Upload image reference'), {target: {files: [new File(['image'], 'primary.png', {type: 'image/png'})]}});
  await screen.findByText('Ready in this workspace');
  fireEvent.change(screen.getByLabelText('Additional image URL'), {target: {value: 'https://media.example.com/secondary.png'}});
  fireEvent.click(screen.getByRole('button', {name: 'Add image URL'}));
  fireEvent.change(screen.getByLabelText('Prompt'), {target: {value: 'Keep these two views'}});
  closePanel();
  view.rerender(<MediaStudio projectId="project" ownerId="owner" visible={false}/>);
  expect(screen.getByLabelText('Prompt')).not.toBeVisible();
  expect(revoke).toHaveBeenCalled();
  view.rerender(<MediaStudio projectId="project" ownerId="owner"/>); await ready();
  expect(screen.getByLabelText('Prompt')).toHaveValue('Keep these two views');
  expect(screen.queryByRole('dialog', {name: 'Review generation'})).not.toBeInTheDocument();
  expect(within(screen.getByRole('group', {name: 'Selected reference media'})).getByText('1. primary.png')).toBeVisible();
  Object.defineProperty(document, 'visibilityState', {configurable: true, value: 'hidden'});
  fireEvent(document, new Event('visibilitychange'));
  Object.defineProperty(document, 'visibilityState', {configurable: true, value: 'visible'});
  fireEvent(document, new Event('visibilitychange'));
  closePanel();
  generateDraft(screen.getByRole('button', {name: 'Generate image'}));
  await waitFor(() => expect(submitStudioJob).toHaveBeenCalledOnce());
  expect(vi.mocked(submitStudioJob).mock.calls[0][2].image_references).toEqual([{image_id: 'owned-primary'}, {image_url: 'https://media.example.com/secondary.png'}]);
  fireEvent(window, new Event('termx-session-locked'));
  fireEvent(window, new Event('termx-session-unlocked')); await ready();
  expect(screen.queryByRole('group', {name: 'Selected reference media'})).not.toBeInTheDocument();
  expect(screen.getByLabelText('Image URL')).toHaveValue('');
  expect(screen.getByLabelText('Prompt')).toHaveValue('');
  expect(screen.queryByRole('dialog', {name: 'Review generation'})).not.toBeInTheDocument();
});

it('retains Content factory brief and edited scene drafts while Studio is hidden and still clears them on lock', async () => {
  const view = render(<MediaStudio projectId="project" ownerId="owner"/>); await ready();
  fireEvent.click(screen.getByRole('tab', {name: 'Video'}));
  openTool('Content factory');
  fireEvent.change(screen.getByLabelText('What are we making?'), {target: {value: 'A quiet ceramic collection'}});
  fireEvent.click(screen.getByRole('button', {name: 'Build scene drafts'}));
  fireEvent.change(screen.getByLabelText('Scene 1 prompt'), {target: {value: 'Keep this carefully edited scene'}});
  closePanel();
  view.rerender(<MediaStudio projectId="project" ownerId="owner" visible={false}/>);
  expect(screen.queryByRole('region', {name: 'Content factory'})).not.toBeInTheDocument();
  view.rerender(<MediaStudio projectId="project" ownerId="owner"/>);
  expect(await screen.findByLabelText('What are we making?')).toHaveValue('A quiet ceramic collection');
  expect(screen.getByLabelText('Scene 1 prompt')).toHaveValue('Keep this carefully edited scene');
  expect(screen.queryByRole('dialog', {name: 'Review generation'})).not.toBeInTheDocument();
  expect(submitStudioJob).not.toHaveBeenCalled();
  fireEvent(window, new Event('termx-session-locked'));
  fireEvent(window, new Event('termx-session-unlocked'));
  expect(await screen.findByLabelText('What are we making?')).toHaveValue('');
  expect(screen.queryByLabelText('Scene 1 prompt')).not.toBeInTheDocument();
});


it('keeps the same saved-assets editor mounted through no-account Create and owner-scopes its draft', async () => {
  vi.mocked(loadStudioCatalog).mockResolvedValue({models: [], accounts: []});
  const view = render(<MediaStudio projectId="project" ownerId="owner" section="assets" onSectionChange={vi.fn()}/>);
  fireEvent.change(await screen.findByLabelText('Fixture saved asset draft'), {target: {value: 'Keep the picked-file editor instance'}});
  view.rerender(<MediaStudio projectId="project" ownerId="owner" section="create" onSectionChange={vi.fn()}/>);
  expect(screen.queryByLabelText('Fixture saved asset draft')).not.toBeInTheDocument();
  view.rerender(<MediaStudio projectId="project" ownerId="owner" section="assets" onSectionChange={vi.fn()}/>);
  expect(await screen.findByLabelText('Fixture saved asset draft')).toHaveValue('Keep the picked-file editor instance');
  view.rerender(<MediaStudio projectId="project" ownerId="next" section="assets" onSectionChange={vi.fn()}/>);
  expect(await screen.findByLabelText('Fixture saved asset draft')).toHaveValue('');
});

it('selects an explicit new submission receipt even when its timestamp predates another take', async () => {
  vi.mocked(loadStudioJobs).mockResolvedValue([{...job, id: 'existing', created: 99, status: 'failed'}]);
  vi.mocked(submitStudioJob).mockResolvedValue({...job, id: 'new-receipt', created: 1});
  render(<MediaStudio projectId="project"/>); await ready(false);
  fireEvent.change(screen.getByLabelText('Prompt'), {target: {value: 'Create a new take'}});
  closePanel(); generateDraft(screen.getByRole('button', {name: 'Generate image'}));
  expect(await screen.findByRole('article', {name: 'Google still · Queued'})).toBeVisible();
  expect(screen.getAllByRole('article')).toHaveLength(1);
  expect(submitStudioJob).toHaveBeenCalledOnce();
});


it('keeps the requested tool panel open when entering Create from Library', async () => {
  render(<MediaStudio projectId="project"/>); await ready(false);
  fireEvent.click(screen.getByRole('button', {name: 'Library'}));
  openTool('Lip sync');
  expect(screen.getByRole('dialog', {name: 'Reference media'})).toBeVisible();
  expect(screen.getByLabelText('Image URL')).toBeVisible();
  expect(screen.queryByRole('dialog', {name: 'Review generation'})).not.toBeInTheDocument();
  expect(submitStudioJob).not.toHaveBeenCalled();
});

it('keeps its open settings panel and draft visible while background observation pauses', async () => {
  vi.mocked(loadStudioCatalog).mockResolvedValue(advancedCatalog());
  const view = render(<MediaStudio projectId="project" observing/>); await ready(false);
  fireEvent.change(screen.getByLabelText('Prompt'), {target: {value: 'Keep the visible canvas draft'}});
  closePanel();
  fireEvent.click(screen.getByRole('button', {name: 'Generation settings'}));
  fireEvent.change(screen.getByLabelText('Negative prompt'), {target: {value: 'Keep this direction'}});
  view.rerender(<MediaStudio projectId="project" observing={false}/>);
  expect(screen.getByRole('region', {name: 'Media studio'})).toBeVisible();
  expect(screen.getByRole('dialog', {name: 'Generation settings'})).toBeVisible();
  expect(screen.getByLabelText('Negative prompt')).toHaveValue('Keep this direction');
  expect(screen.getByLabelText('Prompt')).toHaveValue('Keep the visible canvas draft');
  expect(screen.queryByRole('dialog', {name: 'Review generation'})).not.toBeInTheDocument();
  view.rerender(<MediaStudio projectId="project" observing/>);
  expect(screen.getByRole('dialog', {name: 'Generation settings'})).toBeVisible();
  expect(screen.getByLabelText('Negative prompt')).toHaveValue('Keep this direction');
  expect(submitStudioJob).not.toHaveBeenCalled();
});

it('pauses active-job polling during observation loss without hiding Studio and resumes only status checks', async () => {
  vi.useFakeTimers({shouldAdvanceTime: true});
  vi.mocked(loadStudioJobs).mockResolvedValue([job]);
  const view = render(<MediaStudio projectId="project" observing/>); await ready(false);
  await act(async () => {await vi.advanceTimersByTimeAsync(8000);});
  expect(refreshStudioJob).toHaveBeenCalled();
  const count = vi.mocked(refreshStudioJob).mock.calls.length;
  view.rerender(<MediaStudio projectId="project" observing={false}/>);
  expect(screen.getByRole('region', {name: 'Media studio'})).toBeVisible();
  await act(async () => {await vi.advanceTimersByTimeAsync(24000);});
  expect(refreshStudioJob).toHaveBeenCalledTimes(count);
  view.rerender(<MediaStudio projectId="project" observing/>);
  await act(async () => {await vi.advanceTimersByTimeAsync(8000);});
  expect(vi.mocked(refreshStudioJob).mock.calls.length).toBeGreaterThan(count);
  expect(submitStudioJob).not.toHaveBeenCalled();
});

it('initializes supported per-mode defaults and shares image defaults with Cinema', async () => {
  const configured = structuredClone(catalog);
  configured.models.push({...configured.models[0], id: 'kie/still', label: 'Kie still', adapter: 'kie'});
  configured.settings = {revision: 1, enabled_models: null, limits: {daily_generations: null, concurrent_generations: null, max_video_seconds: null}, defaults: {
    image: {provider_id: 'kie', model: 'kie/still', aspect_ratio: '16:9', resolution: '2K'},
    video: {provider_id: 'or', model: 'google/video', duration: 10},
  }};
  vi.mocked(loadStudioCatalog).mockResolvedValue(configured);
  render(<MediaStudio ownerId="owner" projectId="project"/>);
  await screen.findByRole('option', {name: 'Kie still'});
  expect(screen.getByLabelText('Model')).toHaveValue('kie/still');
  expect(screen.getByLabelText('Aspect ratio')).toHaveValue('16:9');
  expect(screen.getByLabelText('Resolution')).toHaveValue('2K');
  fireEvent.click(screen.getByRole('button', {name: 'Generation settings'}));
  expect(screen.getByLabelText('Provider account')).toHaveValue('kie'); closePanel();
  openTool('Cinema'); closePanel();
  expect(screen.getByLabelText('Model')).toHaveValue('kie/still');
  expect(screen.getByLabelText('Aspect ratio')).toHaveValue('16:9');
  fireEvent.click(screen.getByRole('tab', {name: 'Video'}));
  expect(screen.getByLabelText('Model')).toHaveValue('google/video');
  expect(screen.getByLabelText('Duration')).toHaveValue('10');
});

it('refreshes saved configuration without replacing manual selections, prompt or references', async () => {
  const configured = structuredClone(catalog);
  configured.models[0].supports_image = true; configured.models[0].inline_inputs = ['image'];
  configured.models.push({...configured.models[0], id: 'google/second', label: 'Second still'});
  vi.mocked(loadStudioCatalog).mockResolvedValue(configured);
  const view = render(<MediaStudio ownerId="owner" projectId="project" catalogRevision={0}/>);
  await ready(false);
  const prompt = screen.getByLabelText('Prompt');
  fireEvent.change(prompt, {target: {value: 'Keep my exact scene'}});
  fireEvent.change(screen.getByLabelText('Model'), {target: {value: 'google/second'}});
  fireEvent.change(screen.getByLabelText('Aspect ratio'), {target: {value: '16:9'}});
  openReferences(); fireEvent.change(screen.getByLabelText('Image URL'), {target: {value: 'https://media.example.com/reference.png'}}); closePanel();
  configured.settings = {revision: 2, enabled_models: null, limits: {daily_generations: null, concurrent_generations: null, max_video_seconds: null}, defaults: {image: {provider_id: 'or', model: 'google/still', aspect_ratio: '1:1'}}};
  vi.mocked(loadStudioCatalog).mockResolvedValue(structuredClone(configured));
  view.rerender(<MediaStudio ownerId="owner" projectId="project" catalogRevision={1}/>);
  await waitFor(() => expect(loadStudioCatalog).toHaveBeenCalledTimes(2));
  expect(screen.getByLabelText('Prompt')).toBe(prompt); expect(prompt).toHaveValue('Keep my exact scene');
  expect(screen.getByLabelText('Model')).toHaveValue('google/second');
  expect(screen.getByLabelText('Aspect ratio')).toHaveValue('16:9');
  openReferences(); expect(screen.getByLabelText('Image URL')).toHaveValue('https://media.example.com/reference.png'); closePanel();
  fireEvent.click(screen.getByRole('tab', {name: 'Video'}));
  fireEvent.click(screen.getByRole('tab', {name: 'Image'}));
  expect(screen.getByLabelText('Model')).toHaveValue('google/second');
});

it('requires an explicit replacement when a settings refresh disables the chosen model', async () => {
  const configured = structuredClone(catalog);
  configured.models.push({...configured.models[0], id: 'google/second', label: 'Second still'});
  vi.mocked(loadStudioCatalog).mockResolvedValue(configured);
  const view = render(<MediaStudio ownerId="owner" projectId="project" catalogRevision={0}/>);
  await ready(false);
  fireEvent.change(screen.getByLabelText('Prompt'), {target: {value: 'Do not change my model'}});
  closePanel(); expect(screen.getByRole('button', {name: 'Generate image'})).toBeEnabled();
  vi.mocked(loadStudioCatalog).mockResolvedValue({...configured, models: configured.models.filter(model => model.id !== 'google/still')});
  view.rerender(<MediaStudio ownerId="owner" projectId="project" catalogRevision={1}/>);
  await screen.findByText('Your selected model or provider is no longer available. Choose a supported model and provider before generating.');
  expect(screen.getByLabelText('Model')).toHaveValue('');
  expect(screen.getByRole('button', {name: 'Generate image'})).toBeDisabled();
  expect(screen.queryByRole('dialog', {name: 'Review generation'})).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText('Model'), {target: {value: 'google/second'}});
  expect(screen.getByRole('button', {name: 'Generate image'})).toBeEnabled();
  expect(submitStudioJob).not.toHaveBeenCalled();
  closePanel(); generateDraft(screen.getByRole('button', {name: 'Generate image'}));
  await waitFor(() => expect(submitStudioJob).toHaveBeenCalledOnce());
  expect(vi.mocked(submitStudioJob).mock.calls[0][2].model).toBe('google/second');
});


it('keeps editing and model changes as drafts until the explicit Generate click', async () => {
  const models = structuredClone(catalog);
  models.models.push({...models.models[0], id: 'google/second', label: 'Second still'});
  vi.mocked(loadStudioCatalog).mockResolvedValue(models);
  render(<MediaStudio projectId="project"/>); await ready(false);
  const prompt = screen.getByLabelText('Prompt');
  fireEvent.change(prompt, {target: {value: 'First idea'}});
  fireEvent.change(prompt, {target: {value: 'Changed idea'}});
  fireEvent.change(screen.getByLabelText('Model'), {target: {value: 'google/second'}});
  expect(submitStudioJob).not.toHaveBeenCalled();
  generateDraft(screen.getByRole('button', {name: 'Generate image'}));
  await waitFor(() => expect(submitStudioJob).toHaveBeenCalledOnce());
  expect(vi.mocked(submitStudioJob).mock.calls[0][2]).toMatchObject({prompt: 'Changed idea', model: 'google/second', acknowledge_billing: true});
});

it('rejects stale Generate controls while hidden, backgrounded, unobserved or locked', async () => {
  const view = render(<MediaStudio projectId="project"/>); await ready(false);
  fireEvent.change(screen.getByLabelText('Prompt'), {target: {value: 'Keep this unsubmitted idea'}});
  const generate = screen.getByRole('button', {name: 'Generate image'});
  view.rerender(<MediaStudio projectId="project" visible={false}/>);
  fireEvent.click(generate);
  view.rerender(<MediaStudio projectId="project" observing={false}/>);
  fireEvent.submit(screen.getByRole('form', {name: 'Generation composer'}));
  view.rerender(<MediaStudio projectId="project"/>); await ready(false);
  Object.defineProperty(document, 'visibilityState', {configurable: true, value: 'hidden'});
  fireEvent.submit(screen.getByRole('form', {name: 'Generation composer'}));
  Object.defineProperty(document, 'visibilityState', {configurable: true, value: 'visible'});
  act(() => window.dispatchEvent(new Event('termx-session-locked')));
  fireEvent.click(generate);
  expect(submitStudioJob).not.toHaveBeenCalled();
});

it('drops the old owner generation receipt after a session change', async () => {
  let resolve!: (job: StudioJob) => void;
  vi.mocked(submitStudioJob).mockImplementation(() => new Promise(done => {resolve = done;}));
  const view = render(<MediaStudio ownerId="first" projectId="project"/>); await ready(false);
  fireEvent.change(screen.getByLabelText('Prompt'), {target: {value: 'First owner scene'}});
  generateDraft(screen.getByRole('button', {name: 'Generate image'}));
  const signal = vi.mocked(submitStudioJob).mock.calls[0][3];
  view.rerender(<MediaStudio ownerId="second" projectId="other"/>); await ready(false);
  expect(signal?.aborted).toBe(true);
  await act(async () => resolve({...job, prompt: 'First owner scene'}));
  expect(screen.getByLabelText('Prompt')).toHaveValue('');
  expect(screen.queryByRole('article')).not.toBeInTheDocument();
  expect(screen.queryByText('Job received. Track its status in your results.')).not.toBeInTheDocument();
});

it('opens an explicit gallery selection in a focused preview without generating', async () => {
  vi.mocked(loadStudioJobs).mockResolvedValue([{...job, id: 'take-one', status: 'failed'}]);
  render(<MediaStudio projectId="project" section="library"/>);
  const select = await screen.findByRole('button', {name: 'View generation take-one'});
  fireEvent.click(select);
  const preview = screen.getByRole('dialog', {name: 'Generation preview'});
  expect(within(preview).getByText('A quiet landscape')).toBeVisible();
  expect(select).toHaveAttribute('aria-pressed', 'true');
  expect(submitStudioJob).not.toHaveBeenCalled();
  fireEvent.click(within(preview).getByRole('button', {name: 'Close Generation preview'}));
  expect(screen.queryByRole('dialog', {name: 'Generation preview'})).not.toBeInTheDocument();
});


it('shows output draft actions only when a configured model supports the handoff', async () => {
  vi.mocked(loadStudioJobs).mockResolvedValue([{...job, status: 'completed', outputs: ['https://media.example.com/result.png']}]);
  render(<MediaStudio projectId="project"/>); await ready(false);
  expect(screen.getByRole('button', {name: 'Regenerate'})).toBeVisible();
  expect(screen.queryByRole('button', {name: 'Edit image'})).not.toBeInTheDocument();
  expect(screen.queryByRole('button', {name: 'Animate'})).not.toBeInTheDocument();
  expect(screen.queryByRole('button', {name: 'Use as reference'})).not.toBeInTheDocument();
  expect(submitStudioJob).not.toHaveBeenCalled();
});

it('stages an output as a new reference draft without approving provider charges', async () => {
  vi.mocked(loadStudioCatalog).mockResolvedValue(advancedCatalog());
  vi.mocked(loadStudioJobs).mockResolvedValue([{...job, status: 'completed', outputs: ['https://media.example.com/result.png']}]);
  render(<MediaStudio projectId="project"/>); await ready(false);
  fireEvent.click(screen.getByRole('button', {name: 'Use as reference'}));
  expect(screen.getByLabelText('Prompt')).toHaveValue('');
  openReferences();
  expect(screen.getByLabelText('Image URL')).toHaveValue('https://media.example.com/result.png');
  expect(submitStudioJob).not.toHaveBeenCalled();
  expect(screen.queryByRole('dialog', {name: 'Review generation'})).not.toBeInTheDocument();
});
