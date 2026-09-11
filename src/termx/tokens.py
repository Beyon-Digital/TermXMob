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

SCOPES = ("terminal", "screen-view", "screen-control", "tunnel-admin", "settings-admin")
_ALLOWED = frozenset(SCOPES)


def _tokens_path() -> Path:
    return config_dir() / "tokens.json"


def _hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _normalize_scopes(scopes: list[str] | None) -> list[str]:
    if scopes is None:
        return list(SCOPES)
    seen: set[str] = set()
    out: list[str] = []
    for scope in scopes:
        if scope in _ALLOWED and scope not in seen:
            seen.add(scope)
            out.append(scope)
    return out


class TokenStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or _tokens_path()
        self._lock = threading.Lock()
        self._tokens: list[dict[str, Any]] = self._load()

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
            created_at = item.get("created_at")
            tokens.append(
                {
                    "id": token_id,
                    "hash": digest,
                    "scopes": _normalize_scopes(scopes if isinstance(scopes, list) else None),
                    "created_at": created_at if isinstance(created_at, (int, float)) else time(),
                }
            )
        return tokens

    def _save(self) -> None:
        atomic_write(self.path, json.dumps({"tokens": self._tokens}, indent=2))

    def issue(self, scopes: list[str] | None = None) -> str:
        raw = secrets.token_urlsafe(32)
        record = {
            "id": uuid.uuid4().hex,
            "hash": _hash_token(raw),
            "scopes": _normalize_scopes(scopes),
            "created_at": time(),
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
                if hmac.compare_digest(record["hash"], digest):
                    return list(record["scopes"])
        return None

    def revoke(self, token_id: str) -> bool:
        with self._lock:
            for index, record in enumerate(self._tokens):
                if record["id"] == token_id:
                    del self._tokens[index]
                    self._save()
                    return True
        return False

    def list_public(self) -> list[dict]:
        with self._lock:
            return [
                {"id": record["id"], "scopes": list(record["scopes"]), "created_at": record["created_at"]}
                for record in self._tokens
            ]
