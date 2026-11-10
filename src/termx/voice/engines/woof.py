"""Underdog Woof: an optional local engine the user downloads themselves.

The model is not pip-installable and its license is non-commercial. TermX never bundles it. The UI shows the
license and NOTICE first, and only after the owner accepts that exact text does the host fetch the rest of the
pinned Hugging Face revision. Every file is checked against a sha256 pinned in MANIFEST when it is downloaded and
again each time it is loaded, because the package is third-party Python that runs inside the host process.
Between the load-time hash check and the import a process running as the same user could still swap a file;
that user already controls the host.

Woof output is only ever played to the signed-in owner. It is never exposed as a hosted API and never used to
train or distill a speech model (license sections 3 and 5).
"""
from __future__ import annotations

import array
import asyncio
import gc
import hashlib
import importlib.util
import os
import shutil
import stat
import sys
import threading
import time
from collections.abc import AsyncIterator, Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote

from termx.voice.engines import EngineError, VoiceEngine

PACKAGE = 'underdog_woof'
WEIGHTS = 'woof_int8.npz'
ATTRIBUTION = {'text': 'Voice by Underdog Woof', 'url': 'https://underdog.ai'}
VOICES = [{'id': 'juno', 'label': 'Juno'}, {'id': 'mira', 'label': 'Mira'}]
LICENSE_NAME = 'Underdog Non-Commercial License 1.0'
LICENSE_CONTACT = 'license@conway.tech'


class WoofIntegrityError(EngineError):
    """A model file is missing, altered, linked or unexpected."""


@dataclass(frozen=True)
class WoofManifest:
    repo: str
    revision: str
    # path -> (sha256, size in bytes)
    files: Mapping[str, tuple[str, int]] = field(default_factory=dict)

    @property
    def installable(self) -> bool:
        return bool(self.revision and self.files) and all(
            name in self.files for name in ('LICENSE', 'NOTICE', WEIGHTS, PACKAGE + '/__init__.py'))

    @property
    def total_bytes(self) -> int:
        return sum(size for _, size in self.files.values())


# Recorded from https://huggingface.co/api/models/ConwayResearch/Underdog-Woof. The hashes were computed over the
# downloaded files; woof_int8.npz also equals the LFS object id the Hub reports. Samples, images, the README and the
# command-line entry point are not downloaded. Change the revision only together with every hash.
MANIFEST = WoofManifest(
    repo='ConwayResearch/Underdog-Woof',
    revision='be702960539777fc3f698e54335276a8391b00f9',
    files={
        'LICENSE': ('784c4a353d15f370ff387f74bde64f50a2041df9c6f2338c3d8a2f781b944de5', 7612),
        'NOTICE': ('0849de251c109869d24f8a0b520260afa10594334ae3d0c06b75e68be1e2c623', 919),
        'underdog_woof/__init__.py': ('aaacf02b28eabce10280a60c2699d4e40d4ca5aa22170d169b64943fee7849f7', 3002),
        'underdog_woof/normalize.py': ('f6d7c39ec8a58bb6ab1d36ee6ee0eae3c543aac8dddaa29f4065e72706c7c044', 47736),
        'underdog_woof/respell.py': ('6bf6bd4760474b991230a523d3f17b423012646737000b8974bd257875308a71', 4248),
        'underdog_woof/respell.tsv': ('4c700d4015390bb6e34c1f7362f87ea75acfb45144d2fbc45cf00d5739b66abc', 590622),
        'underdog_woof/runtime.py': ('e22d1e2a92f4d28c3784abbb889d77609dcecd1856bf6e65f03432b803a8d220', 15964),
        'underdog_woof/textsplit.py': ('f46ae52b24fae666ebe1dc55ebf644da757f238f1bb0408a3cc2dadb39eaf6e8', 2982),
        'woof_int8.npz': ('d895fbbe84b8f4f461e3e94ab48b1164353b1988ce8d365f5aa8263d0de1203e', 40595426),
    },
)


NUMPY_MISSING = 'Woof needs numpy, which ships with TermX. Repair this install (in a source checkout: uv sync), then reload.'


def numpy_available() -> bool:
    return importlib.util.find_spec('numpy') is not None


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        while chunk := handle.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def _remove_tree(path: Path) -> None:
    if path.is_symlink():
        path.unlink()
    elif path.exists():
        shutil.rmtree(path)


DOWNLOAD_HOSTS = ('huggingface.co', 'hf.co')


