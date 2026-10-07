"""Canonical identities and revocable sessions for the desktop workspace.

Identity adapters verify evidence; this service alone maps identities, issues
host credentials and evaluates current authority. SQLite transactions serialize
refresh across windows/processes; no refresh token is persisted in plaintext.
"""
from __future__ import annotations

import hashlib
import asyncio
import hmac
import json
import os
import secrets
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from time import time
from typing import Any, Iterator, Protocol

import jwt

from termx.audit import log_event
from termx.config import config_dir
from termx.tokens import SCOPES

ACCESS_TTL = 300
IDLE_TTL = 24 * 60 * 60
ABSOLUTE_TTL = 30 * 24 * 60 * 60
MIGRATION_TTL = 7 * 24 * 60 * 60


class AuthenticationError(Exception):
    """Safe, non-secret failure at the authentication boundary."""


class LoginLimited(AuthenticationError):
    pass


class SessionLocked(AuthenticationError):
    """The refresh family is retained, but interactive unlock is required."""


@dataclass(frozen=True)
class Identity:
    issuer: str
    subject: str
    strength: str


@dataclass(frozen=True)
class Principal:
    id: str
    display_name: str
    scopes: tuple[str, ...]
    policy_version: int
    # Server-derived provenance preserves device grants across live reloads.
    authority_session_id: str | None = None
    authority_execution: bool = False


@dataclass(frozen=True)
class SessionIdentity:
    principal: Principal
    session_id: str
    expires_at: float


@dataclass(frozen=True)
class SessionCredentials:
    access_token: str
    refresh_token: str
    csrf_token: str
    session_id: str
    expires_in: int = ACCESS_TTL


class AuthenticationPort(Protocol):
    id: str
    label: str

    async def authenticate(self, evidence: dict[str, str]) -> Identity: ...


class PrincipalDirectoryPort(Protocol):
    def principal_for(self, identity: Identity) -> Principal | None: ...


class SessionPort(Protocol):
    def resolve(self, token: str | None) -> SessionIdentity | None: ...
    def refresh(self, raw: str) -> SessionCredentials: ...
    def revoke(self, session_id: str, principal_id: str) -> bool: ...


