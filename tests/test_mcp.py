"""MCP defs / pool / OAuth plumbing / gateway tests."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from termx.agent.secrets import CredentialStore
from termx.mcp.client import McpConnectionError, McpPool
from termx.mcp.defs import ConnectionError_, load_connection_file, validate_connection
from termx.mcp.gateway import GatewayAuthError, McpGateway
from termx.mcp.oauth import CredentialTokenStorage
from termx.mcp.registry import ConnectionRegistry
from termx.mcp.ssrf import SSRFError, validate_url

FIXTURE = str(Path(__file__).parent / "fixtures" / "fake_mcp_server.py")


def _stdio_def(**over):
    data = {
        "schema": 1,
        "id": "connection.fake",
        "label": "Fake",
        "transport": "stdio",
        "command": [sys.executable, FIXTURE],
        "trust": "trusted",
        "enabled": True,
    }
    data.update(over)
    return validate_connection(data)


def test_validate_rejects_bad_defs():
    with pytest.raises(ConnectionError_):
        validate_connection({"transport": "stdio"})  # no command
    with pytest.raises(ConnectionError_):
        validate_connection({"transport": "http"})  # no url
    with pytest.raises(ConnectionError_):
        validate_connection({"transport": "carrier-pigeon", "url": "https://x"})
    with pytest.raises(ConnectionError_):
        validate_connection({"transport": "stdio", "command": ["x"],
                             "secret_refs": ["outside.namespace"]})


def test_def_file_roundtrip(tmp_path):
    reg = ConnectionRegistry([str(tmp_path)])
    conn = _stdio_def()
    path = reg.save(conn)
    loaded = reg.get("connection.fake")
    assert loaded and loaded.path == path
    assert loaded.command == conn.command
    assert loaded.trusted


def test_untrusted_blocks_spawn():
    pool = McpPool(CredentialStore(memory={}))
    conn = _stdio_def(trust="untrusted")
    with pytest.raises(McpConnectionError, match="untrusted"):
        asyncio.run(pool.connect(conn))


def test_stdio_connect_catalog_call():
    pool = McpPool(CredentialStore(memory={}))
    conn = _stdio_def()

    async def go():
        catalog = await pool.connect(conn)
        names = {t["name"] for t in catalog["tools"]}
        assert {"echo", "add"} <= names
        assert pool.status()["connection.fake"]["connected"]
        out = await pool.call_tool("connection.fake", "add", {"a": 2, "b": 3})
        assert out["structured_content"]["result"] == 5
        out2 = await pool.call_namespaced("fake.echo", {"text": "hi"})
        assert "echo:hi" in str(out2)
        await pool.shutdown()
        assert pool.status() == {}

    asyncio.run(go())


def test_ssrf_policy():
    validate_url("https://example.com/mcp")
    with pytest.raises(SSRFError):
        validate_url("http://169.254.169.254/latest/meta-data")  # link-local
    with pytest.raises(SSRFError):
        validate_url("ftp://example.com")
    with pytest.raises(SSRFError):
        validate_url("http://10.0.0.4/mcp")
    # lan opt-in permits private + loopback
    validate_url("http://10.0.0.4/mcp", lan=True)
    validate_url("http://127.0.0.1:9999/mcp", lan=True)
    with pytest.raises(SSRFError):
        validate_url("http://metadata.google.internal/", lan=True)


def test_token_storage_roundtrip():
    from mcp.shared.auth import OAuthToken
    store = CredentialStore(memory={})
    ts = CredentialTokenStorage(store, "fake", issuer_hint="https://as.example")
    tok = OAuthToken(access_token="a1", token_type="Bearer", expires_in=60)
    asyncio.run(ts.set_tokens(tok))
    got = asyncio.run(ts.get_tokens())
    assert got and got.access_token == "a1"
    asyncio.run(ts.delete_all())
    assert asyncio.run(ts.get_tokens()) is None


def test_gateway_scoping_and_revocation():
    pool = McpPool(CredentialStore(memory={}))
    conn = _stdio_def()

    async def go():
        await pool.connect(conn)
        gw = McpGateway(pool)
        token = gw.mint("sess-1", {"connection.fake": ["add"]})
        # in-scope call works
        out = await gw.call_tool(token, "connection.fake", "add", {"a": 1, "b": 1})
        assert out
        # out-of-scope tool denied
        with pytest.raises(GatewayAuthError):
            await gw.call_tool(token, "connection.fake", "echo", {"text": "x"})
        # out-of-scope connection denied
        with pytest.raises(GatewayAuthError):
            await gw.call_tool(token, "connection.other", "add", {})
        # bad token denied
        with pytest.raises(GatewayAuthError):
            await gw.catalog("mcpgw_bogus")
        # revocation kills the token
        assert gw.revoke_session("sess-1") == 1
        with pytest.raises(GatewayAuthError):
            await gw.catalog(token)
        await pool.shutdown()

    asyncio.run(go())
