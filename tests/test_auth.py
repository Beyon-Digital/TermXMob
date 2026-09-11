from termx.auth import Auth, extract_passcode
from termx.tokens import TokenStore


def test_open_when_no_passcode() -> None:
    auth = Auth(None)
    assert auth.required is False
    assert auth.check(None)
    assert auth.check("anything")


def test_rejects_wrong_passcode() -> None:
    auth = Auth("secret")
    assert auth.required is True
    assert auth.check("secret")
    assert not auth.check("Secret")
    assert not auth.check(None)
    assert not auth.check("")


def test_extract_prefers_header() -> None:
    assert extract_passcode("h", "Bearer b", "q") == "h"
    assert extract_passcode(None, "Bearer tok", "q") == "tok"
    assert extract_passcode(None, None, "q") == "q"
    assert extract_passcode(None, None, None) is None


def test_token_accepted_when_store_attached() -> None:
    store = TokenStore()
    raw = store.issue(["terminal"])
    auth = Auth("secret", token_store=store)
    assert auth.check("secret")
    assert auth.check(raw)
    assert not auth.check("wrong")
    assert not auth.check(None)
    assert auth.check_secret("secret")
    assert auth.check_secret(raw)
    assert not auth.check_secret("wrong")
    assert Auth("secret").check("secret")
    assert not Auth("secret").check(raw)
    assert not Auth("secret").check_secret(raw)
