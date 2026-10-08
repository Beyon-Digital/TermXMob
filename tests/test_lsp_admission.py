"""Actual actionable websocket admission without broadening authorization."""
import asyncio
from unittest.mock import AsyncMock
import pytest
from fastapi import HTTPException
from termx import lsp


def test_missing_runtime_preserves_actionable_close_after_live_admission(monkeypatch):
    monkeypatch.setattr(lsp,'_command',lambda _:None)
    socket=AsyncMock();steps=[]
    def authorize():steps.append('authorized')
    async def accept():steps.append('accepted')
    socket.accept.side_effect=accept
    asyncio.run(lsp.serve(socket,'/tmp','python',authorize=authorize))
    assert steps==['authorized','accepted']
    socket.close.assert_awaited_once_with(code=4404,reason='Language server unavailable. Install the configured language runtime.')


def test_missing_runtime_never_accepts_unauthorized_socket(monkeypatch):
    monkeypatch.setattr(lsp,'_command',lambda _:None)
    socket=AsyncMock()
    def unauthorized():raise HTTPException(403,'Scope revoked')
    with pytest.raises(HTTPException):asyncio.run(lsp.serve(socket,'/tmp','python',authorize=unauthorized))
    socket.accept.assert_not_awaited()


def test_missing_runtime_delivers_real_websocket_close_code(monkeypatch):
    from fastapi import FastAPI,WebSocket
    from fastapi.testclient import TestClient
    monkeypatch.setattr(lsp,'_command',lambda _:None)
    app=FastAPI()
    @app.websocket('/language')
    async def language(socket:WebSocket):
        await lsp.serve(socket,'/tmp','python',authorize=lambda:None)
    with TestClient(app) as client:
        with client.websocket_connect('/language') as socket:
            event=socket.receive()
            assert event['type']=='websocket.close' and event['code']==4404
            assert 'Install the configured language runtime' in event['reason']
