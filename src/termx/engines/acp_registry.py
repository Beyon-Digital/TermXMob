"""Official ACP registry discovery and host-side installation.

The registry supplies metadata and distribution commands; every launched
agent still speaks ACP through the shared AcpEngine implementation.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import platform
import shutil
import stat
import subprocess
import tarfile
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

from .acp import AcpEngine
from .env import resolve_executable

REGISTRY_URL = "https://cdn.agentclientprotocol.com/registry/v1/latest/registry.json"
MAX_REGISTRY_BYTES = 5 * 1024 * 1024
MAX_ARCHIVE_BYTES = 600 * 1024 * 1024

# Preserve engine IDs already used by TermX profiles while using the official
# registry IDs as their source of truth.
ENGINE_IDS = {"grok-build": "grok", "antigravity-acp": "antigravity"}


def _platform_key() -> str:
    os_name = {"Darwin": "darwin", "Linux": "linux", "Windows": "windows"}.get(platform.system())
    arch = {"arm64": "aarch64", "aarch64": "aarch64", "x86_64": "x86_64", "AMD64": "x86_64"}.get(platform.machine())
    if not os_name or not arch:
        raise ValueError(f"ACP registry binaries are unavailable for {platform.system()} {platform.machine()}")
    return f"{os_name}-{arch}"


def _package_name(spec: str) -> str:
    # Strip an exact npm version while preserving scoped names.
    if spec.startswith("@"):
        scope, _, name = spec.partition("/")
        package = name.split("@", 1)[0]
        return f"{scope}/{package}"
    return spec.split("@", 1)[0]


def _candidate_bin(spec: str) -> str:
    return _package_name(spec).rsplit("/", 1)[-1]


def _npm_bin_path(package_root: Path, package: str) -> Path:
    manifest = json.loads((package_root / "package.json").read_text("utf-8"))
    bins = manifest.get("bin")
    if isinstance(bins, str):
        bin_rel = bins
    elif isinstance(bins, dict) and bins:
        bin_name = _candidate_bin(package)
        bin_rel = bins.get(bin_name) or next(iter(bins.values()))
    else:
        raise ValueError("ACP registry npm package does not declare a runnable command")
    result = (package_root / str(bin_rel)).resolve()
    if not result.is_relative_to(package_root.resolve()) or not result.is_file():
        raise ValueError("ACP registry npm package declares an invalid command path")
    return result


class RegistryAcpEngine(AcpEngine):
    """Generic ACP adapter parameterized by one official registry entry."""

    version_args: list[str] = []

    def __init__(self, engine_id: str, label: str, executable: str,
                 args: list[str], *, environment: dict[str, str] | None = None,
                 **kwargs: Any):
        self.id = engine_id
        self.label = label
        self.executable_name = executable
        env = {**os.environ, **(environment or {})}
        config = {"executable": executable, "args": args,
                  "env_names": list((environment or {}).keys())}
        super().__init__(executable_override=executable, launch_config=config, spawn_env=env, **kwargs)
        self._executable = executable


class AcpRegistry:
    def __init__(self, gateway: Any, *, root: Path | None = None):
        self.gateway = gateway
        self.root = root or Path(os.environ.get("TERMX_ACP_HOME", Path.home() / ".termx" / "acp"))
        self.cache_path = self.root / "registry.json"
        self.entries: dict[str, dict[str, Any]] = {}
        self.error: str | None = None
        self.refreshed_at: float | None = None
        self._refresh_task: asyncio.Task | None = None
        self._attempted = False
        self._global_npm_root: Path | bool | None = None

    def start(self) -> None:
        if self._refresh_task is None:
            self._refresh_task = asyncio.create_task(self._refresh_impl())

    def load_cached(self) -> bool:
        try:
            self._load_index(json.loads(self.cache_path.read_text("utf-8")))
        except (OSError, ValueError, TypeError):
            return False
        self._auto_register_detected()
        return True

    async def list(self) -> dict[str, Any]:
        if self._refresh_task and not self._refresh_task.done():
            await asyncio.shield(self._refresh_task)
        elif not self._attempted:
            await self.refresh()
        return self.as_dict()

    async def refresh(self) -> dict[str, Any]:
        if self._refresh_task and self._refresh_task is not asyncio.current_task() and not self._refresh_task.done():
            return await asyncio.shield(self._refresh_task)
        task = asyncio.create_task(self._refresh_impl())
        self._refresh_task = task
        return await asyncio.shield(task)

    async def _refresh_impl(self) -> dict[str, Any]:
        self._attempted = True
        try:
            index = await asyncio.to_thread(self._fetch_index)
            self._load_index(index)
            self.error = None
            self.refreshed_at = __import__("time").time()
        except Exception as exc:
            self.error = str(exc) or type(exc).__name__
            if not self.entries:
                try:
                    self._load_index(json.loads(self.cache_path.read_text("utf-8")))
                except Exception:
                    pass
        self._auto_register_detected()
        return self.as_dict()

    def _fetch_index(self) -> dict[str, Any]:
        request = urllib.request.Request(REGISTRY_URL, headers={"Accept": "application/json", "User-Agent": "TermX-ACP-Registry"})
        with urllib.request.urlopen(request, timeout=15) as response:
            payload = response.read(MAX_REGISTRY_BYTES + 1)
        if len(payload) > MAX_REGISTRY_BYTES:
            raise ValueError("ACP registry response exceeds the size limit")
        index = json.loads(payload)
        if not isinstance(index, dict) or index.get("version") != "1.0.0" or not isinstance(index.get("agents"), list):
            raise ValueError("ACP registry response has an unsupported format")
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = self.cache_path.with_suffix(".tmp")
        tmp.write_bytes(payload)
        os.replace(tmp, self.cache_path)
        return index

    def _load_index(self, index: dict[str, Any]) -> None:
        agents = index.get("agents")
        if not isinstance(agents, list):
            raise ValueError("ACP registry has no agents list")
        parsed: dict[str, dict[str, Any]] = {}
        for item in agents:
            if not isinstance(item, dict):
                continue
            key = item.get("id")
            dist = item.get("distribution")
            if isinstance(key, str) and key and isinstance(dist, dict):
                parsed[key] = item
        if not parsed:
            raise ValueError("ACP registry contains no usable agents")
        self.entries = parsed

    def _distribution(self, entry: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        dist = entry.get("distribution", {})
        if isinstance(dist.get("npx"), dict):
            return "npx", dist["npx"]
        if isinstance(dist.get("uvx"), dict):
            return "uvx", dist["uvx"]
        binary = dist.get("binary", {})
        try:
            platform_id = _platform_key()
        except ValueError:
            platform_id = ""
        selected = binary.get(platform_id) if isinstance(binary, dict) else None
        if isinstance(selected, dict):
            return "binary", selected
        raise ValueError("This ACP agent has no supported distribution for this host platform")

    def _registered_id(self, registry_id: str) -> str:
        return ENGINE_IDS.get(registry_id, registry_id)

    def _managed_command(self, registry_id: str, entry: dict[str, Any]) -> tuple[str, list[str]] | None:
        engine_id = self._registered_id(registry_id)
        prefs = self.gateway.settings() if self.gateway.settings else None
        runner = getattr(prefs, "acp_runners", {}).get(engine_id, {}) if prefs else {}
        if runner.get("registry_id") == registry_id:
            executable = resolve_executable(str(runner.get("executable", "")),
                                            override=str(runner.get("executable", "")))
            if executable:
                args = runner.get("args", [])
                return executable, [str(arg) for arg in args] if isinstance(args, list) else []
        marker = self.root / registry_id / "install.json"
        try:
            data = json.loads(marker.read_text("utf-8"))
            command = str(data["command"])
            args = data.get("args", [])
            if Path(command).is_file() and isinstance(args, list):
                return command, [str(arg) for arg in args]
        except (OSError, ValueError, KeyError, TypeError):
            pass
        try:
            kind, spec = self._distribution(entry)
        except ValueError:
            return None
        if kind == "binary":
            command = Path(str(spec.get("cmd", ""))).name
            engine_defaults = getattr(prefs, "engines", {}).get(engine_id, {}) if prefs else {}
            if engine_defaults.get("executable"):
                override = resolve_executable(str(engine_defaults["executable"]), override=str(engine_defaults["executable"]))
                if override:
                    return override, list(spec.get("args", []))
            found = resolve_executable(command)
            return (found, list(spec.get("args", []))) if found else None
        package = str(spec.get("package", ""))
        engine_defaults = getattr(prefs, "engines", {}).get(engine_id, {}) if prefs else {}
        if engine_defaults.get("executable"):
            override = resolve_executable(str(engine_defaults["executable"]), override=str(engine_defaults["executable"]))
            if override:
                return override, list(spec.get("args", []))
        if os.name == "nt" and package:
            root = self._npm_root()
            package_root = root / _package_name(package) if root else None
            if package_root and (package_root / "package.json").is_file():
                try:
                    node = resolve_executable("node")
                    script = _npm_bin_path(package_root, package)
                    if node:
                        return node, [str(script), *list(spec.get("args", []))]
                except (OSError, ValueError, json.JSONDecodeError):
                    pass
            return None
        found = resolve_executable(_candidate_bin(package)) if package else None
        return (found, list(spec.get("args", []))) if found else None

    def _npm_root(self) -> Path | None:
        if self._global_npm_root is None:
            npm = resolve_executable("npm")
            if not npm:
                self._global_npm_root = False
            else:
                try:
                    result = subprocess.run([npm, "root", "-g"], check=True, timeout=8,
                        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
                    self._global_npm_root = Path(result.stdout.strip())
                except (OSError, subprocess.SubprocessError):
                    self._global_npm_root = False
        return self._global_npm_root if isinstance(self._global_npm_root, Path) else None

    def as_dict(self) -> dict[str, Any]:
        values = []
        for registry_id, entry in sorted(self.entries.items(), key=lambda pair: str(pair[1].get("name", pair[0])).casefold()):
            installed = self._managed_command(registry_id, entry)
            engine_id = self._registered_id(registry_id)
            prefs = self.gateway.settings() if self.gateway.settings else None
            configured = getattr(prefs, "acp_runners", {}).get(engine_id, {}) if prefs else {}
            values.append({
                "id": registry_id,
                "engine_id": engine_id,
                "name": entry.get("name", registry_id),
                "description": entry.get("description", ""),
                "version": entry.get("version"),
                "website": entry.get("website"),
                "repository": entry.get("repository"),
                "license": entry.get("license"),
                "license_url": entry.get("license_url"),
                "icon": entry.get("icon"),
                "installed": installed is not None,
                "registered": configured.get("registry_id") == registry_id,
                "enabled": configured.get("enabled", True) if configured.get("registry_id") == registry_id else True,
                "executable": installed[0] if installed else None,
                "platform_supported": self._has_platform(entry),
            })
        return {"source": REGISTRY_URL, "refreshed_at": self.refreshed_at,
                "stale": self.error is not None, "error": self.error, "agents": values}

    def _has_platform(self, entry: dict[str, Any]) -> bool:
        dist = entry.get("distribution", {})
        if dist.get("npx") or dist.get("uvx"):
            return True
        try:
            key = _platform_key()
        except ValueError:
            return False
        return bool(isinstance(dist.get("binary"), dict) and dist["binary"].get(key))

    def _auto_register_detected(self) -> None:
        for registry_id, entry in self.entries.items():
            engine_id = self._registered_id(registry_id)
            prefs = self.gateway.settings() if self.gateway.settings else None
            configured = getattr(prefs, "acp_runners", {}).get(engine_id, {}) if prefs else {}
            if configured.get("enabled") is False:
                continue
            if engine_id in self.gateway._adapters:
                continue
            command = self._managed_command(registry_id, entry)
            if not command:
                continue
            _kind, distribution = self._distribution(entry)
            environment = distribution.get("env", {}) if isinstance(distribution.get("env", {}), dict) else {}
            self.gateway.register(RegistryAcpEngine(engine_id, str(entry.get("name", registry_id)), *command,
                environment=environment, event_sink=self.gateway.on_engine_event,
                approval_sink=self.gateway.approval_sink))
            self.gateway._registry_runner_ids.add(engine_id)

    async def install(self, registry_id: str) -> tuple[str, str, list[str], dict[str, Any]]:
        if not self.entries:
            await self.refresh()
        entry = self.entries.get(registry_id)
        if not entry:
            raise KeyError(f"ACP registry agent '{registry_id}' was not found")
        if not self._has_platform(entry):
            raise ValueError("This ACP agent is not available for this host platform")
        existing = self._managed_command(registry_id, entry)
        if existing:
            command, args = existing
        else:
            command, args = await asyncio.to_thread(self._install_sync, registry_id, entry)
        return self._registered_id(registry_id), command, args, entry

    def _install_sync(self, registry_id: str, entry: dict[str, Any]) -> tuple[str, list[str]]:
        kind, spec = self._distribution(entry)
        target = self.root / registry_id / str(entry.get("version", "latest"))
        target.mkdir(parents=True, exist_ok=True)
        args = [str(arg) for arg in spec.get("args", [])]
        env = dict(os.environ)
        env.update({str(k): str(v) for k, v in (spec.get("env") or {}).items()})
        if kind == "npx":
            package = str(spec.get("package", ""))
            if not package:
                raise ValueError("ACP registry npm distribution has no package")
            npm = resolve_executable("npm")
            if not npm:
                raise ValueError("Install Node.js/npm on the TermX host to install this ACP agent")
            subprocess.run([npm, "install", "--prefix", str(target), package], check=True, timeout=900,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            root_package = target / "node_modules" / Path(_package_name(package))
            script = _npm_bin_path(root_package, package)
            if os.name == "nt":
                node = resolve_executable("node")
                if not node:
                    raise ValueError("Install Node.js on the TermX host to run this ACP agent")
                command = Path(node)
                args = [str(script), *args]
            else:
                manifest = json.loads((root_package / "package.json").read_text("utf-8"))
                bins = manifest.get("bin")
                bin_name = _candidate_bin(package)
                if isinstance(bins, dict) and bin_name not in bins:
                    bin_name = next(iter(bins))
                command = target / "node_modules" / ".bin" / bin_name
                if not command.exists():
                    command = script
        elif kind == "uvx":
            package = str(spec.get("package", ""))
            if not package:
                raise ValueError("ACP registry uvx distribution has no package")
            uv = resolve_executable("uv")
            if not uv:
                raise ValueError("Install uv on the TermX host to install this ACP agent")
            bin_dir = target / "bin"
            tools_dir = target / "tools"
            install_env = {**env, "UV_TOOL_BIN_DIR": str(bin_dir), "UV_TOOL_DIR": str(tools_dir)}
            subprocess.run([uv, "tool", "install", package], check=True, timeout=900,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=install_env)
            candidate = bin_dir / _candidate_bin(package)
            if os.name == "nt":
                candidate = candidate.with_suffix(".exe")
            if not candidate.exists():
                candidates = [path for path in bin_dir.iterdir() if path.is_file()]
                if not candidates:
                    raise ValueError("ACP registry package did not install an executable")
                candidate = candidates[0]
            command = candidate
        else:
            command = self._install_binary(target, spec)
        command = Path(command).resolve()
        if not command.is_file():
            raise ValueError("ACP registry installer did not produce the expected command")
        if os.name != "nt":
            command.chmod(command.stat().st_mode | stat.S_IXUSR)
        install_record = {"registry_id": registry_id, "version": entry.get("version"),
                          "command": str(command), "args": args}
        marker = self.root / registry_id / "install.json"
        marker.parent.mkdir(parents=True, exist_ok=True)
        temporary = marker.with_suffix(".tmp")
        temporary.write_text(json.dumps(install_record), "utf-8")
        os.replace(temporary, marker)
        return str(command), args

    def _install_binary(self, target: Path, spec: dict[str, Any]) -> Path:
        url = str(spec.get("archive", ""))
        if not url.startswith("https://"):
            raise ValueError("ACP registry binary must use HTTPS")
        request = urllib.request.Request(url, headers={"User-Agent": "TermX-ACP-Registry"})
        with urllib.request.urlopen(request, timeout=120) as response:
            payload = response.read(MAX_ARCHIVE_BYTES + 1)
        if len(payload) > MAX_ARCHIVE_BYTES:
            raise ValueError("ACP agent archive exceeds the size limit")
        expected = str(spec.get("sha256", "")).lower()
        if expected:
            actual = hashlib.sha256(payload).hexdigest()
            if actual != expected:
                raise ValueError("ACP agent archive SHA-256 verification failed")
        archive = target / "agent.archive"
        archive.write_bytes(payload)
        extract_to = target / "payload"
        extract_to.mkdir(exist_ok=True)
        if zipfile.is_zipfile(archive):
            with zipfile.ZipFile(archive) as bundle:
                for item in bundle.infolist():
                    rel = PurePosixPath(item.filename)
                    if rel.is_absolute() or ".." in rel.parts:
                        raise ValueError("ACP agent archive contains an unsafe path")
                    destination = (extract_to / Path(*rel.parts)).resolve()
                    if not destination.is_relative_to(extract_to.resolve()):
                        raise ValueError("ACP agent archive contains an unsafe path")
                    if item.is_dir():
                        destination.mkdir(parents=True, exist_ok=True)
                    else:
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        destination.write_bytes(bundle.read(item))
        elif tarfile.is_tarfile(archive):
            with tarfile.open(archive) as bundle:
                for item in bundle.getmembers():
                    rel = PurePosixPath(item.name)
                    if rel.is_absolute() or ".." in rel.parts or not (item.isfile() or item.isdir()):
                        if item.isdir():
                            continue
                        raise ValueError("ACP agent archive contains an unsafe path or link")
                    destination = (extract_to / Path(*rel.parts)).resolve()
                    if not destination.is_relative_to(extract_to.resolve()):
                        raise ValueError("ACP agent archive contains an unsafe path")
                    if item.isdir():
                        destination.mkdir(parents=True, exist_ok=True)
                    else:
                        stream = bundle.extractfile(item)
                        if stream is None:
                            continue
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        destination.write_bytes(stream.read(MAX_ARCHIVE_BYTES + 1))
                        if item.mode & 0o111:
                            destination.chmod(destination.stat().st_mode | stat.S_IXUSR)
        else:
            filename = Path(str(spec.get("cmd", "agent"))).name
            destination = extract_to / filename
            destination.write_bytes(payload)
        relative = PurePosixPath(str(spec.get("cmd", ""))).parts
        command = (extract_to / Path(*[part for part in relative if part not in {".", ""}])).resolve()
        if not command.is_relative_to(extract_to.resolve()) or not command.is_file():
            raise ValueError("ACP agent archive did not contain its declared command")
        return command

    async def stop(self) -> None:
        if self._refresh_task and not self._refresh_task.done():
            self._refresh_task.cancel()
            await asyncio.gather(self._refresh_task, return_exceptions=True)
