import {beforeEach, expect, it, vi} from 'vitest';
import {request} from './api';
import {loadStudioPricing} from './studio-pricing';

vi.mock('./api', () => ({request: vi.fn()}));
const model = 'bytedance/seedance-2.5';
const guide = {model, status: 'available', rates: [{provider: 'OpenRouter (model rates)', billable: 'output_video', unit: 'token', cost_usd: '0.0000107', variant: 'with_video_input'}], source_url: 'https://openrouter.ai/api/v1/videos/models', observed_at: '2026-11-10T12:00:00Z'};
beforeEach(() => {vi.clearAllMocks(); vi.mocked(request).mockResolvedValue(guide);});
it('requests an encoded, scoped GET and keeps the published decimal string unchanged', async () => {
  const controller = new AbortController();
  expect(await loadStudioPricing('project & a', model, controller.signal)).toEqual(guide);
  expect(request).toHaveBeenCalledExactlyOnceWith('/api/media/studio/pricing?model=bytedance%2Fseedance-2.5&project_id=project+%26+a', {signal: controller.signal});
});
it.each([
  {...guide, model: 'other/model'}, {...guide, rates: []}, {...guide, observed_at: 'yesterday'}, {...guide, status: 'unavailable'},
  {...guide, rates: [{...guide.rates[0], unit: 'unknown'}]}, {...guide, rates: [{...guide.rates[0], cost_usd: '-1'}]},
  {...guide, rates: [{...guide.rates[0], cost_usd: 'NaN'}]}, {...guide, rates: [{...guide.rates[0], cost_usd: 'Infinity'}]},
  {...guide, rates: [{...guide.rates[0], cost_usd: 0.01}]}, {...guide, rates: [{...guide.rates[0], cost_usd: '9'.repeat(65)}]},
  {...guide, rates: [{...guide.rates[0], billable: 'unknown'}]}, {...guide, rates: [{...guide.rates[0], variant: '<script>'}]},
])('rejects malformed or mismatched metadata without exposing it', async value => {
  vi.mocked(request).mockResolvedValue(value);
  await expect(loadStudioPricing(null, model)).rejects.toThrow('Published rates could not be verified');
});
