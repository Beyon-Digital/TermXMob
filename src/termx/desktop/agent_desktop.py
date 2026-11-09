"""Client for the pinned ``agent-desktop`` CLI (accessibility-tree desktop control).

Pure client: it imports nothing from ``termx.agent``. It finds a hash-verified
copy of the CLI, runs it with a minimal environment and a private state
directory, and turns its single JSON envelope into typed results. Requests are
built only by the builders below, so no raw argv from a model can reach a
process. The CLI is not a security boundary; authorization lives in the policy
engine, which decides before anything here is called.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import platform as _platform
import re
import shutil
import stat
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from termx.config import config_dir
from termx.private_files import protect_private_path

log = logging.getLogger("termx.desktop.agent_desktop")

PINS_PATH = Path(__file__).with_name("agent_desktop_pins.json")
PINNED_VERSION = "1.2.0"
SETTING_ENV = "TERMX_AGENT_DESKTOP"
ENVELOPE_VERSION = "2.6"
MAX_STDOUT = 8 * 1024 * 1024
READ_CONCURRENCY = 4
STATUS_TTL_S = 60.0
SESSION_RE = re.compile(r"^t[0-9a-f]{24}$")
SURFACES = ("window", "focused", "menu", "menubar", "sheet", "popover", "alert")
ACTIONS = (
    "click", "right_click", "set_value", "type", "clear", "select", "toggle", "check", "uncheck",
    "expand", "collapse", "scroll", "scroll_to", "focus",
)
_ACTION_COMMANDS = {name: name.replace("_", "-") for name in ACTIONS}
ALLOWED_COMMANDS = frozenset(
    {"version", "status", "list-apps", "list-windows", "snapshot", "find", "get"}
    | set(_ACTION_COMMANDS.values())
)
FORBIDDEN_FLAGS = frozenset(
    {"--headed", "--force", "--debug", "--screenshot", "--trace", "--trace-strict", "--include-bounds", "--verbose",
     "-v", "--agent-id", "--force-electron-a11y", "--interval-ms"}
)
ENV_ALLOW = (
    "PATH HOME USER LOGNAME LANG LC_ALL TMPDIR TMP TEMP SYSTEMROOT WINDIR COMSPEC USERPROFILE APPDATA LOCALAPPDATA "
    "PROGRAMDATA SYSTEMDRIVE PROGRAMFILES PROGRAMFILES(X86) PATHEXT DISPLAY WAYLAND_DISPLAY XDG_RUNTIME_DIR "
    "XDG_SESSION_TYPE XDG_CURRENT_DESKTOP DBUS_SESSION_BUS_ADDRESS XAUTHORITY __CFBundleIdentifier "
    "__CF_USER_TEXT_ENCODING"
).split()

_REF = re.compile(r"^@s[0-9a-z]{4,40}:e[0-9]{1,6}$")
_HANDLE = re.compile(r"^a[0-9a-f]{8}$")
_WINDOW = re.compile(r"^w-[0-9]{1,9}$")
_ROLE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,39}$")

# Which actions work in the CLI's default background mode, per OS (agent-desktop docs). `physical` ones need --headed,
# which TermX never passes. Value: None when supported, else the text the tool returns.
_UNSUPPORTED = {
    ("windows", "type"): "Typing keystrokes needs physical input here. Use set_value.",
    ("windows", "right_click"): "Opening a context menu needs physical input on Windows.",
    ("windows", "focus"): "Focusing a control needs physical input on Windows.",
    ("linux", "focus"): "Focusing a control needs physical input on Linux.",
    ("linux", "scroll"): "Scrolling needs physical input on Linux. Use scroll_to, or desktop_find with root.",
}


def action_unsupported(action: str, os_name: str) -> str | None:
    return _UNSUPPORTED.get((os_name, action))


class AgentDesktopFailure(Exception):
    """The CLI could not run or answered with something unusable. ``kind`` is stable; ``code`` is an issue code."""

    def __init__(self, kind: str, code: str = "", *, delivery_unknown: bool = False) -> None:
        super().__init__(f"{kind}:{code}" if code else kind)
        self.kind = kind
        self.code = code or kind
        self.delivery_unknown = delivery_unknown


@dataclass(frozen=True)
class CliError:
    code: str
    retry_safe: bool = False
    delivery: str | None = None
    details: dict[str, Any] = field(default_factory=dict)  # message/suggestion text is deliberately dropped


@dataclass(frozen=True)
class Envelope:
    ok: bool
    command: str
    data: Any
    error: CliError | None
    version: str


_CODE_CLASSES = {
    "stale": {"STALE_REF", "ELEMENT_NOT_FOUND", "SNAPSHOT_NOT_FOUND"},
    "ambiguous": {"AMBIGUOUS_TARGET"},
    "unsupported": {"ACTION_NOT_SUPPORTED", "PLATFORM_NOT_SUPPORTED", "POLICY_DENIED"},
    "absent": {"APP_NOT_FOUND", "WINDOW_NOT_FOUND"},
    "timeout": {"TIMEOUT", "APP_UNRESPONSIVE"},
    "permission": {"PERM_DENIED"},
}


def classify(error: CliError) -> str:
    """Branch on code and disposition only; unknown codes become ``failed``."""
    for name, codes in _CODE_CLASSES.items():
        if error.code in codes:
            return name
    return "failed"


# --------------------------------------------------------------------------- platform


@dataclass(frozen=True)
class HostPlatform:
    os: str  # macos | linux | windows
    arch: str  # arm64 | x86_64
    glibc: bool | None = None
    mac_version: tuple[int, ...] = ()

    @property
    def key(self) -> str:
        if self.os == "macos":
            return f"macos-{self.arch}"
        return f"{self.os}-{'aarch64' if self.arch == 'arm64' else self.arch}"

    @property
    def binary_name(self) -> str:
        return "agent-desktop.exe" if self.os == "windows" else "agent-desktop"


def detect_platform() -> HostPlatform:
    system = {"darwin": "macos", "win32": "windows"}.get(sys.platform, "linux")
    machine = _platform.machine().lower()
    arch = "arm64" if machine in {"arm64", "aarch64"} else "x86_64" if machine in {"x86_64", "amd64"} else machine
    glibc = _platform.libc_ver()[0] == "glibc" if system == "linux" else None
    mac: tuple[int, ...] = ()
    if system == "macos":
        try:
            mac = tuple(int(part) for part in _platform.mac_ver()[0].split(".") if part.isdigit())
        except ValueError:
            mac = ()
    return HostPlatform(system, arch, glibc, mac)


def host_os() -> str:
    """The OS name the tools judge actions by. Tests patch this seam."""
    return detect_platform().os


def load_pins() -> dict[str, Any]:
    return json.loads(PINS_PATH.read_text("utf-8"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


# --------------------------------------------------------------------------- requests


@dataclass(frozen=True)
class Request:
    command: str
    argv: tuple[str, ...]
    stdin: bytes | None = None
    wall_s: float = 8.0
    kind: str = "read"  # meta | read | action
    session: bool = True


def _assert_safe(command: str, argv: tuple[str, ...]) -> None:
    if command not in ALLOWED_COMMANDS:
        raise ValueError(f"command not allowed: {command}")
    for item in argv:
        if "\x00" in item:
            raise ValueError("NUL in argument")
    head = argv[: argv.index("--")] if "--" in argv else argv
    for item in head:
        if item.split("=", 1)[0] in FORBIDDEN_FLAGS:
            raise ValueError(f"flag not allowed: {item.split('=', 1)[0]}")


def _make(command: str, argv: list[str], **options: Any) -> Request:
    _assert_safe(command, tuple(argv))
    return Request(command, tuple(argv), **options)


def _free(value: Any, label: str, limit: int = 120) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= limit or "\x00" in value or not value.isprintable():
        raise ValueError(f"{label} must be 1 to {limit} printable characters")
    return value


def _check(pattern: re.Pattern[str], value: Any, label: str) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise ValueError(f"{label} is not valid")
    return value


def valid_ref(value: Any) -> bool:
    return isinstance(value, str) and bool(_REF.fullmatch(value))


def req_version() -> Request:
    return _make("version", [], wall_s=5.0, kind="meta", session=False)


def req_status() -> Request:
    return _make("status", [], wall_s=5.0, kind="meta", session=False)


def req_list_apps(app_name: str | None = None) -> Request:
    argv = ["--timeout-ms=5000"]
    if app_name is not None:
        argv.insert(0, f"--app={_app_name(app_name)}")
    return _make("list-apps", argv, wall_s=8.0, kind="meta", session=False)


def req_list_windows(app_name: str) -> Request:
    return _make("list-windows", [f"--app={_app_name(app_name)}", "--timeout-ms=5000"], wall_s=8.0, kind="meta", session=False)


def valid_app_name(value: Any) -> bool:
    """Whether an application name can be passed to the CLI. An OS-reported name that fails this is not offered to the model."""
    return isinstance(value, str) and 1 <= len(value) <= 200 and not value.startswith("-") and value.isprintable()


def valid_window_id(value: Any) -> bool:
    return isinstance(value, str) and _WINDOW.fullmatch(value) is not None


def _app_name(value: Any) -> str:
    if not valid_app_name(value):
        raise ValueError("application name is not valid")
    return value


def _scope(argv: list[str], app_name: str | None, window_id: str | None, surface: str, root: str | None, *, mode: str = "controls") -> None:
    if (app_name is None) == (root is None):
        raise ValueError("Give exactly one of app_id or root")
    if root is not None:
        if surface != "window" or window_id is not None:
            raise ValueError("root cannot be combined with surface or window_id")
        argv.append(f"--root={_check(_REF, root, 'root')}")
        return
    argv.append(f"--app={_app_name(app_name)}")
    if window_id is not None:
        argv.append(f"--window-id={_check(_WINDOW, window_id, 'window_id')}")
    if surface not in SURFACES:
        raise ValueError("surface is not valid")
    if surface != "window":
        argv.append(f"--surface={surface}")


def _waits(argv: list[str], wait_for: str | None, wait_for_gone: str | None, root: str | None, timeout_ms: int) -> int:
    if wait_for is None and wait_for_gone is None:
        return 0
    if wait_for is not None and wait_for_gone is not None:
        raise ValueError("wait_for and wait_for_gone are exclusive")
    if root is not None:
        raise ValueError("root cannot be combined with wait_for")
    if wait_for is not None:
        argv.append(f"--wait-for={_free(wait_for, 'wait_for')}")
    else:
        argv.append(f"--wait-for-gone={_free(wait_for_gone, 'wait_for_gone')}")
    argv.append(f"--wait-timeout={timeout_ms}")
    return timeout_ms


def _timeout(value: Any) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not 1000 <= value <= 30000:
        raise ValueError("timeout_ms must be 1000 to 30000")
    return value


def req_snapshot(
    *, app_name: str | None = None, window_id: str | None = None, surface: str = "window", mode: str = "controls",
    root: str | None = None, depth: int = 8, wait_for: str | None = None, wait_for_gone: str | None = None,
    timeout_ms: int = 8000,
) -> Request:
    timeout_ms = _timeout(timeout_ms)
    if not isinstance(depth, int) or isinstance(depth, bool) or not 1 <= depth <= 12:
        raise ValueError("depth must be 1 to 12")
    argv: list[str] = []
    _scope(argv, app_name, window_id, surface, root)
    if mode not in {"controls", "full", "overview"}:
        raise ValueError("mode is not valid")
    if mode == "overview" and surface != "window":
        mode = "controls"  # never --skeleton on a surface
    if mode == "controls":
        argv.append("-i")
    elif mode == "overview":
        argv.append("--skeleton")
    argv += ["--compact", f"--max-depth={depth}", f"--timeout-ms={timeout_ms}"]
    waited = _waits(argv, wait_for, wait_for_gone, root, timeout_ms)
    return _make("snapshot", argv, wall_s=(timeout_ms + waited) / 1000 + 5)


def req_find(
    *, app_name: str | None = None, window_id: str | None = None, surface: str = "window", root: str | None = None,
    role: str | None = None, name: str | None = None, text: str | None = None, value: str | None = None,
    exact: bool = False, limit: int = 20, timeout_ms: int = 8000,
) -> Request:
    timeout_ms = _timeout(timeout_ms)
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 50:
        raise ValueError("limit must be 1 to 50")
    if role is None and name is None and text is None and value is None:
        raise ValueError("Give at least one of role, name, text or value")
    argv: list[str] = []
    _scope(argv, app_name, window_id, surface, root)
    if role is not None:
        argv.append(f"--role={_check(_ROLE, role, 'role')}")
    for flag, item in (("name", name), ("text", text), ("value", value)):
        if item is not None:
            argv.append(f"--{flag}={_free(item, flag)}")
    if exact:
        argv.append("--exact")
    argv += [f"--limit={limit}", f"--timeout-ms={timeout_ms}"]
    return _make("find", argv, wall_s=timeout_ms / 1000 + 5)


def req_get(ref: str, prop: str = "text") -> Request:
    if prop not in {"text", "value"}:
        raise ValueError("property must be text or value")
    return _make("get", [_check(_REF, ref, "ref"), f"--property={prop}"], wall_s=10.0)


def req_act(
    action: str, ref: str, *, text: str | None = None, option: str | None = None, direction: str = "down", amount: int = 3,
) -> Request:
    if action not in _ACTION_COMMANDS:
        raise ValueError("action is not valid")
    command = _ACTION_COMMANDS[action]
    argv = [_check(_REF, ref, "ref")]
    stdin: bytes | None = None
    timeout_ms = 5000
    if action in {"type", "set_value"}:
        if not isinstance(text, str):
            raise ValueError("text is required")
        try:
            stdin = text.encode("utf-8")
        except UnicodeEncodeError:
            raise ValueError("text must be valid UTF-8") from None
        if b"\x00" in stdin:
            raise ValueError("text cannot contain NUL")
        limit = 10_000 if action == "type" else 128 * 1024
        if len(stdin) > limit:
            raise ValueError(f"text is over {limit} bytes")
        if action == "type":
            timeout_ms = 5000 + 30 * len(text)
        argv += ["--stdin", f"--timeout-ms={timeout_ms}"]
    elif text is not None:
        raise ValueError("text is only for set_value and type")
    elif action == "scroll":
        if direction not in {"up", "down", "left", "right"} or not isinstance(amount, int) or not 1 <= amount <= 20:
            raise ValueError("direction or amount is not valid")
        argv += [f"--direction={direction}", f"--amount={amount}", f"--timeout-ms={timeout_ms}"]
    elif action == "select":
        if not isinstance(option, str) or not 1 <= len(option) <= 200 or "\x00" in option or not option.isprintable():
            raise ValueError("option is required")
        argv += [f"--timeout-ms={timeout_ms}", "--", option]
    else:
        argv.append(f"--timeout-ms={timeout_ms}")
    if option is not None and action != "select":
        raise ValueError("option is only for select")
    return _make(command, argv, stdin=stdin, wall_s=timeout_ms / 1000 + 5, kind="action")


# --------------------------------------------------------------------------- status


@dataclass
class DesktopStatus:
    installed: bool = False
    source: str | None = None
    path: str | None = None
    version: str | None = None
    pinned_version: str = PINNED_VERSION
    verified: bool = False
    helper_ok: bool | None = None
    platform: str = ""
    arch: str = ""
    platform_supported: bool = True
    platform_reason: str | None = None
    envelope_version: str | None = None
    ready: bool = False
    permission: dict[str, Any] = field(default_factory=lambda: {"state": "unknown", "guidance": "", "grant_to": None})
    supported_surfaces: list[str] = field(default_factory=list)
    issues: list[dict[str, str]] = field(default_factory=list)

    @property
    def issue(self) -> str | None:
        return self.issues[0]["code"] if self.issues else None


_ISSUE_TEXT = {
    "not_installed": ("Desktop control is not installed.", "Install the pinned version of agent-desktop."),
    "version_mismatch": ("The installed program is not the version this build of TermX expects.", "Reinstall desktop control."),
    "envelope_unsupported": ("The program answers in a format this build does not understand.", "Reinstall desktop control."),
    "hash_mismatch": ("The file does not match what this version of TermX expects, so it was not run.", "Reinstall desktop control."),
    "not_pinned_build": ("A program with this name was found but it is not the pinned build, so it was not run.", "Install the pinned version."),
    "helper_missing": ("The helper program that belongs beside it is missing.", "Reinstall desktop control."),
    "permission_denied": ("This computer has not allowed TermX to read other applications.", "Grant the permission, then check again."),
    "no_accessibility_bus": ("No accessibility service is reachable from this session.", "Start TermX from a graphical session with at-spi2-core."),
    "pin_incomplete": ("No verified build is available for this computer in this version of TermX.", "Use a macOS arm64 computer, or wait for a later version."),
    "unsupported_platform": ("Desktop control is not available for this computer.", "None."),
    "spawn_failed": ("The program could not be started.", "Reinstall desktop control."),
    "configured_invalid": ("The configured program path is not usable.", "Fix TERMX_AGENT_DESKTOP."),
}


def _issue(code: str) -> dict[str, str]:
    message, action = _ISSUE_TEXT.get(code, (code, ""))
    return {"code": code, "message": message, "action": action}


def accessibility_label(mac_version: tuple[int, ...]) -> str:
    if mac_version and mac_version[0] >= 27:
        return "Device Control and Data Access (called Accessibility before macOS 27)"
    return "Accessibility"


def _grant_to() -> str | None:
    names = {"com.apple.Terminal": "Terminal", "com.googlecode.iterm2": "iTerm", "com.microsoft.VSCode": "Visual Studio Code"}
    return names.get(os.environ.get("__CFBundleIdentifier", ""))


# --------------------------------------------------------------------------- client


@dataclass
class _Candidate:
    path: Path
    source: str
    verified: bool
    helper: Path | None
    version: str
    signatures: dict[Path, tuple[int, int, int]]


def _signature(path: Path) -> tuple[int, int, int]:
    info = path.stat()
    return (info.st_size, info.st_mtime_ns, info.st_ino)


def _group_world_writable(path: Path) -> bool:
    return os.name != "nt" and bool(path.stat().st_mode & 0o022)


class AgentDesktopClient:
    def __init__(
        self,
        *,
        home_root: Path,
        pins: dict[str, Any] | None = None,
        platform: HostPlatform | None = None,
        launcher: tuple[str, ...] = (),
        setting: str | None = None,
        search_path: list[str] | None = None,
        project_dirs: tuple[str, ...] = (),
    ) -> None:
        self.home_root = Path(home_root)
        self.pins = pins if pins is not None else load_pins()
        self.platform = platform or detect_platform()
        self.launcher = tuple(launcher)  # test seam only; production never sets it
        self.setting = setting
        self.search_path = search_path if search_path is not None else os.environ.get("PATH", "").split(os.pathsep)
        self.project_dirs = project_dirs
        self._candidate: _Candidate | None = None
        self._issues: list[dict[str, str]] = []
        self._status: DesktopStatus | None = None
        self._status_at = 0.0
        self._sem = asyncio.Semaphore(READ_CONCURRENCY)
        self._action_locks: dict[str, asyncio.Lock] = {}
        self._children: dict[str, set[Any]] = {}
        self._tampered = False

    # -- layout
    @property
    def version(self) -> str:
        return str(self.pins["version"])

    @property
    def asset(self) -> dict[str, Any] | None:
        return (self.pins.get("assets") or {}).get(self.platform.key)

    @property
    def bin_dir(self) -> Path:
        return self.home_root / "bin" / self.version

    def pin_complete(self) -> bool:
        asset = self.asset
        return bool(asset and asset.get("files"))

    def _supported(self) -> tuple[bool, str | None]:
        if self.platform.os == "linux" and self.platform.glibc is False:
            return False, "unsupported_platform"
        if self.asset is None:
            return False, "unsupported_platform"
        if not self.pin_complete():
            return False, "pin_incomplete"
        return True, None

    @staticmethod
    def _private_dir(path: Path) -> Path:
        path.mkdir(parents=True, exist_ok=True)
        try:
            protect_private_path(path, directory=True)
        except OSError:
            pass
        return path

    def _ensure_root(self) -> None:
        for path in (self.home_root, self.home_root / "bin", self.home_root / "home"):
            self._private_dir(path)

    def ensure_layout(self) -> None:
        """Create the private state directories (``bin``, ``home``) the installer and the runner use."""
        self._ensure_root()

    def forget(self) -> None:
        """Drop the found candidate and the cached status so the next call discovers again (after install or remove)."""
        self._candidate, self._status, self._issues, self._tampered = None, None, [], False

    async def validate_setting(self, path: str) -> list[dict[str, str]]:
        """Issues that stop ``path`` from being used as the configured program; empty when it passes the version and helper checks.

        Runs on a throwaway client so the live candidate and cache are untouched.
        """
        probe = AgentDesktopClient(
            home_root=self.home_root, pins=self.pins, platform=self.platform, launcher=self.launcher,
            setting=path, search_path=[],
        )
        await probe._try_configured()
        if probe._candidate is not None and probe._candidate.source == "configured":
            return []
        return list(probe._issues) or [_issue("configured_invalid")]

    def home_for(self, owner: str) -> Path:
        self._ensure_root()
        name = hashlib.sha256(owner.encode()).hexdigest()[:16]
        home = self._private_dir(self.home_root / "home" / name)
        self._check_home(home)
        return home

    @staticmethod
    def _check_home(home: Path) -> None:
        bad = (
            not home.is_absolute()
            or home.is_symlink()
            or os.path.isjunction(home)
            or not home.is_dir()
            or home.resolve() != home.absolute().resolve()
        )
        if not bad and os.name != "nt":
            info = home.stat()
            bad = info.st_uid != os.geteuid() or bool(info.st_mode & 0o077)
        if bad:
            raise AgentDesktopFailure("not_ready", "spawn_failed")

    @staticmethod
    def session_id(task_id: str) -> str:
        return "t" + hashlib.sha256(task_id.encode()).hexdigest()[:24]

    def cleanup_task(self, task_id: str) -> None:
        """Remove the task's session directory (refmaps hold element names)."""
        sid = self.session_id(task_id)
        for home in (self.home_root / "home").glob("*"):
            self._remove_session(home, sid)

    def _remove_session(self, home: Path, sid: str) -> None:
        sessions = home / "sessions"
        target = sessions / sid
        if not SESSION_RE.fullmatch(sid) or not target.exists() or target.is_symlink() or os.path.isjunction(target):
            return
        try:
            if target.resolve().parent != sessions.resolve():
                return
            shutil.rmtree(target, ignore_errors=True)
        except OSError:
            pass

    def sweep(self, older_than_s: float = 86400.0) -> None:
        cutoff = time.time() - older_than_s
        for home in (self.home_root / "home").glob("*"):
            for entry in (home / "sessions").glob("t*"):
                try:
                    if SESSION_RE.fullmatch(entry.name) and entry.stat().st_mtime < cutoff:
                        self._remove_session(home, entry.name)
                except OSError:
                    continue

    # -- discovery
    def _expected_hash(self, name: str) -> str | None:
        files = (self.asset or {}).get("files") or {}
        return files.get(name)

    def _hash_ok(self, path: Path) -> bool:
        expected = self._expected_hash(path.name)
        return expected is not None and hmac.compare_digest(sha256_file(path), expected)

    def _helper_path(self, binary: Path) -> Path | None:
        return binary.with_name("agent-desktop-macos-helper") if self.platform.os == "macos" else None

    def _file_ok(self, path: Path, *, strict_dir: bool) -> bool:
        try:
            info = path.lstat()
        except OSError:
            return False
        if not stat.S_ISREG(info.st_mode):
            return False
        if os.name != "nt":
            if info.st_uid not in {os.geteuid(), 0} or info.st_mode & 0o022:
                return False
            if strict_dir and _group_world_writable(path.parent):
                return False
        return True

    async def discover(self) -> None:
        """Find and fully validate a candidate. Hash first, execute second."""
        self._candidate, self._issues, self._tampered = None, [], False
        supported, reason = self._supported()
        if self.setting:
            await self._try_configured()
        if self._candidate is None and supported:
            await self._try_managed()
            if self._candidate is None:
                await self._try_path()
        if self._candidate is not None:
            self._issues = []  # a fallen-through candidate's issue is moot once another one works
        else:
            if len(self._issues) > 1:
                self._issues = [item for item in self._issues if item["code"] != "not_installed"]
            if not self._issues or not supported and reason:
                self._issues = [_issue(reason or "not_installed")]
        self._status = None

    def _note(self, code: str) -> None:
        if all(item["code"] != code for item in self._issues):
            self._issues.append(_issue(code))

    async def _try_configured(self) -> None:
        path = Path(self.setting or "")
        if not path.is_absolute() or not self._file_ok(path, strict_dir=True):
            self._note("configured_invalid")
            return
        helper = self._helper_path(path)
        if helper is not None and not helper.is_file():
            self._note("helper_missing")
            return
        await self._accept(path, "configured", hashed=False)

    async def _try_managed(self) -> None:
        path = self.bin_dir / self.platform.binary_name
        if not path.exists():
            self._note("not_installed")
            return
        await self._accept(path, "managed", hashed=True)

    async def _try_path(self) -> None:
        entries = [
            entry for entry in self.search_path
            if entry and os.path.isabs(entry) and entry not in self.project_dirs and Path(entry) != Path.cwd()
        ]
        found = shutil.which(self.platform.binary_name, path=os.pathsep.join(entries)) if entries else None
        if not found:
            return
        source = Path(found)
        helper = self._helper_path(source)
        if not self._file_ok(source, strict_dir=False) or not self._hash_ok(source):
            self._note("not_pinned_build")
            return
        if helper is not None and (not helper.is_file() or not self._hash_ok(helper)):
            self._note("helper_missing")
            return
        # Adopt by copy: the PATH file is never executed, the private copy is.
        self._ensure_root()
        target_dir = self._private_dir(self.bin_dir)
        for item in (source, helper):
            if item is None:
                continue
            target = target_dir / item.name
            temp = target.with_name(target.name + ".new")
            shutil.copyfile(item, temp)
            temp.chmod(0o700)
            os.replace(temp, target)
        await self._accept(target_dir / self.platform.binary_name, "path", hashed=True)

    async def _accept(self, path: Path, source: str, *, hashed: bool) -> None:
        if not self._file_ok(path, strict_dir=source != "managed"):
            self._note("configured_invalid" if source == "configured" else "hash_mismatch")
            return
        helper = self._helper_path(path)
        if hashed:
            if not self.pin_complete():
                self._note("pin_incomplete")
                return
            if not self._hash_ok(path):
                self._note("hash_mismatch")
                return
            if helper is not None and (not helper.is_file() or not self._hash_ok(helper)):
                self._note("helper_missing")
                return
        try:
            envelope = await self._execute(path, req_version(), owner="host", task_id="", helper=helper)
        except AgentDesktopFailure as exc:
            self._note(exc.code if exc.code in _ISSUE_TEXT else "spawn_failed")
            return
        data = envelope.data if isinstance(envelope.data, dict) else {}
        arch = str(data.get("target") or "")
        arch_ok = arch in {"aarch64", "arm64"} if self.platform.arch == "arm64" else arch in {"x86_64", "amd64"}
        if not envelope.ok or data.get("version") != self.version or data.get("os") != self.platform.os or not arch_ok:
            self._note("version_mismatch")
            return
        signatures = {path: _signature(path)}
        if helper is not None:
            signatures[helper] = _signature(helper)
        self._candidate = _Candidate(path, source, hashed, helper, str(data["version"]), signatures)

    def _recheck(self) -> None:
        cand = self._candidate
        if cand is None:
            raise AgentDesktopFailure("not_ready", self._issues[0]["code"] if self._issues else "not_installed")
        try:
            for path, known in cand.signatures.items():
                if _signature(path) != known:
                    if cand.verified and not self._hash_ok(path):
                        raise OSError("changed")
                    if not cand.verified:
                        raise OSError("changed")
                    cand.signatures[path] = _signature(path)
        except OSError:
            self._candidate, self._tampered = None, True
            self._issues = [_issue("hash_mismatch")]
            self._status = None
            raise AgentDesktopFailure("hash_mismatch", "hash_mismatch") from None

    # -- running
    def _env(self, home: Path, helper: Path | None) -> dict[str, str]:
        env = {key: os.environ[key] for key in ENV_ALLOW if key in os.environ}
        env["AGENT_DESKTOP_HOME"] = str(home)
        if helper is not None:
            env["AGENT_DESKTOP_MACOS_HELPER_PATH"] = str(helper)
        return env

    async def run(
        self, req: Request, *, task_id: str = "", owner: str = "host", cancel: asyncio.Event | None = None
    ) -> Envelope:
        if self._tampered:
            raise AgentDesktopFailure("hash_mismatch", "hash_mismatch")
        if self._candidate is None:
            await self.discover()
        self._recheck()
        cand = self._candidate
        assert cand is not None
        if req.session and not task_id:
            raise ValueError("a session request needs a task")
        if req.kind == "meta":
            return await self._execute(cand.path, req, owner=owner, task_id=task_id, helper=cand.helper, cancel=cancel)
        async with self._sem:
            if req.kind == "action":
                async with self._action_locks.setdefault(owner, asyncio.Lock()):
                    return await self._execute(cand.path, req, owner=owner, task_id=task_id, helper=cand.helper, cancel=cancel)
            return await self._execute(cand.path, req, owner=owner, task_id=task_id, helper=cand.helper, cancel=cancel)

    async def _execute(
        self, binary: Path, req: Request, *, owner: str, task_id: str, helper: Path | None,
        cancel: asyncio.Event | None = None,
    ) -> Envelope:
        home = self.home_for(owner)
        argv = [req.command]
        if req.session:
            argv.append(f"--session={self.session_id(task_id)}")
        argv += req.argv
        _assert_safe(req.command, tuple(argv[1:]))
        started = time.monotonic()
        raw, code = await self._spawn(
            [*self.launcher, str(binary), *argv], home, self._env(home, helper), req.stdin, req.wall_s, cancel, task_id
        )
        envelope = parse_envelope(raw, code)
        log.info(
            "command=%s exit=%s duration_ms=%d stdout_bytes=%d ok=%s code=%s",
            req.command, code, int((time.monotonic() - started) * 1000), len(raw), envelope.ok,
            envelope.error.code if envelope.error else "",
        )
        return envelope

    async def _spawn(
        self, cmd: list[str], home: Path, env: dict[str, str], stdin: bytes | None, wall_s: float,
        cancel: asyncio.Event | None, task_id: str,
    ) -> tuple[bytes, int]:
        options: dict[str, Any] = {
            "cwd": str(home), "env": env, "stdin": subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
            "stdout": subprocess.PIPE, "stderr": subprocess.DEVNULL,
        }
        if os.name == "nt":
            options["creationflags"] = subprocess.CREATE_NO_WINDOW
        try:
            proc = await asyncio.create_subprocess_exec(*cmd, **options)
        except NotImplementedError:
            return await self._spawn_thread(cmd, options, stdin, wall_s, cancel, task_id)
        except OSError:
            raise AgentDesktopFailure("not_ready", "spawn_failed") from None
        children = self._children.setdefault(task_id, set())
        children.add(proc)
        tasks: list[asyncio.Task[Any]] = []
        try:
            async def feed() -> None:
                if stdin is None or proc.stdin is None:
                    return
                try:
                    proc.stdin.write(stdin)
                    await proc.stdin.drain()
                except (BrokenPipeError, ConnectionResetError):
                    pass
                finally:
                    try:
                        proc.stdin.close()
                    except OSError:
                        pass

            async def drain() -> bytes:
                assert proc.stdout is not None
                chunks: list[bytes] = []
                total = 0
                while chunk := await proc.stdout.read(65536):
                    total += len(chunk)
                    if total > MAX_STDOUT:
                        raise AgentDesktopFailure("output_too_large")
                    chunks.append(chunk)
                await proc.wait()
                return b"".join(chunks)

            feeder = asyncio.create_task(feed())
            work = asyncio.create_task(drain())
            tasks += [feeder, work]
            waiting: set[asyncio.Task[Any]] = {work}
            if cancel is not None:
                canceller = asyncio.create_task(cancel.wait())
                tasks.append(canceller)
                waiting.add(canceller)
            done, _ = await asyncio.wait(waiting, timeout=wall_s, return_when=asyncio.FIRST_COMPLETED)
            if work in done:
                return work.result(), int(proc.returncode or 0)
            await self._stop(proc)
            if cancel is not None and cancel.is_set():
                raise AgentDesktopFailure("cancelled", delivery_unknown=True)
            raise AgentDesktopFailure("timeout", delivery_unknown=True)
        finally:
            for task in tasks:
                task.cancel()
            if proc.returncode is None:
                await self._stop(proc)
            children.discard(proc)

    @staticmethod
    async def _stop(proc: Any) -> None:
        try:
            proc.terminate()
        except ProcessLookupError:
            return
        try:
            await asyncio.wait_for(proc.wait(), 1.0)
        except TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                return
            await proc.wait()

    async def _spawn_thread(
        self, cmd: list[str], options: dict[str, Any], stdin: bytes | None, wall_s: float,
        cancel: asyncio.Event | None, task_id: str,
    ) -> tuple[bytes, int]:
        """Windows under a selector loop cannot use asyncio subprocesses: run Popen in a thread."""
        try:
            proc: Any = subprocess.Popen(cmd, **options)
        except OSError:
            raise AgentDesktopFailure("not_ready", "spawn_failed") from None
        children = self._children.setdefault(task_id, set())
        children.add(proc)
        stop_reason: list[str] = []

        def work() -> tuple[bytes, int]:
            try:
                out, _ = proc.communicate(stdin, timeout=wall_s)
            except subprocess.TimeoutExpired:
                stop_reason.append("timeout")
                proc.kill()
                proc.communicate()
                return b"", -1
            return bytes(out or b""), int(proc.returncode or 0)

        async def watch() -> None:
            while proc.poll() is None:
                if cancel is not None and cancel.is_set():
                    stop_reason.append("cancelled")
                    proc.kill()
                    return
                await asyncio.sleep(0.05)

        watcher = asyncio.create_task(watch())
        try:
            out, code = await asyncio.to_thread(work)
        finally:
            watcher.cancel()
            if proc.poll() is None:
                proc.kill()
            children.discard(proc)
        if "cancelled" in stop_reason:
            raise AgentDesktopFailure("cancelled", delivery_unknown=True)
        if "timeout" in stop_reason:
            raise AgentDesktopFailure("timeout", delivery_unknown=True)
        if len(out) > MAX_STDOUT:
            raise AgentDesktopFailure("output_too_large")
        return out, code

    def kill_task(self, task_id: str) -> None:
        for proc in list(self._children.get(task_id, ())):
            try:
                proc.terminate()
            except (ProcessLookupError, OSError):
                pass

    def kill_all(self) -> None:
        for task_id in list(self._children):
            self.kill_task(task_id)

    # -- status
    def ready_cached(self) -> bool:
        return bool(self._status and self._status.ready)

    def status_cached(self) -> DesktopStatus | None:
        return self._status

    def mark_permission_denied(self) -> None:
        if self._status is not None:
            self._status.ready = False
            self._status.permission = {**self._status.permission, "state": "denied"}
            if all(item["code"] != "permission_denied" for item in self._status.issues):
                self._status.issues.append(_issue("permission_denied"))

    async def ensure_fresh(self, max_age: float = STATUS_TTL_S) -> DesktopStatus:
        """Refresh the cached status when older than ``max_age`` seconds. Never raises."""
        if self._status is not None and time.monotonic() - self._status_at < max_age:
            return self._status
        try:
            return await self.status(refresh=True)
        except Exception:  # noqa: BLE001 - availability checks must not fail a turn
            return self._status or DesktopStatus(platform=self.platform.os, arch=self.platform.arch)

    async def status(self, refresh: bool = False) -> DesktopStatus:
        if not refresh and self._status is not None and time.monotonic() - self._status_at < STATUS_TTL_S:
            return self._status
        supported, reason = self._supported()
        if self._candidate is None or refresh:
            await self.discover()
        cand = self._candidate
        status = DesktopStatus(platform=self.platform.os, arch=self.platform.arch, platform_supported=supported or bool(self.setting),
                               platform_reason=None if supported else reason)
        status.issues = list(self._issues)
        permission = {"state": "unknown", "guidance": "", "grant_to": None}
        if cand is not None:
            status.installed, status.source, status.path = True, cand.source, str(cand.path)
            status.version, status.verified = cand.version, cand.verified
            status.helper_ok = True if cand.helper is not None else None
            status.envelope_version = ENVELOPE_VERSION
            try:
                envelope = await self.run(req_status())
            except AgentDesktopFailure as exc:
                status.issues.append(_issue(exc.code if exc.code in _ISSUE_TEXT else "spawn_failed"))
            else:
                data = envelope.data if isinstance(envelope.data, dict) else {}
                status.supported_surfaces = [str(item) for item in data.get("supported_surfaces") or []]
                permission = self._permission(data, status)
        status.permission = permission
        status.ready = cand is not None and not status.issues and permission["state"] in {"granted", "not_required"}
        self._status, self._status_at = status, time.monotonic()
        return status

    def _permission(self, data: dict[str, Any], status: DesktopStatus) -> dict[str, Any]:
        system = self.platform.os
        if system == "macos":
            state = str(((data.get("permissions") or {}).get("accessibility") or {}).get("state") or "unknown")
            grant_to = _grant_to()
            if state == "granted":
                return {"state": "granted", "guidance": "", "grant_to": grant_to}
            status.issues.append(_issue("permission_denied"))
            label = accessibility_label(self.platform.mac_version)
            target = grant_to or "the application that started TermX (for example Terminal or the TermX app)"
            guidance = (
                f"Allow {target} in System Settings, Privacy & Security, {label}, then choose Check again. "
                "If System Settings lists the agent-desktop program separately, allow it too."
            )
            return {"state": "denied" if state == "denied" else "unknown", "guidance": guidance, "grant_to": grant_to}
        if system == "linux":
            if not status.supported_surfaces:
                status.issues.append(_issue("no_accessibility_bus"))
                return {"state": "unavailable", "grant_to": None, "guidance": (
                    "Desktop control needs a graphical session and the accessibility service (at-spi2-core, Ubuntu 24.04 or newer). "
                    "Wayland-only sessions cannot scroll or focus. Chromium and Qt apps may need their accessibility option turned on.")}
            return {"state": "not_required", "guidance": "", "grant_to": None}
        return {"state": "not_required", "grant_to": None, "guidance": (
            "Elevated windows cannot be controlled from a normal TermX process. A locked screen or another user's session is not supported.")}


