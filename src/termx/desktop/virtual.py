from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from typing import Any

from termx.desktop.paths import resolve_macos_helper

LEASE_GRACE_S = 30


class VirtualDisplayError(RuntimeError):
    pass


class VirtualAdapter:
    id = ""

    def available(self) -> bool:
        return False

    def can_create(self) -> bool:
        return False

    def reason(self) -> str:
        return unsupported_reason()

    def create(self, display_id: str, width: int, height: int, dpr: float, refresh_hz: int) -> str:
        raise VirtualDisplayError(self.reason())

    def destroy(self, name: str) -> None:
        raise VirtualDisplayError(self.reason())


def unsupported_reason() -> str:
    if sys.platform == "darwin":
        return "macOS virtual display requires the signed host helper (not installed)"
    if sys.platform.startswith("linux"):
        return "No compositor virtual-output adapter is available yet"
    if sys.platform == "win32":
        return (
            "Windows cannot create virtual displays from user space. Install an IddCx-based "
            "virtual display driver, then set TERMX_WINDOWS_VIRTUAL_DISPLAY_CREATE_CMD "
            "(and _DESTROY_CMD) to its command line, or use the driver's own tool. "
            "Driver-created displays are listed, mirrored, and controlled automatically."
        )
    return "Virtual displays are not supported on this OS"


def _run(argv: list[str], timeout: float = 20) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError as exc:
        raise VirtualDisplayError(str(exc)) from exc
    except subprocess.TimeoutExpired as exc:
        raise VirtualDisplayError(f"{argv[0]} timed out") from exc


def _fail(proc: subprocess.CompletedProcess[str], fallback: str) -> None:
    if proc.returncode != 0:
        raise VirtualDisplayError((proc.stderr or proc.stdout or fallback).strip())


def _help_text(binary: str) -> str | None:
    try:
        proc = subprocess.run([binary, "--help"], capture_output=True, text=True, timeout=10)
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    return f"{proc.stdout or ''}{proc.stderr or ''}"


class XrandrAdapter(VirtualAdapter):
    id = "xrandr"

    def available(self) -> bool:
        return (
            sys.platform.startswith("linux")
            and bool(os.environ.get("DISPLAY"))
            and shutil.which("xrandr") is not None
            and not os.environ.get("WAYLAND_DISPLAY")
        )

    def can_create(self) -> bool:
        return self.available()

    def reason(self) -> str:
        if not sys.platform.startswith("linux"):
            return unsupported_reason()
        if not shutil.which("xrandr"):
            return "xrandr is not installed"
        if not os.environ.get("DISPLAY"):
            return "DISPLAY is not set"
        if os.environ.get("WAYLAND_DISPLAY"):
            return "xrandr cannot create a host output on Wayland"
        return "xrandr virtual monitor is available"

    def create(self, display_id: str, width: int, height: int, dpr: float, refresh_hz: int) -> str:
        name = f"termx-{display_id}"
        offset = self._offset()
        self.last_geometry = {"x": offset, "y": 0}
        geom = f"{width}/96x{height}/96+{offset}+0"
        proc = _run(["xrandr", "--setmonitor", name, geom, "none"])
        _fail(proc, "xrandr --setmonitor failed")
        return name

    def destroy(self, name: str) -> None:
        proc = _run(["xrandr", "--delmonitor", name])
        _fail(proc, "xrandr --delmonitor failed")

    def _offset(self) -> int:
        proc = _run(["xrandr", "--listmonitors"])
        if proc.returncode != 0:
            return 0
        offset = 0
        for line in proc.stdout.splitlines()[1:]:
            match = re.search(r"(\d+)/\d+x(\d+)/\d+\+(\d+)\+\d+", line)
            if match:
                offset = max(offset, int(match.group(1)) + int(match.group(3)))
        return offset


