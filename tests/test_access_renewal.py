"""A socket admitted with a short-lived access token keeps its session when that token expires."""
from __future__ import annotations

import asyncio

import jwt

from termx import identity as identity_module
from termx.identity import AuthenticationService

PASSWORD = "test-only-password-123"


def _service(tmp_path):
    service = AuthenticationService(tmp_path / "auth.sqlite3")
    service.setup_owner("owner", PASSWORD)
    return service


def _login(service):
    return asyncio.run(service.login("local-password", {"username": "owner", "password": PASSWORD}, peer="127.0.0.1"))


def _expired(service, monkeypatch):
    monkeypatch.setattr(identity_module, "ACCESS_TTL", -30)
    credentials = _login(service)
    monkeypatch.undo()
    assert service.resolve(credentials.access_token) is None
    return credentials


def test_an_expired_access_token_of_a_live_session_renews_for_the_same_session(tmp_path, monkeypatch):
    service = _service(tmp_path)
    credentials = _expired(service, monkeypatch)
    renewed = service.renew_access(credentials.access_token)
    assert renewed and renewed != credentials.access_token
    live = service.resolve(renewed)
    assert live and live.session_id == credentials.session_id


def test_renewal_stops_when_the_session_is_revoked_or_the_token_is_not_ours(tmp_path, monkeypatch):
    service = _service(tmp_path)
    credentials = _expired(service, monkeypatch)
    forged = jwt.encode({"sub": "owner"}, "not-the-key", algorithm="HS256", headers={"typ": "at+jwt", "kid": "nope"})
    assert service.renew_access(forged) is None and service.renew_access("") is None and service.renew_access(None) is None
    principal = service.resolve(service.renew_access(credentials.access_token)).principal
    assert service.revoke(credentials.session_id, principal.id)
    assert service.renew_access(credentials.access_token) is None


def test_renewal_refuses_a_token_issued_under_an_older_policy(tmp_path, monkeypatch):
    service = _service(tmp_path)
    credentials = _expired(service, monkeypatch)
    principal = service.resolve(service.renew_access(credentials.access_token)).principal
    with service._db() as db:
        db.execute("UPDATE principals SET policy_version=policy_version+1 WHERE id=?", (principal.id,))
    assert service.renew_access(credentials.access_token) is None
