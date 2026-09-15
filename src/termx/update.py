"""Update checks against the published GitHub releases.

The desktop app ships as a signed bundle; this module reports whether a newer
release exists so the shell (or a remote client) can trigger the install. The
network call is best-effort: an offline or rate-limited host reports "unknown"
rather than failing a request.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

GITHUB_REPO = os.environ.get("TERMX_UPDATE_REPO", "Psyborgs-git/TermXMob")
RELEASES_API = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
USER_AGENT = "termx-update-check"


def parse_version(text: str) -> tuple[int, ...]:
    """Ordering key for versions like ``v0.1.7`` or ``0.2.0-beta.1``."""
    cleaned = str(text).strip().lstrip("vV")
    parts: list[int] = []
    for chunk in cleaned.split("."):
        digits = ""
        for char in chunk:
            if char.isdigit():
                digits += char
            else:
                break
        parts.append(int(digits) if digits else 0)
    return tuple(parts) or (0,)


def is_newer(candidate: str, current: str) -> bool:
    return parse_version(candidate) > parse_version(current)


def fetch_latest_release(timeout: float = 8.0) -> dict[str, object] | None:
    request = urllib.request.Request(
        RELEASES_API,
        headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        return None


def check_for_update(current: str) -> dict[str, object]:
    """Compare the running version with the newest published release."""
    release = fetch_latest_release()
    if release is None:
        return {
            "current": current,
            "available": False,
            "status": "unknown",
            "reason": "could not reach the release feed",
        }
    tag = str(release.get("tag_name") or "")
    latest = tag.lstrip("vV") or current
    return {
        "current": current,
        "latest": latest,
        "tag": tag,
        "available": is_newer(latest, current),
        "status": "ok",
        "url": release.get("html_url"),
        "notes": str(release.get("body") or "")[:2000],
        "published_at": release.get("published_at"),
    }


def desktop_managed() -> bool:
    """True when a desktop shell owns this process (and can install updates)."""
    return os.environ.get("TERMX_DESKTOP") == "1"
