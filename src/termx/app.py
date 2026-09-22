from __future__ import annotations

import asyncio
import hmac
import json
import os
import re
import tempfile
import threading
import uuid
from urllib.parse import unquote
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response

from pydantic import BaseModel, Field

from termx import notify
from termx.agent.manager import AgentManager, AdapterFactory
from termx.agent.secrets import CredentialStore
from termx.agent.store import AgentStore
from termx.audit import log_event, read_events
from termx.auth import Auth, extract_passcode
from termx.config import (
    ConfigStore,
    WorkspaceSession,
    list_dir_entries,
    validate_cwd,
    validate_shell,
)
from termx.desktop.capture import virtual_display_reason
from termx.desktop.permissions import permission_snapshot, request_permissions
from termx.desktop.session import DesktopManager
from termx.desktop.virtual import VirtualDisplayError, create_virtual_display, destroy_virtual_display
from termx.desktop.webrtc import RtcError, RtcManager
from termx.forwards import ForwardManager
from termx.lifecycle import shutdown_state
from termx.machine import machine_snapshot
from termx.net import connect_url, http_urls, qr_svg
from termx.project_files import ProjectFiles
from termx import git_ops, lsp
from termx.sessions import DEFAULT_COLS, DEFAULT_ROWS, SessionManager, default_argv
from termx.tokens import SCOPES, TokenStore
from termx.tunnels import TunnelManager

from termx import __version__

PACKAGE_STATIC = Path(__file__).parent / "static"


class AppState:
    def __init__(
        self,
        passcode: str | None = None,
        port: int = 8787,
        *,
        agent_store: AgentStore | None = None,
        credentials: CredentialStore | None = None,
        adapter_factory: AdapterFactory | None = None,
    ) -> None:
        self.tokens = TokenStore()
        self.auth = Auth(passcode, token_store=self.tokens)
        self.store = ConfigStore()
        self.sessions = SessionManager()
        # A saved workspace is only a cold-start recovery plan. It is never a
        # per-client session factory, so concurrent reconnects must serialize
        # the check-and-restore sequence.
        self.workspace_restore_lock = threading.Lock()
        self.tunnels = TunnelManager(self.store, port=port)
        self.forwards = ForwardManager(self.store)
        self.rtc = RtcManager()
        self.desktop = DesktopManager(self.store, rtc=self.rtc)
        self.agent_store = agent_store or AgentStore()
        self.credentials = credentials or CredentialStore()
        self.agent = AgentManager(
            self.agent_store,
            self.credentials,
            self.desktop,
            adapter_factory=adapter_factory,
        )
        self.projects = ProjectFiles()
        self.port = port
        self.request_shutdown = None


class CreateSessionBody(BaseModel):
    cols: int = Field(default=DEFAULT_COLS, ge=1, le=500)
    rows: int = Field(default=DEFAULT_ROWS, ge=1, le=200)
    title: str | None = None
    shell: str | None = None
    cwd: str | None = None


class WorkspaceSessionBody(BaseModel):
    title: str = Field(default="", max_length=80)
    shell: str = Field(default="", max_length=400)
    cwd: str = Field(default="", max_length=2000)


class WorkspaceBody(BaseModel):
    sessions: list[WorkspaceSessionBody] = Field(default_factory=list)


class ForwardBody(BaseModel):
    name: str = Field(default="", max_length=80)
    kind: str = Field(default="local")
    listen_host: str = Field(default="127.0.0.1", max_length=120)
    listen_port: int = Field(ge=1, le=65535)
    target_host: str = Field(default="127.0.0.1", max_length=120)
    target_port: int = Field(default=0, ge=0, le=65535)
    ssh_host: str = Field(min_length=1, max_length=200)
    auto_start: bool = False


class ForwardPatchBody(BaseModel):
    name: str | None = Field(default=None, max_length=80)
    auto_start: bool | None = None


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


class AgentProviderBody(BaseModel):
    id: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9._-]+$")
    kind: str = Field(default="openai", max_length=40)
    name: str = Field(min_length=1, max_length=80)
    base_url: str = Field(default="https://api.openai.com/v1", min_length=1, max_length=2000)
    model: str = Field(min_length=1, max_length=200)
    capabilities: list[str] = Field(default_factory=lambda: ["shell", "computer"])
    api_key: str | None = Field(default=None, max_length=4000)