class HyprlandAdapter(VirtualAdapter):
    id = "hyprland"

    def available(self) -> bool:
        return (
            sys.platform.startswith("linux")
            and bool(os.environ.get("WAYLAND_DISPLAY"))
            and shutil.which("hyprctl") is not None
        )

    def can_create(self) -> bool:
        return self.available()

    def reason(self) -> str:
        if not sys.platform.startswith("linux"):
            return unsupported_reason()
        if not shutil.which("hyprctl"):
            return "hyprctl is not installed"
        if not os.environ.get("WAYLAND_DISPLAY"):
            return "WAYLAND_DISPLAY is not set"
        return "hyprland headless output is available"

    def create(self, display_id: str, width: int, height: int, dpr: float, refresh_hz: int) -> str:
        name = f"termx-{display_id}"
        proc = _run(["hyprctl", "output", "create", "headless", name])
        _fail(proc, "hyprctl output create failed")
        return name

    def destroy(self, name: str) -> None:
        proc = _run(["hyprctl", "output", "remove", name])
        if proc.returncode == 0:
            return
        fallback = _run(["hyprctl", "output", "destroy", name])
        if fallback.returncode == 0:
            return
        raise VirtualDisplayError(
            (proc.stderr or fallback.stderr or proc.stdout or fallback.stdout or "hyprctl output remove failed").strip()
        )


