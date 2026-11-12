import {afterEach, beforeEach, expect, it, vi} from 'vitest';
import {act, cleanup, fireEvent, render, screen, waitFor, within} from '@testing-library/react';
import MediaFactory from './MediaFactory';
import {createStudioClient, type StudioCatalog, type StudioJob} from '../lib/media-studio';
import {loadStudioPricing} from '../lib/studio-pricing';
vi.mock('../lib/media-studio', () => ({createStudioClient: vi.fn()}));
vi.mock('../lib/studio-pricing', async original => ({...await original<typeof import('../lib/studio-pricing')>(), loadStudioPricing: vi.fn()}));
const generate = vi.fn();
const catalog: StudioCatalog = {models: [{id: 'video', label: 'Verified video', adapter: 'openrouter', kind: 'video', modes: ['video'], description: 'Test', aspect_ratios: ['9:16'], resolutions: ['720p'], durations: [5]}], accounts: [{id: 'account', name: 'Selected account', adapter: 'openrouter', capabilities: ['video'], configured: true}]};
const job = (prompt: string, status: StudioJob['status'] = 'queued'): StudioJob => ({id: 'job-' + prompt, project_id: 'project', provider_id: 'account', model: 'video', kind: 'video', prompt, status, created: 1, updated: 1, outputs: []});
beforeEach(() => {
  vi.clearAllMocks();
  Object.defineProperty(document, 'visibilityState', {configurable: true, value: 'visible'});
  vi.mocked(createStudioClient).mockReturnValue({generateImage: generate, generateVideo: generate, processLipSync: generate, remixVideo: vi.fn(), pollForResult: vi.fn()});
  vi.mocked(loadStudioPricing).mockResolvedValue({model: 'video', status: 'unavailable', rates: [], source_url: 'https://openrouter.ai/api/v1/videos/models', observed_at: '2026-11-11T00:00:00Z'});
  generate.mockImplementation(async input => job(input.prompt));
});
afterEach(cleanup);
function build() {fireEvent.change(screen.getByLabelText('What are we making?'), {target: {value: 'A ceramic collection'}}); fireEvent.click(screen.getByRole('button', {name: 'Build scene drafts'}));}
function review(count = 3) {fireEvent.click(screen.getByRole('button', {name: `Review ${count} scenes`})); return screen.getByRole('dialog', {name: 'Review scene batch'});}
function approve(count = 3) {const panel = review(count); fireEvent.click(within(panel).getByRole('button', {name: `Queue ${count} scenes with Verified video`}));}

