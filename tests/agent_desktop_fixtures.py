"""Canned agent-desktop envelopes and a fake CLI installed into a client, for tests on any host."""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
from pathlib import Path
from typing import Any

from termx.desktop import agent_desktop as ad

SHIM_SOURCE = Path(__file__).parent / "fixtures" / "agent_desktop" / "agent_desktop_fake.py"
ENVELOPE = "2.6"


def ok(command: str, data: Any = None) -> dict[str, Any]:
    return {"version": ENVELOPE, "ok": True, "command": command, "data": {} if data is None else data}


def err(
    command: str, code: str, *, retry: str = "safe", delivery: str = "not_delivered", details: dict | None = None,
    message: str = "synthetic message that quotes Secret Name",
) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message, "suggestion": "launch the app first",
                             "disposition": {"delivery": delivery, "retry": retry}}
    if details:
        error["details"] = details
    return {"version": ENVELOPE, "ok": False, "command": command, "error": error}


def node(role: str, name: str | None = None, ref: str | None = None, children: list | None = None, **extra: Any) -> dict[str, Any]:
    item: dict[str, Any] = {"role": role}
    if name is not None:
        item["name"] = name
    if ref is not None:
        item["ref_id"] = ref
    if children:
        item["children"] = children
    item.update(extra)
    return item


def tree(snapshot_id: str, *items: dict[str, Any]) -> dict[str, Any]:
    """A window with the given children; refs may use the shorthand ``@:eN``."""
    def fix(entry: dict[str, Any]) -> dict[str, Any]:
        out = dict(entry)
        if isinstance(out.get("ref_id"), str):
            out["ref_id"] = out["ref_id"].replace("@:", f"@{snapshot_id}:")
        if out.get("children"):
            out["children"] = [fix(child) for child in out["children"]]
        return out

    return fix({"role": "window", "name": "Untitled", "children": list(items)})


def app_row(name: str, pid: int, bundle_id: str | None = None, presentation: str = "foreground") -> dict[str, Any]:
    row: dict[str, Any] = {"name": name, "pid": pid, "presentation": presentation, "process_instance": f"macos-proc-v1:{pid}:1"}
    if bundle_id:
        row["bundle_id"] = bundle_id
    return row


def window_row(window_id: str, app: dict[str, Any], title: str = "Untitled", focused: bool = True) -> dict[str, Any]:
    return {"accessible": True, "app_name": app["name"], "id": window_id, "is_focused": focused, "pid": app["pid"],
            "process_instance": app["process_instance"], "title": title, "visible": True,
            "bounds": {"x": 0, "y": 0, "width": 800, "height": 600}}


