"""A local stand-in for the GitHub release host, plus builders for synthetic release archives.

Tests never reach the network: the installer is pointed at this loopback server through its test seam
(``base_url``, ``allowed_hosts``, ``allow_http``), which production code never sets.
"""
from __future__ import annotations

import hashlib
import io
import json
import sys
import tarfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from agent_desktop_fixtures import ENVELOPE, ok, status_data
from termx.desktop import agent_desktop as ad

ASSET_NAME = "agent-desktop-v1.2.0-aarch64-apple-darwin.tar.gz"
MAC_ARM64 = ad.HostPlatform("macos", "arm64", None, (26, 0))


def fake_cli_source(*, version: str = ad.PINNED_VERSION, accessibility: str = "granted") -> bytes:
    """A tiny stand-in for the real CLI: answers ``version`` and ``status`` with canned envelopes."""
    answers = {
        "version": ok("version", {"os": "macos", "target": "aarch64", "version": version}),
        "status": ok("status", status_data("macos", accessibility)),
    }
    script = (
        "import json, sys\n"
        f"ANSWERS = json.loads({json.dumps(json.dumps(answers))})\n"
        "command = sys.argv[1] if len(sys.argv) > 1 else ''\n"
        "print(json.dumps(ANSWERS.get(command, {'version': %r, 'ok': False, 'command': command, "
        "'error': {'code': 'INVALID_ARGS', 'disposition': {'retry': 'never'}}})))\n" % ENVELOPE
    )
    return script.encode()


def good_members(**options: Any) -> dict[str, bytes]:
    return {"agent-desktop": fake_cli_source(**options), "agent-desktop-macos-helper": b"helper-bytes"}


def make_archive(members: list[tuple[str, bytes | None, dict[str, Any]]] | dict[str, bytes]) -> bytes:
    """A tar.gz. A dict maps names to file bytes; a list allows custom TarInfo fields (type, linkname)."""
    items = [(name, data, {}) for name, data in members.items()] if isinstance(members, dict) else members
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, data, extra in items:
            info = tarfile.TarInfo(name)
            for key, value in extra.items():
                setattr(info, key, value)
            payload = io.BytesIO(data) if data is not None else None
            if payload is not None:
                info.size = len(data)  # type: ignore[arg-type]
            archive.addfile(info, payload)
    return buffer.getvalue()


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def pins_for(archive: bytes, members: dict[str, bytes] | None = None, *, files: Any = "auto") -> dict[str, Any]:
    """Pins whose archive hash and size match ``archive`` and whose member hashes match ``members``."""
    members = good_members() if members is None else members
    pinned_files = {name: sha(data) for name, data in members.items()} if files == "auto" else files
    return {
        "version": ad.PINNED_VERSION, "tag": "v" + ad.PINNED_VERSION, "repo": "vercel-labs/agent-desktop", "envelope": ENVELOPE,
        "assets": {MAC_ARM64.key: {"asset": ASSET_NAME, "sha256": sha(archive), "size": len(archive), "files": pinned_files}},
    }


def client_for(root: Path, pins: dict[str, Any], **options: Any) -> ad.AgentDesktopClient:
    defaults: dict[str, Any] = {"home_root": Path(root) / "desktop-control", "pins": pins, "platform": MAC_ARM64,
                                "launcher": (sys.executable,), "search_path": []}
    defaults.update(options)
    return ad.AgentDesktopClient(**defaults)


class ReleaseServer:
    """Serves canned responses on 127.0.0.1 and records what was asked for."""

    def __init__(self) -> None:
        self.routes: dict[str, tuple[int, dict[str, str], bytes, int | None]] = {}
        self.requests: list[str] = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 - http.server API
                owner.requests.append(f"{self.headers.get('Host', '')}{self.path}")
                status, headers, body, declared = owner.routes.get(self.path, (404, {}, b"missing", None))
                self.send_response(status)
                for key, value in headers.items():
                    self.send_header(key, value)
                self.send_header("Content-Length", str(len(body) if declared is None else declared))
                self.end_headers()
                try:
                    self.wfile.write(body)
                except OSError:
                    pass

            def log_message(self, *args: Any) -> None:  # silence
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    @property
    def port(self) -> int:
        return self._server.server_address[1]

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/releases/"

    def serve(self, path: str, body: bytes, status: int = 200, *, declared_length: int | None = None) -> None:
        self.routes[path] = (status, {}, body, declared_length)

    def serve_asset(self, body: bytes) -> None:
        self.serve("/releases/" + ASSET_NAME, body)

    def redirect(self, path: str, location: str, status: int = 302) -> None:
        self.routes[path] = (status, {"Location": location}, b"", None)

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
