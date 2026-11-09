"""Optional managed install of the pinned agent-desktop release into TermX's own data directory.

This is the only module that reaches the network for desktop control, and only when the owner confirmed the
install in Settings. The URL is built from the pins, never from input. The download is accepted only when its size and
sha256 equal the pins that ship with this version of TermX, and each extracted file must equal its own pinned hash.
Nothing is executed here; the client hashes the files again before it ever runs them.

Build provenance (the GitHub attestation of the release) is not verified on the user's computer. The maintainer
script that creates the pins verifies it once; this module relies on the checksums built into TermX.
"""
from __future__ import annotations

import http.client
import os
import re
import shutil
import tarfile
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from termx.desktop.agent_desktop import AgentDesktopClient

RELEASE_URL = "https://github.com/vercel-labs/agent-desktop/releases/download/v{version}/"
ALLOWED_HOSTS = ("github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com")
MAX_REDIRECTS = 3
MAX_MEMBER_BYTES = 32 * 1024 * 1024
DOWNLOAD_DEADLINE_S = 300.0
CHECKSUM_NOTE = (
    "Checked against the checksum built into this version of TermX. The release's build provenance "
    "(GitHub attestation) is verified when TermX publishes its checksums, not on your computer."
)
_ASSET_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,120}$")

_MESSAGES = {
    "pin_incomplete": "No verified build is available for this computer in this version of TermX.",
    "download_failed": "The download did not complete. Nothing was installed.",
    "size_mismatch": "The download is not the size this version of TermX expects. Nothing was installed.",
    "hash_mismatch": "The download does not match the checksum built into this version of TermX. Nothing was installed.",
    "bad_archive": "The download does not contain exactly the expected files. Nothing was installed.",
    "busy": "An install is already running.",
    "install_failed": "The files could not be written. Nothing was installed.",
}


class InstallError(Exception):
    """An install step failed. ``code`` is stable; the message is ours and never quotes the network or the archive."""

    def __init__(self, code: str) -> None:
        super().__init__(_MESSAGES[code])
        self.code = code


@dataclass(frozen=True)
class InstallPlan:
    asset: str
    url: str
    sha256: str
    size: int
    host: str


class _Refused(urllib.error.URLError):
    pass