def _digest(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def _password_hash(password: str, salt: bytes) -> str:
    return hashlib.scrypt(password.encode(), salt=salt, n=2**15, r=8, p=1, maxmem=64 * 1024 * 1024).hex()


class LocalPasswordAdapter:
    id = "local-password"
    label = "Password"

    def __init__(self, service: AuthenticationService) -> None:
        self.service = service

    async def authenticate(self, evidence: dict[str, str]) -> Identity:
        username = evidence.get("username", "").strip()
        password = evidence.get("password", "")
        with self.service._db() as db:
            row = db.execute("SELECT salt, digest FROM passwords WHERE username=?", (username,)).fetchone()
        # Equal-cost unknown-user path. The HTTP layer bounds the evidence size.
        salt = bytes.fromhex(row["salt"]) if row else bytes(16)
        actual = await asyncio.to_thread(_password_hash, password, salt)
        expected = row["digest"] if row else "0" * 128
        if not hmac.compare_digest(actual, expected):
            raise AuthenticationError("invalid credentials")
        return Identity("termx:local", username, "password")


class AuthenticationService:
    def __init__(self, path: Path | None = None, *, adapters: tuple[AuthenticationPort, ...] = ()) -> None:
        self.path = path or config_dir() / "identity.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        from termx.private_files import protect_private_path
        if os.name == 'nt':
            # SQLite journals inherit this directory's ACL; protect it before
            # any private signing key or password hash can reach disk.
            protect_private_path(self.path.parent, directory=True)
        self._lock = threading.RLock()
        # Create with restrictive permissions before sqlite opens the file.
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        protect_private_path(self.path)
        with self._db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS signing_keys (id TEXT PRIMARY KEY, secret TEXT NOT NULL, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS principals (
                    id TEXT PRIMARY KEY, display_name TEXT NOT NULL, scopes TEXT NOT NULL,
                    policy_version INTEGER NOT NULL DEFAULT 1, enabled INTEGER NOT NULL DEFAULT 1);
                CREATE TABLE IF NOT EXISTS identities (
                    issuer TEXT NOT NULL, subject TEXT NOT NULL, principal_id TEXT NOT NULL,
                    PRIMARY KEY (issuer, subject));
                CREATE TABLE IF NOT EXISTS passwords (username TEXT PRIMARY KEY, salt TEXT NOT NULL, digest TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY, principal_id TEXT NOT NULL, device_name TEXT NOT NULL,
                    strength TEXT NOT NULL, created REAL NOT NULL, last_seen REAL NOT NULL,
                    expires REAL NOT NULL, revoked INTEGER NOT NULL DEFAULT 0,
                    csrf_hash TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS refresh_tokens (
                    digest TEXT PRIMARY KEY, session_id TEXT NOT NULL, consumed INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS attempts (bucket TEXT PRIMARY KEY, count INTEGER NOT NULL, expires REAL NOT NULL);
            """)
            if "adapter_id" not in {row["name"] for row in db.execute("PRAGMA table_info(sessions)")}:
                db.execute("ALTER TABLE sessions ADD COLUMN adapter_id TEXT NOT NULL DEFAULT 'local-password'")
            if "grant_scopes" not in {row["name"] for row in db.execute("PRAGMA table_info(sessions)")}:
                db.execute("ALTER TABLE sessions ADD COLUMN grant_scopes TEXT")
            if "locked" not in {row["name"] for row in db.execute("PRAGMA table_info(sessions)")}:
                db.execute("ALTER TABLE sessions ADD COLUMN locked INTEGER NOT NULL DEFAULT 0")
            if "auth_generation" not in {row["name"] for row in db.execute("PRAGMA table_info(sessions)")}:
                db.execute("ALTER TABLE sessions ADD COLUMN auth_generation INTEGER NOT NULL DEFAULT 0")
            if "retired_after" not in {row["name"] for row in db.execute("PRAGMA table_info(signing_keys)")}:
                db.execute("ALTER TABLE signing_keys ADD COLUMN retired_after REAL")
            if db.execute("SELECT value FROM metadata WHERE key='host_id'").fetchone() is None:
                db.execute("INSERT INTO metadata VALUES ('host_id', ?)", (uuid.uuid4().hex,))
            self.host_id = db.execute("SELECT value FROM metadata WHERE key='host_id'").fetchone()[0]
            if db.execute("SELECT value FROM metadata WHERE key='active_key'").fetchone() is None:
                self._rotate_key(db)
        self.issuer = f"urn:termx:host:{self.host_id}"
        self.audience = f"termx-api:{self.host_id}"
        self.adapters: dict[str, AuthenticationPort] = {"local-password": LocalPasswordAdapter(self)}
        for adapter in adapters:
            self.register_adapter(adapter)
        with self._db() as db:
            runtime = db.execute("SELECT value FROM metadata WHERE key='runtime_adapter_config'").fetchone()
        if runtime:
            from termx.identity_adapters import prepare_adapter_configuration
            prepared = prepare_adapter_configuration(self, json.loads(runtime[0]), replace=True)
            self.adapters = {'local-password': self.adapters['local-password'], **{a.id: a for a in prepared}}

    def register_adapter(self, adapter: AuthenticationPort) -> None:
        """Host composition only; authentication plugins cannot be installed by agents."""
        if adapter.id in self.adapters:
            raise ValueError("duplicate authentication adapter")
        self.adapters[adapter.id] = adapter

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            db = sqlite3.connect(self.path, timeout=10)
            db.row_factory = sqlite3.Row
            try:
                db.execute("BEGIN IMMEDIATE")
                yield db
                db.commit()
            except Exception:
                db.rollback()
                raise
            finally:
                db.close()

    @property
    def has_runtime_configuration(self) -> bool:
        with self._db() as db:
            return db.execute("SELECT 1 FROM metadata WHERE key='runtime_adapter_config'").fetchone() is not None

    @property
    def configured(self) -> bool:
        with self._db() as db:
            return db.execute("SELECT 1 FROM metadata WHERE key='configured'").fetchone() is not None

    @property
    def migration_deadline(self) -> float:
        with self._db() as db:
            row = db.execute("SELECT value FROM metadata WHERE key='migration_deadline'").fetchone()
            return float(row[0]) if row else 0

    def methods(self) -> list[dict[str, str]]:
        if not self.configured:
            return []
        with self._db() as db:
            disabled = set()
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='adapter_status'").fetchone():
                disabled = {row[0] for row in db.execute("SELECT adapter_id FROM adapter_status WHERE enabled=0")}
        return [{"id": adapter.id, "label": adapter.label,
                 "flow": "redirect" if hasattr(adapter, "begin") else "credentials"}
                for adapter in self.adapters.values() if adapter.id not in disabled]

    def setup_owner(self, username: str, password: str) -> Principal:
        username = username.strip()
        if not username or len(username) > 128 or not 12 <= len(password) <= 1024:
            raise ValueError("username required; password must be 12–1024 characters")
        salt = secrets.token_bytes(16)
        digest = _password_hash(password, salt)
        pid = uuid.uuid4().hex
        with self._db() as db:
            if db.execute("SELECT 1 FROM metadata WHERE key='configured'").fetchone():
                raise AuthenticationError("authentication is already configured")
            db.execute("INSERT INTO principals (id, display_name, scopes) VALUES (?, ?, ?)", (pid, username, json.dumps(SCOPES)))
            db.execute("INSERT INTO identities VALUES ('termx:local', ?, ?)", (username, pid))
            db.execute("INSERT INTO passwords VALUES (?, ?, ?)", (username, salt.hex(), digest))
            db.execute("INSERT INTO metadata VALUES ('configured', '1')")
            db.execute("INSERT INTO metadata VALUES ('migration_deadline', ?)", (str(time() + MIGRATION_TTL),))
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='host_roles'").fetchone():
                db.execute("INSERT INTO host_roles VALUES (?, 'owner', 1)", (pid,))
                db.execute("INSERT OR REPLACE INTO metadata VALUES ('authorization_initialized', '1')")
        log_event("auth_setup", principal_id=pid)
        return Principal(pid, username, tuple(SCOPES), 1)

    def create_principal(self, display_name: str, scopes: list[str]) -> Principal:
        if not display_name.strip() or len(display_name) > 128 or not set(scopes) <= set(SCOPES):
            raise ValueError("invalid display name or scopes")
        pid = uuid.uuid4().hex
        with self._db() as db:
            db.execute("INSERT INTO principals (id, display_name, scopes) VALUES (?, ?, ?)",
                       (pid, display_name.strip(), json.dumps(sorted(set(scopes)))))
        log_event("auth_principal_created", principal_id=pid)
        return Principal(pid, display_name.strip(), tuple(sorted(set(scopes))), 1)

    def create_local_user(self, username: str, password: str, scopes: list[str]) -> Principal:
        username = username.strip()
        if not username or len(username) > 128 or not 12 <= len(password) <= 1024 or not set(scopes) <= set(SCOPES):
            raise ValueError("invalid username/scopes; password must be 12–1024 characters")
        salt = secrets.token_bytes(16)
        digest = _password_hash(password, salt)
        pid = uuid.uuid4().hex
        try:
            with self._db() as db:
                db.execute("INSERT INTO principals (id, display_name, scopes) VALUES (?, ?, ?)",
                           (pid, username, json.dumps(sorted(set(scopes)))))
                db.execute("INSERT INTO identities VALUES ('termx:local', ?, ?)", (username, pid))
                db.execute("INSERT INTO passwords VALUES (?, ?, ?)", (username, salt.hex(), digest))
        except sqlite3.IntegrityError as exc:
            raise ValueError("username unavailable") from exc
        log_event("auth_local_user_created", principal_id=pid)
        return Principal(pid, username, tuple(sorted(set(scopes))), 1)

    def revoke_all(self, principal_id: str) -> int:
        with self._db() as db:
            result = db.execute("UPDATE sessions SET revoked=1 WHERE principal_id=? AND revoked=0", (principal_id,))
        log_event("auth_sessions_revoked", principal_id=principal_id, count=result.rowcount)
        return result.rowcount

    def end_legacy_migration(self) -> None:
        with self._db() as db:
            db.execute("INSERT OR REPLACE INTO metadata VALUES ('migration_deadline', '0')")
        log_event("auth_legacy_migration_ended")

    def session_by_id(self, session_id: str) -> SessionIdentity | None:
        """Trusted host workflows only; never accepts a session id as API auth."""
        with self._db() as db:
            session = db.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
            principal = self._live_principal(db, session)
        return SessionIdentity(principal, session_id, session['expires']) if principal else None

    def execution_session(self, session_id: str) -> SessionIdentity | None:
        """Enrolled background execution only; never authenticates API/capture.

        Lock is a UI transport boundary, while revoke/expiry/current policy
        continue to constrain the already authorized job and its device grant.
        """
        with self._db() as db:
            session=db.execute('SELECT * FROM sessions WHERE id=?',(session_id,)).fetchone()
            principal=self._live_principal(db,session,allow_locked=True)
        if not principal:return None
        from dataclasses import replace
        return SessionIdentity(replace(principal,authority_execution=True),session_id,session['expires'])

    def principal_by_id(self, principal_id: str) -> Principal | None:
        with self._db() as db:
            row = db.execute("SELECT * FROM principals WHERE id=? AND enabled=1", (principal_id,)).fetchone()
        return self._principal(row) if row else None

    def current_principal(self, principal: Principal) -> Principal | None:
        """Reload live account authority without expanding a device session grant."""
        if principal.authority_session_id:
            current = (self.execution_session if principal.authority_execution else self.session_by_id)(principal.authority_session_id)
            return current.principal if current and current.principal.id == principal.id else None
        return self.principal_by_id(principal.id)

    def principal_for(self, identity: Identity) -> Principal | None:
        with self._db() as db:
            row = db.execute("""SELECT p.* FROM principals p JOIN identities i ON i.principal_id=p.id
                WHERE i.issuer=? AND i.subject=? AND p.enabled=1""", (identity.issuer, identity.subject)).fetchone()
        return self._principal(row) if row else None

    @staticmethod
    def _principal(row: sqlite3.Row) -> Principal:
        return Principal(row["id"], row["display_name"], tuple(json.loads(row["scopes"])), row["policy_version"])

    def map_identity(self, identity: Identity, principal_id: str) -> None:
        """Administrator composition API; deliberately absent from agent tools."""
        with self._db() as db:
            if not db.execute("SELECT 1 FROM principals WHERE id=?", (principal_id,)).fetchone():
                raise ValueError("principal not found")
            db.execute("INSERT INTO identities VALUES (?, ?, ?)", (identity.issuer, identity.subject, principal_id))

    def set_scopes(self, principal_id: str, scopes: list[str]) -> None:
        if not set(scopes) <= set(SCOPES):
            raise ValueError("unknown scopes")
        with self._db() as db:
            db.execute("UPDATE principals SET scopes=?, policy_version=policy_version+1 WHERE id=?", (json.dumps(scopes), principal_id))

    def disable(self, principal_id: str) -> None:
        with self._db() as db:
            db.execute("UPDATE principals SET enabled=0 WHERE id=?", (principal_id,))
            db.execute("UPDATE sessions SET revoked=1 WHERE principal_id=?", (principal_id,))

    def _attempt(self, peer: str) -> None:
        # Per-peer persistent limiter; usernames cannot be cycled to bypass it.
        bucket = _digest(peer)
        now = time()
        with self._db() as db:
            db.execute("DELETE FROM attempts WHERE expires<=?", (now,))
            row = db.execute("SELECT count FROM attempts WHERE bucket=?", (bucket,)).fetchone()
            if row and row[0] >= 10:
                raise LoginLimited("too many sign-in attempts; retry later")
            db.execute("""INSERT INTO attempts VALUES (?, 1, ?) ON CONFLICT(bucket)
                DO UPDATE SET count=count+1""", (bucket, now + 60))

    async def verify_login(self, adapter_id: str, evidence: dict[str, str], *, peer: str):
        """Canonical enabled-adapter evidence verification without issuing a session."""
        self._attempt(peer)
        adapter = self.adapters.get(adapter_id)
        if not self.configured or adapter is None:
            raise AuthenticationError("invalid credentials")
        with self._db() as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='adapter_status'").fetchone():
                status = db.execute("SELECT enabled FROM adapter_status WHERE adapter_id=?", (adapter_id,)).fetchone()
                if status and not status[0]:
                    raise AuthenticationError("authentication method disabled")
        try:
            identity = await adapter.authenticate(evidence)
            principal = self.principal_for(identity)
        except AuthenticationError:
            log_event("auth_login_denied", adapter_id=adapter_id)
            raise
        except Exception as exc:
            log_event("auth_adapter_error", adapter_id=adapter_id)
            raise AuthenticationError("identity provider unavailable") from exc
        if principal is None:
            raise AuthenticationError("invalid credentials")
        return identity,principal

    async def login(self, adapter_id: str, evidence: dict[str, str], *, peer: str, device_name: str = "") -> SessionCredentials:
        identity,principal=await self.verify_login(adapter_id,evidence,peer=peer)
        raw, csrf, sid = secrets.token_urlsafe(48), secrets.token_urlsafe(32), uuid.uuid4().hex
        now = time()
        with self._db() as db:
            db.execute("INSERT INTO sessions (id, principal_id, device_name, strength, created, last_seen, expires, revoked, csrf_hash, adapter_id) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?)",
                       (sid, principal.id, device_name[:128], identity.strength, now, now, now + ABSOLUTE_TTL, _digest(csrf), adapter_id))
            db.execute("INSERT INTO refresh_tokens VALUES (?, ?, 0)", (_digest(raw), sid))
            token = self._issue(db, principal, sid)
        log_event("auth_login", principal_id=principal.id, session_id=sid, adapter_id=adapter_id)
        return SessionCredentials(token, raw, csrf, sid)

    @staticmethod
    def _rotate_key(db: sqlite3.Connection) -> str:
        now = time()
        # Retirement starts at rotation, not key creation: an old active key
        # may have signed a still-valid token just before this call.
        old = db.execute("SELECT value FROM metadata WHERE key='active_key'").fetchone()
        if old:
            db.execute("UPDATE signing_keys SET retired_after=? WHERE id=?", (now + ACCESS_TTL, old[0]))
        kid = uuid.uuid4().hex
        db.execute("INSERT INTO signing_keys VALUES (?, ?, ?, NULL)", (kid, secrets.token_urlsafe(48), now))
        db.execute("INSERT OR REPLACE INTO metadata VALUES ('active_key', ?)", (kid,))
        db.execute("DELETE FROM signing_keys WHERE retired_after<=?", (now,))
        return kid

    def rotate_signing_key(self) -> None:
        with self._db() as db:
            self._rotate_key(db)
        log_event("auth_signing_key_rotated")

    def _issue(self, db: sqlite3.Connection, principal: Principal, sid: str) -> str:
        kid = db.execute("SELECT value FROM metadata WHERE key='active_key'").fetchone()[0]
        key = db.execute("SELECT secret FROM signing_keys WHERE id=?", (kid,)).fetchone()[0]
        now = int(time())
        return jwt.encode({"iss": self.issuer, "aud": self.audience, "sub": principal.id,
                           "sid": sid, "iat": now, "exp": now + ACCESS_TTL,
                           "token_type": "access", "policy_version": principal.policy_version,
                           "auth_generation": db.execute("SELECT auth_generation FROM sessions WHERE id=?",(sid,)).fetchone()[0]},
                          key, algorithm="HS256", headers={"typ": "at+jwt", "kid": kid})

    def resolve(self, token: str | None, *, allow_locked: bool = False) -> SessionIdentity | None:
        if not token or len(token) > 16384:
            return None
        try:
            header = jwt.get_unverified_header(token)
            if header.get("typ") != "at+jwt" or header.get("alg") != "HS256":
                return None
            kid = header.get("kid")
            if not isinstance(kid, str):
                return None
            with self._db() as db:
                key = db.execute("SELECT secret FROM signing_keys WHERE id=?", (kid,)).fetchone()
                if key is None:
                    return None
                claims = jwt.decode(token, key[0], algorithms=["HS256"], issuer=self.issuer, audience=self.audience,
                                    options={"require": ["iss", "aud", "sub", "sid", "iat", "exp", "token_type", "policy_version"], "strict_aud": True})
                if claims["token_type"] != "access" or not isinstance(claims["sid"], str):
                    return None
                session = db.execute("SELECT * FROM sessions WHERE id=? AND principal_id=?", (claims["sid"], claims["sub"])).fetchone()
                if session is not None and claims.get("auth_generation",0) != session["auth_generation"]:
                    return None
                principal = self._live_principal(db, session,allow_locked=allow_locked)
                if principal is None:
                    return None
                return SessionIdentity(principal, session["id"], min(claims["exp"], session["expires"], session["last_seen"] + IDLE_TTL))
        except (jwt.PyJWTError, ValueError, TypeError, KeyError):
            return None

    def _live_principal(self, db: sqlite3.Connection, session: sqlite3.Row | None, *, allow_locked: bool = False) -> Principal | None:
        now = time()
        if session is None or session["revoked"] or session["expires"] <= now or session["last_seen"] + IDLE_TTL <= now:
            return None
        if session['locked'] and not allow_locked:
            return None
        row = db.execute("SELECT * FROM principals WHERE id=? AND enabled=1", (session["principal_id"],)).fetchone()
        if not row:
            return None
        scopes = tuple(json.loads(row['scopes']))
        if session['grant_scopes'] is not None:
            granted = set(json.loads(session['grant_scopes']))
            scopes = tuple(scope for scope in scopes if scope in granted)
        return Principal(row['id'], row['display_name'], scopes, row['policy_version'], session['id'])

    def refresh(self, raw: str) -> SessionCredentials:
        failure = "invalid refresh credential"
        result = None
        locked = False
        with self._db() as db:
            row = db.execute("SELECT * FROM refresh_tokens WHERE digest=?", (_digest(raw),)).fetchone()
            if row:
                sid = row["session_id"]
                if row["consumed"]:
                    db.execute("UPDATE sessions SET revoked=1 WHERE id=?", (sid,))
                    failure = "refresh reuse detected; sign in again"
                else:
                    session = db.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
                    principal = self._live_principal(db, session)
                    locked = bool(session and session['locked'] and self._live_principal(db,session,allow_locked=True))
                    if principal:
                        new_raw, csrf = secrets.token_urlsafe(48), secrets.token_urlsafe(32)
                        db.execute("UPDATE refresh_tokens SET consumed=1 WHERE digest=?", (_digest(raw),))
                        db.execute("INSERT INTO refresh_tokens VALUES (?, ?, 0)", (_digest(new_raw), sid))
                        db.execute("UPDATE sessions SET last_seen=?, csrf_hash=? WHERE id=?", (time(), _digest(csrf), sid))
                        result = SessionCredentials(self._issue(db, principal, sid), new_raw, csrf, sid)
        # Commit replay revocation before raising (never roll it back).
        if result is None:
            if locked:
                raise SessionLocked('Session locked; unlock required')
            raise AuthenticationError(failure)
        log_event("auth_refresh", session_id=result.session_id)
        return result

    def valid_csrf(self, credential: str | None, csrf: str | None, *, refresh: bool = False) -> bool:
        if not credential or not csrf:
            return False
        identity = None if refresh else self.resolve(credential)
        with self._db() as db:
            if refresh:
                row = db.execute("""SELECT s.csrf_hash FROM sessions s JOIN refresh_tokens r ON r.session_id=s.id
                    WHERE r.digest=? AND r.consumed=0 AND s.revoked=0""", (_digest(credential),)).fetchone()
            else:
                row = db.execute("SELECT csrf_hash FROM sessions WHERE id=?", (identity.session_id,)).fetchone() if identity else None
        return bool(row and hmac.compare_digest(row[0], _digest(csrf)))

    def list_sessions(self, principal_id: str) -> list[dict[str, Any]]:
        with self._db() as db:
            rows = db.execute("""SELECT id, device_name, strength, created, last_seen, expires, revoked
                FROM sessions WHERE principal_id=? ORDER BY created DESC""", (principal_id,)).fetchall()
        return [dict(row) for row in rows]

    def revoke(self, session_id: str, principal_id: str) -> bool:
        with self._db() as db:
            result = db.execute("UPDATE sessions SET revoked=1 WHERE id=? AND principal_id=?", (session_id, principal_id))
        if result.rowcount:
            log_event("auth_session_revoked", session_id=session_id, principal_id=principal_id)
        return bool(result.rowcount)
