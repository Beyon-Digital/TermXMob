from __future__ import annotations

import hmac

from termx.tokens import TokenStore


class Auth:
    def __init__(self, passcode: str | None = None, token_store: TokenStore | None = None) -> None:
        self.passcode = passcode or None
        self.token_store = token_store

    @property
    def required(self) -> bool:
        return self.passcode is not None

    def check(self, provided: str | None) -> bool:
        if self.passcode is None:
            return True
        if hmac.compare_digest(provided or "", self.passcode):
            return True
        if self.token_store is not None and self.token_store.check(provided) is not None:
            return True
        return False

    def check_secret(self, provided: str | None) -> bool:
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
