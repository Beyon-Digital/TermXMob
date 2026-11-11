"""Durable, explicit-account visual studio; external effects are never replayed."""
from __future__ import annotations

import asyncio
import base64
import io
import hashlib
import ipaddress
import json
import re
import sqlite3
import uuid
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path
from time import time
from urllib.parse import urlsplit

import httpx
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator


from termx.media.studio_openrouter import OpenRouterAdapter, OpenRouterError
from termx.media.studio_music import MUSIC_MODELS, ElevenLabsMusicAdapter, MusicArtifacts, MusicInput
from termx.media.studio_music_catalog import OPENROUTER_MUSIC_MODELS
from termx.media.studio_music_openrouter import OpenRouterMusicAdapter

from collections.abc import Callable, Coroutine, Generator, Iterator, Mapping, Sequence
from typing import ClassVar, Literal, NotRequired, Protocol, TypedDict, cast

from termx.media.service import ArtifactService
from termx.agent.provider_limits import clean_provider_limits
from termx.media.studio_settings import StudioSettingsInput, StudioSettingsStore, validate_settings

AdapterName = Literal['openrouter', 'kie', 'elevenlabs']
SQLValue = str | int | float | bytes | None


class DatabaseRow(Protocol):
    def __getitem__(self, key: str | int, /) -> SQLValue: ...
    def keys(self) -> list[str]: ...


class DatabaseCursor(Protocol):
    def fetchone(self) -> DatabaseRow | None: ...
    def fetchall(self) -> list[DatabaseRow]: ...
    def __iter__(self) -> Iterator[DatabaseRow]: ...


class Database(Protocol):
    def execute(self, sql: str, parameters: Sequence[SQLValue] = (), /) -> DatabaseCursor: ...
    def executescript(self, sql: str, /) -> DatabaseCursor: ...


class ProviderStore(Protocol):
    def get_provider(self, provider_id: str) -> object: ...
    def list_providers(self) -> Sequence[object]: ...


class Credentials(Protocol):
    def get(self, provider_id: str) -> str | None: ...


class StudioState(Protocol):
    @property
    def agent_store(self) -> ProviderStore: ...
    @property
    def credentials(self) -> Credentials: ...


class ArtifactPort(MusicArtifacts, Protocol):
    def get(self, owner: str, identifier: str) -> Mapping[str, object]: ...
    def db(self) -> 'AbstractContextManager[Database]': ...


class ReferenceLimits(TypedDict):
    min: int
    max: int
    app_max: int


class Snapshot(TypedDict):
    source: str
    documentation: list[str]
    openrouter: list['ModelSpec']
    kie: list['ModelSpec']


class ModelSpec(TypedDict):
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
    label: NotRequired[str]
    description: NotRequired[str]
    requires_image: NotRequired[bool]
    requires_audio: NotRequired[bool]
    requires_video: NotRequired[bool]
    _supports_image: NotRequired[bool]
    _supports_audio: NotRequired[bool]
    _supports_video: NotRequired[bool]
    _supports_first_frame: NotRequired[bool]
    _supports_reference_image: NotRequired[bool]
    _supports_negative_prompt: NotRequired[bool]
    _requires_video_for_remix: NotRequired[bool]
    _inline_inputs: NotRequired[list[str]]
    image_reference_limits: NotRequired[ReferenceLimits]


class ImageReferenceData(TypedDict, total=False):
    image_id: str
    image_url: str


class LocalImage(TypedDict):
    data_url: str
    mime: str


class PublicImage(TypedDict):
    url: str


class StudioRequest(TypedDict):
    request_id: str
    project_id: str | None
    provider_id: str
    model: str
    prompt: str
    aspect_ratio: str | None
    resolution: str | None
    duration: int | None
    operation: str | None
    image_role: str | None
    negative_prompt: str | None
    image_id: str | None
    audio_id: str | None
    video_id: str | None
    image_url: str | None
    audio_url: str | None
    video_url: str | None
    image_references: list[ImageReferenceData] | None
    acknowledge_billing: bool
    _local_references: NotRequired[dict[str, LocalImage]]
    _local_image_references: NotRequired[list[LocalImage | PublicImage]]


class JobRecord(TypedDict):
    id: str
    owner: str
    project: str | None
    request_id: str
    digest: str
    provider_id: str
    adapter: str
    model: str
    kind: str
    prompt: str
    status: str
    task_id: str | None
    created: float
    updated: float
    outputs: str
    error: str | None
    parameters: str


class StudioJob(TypedDict):
    id: str
    project_id: str | None
    provider_id: str
    model: str
    kind: str
    prompt: str
    status: str
    created: float
    updated: float
    outputs: list[str]
    parameters: dict[str, object]
    error: NotRequired[str]
    request_id: NotRequired[str]


class HistoryPage(TypedDict):
    items: list[StudioJob]
    next_cursor: str | None


class JobResult(TypedDict):
    status: str
    task_id: NotRequired[str]
    outputs: NotRequired[list[str]]
    error: NotRequired[str]


class StudioAdapter(Protocol):
    async def discover(self) -> Sequence[object]: ...
    async def discover_music(self) -> Sequence[object]: ...
    async def submit(self, spec: ModelSpec, body: StudioRequest, key: str,
                     owner: str, project: str | None, *, before_submit: Callable[[], None] | None = None) -> Mapping[str, object]: ...
    async def refresh(self, task_id: str, key: str, owner: str,
                      project: str | None) -> Mapping[str, object]: ...


class SubmissionBlocked(Exception):
    """A live authorization guard stopped a provider effect before it began."""


def effect_guard(authorize: Callable[[], None] | None) -> Callable[[], None] | None:
    if authorize is None:
        return None

    def check() -> None:
        try:
            authorize()
        except HTTPException:
            # Authorization details and managed-session credentials never
            # become a job error, provider payload or persisted callback.
            raise SubmissionBlocked() from None
    return check


def decode_json(raw: str | bytes | bytearray) -> object:
    # JSON is an untyped external boundary. Its shape is narrowed by callers.
    return cast(object, json.loads(raw))


