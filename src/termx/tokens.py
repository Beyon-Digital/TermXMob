from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import threading
import uuid
from pathlib import Path
from time import time
from typing import Any

from termx.config import atomic_write, config_dir

# Device scope v2 (SEC-001): granular scopes across the whole host surface.
SCOPES = (
    "machine-view",
    "terminal-view",
    "terminal-control",
    "files-read",
    "files-write",
    "git-read",
    "git-write",
    "desktop-view",
    "desktop-control",
    "network-manage",
    "agent-view",
    "agent-run",
    "agent-control",
    "ai-settings",
    "host-admin",
)
_ALLOWED = frozenset(SCOPES)

# v1 scope -> its exact v2 equivalent(s). Migration preserves existing
# authority only — no legacy token silently gains host-admin, the one scope
# with genuinely new power; that requires an explicit admin grant/re-pair.
LEGACY_SCOPE_MAP: dict[str, tuple[str, ...]] = {
    "terminal": (
        "terminal-view",
        "terminal-control",
        "files-read",
        "files-write",
        "git-read",
        "git-write",
    ),
    "screen-view": ("desktop-view",),
    "screen-control": ("desktop-control",),
    "tunnel-admin": ("network-manage",),
    "settings-admin": ("ai-settings",),
    "agent-view": ("agent-view",),
    "agent-run": ("agent-run",),
    "agent-control": ("agent-control",),
    "ai-settings": ("ai-settings",),
}
LEGACY_SCOPES = frozenset(LEGACY_SCOPE_MAP)

# Scopes granted to a default /api/pair issue: everything except host-admin
# (the old pair granted all v1 scopes; host-admin is the new admin tier and
# is only granted explicitly by the passcode-holder).
DEFAULT_DEVICE_SCOPES = tuple(scope for scope in SCOPES if scope != "host-admin")

_LAST_SEEN_FLUSH_S = 60.0


def _tokens_path() -> Path:
    return config_dir() / "tokens.json"


def _hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _normalize_scopes(scopes: list[str] | None) -> tuple[list[str], bool]:
    """Return (v2 scopes, migrated-from-legacy) preserving authority, never gains."""
    if scopes is None:
        return list(DEFAULT_DEVICE_SCOPES), False
    seen: set[str] = set()
    out: list[str] = []
    migrated = False
    for scope in scopes:
        if scope in _ALLOWED:
            targets: tuple[str, ...] = (scope,)
        elif scope in LEGACY_SCOPE_MAP:
            targets = LEGACY_SCOPE_MAP[scope]
            migrated = True
        else:
            continue
        for target in targets:
            if target not in seen:
                seen.add(target)
                out.append(target)
    if migrated and "machine-view" not in seen:
        # Every v1 credential could already call read-only host endpoints.
        out.append("machine-view")
    return out, migrated


class TokenStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or _tokens_path()
        self._lock = threading.Lock()
        self._tokens: list[dict[str, Any]] = self._load()
        self._last_save = time()

    def _load(self) -> list[dict[str, Any]]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        if isinstance(raw, dict):
            items = raw.get("tokens")
        elif isinstance(raw, list):
            items = raw
        else:
            return []
        if not isinstance(items, list):
            return []
        tokens: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            token_id = item.get("id")
            digest = item.get("hash")
            if not isinstance(token_id, str) or not isinstance(digest, str):
                continue
            scopes = item.get("scopes")
            if isinstance(scopes, list):
                normalized, migrated = _normalize_scopes(scopes)
            else:
                # Malformed/absent scope list on a stored (v1-era) record:
                # preserve only the machine-read authority any v1 credential
                # already had — never default to the full device set.
                normalized, migrated = ["machine-view"], True
            created_at = item.get("created_at")
            expires_at = item.get("expires_at")
            tokens.append(
                {
                    "id": token_id,
                    "hash": digest,
                    "scopes": normalized,
                    "device_name": item.get("device_name") or "",
                    "created_at": created_at if isinstance(created_at, (int, float)) else time(),
                    "last_seen": item.get("last_seen") if isinstance(item.get("last_seen"), (int, float)) else None,
                    "expires_at": expires_at if isinstance(expires_at, (int, float)) else None,
                    "legacy": bool(migrated or item.get("legacy")),
                }
            )
        return tokens

    def _save(self) -> None:
        atomic_write(self.path, json.dumps({"tokens": self._tokens}, indent=2))
        self._last_save = time()

    def issue(
        self,
        scopes: list[str] | None = None,
        *,
        device_name: str = "",
        expires_at: float | None = None,
    ) -> str:
        raw = secrets.token_urlsafe(32)
        normalized, migrated = _normalize_scopes(scopes)
        record = {
            "id": uuid.uuid4().hex,
            "hash": _hash_token(raw),
            "scopes": normalized,
            "device_name": device_name.strip(),
            "created_at": time(),
            "last_seen": None,
            "expires_at": expires_at,
            "legacy": migrated,
        }
        with self._lock:
            self._tokens.append(record)
            self._save()
        return raw

    def check(self, raw: str | None) -> list[str] | None:
        if not raw:
            return None
        digest = _hash_token(raw)
        with self._lock:
            for record in self._tokens:
                if not hmac.compare_digest(record["hash"], digest):
                    continue
                expires_at = record.get("expires_at")
                if expires_at is not None and time() >= float(expires_at):
                    return None
                record["last_seen"] = time()
                if time() - self._last_save >= _LAST_SEEN_FLUSH_S:
                    self._save()
                return list(record["scopes"])
        return None

    def update_scopes(self, token_id: str, scopes: list[str]) -> dict[str, Any] | None:
        """Explicit admin re-scope — the migration/re-pair path for v1 tokens."""
        normalized, _ = _normalize_scopes(scopes)
        with self._lock:
            for record in self._tokens:
                if record["id"] == token_id:
                    record["scopes"] = normalized
                    record["legacy"] = False
                    self._save()
                    return {
                        "id": record["id"],
                        "scopes": list(record["scopes"]),
                        "device_name": record["device_name"],
                    }
        return None

    def update_device(
        self, token_id: str, *, device_name: str | None = None, expires_at: float | None = None
    ) -> dict[str, Any] | None:
        with self._lock:
            for record in self._tokens:
                if record["id"] == token_id:
                    if device_name is not None:
                        record["device_name"] = device_name.strip()
                    if expires_at is not None:
                        record["expires_at"] = expires_at
                    self._save()
                    return {
                        "id": record["id"],
                        "scopes": list(record["scopes"]),
                        "device_name": record["device_name"],
                        "expires_at": record["expires_at"],
                    }
        return None

    def revoke(self, token_id: str) -> bool:
        with self._lock:
            for index, record in enumerate(self._tokens):
                if record["id"] == token_id:
                    del self._tokens[index]
                    self._save()
                    return True
        return False

    def association_record(self, raw: str) -> dict[str, Any] | None:
        """Proof of possession for explicit managed migration, never API auth.

        Expired records may be associated by an authenticated administrator;
        this does not extend their expiry or invent an owner for legacy data.
        """
        if not raw or len(raw) > 512:
            return None
        digest = _hash_token(raw)
        with self._lock:
            record = next((row for row in self._tokens if hmac.compare_digest(row['hash'],digest)),None)
            return {'id':record['id'],'scopes':list(record['scopes'])} if record else None

    def list_public(self) -> list[dict]:
        with self._lock:
            return [
                {
                    "id": record["id"],
                    "scopes": list(record["scopes"]),
                    "device_name": record["device_name"],
                    "created_at": record["created_at"],
                    "last_seen": record["last_seen"],
                    "expires_at": record["expires_at"],
                    "expired": record["expires_at"] is not None
                    and time() >= float(record["expires_at"]),
                    "legacy": record["legacy"],
                }
                for record in self._tokens
            ]
