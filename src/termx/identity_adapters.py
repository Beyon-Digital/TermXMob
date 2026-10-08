"""Trusted authentication adapters. No client-selected issuer or key source.

OIDC uses one-time browser-bound Authorization Code + PKCE flows. The custom
reference accepts a separately typed signed assertion from a configured system;
its identity still needs explicit administrator mapping to a TermX principal.
"""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
import re
from dataclasses import asdict, dataclass
from time import time
from urllib.parse import urlencode, urlsplit

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import load_pem_public_key

from termx.identity import AuthenticationError, AuthenticationService, Identity


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _https_url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password or parsed.fragment:
        raise ValueError("identity-provider URLs must use HTTPS without credentials or fragments")
    return value


def _group_config(claim,ttl):
    if claim is not None and (not isinstance(claim,str) or not re.fullmatch(r'[^\s\x00-\x1f]{1,256}',claim)):
        raise ValueError('Group claim must be an explicit bounded top-level claim name')
    if isinstance(ttl,bool) or not isinstance(ttl,int) or not 60<=ttl<=86400:
        raise ValueError('Group membership TTL must be60–86400 seconds')


def _verified_groups(claims,claim):
    if claim is None:return None
    values=claims.get(claim,[])
    if (not isinstance(values,list) or len(values)>128 or any(not isinstance(v,str) or not v or len(v)>256 for v in values)):
        raise AuthenticationError('Malformed configured group claim')
    return tuple(sorted(set(values)))


@dataclass(frozen=True)
class OidcConfig:
    id: str
    label: str
    issuer: str
    client_id: str
    redirect_uri: str
    groups_claim: str | None = None
    membership_ttl: int = 300

    def __post_init__(self):
        _group_config(self.groups_claim,self.membership_ttl)
        _https_url(self.issuer)
        redirect = urlsplit(self.redirect_uri)
        if redirect.scheme == "http" and redirect.hostname == "127.0.0.1" and redirect.port and not redirect.username and not redirect.password and not redirect.fragment:
            # RFC 8252 public native clients use a registered loopback callback.
            # Exact URI matching and PKCE remain mandatory; issuer stays TLS.
            if redirect.path != f"/auth/oidc/{self.id}/callback":
                raise ValueError("native callback must match the adapter route")
        else:
            _https_url(self.redirect_uri)
        if not re.fullmatch(r"[a-z][a-z0-9-]{0,63}", self.id) or not self.client_id or urlsplit(self.redirect_uri).query or urlsplit(self.issuer).query:
            raise ValueError("provider id/client id and an exact redirect URI are required")