it('keeps drafting and review local and only authorizes the exact remaining batch from its named action', async () => {
  const onJob = vi.fn();
  render(<MediaFactory catalog={catalog} ownerId="owner" projectId="project" visible onJob={onJob}/>);
  build();
  const prompt = (screen.getByLabelText('Scene 1 prompt') as HTMLTextAreaElement).value;
  expect(prompt).toContain('A ceramic collection');
  expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();
  const panel = review();
  expect(generate).not.toHaveBeenCalled();
  expect(within(panel).getByRole('button', {name: 'Cancel batch'})).toHaveFocus();
  expect(within(panel).getByText('Selected account')).toBeInTheDocument();
  expect(within(panel).getByText('Verified video')).toBeInTheDocument();
  for (const value of ['9:16', '720p', '5 seconds']) expect(within(panel).getByText(value)).toBeInTheDocument();
  expect(within(panel).getByRole('list', {name: 'Exact scene prompts'}).querySelector('li p')?.textContent).toBe(prompt);
  expect(within(panel).queryByText(/authorizes provider charges/)).not.toBeInTheDocument();
  expect(within(panel).queryByRole('heading', {name: 'Published unit rates'})).not.toBeInTheDocument();
  const queue = within(panel).getByRole('button', {name: 'Queue 3 scenes with Verified video'});
  fireEvent.click(queue); fireEvent.click(queue);
  await waitFor(() => expect(onJob).toHaveBeenCalledTimes(3));
  expect(generate).toHaveBeenCalledTimes(3);
  expect(generate).toHaveBeenNthCalledWith(1, {provider_id: 'account', model: 'video', prompt, aspect_ratio: '9:16', resolution: '720p', duration: 5, acknowledge_billing: true}, expect.any(AbortSignal));
  expect(screen.queryByRole('dialog', {name: 'Review scene batch'})).not.toBeInTheDocument();
});
it('cancels a reviewed batch without generation and invalidates review when its draft changes', () => {
  render(<MediaFactory catalog={catalog} projectId="project" visible onJob={vi.fn()}/>);
  build(); review();
  fireEvent.click(screen.getByRole('button', {name: 'Cancel batch'}));
  expect(generate).not.toHaveBeenCalled();
  expect(screen.queryByRole('dialog', {name: 'Review scene batch'})).not.toBeInTheDocument();
  review(); fireEvent.change(screen.getByLabelText('Scene 2 prompt'), {target: {value: 'A newly edited scene'}});
  expect(screen.queryByRole('dialog', {name: 'Review scene batch'})).not.toBeInTheDocument();
  expect(generate).not.toHaveBeenCalled();
  const updated = review(); expect(within(updated).getByText('A newly edited scene')).toBeInTheDocument();
});
it('invalidates a reviewed batch when catalog selection changes before confirmation', () => {
  const onJob = vi.fn(); const view = render(<MediaFactory catalog={catalog} projectId="project" visible onJob={onJob}/>);
  build(); review();
  const next = {...catalog, models: catalog.models.map(model => ({...model, id: 'new-video', label: 'New video'}))};
  view.rerender(<MediaFactory catalog={next} projectId="project" visible onJob={onJob}/>);
  expect(screen.queryByRole('dialog', {name: 'Review scene batch'})).not.toBeInTheDocument();
  expect(generate).not.toHaveBeenCalled();
});
it('pauses the batch on uncertain response and never automatically resubmits or queues later scenes', async () => {
  generate.mockRejectedValueOnce(new Error('The host request timed out'));
  render(<MediaFactory catalog={catalog} projectId="project" visible onJob={vi.fn()}/>);
  build(); approve();
  await screen.findByRole('alert');
  expect(generate).toHaveBeenCalledTimes(1);
  expect(screen.getByLabelText('Scene 1 prompt')).toBeDisabled();
  const panel = review(2);
  expect(within(panel).getByRole('list', {name: 'Exact scene prompts'})).not.toHaveTextContent('Scene 1');
  expect(generate).toHaveBeenCalledTimes(1);
  fireEvent.click(within(panel).getByRole('button', {name: 'Cancel batch'}));
  expect(generate).toHaveBeenCalledTimes(1);
});
it('does not continue a batch or disclose accepted results after the workspace locks', async () => {
  let finish!: (value: StudioJob) => void;
  generate.mockReturnValueOnce(new Promise(resolve => finish = resolve));
  const onJob = vi.fn();
  render(<MediaFactory catalog={catalog} projectId="project" visible onJob={onJob}/>);
  build(); approve();
  await waitFor(() => expect(generate).toHaveBeenCalledOnce());
  fireEvent(window, new Event('termx-session-locked'));
  await act(async () => finish(job('Accepted after lock')));
  expect(generate).toHaveBeenCalledOnce(); expect(onJob).not.toHaveBeenCalled();
  expect(screen.queryByRole('region', {name: 'Content factory'})).not.toBeInTheDocument();
});
it('fences an in-flight batch when its owner/project changes', async () => {
  let finish!: (value: StudioJob) => void;
  generate.mockReturnValueOnce(new Promise(resolve => finish = resolve));
  const onJob = vi.fn();
  const view = render(<MediaFactory catalog={catalog} ownerId="first" projectId="project" visible onJob={onJob}/>);
  build(); approve();
  await waitFor(() => expect(generate).toHaveBeenCalledOnce());
  view.rerender(<MediaFactory catalog={catalog} ownerId="second" projectId="next" visible onJob={onJob}/>);
  await act(async () => finish(job('Previous owner output')));
  expect(onJob).not.toHaveBeenCalled(); expect(generate).toHaveBeenCalledOnce();
  expect(screen.getByLabelText('What are we making?')).toHaveValue('');
});
it('does not offer reference-required models as a text-to-video factory', () => {
  const referenceOnly = {...catalog, models: catalog.models.map(row => ({...row, requires_image: true}))};
  render(<MediaFactory catalog={referenceOnly} projectId="project" visible onJob={vi.fn()}/>);
  expect(screen.getByLabelText('Factory model')).toBeDisabled();
  expect(screen.getByRole('button', {name: 'Review 0 scenes'})).toBeDisabled();
  expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();
});
it('pauses observation without removing form drafts, closes review and never queues on resume', async () => {
  const onJob = vi.fn();
  const view = render(<MediaFactory catalog={catalog} projectId="project" visible onJob={onJob}/>);
  build();
  const brief = screen.getByLabelText('What are we making?'), scene = screen.getByLabelText('Scene 2 prompt');
  fireEvent.change(scene, {target: {value: 'Keep this edited scene'}}); review();
  view.rerender(<MediaFactory catalog={catalog} projectId="project" visible observing={false} onJob={onJob}/>);
  expect(screen.getByLabelText('What are we making?')).toBe(brief); expect(brief).toHaveValue('A ceramic collection');
  expect(screen.getByLabelText('Scene 2 prompt')).toBe(scene); expect(scene).toHaveValue('Keep this edited scene'); expect(scene).toBeEnabled();
  expect(screen.queryByRole('dialog', {name: 'Review scene batch'})).not.toBeInTheDocument();
  expect(screen.getByRole('button', {name: 'Review 3 scenes'})).toBeDisabled();
  expect(screen.queryByRole('button', {name: 'Published rates'})).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', {name: 'Review 3 scenes'})); expect(generate).not.toHaveBeenCalled();
  view.rerender(<MediaFactory catalog={catalog} projectId="project" visible observing onJob={onJob}/>);
  expect(screen.getByLabelText('What are we making?')).toBe(brief); expect(scene).toHaveValue('Keep this edited scene');
  expect(screen.getByRole('button', {name: 'Review 3 scenes'})).toBeEnabled();
  expect(screen.queryByRole('button', {name: 'Published rates'})).not.toBeInTheDocument(); expect(generate).not.toHaveBeenCalled();
  approve(); await waitFor(() => expect(onJob).toHaveBeenCalledTimes(3));
  expect(generate).toHaveBeenNthCalledWith(2, expect.objectContaining({prompt: 'Keep this edited scene'}), expect.any(AbortSignal));
});
it('aborts an observed batch pause, marks the pending scene unknown and ignores a late receipt after resume', async () => {
  let finish!: (value: StudioJob) => void;
  generate.mockReturnValueOnce(new Promise(resolve => finish = resolve));
  const onJob = vi.fn();
  const view = render(<MediaFactory catalog={catalog} projectId="project" visible onJob={onJob}/>);
  build(); const brief = screen.getByLabelText('What are we making?'); approve();
  const signal = generate.mock.calls[0][1] as AbortSignal;
  view.rerender(<MediaFactory catalog={catalog} projectId="project" visible observing={false} onJob={onJob}/>);
  expect(signal.aborted).toBe(true); expect(screen.getByLabelText('What are we making?')).toBe(brief); expect(brief).toHaveValue('A ceramic collection');
  expect(screen.getByText('Needs review')).toBeInTheDocument(); expect(screen.getByText(/Batch paused. Refresh the library/)).toBeInTheDocument();
  expect(screen.getByLabelText('Scene 1 prompt')).toBeDisabled(); expect(screen.getByLabelText('Scene 2 prompt')).toBeEnabled();
  view.rerender(<MediaFactory catalog={catalog} projectId="project" visible observing onJob={onJob}/>);
  expect(screen.getByRole('button', {name: 'Review 2 scenes'})).toBeEnabled();
  await act(async () => finish(job('Late accepted receipt')));
  expect(onJob).not.toHaveBeenCalled(); expect(generate).toHaveBeenCalledOnce(); expect(screen.getByText('Needs review')).toBeInTheDocument();
  expect(screen.queryByText('Job queued')).not.toBeInTheDocument();
  approve(2); await waitFor(() => expect(onJob).toHaveBeenCalledTimes(2)); expect(generate).toHaveBeenCalledTimes(3);
  expect(screen.queryByRole('dialog', {name: 'Review scene batch'})).not.toBeInTheDocument();
});

