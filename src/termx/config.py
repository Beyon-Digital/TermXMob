from __future__ import annotations

import json
import os
import shutil
import stat
import tempfile
import threading
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from time import time
from typing import Any

from termx.sessions import default_shell


CONFIG_VERSION = 1


def config_dir() -> Path:
    override = os.environ.get("TERMX_CONFIG_DIR")
    if override:
        return Path(override)
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg) / "termx"
    return Path.home() / ".config" / "termx"


def config_path() -> Path:
    return config_dir() / "config.json"


def default_cwd() -> str:
    return str(Path.home())


def available_shells() -> list[str]:
    if os.name == "nt":
        found: list[str] = []
        candidates = [
            os.environ.get("COMSPEC"),
            shutil.which("pwsh.exe"),
            shutil.which("powershell.exe"),
            shutil.which("bash.exe"),
        ]
        for candidate in candidates:
            if candidate and os.path.isfile(candidate) and candidate not in found:
                found.append(candidate)
        return found
    found = []
    path = Path("/etc/shells")
    if path.is_file():
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if os.path.isfile(line) and os.access(line, os.X_OK) and line not in found:
                found.append(line)
    sh = default_shell()
    if os.path.isfile(sh) and os.access(sh, os.X_OK) and sh not in found:
        found.insert(0, sh)
    return found


def validate_shell(shell: str) -> str:
    resolved = str(Path(shell).expanduser())
    if resolved in available_shells():
        return resolved
    if os.path.isfile(resolved) and os.access(resolved, os.X_OK):
        return resolved
    raise ValueError(f"invalid shell: {shell}")


def validate_cwd(cwd: str) -> str:
    path = Path(cwd).expanduser()
    try:
        path = path.resolve(strict=True)
    except OSError as exc:
        raise ValueError(f"invalid working directory: {cwd}") from exc
    if not path.is_dir():
        raise ValueError(f"not a directory: {cwd}")
    if not os.access(path, os.R_OK | os.X_OK):
        raise ValueError(f"working directory is not accessible: {cwd}")
    return str(path)


FS_LIST_MAX = 400


def list_dir_entries(path: str | None = None) -> dict[str, Any]:
    current = Path(validate_cwd(path or default_cwd()))
    entries: list[dict[str, str]] = []
    try:
        children = list(current.iterdir())
    except OSError as exc:
        raise ValueError(f"cannot list directory: {exc}") from exc
    children.sort(key=lambda child: child.name.lower())
    for child in children:
        try:
            if not child.is_dir():
                continue
        except OSError:
            continue
        entries.append({"name": child.name, "path": str(child)})
        if len(entries) >= FS_LIST_MAX:
            break
    parent = current.parent
    return {
        "path": str(current),
        "parent": str(parent) if parent != current else None,
        "home": default_cwd(),
        "entries": entries,
    }