class OidcAdapter:
    def __init__(self, service: AuthenticationService, config: OidcConfig, *, client: httpx.AsyncClient | None = None):
        self.service, self.config = service, config
        self.id, self.label = config.id, config.label
        self.client = client
        self.config_hash = _hash(json.dumps(asdict(config), sort_keys=True))
        with service._db() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS oidc_flows (
                state_hash TEXT PRIMARY KEY, adapter_id TEXT NOT NULL, config_hash TEXT NOT NULL,
                binding_hash TEXT NOT NULL, verifier TEXT NOT NULL, nonce TEXT NOT NULL,
                expires REAL NOT NULL)""")

    async def _request(self, method: str, url: str, **kwargs):
        _https_url(url)
        if self.client:
            response = await self.client.request(method, url, follow_redirects=False, **kwargs)
        else:
            async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
                response = await client.request(method, url, **kwargs)
        response.raise_for_status()
        if response.is_redirect or len(response.content) > 1024 * 1024:
            raise AuthenticationError("invalid identity provider response")
        result = response.json()
        if not isinstance(result, dict):
            raise AuthenticationError("invalid identity provider response")
        return result

    async def _metadata(self):
        data = await self._request("GET", self.config.issuer.rstrip("/") + "/.well-known/openid-configuration")
        if data.get("issuer") != self.config.issuer:
            raise AuthenticationError("identity provider issuer mismatch")
        for field in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
            _https_url(data[field])
        if "S256" not in data.get("code_challenge_methods_supported", []):
            raise AuthenticationError("identity provider must support PKCE S256")
        return data

    async def begin(self) -> tuple[str, str]:
        try:
            metadata = await self._metadata()
            state, binding, verifier, nonce = (secrets.token_urlsafe(32) for _ in range(4))
            challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
            with self.service._db() as db:
                db.execute("DELETE FROM oidc_flows WHERE expires<=?", (time(),))
                db.execute("INSERT INTO oidc_flows VALUES (?, ?, ?, ?, ?, ?, ?)",
                           (_hash(state), self.id, self.config_hash, _hash(binding), verifier, nonce, time() + 300))
            params = {"response_type": "code", "client_id": self.config.client_id,
                      "redirect_uri": self.config.redirect_uri, "scope": "openid",
                      "state": state, "nonce": nonce, "code_challenge": challenge, "code_challenge_method": "S256"}
            separator = "&" if urlsplit(metadata["authorization_endpoint"]).query else "?"
            return metadata["authorization_endpoint"] + separator + urlencode(params), binding
        except Exception as exc:
            raise AuthenticationError("identity provider unavailable") from exc

    async def authenticate(self, evidence: dict[str, str]) -> Identity:
        state, binding, code = evidence.get("state", ""), evidence.get("binding", ""), evidence.get("code", "")
        if not state or not binding or not code or max(map(len, (state, binding, code))) > 4096:
            raise AuthenticationError("invalid identity callback")
        with self.service._db() as db:
            flow = db.execute("SELECT * FROM oidc_flows WHERE state_hash=?", (_hash(state),)).fetchone()
            if not flow or flow["adapter_id"] != self.id or flow["config_hash"] != self.config_hash or flow["expires"] <= time() or flow["binding_hash"] != _hash(binding):
                raise AuthenticationError("invalid or expired identity callback")
            db.execute("DELETE FROM oidc_flows WHERE state_hash=?", (_hash(state),))
        try:
            metadata = await self._metadata()
            tokens = await self._request("POST", metadata["token_endpoint"], data={
                "grant_type": "authorization_code", "client_id": self.config.client_id,
                "code": code, "redirect_uri": self.config.redirect_uri, "code_verifier": flow["verifier"]})
            raw = tokens["id_token"]
            header = jwt.get_unverified_header(raw)
            if header.get("alg") != "RS256" or not isinstance(header.get("kid"), str):
                raise AuthenticationError("invalid identity token")
            jwks = await self._request("GET", metadata["jwks_uri"])
            candidates = [key for key in jwks.get("keys", []) if key.get("kid") == header["kid"] and key.get("kty") == "RSA" and key.get("use", "sig") == "sig" and key.get("alg", "RS256") == "RS256"]
            if len(candidates) != 1:
                raise AuthenticationError("unknown identity signing key")
            claims = jwt.decode(raw, jwt.PyJWK.from_dict(candidates[0]).key, algorithms=["RS256"],
                                issuer=self.config.issuer, audience=self.config.client_id,
                                options={"require": ["iss", "aud", "sub", "iat", "exp", "nonce"]})
            if claims["nonce"] != flow["nonce"]:
                raise AuthenticationError("identity nonce mismatch")
            aud = claims["aud"]
            if isinstance(aud, list) and len(aud) > 1 and claims.get("azp") != self.config.client_id:
                raise AuthenticationError("identity authorized party mismatch")
            if claims.get("azp", self.config.client_id) != self.config.client_id:
                raise AuthenticationError("identity authorized party mismatch")
            return Identity(self.config.issuer, claims["sub"], "oidc",_verified_groups(claims,self.config.groups_claim),self.config.membership_ttl)
        except AuthenticationError:
            raise
        except Exception as exc:
            raise AuthenticationError("identity verification failed") from exc


class SignedAssertionAdapter:
    """Custom integration reference with pinned Ed25519 key and replay defense."""
    def __init__(self, service: AuthenticationService, *, adapter_id: str, label: str,
                 issuer: str, audience: str, public_key: str,groups_claim: str | None=None,membership_ttl: int=300):
        _group_config(groups_claim,membership_ttl)
        self.groups_claim,self.membership_ttl=groups_claim,membership_ttl
        self.service, self.id, self.label = service, adapter_id, label
        key = load_pem_public_key(public_key.encode())
        if not isinstance(key, Ed25519PublicKey) or not issuer or not audience:
            raise ValueError("a pinned Ed25519 public key, issuer and audience are required")
        self.issuer, self.audience, self.public_key = issuer, audience, key
        with service._db() as db:
            db.execute("CREATE TABLE IF NOT EXISTS identity_assertions (adapter_id TEXT, jti TEXT, expires REAL, PRIMARY KEY(adapter_id, jti))")

    async def authenticate(self, evidence: dict[str, str]) -> Identity:
        try:
            raw = evidence.get("assertion", "")
            if len(raw) > 16384 or jwt.get_unverified_header(raw).get("typ") != "termx-identity+jwt":
                raise AuthenticationError("invalid identity assertion")
            claims = jwt.decode(raw, self.public_key, algorithms=["EdDSA"], issuer=self.issuer, audience=self.audience,
                                options={"require": ["iss", "aud", "sub", "iat", "exp", "jti"], "strict_aud": True})
            if not isinstance(claims["jti"], str) or claims["exp"] - claims["iat"] > 300:
                raise AuthenticationError("invalid identity assertion")
            with self.service._db() as db:
                db.execute("DELETE FROM identity_assertions WHERE expires<=?", (time(),))
                db.execute("INSERT INTO identity_assertions VALUES (?, ?, ?)", (self.id, claims["jti"], claims["exp"]))
            return Identity(self.issuer, claims["sub"], "signed-assertion",_verified_groups(claims,self.groups_claim),self.membership_ttl)
        except Exception as exc:
            raise AuthenticationError("invalid or replayed identity assertion") from exc


def prepare_adapter_configuration(service: AuthenticationService, config: dict, *, replace: bool = False):
    """Validate trusted built-in provider configuration without granting identities."""
    if not isinstance(config, dict) or set(config) - {"version", "adapters", "bindings"} or config.get("version") != 1:
        raise ValueError("invalid authentication adapter configuration")
    if not isinstance(config.get('adapters', []), list) or not isinstance(config.get('bindings', []), list):
        raise ValueError('adapter and binding arrays required')
    prepared = []
    ids = {'local-password'} if replace else set(service.adapters)
    for item in config.get("adapters", []):
        adapter_id = item["id"]
        if not re.fullmatch(r"[a-z][a-z0-9-]{0,63}", adapter_id) or adapter_id in ids:
            raise ValueError("invalid or duplicate authentication adapter id")
        ids.add(adapter_id)
        if item["kind"] == "oidc":
            if set(item)-{'id','kind','label','issuer','client_id','redirect_uri','groups_claim','membership_ttl'}:
                raise ValueError('unknown OIDC configuration field')
            prepared.append(OidcAdapter(service, OidcConfig(adapter_id, item["label"], item["issuer"], item["client_id"], item["redirect_uri"],item.get('groups_claim'),item.get('membership_ttl',300))))
        elif item["kind"] == "signed-assertion":
            if set(item)-{'id','kind','label','issuer','audience','public_key','groups_claim','membership_ttl'}:
                raise ValueError('unknown assertion configuration field')
            prepared.append(SignedAssertionAdapter(service, adapter_id=adapter_id, label=item["label"], issuer=item["issuer"],
                                                   audience=item["audience"], public_key=item["public_key"],groups_claim=item.get('groups_claim'),membership_ttl=item.get('membership_ttl',300)))
        else:
            raise ValueError("unknown authentication adapter kind")
    with service._db() as db:
        for binding in config.get("bindings", []):
            if set(binding) != {'principal_id','issuer','subject'} or not all(isinstance(value,str) and value and len(value)<=1024 for value in binding.values()):
                raise ValueError('invalid identity binding')
            pid = binding["principal_id"]
            if not db.execute("SELECT 1 FROM principals WHERE id=? AND enabled=1", (pid,)).fetchone():
                raise ValueError("authentication binding references an unknown principal")
            old = db.execute("SELECT principal_id FROM identities WHERE issuer=? AND subject=?", (binding["issuer"], binding["subject"])).fetchone()
            if old and old[0] != pid:
                raise ValueError("identity rebinding requires explicit migration")
    return prepared


def load_configured_adapters(service: AuthenticationService, path) -> None:
    """Atomic startup composition from an owner-protected administrator file."""
    import os
    from pathlib import Path
    config_path = Path(path)
    from termx.private_files import private_path_permissions
    if not private_path_permissions(config_path):
        raise ValueError("authentication adapter configuration must be owner-readable only")
    config = json.loads(config_path.read_text())
    prepared = prepare_adapter_configuration(service, config)
    with service._db() as db:
        for binding in config.get("bindings", []):
            db.execute("INSERT OR IGNORE INTO identities VALUES (?, ?, ?)", (binding["issuer"], binding["subject"], binding["principal_id"]))
    for adapter in prepared:
        service.register_adapter(adapter)