def object_map(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError('Expected an object')
    entries = cast(dict[object, object], value)
    result: dict[str, object] = {}
    for key, item in entries.items():
        if not isinstance(key, str):
            raise ValueError('Expected string object keys')
        result[key] = item
    return result


def object_list(value: object) -> list[object]:
    if not isinstance(value, list):
        raise ValueError('Expected a list')
    return cast(list[object], value)


def string_list(value: object) -> list[str]:
    result: list[str] = []
    for item in object_list(value):
        if not isinstance(item, str):
            raise ValueError('Expected a string list')
        result.append(item)
    return result


def text(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError('Expected text')
    return value


def nullable_text(value: object) -> str | None:
    return None if value is None else text(value)


def integer(value: object) -> int:
    if type(value) is not int:
        raise ValueError('Expected an integer')
    return value


def number(value: object) -> float:
    if type(value) not in (int, float):
        raise ValueError('Expected a number')
    return float(cast(int | float, value))


def model_spec(value: object) -> ModelSpec:
    data = object_map(value)
    _ = text(data['id'])
    if data['kind'] not in ('image', 'video', 'audio') or data['adapter'] not in ('openrouter', 'kie', 'elevenlabs'):
        raise ValueError('Unsupported model adapter or output kind')
    if data['adapter'] == 'elevenlabs' and data['kind'] != 'audio' or data['kind'] == 'audio' and (data['adapter'] not in ('openrouter', 'elevenlabs') or data.get('modes') != ['music']):
        raise ValueError('Unsupported model adapter and output kind combination')
    for key in ('modes', 'aspect_ratios', 'resolutions'):
        _ = string_list(data[key])
    for duration in object_list(data['durations']):
        if not 1 <= integer(duration) <= 600:
            raise ValueError('Invalid model duration')
    for key in ('requires_image', 'requires_audio', 'requires_video', '_supports_image', '_supports_audio',
                '_supports_video', '_supports_first_frame', '_supports_reference_image', '_supports_negative_prompt',
                '_requires_video_for_remix'):
        if key in data and type(data[key]) is not bool:
            raise ValueError('Invalid model capability flag')
    if '_inline_inputs' in data:
        _ = string_list(data['_inline_inputs'])
    if 'image_reference_limits' in data:
        limits = object_map(data['image_reference_limits'])
        minimum, maximum, app_max = (integer(limits[key]) for key in ('min', 'max', 'app_max'))
        if not 0 <= minimum <= maximum or not 0 <= app_max <= min(5, maximum):
            raise ValueError('Invalid reference limits')
    # The required model fields above are validated; retain additional adapter
    # proof metadata unchanged for its own typed/validated boundary.
    return cast(ModelSpec, cast(object, data))


def job_record(row: DatabaseRow) -> JobRecord:
    return {'id': text(row['id']), 'owner': text(row['owner']), 'project': nullable_text(row['project']),
            'request_id': text(row['request_id']), 'digest': text(row['digest']), 'provider_id': text(row['provider_id']),
            'adapter': text(row['adapter']), 'model': text(row['model']), 'kind': text(row['kind']),
            'prompt': text(row['prompt']), 'status': text(row['status']), 'task_id': nullable_text(row['task_id']),
            'created': number(row['created']), 'updated': number(row['updated']), 'outputs': text(row['outputs']),
            'error': nullable_text(row['error']), 'parameters': text(row['parameters']) if 'parameters' in row.keys() else '{}'}


def job_result(data: Mapping[str, object]) -> JobResult:
    status = text(data['status'])
    if status not in STATUSES:
        raise ValueError('Invalid provider job state')
    result: JobResult = {'status': status}
    if 'task_id' in data:
        identifier = text(data['task_id'])
        if not REMOTE_ID.fullmatch(identifier):
            raise ValueError('Invalid provider receipt')
        result['task_id'] = identifier
    if 'outputs' in data:
        result['outputs'] = string_list(data['outputs'])
    if 'error' in data:
        result['error'] = text(data['error'])
    return result


def load_snapshot() -> Snapshot:
    source = object_map(decode_json(Path(__file__).with_name('studio_models.json').read_text()))
    return {'source': text(source['source']), 'documentation': string_list(source['documentation']),
            'openrouter': [model_spec(item) for item in object_list(source['openrouter'])],
            'kie': [model_spec(item) for item in object_list(source['kie'])]}


def history_search(value: object) -> str:
    if value is None:
        return ''
    if not isinstance(value, str) or len(value) > 200 or any(ord(c) < 32 for c in value):
        raise HTTPException(400, 'History search must contain at most 200 printable characters')
    return value.strip()


def reference_inputs(body: StudioRequest) -> dict[str, tuple[str | None, str | None]]:
    return {'image': (body['image_url'], body['image_id']), 'audio': (body['audio_url'], body['audio_id']),
            'video': (body['video_url'], body['video_id'])}


KIE_BASE = 'https://api.kie.ai/api/v1/jobs'
SNAPSHOT = load_snapshot()
REMOTE_ID = re.compile(r'^[A-Za-z0-9_-]{1,256}$')
TERMINAL = {'completed', 'failed'}
STATUSES = {'submitting', 'queued', 'running', 'completed', 'failed', 'unknown'}
HISTORY_GROUPS = {'active': ('submitting', 'queued', 'running'), 'attention': ('failed', 'unknown')}
PARAMETERS = {'operation', 'image_role', 'negative_prompt', 'aspect_ratio', 'resolution', 'duration',
              'image_url', 'audio_url', 'video_url', 'image_id', 'audio_id', 'video_id', 'image_references',
              'music_mode', 'lyrics', 'styles', 'negative_styles', 'title', 'instrumental'}
NEW_FIELDS = {'operation', 'image_role', 'negative_prompt', 'image_id', 'audio_id', 'video_id', 'image_references'}
INLINE_IMAGE_LIMIT = 8 * 1024 * 1024
INLINE_TOTAL_LIMIT = 20 * 1024 * 1024


class StudioImageReference(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid')
    image_id: str | None = Field(default=None, min_length=1, max_length=128, pattern=r'^[A-Za-z0-9_-]+$')
    image_url: str | None = Field(default=None, min_length=1, max_length=4096)

    @model_validator(mode='after')
    def one_source(self) -> 'StudioImageReference':
        if (self.image_id is None) == (self.image_url is None):
            raise ValueError('Each image reference requires exactly one owned image or public URL')
        return self

    def reference_data(self) -> ImageReferenceData:
        return {'image_id': self.image_id} if self.image_id is not None else {'image_url': text(self.image_url)}


class StudioInput(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid')
    request_id: str = Field(min_length=8, max_length=128, pattern=r'^[A-Za-z0-9_-]+$')
    project_id: str | None = Field(default=None, min_length=1, max_length=128)
    provider_id: str = Field(min_length=1, max_length=128)
    model: str = Field(min_length=1, max_length=200)
    prompt: str = Field(default='', max_length=20000)
    aspect_ratio: str | None = Field(default=None, max_length=32)
    resolution: str | None = Field(default=None, max_length=32)
    duration: int | None = Field(default=None, ge=1, le=600, strict=True)
    operation: Literal['image', 'video', 'lipsync', 'remix'] | None = None
    image_role: Literal['first_frame', 'reference'] | None = None
    negative_prompt: str | None = Field(default=None, max_length=2000)
    image_id: str | None = Field(default=None, min_length=1, max_length=128, pattern=r'^[A-Za-z0-9_-]+$')
    audio_id: str | None = Field(default=None, min_length=1, max_length=128, pattern=r'^[A-Za-z0-9_-]+$')
    video_id: str | None = Field(default=None, min_length=1, max_length=128, pattern=r'^[A-Za-z0-9_-]+$')
    image_url: str | None = Field(default=None, min_length=1, max_length=4096)
    audio_url: str | None = Field(default=None, min_length=1, max_length=4096)
    video_url: str | None = Field(default=None, min_length=1, max_length=4096)
    image_references: list[StudioImageReference] | None = Field(default=None, min_length=1, max_length=5)
    acknowledge_billing: StrictBool = False

    @model_validator(mode='after')
    def exclusive_references(self) -> 'StudioInput':
        for kind, identifier, url in (('image', self.image_id, self.image_url), ('audio', self.audio_id, self.audio_url),
                                      ('video', self.video_id, self.video_url)):
            if identifier is not None and url is not None:
                raise ValueError(f'Choose an owned {kind} or a public URL, not both')
        if self.image_references is not None and (self.image_id is not None or self.image_url is not None):
            raise ValueError('Choose image references or a single image, not both')
        return self

    def request_data(self) -> StudioRequest:
        return {'request_id': self.request_id, 'project_id': self.project_id, 'provider_id': self.provider_id,
                'model': self.model, 'prompt': self.prompt, 'aspect_ratio': self.aspect_ratio, 'resolution': self.resolution,
                'duration': self.duration, 'operation': self.operation, 'image_role': self.image_role,
                'negative_prompt': self.negative_prompt, 'image_id': self.image_id, 'audio_id': self.audio_id,
                'video_id': self.video_id, 'image_url': self.image_url, 'audio_url': self.audio_url,
                'video_url': self.video_url, 'acknowledge_billing': self.acknowledge_billing,
                'image_references': [item.reference_data() for item in self.image_references] if self.image_references is not None else None}


def account_adapter(provider: object) -> AdapterName | None:
    """Only documented adapters on exact HTTPS origins may receive host secrets."""
    try:
        account = object_map(provider)
        if account.get('kind') != 'openai-compatible':
            return None
        url = urlsplit(text(account.get('base_url', '')))
        if url.scheme != 'https' or url.username or url.password or url.query or url.fragment or url.port not in (None, 443):
            return None
        path = url.path.rstrip('/')
        if url.hostname == 'openrouter.ai' and path in ('', '/v1', '/api/v1'):
            return 'openrouter'
        if url.hostname == 'api.kie.ai' and path in ('', '/v1', '/api/v1'):
            return 'kie'
        if url.hostname == 'api.elevenlabs.io' and path in ('', '/v1'):
            return 'elevenlabs'
    except (ValueError, TypeError):
        pass
    return None


def public_https_url(value: object) -> bool:
    """Validate links without fetching them or resolving provider-supplied hosts."""
    if not isinstance(value, str) or len(value) > 4096 or any(ord(c) < 33 for c in value) or '\\' in value:
        return False
    try:
        url = urlsplit(value)
        host = (url.hostname or '').lower()
        if url.scheme != 'https' or url.username or url.password or url.fragment or url.port not in (None, 443):
            return False
        try:
            return ipaddress.ip_address(host).is_global
        except ValueError:
            pass
        # Local names, non-canonical numeric IPs and credential-bearing URLs are
        # not public reference links. No DNS lookup/server fetch is performed.
        return (len(host) <= 253 and '.' in host and not host.endswith(('.local', '.localhost', '.internal', '.test', '.invalid'))
                and not re.fullmatch(r'[0-9.]+', host) and all(re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label) for label in host.split('.')))
    except (ValueError, TypeError):
        return False


class StudioService:
    def __init__(self, state: object, directory: str | Path, artifacts: ArtifactService,
                 client: httpx.AsyncClient | None = None) -> None:
        # Legacy host/store adapters are structurally checked at these small
        # boundaries; JSON and SQLite payloads are validated separately below.
        self.state: StudioState = cast(StudioState, state)
        self.artifacts: ArtifactPort = cast(ArtifactPort, artifacts)
        self.client: httpx.AsyncClient | None = client
        self.openrouter: StudioAdapter = cast(StudioAdapter, cast(object, OpenRouterAdapter(artifacts, client=client)))
        self.tasks: set[asyncio.Task[None]] = set()
        self.refreshing: set[str] = set()
        self._catalog: list[ModelSpec] = []
        self._music_catalog: list[ModelSpec] = [model_spec(item) for item in OPENROUTER_MUSIC_MODELS]
        self._music_catalog_at: float = 0
        self._music_catalog_live: bool = False
        self._music_catalog_lock: asyncio.Lock = asyncio.Lock()
        self._catalog_at: float = 0
        self._catalog_live: bool = False
        self._catalog_lock: asyncio.Lock = asyncio.Lock()
        root = Path(directory)
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path: Path = root / 'studio.sqlite3'
        with self.db() as db:
            _ = db.executescript('''CREATE TABLE IF NOT EXISTS studio_jobs (
                id TEXT PRIMARY KEY, owner TEXT NOT NULL, project TEXT,
                request_id TEXT NOT NULL, digest TEXT NOT NULL, provider_id TEXT NOT NULL,
                adapter TEXT NOT NULL, model TEXT NOT NULL, kind TEXT NOT NULL,
                prompt TEXT NOT NULL, status TEXT NOT NULL, task_id TEXT,
                created REAL NOT NULL, updated REAL NOT NULL,
                outputs TEXT NOT NULL DEFAULT '[]', error TEXT, parameters TEXT NOT NULL DEFAULT '{}',
                UNIQUE(owner,request_id));
                CREATE INDEX IF NOT EXISTS studio_owner_project ON studio_jobs(owner,project,created);''')
            columns = {row['name'] for row in db.execute('PRAGMA table_info(studio_jobs)')}
            if 'parameters' not in columns:
                _ = db.execute("ALTER TABLE studio_jobs ADD COLUMN parameters TEXT NOT NULL DEFAULT '{}'")
            _ = db.execute('CREATE INDEX IF NOT EXISTS studio_history_order ON studio_jobs(owner,project,created DESC,id DESC)')
            _ = db.execute("UPDATE studio_jobs SET status='unknown',error='Host stopped before a provider receipt was saved. This request will not be resubmitted.',updated=? WHERE status='submitting'", (time(),))
        self.path.chmod(0o600)
        self.settings_store: StudioSettingsStore = StudioSettingsStore(self.db)

    @contextmanager
    def db(self) -> Generator[Database]:
        connection = sqlite3.connect(self.path, timeout=15)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield cast(Database, connection)
        finally:
            connection.close()

    def account(self, provider_id: str, kind: str, adapter: str) -> str:
        provider = self.state.agent_store.get_provider(provider_id)
        if account_adapter(provider) != adapter:
            raise HTTPException(400, 'Select a stored compatible API account for this model provider')
        account = object_map(provider)
        if kind not in string_list(account.get('capabilities', [])):
            raise HTTPException(409, f'The selected account does not declare {kind} support')
        key = self.state.credentials.get(text(account['id']))
        if not key:
            raise HTTPException(409, 'Connect this API account on the host in Models & engines')
        return key

    async def _load_catalog(self) -> None:
        # Public, nonbillable discovery is bounded and cached. No account secrets
        # are needed and no provider key is returned to the browser.
        if time() - self._catalog_at > 300:
            async with self._catalog_lock:
                if time() - self._catalog_at > 300:
                    try:
                        self._catalog = [model_spec(item) for item in await self.openrouter.discover()]
                        if not self._catalog:
                            raise OpenRouterError('Model discovery was empty', uncertain=True)
                        self._catalog_live = True
                    except (OpenRouterError, httpx.RequestError, ValueError, TypeError):
                        self._catalog = SNAPSHOT['openrouter']
                        self._catalog_live = False
                    self._catalog_at = time()

    async def _load_music_catalog(self) -> None:
        if time() - self._music_catalog_at <= 300:
            return
        async with self._music_catalog_lock:
            if time() - self._music_catalog_at <= 300:
                return
            try:
                self._music_catalog = [model_spec(item) for item in await self.openrouter.discover_music()]
                self._music_catalog_live = True
            except (OpenRouterError, httpx.RequestError, ValueError, TypeError):
                self._music_catalog = [model_spec(item) for item in OPENROUTER_MUSIC_MODELS]
                self._music_catalog_live = False
            self._music_catalog_at = time()

    def available_models(self) -> list[ModelSpec]:
        return self._catalog + self._music_catalog + SNAPSHOT['kie'] + [model_spec(item) for item in MUSIC_MODELS]

    async def catalog(self, owner: str | None = None) -> dict[str, object]:
        await asyncio.gather(self._load_catalog(), self._load_music_catalog())
        accounts: list[dict[str, object]] = []
        for raw_provider in self.state.agent_store.list_providers():
            adapter = account_adapter(raw_provider)
            if adapter is None:
                continue
            provider = object_map(raw_provider)
            identifier = text(provider['id'])
            capabilities = [v for v in string_list(provider.get('capabilities', [])) if v in {'image', 'video', 'audio'}]
            if capabilities:
                accounts.append({'id': identifier, 'name': provider.get('name') or identifier,
                    'configured': bool(self.state.credentials.get(identifier)), 'adapter': adapter, 'capabilities': capabilities})
        models = self.available_models()
        result: dict[str, object] = {'models': [{**{k: v for k, v in model.items() if not k.startswith('_')}, **self.public_capabilities(model)} for model in models],
                  'accounts': accounts, 'catalog_source': 'live' if self._catalog_live else 'snapshot',
                  'music_catalog_source': 'live' if self._music_catalog_live else 'snapshot'}
        if not self._catalog_live or not self._music_catalog_live:
            result['catalog_error'] = 'Some live OpenRouter capabilities are unavailable. Saved model capabilities are shown for the affected operations.'
        return self.catalog_for_owner(result, owner) if owner is not None else result

    def catalog_for_owner(self, catalog: dict[str, object], owner: str) -> dict[str, object]:
        settings = self.settings_store.get(owner)
        models = [object_map(model) for model in object_list(catalog['models'])]
        if settings.enabled_models is not None:
            models = [model for model in models if model['id'] in settings.enabled_models]
        return {**catalog, 'models': models, 'settings': settings.public()}

    async def settings(self, owner: str, *, before_read: Callable[[], None] | None = None) -> dict[str, object]:
        catalog = await self.catalog()
        if before_read is not None:
            before_read()
        settings, usage = self.settings_store.snapshot(owner, time())
        return {'settings': settings.public(), 'usage': usage, 'catalog': catalog}

    async def update_settings(self, owner: str, values: object, *,
                              before_write: Callable[[], None] | None = None) -> dict[str, object]:
        settings = StudioSettingsInput.model_validate(values)
        catalog = await self.catalog()
        if before_write is not None:
            before_write()
        accounts = [object_map(account) for account in object_list(catalog['accounts'])]
        validate_settings(settings, self.available_models(), accounts)
        saved = self.settings_store.update(owner, settings, before_write)
        _, usage = self.settings_store.snapshot(owner, time())
        return {'settings': saved.public(), 'usage': usage, 'catalog': catalog}

    @staticmethod
    def public_capabilities(model: Mapping[str, object]) -> dict[str, object]:
        result: dict[str, object] = {flag: True for flag in ('supports_image', 'supports_audio', 'supports_video', 'supports_first_frame', 'supports_reference_image', 'supports_negative_prompt', 'requires_video_for_remix') if model.get('_' + flag)}
        result['inline_inputs'] = [kind for kind in string_list(model.get('_inline_inputs', [])) if kind in {'image', 'audio', 'video'}]
        return result

    @staticmethod
    def parameters(body: object) -> dict[str, object]:
        """Reusable settings, never credentials, idempotency keys or inline data."""
        result: dict[str, object] = {}
        try:
            settings = object_map(body)
        except ValueError:
            return result
        for key in PARAMETERS:
            value = settings.get(key)
            if value is None:
                continue
            if key == 'image_references':
                try:
                    references = object_list(value)
                except ValueError:
                    continue
                if not 1 <= len(references) <= 5:
                    result['has_image_reference'] = True
                    continue
                safe_references: list[dict[str, object]] = []
                for entry in references:
                    try:
                        reference = object_map(entry)
                    except ValueError:
                        reference = {}
                    identifier, url = reference.get('image_id'), reference.get('image_url')
                    if isinstance(identifier, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,128}', identifier):
                        safe_references.append({'image_id': identifier})
                    elif isinstance(url, str) and public_https_url(url) and not urlsplit(url).query:
                        safe_references.append({'image_url': url})
                    else:
                        # A placeholder retains its position and requires
                        # explicit re-entry rather than composing fewer inputs.
                        safe_references.append({'has_image_reference': True})
                result['image_references'] = safe_references
                result['has_image_reference'] = True
            elif key.endswith('_url'):
                if isinstance(value, str) and public_https_url(value) and not urlsplit(value).query:
                    result[key] = value
                result['has_' + key[:-4] + '_reference'] = True
            elif key.endswith('_id'):
                if isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,128}', value):
                    result[key] = value
                    result['has_' + key[:-3] + '_reference'] = True
            elif key == 'instrumental':
                if type(value) is bool:
                    result[key] = value
            elif key in {'lyrics', 'styles', 'negative_styles', 'title'}:
                maximum = {'lyrics': 12000, 'styles': 1000, 'negative_styles': 1000, 'title': 200}[key]
                if isinstance(value, str) and len(value) <= maximum:
                    result[key] = value
            elif key == 'duration':
                if type(value) is int and 1 <= value <= 600:
                    result[key] = value
            elif isinstance(value, str) and len(value) <= (2000 if key == 'negative_prompt' else 32):
                result[key] = value
        for kind in ('image', 'audio', 'video'):
            if settings.get('has_' + kind + '_reference') is True:
                result['has_' + kind + '_reference'] = True
        return result

    @staticmethod
    def public(row: JobRecord) -> StudioJob:
        result: StudioJob = {'id': row['id'], 'project_id': row['project'], 'provider_id': row['provider_id'],
            'model': row['model'], 'kind': row['kind'], 'prompt': row['prompt'], 'status': row['status'],
            'created': row['created'], 'updated': row['updated'], 'outputs': string_list(decode_json(row['outputs'])),
            'parameters': {}}
        try:
            settings = decode_json(row['parameters'])
        except (ValueError, TypeError):
            settings = None
        result['parameters'] = StudioService.parameters(settings)
        if row['kind'] == 'audio':
            result['request_id'] = row['request_id']
        if row['error']:
            result['error'] = row['error']
        return result

    def _row(self, owner: str, identifier: str) -> JobRecord:
        with self.db() as db:
            row = db.execute('SELECT * FROM studio_jobs WHERE owner=? AND id=?', (owner, identifier)).fetchone()
        if not row:
            raise HTTPException(404, 'Studio job not found')
        return job_record(row)

    def get(self, owner: str, identifier: str) -> StudioJob:
        return self.public(self._row(owner, identifier))

    def list(self, owner: str, project: str | None) -> list[StudioJob]:
        with self.db() as db:
            rows = db.execute('SELECT * FROM studio_jobs WHERE owner=? AND project IS ? ORDER BY created DESC,id DESC LIMIT 100', (owner, project)).fetchall()
        return [self.public(job_record(row)) for row in rows]

    def history(self, owner: str, project: str | None, *, limit: int = 40, cursor: str | None = None,
                kind: str | None = None, status: str | None = None, q: str | None = None) -> HistoryPage:
        """Owned project history with an immutable ordering and insertion fence.

        The cursor is scoped, not an authorization token. Its anchor is loaded
        again through owner/project authority and timestamps come from the DB.
        Status updates do not invalidate an otherwise owned ordering anchor.
        """
        if type(limit) is not int or not 1 <= limit <= 100:
            raise HTTPException(400, 'History limit must be between 1 and 100')
        if kind is not None and kind not in {'image', 'video', 'audio'}:
            raise HTTPException(400, 'Unsupported history media kind')
        if status is not None and status not in STATUSES | HISTORY_GROUPS.keys():
            raise HTTPException(400, 'Unsupported history job status')
        search = history_search(q)
        scope = hashlib.sha256(json.dumps([owner, project, kind, status, search], separators=(',', ':')).encode()).hexdigest()
        after = None
        anchor_identifier = None
        with self.db() as db:
            maximum_row = db.execute('SELECT COALESCE(MAX(rowid),0) FROM studio_jobs WHERE owner=? AND project IS ?', (owner, project)).fetchone()
            maximum = integer(maximum_row[0]) if maximum_row is not None else 0
            snapshot = maximum
            if cursor is not None:
                try:
                    if not re.fullmatch(r'[A-Za-z0-9_-]{1,1024}', cursor):
                        raise ValueError()
                    decoded = object_map(decode_json(base64.b64decode(cursor + '=' * (-len(cursor) % 4), altchars=b'-_', validate=True)))
                    if set(decoded) != {'v', 's', 'a', 'w'} or type(decoded['v']) is not int or decoded['v'] != 1 or decoded['s'] != scope:
                        raise ValueError()
                    candidate = decoded['a']
                    if not isinstance(candidate, str) or not REMOTE_ID.fullmatch(candidate):
                        raise ValueError()
                    snapshot = integer(decoded['w'])
                    if type(snapshot) is not int or not 1 <= snapshot <= maximum:
                        raise ValueError()
                    anchor_identifier = candidate
                    after = db.execute('SELECT created,rowid AS sequence FROM studio_jobs WHERE owner=? AND project IS ? AND id=?', (owner, project, anchor_identifier)).fetchone()
                    if not after or integer(after['sequence']) > snapshot:
                        raise ValueError()
                except (ValueError, TypeError, KeyError, UnicodeError, RecursionError):
                    raise HTTPException(400, 'History cursor is invalid or belongs to a different history view') from None
            clauses = ['owner=?', 'project IS ?', 'rowid<=?']
            arguments: list[SQLValue] = [owner, project, snapshot]
            if kind is not None:
                clauses.append('kind=?'); arguments.append(kind)
            if status is not None:
                if status in HISTORY_GROUPS:
                    grouped = HISTORY_GROUPS[status]
                    clauses.append('status IN (' + ','.join('?' for _ in grouped) + ')')
                    arguments.extend(grouped)
                else:
                    clauses.append('status=?'); arguments.append(status)
            if search:
                # instr gives literal substring search: %, _ and quotes have no
                # wildcard or SQL meaning, including in pages older than 100.
                clauses.append('''(instr(lower(prompt),lower(?))>0 OR instr(lower(model),lower(?))>0 OR
                    (kind='audio' AND json_valid(parameters) AND
                    (instr(lower(COALESCE(json_extract(parameters,'$.title'),'')),lower(?))>0 OR
                     instr(lower(COALESCE(json_extract(parameters,'$.lyrics'),'')),lower(?))>0)))''')
                arguments.extend([search, search, search, search])
            if after:
                clauses.append('(created<? OR (created=? AND id<?))')
                arguments.extend([after['created'], after['created'], anchor_identifier])
            rows = db.execute('SELECT * FROM studio_jobs WHERE ' + ' AND '.join(clauses) + ' ORDER BY created DESC,id DESC LIMIT ?', (*arguments, limit + 1)).fetchall()
        items = rows[:limit]
        next_cursor = None
        if len(rows) > limit:
            payload = {'v': 1, 's': scope, 'a': items[-1]['id'], 'w': snapshot}
            next_cursor = base64.urlsafe_b64encode(json.dumps(payload, separators=(',', ':')).encode()).decode().rstrip('=')
        return {'items': [self.public(job_record(row)) for row in items], 'next_cursor': next_cursor}

    @staticmethod
    def validate(spec: ModelSpec, body: StudioRequest) -> None:
        if not body['prompt'].strip() and 'lipsync' not in spec['modes']:
            raise HTTPException(400, 'Enter a prompt for this model')
        for value, choices, label in ((body['aspect_ratio'], spec['aspect_ratios'], 'aspect ratio'),
                                      (body['resolution'], spec['resolutions'], 'resolution'),
                                      (body['duration'], spec['durations'], 'duration')):
            if value is not None and value not in choices:
                raise HTTPException(400, f'The selected model does not support this {label}')
        references = body.get('image_references')
        if references is not None and spec['kind'] != 'image':
            raise HTTPException(400, 'Multiple image references are supported only for Image and Cinema')
        if spec['kind'] == 'image':
            count = len(references) if references is not None else int(bool(body['image_url'] or body['image_id']))
            supports_image = spec.get('_supports_image') or spec.get('requires_image')
            limits = spec.get('image_reference_limits', {'min': int(bool(spec.get('requires_image'))), 'max': int(bool(supports_image)), 'app_max': int(bool(supports_image))})
            if not limits['min'] <= count <= min(5, limits['max'], limits['app_max']):
                raise HTTPException(400, 'Image reference count is outside the selected model limits')
        for reference in references or []:
            url = reference.get('image_url')
            if url is not None and not public_https_url(url):
                raise HTTPException(400, 'Each image reference URL must be public HTTPS')
        operation = body.get('operation')
        if operation is not None and operation not in spec['modes']:
            raise HTTPException(400, 'The selected model does not support this studio operation')
        if body.get('negative_prompt') and not spec.get('_supports_negative_prompt'):
            raise HTTPException(400, 'The selected model does not support a negative prompt')
        image_role = body.get('image_role')
        if image_role is not None:
            flag = '_supports_first_frame' if image_role == 'first_frame' else '_supports_reference_image'
            if spec['kind'] != 'video' or not spec.get(flag) or not (body.get('image_url') or body.get('image_id')):
                raise HTTPException(400, 'The selected model does not support this image role')
            if image_role == 'first_frame' and any(value or identifier for kind, (value, identifier) in reference_inputs(body).items() if kind != 'image'):
                raise HTTPException(400, 'First-frame mode cannot be combined with audio or video references')
        if operation == 'remix' and spec.get('_requires_video_for_remix') and not (body.get('video_url') or body.get('video_id')):
            raise HTTPException(400, 'Remix requires a source video URL')
        for kind, (value, identifier) in reference_inputs(body).items():
            if spec.get('requires_' + kind) and not (value or identifier or (kind == 'image' and references)):
                raise HTTPException(400, f'This model requires a public HTTPS {kind} URL')
            allowed = spec.get('requires_' + kind) or spec.get('_supports_' + kind)
            if (identifier and not allowed) or (value and (not allowed or not public_https_url(value))):
                raise HTTPException(400, f'This model requires a supported public HTTPS {kind} URL')

    def _owned_reference(self, owner: str, project: str | None, spec: ModelSpec,
                         kind: str, identifier: str) -> LocalImage:
        item = self.artifacts.get(owner, identifier)
        if item['project'] != project:
            raise HTTPException(403, 'Reference belongs to another project')
        if spec['adapter'] != 'openrouter' or kind not in spec.get('_inline_inputs', []):
            raise HTTPException(400, f'This model requires a public HTTPS {kind} URL; local {kind} references are not supported')
        # Only raster image inline input is documented by the current
        # adapters. Audio/video remain explicit public URL inputs.
        mime_type = item.get('mime')
        if kind != 'image' or not isinstance(mime_type, str) or mime_type not in {'image/png', 'image/jpeg', 'image/webp'}:
            raise HTTPException(415, 'Local references require a supported PNG, JPEG or WebP image')
        with self.artifacts.db() as db:
            binary = db.execute('SELECT blob FROM versions WHERE artifact=? AND version=?', (identifier, integer(item['version']))).fetchone()
        if not binary or not binary['blob']:
            raise HTTPException(400, 'Reference is not a binary media artifact')
        try:
            with Path(text(binary['blob'])).open('rb') as source:
                raw = source.read(INLINE_IMAGE_LIMIT + 1)
        except OSError:
            raise HTTPException(409, 'Reference bytes are unavailable on this host') from None
        if len(raw) > INLINE_IMAGE_LIMIT:
            raise HTTPException(413, 'Local image reference exceeds 8 MiB')
        from PIL import Image
        try:
            with Image.open(io.BytesIO(raw), formats=['PNG', 'JPEG', 'WEBP']) as image:
                mime = {'PNG': 'image/png', 'JPEG': 'image/jpeg', 'WEBP': 'image/webp'}.get(image.format or '')
                if mime != mime_type or image.width * image.height > 32_000_000 or getattr(image, 'n_frames', 1) != 1:
                    raise ValueError()
                _ = image.load()
        except (ValueError, OSError, Image.DecompressionBombError):
            raise HTTPException(400, 'Local image reference is invalid or exceeds 32 million pixels') from None
        data_url = 'data:' + mime_type + ';base64,' + base64.b64encode(raw).decode()
        return {'data_url': data_url, 'mime': mime_type}

    def prepare_references(self, owner: str, project: str | None, spec: ModelSpec, body: StudioRequest) -> StudioRequest:
        prepared: StudioRequest = body.copy()
        total = 0
        resolved: dict[str, LocalImage] = {}
        for kind, (_, identifier) in reference_inputs(body).items():
            if identifier is not None:
                local = self._owned_reference(owner, project, spec, kind, identifier)
                total += len(local['data_url'])
                if total > INLINE_TOTAL_LIMIT:
                    raise HTTPException(413, 'Inline references exceed 20 MiB')
                resolved[kind] = local
        if resolved:
            prepared['_local_references'] = resolved
        if body['image_references'] is not None:
            ordered: list[LocalImage | PublicImage] = []
            for reference in body['image_references']:
                identifier, url = reference.get('image_id'), reference.get('image_url')
                if identifier is not None:
                    local = self._owned_reference(owner, project, spec, 'image', identifier)
                    total += len(local['data_url'])
                    if total > INLINE_TOTAL_LIMIT:
                        raise HTTPException(413, 'Inline references exceed 20 MiB')
                    ordered.append(local)
                elif url is not None:
                    ordered.append({'url': url})
                else:
                    raise HTTPException(400, 'An image reference source is missing')
            prepared['_local_image_references'] = ordered
        if total > INLINE_TOTAL_LIMIT:
            raise HTTPException(413, 'Inline references exceed 20 MiB')
        return prepared

    async def close(self) -> None:
        """Stop in-flight transports; unknown submissions remain non-replayable."""
        tasks = list(self.tasks)
        for task in tasks:
            _ = task.cancel()
        if tasks:
            _ = await asyncio.gather(*tasks, return_exceptions=True)

    def _spawn(self, coroutine: Coroutine[object, object, None]) -> None:
        task = asyncio.create_task(coroutine)
        self.tasks.add(task)
        task.add_done_callback(self._completed)

    def _completed(self, task: asyncio.Task[None]) -> None:
        self.tasks.discard(task)
        # Consume exceptions; submission handlers already persist safe errors.
        if not task.cancelled():
            _ = task.exception()

    async def create(self, owner: str, project: str | None, values: object, *,
                     before_submit: Callable[[], None] | None = None) -> StudioJob:
        body = StudioInput.model_validate(values).request_data()
        if body['project_id'] != project:
            raise HTTPException(400, 'Project does not match this studio request')
        if not body['acknowledge_billing']:
            raise HTTPException(409, 'Confirm paid usage for this selected account and model before generating')
        # Newly optional fields do not change legacy digests when omitted, so
        # host upgrades can still return an already reserved request receipt.
        canonical = {key: value for key, value in body.items() if key not in NEW_FIELDS or value is not None}
        digest = hashlib.sha256(json.dumps(canonical, sort_keys=True).encode()).hexdigest()
        with self.db() as db:
            prior = db.execute('SELECT * FROM studio_jobs WHERE owner=? AND request_id=?', (owner, body['request_id'])).fetchone()
        if prior:
            if prior['digest'] != digest:
                raise HTTPException(409, 'Studio request ID was reused with different arguments')
            return self.public(job_record(prior))
        await self._load_catalog()
        if before_submit is not None:
            before_submit()
        spec = next((model for model in self.available_models() if model['id'] == body['model']), None)
        if not spec:
            raise HTTPException(400, 'This model is not supported by the studio catalog')
        if spec['kind'] == 'audio':
            raise HTTPException(400, 'Use the dedicated music generation endpoint for audio models')
        self.validate(spec, body)
        resolved = self.prepare_references(owner, project, spec, body)
        key = self.account(body['provider_id'], spec['kind'], spec['adapter'])
        identifier = uuid.uuid4().hex
        from termx.media.studio_projects import project_mutation
        with project_mutation(self.state, owner, project), self.db() as db:
            _ = db.execute('BEGIN IMMEDIATE')
            # A concurrent browser request may have reserved the same effect.
            prior = db.execute('SELECT * FROM studio_jobs WHERE owner=? AND request_id=?', (owner, body['request_id'])).fetchone()
            if prior:
                if prior['digest'] != digest:
                    raise HTTPException(409, 'Studio request ID was reused with different arguments')
                return self.public(job_record(prior))
            now = time()
            self.settings_store.enforce(db, owner, spec, body, now)
            _ = db.execute('''INSERT INTO studio_jobs(id,owner,project,request_id,digest,provider_id,adapter,model,kind,prompt,status,created,updated,parameters)
                VALUES(?,?,?,?,?,?,?,?,?,?,'submitting',?,?,?)''', (identifier, owner, project, body['request_id'], digest,
                body['provider_id'], spec['adapter'], spec['id'], spec['kind'], body['prompt'], now, now, json.dumps(self.parameters(body))))
        self._spawn(self._submit(owner, identifier, spec, resolved, key, effect_guard(before_submit)))
        return self.get(owner, identifier)

    async def create_music(self, owner: str, project: str | None, values: object, *,
                           before_submit: Callable[[], None] | None = None) -> StudioJob:
        body = MusicInput.model_validate(values)
        if body.project_id != project:
            raise HTTPException(400, 'Project does not match this music request')
        if not body.acknowledge_billing:
            raise HTTPException(409, 'Confirm paid usage for this selected account and model before generating')
        digest = hashlib.sha256(json.dumps({'music': body.model_dump()}, sort_keys=True).encode()).hexdigest()
        with self.db() as db:
            prior = db.execute('SELECT * FROM studio_jobs WHERE owner=? AND request_id=?', (owner, body.request_id)).fetchone()
        if prior:
            if prior['digest'] != digest:
                raise HTTPException(409, 'Studio request ID was reused with different arguments')
            return self.public(job_record(prior))
        if before_submit is not None:
            before_submit()
        if any(item['id'] == body.model for item in OPENROUTER_MUSIC_MODELS):
            await self._load_music_catalog()
            if before_submit is not None:
                before_submit()
        spec = next((item for item in self.available_models() if item['id'] == body.model and item['kind'] == 'audio' and 'music' in item['modes']), None)
        if spec is None:
            raise HTTPException(400, 'This music model is not supported by the Studio catalog')
        key = self.account(body.provider_id, 'audio', spec['adapter'])
        provider = object_map(self.state.agent_store.get_provider(body.provider_id))
        provider_limits = clean_provider_limits(object_map(provider.get('limits') or {}))
        timeout_s = provider_limits['request_timeout_s']
        identifier = uuid.uuid4().hex
        from termx.media.studio_projects import project_mutation
        with project_mutation(self.state, owner, project), self.db() as db:
            _ = db.execute('BEGIN IMMEDIATE')
            prior = db.execute('SELECT * FROM studio_jobs WHERE owner=? AND request_id=?', (owner, body.request_id)).fetchone()
            if prior:
                if prior['digest'] != digest:
                    raise HTTPException(409, 'Studio request ID was reused with different arguments')
                return self.public(job_record(prior))
            now = time()
            self.settings_store.enforce(db, owner, spec, {'duration': body.duration}, now)
            _ = db.execute('''INSERT INTO studio_jobs(id,owner,project,request_id,digest,provider_id,adapter,model,kind,prompt,status,created,updated,parameters)
                VALUES(?,?,?,?,?,?,?,?,?,?,'submitting',?,?,?)''', (identifier, owner, project, body.request_id, digest,
                body.provider_id, spec['adapter'], spec['id'], 'audio', body.prompt, now, now, json.dumps(body.parameters())))
        self._spawn(self._submit_music(owner, identifier, body, key, spec['adapter'], effect_guard(before_submit), request_timeout_s=timeout_s))
        return self.get(owner, identifier)

    async def _submit_music(self, owner: str, identifier: str, body: MusicInput, key: str, adapter_name: str,
                           before_submit: Callable[[], None] | None = None, *, request_timeout_s: float | None = None) -> None:
        adapter = OpenRouterMusicAdapter(self.artifacts, client=self.client, request_timeout_s=request_timeout_s) if adapter_name == 'openrouter' else ElevenLabsMusicAdapter(self.artifacts, client=self.client)
        try:
            result = await adapter.submit(body, key, owner, before_submit=before_submit)
            self._update(owner, identifier, job_result(result))
        except SubmissionBlocked:
            self._update(owner, identifier, {'status': 'failed', 'error': 'Authorization changed before provider submission. No music was submitted.'})
        except OpenRouterError as exc:
            self._update(owner, identifier, {'status': 'unknown' if exc.uncertain else 'failed', 'error': exc.message})
        except asyncio.CancelledError:
            raise
        except Exception:
            self._update(owner, identifier, {'status': 'unknown', 'error': 'Music submission could not be confirmed. Inspect the provider account; this request will not be resubmitted.'})
        finally:
            with self.db() as db:
                _ = db.execute("UPDATE studio_jobs SET status='unknown',error='Music submission was interrupted. Inspect the provider account; this request will not be resubmitted.',updated=? WHERE owner=? AND id=? AND status='submitting'", (time(), owner, identifier))

    def _update(self, owner: str, identifier: str, result: JobResult) -> None:
        with self.db() as db:
            _ = db.execute('''UPDATE studio_jobs SET status=?,task_id=COALESCE(?,task_id),outputs=?,error=?,updated=?
                WHERE owner=? AND id=? AND status NOT IN ('completed','failed')''',
                (result['status'], result.get('task_id'), json.dumps(result.get('outputs', [])), result.get('error'), time(), owner, identifier))

    async def _submit(self, owner: str, identifier: str, spec: ModelSpec, body: StudioRequest, key: str,
                      before_submit: Callable[[], None] | None = None) -> None:
        result: JobResult
        try:
            if spec['adapter'] == 'openrouter':
                if before_submit is None:
                    result = job_result(await self.openrouter.submit(spec, body, key, owner, body['project_id']))
                else:
                    result = job_result(await self.openrouter.submit(spec, body, key, owner, body['project_id'], before_submit=before_submit))
            else:
                payload = self.kie_payload(spec, body)
                data = await self.kie_request('POST', '/createTask', key, json=payload, before_submit=before_submit)
                try:
                    receipt = object_map(data.get('data'))
                except ValueError:
                    receipt = {}
                task_id = receipt.get('taskId')
                if not isinstance(task_id, str) or not REMOTE_ID.fullmatch(task_id):
                    raise OpenRouterError('The provider returned no valid job receipt. Inspect the provider account; this request will not be resubmitted.', uncertain=True)
                result = {'status': 'queued', 'task_id': task_id}
            self._update(owner, identifier, result)
        except SubmissionBlocked:
            self._update(owner, identifier, {'status': 'failed', 'error': 'Authorization changed before provider submission. No generation was submitted.'})
        except OpenRouterError as exc:
            self._update(owner, identifier, {'status': 'unknown' if exc.uncertain else 'failed', 'error': exc.message})
        except asyncio.CancelledError:
            raise
        except Exception:
            self._update(owner, identifier, {'status': 'unknown', 'error': 'Submission could not be confirmed. Inspect the provider account; this request will not be resubmitted.'})
        finally:
            with self.db() as db:
                _ = db.execute("UPDATE studio_jobs SET status='unknown',error='Submission was interrupted. Inspect the provider account; this request will not be resubmitted.',updated=? WHERE owner=? AND id=? AND status='submitting'", (time(), owner, identifier))

    async def refresh(self, owner: str, identifier: str) -> StudioJob:
        row = self._row(owner, identifier)
        # ElevenLabs compose returns synchronous bytes, with no documented
        # polling contract. Even a future stored song receipt must never be
        # sent to another provider's status endpoint.
        if row['adapter'] == 'elevenlabs' or row['status'] in TERMINAL or not row['task_id'] or identifier in self.refreshing:
            return self.public(row)
        key = self.account(row['provider_id'], row['kind'], row['adapter'])
        self.refreshing.add(identifier)
        self._spawn(self._refresh(owner, identifier, row, key))
        return self.public(row)

    async def _refresh(self, owner: str, identifier: str, row: JobRecord, key: str) -> None:
        result: JobResult
        try:
            task_id = text(row['task_id'])
            if row['adapter'] == 'openrouter':
                result = job_result(await self.openrouter.refresh(task_id, key, owner, row['project']))
            else:
                data = await self.kie_request('GET', '/recordInfo', key, params={'taskId': task_id})
                try:
                    record = object_map(data.get('data'))
                except ValueError:
                    raise OpenRouterError('Provider job status could not be read. Refresh to reconcile this existing job.', uncertain=True)
                provider_state = record.get('state')
                status = {'waiting': 'queued', 'queuing': 'queued', 'generating': 'running', 'success': 'completed', 'fail': 'failed'}.get(provider_state) if isinstance(provider_state, str) else None
                if not status:
                    raise OpenRouterError('Provider job status is unrecognized. Refresh to reconcile this existing job.', uncertain=True)
                result = {'status': status}
                if status == 'completed':
                    try:
                        outputs = string_list(object_map(decode_json(text(record['resultJson'])))['resultUrls'])
                        if not 1 <= len(outputs) <= 8 or not all(public_https_url(url) for url in outputs):
                            raise ValueError('unsafe outputs')
                    except (KeyError, ValueError, TypeError):
                        raise OpenRouterError('Provider returned no safe output URLs. Refresh to reconcile this existing job.', uncertain=True) from None
                    result['outputs'] = outputs
                elif status == 'failed':
                    result['error'] = 'The provider reported generation failed. Check the selected account for billing and provider details.'
            self._update(owner, identifier, result)
        except OpenRouterError as exc:
            # A read failure does not turn a provider job into a terminal failed
            # generation, and a later refresh may still reconcile its receipt.
            self._update(owner, identifier, {'status': row['status'], 'error': exc.message})
        except Exception:
            self._update(owner, identifier, {'status': row['status'], 'error': 'Could not refresh the existing provider job. Refresh again to reconcile; no generation was resubmitted.'})
        finally:
            self.refreshing.discard(identifier)

    @staticmethod
    def kie_payload(spec: ModelSpec, body: StudioRequest) -> dict[str, object]:
        values: dict[str, object] = {}
        if body['prompt']:
            values['prompt'] = body['prompt']
        for field, value in (('aspect_ratio', body['aspect_ratio']), ('resolution', body['resolution']), ('duration', body['duration'])):
            if value is not None:
                values[field] = str(value) if field == 'duration' else value
        if spec['id'] in {'nano-banana-pro', 'nano-banana-2'}:
            values['output_format'] = 'png'
            if '_local_image_references' in body:
                urls: list[str] = []
                for reference in body['_local_image_references']:
                    if 'url' not in reference:
                        raise ValueError('Kie requires public image URLs')
                    urls.append(reference['url'])
                values['image_input'] = urls
            elif body.get('image_url'):
                values['image_input'] = [body['image_url']]
        elif spec['id'] == 'omnihuman-1-5':
            values.update(image_url=body['image_url'], audio_url=body['audio_url'])
            values['output_resolution'] = values.pop('resolution', '1080')
        return {'model': spec['id'], 'input': values}

    async def kie_request(self, method: str, path: str, key: str, *, json: Mapping[str, object] | None = None,
                          params: Mapping[str, str] | None = None,
                          before_submit: Callable[[], None] | None = None) -> dict[str, object]:
        client = self.client or httpx.AsyncClient(timeout=30, follow_redirects=False)
        try:
            if before_submit is not None:
                before_submit()
            async with client.stream(method, KIE_BASE + path, headers={'Authorization': 'Bearer ' + key}, follow_redirects=False, json=json, params=params) as response:
                if response.status_code >= 300:
                    raise OpenRouterError('Kie rejected or could not confirm this request. Check the stored account, model access and quota.', uncertain=response.status_code >= 500 or response.status_code in {408, 499} or response.status_code < 400)
                raw = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(raw) + len(chunk) > 1024 * 1024:
                        raise OpenRouterError('Provider response exceeded the status limit; reconcile this job in the provider account.', uncertain=True)
                    raw.extend(chunk)
                try:
                    data = decode_json(raw)
                except (ValueError, UnicodeError):
                    raise OpenRouterError('Provider returned an unreadable response; reconcile this job in the provider account.', uncertain=True) from None
                try:
                    data = object_map(data)
                except ValueError:
                    raise OpenRouterError('Provider returned an invalid response; reconcile this job in the provider account.', uncertain=True) from None
                code = data.get('code')
                if type(code) is not int:
                    raise OpenRouterError('Provider returned no valid acceptance code; reconcile this job in the provider account.', uncertain=True)
                if code != 200:
                    raise OpenRouterError('Kie did not accept this operation. Check the selected model, account access and quota.', uncertain=code in {500, 501, 502, 503, 504, 408, 499})
                return data
        except httpx.RequestError:
            raise OpenRouterError('Provider connection was interrupted; reconcile this job in the provider account. Generation will not be resubmitted.', uncertain=True) from None
        finally:
            if self.client is None:
                await client.aclose()