def status_data(system: str = "macos", accessibility: str = "granted", surfaces: list[str] | None = None) -> dict[str, Any]:
    return {"permissions": {"accessibility": {"state": accessibility}, "screen_recording": {"state": "denied"}},
            "platform": system, "supported_surfaces": ["window", "focused", "menu"] if surfaces is None else surfaces,
            "version": ad.PINNED_VERSION}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class FakeDesktop:
    """A client whose managed binary is the Python shim, driven by ``scenario.json``."""

    def __init__(self, root: Path, *, platform: str = "macos", install: bool = True) -> None:
        arch = "arm64" if platform == "macos" else "x86_64"
        self.platform = ad.HostPlatform(platform, arch, True if platform == "linux" else None, (26, 0) if platform == "macos" else ())
        self.root = Path(root)
        self.home_root = self.root / "desktop-control"
        self.bin_dir = self.home_root / "bin" / ad.PINNED_VERSION
        self.binary = self.bin_dir / self.platform.binary_name
        self.helper = self.bin_dir / "agent-desktop-macos-helper"
        self.scenario = self.bin_dir / "scenario.json"
        self.calls_file = self.bin_dir / "calls.jsonl"
        self._extra: list[dict[str, Any]] = []
        self._apps: list[dict[str, Any]] = []
        self._trees: dict[str, dict[str, Any]] = {}
        self._windows: dict[str, list[dict[str, Any]]] = {}
        self._sids: dict[str, str] = {}
        self.accessibility = "granted"
        self.surfaces: list[str] | None = None
        self.version = ad.PINNED_VERSION
        if install:
            self.install_files()
        self.client = self.build_client()
        self.write()

    # -- construction
    def install_files(self) -> None:
        self.bin_dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(SHIM_SOURCE, self.binary)
        self.binary.chmod(0o700)
        if self.platform.os == "macos":
            self.helper.write_bytes(b"")
            self.helper.chmod(0o700)

    def pins(self) -> dict[str, Any]:
        files = {self.binary.name: sha(self.binary)}
        if self.platform.os == "macos":
            files[self.helper.name] = sha(self.helper)
        return {"version": ad.PINNED_VERSION, "envelope": ENVELOPE,
                "assets": {self.platform.key: {"asset": "x", "sha256": "0" * 64, "size": 1, "files": files}}}

    def build_client(self, **options: Any) -> ad.AgentDesktopClient:
        defaults: dict[str, Any] = {"home_root": self.home_root, "pins": self.pins() if self.binary.exists() else
                                    {"version": ad.PINNED_VERSION, "assets": {self.platform.key: {"files": {
                                        self.platform.binary_name: "0" * 64, "agent-desktop-macos-helper": "0" * 64}}}},
                                    "platform": self.platform, "launcher": (sys.executable,), "search_path": []}
        defaults.update(options)
        return ad.AgentDesktopClient(**defaults)

    def install(self) -> "FakeDesktop":
        ad.set_client(self.client)
        return self

    # -- scenario
    def rules(self, *rules: dict[str, Any]) -> "FakeDesktop":
        """Rules that win over the defaults."""
        self._extra = list(rules) + self._extra
        self.write()
        return self

    def reset_rules(self) -> None:
        self._extra = []
        self.write()

    def arrange_apps(self, *apps: tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any] | None]) -> "FakeDesktop":
        """Each item: (app row, window rows, tree built with ``tree(snapshot_id, ...)``)."""
        for row, windows, root in apps:
            self._apps = [item for item in self._apps if item["pid"] != row["pid"]] + [row]
            self._windows[row["name"]] = windows
            if root is not None:
                self._trees[row["name"]] = root
        self.write()
        return self

    def write(self) -> None:
        target = "aarch64" if self.platform.arch == "arm64" else "x86_64"
        rules: list[dict[str, Any]] = list(self._extra)
        rules.append({"match": {"command": "version"}, "respond": {"envelope": ok("version", {"os": self.platform.os, "target": target, "version": self.version})}})
        rules.append({"match": {"command": "status"}, "respond": {"envelope": ok("status", status_data(self.platform.os, self.accessibility, self.surfaces))}})
        for row in self._apps:
            name = row["name"]
            rules.append({"match": {"command": "list-apps", "argv_contains": [f"--app={name}"]}, "respond": {"envelope": ok("list-apps", {"apps": [row]})}})
            rules.append({"match": {"command": "list-windows", "argv_contains": [f"--app={name}"]}, "respond": {"envelope": ok("list-windows", self._windows.get(name, []))}})
            root = self._trees.get(name)
            if root is not None:
                sid = root["ref_id"] if False else self._snapshot_id(root)
                data = {"app": name, "complete": True, "ref_count": _count_refs(root), "snapshot_id": sid, "tree": root}
                windows = self._windows.get(name) or []
                if windows:
                    data["window"] = {"id": windows[0]["id"], "title": windows[0]["title"]}
                rules.append({"match": {"command": "snapshot", "argv_contains": [f"--app={name}"]}, "respond": {"envelope": ok("snapshot", data)}})
        rules.append({"match": {"command": "list-apps"}, "respond": {"envelope": ok("list-apps", {"apps": self._apps})}})
        for command in ad.ALLOWED_COMMANDS - {"version", "status", "list-apps", "list-windows", "snapshot", "find", "get"}:
            rules.append({"match": {"command": command}, "respond": {"envelope": ok(command, {
                "disposition": {"delivery": "delivered_verified"}, "post_state": {"checked": True, "value": "SECRET-ECHO"}, "surfaces": ["sheet", "weird"]})}})
        self.bin_dir.mkdir(parents=True, exist_ok=True)
        self.scenario.write_text(json.dumps(rules), "utf-8")

    @staticmethod
    def _snapshot_id(root: dict[str, Any]) -> str:
        def first(entry: dict[str, Any]) -> str | None:
            ref = entry.get("ref_id")
            if isinstance(ref, str):
                return ref[1:].split(":")[0]
            for child in entry.get("children") or []:
                found = first(child)
                if found:
                    return found
            return None
        return first(root) or "s0snapshot"

    # -- observations
    def calls(self) -> list[dict[str, Any]]:
        if not self.calls_file.exists():
            return []
        return [json.loads(line) for line in self.calls_file.read_text("utf-8").splitlines() if line.strip()]

    def commands(self) -> list[str]:
        return [call["argv"][0] for call in self.calls() if call["argv"]]

    def spawns(self) -> list[dict[str, Any]]:
        """Calls other than the host's own availability probes."""
        return [call for call in self.calls() if call["argv"] and call["argv"][0] not in {"version", "status"}]

    def last_stdin(self) -> str | None:
        for call in reversed(self.calls()):
            if call["stdin"] is not None:
                return call["stdin"]
        return None


def _count_refs(entry: dict[str, Any]) -> int:
    return (1 if entry.get("ref_id") else 0) + sum(_count_refs(child) for child in entry.get("children") or [])


def assert_spawns(desk: FakeDesktop, allowed: set[str]) -> None:
    extra = [command for command in desk.commands() if command not in {"version", "status"} | allowed]
    assert not extra, f"unexpected commands spawned: {extra}"
