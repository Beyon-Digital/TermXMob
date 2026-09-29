"""Port + process discovery (PROD-005).

Two scoped views:

- ``listeners()`` — listening TCP sockets from ``/proc/net/tcp{,6}`` joined
  with the owning PID (when ``/proc/<pid>/fd`` is readable), process name,
  cmdline and cwd, plus a bounded HTTP probe so the client can offer a
  one-click preview URL.
- ``termx_processes(projects)`` — processes Termx owns (our own PID tree:
  PTY shells, forward/tunnel helpers, agent-spawned children) plus any
  process whose cwd sits inside a registered project root. Arbitrary
  unrelated system processes are never listed.

Linux-only in this version; other platforms degrade to empty lists rather
than pretending discovery worked.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any

_PROC = Path("/proc")


def supported() -> bool:
    """Whether this host can discover listeners/processes: Linux via /proc,
    macOS via lsof+ps. Windows is truthfully gated off."""
    return sys.platform.startswith("linux") or sys.platform == "darwin"


def _lsof(*args: str) -> str:
    try:
        return subprocess.run(
            ["lsof", *args], capture_output=True, text=True, timeout=15
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def _darwin_listeners() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for line in _lsof("-nP", "-iTCP", "-sTCP:LISTEN").splitlines()[1:]:
        parts = line.split()
        if len(parts) < 9:
            continue
        name = parts[-2] if parts[-1].startswith("(") else parts[-1]
        addr, _, port_raw = name.rpartition(":")
        try:
            port = int(port_raw)
        except ValueError:
            continue
        try:
            pid = int(parts[1])
        except (ValueError, IndexError):
            pid = None
        address = addr.lstrip("*") or "0.0.0.0"
        loopback = address in {"127.0.0.1", "0.0.0.0", "::", "::1", "localhost"}
        is_http = probe_http(port) if loopback else False
        url = None
        if is_http:
            url = f"http://{'127.0.0.1' if address in {'0.0.0.0', '::'} else address}:{port}"
        out.append(
            {
                "port": port,
                "address": address,
                "family": "ipv6" if ":" in addr else "ipv4",
                "pid": pid,
                "process": parts[0],
                "cmdline": None,
                "cwd": None,
                "is_http": is_http,
                "url": url,
            }
        )
    out.sort(key=lambda item: (item["port"], item["family"]))
    return out


def _darwin_infos() -> dict[int, dict[str, Any]]:
    infos: dict[int, dict[str, Any]] = {}
    try:
        out = subprocess.run(
            ["ps", "-axo", "pid=,ppid=,comm=,args="],
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return infos
    for line in out.splitlines()[1:]:
        parts = line.split(None, 3)
        if len(parts) < 4:
            continue
        try:
            pid, ppid = int(parts[0]), int(parts[1])
        except ValueError:
            continue
        infos[pid] = {
            "pid": pid,
            "ppid": ppid,
            "name": parts[2],
            "cmdline": parts[3],
            "cwd": None,
        }
    pid: int | None = None
    for line in _lsof("-n", "-d", "cwd", "-FpLn").splitlines():
        if line.startswith("p"):
            try:
                pid = int(line[1:])
            except ValueError:
                pid = None
        elif line.startswith("n") and pid is not None:
            if pid in infos:
                infos[pid]["cwd"] = line[1:]
            pid = None
    return infos


_HTTP_PROBE_BYTES = b"GET / HTTP/1.0\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n"
_PROBE_TIMEOUT = 0.25


def _hex_addr(raw: str, ipv6: bool) -> str:
    host_hex, _ = raw.rsplit(":", 1)
    if not ipv6:
        octets = [str(int(host_hex[i : i + 2], 16)) for i in range(0, 8, 2)]
        return ".".join(reversed(octets))
    try:
        packed = bytes.fromhex(host_hex)
        # /proc/net/tcp6 stores 4 little-endian words
        reordered = b"".join(packed[i : i + 4][::-1] for i in range(0, 16, 4))
        return socket.inet_ntop(socket.AF_INET6, reordered)
    except (ValueError, OSError):
        return host_hex


def _proc_net_listeners() -> list[dict[str, Any]]:
    """LISTEN-state TCP sockets: {address, port, family, inode}."""
    out: list[dict[str, Any]] = []
    for name, family, ipv6 in (("tcp", "ipv4", False), ("tcp6", "ipv6", True)):
        path = _PROC / "net" / name
        try:
            lines = path.read_text().splitlines()[1:]
        except OSError:
            continue
        for line in lines:
            fields = line.split()
            if len(fields) < 10 or fields[3] != "0A":  # 0A = LISTEN
                continue
            try:
                port = int(fields[1].rsplit(":", 1)[1], 16)
            except (ValueError, IndexError):
                continue
            out.append(
                {
                    "address": _hex_addr(fields[1], ipv6),
                    "port": port,
                    "family": family,
                    "inode": fields[9],
                }
            )
    return out


def _socket_inodes(pid: int) -> set[str]:
    fd_dir = _PROC / str(pid) / "fd"
    inodes: set[str] = set()
    try:
        for fd in fd_dir.iterdir():
            try:
                target = os.readlink(fd)
            except OSError:
                continue
            if target.startswith("socket:["):
                inodes.add(target[8:-1])
    except (OSError, PermissionError):
        return inodes
    return inodes


def _proc_info(pid: int) -> dict[str, Any] | None:
    base = _PROC / str(pid)
    try:
        cmdline_raw = (base / "cmdline").read_bytes()
        cmdline = " ".join(
            part.decode("utf-8", "replace") for part in cmdline_raw.split(b"\0") if part
        )
        name = (base / "comm").read_text().strip()
        stat = (base / "stat").read_text()
        ppid = int(stat.rsplit(")", 1)[1].split()[1])
        try:
            cwd = os.readlink(base / "cwd")
        except OSError:
            cwd = None
        return {"pid": pid, "ppid": ppid, "name": name, "cmdline": cmdline, "cwd": cwd}
    except (OSError, ValueError, IndexError):
        return None


def _live_pids() -> list[int]:
    try:
        return [int(p.name) for p in _PROC.iterdir() if p.name.isdigit()]
    except OSError:
        return []


def probe_http(port: int, host: str = "127.0.0.1") -> bool:
    """Bounded probe: True when the listener answers an HTTP request line."""
    try:
        with socket.create_connection((host, port), timeout=_PROBE_TIMEOUT) as sock:
            sock.settimeout(_PROBE_TIMEOUT)
            sock.sendall(_HTTP_PROBE_BYTES)
            head = sock.recv(64)
            return head.startswith(b"HTTP/")
    except OSError:
        return False


def listeners(probe: bool = True) -> list[dict[str, Any]]:
    """Listening TCP sockets enriched with owner process data when permitted."""
    if not supported():
        return []
    if sys.platform == "darwin":
        return _darwin_listeners()
    sockets = _proc_net_listeners()
    if not sockets:
        return []
    inode_to_pid: dict[str, int] = {}
    info_cache: dict[int, dict[str, Any] | None] = {}
    wanted = {entry["inode"] for entry in sockets}
    for pid in _live_pids():
        for inode in _socket_inodes(pid) & wanted:
            inode_to_pid[inode] = pid
    out: list[dict[str, Any]] = []
    for entry in sockets:
        pid = inode_to_pid.get(entry["inode"])
        info = info_cache.get(pid) if pid is not None else None
        if pid is not None and pid not in info_cache:
            info = info_cache[pid] = _proc_info(pid)
        address = entry["address"]
        loopback = address in {"127.0.0.1", "0.0.0.0", "::", "::1"}
        is_http = probe_http(entry["port"]) if probe and loopback else False
        url = None
        if is_http:
            host = "127.0.0.1" if address in {"0.0.0.0", "::"} else address
            url = f"http://{host}:{entry['port']}"
        out.append(
            {
                "port": entry["port"],
                "address": address,
                "family": entry["family"],
                "pid": pid,
                "process": info["name"] if info else None,
                "cmdline": info["cmdline"] if info else None,
                "cwd": info["cwd"] if info else None,
                "is_http": is_http,
                "url": url,
            }
        )
    out.sort(key=lambda item: (item["port"], item["family"]))
    return out


def termx_processes(project_roots: list[str] | None = None) -> list[dict[str, Any]]:
    """Processes owned by this Termx host (our PID tree) plus processes whose
    cwd sits inside one of `project_roots`. Arbitrary system processes are
    never returned — discovery stays scoped to Termx and project workspaces."""
    if not supported():
        return []
    infos = (
        _darwin_infos()
        if sys.platform == "darwin"
        else {
            pid: info
            for pid in _live_pids()
            if (info := _proc_info(pid)) is not None
        }
    )
    own = os.getpid()
    children: dict[int, list[int]] = {}
    for pid, info in infos.items():
        children.setdefault(info["ppid"], []).append(pid)
    owned: set[int] = set()
    frontier = [own]
    while frontier:
        pid = frontier.pop()
        if pid in owned or pid not in infos:
            continue
        owned.add(pid)
        frontier.extend(children.get(pid, []))
    roots = [os.path.normpath(r) for r in (project_roots or []) if r]
    for pid, info in infos.items():
        cwd = info["cwd"]
        if pid in owned or not cwd:
            continue
        norm = os.path.normpath(cwd)
        if any(norm == root or norm.startswith(root + os.sep) for root in roots):
            owned.add(pid)
    return [infos[pid] for pid in sorted(owned)]