def parse_envelope(raw: bytes, exit_code: int) -> Envelope:
    """Exactly one JSON object; anything else is BadOutput and the raw bytes are dropped."""
    try:
        body = json.loads(raw.decode("utf-8"))
    except (ValueError, RecursionError, MemoryError):
        raise AgentDesktopFailure("bad_output") from None
    if not isinstance(body, dict) or not isinstance(body.get("ok"), bool) or not isinstance(body.get("version"), str):
        raise AgentDesktopFailure("bad_output")
    if body["version"] != ENVELOPE_VERSION:
        raise AgentDesktopFailure("envelope_unsupported", "envelope_unsupported")
    error = None
    if not body["ok"]:
        raw_error: dict[str, Any] = body["error"] if isinstance(body.get("error"), dict) else {}
        disposition: dict[str, Any] = raw_error["disposition"] if isinstance(raw_error.get("disposition"), dict) else {}
        details: dict[str, Any] = raw_error["details"] if isinstance(raw_error.get("details"), dict) else {}
        error = CliError(
            code=str(raw_error.get("code") or "INTERNAL")[:40],
            retry_safe=disposition.get("retry") == "safe",
            delivery=str(disposition["delivery"]) if isinstance(disposition.get("delivery"), str) else None,
            details=details,
        )
    return Envelope(body["ok"], str(body.get("command") or ""), body.get("data"), error, body["version"])


# --------------------------------------------------------------------------- process-wide client

_client: AgentDesktopClient | None = None


def get_client() -> AgentDesktopClient:
    global _client
    if _client is None:
        from termx.desktop.agent_desktop_setting import load_setting

        home_root = config_dir() / "desktop-control"
        # The environment wins; otherwise the path the owner saved through the host-admin API. Never a plain config key.
        _client = AgentDesktopClient(home_root=home_root, setting=os.environ.get(SETTING_ENV) or load_setting(home_root))
    return _client


def set_client(client: AgentDesktopClient | None) -> None:
    global _client
    _client = client
