from __future__ import annotations

import asyncio
import hmac
import json
import os
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response

from pydantic import BaseModel, Field

from termx import notify
from termx.audit import log_event, read_events
from termx.auth import Auth, extract_passcode
from termx.config import ConfigStore, list_dir_entries, validate_cwd, validate_shell
from termx.desktop.capture import virtual_display_reason
from termx.desktop.permissions import permission_snapshot, request_permissions
from termx.desktop.session import DesktopManager
from termx.desktop.virtual import VirtualDisplayError, create_virtual_display, destroy_virtual_display
from termx.desktop.webrtc import RtcError, RtcManager
from termx.lifecycle import shutdown_state
from termx.machine import machine_snapshot
from termx.net import connect_url, http_urls, qr_svg
from termx.sessions import DEFAULT_COLS, DEFAULT_ROWS, SessionManager, default_argv
from termx.tokens import SCOPES, TokenStore
from termx.tunnels import TunnelManager

from termx import __version__

PACKAGE_STATIC = Path(__file__).parent / "static"


class AppState:
    def __init__(self, passcode: str | None = None, port: int = 8787) -> None:
        self.tokens = TokenStore()
        self.auth = Auth(passcode, token_store=self.tokens)
        self.store = ConfigStore()
        self.sessions = SessionManager()
        self.tunnels = TunnelManager(self.store, port=port)
        self.desktop = DesktopManager()
        self.rtc = RtcManager()
        self.port = port
        self.request_shutdown = None


class CreateSessionBody(BaseModel):
    cols: int = Field(default=DEFAULT_COLS, ge=1, le=500)
    rows: int = Field(default=DEFAULT_ROWS, ge=1, le=200)
    title: str | None = None
    shell: str | None = None
    cwd: str | None = None


class RenameSessionBody(BaseModel):
    title: str = Field(min_length=1, max_length=80)


class PreferencesBody(BaseModel):
    shell: str | None = None
    cwd: str | None = None