class _Redirects(urllib.request.HTTPRedirectHandler):
    """Follow redirects only to the allowed hosts over https, at most MAX_REDIRECTS times."""

    def __init__(self, check) -> None:  # noqa: ANN001
        super().__init__()
        self._check = check
        self._hops = 0

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        self._hops += 1
        if self._hops > MAX_REDIRECTS:
            raise _Refused("too many redirects")
        self._check(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class Installer:
    _busy = threading.Lock()

    def __init__(
        self,
        client: AgentDesktopClient,
        *,
        base_url: str | None = None,
        allowed_hosts: tuple[str, ...] = ALLOWED_HOSTS,
        allow_http: bool = False,
        timeout: float = 30.0,
    ) -> None:
        # base_url, allowed_hosts and allow_http are test seams; nothing reachable from the API sets them.
        self.client = client
        self.base_url = base_url or RELEASE_URL.format(version=client.version)
        self.allowed_hosts = tuple(host.lower() for host in allowed_hosts)
        self.allow_http = allow_http
        self.timeout = timeout

    # -- plan
    def plan(self) -> InstallPlan:
        asset = self.client.asset
        if asset is None or not self.client.pin_complete():
            raise InstallError("pin_incomplete")
        name = str(asset.get("asset") or "")
        if not _ASSET_NAME.fullmatch(name):
            raise InstallError("pin_incomplete")
        url = self.base_url + name
        return InstallPlan(name, url, str(asset["sha256"]), int(asset["size"]), urllib.parse.urlsplit(url).hostname or "")

    # -- network
    def _check_url(self, url: str) -> None:
        parts = urllib.parse.urlsplit(url)
        secure = parts.scheme == "https"
        if not (secure or (self.allow_http and parts.scheme == "http")):
            raise _Refused("scheme not allowed")
        if parts.username or parts.password or (parts.hostname or "").lower() not in self.allowed_hosts:
            raise _Refused("host not allowed")
        if not self.allow_http and parts.port not in (None, 443):
            raise _Refused("port not allowed")

    def _fetch(self, url: str) -> Iterator[bytes]:
        self._check_url(url)
        opener = urllib.request.build_opener(_Redirects(self._check_url))
        request = urllib.request.Request(url, headers={"User-Agent": "termx", "Accept": "application/octet-stream"})
        deadline = time.monotonic() + DOWNLOAD_DEADLINE_S
        with opener.open(request, timeout=self.timeout) as response:
            if response.status != 200:
                raise _Refused("unexpected status")
            while chunk := response.read(65536):
                if time.monotonic() > deadline:
                    raise _Refused("too slow")
                yield chunk

    def _download(self, plan: InstallPlan, target_dir: Path) -> Path:
        handle, name = tempfile.mkstemp(prefix=".download-", dir=target_dir)
        path = Path(name)
        total, digest = 0, sha256()
        try:
            with os.fdopen(handle, "wb") as out:
                for chunk in self._fetch(plan.url):
                    total += len(chunk)
                    if total > plan.size:
                        raise InstallError("size_mismatch")
                    digest.update(chunk)
                    out.write(chunk)
        except InstallError:
            path.unlink(missing_ok=True)
            raise
        except (OSError, http.client.HTTPException, ValueError):
            path.unlink(missing_ok=True)
            raise InstallError("download_failed") from None
        if total != plan.size:
            path.unlink(missing_ok=True)
            raise InstallError("size_mismatch")
        if digest.hexdigest() != plan.sha256:
            path.unlink(missing_ok=True)
            raise InstallError("hash_mismatch")
        return path

    # -- archive
    def _extract(self, archive: Path, stage: Path) -> None:
        expected: dict[str, str] = dict((self.client.asset or {}).get("files") or {})
        seen: set[str] = set()
        try:
            with tarfile.open(archive, "r:gz") as tar:
                for member in tar:
                    name = member.name
                    if name not in expected or name in seen or not member.isreg() or member.size > MAX_MEMBER_BYTES:
                        raise InstallError("bad_archive")
                    source = tar.extractfile(member)
                    if source is None:
                        raise InstallError("bad_archive")
                    data = source.read(MAX_MEMBER_BYTES + 1)
                    if len(data) > MAX_MEMBER_BYTES:
                        raise InstallError("bad_archive")
                    if sha256(data).hexdigest() != expected[name]:
                        raise InstallError("hash_mismatch")
                    seen.add(name)
                    descriptor = os.open(stage / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o700)
                    with os.fdopen(descriptor, "wb") as out:
                        out.write(data)
                    os.chmod(stage / name, 0o700)
        except (tarfile.TarError, EOFError, OSError, ValueError, UnicodeError):
            raise InstallError("bad_archive") from None
        if seen != set(expected):
            raise InstallError("bad_archive")

    # -- install and remove
    def install(self) -> None:
        """Blocking: download, verify and put the files in place. Run it off the event loop."""
        plan = self.plan()
        if not self._busy.acquire(blocking=False):
            raise InstallError("busy")
        download: Path | None = None
        stage: Path | None = None
        old: Path | None = None
        try:
            self.client.ensure_layout()
            target = self.client.bin_dir
            bin_root = target.parent
            download = self._download(plan, bin_root)
            stage = Path(tempfile.mkdtemp(prefix=".stage-", dir=bin_root))
            self._extract(download, stage)
            try:
                if target.is_symlink() or os.path.isjunction(target):
                    target.unlink()
                elif target.exists():
                    old = bin_root / f".old-{os.urandom(4).hex()}"
                    os.replace(target, old)
                os.replace(stage, target)
                stage = None
            except OSError:
                if old is not None and not target.exists():
                    os.replace(old, target)
                    old = None
                raise
        except OSError:
            # Our own message only: the OS error text holds local paths.
            raise InstallError("install_failed") from None
        finally:
            if download is not None:
                download.unlink(missing_ok=True)
            for leftover in (stage, old):
                if leftover is not None:
                    shutil.rmtree(leftover, ignore_errors=True)
            self._busy.release()
        self.client.forget()

    def remove(self) -> bool:
        """Delete only this version's managed directory (a link is unlinked, never followed). Returns whether it existed."""
        target = self.client.bin_dir
        removed = False
        self.client.kill_all()  # nothing may be running from the files while they are deleted
        try:
            if target.is_symlink():
                target.unlink()
                removed = True
            elif os.path.isjunction(target):
                os.rmdir(target)
                removed = True
            elif target.is_dir():
                if target.resolve().parent != target.parent.resolve():
                    raise InstallError("install_failed")
                shutil.rmtree(target)
                removed = True
        except OSError:
            raise InstallError("install_failed") from None
        finally:
            self.client.forget()
        return removed


def make_installer(client: AgentDesktopClient) -> Installer:
    """The production installer: the pinned GitHub release, https only. Tests replace this seam."""
    return Installer(client)
