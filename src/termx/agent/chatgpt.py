"""Host-owned Sign in with ChatGPT (OSS dynamic registration).

Contract: https://developers.openai.com/siwc/token-sharing-open-source/sign-in
Tokens live in the OS credential store; this file's registry contains only
validated account/client mappings and a stable host ID. Never log OAuth data.
"""
from __future__ import annotations

import asyncio
import base64
from contextlib import asynccontextmanager
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import tempfile
from time import time
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse
import uuid
import webbrowser

import httpx
import jwt

from termx.config import config_dir
from termx.agent.providers import ProviderError

ISSUER = "https://auth.openai.com"
RESOURCE = "https://api.openai.com/v1"
DIRECT_SCOPE = "chatgpt.tokens.use.direct"
USAGE_URL = "https://chatgpt.com/settings/usage"
SCOPE = f"openid profile email offline_access resource.invoke {DIRECT_SCOPE}"
TERMINAL_REFRESH_ERRORS = {
    "invalid_grant", "invalid_refresh_token", "token_expired", "refresh_token_expired",
    "refresh_token_invalidated", "refresh_token_reused",
}


class ChatGPTAccounts:
    def __init__(self, credentials, store, *, client=None, browser_open=None):
        self.credentials = credentials
        self.store = store
        self.client = client or httpx.AsyncClient(timeout=30, follow_redirects=False)
        self._own_client = client is None
        self.browser_open = browser_open or webbrowser.open
        self.path = config_dir() / "chatgpt-accounts.json"
        self._lock = asyncio.Lock()
        self._start_lock = asyncio.Lock()
        self._attempts: dict[str, dict] = {}
        self._refresh_tasks: set[asyncio.Task] = set()
        self._closing = False
        self._discovery: dict | None = None
        self._keys: dict | None = None
        self._keys_at = 0.0

    @asynccontextmanager
    async def _locked(self):
        # One daemon normally owns this directory. Serialize rotating refresh
        # tokens across daemons too, using a host-level advisory lock.
        async with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if os.name == 'nt':
                from termx.private_files import protect_private_path
                protect_private_path(self.path.parent, directory=True)
            fd = os.open(self.path.with_suffix(".lock"), os.O_CREAT | os.O_RDWR, 0o600)
            def acquire():
                if os.name == "nt":
                    import msvcrt
                    if os.fstat(fd).st_size == 0:
                        os.write(fd, b"0")
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
                else:
                    import fcntl
                    fcntl.flock(fd, fcntl.LOCK_EX)
            # Shield acquisition; cancellation must not leave a worker holding
            # a lock whose descriptor has already been closed/reused.
            worker = asyncio.create_task(asyncio.to_thread(acquire))
            try:
                try:
                    await asyncio.shield(worker)
                except asyncio.CancelledError:
                    await worker
                    raise
                yield
            finally:
                os.close(fd)

    def _read(self):
        if not self.path.exists():
            return {"host_id": f"urn:uuid:{uuid.uuid4()}", "accounts": {}}
        return json.loads(self.path.read_text())

    def _write(self, registry):
        fd, name = tempfile.mkstemp(dir=self.path.parent, prefix=".chatgpt-")
        try:
            with os.fdopen(fd, "w") as out:
                from termx.private_files import protect_private_path
                protect_private_path(Path(name))
                json.dump(registry, out)
                out.flush()
                os.fsync(out.fileno())
            os.replace(name, self.path)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    @staticmethod
    def _secret_key(account_id):
        return f"chatgpt.oauth.{account_id}"

    async def _tokens(self, account_id):
        value = await asyncio.to_thread(self.credentials.get, self._secret_key(account_id))
        return json.loads(value) if value else {}

    async def _save_tokens(self, account_id, value):
        await asyncio.to_thread(self.credentials.set, self._secret_key(account_id), json.dumps(value))

    async def _metadata(self):
        if self._discovery is None:
            response = await self.client.get(f"{ISSUER}/.well-known/openid-configuration")
            response.raise_for_status()
            data = response.json()
            if data.get("issuer") != ISSUER:
                raise ValueError("Unexpected ChatGPT identity issuer")
            for key in ("jwks_uri", "revocation_endpoint"):
                endpoint = data.get(key, "")
                parsed = urlparse(endpoint)
                if parsed.scheme != "https" or parsed.netloc != "auth.openai.com":
                    raise ValueError("Unexpected ChatGPT identity endpoint")
            self._discovery = data
        return self._discovery

    async def _identity(self, token, client_id, *, nonce=None, subject=None):
        try:
            header = jwt.get_unverified_header(token)
            if header.get("alg") != "RS256" or not header.get("kid"):
                raise ValueError("Invalid ChatGPT identity signature")
            metadata = await self._metadata()
            if not self._keys or time() - self._keys_at > 3600 or not any(
                k.get("kid") == header["kid"] for k in self._keys.get("keys", [])
            ):
                response = await self.client.get(metadata["jwks_uri"])
                response.raise_for_status()
                self._keys = response.json()
                self._keys_at = time()
            key = next(k for k in self._keys["keys"] if k.get("kid") == header["kid"]
                       and k.get("kty") == "RSA" and k.get("use", "sig") == "sig"
                       and k.get("alg", "RS256") == "RS256")
            claims = jwt.decode(token, jwt.PyJWK.from_dict(key).key, algorithms=["RS256"],
                                audience=client_id, issuer=ISSUER, leeway=5,
                                options={"require": ["sub", "exp", "iat", "aud", "iss"]})
            if claims.get("azp", client_id) != client_id or (isinstance(claims["aud"], list) and len(claims["aud"]) > 1 and claims.get("azp") != client_id):
                raise ValueError("ChatGPT identity authorized party did not match")
            if not isinstance(claims["sub"], str) or not claims["sub"]:
                raise ValueError("Missing ChatGPT account identity")
            if nonce is not None and not hmac.compare_digest(str(claims.get("nonce", "")), nonce):
                raise ValueError("ChatGPT sign-in nonce did not match")
            if subject is not None and claims["sub"] != subject:
                raise ValueError("ChatGPT sign-in returned a different account")
            return claims
        except (jwt.PyJWTError, StopIteration, KeyError, TypeError) as exc:
            raise ValueError("Could not validate ChatGPT account identity") from exc

    @staticmethod
    def _token_record(data, old=None):
        old = old or {}
        if not isinstance(data.get("access_token"), str) or not data["access_token"]:
            raise ValueError("ChatGPT did not return an access token")
        if str(data.get("token_type", "")).lower() != "bearer":
            raise ValueError("ChatGPT returned an unsupported token type")
        lifetime = data.get("expires_in")
        if isinstance(lifetime, bool) or not isinstance(lifetime, (int, float)) or lifetime <= 0:
            raise ValueError("ChatGPT returned an invalid token expiry")
        scopes = str(data.get("scope", " ".join(old.get("scopes", [])))).split()
        return {
            "access_token": data["access_token"], "refresh_token": data.get("refresh_token", ""),
            "id_token": data.get("id_token") or old.get("id_token", ""),
            "scopes": scopes, "expires_at": time() + lifetime,
            "earliest_refresh_at": data.get("earliest_refresh_at", 0),
        }

    async def accounts(self):
        async with self._locked():
            registry = self._read()
            result = []
            for account_id, account in registry["accounts"].items():
                tokens = await self._tokens(account_id)
                result.append({**account, "id": account_id, "provider_id": account_id,
                               "signed_in": bool(tokens.get("access_token")),
                               "plan_enabled": DIRECT_SCOPE in tokens.get("scopes", []),
                               "usage_url": USAGE_URL})
            return result

    async def start(self, owner, account_id=None):
        async with self._start_lock:
            return await self._start(owner, account_id)

    async def _start(self, owner, account_id=None):
        if self._closing:
            raise ValueError("ChatGPT connection is closing")
        if not self.credentials.available():
            raise ValueError("ChatGPT sign-in requires secure credential storage on the host")
        # Bound concurrent attempts and completed status retention.
        for key, item in list(self._attempts.items()):
            if item["expires_at"] < time() and item["status"] != "pending":
                self._attempts.pop(key)
        if len(self._attempts) >= 32:
            raise ValueError("Too many recent ChatGPT sign-in attempts. Try again later.")
        for attempt_id, pending in self._attempts.items():
            if pending["status"] != "pending":
                continue
            if not hmac.compare_digest(pending["owner"], owner):
                raise ValueError("A ChatGPT sign-in is already pending on this host")
            if pending.get("account_id") != account_id:
                raise ValueError("Cancel the pending ChatGPT sign-in before choosing another account")
            return {"attempt_id": attempt_id, "authorization_url": pending["authorization_url"],
                    "browser_opened": False, "expires_at": pending["expires_at"], "status": "pending"}
        async with self._locked():
            registry = self._read()
            selected = registry["accounts"].get(account_id) if account_id else None
            if not account_id and registry.get("pending_registrations", {}).get(owner):
                selected = {"client_id": registry["pending_registrations"][owner]}
            if account_id and not selected:
                raise ValueError("ChatGPT account not found")
            self._write(registry)  # Persist the host ID before first sign-in.
        attempt_id = secrets.token_urlsafe(24)
        attempt = {
            "owner": owner, "status": "pending", "expires_at": time() + 600,
            "state": secrets.token_urlsafe(32), "nonce": secrets.token_urlsafe(32),
            "verifier": secrets.token_urlsafe(48), "selected": selected, "account_id": account_id,
        }
        future = asyncio.get_running_loop().create_future()
        attempt["future"] = future

        async def callback(reader, writer):
            status, body = "400 Bad Request", "This sign-in callback is invalid."
            try:
                request = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 10)
                method, target, _ = request.split(b"\r\n", 1)[0].decode("ascii").split(" ", 2)
                parsed = urlparse(target)
                query = parse_qs(parsed.query)
                valid = (method == "GET" and parsed.path == "/auth/callback"
                         and len(query.get("state", [])) == 1
                         and hmac.compare_digest(query["state"][0], attempt["state"]))
                if valid and not future.done():
                    if any(len(v) != 1 for v in query.values()):
                        raise ValueError("Duplicate callback parameter")
                    future.set_result({k: v[0] for k, v in query.items()})
                    await attempt["done"].wait()
                    status = "200 OK"
                    body = ("ChatGPT sign-in complete. Return to TermX." if attempt["status"] == "completed"
                            else "ChatGPT sign-in could not be completed. Return to TermX for details.")
            except (ValueError, OSError, TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError):
                pass
            finally:
                encoded = body.encode()
                writer.write(f"HTTP/1.1 {status}\r\nContent-Type: text/plain; charset=utf-8\r\nCache-Control: no-store\r\nReferrer-Policy: no-referrer\r\nConnection: close\r\nContent-Length: {len(encoded)}\r\n\r\n".encode() + encoded)
                try:
                    await writer.drain()
                except OSError:
                    pass
                writer.close()
        server = await asyncio.start_server(callback, "127.0.0.1", 0)
        attempt["server"] = server
        attempt["done"] = asyncio.Event()
        attempt["redirect_uri"] = f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}/auth/callback"
        params = {
            "client_id": selected["client_id"] if selected else "dynamic_agent_client",
            "ext_agent_host_id": registry["host_id"], "response_type": "code",
            "redirect_uri": attempt["redirect_uri"], "scope": SCOPE, "resource": RESOURCE,
            "state": attempt["state"], "nonce": attempt["nonce"], "code_challenge_method": "S256",
            "code_challenge": base64.urlsafe_b64encode(hashlib.sha256(attempt["verifier"].encode()).digest()).decode().rstrip("="),
        }
        if selected:
            params["login_hint"] = selected.get("email", "")
        else:
            params["agent_name_hint"] = "TermX"
        # Omit id_token_hint so credentials never leave the host via a URL.
        url = f"{ISSUER}/api/accounts/authorize?{urlencode(params)}"
        attempt["authorization_url"] = url
        self._attempts[attempt_id] = attempt
        attempt["task"] = asyncio.create_task(self._complete(attempt))
        try:
            opened = await asyncio.to_thread(self.browser_open, url)
        except Exception:
            opened = False
        return {"attempt_id": attempt_id, "authorization_url": url, "browser_opened": bool(opened),
                "expires_at": attempt["expires_at"], "status": "pending"}

    def status(self, attempt_id, owner):
        item = self._attempts.get(attempt_id)
        if not item or not hmac.compare_digest(item["owner"], owner):
            raise ValueError("ChatGPT sign-in attempt not found")
        return {k: item[k] for k in ("status", "expires_at", "account_id", "message") if k in item}

    async def cancel(self, attempt_id, owner):
        self.status(attempt_id, owner)
        item = self._attempts[attempt_id]
        if item["status"] == "pending":
            item["task"].cancel()
            await asyncio.gather(item["task"], return_exceptions=True)
        return self.status(attempt_id, owner)

    async def _complete(self, attempt):
        try:
            query = await asyncio.wait_for(attempt["future"], 600)
            if query.get("error"):
                raise ValueError("ChatGPT sign-in was declined or cancelled")
            selected = attempt["selected"]
            client_id = query.get("client_id") or (selected["client_id"] if selected else "")
            if not client_id or client_id == "dynamic_agent_client":
                raise ValueError("ChatGPT registration did not return an issued client ID")
            if selected and client_id != selected["client_id"]:
                raise ValueError("ChatGPT sign-in returned a different client registration")
            if not query.get("code"):
                raise ValueError("ChatGPT sign-in did not return an authorization code")
            async with self._locked():
                registry = self._read()
                if not selected or not selected.get("subject"):
                    registry.setdefault("pending_registrations", {})[attempt["owner"]] = client_id
                    self._write(registry)
                response = await self.client.post(f"{ISSUER}/api/accounts/oauth/token", data={
                    "grant_type": "authorization_code", "client_id": client_id, "code": query["code"],
                    "code_verifier": attempt["verifier"], "redirect_uri": attempt["redirect_uri"], "resource": RESOURCE,
                })
                if response.is_error:
                    raise ValueError("ChatGPT code exchange failed. Start sign-in again.")
                data = response.json()
                identity = await self._identity(data.get("id_token", ""), client_id, nonce=attempt["nonce"],
                                                subject=selected.get("subject") if selected else None)
                tokens = self._token_record(data)
                account_id = "chatgpt-" + hashlib.sha256(f"{client_id}\0{identity['sub']}".encode()).hexdigest()[:24]
                registry = self._read()
                old = registry["accounts"].get(account_id, {})
                account = {"client_id": client_id, "subject": identity["sub"],
                           "email": str(identity.get("email") or ""),
                           "label": f"{identity.get('email') or 'ChatGPT account'} · {account_id[-6:]}",
                           "welcome_seen": old.get("welcome_seen", False)}
                await self._save_tokens(account_id, tokens)
                registry["accounts"][account_id] = account
                registry.get("pending_registrations", {}).pop(attempt["owner"], None)
                self._write(registry)
                attempt["account_id"] = account_id
                current = self.store.get_provider(account_id)
                self.store.put_provider(account_id, kind="chatgpt", name=f"ChatGPT · {account['label']}",
                                        base_url=RESOURCE, model=current["model"] if current else "", capabilities=["shell", "functions"],
                                        secret_configured=DIRECT_SCOPE in tokens["scopes"])
            attempt["committed"] = True
            catalog_error = False
            if DIRECT_SCOPE in tokens["scopes"]:
                try:
                    await self.models(account_id)
                except (httpx.HTTPError, ValueError, RuntimeError):
                    catalog_error = True
            attempt["status"] = "completed"
            attempt["message"] = ("Signed in. Eligible AI requests use your ChatGPT plan." if DIRECT_SCOPE in tokens["scopes"]
                                    else "Signed in, but ChatGPT plan usage was not authorized. Sign in again to enable it.")
            if catalog_error:
                attempt["message"] += " Models could not be loaded; use Refresh models in settings."

        except asyncio.CancelledError:
            if attempt.get("committed"):
                attempt.update(status="completed", message="ChatGPT sign-in completed. Refresh models in settings if needed.")
            else:
                attempt.update(status="cancelled", message="ChatGPT sign-in cancelled")
        except TimeoutError:
            attempt.update(status="expired", message="ChatGPT sign-in expired. Try again.")
        except Exception as exc:
            # Never publish token responses, callback URLs or network errors.
            message = str(exc) if isinstance(exc, ValueError) and not isinstance(exc, json.JSONDecodeError) else "ChatGPT sign-in failed. Try again."
            attempt.update(status="failed", message=message)
        finally:
            attempt["done"].set()
            attempt["server"].close()
            await attempt["server"].wait_closed()
            for key in ("state", "nonce", "verifier", "selected", "future", "authorization_url"):
                attempt.pop(key, None)

    async def access_token(self, account_id, *, force=False):
        # Finish a rotating-token transaction even when its caller is cancelled.
        if self._closing:
            raise ProviderError("ChatGPT connection is closing")
        worker = asyncio.create_task(self._access_token(account_id, force=force))
        self._refresh_tasks.add(worker)
        worker.add_done_callback(self._refresh_tasks.discard)
        try:
            return await asyncio.shield(worker)
        except asyncio.CancelledError:
            await asyncio.gather(worker, return_exceptions=True)
            raise

    async def _access_token(self, account_id, *, force=False):
        async with self._locked():
            registry = self._read()
            account = registry["accounts"].get(account_id)
            tokens = await self._tokens(account_id)
            if not account or not tokens.get("access_token"):
                raise ProviderError("Sign in with ChatGPT again to use this account")
            if DIRECT_SCOPE not in tokens.get("scopes", []):
                raise ProviderError("ChatGPT plan usage was not authorized. Sign in again to enable it.")
            if force or tokens["expires_at"] < time() + 60:
                if not tokens.get("refresh_token"):
                    raise ProviderError("ChatGPT session expired. Sign in again.")
                earliest = tokens.get("earliest_refresh_at", 0)
                if isinstance(earliest, (int, float)) and earliest > time():
                    if tokens["expires_at"] > time() and not force:
                        return tokens["access_token"]
                    raise ProviderError("ChatGPT access cannot be renewed yet. Try again shortly.",
                                        retry_after_s=earliest - time(), status_code=429)
                try:
                    response = await self.client.post(f"{ISSUER}/api/accounts/oauth/token", data={
                        "grant_type": "refresh_token", "client_id": account["client_id"],
                        "refresh_token": tokens["refresh_token"], "resource": RESOURCE,
                    })
                except httpx.RequestError as exc:
                    raise ProviderError("Could not renew ChatGPT access. Try again later.", network=True) from exc
                if response.is_error:
                    try:
                        code = response.json().get("error")
                        if isinstance(code, dict):
                            code = code.get("code")
                    except ValueError:
                        code = ""
                    if code in TERMINAL_REFRESH_ERRORS:
                        await asyncio.to_thread(self.credentials.delete, self._secret_key(account_id))
                        self.store.set_provider_secret_state(account_id, False)
                        raise ProviderError("ChatGPT session expired or was revoked. Sign in again.")
                    raise ProviderError("Could not renew ChatGPT access. Try again later.",
                                        status_code=response.status_code)
                data = response.json()
                if data.get("id_token"):
                    await self._identity(data["id_token"], account["client_id"], subject=account["subject"])
                updated = self._token_record(data, tokens)
                if not updated["refresh_token"]:
                    raise ProviderError("ChatGPT did not return a replacement refresh token. Sign in again.")
                await self._save_tokens(account_id, updated)
                tokens = updated
                self.store.set_provider_secret_state(account_id, DIRECT_SCOPE in tokens["scopes"])
                if DIRECT_SCOPE not in tokens["scopes"]:
                    raise ProviderError("ChatGPT plan usage is no longer authorized. Sign in again.")
            return tokens["access_token"]

    async def models(self, account_id):
        token = await self.access_token(account_id)
        response = await self.client.get(f"{RESOURCE}/models", headers={"Authorization": f"Bearer {token}"})
        if response.status_code == 401:
            token = await self.access_token(account_id, force=True)
            response = await self.client.get(f"{RESOURCE}/models", headers={"Authorization": f"Bearer {token}"})
        if response.is_error:
            raise ValueError("Could not load models for this ChatGPT account. Check access and try again.")
        catalog = response.json().get("models")
        if not isinstance(catalog, list):
            raise ValueError("ChatGPT returned an invalid model catalog")
        models = [{"slug": m["slug"], "display_name": m.get("display_name") or m["slug"]}
                  for m in catalog
                  if isinstance(m, dict) and m.get("visibility") == "list" and isinstance(m.get("slug"), str) and m["slug"]]
        async with self._locked():
            tokens = await self._tokens(account_id)
            if DIRECT_SCOPE not in tokens.get("scopes", []):
                raise ValueError("ChatGPT account was signed out. Sign in again.")
            provider = self.store.get_provider(account_id)
            if provider:
                self.store.put_provider(account_id, kind="chatgpt", name=provider["name"], base_url=RESOURCE,
                                        model=",".join(m["slug"] for m in models),
                                        capabilities=["shell", "functions"], secret_configured=True)
        return models

    async def acknowledge(self, account_id):
        async with self._locked():
            registry = self._read()
            if account_id not in registry["accounts"]:
                raise ValueError("ChatGPT account not found")
            registry["accounts"][account_id]["welcome_seen"] = True
            self._write(registry)

    async def sign_out(self, account_id):
        async with self._locked():
            registry = self._read()
            account = registry["accounts"].get(account_id)
            if not account:
                raise ValueError("ChatGPT account not found")
            tokens = await self._tokens(account_id)
            confirmed = not tokens.get("refresh_token")
            if tokens.get("refresh_token"):
                try:
                    metadata = await self._metadata()
                    for delay in (0, 0.25, 1):
                        await asyncio.sleep(delay)
                        response = await self.client.post(metadata["revocation_endpoint"], data={
                            "token": tokens["refresh_token"], "token_type_hint": "refresh_token",
                            "client_id": account["client_id"],
                        })
                        if response.status_code == 200:
                            confirmed = True
                            break
                        if response.status_code < 500:
                            break
                except (httpx.HTTPError, ValueError):
                    confirmed = False
            await asyncio.to_thread(self.credentials.delete, self._secret_key(account_id))
            self.store.set_provider_secret_state(account_id, False)
            return {"signed_out": True, "revocation_confirmed": confirmed,
                    "message": "Signed out." if confirmed else "Signed out locally. Remote revocation was not confirmed; disconnect TermX in ChatGPT settings.",
                    "usage_url": USAGE_URL}

    async def close(self):
        self._closing = True
        tasks = [i["task"] for i in self._attempts.values() if i["status"] == "pending"]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await asyncio.gather(*list(self._refresh_tasks), return_exceptions=True)
        if self._own_client:
            await self.client.aclose()