class CommandBody(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    command: str = Field(min_length=1, max_length=4000)
    confirm: bool = False


class DirectoryBody(BaseModel):
    name: str = Field(default="", max_length=80)
    path: str = Field(min_length=1, max_length=4000)


class CommandPatchBody(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    command: str | None = Field(default=None, min_length=1, max_length=4000)
    confirm: bool | None = None


class ReorderBody(BaseModel):
    order: list[str]


class TunnelProfileBody(BaseModel):
    provider: str
    name: str = Field(min_length=1, max_length=80)
    kind: str = "quick"
    extra: dict[str, object] | None = None


class TunnelStartBody(BaseModel):
    profile_id: str | None = None
    provider: str | None = None
    kind: str | None = None


class VirtualDisplayBody(BaseModel):
    width: int = Field(default=1170, ge=320, le=7680)
    height: int = Field(default=2532, ge=320, le=4320)
    dpr: float = Field(default=2.0, ge=1.0, le=4.0)
    refresh_hz: int = Field(default=60, ge=15, le=120)


class RtcOfferBody(BaseModel):
    offer: dict[str, object]
    session_id: str | None = None


class RtcIceBody(BaseModel):
    session_id: str
    candidate: dict[str, object]


class NotifyBody(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    body: str = Field(default="", max_length=1000)
    url: str | None = Field(default=None, max_length=2000)


class PermissionsBody(BaseModel):
    which: list[str] | None = None


def _require(state: AppState, provided: str | None) -> None:
    if not state.auth.check(provided):
        raise HTTPException(status_code=401, detail="invalid passcode")


def create_app(state: AppState | None = None, web_dir: Path | None = None) -> FastAPI:
    state = state or AppState()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        await shutdown_state(state)

    app = FastAPI(title="termx", docs_url=None, redoc_url=None, lifespan=lifespan)
    app.state.termx = state
    origins = [item.strip() for item in os.environ.get("TERMX_CORS_ORIGINS", "*").split(",") if item.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins or ["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    def provided(
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> str | None:
        return extract_passcode(x_termx_passcode, authorization, k)

    @app.get("/api/health")
    def health() -> dict[str, object]:
        snapshot = machine_snapshot(state.store, state.tunnels.status_public())
        capabilities = dict(snapshot["capabilities"])
        capabilities["webrtc"] = state.rtc.available()
        return {
            "app": "termx",
            "ok": True,
            "version": __version__,
            "passcode_required": state.auth.required,
            "hostname": snapshot["hostname"],
            "os": snapshot["os"],
            "capabilities": capabilities,
            "tunnel": snapshot["tunnel"],
        }

    def _connect_target() -> tuple[str, str | None]:
        tunnel = state.tunnels.status_public()
        urls = http_urls(state.port)
        # Phones cannot reach loopback, so the QR/pairing link must prefer the LAN
        # address when one exists.
        remote = next(
            (
                url
                for url in urls
                if not url.startswith("http://127.") and not url.startswith("http://localhost")
            ),
            None,
        )
        target = tunnel.get("url") or remote or (urls[0] if urls else f"http://127.0.0.1:{state.port}")
        return str(target), tunnel.get("url")

    @app.get("/api/connect")
    def connect_info(
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, object]:
        _require(state, provided(x_termx_passcode, authorization, k))
        target, tunnel_url = _connect_target()
        return {
            "urls": http_urls(state.port),
            "tunnel_url": tunnel_url,
            "passcode": state.auth.passcode,
            "connect_url": connect_url(target, state.auth.passcode),
            "qr_svg": "/api/connect/qr.svg",
        }

    @app.get("/api/connect/qr.svg")
    def connect_qr(
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> Response:
        _require(state, provided(x_termx_passcode, authorization, k))
        target, _tunnel = _connect_target()
        svg = qr_svg(connect_url(target, state.auth.passcode))
        return Response(content=svg, media_type="image/svg+xml", headers={"Cache-Control": "no-store"})

    @app.post("/api/notify")
    def send_notification(
        body: NotifyBody,
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, bool]:
        _require(state, provided(x_termx_passcode, authorization, k))
        notify.notify(body.title, body.body, kind="info", url=body.url)
        return {"ok": True}

    @app.get("/api/permissions")
    def get_permissions(
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, object]:
        _require(state, provided(x_termx_passcode, authorization, k))
        return permission_snapshot()

    @app.post("/api/permissions/request")
    def post_permissions(
        body: PermissionsBody | None = None,
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, object]:
        _require(state, provided(x_termx_passcode, authorization, k))
        which = body.which if body is not None else None
        return request_permissions(which)

    @app.post("/api/shutdown")
    def shutdown(
        request: Request,
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, bool]:
        _require(state, provided(x_termx_passcode, authorization, k))
        if os.environ.get("TERMX_DESKTOP") != "1":
            raise HTTPException(status_code=404, detail="not found")
        client = request.client.host if request.client is not None else ""
        if client not in {"127.0.0.1", "::1", "localhost"}:
            raise HTTPException(status_code=403, detail="loopback only")
        callback = state.request_shutdown
        if not callable(callback):
            raise HTTPException(status_code=503, detail="shutdown unavailable")
        log_event("shutdown", source="desktop")
        callback()
        return {"ok": True}

    @app.post("/api/pair")
    def pair(
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, object]:
        secret = provided(x_termx_passcode, authorization, k)
        if state.auth.passcode is not None and not hmac.compare_digest(secret or "", state.auth.passcode):
            raise HTTPException(status_code=401, detail="invalid passcode")
        token = state.tokens.issue()
        log_event("pair", scopes=list(SCOPES))
        return {"token": token, "scopes": list(SCOPES)}

    @app.get("/api/audit")
    def get_audit(
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, object]:
        _require(state, provided(x_termx_passcode, authorization, k))
        return {"events": read_events(100)}

    @app.get("/api/machine")
    def machine(
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, object]:
        _require(state, provided(x_termx_passcode, authorization, k))
        return machine_snapshot(state.store, state.tunnels.status_public())

    @app.get("/api/preferences")
    def get_preferences(
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, object]:
        _require(state, provided(x_termx_passcode, authorization, k))
        prefs = state.store.get().terminal
        return {"shell": prefs.shell, "cwd": prefs.cwd, "shells": machine_snapshot(state.store)["shells"]}

    @app.put("/api/preferences")
    def put_preferences(
        body: PreferencesBody,
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, object]:
        _require(state, provided(x_termx_passcode, authorization, k))
        try:
            prefs = state.store.update_terminal(shell=body.shell, cwd=body.cwd)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"shell": prefs.shell, "cwd": prefs.cwd}

    @app.get("/api/commands")
    def list_commands(
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, object]:
        _require(state, provided(x_termx_passcode, authorization, k))
        return {"commands": [item.public() for item in state.store.list_commands()]}

    @app.post("/api/commands")
    def create_command(
        body: CommandBody,
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, object]:
        _require(state, provided(x_termx_passcode, authorization, k))
        try:
            item = state.store.add_command(body.name, body.command, body.confirm)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        log_event("command_create", command_id=item.id, name=item.name)
        return item.public()

    @app.patch("/api/commands/{command_id}")
    def patch_command(
        command_id: str,
        body: CommandPatchBody,
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, object]:
        _require(state, provided(x_termx_passcode, authorization, k))
        try:
            item = state.store.patch_command(command_id, body.name, body.command, body.confirm)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if item is None:
            raise HTTPException(status_code=404, detail="command not found")
        return item.public()

    @app.delete("/api/commands/{command_id}")
    def delete_command(
        command_id: str,
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, bool]:
        _require(state, provided(x_termx_passcode, authorization, k))
        if not state.store.delete_command(command_id):
            raise HTTPException(status_code=404, detail="command not found")
        log_event("command_delete", command_id=command_id)
        return {"ok": True}

    @app.post("/api/commands/reorder")
    def reorder_commands(
        body: ReorderBody,
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, object]:
        _require(state, provided(x_termx_passcode, authorization, k))
        items = state.store.reorder_commands(body.order)
        return {"commands": [item.public() for item in items]}

    @app.get("/api/fs")
    def get_fs(
        path: str | None = Query(default=None),
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, object]:
        _require(state, provided(x_termx_passcode, authorization, k))
        try:
            return list_dir_entries(path)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/directories")
    def list_directories(
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, object]:
        _require(state, provided(x_termx_passcode, authorization, k))
        prefs = state.store.get().terminal
        return {
            "cwd": prefs.cwd,
            "directories": [item.public() for item in state.store.list_directories()],
        }

    @app.post("/api/directories")
    def create_directory(
        body: DirectoryBody,
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, object]:
        _require(state, provided(x_termx_passcode, authorization, k))
        try:
            item = state.store.add_directory(body.name, body.path)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        log_event("directory_create", directory_id=item.id, name=item.name)
        return item.public()

    @app.delete("/api/directories/{directory_id}")
    def delete_directory(
        directory_id: str,
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, bool]:
        _require(state, provided(x_termx_passcode, authorization, k))
        if not state.store.delete_directory(directory_id):
            raise HTTPException(status_code=404, detail="directory not found")
        log_event("directory_delete", directory_id=directory_id)
        return {"ok": True}

    @app.post("/api/directories/{directory_id}/use")
    def use_directory(
        directory_id: str,
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, object]:
        _require(state, provided(x_termx_passcode, authorization, k))
        try:
            item = state.store.use_directory(directory_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="directory not found") from None
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        log_event("directory_use", directory_id=item.id, path=item.path)
        return {"directory": item.public(), "cwd": item.path}

    @app.get("/api/tunnels")
    def get_tunnels(
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, object]:
        _require(state, provided(x_termx_passcode, authorization, k))
        return state.tunnels.runtime()

    @app.post("/api/tunnels/profiles")
    def create_tunnel_profile(
        body: TunnelProfileBody,
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, object]:
        _require(state, provided(x_termx_passcode, authorization, k))
        if body.provider not in {"cloudflare", "ngrok", "tailscale"}:
            raise HTTPException(status_code=400, detail="unknown provider")
        profile = state.store.add_profile(body.provider, body.name, body.kind, body.extra)
        return profile.public()

    @app.delete("/api/tunnels/profiles/{profile_id}")
    def delete_tunnel_profile(
        profile_id: str,
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, bool]:
        _require(state, provided(x_termx_passcode, authorization, k))
        if not state.store.delete_profile(profile_id):
            raise HTTPException(status_code=404, detail="profile not found")
        return {"ok": True}

    @app.post("/api/tunnels/start")
    async def start_tunnel(
        body: TunnelStartBody | None = None,
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, object]:
        _require(state, provided(x_termx_passcode, authorization, k))
        body = body or TunnelStartBody()
        profile = None
        if body.profile_id:
            profile = state.store.get_profile(body.profile_id)
            if profile is None:
                raise HTTPException(status_code=404, detail="profile not found")
        elif body.provider:
            profile = state.store.add_profile(
                body.provider,
                body.provider,
                body.kind or ("quick" if body.provider == "cloudflare" else "http" if body.provider == "ngrok" else "funnel"),
            )
        if profile is None:
            status = await state.tunnels.start_quick_cloudflare()
        else:
            status = await state.tunnels.start(profile)
        if status.state == "error":
            notify.notify("Tunnel failed", status.detail or "tunnel failed", kind="error")
            raise HTTPException(status_code=400, detail=status.detail or "tunnel failed")
        log_event("tunnel_start", provider=status.provider, state=status.state)
        if status.state == "connected" and status.url:
            notify.notify("Tunnel connected", status.url, kind="success", url=status.url)
        return status.public()

    @app.post("/api/tunnels/stop")
    async def stop_tunnel(
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, object]:
        _require(state, provided(x_termx_passcode, authorization, k))
        status = await state.tunnels.stop()
        log_event("tunnel_stop", state=status.state)
        return status.public()

    @app.post("/api/tunnels/restart")
    async def restart_tunnel(
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, object]:
        _require(state, provided(x_termx_passcode, authorization, k))
        status = await state.tunnels.restart()
        if status.state == "error":
            notify.notify("Tunnel failed", status.detail or "tunnel failed", kind="error")
            raise HTTPException(status_code=400, detail=status.detail or "tunnel failed")
        log_event("tunnel_restart", provider=status.provider, state=status.state)
        if status.state == "connected" and status.url:
            notify.notify("Tunnel connected", status.url, kind="success", url=status.url)
        return status.public()

    @app.get("/api/displays")
    def get_displays(
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, object]:
        _require(state, provided(x_termx_passcode, authorization, k))
        return {
            **state.desktop.snapshot(),
            "virtual_display_reason": virtual_display_reason(),
        }

    @app.post("/api/displays/virtual")
    def create_display(
        body: VirtualDisplayBody,
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, object]:
        _require(state, provided(x_termx_passcode, authorization, k))
        try:
            created = create_virtual_display(body.width, body.height, body.dpr, body.refresh_hz)
        except VirtualDisplayError as exc:
            raise HTTPException(status_code=501, detail=str(exc)) from exc
        log_event("display_create", display_id=created.get("id"), adapter=created.get("adapter"))
        return created

    @app.delete("/api/displays/{display_id}")
    def delete_display(
        display_id: str,
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, bool]:
        _require(state, provided(x_termx_passcode, authorization, k))
        try:
            destroy_virtual_display(display_id)
        except VirtualDisplayError as exc:
            raise HTTPException(status_code=501, detail=str(exc)) from exc
        log_event("display_destroy", display_id=display_id)
        return {"ok": True}

    @app.post("/api/desktop/rtc/offer")
    def rtc_offer(
        body: RtcOfferBody,
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, object]:
        _require(state, provided(x_termx_passcode, authorization, k))
        session_id = body.session_id or uuid.uuid4().hex[:12]
        try:
            return state.rtc.handle_offer(session_id, body.offer)
        except RtcError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @app.post("/api/desktop/rtc/ice")
    def rtc_ice(
        body: RtcIceBody,
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, bool]:
        _require(state, provided(x_termx_passcode, authorization, k))
        state.rtc.add_ice(body.session_id, body.candidate)
        return {"ok": True}

    @app.get("/api/sessions")
    def list_sessions(
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, object]:
        _require(state, provided(x_termx_passcode, authorization, k))
        return {"sessions": [s.snapshot() for s in state.sessions.list()]}

    @app.post("/api/sessions")
    async def create_session(
        body: CreateSessionBody | None = None,
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, object]:
        _require(state, provided(x_termx_passcode, authorization, k))
        body = body or CreateSessionBody()
        prefs = state.store.get().terminal
        try:
            shell = validate_shell(body.shell or prefs.shell)
            cwd = validate_cwd(body.cwd or prefs.cwd)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        session = state.sessions.create(
            cols=body.cols,
            rows=body.rows,
            title=body.title,
            argv=default_argv(shell),
            cwd=cwd,
            shell=shell,
        )
        return session.snapshot()

    @app.patch("/api/sessions/{session_id}")
    def rename_session(
        session_id: str,
        body: RenameSessionBody,
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, object]:
        _require(state, provided(x_termx_passcode, authorization, k))
        session = state.sessions.rename(session_id, body.title)
        if session is None:
            raise HTTPException(status_code=404, detail="session not found")
        return session.snapshot()

    @app.delete("/api/sessions/{session_id}")
    def delete_session(
        session_id: str,
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, bool]:
        _require(state, provided(x_termx_passcode, authorization, k))
        if not state.sessions.kill(session_id):
            raise HTTPException(status_code=404, detail="session not found")
        return {"ok": True}

    @app.websocket("/api/sessions/{session_id}/pty")
    async def pty_socket(
        websocket: WebSocket,
        session_id: str,
        k: str | None = None,
    ) -> None:
        header_k = websocket.headers.get("x-termx-passcode")
        authorization = websocket.headers.get("authorization")
        token = extract_passcode(header_k, authorization, k)
        if not state.auth.check(token):
            await websocket.close(code=4401)
            return
        session = state.sessions.get(session_id)
        if session is None or session.exited:
            await websocket.close(code=4404)
            return
        await websocket.accept()
        session.attach(asyncio.get_running_loop())
        replay = session.subscribe(websocket)
        if replay:
            await websocket.send_bytes(replay)
        try:
            while True:
                message = await websocket.receive()
                if message["type"] == "websocket.disconnect":
                    break
                raw_bytes = message.get("bytes")
                raw_text = message.get("text")
                if raw_bytes is not None:
                    session.write(raw_bytes)
                elif raw_text is not None:
                    try:
                        payload = json.loads(raw_text)
                    except json.JSONDecodeError:
                        session.write(raw_text.encode("utf-8"))
                        continue
                    kind = payload.get("type")
                    if kind in {"ping", "pong"}:
                        continue
                    if kind == "resize":
                        session.resize(int(payload.get("cols", session.cols)), int(payload.get("rows", session.rows)))
                    elif kind == "signal":
                        name = str(payload.get("name") or "int")
                        session.send_signal(name)
                    elif kind == "input":
                        data = payload.get("data", "")
                        if isinstance(data, str):
                            session.write(data.encode("utf-8", "surrogateescape"))
        except WebSocketDisconnect:
            pass
        finally:
            session.unsubscribe(websocket)

    @app.websocket("/api/desktop/session")
    async def desktop_socket(websocket: WebSocket, k: str | None = None) -> None:
        header_k = websocket.headers.get("x-termx-passcode")
        authorization = websocket.headers.get("authorization")
        token = extract_passcode(header_k, authorization, k)
        if not state.auth.check(token):
            await websocket.close(code=4401)
            return
        await state.desktop.attach(websocket)

    vendor = PACKAGE_STATIC / "vendor"
    allowed_vendor = {"xterm.js", "xterm.css", "addon-fit.js"}

    @app.get("/_/vendor/{name}")
    def vendor_file(name: str) -> FileResponse:
        if name not in allowed_vendor:
            raise HTTPException(status_code=404)
        path = vendor / name
        if not path.is_file():
            raise HTTPException(status_code=404)
        return FileResponse(path)

    html_headers = {"Cache-Control": "no-store"}

    @app.get("/_/embed.html")
    def embed() -> FileResponse:
        return FileResponse(PACKAGE_STATIC / "embed.html", media_type="text/html", headers=html_headers)

    @app.get("/_/app.html")
    def builtin_app() -> FileResponse:
        return FileResponse(PACKAGE_STATIC / "index.html", media_type="text/html", headers=html_headers)

    @app.get("/_/connect.html")
    def connect_page() -> FileResponse:
        return FileResponse(PACKAGE_STATIC / "connect.html", media_type="text/html", headers=html_headers)

    dist = web_dir if web_dir and web_dir.is_dir() else None
    index_html = (dist / "index.html") if dist else None
    fallback = PACKAGE_STATIC / "index.html"

    @app.get("/")
    def root() -> FileResponse:
        if index_html is not None and index_html.is_file():
            return FileResponse(index_html, headers=html_headers)
        return FileResponse(fallback, headers=html_headers)

    if dist is not None:

        @app.get("/{path:path}")
        def spa(path: str) -> FileResponse:
            if path.startswith("api/") or path.startswith("_/"):
                raise HTTPException(status_code=404)
            target = (dist / path).resolve()
            try:
                target.relative_to(dist.resolve())
            except ValueError:
                raise HTTPException(status_code=404) from None
            if target.is_file():
                return FileResponse(target)
            if index_html is not None and index_html.is_file():
                return FileResponse(index_html)
            return FileResponse(fallback)
    else:

        @app.get("/{path:path}")
        def spa_fallback(path: str) -> FileResponse:
            if path.startswith("api/") or path.startswith("_/"):
                raise HTTPException(status_code=404)
            return FileResponse(fallback)

    return app
