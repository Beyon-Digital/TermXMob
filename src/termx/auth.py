from __future__ import annotations

import hmac
from time import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from termx.identity import AuthenticationService

from termx.tokens import SCOPES, TokenStore


class Auth:
    def __init__(self, passcode: str | None = None, token_store: TokenStore | None = None,
                 identity: AuthenticationService | None = None) -> None:
        self.passcode = passcode or None
        self.token_store = token_store
        self.identity = identity

    @property
    def required(self) -> bool:
        return self.passcode is not None or bool(self.identity and self.identity.configured)

    def check(self, provided: str | None) -> bool:
        return self.scopes(provided) is not None

    def scopes(self, provided: str | None) -> list[str] | None:
        if self.identity and self.identity.configured:
            session = self.identity.resolve(provided)
            if session is not None:
                return list(session.principal.scopes)
            # Existing opaque devices have a bounded migration window. The
            # owner password/passcode ceases to be a general API credential.
            if time() < self.identity.migration_deadline and self.token_store:
                return self.token_store.check(provided)
            return None
        # A host without a passcode retains the existing trusted-local/open
        # behavior. A passcode is the administrator credential and therefore
        # receives every scope; paired tokens remain least-privilege capable.
        if self.passcode is None:
            return list(SCOPES)
        if hmac.compare_digest(provided or "", self.passcode):
            return list(SCOPES)
        if self.token_store is not None:
            return self.token_store.check(provided)
        return None

    def allows(self, provided: str | None, scope: str) -> bool:
        scopes = self.scopes(provided)
        return scopes is not None and scope in scopes

    def check_secret(self, provided: str | None) -> bool:
        if self.identity and self.identity.configured:
            return self.check(provided)
        if self.passcode is not None and hmac.compare_digest(provided or "", self.passcode):
            return True
        if self.token_store is not None and self.token_store.check(provided) is not None:
            return True
        return False


def extract_passcode(
    header_passcode: str | None = None,
    authorization: str | None = None,
    query: str | None = None,
) -> str | None:
    if header_passcode:
        return header_passcode
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    if query:
        return query
    return None
