import {afterEach, beforeEach, expect, it, vi} from 'vitest';
import {act, cleanup, fireEvent, render, screen, waitFor} from '@testing-library/react';
import StudioPricing from './StudioPricing';
import {loadStudioPricing, type StudioPricingGuide} from '../lib/studio-pricing';

vi.mock('../lib/studio-pricing', async original => ({...await original<typeof import('../lib/studio-pricing')>(), loadStudioPricing: vi.fn()}));
const model = 'google/gemini-3-pro-image';
const guide: StudioPricingGuide = {
  model, status: 'available', source_url: 'https://openrouter.ai/api/v1/images/models/' + model + '/endpoints', observed_at: '2026-11-10T12:00:00Z',
  rates: [{provider: 'Google Vertex', billable: 'output_image', unit: 'token', cost_usd: '0.00012'}, {provider: 'Google Vertex', billable: 'input_image', unit: 'image', cost_usd: '0.02', variant: '2k'}],
};
const props = {projectId: 'project', ownerId: 'owner', modelId: model, adapter: 'openrouter' as const, visible: true};
function expand() {fireEvent.click(screen.getByRole('button', {name: 'Published rates'}));}
beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(loadStudioPricing).mockResolvedValue(guide);
  Object.defineProperty(document, 'visibilityState', {configurable: true, value: 'visible'});
});
afterEach(cleanup);

it('fetches only after expansion and preserves mixed published units without a quote', async () => {
  const view = render(<form><StudioPricing {...props}/></form>);
  expect(loadStudioPricing).not.toHaveBeenCalled();
  expand();
  expect(await screen.findByText('USD 0.00012')).toBeInTheDocument();
  expect(screen.getByText('per token')).toBeInTheDocument();
  expect(screen.getByText('per image')).toBeInTheDocument();
  expect(screen.getByText('2k')).toBeInTheDocument();
  expect(screen.getAllByText('Google Vertex')).toHaveLength(2);
  expect(screen.queryByText(/Actual charges depend on inputs and settings/)).not.toBeInTheDocument();
  expect(screen.getByText(/Checked/).closest('time')).toHaveAttribute('datetime', guide.observed_at);
  expect(screen.getByRole('link', {name: 'OpenRouter source'})).toHaveAttribute('href', guide.source_url);
  expect(loadStudioPricing).toHaveBeenCalledExactlyOnceWith('project', model, expect.any(AbortSignal));
  expect(view.container.textContent).not.toMatch(/estimated|free|total|quote/i);
  expect(view.container.querySelectorAll('form')).toHaveLength(1);
  expect([...view.container.querySelectorAll('button')].every(button => button.type === 'button')).toBe(true);
});