class AgentImageBody(BaseModel):
    name: str = Field(default="image", max_length=180)
    mime: str = Field(pattern=r"^image/(jpeg|png|gif|webp)$")
    data: str = Field(min_length=16, max_length=3_000_000)


class AgentTaskBody(BaseModel):
    prompt: str = Field(min_length=1, max_length=20_000)
    cwd: str = Field(min_length=1, max_length=4000)
    provider_id: str = Field(min_length=1, max_length=80)
    model: str | None = Field(default=None, max_length=200)
    attachments: list[AgentImageBody] = Field(default_factory=list, max_length=4)
    limits: dict[str, int] | None = None
    mode: str = Field(default="agent", pattern=r"^(ask|agent)$")


class AgentApprovalBody(BaseModel):
    decision: str = Field(pattern=r"^(approved|denied)$")


class AgentSteerBody(BaseModel):
    message: str = Field(min_length=1, max_length=4000)


class AgentRetentionBody(BaseModel):
    retention_days: int = Field(default=30, ge=1, le=3650)
    max_bytes: int = Field(default=500_000_000, ge=1_000_000, le=100_000_000_000)


class ProjectBody(BaseModel):
    path: str = Field(min_length=1, max_length=4000)
    name: str = Field(default="", max_length=120)


class ProjectNameBody(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class FileSaveBody(BaseModel):
    path: str = Field(min_length=1, max_length=4000)
    content: str = Field(default="", max_length=2_100_000)
    revision: str = Field(min_length=1, max_length=128)


class FileActionBody(BaseModel):
    action: str = Field(pattern=r"^(file|directory|move|delete)$")
    path: str = Field(min_length=1, max_length=4000)
    destination: str = Field(default="", max_length=4000)
    revision: str = Field(default="", max_length=128)


class FileSearchBody(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    content: bool = True
    case_sensitive: bool = False


class GitCommitBody(BaseModel):
    message: str = Field(min_length=1, max_length=4000)


class GitHunkBody(BaseModel):
    patch: str = Field(min_length=1, max_length=1_000_000)
    stage: bool = True


class GitStageBody(BaseModel):
    paths: list[str] = Field(default_factory=list)
    stage: bool = True


class GitBranchBody(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    create: bool = False


class PreviewBody(BaseModel):
    name: str = Field(default="", max_length=120)
    url: str = Field(min_length=1, max_length=2000)


def _require(state: AppState, provided: str | None) -> None:
    if not state.auth.check(provided):
        raise HTTPException(status_code=401, detail="invalid passcode")


def _require_scope(state: AppState, provided: str | None, scope: str) -> None:
    if not state.auth.check(provided):
        raise HTTPException(status_code=401, detail="invalid passcode")
    if not state.auth.allows(provided, scope):
        raise HTTPException(status_code=403, detail=f"missing scope: {scope}")


def create_app(state: AppState | None = None, web_dir: Path | None = None) -> FastAPI:
    state = state or AppState()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        state.forwards.start_auto()
        yield
        state.forwards.stop_all()
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
        capabilities["agent_credentials"] = state.credentials.available()
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
        which = body.which if body is not None else ["screen_recording", "accessibility"]
        from termx.desktop import broker as desktop_broker

        # With the desktop shell attached, the broker prompts from the Termx.app
        # process itself (the identity the user manages in System Settings).
        # Without it (plain `termx` from a terminal) fall back to the shell
        # notification channel for a best-effort prompt.
        if not desktop_broker.available():
            for item in which:
                if item in {"screen_recording", "accessibility", "open_settings"}:
                    notify.permission(item)
        log_event("permissions_request", which=",".join(which))
        return request_permissions(which)

    @app.get("/api/update/check")
    def get_update(
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, object]:
        _require(state, provided(x_termx_passcode, authorization, k))
        from termx import __version__
        from termx.update import check_for_update

        return check_for_update(__version__)

    @app.post("/api/update/apply")
    def post_update(
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, object]:
        _require(state, provided(x_termx_passcode, authorization, k))
        from termx import __version__
        from termx.update import check_for_update, desktop_managed

        if not desktop_managed():
            info = check_for_update(__version__)
            return {
                "started": False,
                "reason": "this host runs the backend without the desktop app",
                "instructions": "install the new release, or update the termx package",
                **info,
            }
        notify.update("install")
        log_event("update_apply", version=__version__)
        return {"started": True}

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

    @app.get("/api/devices")
    def list_devices(
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, object]:
        _require(state, provided(x_termx_passcode, authorization, k))
        return {"devices": state.tokens.list_public()}

    @app.delete("/api/devices/{device_id}")
    def revoke_device(
        device_id: str,
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, bool]:
        _require(state, provided(x_termx_passcode, authorization, k))
        if not state.tokens.revoke(device_id):
            raise HTTPException(status_code=404, detail="device not found")
        log_event("device_revoke", device_id=device_id)
        return {"ok": True}

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

    # Agent -----------------------------------------------------------

    @app.get("/api/agent/providers")
    def list_agent_providers(
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, object]:
        secret = provided(x_termx_passcode, authorization, k)
        _require_scope(state, secret, "agent-view")
        return {"providers": state.agent.list_providers()}

    @app.put("/api/agent/providers/{provider_id}")
    def put_agent_provider(
        provider_id: str,
        body: AgentProviderBody,
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, object]:
        secret = provided(x_termx_passcode, authorization, k)
        _require_scope(state, secret, "ai-settings")
        if provider_id != body.id:
            raise HTTPException(status_code=400, detail="provider id does not match path")
        try:
            provider = state.agent.save_provider(
                provider_id=body.id,
                kind=body.kind,
                name=body.name,
                base_url=body.base_url,
                model=body.model,
                capabilities=body.capabilities,
                api_key=body.api_key,
            )
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        log_event("agent_provider_save", provider_id=provider_id, provider_kind=body.kind)
        return provider

    @app.delete("/api/agent/providers/{provider_id}")
    def delete_agent_provider(
        provider_id: str,
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, bool]:
        secret = provided(x_termx_passcode, authorization, k)
        _require_scope(state, secret, "ai-settings")
        try:
            deleted = state.agent.delete_provider(provider_id)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if not deleted:
            raise HTTPException(status_code=404, detail="provider not found")
        log_event("agent_provider_delete", provider_id=provider_id)
        return {"ok": True}

    @app.post("/api/agent/providers/{provider_id}/test")
    async def test_agent_provider(
        provider_id: str,
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, str]:
        secret = provided(x_termx_passcode, authorization, k)
        _require_scope(state, secret, "ai-settings")
        try:
            return await state.agent.test_provider(provider_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="provider not found") from exc
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/agent/tasks")
    def list_agent_tasks(
        limit: int = Query(default=100, ge=1, le=500),
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, object]:
        secret = provided(x_termx_passcode, authorization, k)
        _require_scope(state, secret, "agent-view")
        return {"tasks": state.agent_store.list_tasks(limit=limit)}

    @app.post("/api/agent/tasks")
    async def create_agent_task(
        body: AgentTaskBody,
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, object]:
        secret = provided(x_termx_passcode, authorization, k)
        _require_scope(state, secret, "agent-run")
        try:
            task = await state.agent.create_task(
                prompt=body.prompt,
                cwd=body.cwd,
                provider_id=body.provider_id,
                limits=body.limits,
                mode=body.mode,
                model=body.model,
                attachments=[{"name": item.name, "mime": item.mime, "data": item.data} for item in body.attachments],
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="provider not found") from exc
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        log_event("agent_task_create", task_id=task["id"], provider_id=body.provider_id)
        return task

    @app.get("/api/agent/tasks/{task_id}")
    def get_agent_task(
        task_id: str,
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, object]:
        secret = provided(x_termx_passcode, authorization, k)
        _require_scope(state, secret, "agent-view")
        task = state.agent_store.get_task(task_id, include_events=True)
        if task is None:
            raise HTTPException(status_code=404, detail="task not found")
        return task

    @app.delete("/api/agent/tasks/{task_id}")
    def delete_agent_task(
        task_id: str,
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, bool]:
        secret = provided(x_termx_passcode, authorization, k)
        _require_scope(state, secret, "agent-control")
        try:
            deleted = state.agent_store.delete_task(task_id)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if not deleted:
            raise HTTPException(status_code=404, detail="task not found")
        return {"ok": True}

    @app.post("/api/agent/tasks/{task_id}/approvals/{approval_id}")
    async def resolve_agent_approval(
        task_id: str,
        approval_id: str,
        body: AgentApprovalBody,
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, object]:
        secret = provided(x_termx_passcode, authorization, k)
        _require_scope(state, secret, "agent-control")
        try:
            return await state.agent.resolve_approval(task_id, approval_id, body.decision)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="approval not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/agent/tasks/{task_id}/steer")
    def steer_agent_task(
        task_id: str,
        body: AgentSteerBody,
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, object]:
        secret = provided(x_termx_passcode, authorization, k)
        _require_scope(state, secret, "agent-control")
        try:
            return state.agent.steer(task_id, body.message)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="task not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/agent/tasks/{task_id}/cancel")
    async def cancel_agent_task(
        task_id: str,
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, object]:
        secret = provided(x_termx_passcode, authorization, k)
        _require_scope(state, secret, "agent-control")
        try:
            return state.agent.cancel(task_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="task not found") from exc

    @app.post("/api/agent/tasks/{task_id}/takeover")
    async def takeover_agent_task(
        task_id: str,
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, object]:
        secret = provided(x_termx_passcode, authorization, k)
        _require_scope(state, secret, "agent-control")
        try:
            return await state.agent.takeover(task_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="task not found") from exc

    @app.get("/api/agent/tasks/{task_id}/export")
    def export_agent_task(
        task_id: str,
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> Response:
        secret = provided(x_termx_passcode, authorization, k)
        _require_scope(state, secret, "agent-view")
        task = state.agent_store.export_task(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="task not found")
        return Response(
            content=json.dumps(task, ensure_ascii=False, indent=2),
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="termx-task-{task_id}.json"'},
        )

    @app.get("/api/agent/tasks/{task_id}/artifacts/{artifact_id}")
    def get_agent_artifact(
        task_id: str,
        artifact_id: str,
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> FileResponse:
        secret = provided(x_termx_passcode, authorization, k)
        _require_scope(state, secret, "agent-view")
        artifact = state.agent_store.get_artifact(task_id, artifact_id)
        if artifact is None:
            raise HTTPException(status_code=404, detail="artifact not found")
        suffix = {"image/jpeg": "jpg", "image/png": "png", "image/gif": "gif", "image/webp": "webp"}.get(artifact["mime"], "bin")
        return FileResponse(artifact["path"], media_type=artifact["mime"], filename=f"{artifact_id}.{suffix}")

    @app.get("/api/agent/storage")
    def get_agent_storage(
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, object]:
        secret = provided(x_termx_passcode, authorization, k)
        _require_scope(state, secret, "ai-settings")
        return state.agent_store.storage_status()

    @app.post("/api/agent/storage/prune")
    def prune_agent_storage(
        body: AgentRetentionBody | None = None,
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, object]:
        secret = provided(x_termx_passcode, authorization, k)
        _require_scope(state, secret, "ai-settings")
        body = body or AgentRetentionBody()
        state.agent_store.set_retention_policy(
            retention_days=body.retention_days,
            max_bytes=body.max_bytes,
        )
        removed = state.agent_store.prune(retention_days=body.retention_days, max_bytes=body.max_bytes)
        return {"removed": removed, "storage": state.agent_store.storage_status(retention_days=body.retention_days, max_bytes=body.max_bytes)}

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
        files: int = Query(default=0, ge=0, le=1),
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, object]:
        _require(state, provided(x_termx_passcode, authorization, k))
        try:
            return list_dir_entries(path, include_files=bool(files))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/fs/download")
    def download_file(
        path: str = Query(...),
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> FileResponse:
        _require(state, provided(x_termx_passcode, authorization, k))
        try:
            resolved = Path(path).expanduser().resolve(strict=True)
        except OSError as exc:
            raise HTTPException(status_code=404, detail="file not found") from exc
        if not resolved.is_file():
            raise HTTPException(status_code=404, detail="not a file")
        log_event("fs_download", path=str(resolved), size=resolved.stat().st_size)
        return FileResponse(resolved, filename=resolved.name)

    @app.post("/api/fs/upload")
    async def upload_file(
        request: Request,
        dir: str = Query(...),
        x_termx_name: str | None = Header(default=None),
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, object]:
        _require(state, provided(x_termx_passcode, authorization, k))
        name = Path(unquote(x_termx_name or "")).name.strip()
        if not name or name in {".", ".."}:
            raise HTTPException(status_code=400, detail="file name required")
        try:
            target_dir = Path(validate_cwd(dir))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        target = target_dir / name
        if target.exists():
            stem, suffix = target.stem, target.suffix
            for index in range(1, 1000):
                candidate = target_dir / f"{stem} ({index}){suffix}"
                if not candidate.exists():
                    target = candidate
                    break
        written = 0
        fd, tmp = tempfile.mkstemp(dir=str(target_dir), prefix=".termx-upload-")
        try:
            with os.fdopen(fd, "wb") as handle:
                async for chunk in request.stream():
                    if not chunk:
                        continue
                    handle.write(chunk)
                    written += len(chunk)
            os.replace(tmp, target)
        except Exception as exc:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise HTTPException(status_code=500, detail=f"upload failed: {exc}") from exc
        log_event("fs_upload", path=str(target), size=written)
        return {"ok": True, "path": str(target), "name": target.name, "size": written}

    # Editor / project files ------------------------------------------
    @app.get("/api/projects")
    def list_projects(
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, object]:
        _require(state, provided(x_termx_passcode, authorization, k))
        return {"projects": state.projects.projects()}

    @app.post("/api/projects")
    def register_project(
        body: ProjectBody,
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, object]:
        _require(state, provided(x_termx_passcode, authorization, k))
        try:
            project = state.projects.register(body.path, body.name)
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        log_event("project_register", project_id=project["id"])
        return project

    @app.patch("/api/projects/{project_id}")
    def rename_project(
        project_id: str,
        body: ProjectNameBody,
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, object]:
        _require(state, provided(x_termx_passcode, authorization, k))
        return state.projects.update_project(project_id, body.name)

    @app.delete("/api/projects/{project_id}")
    def forget_project(
        project_id: str,
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, bool]:
        _require(state, provided(x_termx_passcode, authorization, k))
        state.projects.forget(project_id)
        return {"ok": True}

    @app.get("/api/projects/{project_id}/tree")
    def project_tree(
        project_id: str,
        path: str = Query(default=""),
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=200, ge=1, le=1000),
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, object]:
        _require(state, provided(x_termx_passcode, authorization, k))
        return state.projects.listing(project_id, path, offset, limit)

    @app.get("/api/projects/{project_id}/file")
    def project_file(
        project_id: str,
        path: str = Query(...),
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, object]:
        _require(state, provided(x_termx_passcode, authorization, k))
        return state.projects.read(project_id, path)

    @app.put("/api/projects/{project_id}/file")
    def project_file_save(
        project_id: str,
        body: FileSaveBody,
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, object]:
        _require(state, provided(x_termx_passcode, authorization, k))
        result = state.projects.save(project_id, body.path, body.content, body.revision)
        log_event("project_file_save", project_id=project_id, path=body.path, size=result.get("size"))
        return result

    @app.get("/api/projects/{project_id}/download")
    def project_file_download(
        project_id: str,
        path: str = Query(...),
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> FileResponse:
        _require(state, provided(x_termx_passcode, authorization, k))
        target = state.projects.resolve(project_id, path)
        if not target.is_file():
            raise HTTPException(status_code=404, detail="not a file")
        return FileResponse(target, filename=target.name)

    @app.post("/api/projects/{project_id}/upload")
    async def project_file_upload(
        project_id: str,
        request: Request,
        path: str = Query(default=""),
        x_termx_name: str | None = Header(default=None),
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, object]:
        _require(state, provided(x_termx_passcode, authorization, k))
        name = Path(unquote(x_termx_name or "")).name.strip()
        if not name or name in {".", ".."}:
            raise HTTPException(status_code=400, detail="file name required")
        target_dir = state.projects.resolve(project_id, path)
        if not target_dir.is_dir():
            raise HTTPException(status_code=400, detail="not a directory")
        relative = f"{path.rstrip('/')}/{name}" if path else name
        target = state.projects.resolve(project_id, relative, exists=False)
        if target.exists():
            raise HTTPException(status_code=409, detail="A file with this name already exists")
        written = 0
        fd, temporary = tempfile.mkstemp(dir=target_dir, prefix=".termx-import-")
        try:
            with os.fdopen(fd, "wb") as handle:
                async for chunk in request.stream():
                    written += len(chunk)
                    if written > 100 * 1024 * 1024:
                        raise HTTPException(status_code=413, detail="Import exceeds the 100 MiB limit")
                    handle.write(chunk)
            os.chmod(temporary, 0o644)
            try:
                os.link(temporary, target)
            except FileExistsError as exc:
                raise HTTPException(status_code=409, detail="A file with this name already exists") from exc
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        log_event("project_file_upload", project_id=project_id, path=relative, size=written)
        return {"ok": True, "path": relative, "name": name, "size": written}

    @app.post("/api/projects/{project_id}/action")
    def project_file_action(
        project_id: str,
        body: FileActionBody,
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, object]:
        _require(state, provided(x_termx_passcode, authorization, k))
        result = state.projects.mutate(project_id, body.action, body.path, body.destination, body.revision)
        log_event("project_file_action", project_id=project_id, action=body.action, path=body.path)
        return result

    @app.post("/api/projects/{project_id}/search")
    def project_search(
        project_id: str,
        body: FileSearchBody,
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, object]:
        _require(state, provided(x_termx_passcode, authorization, k))
        return state.projects.search(project_id, body.query, content=body.content, case_sensitive=body.case_sensitive)

    # Git ------------------------------------------------------------
    def _project_root(project_id: str) -> str:
        return state.projects.project(project_id)["path"]

    @app.get("/api/projects/{project_id}/git/status")
    def git_status(
        project_id: str,
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, object]:
        _require(state, provided(x_termx_passcode, authorization, k))
        return git_ops.status(_project_root(project_id))

    @app.get("/api/projects/{project_id}/git/diff")
    def git_diff(
        project_id: str,
        path: str = Query(...),
        staged: int = Query(default=0, ge=0, le=1),
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, object]:
        _require(state, provided(x_termx_passcode, authorization, k))
        return git_ops.diff(_project_root(project_id), path, staged=bool(staged))

    @app.post("/api/projects/{project_id}/git/stage")
    def git_stage(
        project_id: str,
        body: GitStageBody,
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, object]:
        _require(state, provided(x_termx_passcode, authorization, k))
        return git_ops.stage(_project_root(project_id), body.paths, body.stage)

    @app.post("/api/projects/{project_id}/git/hunk")
    def git_hunk(
        project_id: str,
        body: GitHunkBody,
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, object]:
        _require(state, provided(x_termx_passcode, authorization, k))
        return git_ops.stage_hunk(_project_root(project_id), body.patch, body.stage)

    @app.post("/api/projects/{project_id}/git/commit")
    def git_commit(
        project_id: str,
        body: GitCommitBody,
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, object]:
        _require(state, provided(x_termx_passcode, authorization, k))
        result = git_ops.commit(_project_root(project_id), body.message)
        log_event("git_commit", project_id=project_id)
        return result

    @app.get("/api/projects/{project_id}/git/branches")
    def git_branches(
        project_id: str,
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, object]:
        _require(state, provided(x_termx_passcode, authorization, k))
        return git_ops.branches(_project_root(project_id))

    @app.post("/api/projects/{project_id}/git/branch")
    def git_branch(
        project_id: str,
        body: GitBranchBody,
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, object]:
        _require(state, provided(x_termx_passcode, authorization, k))
        result = git_ops.switch_branch(_project_root(project_id), body.name, body.create)
        log_event("git_branch", project_id=project_id, create=body.create)
        return result

    @app.post("/api/projects/{project_id}/git/{operation}")
    def git_remote_op(
        project_id: str,
        operation: str,
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, object]:
        _require(state, provided(x_termx_passcode, authorization, k))
        root = _project_root(project_id)
        if operation == "fetch":
            return git_ops.fetch(root)
        if operation == "pull":
            return git_ops.pull(root)
        if operation == "push":
            log_event("git_push", project_id=project_id)
            return git_ops.push(root)
        raise HTTPException(status_code=404, detail="unknown git operation")

    @app.get("/api/projects/{project_id}/previews")
    def project_previews(
        project_id: str,
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, object]:
        _require(state, provided(x_termx_passcode, authorization, k))
        return {"previews": state.projects.previews(project_id)}

    @app.post("/api/projects/{project_id}/previews")
    def create_project_preview(
        project_id: str,
        body: PreviewBody,
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, object]:
        _require(state, provided(x_termx_passcode, authorization, k))
        result = state.projects.add_preview(project_id, body.name, body.url)
        log_event("project_preview_create", project_id=project_id)
        return result

    @app.delete("/api/projects/{project_id}/previews/{preview_id}")
    def remove_project_preview(
        project_id: str,
        preview_id: str,
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, bool]:
        _require(state, provided(x_termx_passcode, authorization, k))
        state.projects.delete_preview(project_id, preview_id)
        return {"ok": True}

    @app.get("/api/projects/{project_id}/lsp")
    def project_lsp_servers(
        project_id: str,
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, object]:
        _require(state, provided(x_termx_passcode, authorization, k))
        state.projects.project(project_id)
        return {"servers": lsp.server_snapshot()}

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

    @app.get("/api/workspace")
    def get_workspace(
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, object]:
        _require(state, provided(x_termx_passcode, authorization, k))
        workspace = state.store.get_workspace()
        return {
            "sessions": [item.public() for item in workspace.sessions],
            "saved_at": workspace.saved_at,
        }

    @app.put("/api/workspace")
    def put_workspace(
        body: WorkspaceBody,
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, object]:
        _require(state, provided(x_termx_passcode, authorization, k))
        saved = state.store.save_workspace(
            [WorkspaceSession(title=item.title, shell=item.shell, cwd=item.cwd) for item in body.sessions]
        )
        return {"sessions": [item.public() for item in saved.sessions], "saved_at": saved.saved_at}

    @app.post("/api/workspace/restore")
    def restore_workspace(
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, object]:
        _require(state, provided(x_termx_passcode, authorization, k))
        # Return host-owned live sessions unchanged. Replaying persisted specs
        # on every new client connection duplicated PTYs and then compounded
        # the duplicates when the client saved its next workspace snapshot.
        with state.workspace_restore_lock:
            live = state.sessions.list()
            if live:
                return {
                    "sessions": [session.snapshot() for session in live],
                    "restored": 0,
                    "already_running": len(live),
                }

            prefs = state.store.get().terminal
            workspace = state.store.get_workspace()
            restored = []
            for item in workspace.sessions:
                try:
                    shell = validate_shell(item.shell or prefs.shell)
                except ValueError:
                    shell = prefs.shell
                try:
                    cwd = validate_cwd(item.cwd or prefs.cwd)
                except ValueError:
                    cwd = prefs.cwd
                session = state.sessions.create(
                    cols=DEFAULT_COLS,
                    rows=DEFAULT_ROWS,
                    title=item.title or None,
                    argv=default_argv(shell),
                    cwd=cwd,
                    shell=shell,
                )
                restored.append(session.snapshot())
        if restored:
            log_event("workspace_restore", count=len(restored))
        return {"sessions": restored, "restored": len(restored), "already_running": 0}

    @app.get("/api/forwards")
    def list_forwards(
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, object]:
        _require(state, provided(x_termx_passcode, authorization, k))
        return {
            "rules": [rule.public() for rule in state.store.list_rules()],
            "statuses": state.forwards.statuses(),
        }

    @app.post("/api/forwards")
    def create_forward(
        body: ForwardBody,
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, object]:
        _require(state, provided(x_termx_passcode, authorization, k))
        try:
            rule = state.store.add_rule(
                name=body.name,
                kind=body.kind,
                listen_port=body.listen_port,
                target_port=body.target_port,
                ssh_host=body.ssh_host,
                listen_host=body.listen_host,
                target_host=body.target_host,
                auto_start=body.auto_start,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        log_event("forward_create", rule_id=rule.id, forward_kind=rule.kind, listen_port=rule.listen_port)
        return rule.public()

    @app.patch("/api/forwards/{rule_id}")
    def patch_forward(
        rule_id: str,
        body: ForwardPatchBody,
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, object]:
        _require(state, provided(x_termx_passcode, authorization, k))
        rule = state.store.patch_rule(rule_id, auto_start=body.auto_start, name=body.name)
        if rule is None:
            raise HTTPException(status_code=404, detail="rule not found")
        return rule.public()

    @app.delete("/api/forwards/{rule_id}")
    def delete_forward(
        rule_id: str,
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, bool]:
        _require(state, provided(x_termx_passcode, authorization, k))
        state.forwards.stop(rule_id)
        if not state.store.delete_rule(rule_id):
            raise HTTPException(status_code=404, detail="rule not found")
        log_event("forward_delete", rule_id=rule_id)
        return {"ok": True}

    @app.post("/api/forwards/{rule_id}/start")
    def start_forward(
        rule_id: str,
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, object]:
        _require(state, provided(x_termx_passcode, authorization, k))
        rule = state.store.get_rule(rule_id)
        if rule is None:
            raise HTTPException(status_code=404, detail="rule not found")
        status = state.forwards.start(rule)
        log_event("forward_start", rule_id=rule_id, state=status.state)
        return status.public()

    @app.post("/api/forwards/{rule_id}/stop")
    def stop_forward(
        rule_id: str,
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, object]:
        _require(state, provided(x_termx_passcode, authorization, k))
        if state.store.get_rule(rule_id) is None:
            raise HTTPException(status_code=404, detail="rule not found")
        state.forwards.stop(rule_id)
        log_event("forward_stop", rule_id=rule_id)
        return state.forwards.status_for(rule_id).public()

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

    @app.websocket("/api/projects/{project_id}/lsp/{language}")
    async def project_lsp_socket(
        websocket: WebSocket,
        project_id: str,
        language: str,
        k: str | None = None,
    ) -> None:
        header_k = websocket.headers.get("x-termx-passcode")
        authorization = websocket.headers.get("authorization")
        token = extract_passcode(header_k, authorization, k)
        if not state.auth.check(token):
            await websocket.close(code=4401)
            return
        try:
            root = state.projects.project(project_id)["path"]
        except HTTPException:
            await websocket.close(code=4404)
            return
        await lsp.serve(websocket, str(root), language)

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

    @app.websocket("/api/agent/tasks/{task_id}/events")
    async def agent_events_socket(
        websocket: WebSocket,
        task_id: str,
        k: str | None = None,
        after: int = 0,
    ) -> None:
        header_k = websocket.headers.get("x-termx-passcode")
        authorization = websocket.headers.get("authorization")
        token = extract_passcode(header_k, authorization, k)
        if not state.auth.check(token):
            await websocket.close(code=4401)
            return
        if not state.auth.allows(token, "agent-view"):
            await websocket.close(code=4403)
            return
        if state.agent_store.get_task(task_id) is None:
            await websocket.close(code=4404)
            return
        queue = state.agent.subscribe(task_id)
        await websocket.accept()
        cursor = max(0, after)
        try:
            for event in state.agent_store.events(task_id, after=cursor):
                await websocket.send_json(event)
                cursor = max(cursor, int(event["sequence"]))
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=20)
                except asyncio.TimeoutError:
                    await websocket.send_json({"type": "heartbeat", "sequence": cursor})
                    continue
                sequence = int(event["sequence"])
                if sequence <= cursor:
                    continue
                await websocket.send_json(event)
                cursor = sequence
        except WebSocketDisconnect:
            pass
        finally:
            state.agent.unsubscribe(task_id, queue)

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

    @app.get("/_/vendor/{name}")
    def vendor_file(name: str) -> FileResponse:
        if not re.fullmatch(r"[A-Za-z0-9._-]+", name):
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
