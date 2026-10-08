"""Explicit administrator pricing; provider usage produces estimates, never bills."""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field


Rate = Annotated[Decimal, Field(ge=0, le=1_000_000, max_digits=15, decimal_places=8)]


class PricingSnapshot(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False, str_strip_whitespace=True, frozen=True)
    currency: str = Field(pattern=r'^[A-Z]{3}$')
    input_per_million: Rate
    cached_input_per_million: Rate
    output_per_million: Rate
    source: str = Field(min_length=1, max_length=500)
    effective_date: date
    version: str = Field(min_length=1, max_length=100)


def estimate_cost(usage: dict, pricing: PricingSnapshot | None, *, provider_id: str, model: str):
    report = {'reported': False, 'kind': 'estimate', 'billed_charge': False,
              'provider_id': provider_id, 'model': model,
              'basis': 'Administrator pricing snapshot multiplied by provider-reported token usage.'}
    if pricing is None:
        return {**report, 'reason': 'No administrator pricing snapshot supplied.'}
    report['pricing'] = pricing.model_dump(mode='json')
    report['currency'] = pricing.currency
    if pricing.effective_date > date.today():
        return {**report, 'reason': 'Pricing snapshot is not yet effective.'}
    required = ('requests', 'input_tokens', 'output_tokens', 'missing_usage_requests')
    if any(not isinstance(usage.get(key), int) or isinstance(usage[key], bool) or usage[key] < 0 for key in required) or usage['requests'] == 0:
        return {**report, 'reason': 'No complete provider-reported usage is available.'}
    if usage['missing_usage_requests']:
        return {**report, 'reason': 'Some requests have missing or invalid provider usage; total cost cannot be estimated.'}
    input_tokens, output_tokens = usage['input_tokens'], usage['output_tokens']
    cached = usage.get('cached_input_tokens')
    cached_complete = (isinstance(cached, int) and not isinstance(cached, bool) and 0 <= cached <= input_tokens
                       and usage.get('missing_cached_usage_requests') == 0)
    def amount(input_rate, cached_tokens=0):
        return (Decimal(input_tokens - cached_tokens) * input_rate
                + Decimal(cached_tokens) * pricing.cached_input_per_million
                + Decimal(output_tokens) * pricing.output_per_million) / Decimal(1_000_000)
    if not cached_complete and pricing.input_per_million != pricing.cached_input_per_million and input_tokens:
        low, high = sorted((amount(pricing.input_per_million), amount(pricing.cached_input_per_million)))
        return {**report, 'reason': 'Cached-input usage is missing; only an estimated cost range is available.',
                'bounds': {'minimum': format(low, 'f'), 'maximum': format(high, 'f')}}
    total = amount(pricing.input_per_million, cached if cached_complete else 0)
    return {**report, 'reported': True, 'amount': format(total, 'f'),
            'input_tokens': input_tokens, 'cached_input_tokens': cached if cached_complete else None,
            'output_tokens': output_tokens,
            'reason': 'Estimated from actual token usage and the supplied snapshot; taxes, fees, discounts and billed charges are not known.'}
