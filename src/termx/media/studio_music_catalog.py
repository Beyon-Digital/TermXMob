"""Documented music contracts; audio output alone does not imply music support."""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import cast

OPENROUTER_MUSIC_MODELS: list[dict[str, object]] = [
    {'id': 'google/lyria-3-pro-preview', 'label': 'Lyria 3 Pro', 'kind': 'audio',
     'adapter': 'openrouter', 'description': 'Full songs, vocals and instrumentals. Duration and lyrics are directed through the prompt.',
     'modes': ['music'], 'aspect_ratios': [], 'resolutions': [], 'durations': [30, 60, 90, 120, 180],
     'duration_min': 3, 'duration_max': 184, 'custom_duration_max': 184, 'duration_mode': 'prompt',
     'supported_music_modes': ['simple', 'custom'], 'supports_custom_lyrics': True,
     'supports_instrumental': True, 'supports_negative_styles': True, 'lyrics_constraints': 'prompt',
     'prompt_max_length': 4100, 'lyrics_max_length': 12000,
     'documentation': ['https://openrouter.ai/google/lyria-3-pro-preview',
        'https://openrouter.ai/docs/guides/overview/multimodal/audio',
        'https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/lyria/lyria-3']},
    {'id': 'google/lyria-3-clip-preview', 'label': 'Lyria 3 Clip', 'kind': 'audio',
     'adapter': 'openrouter', 'description': '30-second music clips with vocals or instruments.',
     'modes': ['music'], 'aspect_ratios': [], 'resolutions': [], 'durations': [30],
     'duration_min': 30, 'duration_max': 30, 'custom_duration_max': 30, 'duration_mode': 'fixed',
     'supported_music_modes': ['simple', 'custom'], 'supports_custom_lyrics': True,
     'supports_instrumental': True, 'supports_negative_styles': True, 'lyrics_constraints': 'prompt',
     'prompt_max_length': 4100, 'lyrics_max_length': 12000,
     'documentation': ['https://openrouter.ai/google/lyria-3-clip-preview',
        'https://openrouter.ai/docs/guides/overview/multimodal/audio']},
]


def openrouter_music_models(inventory: object) -> list[dict[str, object]]:
    """Intersect verified operation contracts with current text-to-audio metadata."""
    if not isinstance(inventory, list) or len(inventory) > 1000:
        raise ValueError('Invalid music model inventory')
    contracts = {model['id']: model for model in OPENROUTER_MUSIC_MODELS}
    result = []
    seen = set()
    for row in inventory:
        if not isinstance(row, Mapping) or row.get('id') not in contracts:
            continue
        identifier = row['id']
        if identifier in seen:
            raise ValueError('Duplicate music model inventory entry')
        seen.add(identifier)
        architecture = row.get('architecture')
        if not isinstance(architecture, Mapping):
            continue
        inputs, outputs = architecture.get('input_modalities'), architecture.get('output_modalities')
        if not isinstance(inputs, list) or not isinstance(outputs, list) or 'text' not in inputs or 'audio' not in outputs:
            continue
        endpoints = row.get('endpoints')
        if endpoints is not None and (not isinstance(endpoints, list) or '/api/v1/chat/completions' not in endpoints):
            continue
        model = deepcopy(contracts[identifier])
        model['capability_evidence'] = [
            'https://openrouter.ai/api/v1/models?output_modalities=audio', *cast(list[str], model['documentation'])]
        result.append(model)
    return result