it('drops unsubmitted review on hide and lock without authorizing any provider call', () => {
  const onJob = vi.fn(); const view = render(<MediaFactory catalog={catalog} projectId="project" visible onJob={onJob}/>);
  build(); review();
  view.rerender(<MediaFactory catalog={catalog} projectId="project" visible={false} onJob={onJob}/>);
  expect(screen.queryByRole('dialog', {name: 'Review scene batch'})).not.toBeInTheDocument();
  view.rerender(<MediaFactory catalog={catalog} projectId="project" visible onJob={onJob}/>);
  expect(screen.getByLabelText('What are we making?')).toHaveValue('A ceramic collection');
  expect(screen.queryByRole('dialog', {name: 'Review scene batch'})).not.toBeInTheDocument();
  review(); fireEvent(window, new Event('termx-session-locked'));
  expect(screen.queryByRole('dialog', {name: 'Review scene batch'})).not.toBeInTheDocument();
  fireEvent(window, new Event('termx-session-unlocked'));
  expect(screen.getByLabelText('What are we making?')).toHaveValue('');
  expect(generate).not.toHaveBeenCalled(); expect(onJob).not.toHaveBeenCalled();
});


it('discards a reviewed batch on document hide and requires renewed review after returning', () => {
  render(<MediaFactory catalog={catalog} projectId="project" visible onJob={vi.fn()}/>);
  build(); const panel = review();
  const staleConfirm = within(panel).getByRole('button', {name: 'Queue 3 scenes with Verified video'});
  Object.defineProperty(document, 'visibilityState', {configurable: true, value: 'hidden'});
  fireEvent(document, new Event('visibilitychange'));
  expect(screen.queryByRole('dialog', {name: 'Review scene batch'})).not.toBeInTheDocument();
  fireEvent.click(staleConfirm); expect(generate).not.toHaveBeenCalled();
  Object.defineProperty(document, 'visibilityState', {configurable: true, value: 'visible'});
  fireEvent(document, new Event('visibilitychange'));
  expect(screen.getByLabelText('What are we making?')).toHaveValue('A ceramic collection');
  expect(screen.getByRole('button', {name: 'Review 3 scenes'})).toBeEnabled();
  expect(screen.queryByRole('dialog', {name: 'Review scene batch'})).not.toBeInTheDocument();
  expect(generate).not.toHaveBeenCalled();
});