def atomic_write(path: Path, data: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".termx-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


@dataclass
class TerminalPrefs:
    shell: str = field(default_factory=default_shell)
    cwd: str = field(default_factory=default_cwd)


@dataclass
class SavedCommand:
    id: str
    name: str
    command: str
    confirm: bool = False
    order: int = 0
    created_at: float = 0
    updated_at: float = 0

    def public(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SavedDirectory:
    id: str
    name: str
    path: str
    order: int = 0
    created_at: float = 0
    updated_at: float = 0

    def public(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TunnelProfile:
    id: str
    provider: str
    name: str
    kind: str
    extra: dict[str, Any] = field(default_factory=dict)

    def public(self) -> dict[str, Any]:
        extra = {key: value for key, value in self.extra.items() if key not in {"token", "authtoken", "secret"}}
        if any(key in self.extra for key in ("token", "authtoken", "secret")):
            extra["configured"] = True
        return {
            "id": self.id,
            "provider": self.provider,
            "name": self.name,
            "kind": self.kind,
            "extra": extra,
        }


@dataclass
class TunnelPrefs:
    active_profile_id: str | None = None
    profiles: list[TunnelProfile] = field(default_factory=list)


@dataclass
class DesktopPrefs:
    view_only_default: bool = True
    retain_virtual_display: bool = False


@dataclass
class HostConfig:
    version: int = CONFIG_VERSION
    terminal: TerminalPrefs = field(default_factory=TerminalPrefs)
    commands: list[SavedCommand] = field(default_factory=list)
    directories: list[SavedDirectory] = field(default_factory=list)
    tunnels: TunnelPrefs = field(default_factory=TunnelPrefs)
    desktop: DesktopPrefs = field(default_factory=DesktopPrefs)

    def to_json(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "terminal": asdict(self.terminal),
            "commands": [asdict(item) for item in self.commands],
            "directories": [asdict(item) for item in self.directories],
            "tunnels": {
                "active_profile_id": self.tunnels.active_profile_id,
                "profiles": [asdict(item) for item in self.tunnels.profiles],
            },
            "desktop": asdict(self.desktop),
        }


def _command_from(raw: dict[str, Any]) -> SavedCommand:
    return SavedCommand(
        id=str(raw.get("id") or uuid.uuid4().hex[:12]),
        name=str(raw.get("name") or ""),
        command=str(raw.get("command") or ""),
        confirm=bool(raw.get("confirm", False)),
        order=int(raw.get("order") or 0),
        created_at=float(raw.get("created_at") or 0),
        updated_at=float(raw.get("updated_at") or 0),
    )


def _directory_from(raw: dict[str, Any]) -> SavedDirectory:
    return SavedDirectory(
        id=str(raw.get("id") or uuid.uuid4().hex[:12]),
        name=str(raw.get("name") or ""),
        path=str(raw.get("path") or ""),
        order=int(raw.get("order") or 0),
        created_at=float(raw.get("created_at") or 0),
        updated_at=float(raw.get("updated_at") or 0),
    )


def _profile_from(raw: dict[str, Any]) -> TunnelProfile:
    extra = raw.get("extra") if isinstance(raw.get("extra"), dict) else {}
    return TunnelProfile(
        id=str(raw.get("id") or uuid.uuid4().hex[:12]),
        provider=str(raw.get("provider") or "cloudflare"),
        name=str(raw.get("name") or "Tunnel"),
        kind=str(raw.get("kind") or "quick"),
        extra=dict(extra),
    )


def config_from_json(raw: dict[str, Any]) -> HostConfig:
    terminal_raw = raw.get("terminal") if isinstance(raw.get("terminal"), dict) else {}
    tunnels_raw = raw.get("tunnels") if isinstance(raw.get("tunnels"), dict) else {}
    desktop_raw = raw.get("desktop") if isinstance(raw.get("desktop"), dict) else {}
    commands_raw = raw.get("commands") if isinstance(raw.get("commands"), list) else []
    directories_raw = raw.get("directories") if isinstance(raw.get("directories"), list) else []
    profiles_raw = tunnels_raw.get("profiles") if isinstance(tunnels_raw.get("profiles"), list) else []
    shell = str(terminal_raw.get("shell") or default_shell())
    cwd = str(terminal_raw.get("cwd") or default_cwd())
    return HostConfig(
        version=int(raw.get("version") or CONFIG_VERSION),
        terminal=TerminalPrefs(shell=shell, cwd=cwd),
        commands=[_command_from(item) for item in commands_raw if isinstance(item, dict)],
        directories=[_directory_from(item) for item in directories_raw if isinstance(item, dict)],
        tunnels=TunnelPrefs(
            active_profile_id=tunnels_raw.get("active_profile_id")
            if isinstance(tunnels_raw.get("active_profile_id"), str)
            else None,
            profiles=[_profile_from(item) for item in profiles_raw if isinstance(item, dict)],
        ),
        desktop=DesktopPrefs(
            view_only_default=bool(desktop_raw.get("view_only_default", True)),
            retain_virtual_display=bool(desktop_raw.get("retain_virtual_display", False)),
        ),
    )


class ConfigStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or config_path()
        self._lock = threading.Lock()
        self._config = self._load()

    def _load(self) -> HostConfig:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                return config_from_json(raw)
        except (OSError, json.JSONDecodeError):
            pass
        return HostConfig()

    def get(self) -> HostConfig:
        with self._lock:
            return self._config

    def _save(self) -> None:
        atomic_write(self.path, json.dumps(self._config.to_json(), indent=2))

    def update_terminal(self, shell: str | None = None, cwd: str | None = None) -> TerminalPrefs:
        with self._lock:
            if shell is not None:
                self._config.terminal.shell = validate_shell(shell)
            if cwd is not None:
                self._config.terminal.cwd = validate_cwd(cwd)
            self._save()
            return TerminalPrefs(shell=self._config.terminal.shell, cwd=self._config.terminal.cwd)

    def list_commands(self) -> list[SavedCommand]:
        with self._lock:
            return sorted(self._config.commands, key=lambda item: (item.order, item.created_at))

    def add_command(self, name: str, command: str, confirm: bool = False) -> SavedCommand:
        name = name.strip()
        command = command.strip()
        if not name:
            raise ValueError("name is required")
        if not command:
            raise ValueError("command is required")
        now = time()
        with self._lock:
            item = SavedCommand(
                id=uuid.uuid4().hex[:12],
                name=name,
                command=command,
                confirm=confirm,
                order=len(self._config.commands),
                created_at=now,
                updated_at=now,
            )
            self._config.commands.append(item)
            self._save()
            return item

    def patch_command(
        self,
        command_id: str,
        name: str | None = None,
        command: str | None = None,
        confirm: bool | None = None,
    ) -> SavedCommand | None:
        with self._lock:
            for item in self._config.commands:
                if item.id != command_id:
                    continue
                if name is not None:
                    name = name.strip()
                    if not name:
                        raise ValueError("name is required")
                    item.name = name
                if command is not None:
                    command = command.strip()
                    if not command:
                        raise ValueError("command is required")
                    item.command = command
                if confirm is not None:
                    item.confirm = confirm
                item.updated_at = time()
                self._save()
                return item
            return None

    def delete_command(self, command_id: str) -> bool:
        with self._lock:
            before = len(self._config.commands)
            self._config.commands = [item for item in self._config.commands if item.id != command_id]
            if len(self._config.commands) == before:
                return False
            for index, item in enumerate(self._config.commands):
                item.order = index
            self._save()
            return True

    def reorder_commands(self, order: list[str]) -> list[SavedCommand]:
        with self._lock:
            by_id = {item.id: item for item in self._config.commands}
            next_items: list[SavedCommand] = []
            seen: set[str] = set()
            for command_id in order:
                item = by_id.get(command_id)
                if item is None or command_id in seen:
                    continue
                next_items.append(item)
                seen.add(command_id)
            for item in self._config.commands:
                if item.id not in seen:
                    next_items.append(item)
            for index, item in enumerate(next_items):
                item.order = index
            self._config.commands = next_items
            self._save()
            return list(next_items)

    def list_directories(self) -> list[SavedDirectory]:
        with self._lock:
            return sorted(self._config.directories, key=lambda item: (item.order, item.created_at))

    def add_directory(self, name: str, path: str) -> SavedDirectory:
        resolved = validate_cwd(path)
        label = name.strip() or Path(resolved).name or resolved
        now = time()
        with self._lock:
            for item in self._config.directories:
                if item.path == resolved:
                    raise ValueError("directory already saved")
            item = SavedDirectory(
                id=uuid.uuid4().hex[:12],
                name=label,
                path=resolved,
                order=len(self._config.directories),
                created_at=now,
                updated_at=now,
            )
            self._config.directories.append(item)
            self._save()
            return item

    def get_directory(self, directory_id: str) -> SavedDirectory | None:
        with self._lock:
            for item in self._config.directories:
                if item.id == directory_id:
                    return item
            return None

    def delete_directory(self, directory_id: str) -> bool:
        with self._lock:
            before = len(self._config.directories)
            self._config.directories = [item for item in self._config.directories if item.id != directory_id]
            if len(self._config.directories) == before:
                return False
            for index, item in enumerate(self._config.directories):
                item.order = index
            self._save()
            return True

    def use_directory(self, directory_id: str) -> SavedDirectory:
        item = self.get_directory(directory_id)
        if item is None:
            raise KeyError(directory_id)
        resolved = validate_cwd(item.path)
        with self._lock:
            item.path = resolved
            item.updated_at = time()
            self._config.terminal.cwd = resolved
            self._save()
            return item

    def list_profiles(self) -> list[TunnelProfile]:
        with self._lock:
            return list(self._config.tunnels.profiles)

    def add_profile(self, provider: str, name: str, kind: str, extra: dict[str, Any] | None = None) -> TunnelProfile:
        with self._lock:
            profile = TunnelProfile(
                id=uuid.uuid4().hex[:12],
                provider=provider,
                name=name.strip() or provider,
                kind=kind,
                extra=dict(extra or {}),
            )
            self._config.tunnels.profiles.append(profile)
            self._save()
            return profile

    def get_profile(self, profile_id: str) -> TunnelProfile | None:
        with self._lock:
            for item in self._config.tunnels.profiles:
                if item.id == profile_id:
                    return item
            return None

    def delete_profile(self, profile_id: str) -> bool:
        with self._lock:
            before = len(self._config.tunnels.profiles)
            self._config.tunnels.profiles = [item for item in self._config.tunnels.profiles if item.id != profile_id]
            if self._config.tunnels.active_profile_id == profile_id:
                self._config.tunnels.active_profile_id = None
            if len(self._config.tunnels.profiles) == before:
                return False
            self._save()
            return True

    def set_active_profile(self, profile_id: str | None) -> None:
        with self._lock:
            self._config.tunnels.active_profile_id = profile_id
            self._save()