class SwayAdapter(VirtualAdapter):
    id = "sway"

    def available(self) -> bool:
        return (
            sys.platform.startswith("linux")
            and bool(os.environ.get("WAYLAND_DISPLAY"))
            and shutil.which("swaymsg") is not None
        )

    def can_create(self) -> bool:
        return self.available()

    def reason(self) -> str:
        if not sys.platform.startswith("linux"):
            return unsupported_reason()
        if not shutil.which("swaymsg"):
            return "swaymsg is not installed"
        if not os.environ.get("WAYLAND_DISPLAY"):
            return "WAYLAND_DISPLAY is not set"
        return "sway headless output is available"

    def create(self, display_id: str, width: int, height: int, dpr: float, refresh_hz: int) -> str:
        before = self._output_names()
        proc = _run(["swaymsg", "create_output"])
        _fail(proc, "swaymsg create_output failed")
        added = [name for name in self._output_names() if name not in before]
        if added:
            return added[0]
        match = re.search(r"(HEADLESS-\d+|termx-[A-Za-z0-9]+)", proc.stdout or "")
        if match:
            return match.group(1)
        return f"termx-{display_id}"

    def destroy(self, name: str) -> None:
        try:
            _run(["swaymsg", "output", name, "unplug"])
        except VirtualDisplayError:
            return

    def _output_names(self) -> set[str]:
        try:
            proc = subprocess.run(
                ["swaymsg", "-t", "get_outputs"],
                capture_output=True,
                text=True,
                timeout=20,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            return set()
        if proc.returncode != 0:
            return set()
        return set(re.findall(r'"name"\s*:\s*"([^"]+)"', proc.stdout or ""))


class GnomeAdapter(VirtualAdapter):
    id = "gdctl"

    def _bin(self) -> str | None:
        return shutil.which("gdctl") or shutil.which("gnome-randr")

    def _help_has_virtual(self) -> bool:
        cached = getattr(self, "_virtual_help", None)
        if cached is not None:
            return cached
        binary = self._bin()
        if not binary:
            self._virtual_help = False
            return False
        text = _help_text(binary)
        if text is None:
            self._virtual_help = False
            return False
        self._virtual_help = "virtual" in text.lower()
        return self._virtual_help

    def available(self) -> bool:
        if not sys.platform.startswith("linux") or self._bin() is None:
            return False
        self._help_has_virtual()
        return True

    def can_create(self) -> bool:
        return self.available() and self._help_has_virtual()

    def reason(self) -> str:
        binary = self._bin()
        if not binary:
            return "gdctl/gnome-randr is not installed"
        if not self._help_has_virtual():
            return f"{os.path.basename(binary)} has no documented virtual-output create command"
        return f"{os.path.basename(binary)} virtual output is available"

    def create(self, display_id: str, width: int, height: int, dpr: float, refresh_hz: int) -> str:
        if not self.can_create():
            raise VirtualDisplayError(self.reason())
        binary = self._bin()
        if not binary:
            raise VirtualDisplayError(self.reason())
        name = f"termx-{display_id}"
        proc = _run(
            [
                binary,
                "set",
                "--virtual-monitor",
                f"{width}x{height}@{int(refresh_hz)}",
                "--name",
                name,
            ]
        )
        _fail(proc, f"{os.path.basename(binary)} virtual create failed")
        return (proc.stdout or "").strip() or name

    def destroy(self, name: str) -> None:
        if not self.can_create():
            raise VirtualDisplayError(self.reason())
        binary = self._bin()
        if not binary:
            raise VirtualDisplayError(self.reason())
        proc = _run([binary, "set", "--off", name])
        _fail(proc, f"{os.path.basename(binary)} virtual destroy failed")


class KscreenAdapter(VirtualAdapter):
    id = "kscreen-doctor"

    def _bin(self) -> str | None:
        return shutil.which("kscreen-doctor")

    def _help_has_create(self) -> bool:
        cached = getattr(self, "_create_help", None)
        if cached is not None:
            return cached
        binary = self._bin()
        if not binary:
            self._create_help = False
            return False
        text = _help_text(binary)
        if text is None:
            self._create_help = False
            return False
        lowered = text.lower()
        self._create_help = any(token in lowered for token in ("virtual", "dummy", "create"))
        return self._create_help

    def available(self) -> bool:
        if not sys.platform.startswith("linux") or self._bin() is None:
            return False
        self._help_has_create()
        return True

    def can_create(self) -> bool:
        return self.available() and self._help_has_create()

    def reason(self) -> str:
        if not self._bin():
            return "kscreen-doctor is not installed"
        if not self._help_has_create():
            return "kscreen-doctor has no documented virtual-output create command"
        return "kscreen-doctor virtual output is available"

    def create(self, display_id: str, width: int, height: int, dpr: float, refresh_hz: int) -> str:
        if not self.can_create():
            raise VirtualDisplayError(self.reason())
        binary = self._bin()
        if not binary:
            raise VirtualDisplayError(self.reason())
        name = f"termx-{display_id}"
        proc = _run([binary, f"output.{name}.mode.{width}x{height}@{int(refresh_hz)}"])
        _fail(proc, self.reason())
        return name

    def destroy(self, name: str) -> None:
        binary = self._bin()
        if not binary:
            raise VirtualDisplayError(self.reason())
        proc = _run([binary, f"output.{name}.disable"])
        _fail(proc, self.reason())


def _is_termx_helper(binary: str) -> bool:
    return os.path.basename(binary).startswith("termx-virtual-display")


class HelperAdapter(VirtualAdapter):
    id = "helper"
    helpers = ("termx-virtual-display", "BetterDisplay", "deskpad")

    def __init__(self) -> None:
        self._procs: dict[str, subprocess.Popen[str]] = {}

    def _bin(self) -> str | None:
        found = resolve_macos_helper("termx-virtual-display", "TERMX_VIRTUAL_DISPLAY_BIN")
        if found:
            return found
        for name in self.helpers:
            if name == "termx-virtual-display":
                continue
            path = shutil.which(name)
            if path:
                return path
        return None

    def available(self) -> bool:
        return sys.platform == "darwin" and self._bin() is not None

    def can_create(self) -> bool:
        return self.available()

    def reason(self) -> str:
        if sys.platform == "darwin" and not self._bin():
            return "macOS virtual display requires the signed host helper (not installed)"
        if not self._bin():
            return unsupported_reason()
        return "macOS virtual display helper is available"

    def create(self, display_id: str, width: int, height: int, dpr: float, refresh_hz: int) -> str:
        binary = self._bin()
        if not binary:
            raise VirtualDisplayError(self.reason())
        if not _is_termx_helper(binary):
            proc = _run(
                [
                    binary,
                    "create",
                    "--width",
                    str(width),
                    "--height",
                    str(height),
                    "--dpr",
                    str(dpr),
                    "--refresh",
                    str(refresh_hz),
                    "--id",
                    display_id,
                ]
            )
            _fail(proc, f"{os.path.basename(binary)} create failed")
            return (proc.stdout or "").strip() or f"termx-{display_id}"
        # The termx helper keeps the CGVirtualDisplay alive for the lifetime of
        # its process, so it runs until destroyed or its parent exits.
        process = subprocess.Popen(
            [
                binary,
                "create",
                "--width",
                str(width),
                "--height",
                str(height),
                "--dpr",
                str(dpr),
                "--refresh",
                str(refresh_hz),
                "--id",
                display_id,
                "--parent-pid",
                str(os.getpid()),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            text=True,
        )
        line = _read_line(process, timeout=15)
        if not line:
            if process.poll() is not None:
                error = ""
                try:
                    if process.stderr is not None:
                        error = (process.stderr.read() or "").strip()
                except (OSError, ValueError):
                    error = ""
                raise VirtualDisplayError(error or "termx-virtual-display create failed")
            process.terminate()
            raise VirtualDisplayError("termx-virtual-display timed out")
        name = line.strip() or f"termx-{display_id}"
        if process.poll() is None:
            # Real CGVirtualDisplay: the helper holds the display until killed.
            self._procs[name] = process
            return name
        # One-shot helper (older bundle or a third-party CLI): the display is
        # managed by the helper itself and destroyed through its CLI.
        if process.returncode not in (0, None):
            error = ""
            try:
                if process.stderr is not None:
                    error = (process.stderr.read() or "").strip()
            except (OSError, ValueError):
                error = ""
            raise VirtualDisplayError(error or "termx-virtual-display create failed")
        return name

    def destroy(self, name: str) -> None:
        process = self._procs.pop(name, None)
        if process is not None:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    try:
                        process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        pass
            return
        binary = self._bin()
        if not binary:
            raise VirtualDisplayError(self.reason())
        if not _is_termx_helper(binary):
            proc = _run([binary, "destroy", name])
            _fail(proc, f"{os.path.basename(binary)} destroy failed")
            return
        proc = _run([binary, "destroy", name])
        _fail(proc, f"{os.path.basename(binary)} destroy failed")


def _read_line(process: subprocess.Popen[str], timeout: float) -> str | None:
    import select

    if process.stdout is None:
        return None
    try:
        ready, _, _ = select.select([process.stdout], [], [], timeout)
    except (OSError, ValueError):
        return None
    if not ready:
        return None
    try:
        return process.stdout.readline()
    except (OSError, ValueError):
        return None


class WindowsVirtualAdapter(VirtualAdapter):
    """IddCx driver bridge: creation delegates to a user-configured command hook."""

    id = "windows-idd"
    create_env = "TERMX_WINDOWS_VIRTUAL_DISPLAY_CREATE_CMD"
    destroy_env = "TERMX_WINDOWS_VIRTUAL_DISPLAY_DESTROY_CMD"

    def _create_command(self) -> str:
        return (os.environ.get(self.create_env) or "").strip()

    def _destroy_command(self) -> str:
        return (os.environ.get(self.destroy_env) or "").strip()

    def available(self) -> bool:
        return sys.platform == "win32" and bool(self._create_command())

    def can_create(self) -> bool:
        return self.available()

    def reason(self) -> str:
        if sys.platform != "win32":
            return unsupported_reason()
        if not self._create_command():
            return unsupported_reason()
        return "IddCx virtual display command hook is configured"

    def create(self, display_id: str, width: int, height: int, dpr: float, refresh_hz: int) -> str:
        name = f"termx-{display_id}"
        _run_shell(
            self._create_command(),
            {
                "id": display_id,
                "name": name,
                "width": width,
                "height": height,
                "dpr": dpr,
                "refresh": refresh_hz,
            },
        )
        return name

    def destroy(self, name: str) -> None:
        command = self._destroy_command()
        if not command:
            raise VirtualDisplayError(
                f"{self.destroy_env} is not set; remove the display with the driver's own tool"
            )
        _run_shell(command, {"name": name, "id": name})


def _run_shell(template: str, values: dict[str, Any]) -> None:
    import shlex

    try:
        command = template.format(**{key: str(value) for key, value in values.items()})
    except KeyError as exc:
        raise VirtualDisplayError(f"unknown placeholder {exc} in virtual display command") from exc
    argv = shlex.split(command, posix=(os.name != "nt"))
    if not argv:
        raise VirtualDisplayError("empty virtual display command")
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=60)
    except FileNotFoundError as exc:
        raise VirtualDisplayError(str(exc)) from exc
    except subprocess.TimeoutExpired as exc:
        raise VirtualDisplayError(f"{argv[0]} timed out") from exc
    if proc.returncode != 0:
        raise VirtualDisplayError((proc.stderr or proc.stdout or f"{argv[0]} failed").strip())


_ADAPTERS: list[VirtualAdapter] = [
    HelperAdapter(),
    HyprlandAdapter(),
    SwayAdapter(),
    XrandrAdapter(),
    GnomeAdapter(),
    KscreenAdapter(),
    WindowsVirtualAdapter(),
]
_active: dict[str, dict[str, Any]] = {}
_impls: dict[str, VirtualAdapter] = {}


def active_adapter() -> VirtualAdapter | None:
    for adapter in _ADAPTERS:
        if adapter.available():
            return adapter
    return None


def _adapter_by_id(adapter_id: str) -> VirtualAdapter | None:
    for adapter in _ADAPTERS:
        if adapter.id == adapter_id:
            return adapter
    return None


def _resolve_adapter(force_adapter: VirtualAdapter | str | None = None) -> VirtualAdapter | None:
    if isinstance(force_adapter, str):
        return _adapter_by_id(force_adapter)
    if force_adapter is not None:
        return force_adapter
    return active_adapter()


def list_virtual_displays() -> list[dict[str, Any]]:
    return [
        {
            "id": display_id,
            "name": rec["name"],
            "width": rec["width"],
            "height": rec["height"],
            "x": rec.get("x", 0),
            "y": rec.get("y", 0),
            "adapter": rec["adapter"],
            "owner": rec.get("owner"),
            "lease_until": rec.get("lease_until"),
        }
        for display_id, rec in _active.items()
    ]


def create_virtual_display(
    width: int,
    height: int,
    dpr: float = 1.0,
    refresh_hz: int = 60,
    *,
    force_adapter: VirtualAdapter | str | None = None,
    owner: str | None = None,
) -> dict[str, object]:
    adapter = _resolve_adapter(force_adapter)
    if adapter is None or not adapter.can_create():
        raise VirtualDisplayError(adapter.reason() if adapter else unsupported_reason())
    display_id = uuid.uuid4().hex[:8]
    name = adapter.create(display_id, width, height, dpr, refresh_hz)
    now = time.time()
    geometry = getattr(adapter, "last_geometry", None) or {}
    rec = {
        "name": name,
        "width": width,
        "height": height,
        "x": int(geometry.get("x", 0)),
        "y": int(geometry.get("y", 0)),
        "adapter": adapter.id,
        "owner": owner,
        "lease_until": now + 60,
        "created_at": now,
    }
    _active[display_id] = rec
    _impls[display_id] = adapter
    return {
        "id": display_id,
        "name": name,
        "kind": "virtual",
        "width": width,
        "height": height,
        "selected": True,
        "adapter": adapter.id,
    }


def touch_lease(display_id: str, owner: str, ttl: float = 60) -> None:
    rec = _active.get(display_id)
    if rec is None:
        raise VirtualDisplayError(f"unknown virtual display {display_id}")
    rec["owner"] = owner
    rec["lease_until"] = time.time() + ttl


def expire_leases(now: float | None = None) -> list[str]:
    current = time.time() if now is None else now
    expired: list[str] = []
    for display_id, rec in list(_active.items()):
        lease_until = rec.get("lease_until")
        if lease_until is None:
            continue
        if current > float(lease_until) + LEASE_GRACE_S:
            try:
                destroy_virtual_display(display_id)
            except VirtualDisplayError:
                continue
            expired.append(display_id)
    return expired


def destroy_virtual_display(display_id: str) -> None:
    rec = _active.get(display_id)
    if rec is None:
        raise VirtualDisplayError(f"unknown virtual display {display_id}")
    impl = _impls.get(display_id) or _adapter_by_id(str(rec["adapter"]))
    if impl is None:
        _active.pop(display_id, None)
        _impls.pop(display_id, None)
        raise VirtualDisplayError(f"unknown virtual display adapter {rec['adapter']}")
    impl.destroy(str(rec["name"]))
    _active.pop(display_id, None)
    _impls.pop(display_id, None)


def destroy_all_virtual_displays() -> None:
    for display_id in list(_active):
        rec = _active.pop(display_id, None)
        impl = _impls.pop(display_id, None)
        if rec is None:
            continue
        if impl is None:
            impl = _adapter_by_id(str(rec["adapter"]))
        if impl is None:
            continue
        try:
            impl.destroy(str(rec["name"]))
        except VirtualDisplayError:
            continue