it('shows a loading state, then recoverable metadata failure with an explicit retry', async () => {
  let fail!: (error: Error) => void;
  vi.mocked(loadStudioPricing).mockReturnValueOnce(new Promise((_resolve, reject) => fail = reject));
  render(<StudioPricing {...props}/>); expand();
  expect(screen.getByRole('status')).toHaveTextContent('Checking published rates');
  await act(async () => fail(Error('upstream-private-detail')));
  expect(screen.getByRole('status')).toHaveTextContent('Rates could not be loaded');
  expect(screen.queryByText('upstream-private-detail')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', {name: 'Retry rates'}));
  await screen.findByText('USD 0.00012');
  expect(loadStudioPricing).toHaveBeenCalledTimes(2);
});

it('keeps unknown rates unavailable without turning placeholders into free media', async () => {
  vi.mocked(loadStudioPricing).mockResolvedValue({...guide, status: 'unavailable', rates: []});
  const view = render(<StudioPricing {...props}/>); expand();
  expect(await screen.findByText('Rates unavailable.')).toBeInTheDocument();
  expect(screen.queryByRole('list')).not.toBeInTheDocument();
  expect(view.container.textContent).not.toMatch(/free|estimated|total/i);
});

it('does not fetch unsupported Kie metadata', () => {
  render(<StudioPricing {...props} adapter="kie"/>); expand();
  expect(screen.getByText('Rates unavailable.')).toBeInTheDocument();
  expect(loadStudioPricing).not.toHaveBeenCalled();
});

it.each(['project', 'owner', 'model', 'adapter', 'hidden'])('aborts and discards stale metadata when %s changes', async change => {
  let finish!: (value: StudioPricingGuide) => void;
  vi.mocked(loadStudioPricing).mockReturnValueOnce(new Promise(resolve => finish = resolve));
  const view = render(<StudioPricing {...props}/>); expand();
  const signal = vi.mocked(loadStudioPricing).mock.calls[0][2];
  const next = {...props, ...(change === 'project' ? {projectId: 'next'} : change === 'owner' ? {ownerId: 'next'} : change === 'model' ? {modelId: 'krea/k3-edit'} : change === 'adapter' ? {adapter: 'kie' as const} : {visible: false})};
  view.rerender(<StudioPricing {...next}/>);
  expect(signal?.aborted).toBe(true);
  await act(async () => finish(guide));
  expect(screen.queryByText('USD 0.00012')).not.toBeInTheDocument();
  expect(loadStudioPricing).toHaveBeenCalledTimes(1);
  if (change !== 'hidden') expect(screen.getByRole('button', {name: 'Published rates'})).toHaveAttribute('aria-expanded', 'false');
});

it.each(['termx-locked', 'termx-session-locked', 'termx-signed-out', 'document-hidden'])('clears pending data on %s and remains collapsed when restored', async event => {
  let finish!: (value: StudioPricingGuide) => void;
  vi.mocked(loadStudioPricing).mockReturnValueOnce(new Promise(resolve => finish = resolve));
  render(<StudioPricing {...props}/>); expand();
  const signal = vi.mocked(loadStudioPricing).mock.calls[0][2];
  if (event === 'document-hidden') {
    Object.defineProperty(document, 'visibilityState', {configurable: true, value: 'hidden'});
    fireEvent(document, new Event('visibilitychange'));
  } else fireEvent(window, new Event(event));
  expect(signal?.aborted).toBe(true);
  await act(async () => finish(guide));
  expect(screen.queryByRole('button', {name: 'Published rates'})).not.toBeInTheDocument();
  if (event === 'document-hidden') {
    Object.defineProperty(document, 'visibilityState', {configurable: true, value: 'visible'});
    fireEvent(document, new Event('visibilitychange'));
  } else fireEvent(window, new Event('termx-session-unlocked'));
  expect(screen.getByRole('button', {name: 'Published rates'})).toHaveAttribute('aria-expanded', 'false');
  expect(screen.queryByText('USD 0.00012')).not.toBeInTheDocument();
  expect(loadStudioPricing).toHaveBeenCalledTimes(1);
});

it('rejects a response for another model even if an adapter bypasses the parser', async () => {
  vi.mocked(loadStudioPricing).mockResolvedValue({...guide, model: 'other/model'});
  render(<StudioPricing {...props}/>); expand();
  await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('Rates could not be loaded'));
  expect(screen.queryByText('USD 0.00012')).not.toBeInTheDocument();
});

it.each(['https://evil.test/api/v1/videos/models', 'javascript:alert(1)', 'https://openrouter.ai.evil.test/api/v1/videos/models', 'https://name:pass@openrouter.ai/api/v1/videos/models', 'https://openrouter.ai/private', 'https://openrouter.ai/api/v1/videos/models?token=secret'])('never links an untrusted pricing source %s', async source_url => {
  vi.mocked(loadStudioPricing).mockResolvedValue({...guide, source_url});
  render(<StudioPricing {...props}/>); expand(); await screen.findByText('USD 0.00012');
  expect(screen.queryByRole('link')).not.toBeInTheDocument();
});