def _allowed_download_url(url) -> bool:
    host = (url.host or '').lower()
    return url.scheme == 'https' and any(host == name or host.endswith('.' + name) for name in DOWNLOAD_HOSTS)


async def _check_redirect(response) -> None:
    """Hugging Face sends large files to its CDN; follow that, and nothing else."""
    if response.has_redirect_location and not _allowed_download_url(response.request.url.join(response.headers['location'])):
        raise EngineError('The download server sent the request somewhere unexpected, so nothing was downloaded.')


def _default_client(transport=None):
    import httpx
    return httpx.AsyncClient(timeout=httpx.Timeout(30, read=120), follow_redirects=True, transport=transport,
                             headers={'User-Agent': 'termx-voice'}, event_hooks={'response': [_check_redirect]})


class WoofEngine(VoiceEngine):
    id = 'woof'
    label = 'Underdog Woof'

    def __init__(self, root: Path, manifest: WoofManifest = MANIFEST,
                 client_factory: Callable[[], Any] = _default_client, idle_seconds: float = 300.0,
                 clock: Callable[[], float] = time.monotonic, schedule_unload: bool = True):
        self.manifest, self.client_factory = manifest, client_factory
        self.idle_seconds, self.clock, self.schedule_unload = idle_seconds, clock, schedule_unload
        self.base = Path(root) / 'woof'
        self.dir = self.base / (manifest.revision or 'unpinned')
        self.partial = self.base / 'partial'
        self._lock = threading.RLock()
        self._install_guard = threading.Lock()
        self._installing = False
        self._error = ''
        self._damaged = ''  # why the installed files failed their check, until Woof is removed and downloaded again
        self._progress = {'done': 0, 'total': 0}
        self._texts: dict[str, Any] | None = None
        self._module_name = f'_termx_woof_{manifest.revision[:12] or "unpinned"}'
        self._model: Any = None
        self._voice: str | None = None
        self._last_used = 0.0
        self._timer: threading.Timer | None = None
        self._pool: ThreadPoolExecutor | None = None

    # -- status -----------------------------------------------------------------------------------

    @property
    def installed(self) -> bool:
        return (self.manifest.installable and self.dir.is_dir() and not self.dir.is_symlink()
                and (self.dir / '.complete').is_file())

    @property
    def loaded(self) -> bool:
        return self._model is not None

    @property
    def license_sha256(self) -> str:
        return self.manifest.files.get('LICENSE', ('', 0))[0]

    def status(self) -> dict[str, Any]:
        installed = self.installed
        if not self.manifest.installable:
            state, detail = 'unavailable', 'This build has no pinned Woof release, so it cannot be downloaded.'
        elif self._installing:
            state, detail = 'downloading', 'Downloading and checking the model files.'
        elif not numpy_available():
            state, detail = 'unavailable', NUMPY_MISSING
        elif self._error and not installed:
            state, detail = 'error', self._error
        elif installed and self._damaged:
            state, detail = 'error', f'{self._damaged} Remove Woof and download it again.'
        elif installed:
            state, detail = 'ready', 'Runs on this device. Nothing you say or read leaves it.'
        else:
            state, detail = 'not_installed', 'Not downloaded. The license is shown before anything is fetched.'
        return {'id': self.id, 'label': self.label, 'kind': 'local', 'state': state, 'detail': detail,
                'installed': installed, 'installable': self.manifest.installable and (numpy_available() or self._installing),
                'size_bytes': self.manifest.total_bytes, 'revision': self.manifest.revision[:12],
                'progress': dict(self._progress) if self._installing else None,
                'voices': self.voices(), 'attribution': ATTRIBUTION,
                'license': {'name': LICENSE_NAME, 'sha256': self.license_sha256, 'noncommercial': True,
                            'contact': LICENSE_CONTACT, 'url': self._url('LICENSE').replace('/resolve/', '/blob/')}}

    def voices(self) -> list[dict[str, str]]:
        return [dict(voice) for voice in VOICES]

    # -- download ---------------------------------------------------------------------------------

    def _url(self, path: str) -> str:
        return f'https://huggingface.co/{self.manifest.repo}/resolve/{self.manifest.revision}/{quote(path)}'

    async def _fetch_small(self, client, path: str) -> str:
        digest, size = self.manifest.files[path]
        response = await client.get(self._url(path))
        if response.status_code != 200:
            raise EngineError('The download server did not return the license files.')
        if len(response.content) > size or hashlib.sha256(response.content).hexdigest() != digest:
            raise WoofIntegrityError(f'The {path} file does not match the pinned release.')
        try:
            return response.content.decode('utf-8')
        except UnicodeDecodeError as exc:
            raise WoofIntegrityError(f'The {path} file is not text.') from exc

    def _license_payload(self, license_text: str, notice_text: str) -> dict[str, Any]:
        return {'revision': self.manifest.revision, 'name': LICENSE_NAME, 'contact': LICENSE_CONTACT,
                'license': {'text': license_text, 'sha256': self.license_sha256},
                'notice': {'text': notice_text, 'sha256': self.manifest.files['NOTICE'][0]},
                'attribution': ATTRIBUTION, 'noncommercial': True}

    async def license_texts(self) -> dict[str, Any]:
        """LICENSE and NOTICE exactly as published at the pinned revision."""
        if not self.manifest.installable:
            raise EngineError('This build has no pinned Woof release.')
        if self._texts is None:
            if self.installed:
                self._texts = self._license_payload(*(self._read_verified(name) for name in ('LICENSE', 'NOTICE')))
            else:
                async with self.client_factory() as client:
                    self._texts = self._license_payload(await self._fetch_small(client, 'LICENSE'),
                                                        await self._fetch_small(client, 'NOTICE'))
        return self._texts

    def _read_verified(self, name: str) -> str:
        path = self.dir / name
        if path.is_symlink() or _sha256_file(path) != self.manifest.files[name][0]:
            raise WoofIntegrityError(f'The installed {name} file does not match the pinned release.')
        return path.read_text(encoding='utf-8')

    async def install(self, accepted_license_sha256: str) -> None:
        if not self.manifest.installable:
            raise EngineError('This build has no pinned Woof release to download.')
        if not numpy_available():
            raise EngineError(NUMPY_MISSING)
        if self.installed:
            return
        with self._install_guard:
            if self._installing:
                raise EngineError('A Woof download is already running.')
            self._installing = True
            self._error = ''
            self._progress = {'done': 0, 'total': self.manifest.total_bytes}
        try:
            texts = await self.license_texts()
            if accepted_license_sha256 != texts['license']['sha256']:
                raise EngineError('Accept the current Woof license text before downloading the model.')
            _remove_tree(self.partial)
            self.partial.mkdir(parents=True, mode=0o700)
            async with self.client_factory() as client:
                for path in sorted(self.manifest.files, key=lambda name: (name == WEIGHTS, name)):
                    await self._download(client, path)
            (self.partial / '.complete').write_text(self.manifest.revision)
            _remove_tree(self.dir)
            self.partial.rename(self.dir)
        except BaseException as exc:
            _remove_tree(self.partial)
            self._error = str(exc) if isinstance(exc, EngineError) else 'The download failed. Check the connection and try again.'
            raise
        finally:
            self._installing = False

    async def _download(self, client, path: str) -> None:
        digest, size = self.manifest.files[path]
        target = self.partial / path
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        hasher, written = hashlib.sha256(), 0
        async with client.stream('GET', self._url(path)) as response:
            if response.status_code != 200:
                raise EngineError('The download server did not return a model file.')
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'wb') as handle:
                async for chunk in response.aiter_bytes(1 << 16):
                    written += len(chunk)
                    if written > size:
                        raise WoofIntegrityError('A downloaded file is larger than its pinned size.')
                    hasher.update(chunk)
                    handle.write(chunk)
                    self._progress['done'] += len(chunk)
        if written != size:
            raise WoofIntegrityError('A downloaded file is smaller than its pinned size.')
        if hasher.hexdigest() != digest:
            raise WoofIntegrityError('A downloaded file does not match its pinned checksum.')

    def remove(self) -> None:
        if self._installing:
            raise EngineError('Wait for the download to finish before removing Woof.')
        with self._lock:
            self._cancel_timer()
            self._unload()
            _remove_tree(self.base)
            self._error = ''
            self._damaged = ''
            self._texts = None

    # -- load, synthesize, unload -----------------------------------------------------------------

    def verify(self) -> None:
        root = self.dir
        if not self.installed:
            raise EngineError('Download Woof in Voice settings first.')
        for current, directories, files in os.walk(root, followlinks=False):
            directories[:] = [name for name in directories if name != '__pycache__']
            for name in directories:
                if (Path(current) / name).is_symlink():
                    raise WoofIntegrityError('A link was found in the Woof model folder.')
            for name in files:
                relative = (Path(current) / name).relative_to(root).as_posix()
                if relative != '.complete' and relative not in self.manifest.files:
                    raise WoofIntegrityError('An unexpected file was found in the Woof model folder.')
        for relative, (digest, size) in self.manifest.files.items():
            path = root / relative
            try:
                info = path.lstat()
            except OSError as exc:
                raise WoofIntegrityError(f'The model file {relative} is missing.') from exc
            if not stat.S_ISREG(info.st_mode) or info.st_size != size or _sha256_file(path) != digest:
                raise WoofIntegrityError(f'The model file {relative} does not match the pinned release.')

    def _clear_bytecode(self) -> None:
        _remove_tree(self.dir / PACKAGE / '__pycache__')

    def _load_module(self) -> Any:
        self.verify()
        self._clear_bytecode()
        package = self.dir / PACKAGE
        spec = importlib.util.spec_from_file_location(self._module_name, package / '__init__.py',
                                                      submodule_search_locations=[str(package)])
        if spec is None or spec.loader is None:
            raise EngineError('Woof could not be started.')
        module = importlib.util.module_from_spec(spec)
        sys.modules[self._module_name] = module
        try:
            spec.loader.exec_module(module)
        except Exception as exc:
            self._drop_modules()
            raise EngineError('Woof could not be started.') from exc
        finally:
            self._clear_bytecode()
        return module

    def _drop_modules(self) -> None:
        for name in [name for name in sys.modules if name == self._module_name or name.startswith(self._module_name + '.')]:
            sys.modules.pop(name, None)

    def _ensure_loaded(self, voice: str) -> None:
        if self._model is not None and self._voice == voice:
            return
        self._model = None
        module = sys.modules.get(self._module_name)
        try:
            if module is None:
                module = self._load_module()
            else:
                self.verify()
        except WoofIntegrityError as exc:
            self._unload()
            self._damaged = str(exc)
            raise
        except EngineError:
            self._unload()
            raise
        self._damaged = ''
        try:
            self._model = module.Woof(weights=str(self.dir / WEIGHTS), voice=voice)
        except Exception as exc:
            self._unload()
            raise EngineError('Woof could not load its model.') from exc
        self._voice = voice

    def _unload(self) -> None:
        self._model = None
        self._voice = None
        self._drop_modules()
        gc.collect()

    def unload_if_idle(self) -> bool:
        with self._lock:
            if self._model is None or self.clock() - self._last_used < self.idle_seconds:
                return False
            self._unload()
            return True

    def _cancel_timer(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

    def _arm_timer(self) -> None:
        if not self.schedule_unload:
            return
        self._cancel_timer()
        self._timer = threading.Timer(self.idle_seconds + 1, self._on_timer)
        self._timer.daemon = True
        self._timer.start()

    def _on_timer(self) -> None:
        if not self.unload_if_idle() and self._model is not None:
            self._arm_timer()

    def _run(self, text: str, voice: str) -> bytes:
        # One worker thread and this lock: the upstream package keeps a module-level random generator.
        with self._lock:
            self._ensure_loaded(voice)
            wav = self._model(text)
            self._last_used = self.clock()
        view = memoryview(wav)
        data = view.tobytes() if view.format == 'f' and view.ndim == 1 else array.array('f', wav).tobytes()
        if sys.byteorder == 'big':
            swapped = array.array('f')
            swapped.frombytes(data)
            swapped.byteswap()
            data = swapped.tobytes()
        return data

    async def synthesize(self, text: str, voice: str, options: dict[str, Any]) -> AsyncIterator[bytes]:
        voice = (voice or 'juno').lower()
        if voice not in {item['id'] for item in VOICES}:
            raise EngineError('Unknown voice. Choose Juno or Mira.')
        if not self.installed:
            raise EngineError('Download Woof in Voice settings first.')
        if not numpy_available():
            raise EngineError(NUMPY_MISSING)
        if self._pool is None:
            self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='termx-woof')
        try:
            data = await asyncio.get_running_loop().run_in_executor(self._pool, self._run, text, voice)
        except EngineError:
            raise
        except Exception as exc:
            raise EngineError('Woof could not speak this text.') from exc
        self._arm_timer()
        yield data

    def close(self) -> None:
        with self._lock:
            self._cancel_timer()
            self._unload()
        if self._pool is not None:
            self._pool.shutdown(wait=False)
            self._pool = None
