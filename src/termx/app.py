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
from time import time
from typing import Any

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
from termx.terminals import TerminalError
from termx.tokens import SCOPES, TokenStore
from termx.tunnels import TunnelManager

from termx import __version__

PACKAGE_STATIC = Path(__file__).parent / "static"

# Browsers on loopback or private LAN addresses are the only web origins the
# daemon serves by default; anything else (e.g. a public tunnel hostname used
# from another origin) must be opted in via TERMX_CORS_ORIGINS.
_LAN_ORIGIN_REGEX = (
    r"^https?://(localhost|127\.0\.0\.1|192\.168\.\d+\.\d+|10\.\d+\.\d+\.\d+"
    r"|172\.(1[6-9]|2\d|3[01])\.\d+\.\d+)(:\d+)?$"
)


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
        self.projects = ProjectFiles()
        self.agent = AgentManager(
            self.agent_store,
            self.credentials,
            self.desktop,
            adapter_factory=adapter_factory,
            project_files=self.projects,
        )
        self.port = port
        self.request_shutdown = None


class CreateSessionBody(BaseModel):
    cols: int = Field(default=DEFAULT_COLS, ge=1, le=500)
    rows: int = Field(default=DEFAULT_ROWS, ge=1, le=200)
    title: str | None = None
    shell: str | None = None
    cwd: str | None = None
    # host (default, legacy unrestricted) or a restricted profile — the
    # session routes through runner_for(profile) and its pty spawn_argv.
    sandbox_profile: str | None = Field(default=None, pattern=r"^(host|workspace|agent)$")


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
    provider_id: str | None = Field(default=None, max_length=80)
    model: str | None = Field(default=None, max_length=200)
    attachments: list[AgentImageBody] = Field(default_factory=list, max_length=4)
    limits: dict[str, int] | None = None
    mode: str = Field(default="agent", pattern=r"^(ask|agent)$")
    execution_mode: str | None = Field(default=None, pattern=r"^(direct|worktree)$")
    conversation_id: str | None = Field(default=None, max_length=80)
    custom_agent_id: str | None = Field(default=None, max_length=80)


class WorktreeActionBody(BaseModel):
    action: str = Field(pattern=r"^(apply|keep|discard)$")
    confirm: bool = False


class PreviewFromPortBody(BaseModel):
    port: int = Field(ge=1, le=65535)
    name: str = Field(default="", max_length=80)


