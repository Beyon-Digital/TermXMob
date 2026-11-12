import {request} from './api';

export type PublishedRate = {provider: string; billable: string; unit: string; cost_usd: string; variant?: string};
export type StudioPricingGuide = {
  model: string; status: 'available' | 'unavailable'; rates: PublishedRate[];
  source_url: string; observed_at: string; message?: string;
};
const invalid = () => Error('Published rates could not be verified. Check the provider before generating.');
const object = (value: unknown): Record<string, unknown> | null => value !== null && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : null;
const label = (value: unknown, limit = 100): value is string => typeof value === 'string' && value.trim().length > 0 && value.length <= limit && !/[\u0000-\u001f\u007f]/.test(value);
const units = new Set(['image', 'token', 'request', 'second', 'generation']);
const billables = new Set(['input_image', 'output_image', 'input_text', 'output_text', 'output_video', 'minimum_generation_charge']);

export function pricingSourceUrl(value: string, model: string): string | null {
  try {
    const url = new URL(value);
    if (url.origin !== 'https://openrouter.ai' || url.username || url.password || url.search || url.hash) return null;
    if (url.pathname !== '/api/v1/videos/models' && url.pathname !== '/api/v1/images/models/' + model + '/endpoints') return null;
    return url.href;
  } catch { return null; }
}

export async function loadStudioPricing(projectId: string | null, model: string, signal?: AbortSignal): Promise<StudioPricingGuide> {
  const query = new URLSearchParams({model});
  if (projectId !== null) query.set('project_id', projectId);
  const value = object(await request<unknown>('/api/media/studio/pricing?' + query.toString(), {signal}));
  if (!value || value.model !== model || !['available', 'unavailable'].includes(String(value.status)) || !Array.isArray(value.rates) || value.rates.length > 200 || !label(value.source_url, 500) || !label(value.observed_at, 64) || !Number.isFinite(Date.parse(value.observed_at))) throw invalid();
  const rates: PublishedRate[] = value.rates.map((raw: unknown) => {
    const rate = object(raw);
    if (!rate || !label(rate.provider) || !label(rate.billable) || !billables.has(rate.billable) || !label(rate.unit) || !units.has(rate.unit) || typeof rate.cost_usd !== 'string' || rate.cost_usd.length > 64 || !/^[0-9]+(?:\.[0-9]+)?$/.test(rate.cost_usd) || !Number.isFinite(Number(rate.cost_usd)) || Number(rate.cost_usd) > 1_000_000_000 || (rate.variant !== undefined && (typeof rate.variant !== 'string' || !/^[A-Za-z0-9][A-Za-z0-9_. -]{0,79}$/.test(rate.variant)))) throw invalid();
    return {provider: rate.provider, billable: rate.billable, unit: rate.unit, cost_usd: rate.cost_usd, ...(typeof rate.variant === 'string' ? {variant: rate.variant} : {})};
  });
  if ((value.status === 'available' && !rates.length) || (value.status === 'unavailable' && rates.length) || (value.message !== undefined && !label(value.message, 500))) throw invalid();
  return {model, status: value.status as StudioPricingGuide['status'], rates, source_url: value.source_url, observed_at: value.observed_at,
    ...(typeof value.message === 'string' ? {message: value.message} : {})};
}
