"""Owned Studio preferences and atomic local generation limits.

Limits count accepted local attempts, including failed and uncertain outcomes;
they are not a provider bill or a dollar budget.
"""
from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractContextManager
from typing import Annotated, ClassVar, Literal, NotRequired, Protocol, TypedDict

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator

Operation = Literal['image', 'video', 'lipsync', 'remix', 'music']
ModelId = Annotated[str, Field(strict=True, min_length=1, max_length=200)]
Limit = Annotated[int, Field(strict=True, ge=0, le=2_147_483_647)]
SQLValue = str | int | float | bytes | None


class SettingsRow(Protocol):
    def __getitem__(self, key: str | int, /) -> SQLValue: ...


class SettingsCursor(Protocol):
    def fetchone(self) -> SettingsRow | None: ...


class SettingsDatabase(Protocol):
    def execute(self, sql: str, parameters: Sequence[SQLValue] = (), /) -> SettingsCursor: ...


class SettingsModel(TypedDict):
    id: str
    kind: str
    adapter: str
    modes: list[str]
    aspect_ratios: list[str]
    resolutions: list[str]
    durations: list[int]
    duration_min: NotRequired[int]
    duration_max: NotRequired[int]
    duration_mode: NotRequired[str]


class GenerationParameters(TypedDict):
    duration: int | None