class RunbookBody(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    project_id: str | None = Field(default=None, max_length=80)
    steps: list[dict[str, Any]] = Field(min_length=1, max_length=50)


class RunbookPatchBody(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    project_id: str | None = Field(default=None, max_length=80)
    steps: list[dict[str, Any]] | None = Field(default=None, min_length=1, max_length=50)


class RunbookRunBody(BaseModel):
    # Execution profile for a human-invoked runbook run. "host" keeps the
    # legacy unrestricted behavior; "workspace" runs each step inside the
    # restricted backend (net still governed by remembered grants).
    profile: str = Field(default="host", pattern=r"^(host|workspace)$")


class ConversationBody(BaseModel):
    title: str = Field(default="", max_length=300)
    project_id: str | None = Field(default=None, max_length=200)
    cwd: str | None = Field(default=None, max_length=4000)
    mode: str = Field(default="ask", pattern=r"^(ask|agent)$")
    custom_agent_id: str | None = Field(default=None, max_length=80)
    provider_id: str | None = Field(default=None, max_length=80)
    model: str | None = Field(default=None, max_length=200)
    pinned: bool = False
    archived: bool = False
    draft: bool = False


class ConversationPatchBody(BaseModel):
    title: str | None = Field(default=None, max_length=300)
    project_id: str | None = Field(default=None, max_length=200)
    cwd: str | None = Field(default=None, max_length=4000)
    mode: str | None = Field(default=None, pattern=r"^(ask|agent)$")
    custom_agent_id: str | None = Field(default=None, max_length=80)
    provider_id: str | None = Field(default=None, max_length=80)
    model: str | None = Field(default=None, max_length=200)
    pinned: bool | None = None
    archived: bool | None = None
    draft: bool | None = None


class ConversationTurnBody(BaseModel):
    prompt: str = Field(min_length=1, max_length=20_000)
    task_id: str | None = Field(default=None, max_length=80)
    mode: str | None = Field(default=None, pattern=r"^(ask|agent)$")
    provider_id: str | None = Field(default=None, max_length=80)
    model: str | None = Field(default=None, max_length=200)
    context_refs: list[dict[str, Any]] = Field(default_factory=list, max_length=50)
    attachment_refs: list[dict[str, Any]] = Field(default_factory=list, max_length=20)


class CustomAgentBody(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)
    instructions: str = Field(default="", max_length=20_000)
    provider_id: str | None = Field(default=None, max_length=80)
    model: str | None = Field(default=None, max_length=200)
    tools: list[str] = Field(default_factory=list, max_length=64)
    limits: dict[str, Any] = Field(default_factory=dict)
    approval_mode: str = Field(default="standard", pattern=r"^(standard|remember|autonomous)$")
    sandbox_profile: str = Field(default="agent", pattern=r"^(host|workspace|agent)$")


class CustomAgentPatchBody(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    instructions: str | None = Field(default=None, max_length=20_000)
    provider_id: str | None = Field(default=None, max_length=80)
    model: str | None = Field(default=None, max_length=200)
    tools: list[str] | None = Field(default=None, max_length=64)
    limits: dict[str, Any] | None = None
    approval_mode: str | None = Field(default=None, pattern=r"^(standard|remember|autonomous)$")
    sandbox_profile: str | None = Field(default=None, pattern=r"^(host|workspace|agent)$")


class AgentApprovalBody(BaseModel):
    decision: str = Field(pattern=r"^(approved|denied)$")
    # v2: "once" or omitted = just this call; task/project/custom_agent
    # persist a remembered policy rule for equivalent actions.
    remember: str | None = Field(
        default=None, pattern=r"^(once|task|project|custom_agent)$"
    )


class PolicyRuleBody(BaseModel):
    effect: str = Field(pattern=r"^(allow|deny)$")
    scope_type: str = Field(pattern=r"^(task|project|custom_agent|host)$")
    scope_id: str | None = Field(default=None, max_length=128)
    action_type: str = Field(default="tool", pattern=r"^(tool|capability|publication|computer)$")
    tool: str = Field(min_length=1, max_length=80)
    fingerprint: str = Field(min_length=1, max_length=128)
    fingerprint_kind: str = Field(default="exact", pattern=r"^(exact|conservative)$")
    matcher: dict[str, Any] = Field(default_factory=dict)
    capabilities: list[str] = Field(default_factory=list, max_length=32)
    sandbox_profile: str | None = Field(default=None, pattern=r"^(host|workspace|agent)$")
    display: str = Field(default="", max_length=2000)
    task_id: str | None = Field(default=None, max_length=64)
    project_id: str | None = Field(default=None, max_length=128)
    expires_at: float | None = None


class PolicyRulePatchBody(BaseModel):
    effect: str | None = Field(default=None, pattern=r"^(allow|deny)$")
    expires_at: float | None = None
    display: str | None = Field(default=None, max_length=2000)
    capabilities: list[str] | None = Field(default=None, max_length=32)


class AgentSteerBody(BaseModel):
    message: str = Field(min_length=1, max_length=4000)


class RecoverTaskBody(BaseModel):
    confirm: bool = False


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


class PairBody(BaseModel):
    device_name: str = Field(default="", max_length=80)
    scopes: list[str] | None = Field(default=None, max_length=20)
    expires_in_s: int | None = Field(default=None, ge=60, le=60 * 60 * 24 * 365)


class DeviceScopesBody(BaseModel):
    scopes: list[str] = Field(min_length=0, max_length=20)


class GitBranchBody(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    create: bool = False


class PreviewBody(BaseModel):
    name: str = Field(default="", max_length=120)
    url: str = Field(min_length=1, max_length=2000)


def _require_scope(state: AppState, provided: str | None, scope: str) -> None:
    if not state.auth.check(provided):
        raise HTTPException(status_code=401, detail="invalid passcode")
    if not state.auth.allows(provided, scope):
        raise HTTPException(status_code=403, detail=f"missing scope: {scope}")


def _require_any_scope(state: AppState, provided: str | None, scopes: tuple[str, ...]) -> None:
    if not state.auth.check(provided):
        raise HTTPException(status_code=401, detail="invalid passcode")
    if not any(state.auth.allows(provided, scope) for scope in scopes):
        raise HTTPException(status_code=403, detail=f"missing scope: {'|'.join(scopes)}")


def create_app(state: AppState | None = None, web_dir: Path | None = None) -> FastAPI:
    state = state or AppState()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        state.forwards.start_auto()
        for task_id in state.agent.pending_recoveries():
            asyncio.create_task(state.agent.recover_task(task_id))
        yield
        state.forwards.stop_all()
        await shutdown_state(state)

    app = FastAPI(title="termx", docs_url=None, redoc_url=None, lifespan=lifespan)
    app.state.termx = state
    cors_origins = [
        item.strip() for item in os.environ.get("TERMX_CORS_ORIGINS", "").split(",") if item.strip()
    ]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_origin_regex=None if cors_origins else _LAN_ORIGIN_REGEX,
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
        # Unauthenticated: keep the payload minimal. Clients gate features on
        # capabilities here (machine detail lives behind auth in /api/machine).
        capabilities = dict(
            machine_snapshot(state.store, webrtc=state.rtc.available())["capabilities"]
        )
        capabilities["agent_credentials"] = state.credentials.available()
        return {
            "app": "termx",
            "ok": True,
            "version": __version__,
            "passcode_required": state.auth.required,
            "capabilities": capabilities,
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
        _require_scope(state, provided(x_termx_passcode, authorization, k), "host-admin")
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
        _require_scope(state, provided(x_termx_passcode, authorization, k), "host-admin")
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
        _require_scope(state, provided(x_termx_passcode, authorization, k), "machine-view")
        notify.notify(body.title, body.body, kind="info", url=body.url)
        return {"ok": True}

    @app.get("/api/permissions")
    def get_permissions(
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, object]:
        _require_scope(state, provided(x_termx_passcode, authorization, k), "machine-view")
        return permission_snapshot()

    @app.post("/api/permissions/request")
    def post_permissions(
        body: PermissionsBody | None = None,
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, object]:
        _require_scope(state, provided(x_termx_passcode, authorization, k), "host-admin")
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
        _require_scope(state, provided(x_termx_passcode, authorization, k), "host-admin")
        from termx import __version__
        from termx.update import check_for_update

        return check_for_update(__version__)

    @app.post("/api/update/apply")
    def post_update(
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, object]:
        _require_scope(state, provided(x_termx_passcode, authorization, k), "host-admin")
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
        _require_scope(state, provided(x_termx_passcode, authorization, k), "host-admin")
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
        body: PairBody | None = None,
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, object]:
        secret = provided(x_termx_passcode, authorization, k)
        if state.auth.passcode is not None and not hmac.compare_digest(secret or "", state.auth.passcode):
            raise HTTPException(status_code=401, detail="invalid passcode")
        body = body or PairBody()
        expires_at = time() + body.expires_in_s if body.expires_in_s else None
        token = state.tokens.issue(
            body.scopes, device_name=body.device_name, expires_at=expires_at
        )
        scopes = state.tokens.check(token) or []
        log_event("pair", device_name=body.device_name, scopes=scopes)
        return {"token": token, "scopes": scopes, "expires_at": expires_at}

    @app.get("/api/devices")
    def list_devices(
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, object]:
        _require_scope(state, provided(x_termx_passcode, authorization, k), "host-admin")
        return {"devices": state.tokens.list_public()}

    @app.delete("/api/devices/{device_id}")
    def revoke_device(
        device_id: str,
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, bool]:
        _require_scope(state, provided(x_termx_passcode, authorization, k), "host-admin")
        if not state.tokens.revoke(device_id):
            raise HTTPException(status_code=404, detail="device not found")
        log_event("device_revoke", device_id=device_id)
        return {"ok": True}

    @app.post("/api/devices/{device_id}/scopes")
    def set_device_scopes(
        device_id: str,
        body: DeviceScopesBody,
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, object]:
        """Explicit admin re-scope — the v1 token migration/re-pair path."""
        _require_scope(state, provided(x_termx_passcode, authorization, k), "host-admin")
        device = state.tokens.update_scopes(device_id, body.scopes)
        if device is None:
            raise HTTPException(status_code=404, detail="device not found")
        log_event("device_scopes", device_id=device_id, scopes=device["scopes"])
        return {"device": device}

    @app.get("/api/audit")
    def get_audit(
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, object]:
        _require_scope(state, provided(x_termx_passcode, authorization, k), "host-admin")
        return {"events": read_events(100)}

    @app.get("/api/machine")
    def machine(
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, object]:
        _require_scope(state, provided(x_termx_passcode, authorization, k), "machine-view")
        return machine_snapshot(
            state.store, state.tunnels.status_public(), webrtc=state.rtc.available()
        )

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
        conversation = (
            state.agent_store.get_conversation(body.conversation_id)
            if body.conversation_id
            else None
        )
        if body.conversation_id and conversation is None:
            raise HTTPException(status_code=404, detail="conversation not found")
        custom_agent_id = body.custom_agent_id or (
            str(conversation.get("custom_agent_id"))
            if conversation and conversation.get("custom_agent_id")
            else None
        )
        custom_agent = (
            state.agent_store.get_custom_agent(custom_agent_id)
            if custom_agent_id
            else None
        )
        if custom_agent_id and custom_agent is None:
            raise HTTPException(status_code=404, detail="custom agent not found")
        provider_id = body.provider_id or (
            str(conversation.get("provider_id"))
            if conversation and conversation.get("provider_id")
            else None
        ) or (
            str(custom_agent.get("provider_id"))
            if custom_agent and custom_agent.get("provider_id")
            else None
        )
        model = body.model or (
            str(conversation.get("model"))
            if conversation and conversation.get("model")
            else None
        ) or (
            str(custom_agent.get("model"))
            if custom_agent and custom_agent.get("model")
            else None
        )
        if not provider_id:
            raise HTTPException(status_code=400, detail="provider_id is required")
        try:
            task = await state.agent.create_task(
                prompt=body.prompt,
                cwd=body.cwd,
                provider_id=provider_id,
                limits=body.limits,
                mode=body.mode,
                model=model,
                attachments=[{"name": item.name, "mime": item.mime, "data": item.data} for item in body.attachments],
                execution_mode=body.execution_mode,
                conversation_id=body.conversation_id or None,
                custom_agent_id=custom_agent_id,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="provider not found") from exc
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if body.conversation_id:
            state.agent_store.add_conversation_turn(
                body.conversation_id,
                prompt=body.prompt,
                task_id=task["id"],
                mode=body.mode,
                provider_id=body.provider_id,
                model=body.model,
                attachment_refs=[{"ref": item.name} for item in body.attachments],
            )
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
            deleted = state.agent.delete_task(task_id)
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
            return await state.agent.resolve_approval(
                task_id, approval_id, body.decision, remember=body.remember
            )
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

    @app.post("/api/agent/tasks/{task_id}/recover")
    async def recover_agent_task(
        task_id: str,
        body: RecoverTaskBody | None = None,
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, object]:
        secret = provided(x_termx_passcode, authorization, k)
        _require_scope(state, secret, "agent-control")
        try:
            return await state.agent.recover_task(task_id, confirm=bool(body and body.confirm))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="task not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

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

    @app.get("/api/agent/tasks/{task_id}/worktree")
    def get_task_worktree(
        task_id: str,
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, object]:
        secret = provided(x_termx_passcode, authorization, k)
        _require_scope(state, secret, "agent-view")
        try:
            return {"worktree": state.agent.task_worktree(task_id)}
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="task not found") from exc

    @app.post("/api/agent/tasks/{task_id}/worktree")
    def resolve_task_worktree(
        task_id: str,
        body: WorktreeActionBody,
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, object]:
        secret = provided(x_termx_passcode, authorization, k)
        _require_scope(state, secret, "agent-control")
        from termx.agent.worktrees import WorktreeConfirmRequired

        try:
            worktree = state.agent.resolve_worktree(
                task_id, body.action, confirm=body.confirm
            )
        except WorktreeConfirmRequired as exc:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": "worktree has uncommitted changes — confirm to discard",
                    "requires_confirm": True,
                    "dirty": exc.dirty,
                },
            ) from exc
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="task not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        log_event("agent_worktree", task_id=task_id, action=body.action)
        return {"worktree": worktree}

    # ------------------------------------------------------------------
    # Activity Center (PROD-004): normalized Termx-owned activity.

    @app.get("/api/activity")
    def get_activity(
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, object]:
        from termx.activity import activity_snapshot

        _require_scope(state, provided(x_termx_passcode, authorization, k), "machine-view")
        return activity_snapshot(state)

    @app.websocket("/api/activity/events")
    async def activity_events_socket(websocket: WebSocket, k: str | None = None) -> None:
        from termx.activity import activity_snapshot

        token = extract_passcode(
            websocket.headers.get("x-termx-passcode"),
            websocket.headers.get("authorization"),
            k,
        )
        if not state.auth.check(token):
            await websocket.close(code=4401)
            return
        if not state.auth.allows(token, "machine-view"):
            await websocket.close(code=4403)
            return
        await websocket.accept()
        last: dict[str, dict[str, object]] = {}
        try:
            while True:
                snapshot = activity_snapshot(state)
                current = {item["id"]: item for item in snapshot["activity"]}
                for item_id, item in current.items():
                    previous = last.get(item_id)
                    if previous != item:
                        await websocket.send_json({"type": "activity.upsert", "activity": item})
                for item_id in last.keys() - current.keys():
                    await websocket.send_json({"type": "activity.remove", "id": item_id})
                last = current
                await asyncio.sleep(2.0)
        except WebSocketDisconnect:
            pass

    # ------------------------------------------------------------------
    # Port + process discovery (PROD-005).

    @app.get("/api/ports")
    def get_ports(
        project_id: str | None = None,
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, object]:
        from termx.activity import _project_for
        from termx.processes import listeners

        _require_scope(state, provided(x_termx_passcode, authorization, k), "machine-view")
        try:
            projects = state.projects.projects()
        except Exception:
            projects = []
        from termx.processes import supported

        ports = listeners()
        for entry in ports:
            entry["project_id"] = _project_for(projects, entry.get("cwd"))
        if project_id:
            ports = [entry for entry in ports if entry["project_id"] == project_id]
        return {"ports": ports, "supported": supported()}

    @app.get("/api/processes")
    def get_processes(
        project_id: str | None = None,
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, object]:
        from termx.activity import _project_for
        from termx.processes import termx_processes

        _require_scope(state, provided(x_termx_passcode, authorization, k), "machine-view")
        try:
            projects = state.projects.projects()
        except Exception:
            projects = []
        roots = [str(project["path"]) for project in projects if project.get("path")]
        from termx.processes import supported

        procs = termx_processes(roots)
        for proc in procs:
            proc["project_id"] = _project_for(projects, proc.get("cwd"))
        if project_id:
            procs = [proc for proc in procs if proc["project_id"] == project_id]
        return {"processes": procs, "supported": supported()}

    @app.post("/api/projects/{project_id}/previews/from-port")
    def preview_from_port(
        project_id: str,
        body: PreviewFromPortBody,
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, object]:
        from termx.processes import listeners

        _require_scope(state, provided(x_termx_passcode, authorization, k), "files-write")
        match = next(
            (entry for entry in listeners() if entry["port"] == body.port), None
        )
        if match is None:
            raise HTTPException(status_code=404, detail="no listening process on that port")
        url = match.get("url") or f"http://127.0.0.1:{body.port}"
        result = state.projects.add_preview(project_id, body.name, str(url))
        log_event("project_preview_from_port", project_id=project_id, port=body.port)
        return {"preview": result, "listener": match}

    # ------------------------------------------------------------------
    # Runbooks (PROD-006): named multi-step workflows + run history.

    @app.get("/api/runbooks")
    def list_runbooks(
        project_id: str | None = None,
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, object]:
        _require_scope(state, provided(x_termx_passcode, authorization, k), "machine-view")
        return {"runbooks": state.agent_store.list_runbooks(project_id)}

    @app.post("/api/runbooks")
    def create_runbook(
        body: RunbookBody,
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, object]:
        from termx.runbooks import validate_steps

        _require_scope(state, provided(x_termx_passcode, authorization, k), "terminal-control")
        if body.project_id:
            state.projects.project(body.project_id)
        try:
            steps = validate_steps(body.steps)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        runbook = state.agent_store.create_runbook(
            name=body.name.strip(), project_id=body.project_id, steps=steps
        )
        log_event("runbook_create", runbook_id=runbook["id"])
        return {"runbook": runbook}

    @app.get("/api/runbooks/{runbook_id}")
    def get_runbook(
        runbook_id: str,
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, object]:
        _require_scope(state, provided(x_termx_passcode, authorization, k), "machine-view")
        runbook = state.agent_store.get_runbook(runbook_id)
        if runbook is None:
            raise HTTPException(status_code=404, detail="runbook not found")
        return {
            "runbook": runbook,
            "runs": state.agent_store.list_runbook_runs(runbook_id, limit=20),
        }

    @app.patch("/api/runbooks/{runbook_id}")
    def update_runbook(
        runbook_id: str,
        body: RunbookPatchBody,
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, object]:
        from termx.runbooks import validate_steps

        _require_scope(state, provided(x_termx_passcode, authorization, k), "terminal-control")
        if state.agent_store.get_runbook(runbook_id) is None:
            raise HTTPException(status_code=404, detail="runbook not found")
        updates: dict[str, Any] = {}
        if body.name is not None:
            if not body.name.strip():
                raise HTTPException(status_code=400, detail="name is required")
            updates["name"] = body.name.strip()
        if body.project_id is not None:
            state.projects.project(body.project_id)
            updates["project_id"] = body.project_id
        if body.steps is not None:
            try:
                updates["steps"] = validate_steps(body.steps)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        runbook = state.agent_store.update_runbook(runbook_id, **updates)
        return {"runbook": runbook}

    @app.delete("/api/runbooks/{runbook_id}")
    def delete_runbook(
        runbook_id: str,
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, object]:
        _require_scope(state, provided(x_termx_passcode, authorization, k), "terminal-control")
        if not state.agent_store.delete_runbook(runbook_id):
            raise HTTPException(status_code=404, detail="runbook not found")
        return {"deleted": True}

    @app.post("/api/runbooks/{runbook_id}/run")
    async def run_runbook(
        runbook_id: str,
        body: RunbookRunBody | None = None,
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, object]:
        _require_scope(state, provided(x_termx_passcode, authorization, k), "terminal-control")
        runbook = state.agent_store.get_runbook(runbook_id)
        if runbook is None:
            raise HTTPException(status_code=404, detail="runbook not found")
        cwd = str(Path.home())
        if runbook.get("project_id"):
            cwd = str(state.projects.project(runbook["project_id"])["path"])
        profile = (body.profile if body else "host") or "host"
        run = state.agent.runbooks.start(
            runbook, cwd, profile=profile, project_id=runbook.get("project_id")
        )
        log_event("runbook_run", runbook_id=runbook_id, run_id=run["id"], profile=profile)
        return {"run": run}

    @app.get("/api/runbook-runs")
    def list_runbook_runs(
        runbook_id: str | None = None,
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, object]:
        _require_scope(state, provided(x_termx_passcode, authorization, k), "machine-view")
        return {"runs": state.agent_store.list_runbook_runs(runbook_id)}

    @app.get("/api/runbook-runs/{run_id}")
    def get_runbook_run(
        run_id: str,
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, object]:
        _require_scope(state, provided(x_termx_passcode, authorization, k), "machine-view")
        run = state.agent_store.get_runbook_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="run not found")
        return {"run": run}

    @app.post("/api/runbook-runs/{run_id}/confirm")
    def confirm_runbook_run(
        run_id: str,
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, object]:
        _require_scope(state, provided(x_termx_passcode, authorization, k), "terminal-control")
        try:
            run = state.agent.runbooks.confirm(run_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="run not found") from exc
        return {"run": run}

    @app.post("/api/runbook-runs/{run_id}/cancel")
    async def cancel_runbook_run(
        run_id: str,
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, object]:
        _require_scope(state, provided(x_termx_passcode, authorization, k), "terminal-control")
        try:
            run = await state.agent.runbooks.cancel(run_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="run not found") from exc
        return {"run": run}

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
        removed = state.agent.prune_storage(retention_days=body.retention_days, max_bytes=body.max_bytes)
        return {"removed": removed, "storage": state.agent_store.storage_status(retention_days=body.retention_days, max_bytes=body.max_bytes)}

    # Host-persisted conversations (PROD-001) -------------------------------

    @app.get("/api/conversations")
    def list_conversations(
        archived: str | None = Query(default=None),
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, object]:
        secret = provided(x_termx_passcode, authorization, k)
        _require_scope(state, secret, "agent-view")
        flag: bool | None = False
        if archived in {"1", "true", "all"}:
            flag = None if archived == "all" else True
        return {"conversations": state.agent_store.list_conversations(archived=flag)}

    @app.post("/api/conversations")
    def create_conversation(
        body: ConversationBody,
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, object]:
        secret = provided(x_termx_passcode, authorization, k)
        _require_scope(state, secret, "agent-control")
        return {"conversation": state.agent_store.create_conversation(**body.model_dump())}

    @app.get("/api/conversations/{conversation_id}")
    def get_conversation(
        conversation_id: str,
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, object]:
        secret = provided(x_termx_passcode, authorization, k)
        _require_scope(state, secret, "agent-view")
        conversation = state.agent_store.get_conversation(conversation_id, include_turns=True)
        if conversation is None:
            raise HTTPException(status_code=404, detail="conversation not found")
        return {"conversation": conversation}

    @app.patch("/api/conversations/{conversation_id}")
    def patch_conversation(
        conversation_id: str,
        body: ConversationPatchBody,
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, object]:
        secret = provided(x_termx_passcode, authorization, k)
        _require_scope(state, secret, "agent-control")
        updates = {key: value for key, value in body.model_dump().items() if value is not None}
        conversation = state.agent_store.update_conversation(conversation_id, **updates)
        if conversation is None:
            raise HTTPException(status_code=404, detail="conversation not found")
        return {"conversation": conversation}

    @app.delete("/api/conversations/{conversation_id}")
    def delete_conversation(
        conversation_id: str,
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, object]:
        secret = provided(x_termx_passcode, authorization, k)
        _require_scope(state, secret, "agent-control")
        if not state.agent_store.delete_conversation(conversation_id):
            raise HTTPException(status_code=404, detail="conversation not found")
        return {"deleted": conversation_id}

    @app.post("/api/conversations/{conversation_id}/turns")
    def add_conversation_turn(
        conversation_id: str,
        body: ConversationTurnBody,
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, object]:
        secret = provided(x_termx_passcode, authorization, k)
        _require_scope(state, secret, "agent-control")
        try:
            turn = state.agent_store.add_conversation_turn(
                conversation_id,
                prompt=body.prompt,
                task_id=body.task_id,
                mode=body.mode,
                provider_id=body.provider_id,
                model=body.model,
                context_refs=body.context_refs,
                attachment_refs=body.attachment_refs,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="conversation not found") from exc
        return {"turn": turn}

    # Host-persisted custom agents (PROD-002) -------------------------------

    @app.get("/api/custom-agents")
    def list_custom_agents(
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, object]:
        secret = provided(x_termx_passcode, authorization, k)
        _require_scope(state, secret, "agent-view")
        return {"agents": state.agent_store.list_custom_agents()}

    @app.post("/api/custom-agents")
    def create_custom_agent(
        body: CustomAgentBody,
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, object]:
        secret = provided(x_termx_passcode, authorization, k)
        _require_scope(state, secret, "agent-control")
        try:
            agent = state.agent_store.create_custom_agent(**body.model_dump())
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"agent": agent}

    @app.patch("/api/custom-agents/{agent_id}")
    def patch_custom_agent(
        agent_id: str,
        body: CustomAgentPatchBody,
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, object]:
        secret = provided(x_termx_passcode, authorization, k)
        _require_scope(state, secret, "agent-control")
        updates = {key: value for key, value in body.model_dump().items() if value is not None}
        try:
            agent = state.agent_store.update_custom_agent(agent_id, **updates)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if agent is None:
            raise HTTPException(status_code=404, detail="custom agent not found")
        return {"agent": agent}

    @app.delete("/api/custom-agents/{agent_id}")
    def delete_custom_agent(
        agent_id: str,
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, object]:
        secret = provided(x_termx_passcode, authorization, k)
        _require_scope(state, secret, "agent-control")
        if not state.agent_store.delete_custom_agent(agent_id):
            raise HTTPException(status_code=404, detail="custom agent not found")
        return {"deleted": agent_id}

    @app.get("/api/agent/policies")
    def list_agent_policies(
        scope_type: str | None = Query(default=None),
        scope_id: str | None = Query(default=None),
        effect: str | None = Query(default=None),
        include_revoked: bool = Query(default=False),
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, object]:
        from termx.agent.policies.models import rule_public

        _require_scope(state, provided(x_termx_passcode, authorization, k), "agent-view")
        rules = state.agent_store.list_policy_rules(
            scope_type=scope_type,
            scope_id=scope_id,
            effect=effect,
            include_revoked=include_revoked,
        )
        return {"rules": [rule_public(rule) for rule in rules]}

    @app.post("/api/agent/policies")
    def create_agent_policy(
        body: PolicyRuleBody,
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, object]:
        from termx.agent.policies.models import rule_public

        _require_scope(state, provided(x_termx_passcode, authorization, k), "agent-control")
        try:
            rule = state.agent_store.create_policy_rule(**body.model_dump())
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"rule": rule_public(rule)}

    @app.get("/api/agent/policies/effective")
    def effective_agent_policies(
        project_id: str | None = Query(default=None),
        custom_agent_id: str | None = Query(default=None),
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, object]:
        from termx.agent.policies.models import rule_public

        _require_scope(state, provided(x_termx_passcode, authorization, k), "agent-view")
        scopes: list[tuple[str, str]] = [("host", "")]
        if custom_agent_id:
            scopes.append(("custom_agent", custom_agent_id))
        if project_id:
            scopes.append(("project", project_id))
        rules = state.agent_store.list_policy_rules(limit=1000)
        now = time()
        matched = [
            rule
            for rule in rules
            # "Effective" mirrors matching semantics: expired rules stay in the
            # ordinary list for audit but never apply here.
            if (rule["expires_at"] is None or rule["expires_at"] > now)
            and (
                rule["scope_type"] == "host"
                or any(
                    rule["scope_type"] == scope_type and rule["scope_id"] == scope_id
                    for scope_type, scope_id in scopes
                )
            )
        ]
        custom = None
        if custom_agent_id:
            custom = state.agent_store.get_custom_agent(custom_agent_id)
        from termx.sandbox import runner_for

        profile = str((custom or {}).get("sandbox_profile") or "agent")
        return {
            "rules": [rule_public(rule) for rule in matched],
            "approval_mode": str((custom or {}).get("approval_mode") or "standard"),
            "sandbox_profile": profile,
            "sandbox_capabilities": sorted(
                runner_for(profile).capabilities().granted
            ),
        }

    @app.patch("/api/agent/policies/{rule_id}")
    def patch_agent_policy(
        rule_id: str,
        body: PolicyRulePatchBody,
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, object]:
        from termx.agent.policies.models import rule_public

        _require_scope(state, provided(x_termx_passcode, authorization, k), "agent-control")
        updates = {key: value for key, value in body.model_dump().items() if value is not None}
        try:
            rule = state.agent_store.update_policy_rule(rule_id, **updates)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="policy rule not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"rule": rule_public(rule)}

    @app.delete("/api/agent/policies/{rule_id}")
    def revoke_agent_policy(
        rule_id: str,
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, object]:
        _require_scope(state, provided(x_termx_passcode, authorization, k), "agent-control")
        try:
            state.agent_store.revoke_policy_rule(rule_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="policy rule not found") from exc
        return {"revoked": rule_id}

    @app.get("/api/preferences")
    def get_preferences(
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, object]:
        _require_scope(state, provided(x_termx_passcode, authorization, k), "machine-view")
        prefs = state.store.get().terminal
        return {"shell": prefs.shell, "cwd": prefs.cwd, "shells": machine_snapshot(state.store)["shells"]}

    @app.put("/api/preferences")
    def put_preferences(
        body: PreferencesBody,
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, object]:
        _require_scope(state, provided(x_termx_passcode, authorization, k), "host-admin")
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
        _require_scope(state, provided(x_termx_passcode, authorization, k), "machine-view")
        return {"commands": [item.public() for item in state.store.list_commands()]}

    @app.post("/api/commands")
    def create_command(
        body: CommandBody,
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, object]:
        _require_scope(state, provided(x_termx_passcode, authorization, k), "host-admin")
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
        _require_scope(state, provided(x_termx_passcode, authorization, k), "host-admin")
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
        _require_scope(state, provided(x_termx_passcode, authorization, k), "host-admin")
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
        _require_scope(state, provided(x_termx_passcode, authorization, k), "host-admin")
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
        _require_scope(state, provided(x_termx_passcode, authorization, k), "files-read")
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
        _require_scope(state, provided(x_termx_passcode, authorization, k), "files-read")
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
        _require_scope(state, provided(x_termx_passcode, authorization, k), "files-write")
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
        _require_scope(state, provided(x_termx_passcode, authorization, k), "files-read")
        return {"projects": state.projects.projects()}

    @app.post("/api/projects")
    def register_project(
        body: ProjectBody,
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, object]:
        _require_scope(state, provided(x_termx_passcode, authorization, k), "files-write")
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
        _require_scope(state, provided(x_termx_passcode, authorization, k), "files-write")
        return state.projects.update_project(project_id, body.name)

    @app.delete("/api/projects/{project_id}")
    def forget_project(
        project_id: str,
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, bool]:
        _require_scope(state, provided(x_termx_passcode, authorization, k), "files-write")
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
        _require_scope(state, provided(x_termx_passcode, authorization, k), "files-read")
        return state.projects.listing(project_id, path, offset, limit)

    @app.get("/api/projects/{project_id}/file")
    def project_file(
        project_id: str,
        path: str = Query(...),
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, object]:
        _require_scope(state, provided(x_termx_passcode, authorization, k), "files-read")
        return state.projects.read(project_id, path)

    @app.put("/api/projects/{project_id}/file")
    def project_file_save(
        project_id: str,
        body: FileSaveBody,
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, object]:
        _require_scope(state, provided(x_termx_passcode, authorization, k), "files-write")
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
        _require_scope(state, provided(x_termx_passcode, authorization, k), "files-read")
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
        _require_scope(state, provided(x_termx_passcode, authorization, k), "files-write")
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
        _require_scope(state, provided(x_termx_passcode, authorization, k), "terminal-control")
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
        _require_scope(state, provided(x_termx_passcode, authorization, k), "files-read")
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
        _require_scope(state, provided(x_termx_passcode, authorization, k), "git-read")
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
        _require_scope(state, provided(x_termx_passcode, authorization, k), "git-read")
        return git_ops.diff(_project_root(project_id), path, staged=bool(staged))

    @app.post("/api/projects/{project_id}/git/stage")
    def git_stage(
        project_id: str,
        body: GitStageBody,
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, object]:
        _require_scope(state, provided(x_termx_passcode, authorization, k), "git-write")
        return git_ops.stage(_project_root(project_id), body.paths, body.stage)

    @app.post("/api/projects/{project_id}/git/hunk")
    def git_hunk(
        project_id: str,
        body: GitHunkBody,
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, object]:
        _require_scope(state, provided(x_termx_passcode, authorization, k), "git-write")
        return git_ops.stage_hunk(_project_root(project_id), body.patch, body.stage)

    @app.post("/api/projects/{project_id}/git/commit")
    def git_commit(
        project_id: str,
        body: GitCommitBody,
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, object]:
        _require_scope(state, provided(x_termx_passcode, authorization, k), "git-write")
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
        _require_scope(state, provided(x_termx_passcode, authorization, k), "git-read")
        return git_ops.branches(_project_root(project_id))

    @app.post("/api/projects/{project_id}/git/branch")
    def git_branch(
        project_id: str,
        body: GitBranchBody,
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, object]:
        _require_scope(state, provided(x_termx_passcode, authorization, k), "git-write")
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
        _require_scope(state, provided(x_termx_passcode, authorization, k), "git-write")
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
        _require_scope(state, provided(x_termx_passcode, authorization, k), "files-read")
        return {"previews": state.projects.previews(project_id)}

    @app.post("/api/projects/{project_id}/previews")
    def create_project_preview(
        project_id: str,
        body: PreviewBody,
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, object]:
        _require_scope(state, provided(x_termx_passcode, authorization, k), "files-write")
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
        _require_scope(state, provided(x_termx_passcode, authorization, k), "files-write")
        state.projects.delete_preview(project_id, preview_id)
        return {"ok": True}

    @app.get("/api/projects/{project_id}/lsp")
    def project_lsp_servers(
        project_id: str,
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, object]:
        _require_scope(state, provided(x_termx_passcode, authorization, k), "files-read")
        state.projects.project(project_id)
        return {"servers": lsp.server_snapshot()}

    @app.get("/api/directories")
    def list_directories(
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, object]:
        _require_scope(state, provided(x_termx_passcode, authorization, k), "machine-view")
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
        _require_scope(state, provided(x_termx_passcode, authorization, k), "host-admin")
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
        _require_scope(state, provided(x_termx_passcode, authorization, k), "host-admin")
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
        _require_scope(state, provided(x_termx_passcode, authorization, k), "host-admin")
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
        _require_scope(state, provided(x_termx_passcode, authorization, k), "network-manage")
        return state.tunnels.runtime()

    @app.post("/api/tunnels/profiles")
    def create_tunnel_profile(
        body: TunnelProfileBody,
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, object]:
        _require_scope(state, provided(x_termx_passcode, authorization, k), "network-manage")
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
        _require_scope(state, provided(x_termx_passcode, authorization, k), "network-manage")
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
        _require_scope(state, provided(x_termx_passcode, authorization, k), "network-manage")
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
        _require_scope(state, provided(x_termx_passcode, authorization, k), "network-manage")
        status = await state.tunnels.stop()
        log_event("tunnel_stop", state=status.state)
        return status.public()

    @app.post("/api/tunnels/restart")
    async def restart_tunnel(
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, object]:
        _require_scope(state, provided(x_termx_passcode, authorization, k), "network-manage")
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
        _require_scope(state, provided(x_termx_passcode, authorization, k), "desktop-view")
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
        _require_scope(state, provided(x_termx_passcode, authorization, k), "desktop-control")
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
        _require_scope(state, provided(x_termx_passcode, authorization, k), "desktop-control")
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
        _require_scope(state, provided(x_termx_passcode, authorization, k), "desktop-view")
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
        _require_scope(state, provided(x_termx_passcode, authorization, k), "desktop-view")
        state.rtc.add_ice(body.session_id, body.candidate)
        return {"ok": True}

    @app.get("/api/workspace")
    def get_workspace(
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, object]:
        _require_scope(state, provided(x_termx_passcode, authorization, k), "machine-view")
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
        _require_scope(state, provided(x_termx_passcode, authorization, k), "host-admin")
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
        _require_scope(state, provided(x_termx_passcode, authorization, k), "host-admin")
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
        _require_scope(state, provided(x_termx_passcode, authorization, k), "network-manage")
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
        _require_scope(state, provided(x_termx_passcode, authorization, k), "network-manage")
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
        _require_scope(state, provided(x_termx_passcode, authorization, k), "network-manage")
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
        _require_scope(state, provided(x_termx_passcode, authorization, k), "network-manage")
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
        _require_scope(state, provided(x_termx_passcode, authorization, k), "network-manage")
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
        _require_scope(state, provided(x_termx_passcode, authorization, k), "network-manage")
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
        _require_scope(state, provided(x_termx_passcode, authorization, k), "terminal-view")
        return {"sessions": [s.snapshot() for s in state.sessions.list()]}

    @app.post("/api/sessions")
    async def create_session(
        body: CreateSessionBody | None = None,
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, object]:
        _require_scope(state, provided(x_termx_passcode, authorization, k), "terminal-control")
        body = body or CreateSessionBody()
        prefs = state.store.get().terminal
        try:
            shell = validate_shell(body.shell or prefs.shell)
            cwd = validate_cwd(body.cwd or prefs.cwd)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        try:
            session = state.sessions.create(
                cols=body.cols,
                rows=body.rows,
                title=body.title,
                argv=default_argv(shell),
                cwd=cwd,
                shell=shell,
                sandbox_profile=body.sandbox_profile,
            )
        except TerminalError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return session.snapshot()

    @app.patch("/api/sessions/{session_id}")
    def rename_session(
        session_id: str,
        body: RenameSessionBody,
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> dict[str, object]:
        _require_scope(state, provided(x_termx_passcode, authorization, k), "terminal-control")
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
        _require_scope(state, provided(x_termx_passcode, authorization, k), "terminal-control")
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
        if not state.auth.allows(token, "files-read"):
            await websocket.close(code=4403)
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
        if not state.auth.allows(token, "terminal-control"):
            await websocket.close(code=4403)
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
        if not state.auth.allows(token, "desktop-view"):
            await websocket.close(code=4403)
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