it('aborts a document-hidden batch and never queues later scenes from a late receipt', async () => {
  let finish!: (value: StudioJob) => void;
  generate.mockReturnValueOnce(new Promise(resolve => finish = resolve));
  const onJob = vi.fn();
  render(<MediaFactory catalog={catalog} projectId="project" visible onJob={onJob}/>);
  build(); approve();
  const signal = generate.mock.calls[0][1] as AbortSignal;
  Object.defineProperty(document, 'visibilityState', {configurable: true, value: 'hidden'});
  fireEvent(document, new Event('visibilitychange'));
  expect(signal.aborted).toBe(true);
  expect(screen.getByLabelText('Scene 1 prompt')).toBeDisabled();
  expect(screen.getByLabelText('Scene 2 prompt')).toBeEnabled();
  expect(screen.getByText('Needs review')).toBeVisible();
  Object.defineProperty(document, 'visibilityState', {configurable: true, value: 'visible'});
  fireEvent(document, new Event('visibilitychange'));
  await act(async () => finish(job('Late document-hidden receipt')));
  expect(generate).toHaveBeenCalledOnce(); expect(onJob).not.toHaveBeenCalled();
  expect(screen.getByRole('button', {name: 'Review 2 scenes'})).toBeEnabled();
  expect(screen.getByLabelText('What are we making?')).toHaveValue('A ceramic collection');
});