class StudioDefault(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid', strict=True)
    provider_id: str = Field(min_length=1, max_length=128)
    model: ModelId
    aspect_ratio: str | None = Field(default=None, min_length=1, max_length=32)
    resolution: str | None = Field(default=None, min_length=1, max_length=32)
    duration: int | None = Field(default=None, ge=1, le=600)


class StudioLimits(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid', strict=True)
    daily_generations: Limit | None
    concurrent_generations: Limit | None
    max_video_seconds: Limit | None
    max_music_seconds: Limit | None = None


class StudioSettingsInput(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid', strict=True)
    revision: int = Field(ge=0, le=2_147_483_647)
    defaults: dict[Operation, StudioDefault]
    enabled_models: list[ModelId] | None = Field(max_length=2000)
    limits: StudioLimits

    @field_validator('enabled_models')
    @classmethod
    def unique_models(cls, value: list[str] | None) -> list[str] | None:
        if value is not None and len(value) != len(set(value)):
            raise ValueError('Choose distinct enabled models')
        return value

    def public(self) -> dict[str, object]:
        return {'revision': self.revision,
                'defaults': {mode: values.model_dump(exclude_none=True) for mode, values in self.defaults.items()},
                'enabled_models': self.enabled_models, 'limits': self.limits.model_dump()}


def initial_settings() -> StudioSettingsInput:
    return StudioSettingsInput(revision=0, defaults={}, enabled_models=None,
        limits=StudioLimits(daily_generations=None, concurrent_generations=None, max_video_seconds=None))


def validate_settings(settings: StudioSettingsInput, models: Sequence[SettingsModel],
                      accounts: Sequence[Mapping[str, object]]) -> None:
    """Validate selection hints with public catalog metadata; never run inference."""
    by_model = {model['id']: model for model in models}
    by_account = {account['id']: account for account in accounts}
    if settings.enabled_models is not None:
        if any(identifier not in by_model for identifier in settings.enabled_models):
            raise HTTPException(400, 'Enabled models must belong to the current Studio catalog')
    for operation, default in settings.defaults.items():
        spec = by_model.get(default.model)
        kind = 'audio' if operation == 'music' else ('image' if operation == 'image' else 'video')
        if spec is None or spec['kind'] != kind or operation not in spec['modes']:
            raise HTTPException(400, f'The default model does not support {operation}')
        if settings.enabled_models is not None and default.model not in settings.enabled_models:
            raise HTTPException(400, 'A default model must be enabled')
        account = by_account.get(default.provider_id)
        if account is None or account.get('adapter') != spec['adapter']:
            raise HTTPException(400, 'Select a stored compatible API account for each default model')
        capabilities = account.get('capabilities')
        if not isinstance(capabilities, list) or kind not in capabilities:
            raise HTTPException(400, f'The default account does not declare {kind} support')
        for value, choices, label in ((default.aspect_ratio, spec['aspect_ratios'], 'aspect ratio'),
                                     (default.resolution, spec['resolutions'], 'resolution'),
                                     (default.duration, spec['durations'], 'duration')):
            if operation == 'music' and label == 'duration' and value is not None:
                if not isinstance(value, int) or not spec.get('duration_min', 3) <= value <= spec.get('duration_max', 600):
                    raise HTTPException(400, 'The default model does not support this duration')
            elif value is not None and value not in choices:
                raise HTTPException(400, f'The default model does not support this {label}')


class StudioSettingsStore:
    def __init__(self, database: Callable[[], AbstractContextManager[SettingsDatabase]]) -> None:
        self.db: Callable[[], AbstractContextManager[SettingsDatabase]] = database
        with self.db() as db:
            _ = db.execute('''CREATE TABLE IF NOT EXISTS studio_settings (
                owner TEXT PRIMARY KEY, revision INTEGER NOT NULL, settings TEXT NOT NULL)''')
            _ = db.execute('CREATE INDEX IF NOT EXISTS studio_owner_usage ON studio_jobs(owner,created,status)')

    @staticmethod
    def read_in(db: SettingsDatabase, owner: str) -> StudioSettingsInput:
        row = db.execute('SELECT revision,settings FROM studio_settings WHERE owner=?', (owner,)).fetchone()
        if row is None:
            return initial_settings()
        # Invalid persisted data fails closed rather than silently disabling limits.
        settings = StudioSettingsInput.model_validate_json(str(row['settings']))
        if settings.revision != row['revision']:
            raise RuntimeError('Studio settings revision is inconsistent')
        return settings

    def get(self, owner: str) -> StudioSettingsInput:
        with self.db() as db:
            return self.read_in(db, owner)

    def update(self, owner: str, settings: StudioSettingsInput,
               before_write: Callable[[], None] | None = None) -> StudioSettingsInput:
        with self.db() as db:
            _ = db.execute('BEGIN IMMEDIATE')
            current = self.read_in(db, owner)
            if current.revision != settings.revision:
                raise HTTPException(409, 'Studio settings changed; reload before saving')
            if before_write is not None:
                before_write()
            saved = settings.model_copy(update={'revision': current.revision + 1})
            _ = db.execute('''INSERT INTO studio_settings(owner,revision,settings) VALUES(?,?,?)
                           ON CONFLICT(owner) DO UPDATE SET revision=excluded.revision,settings=excluded.settings''',
                           (owner, saved.revision, json.dumps(saved.public())))
            return saved

    @staticmethod
    def usage_in(db: SettingsDatabase, owner: str, now: float) -> dict[str, int]:
        # Unix days are UTC days. Every reserved attempt counts for its original
        # day, even if a later refresh reports failure or an uncertain result.
        start = int(now // 86400) * 86400
        row = db.execute('''SELECT
            COUNT(CASE WHEN created>=? AND created<? THEN 1 END) AS daily,
            COUNT(CASE WHEN status IN ('submitting','queued','running','unknown') THEN 1 END) AS active
            FROM studio_jobs WHERE owner=?''', (start, start + 86400, owner)).fetchone()
        if row is None:
            raise RuntimeError('Studio usage could not be read')
        return {'daily_generations': int(str(row['daily'])), 'active_generations': int(str(row['active'])),
                'reset_at': start + 86400}

    def snapshot(self, owner: str, now: float) -> tuple[StudioSettingsInput, dict[str, int]]:
        with self.db() as db:
            _ = db.execute('BEGIN')
            return self.read_in(db, owner), self.usage_in(db, owner, now)

    @classmethod
    def enforce(cls, db: SettingsDatabase, owner: str, spec: SettingsModel, body: GenerationParameters, now: float) -> None:
        settings = cls.read_in(db, owner)
        if settings.enabled_models is not None and spec['id'] not in settings.enabled_models:
            raise HTTPException(403, 'This model is disabled in Studio settings')
        limits = settings.limits
        if spec['kind'] == 'video' and limits.max_video_seconds is not None:
            duration = body['duration']
            # Provider defaults and media-derived lengths have no proven upper
            # bound. Current adapters forward explicit supported durations.
            if duration is None or duration > limits.max_video_seconds:
                raise HTTPException(409, 'Choose an explicit video duration within the Studio seconds limit; this model may not support a bounded duration')
        if spec['kind'] == 'audio' and limits.max_music_seconds is not None:
            duration = spec.get('duration_max') if spec.get('duration_mode') == 'prompt' else body['duration']
            if duration is None or duration > limits.max_music_seconds:
                raise HTTPException(409, 'Choose a music duration within the Studio music seconds limit')
        if limits.daily_generations is None and limits.concurrent_generations is None:
            return
        usage = cls.usage_in(db, owner, now)
        if limits.daily_generations is not None and usage['daily_generations'] >= limits.daily_generations:
            raise HTTPException(429, 'Studio daily accepted generation limit reached; resets at midnight UTC')
        if limits.concurrent_generations is not None and usage['active_generations'] >= limits.concurrent_generations:
            raise HTTPException(429, 'Studio concurrent generation limit reached; unknown outcomes also count until reconciled')
