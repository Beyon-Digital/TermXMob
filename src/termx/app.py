from __future__ import annotations

import asyncio
import json
import os
import re
import tempfile
import threading
from urllib.parse import unquote
from contextlib import asynccontextmanager
from pathlib import Path
from time import time
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response, JSONResponse


from termx.agent.manager import AgentManager, AdapterFactory
from termx.agent.secrets import CredentialStore
from termx.agent.store import AgentStore
from termx.audit import log_event
from termx.auth import Auth, extract_passcode
from termx.identity import AuthenticationService
from termx.config import (
    config_dir,
    ConfigStore,
    validate_cwd,
    validate_shell,
)
from termx.desktop.session import DesktopManager
from termx.agents.registry import AgentRegistry
from termx.desktop.webrtc import RtcManager
from termx.engines.claude import ClaudeEngine
from termx.engines.codex import CodexEngine
from termx.engines.gateway import EngineGateway
from termx.engines.acp_registry import AcpRegistry
from termx.forwards import ForwardManager
from termx.lifecycle import shutdown_state
from termx.mcp.client import McpPool, McpConnectionError
from termx.mcp.gateway import GatewayAuthError, McpGateway
from termx.mcp.oauth import LoopbackCallback
from termx.mcp.registry import ConnectionRegistry
from termx.net import connect_url, http_urls, qr_svg
from termx.notifications import NotificationCenter
from termx.project_files import ProjectFiles
from termx import lsp
from termx.sessions import SessionManager
from termx.tokens import TokenStore
from termx.tunnels import TunnelManager


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
        identity: AuthenticationService | None = None,
    ) -> None:
        self.tokens = TokenStore()
        self.identity = identity or AuthenticationService()
        if identity is None and not self.identity.has_runtime_configuration and os.environ.get("TERMX_AUTH_ADAPTERS_FILE"):
            from termx.identity_adapters import load_configured_adapters
            load_configured_adapters(self.identity, os.environ["TERMX_AUTH_ADAPTERS_FILE"])
        self.auth = Auth(passcode, token_store=self.tokens, identity=self.identity)
        from termx.authorization import AuthorizationService
        self.authorization = AuthorizationService(self.identity, self.auth)
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
            settings=lambda: self.store.get().agent,
        )
        from termx.agent.chatgpt import ChatGPTAccounts
        self.chatgpt = ChatGPTAccounts(self.credentials, self.agent_store)
        self.agent.chatgpt = self.chatgpt
        self.engines = EngineGateway(self.agent_store, self.agent.emit_external)
        self.engines.settings = lambda: self.store.get().agent
        self.engines.acp_registry = AcpRegistry(self.engines)
        # Carry forward installations saved by the earlier vendor-specific
        # ACP implementation into the registry-managed generic adapter.
        agent_prefs = self.store.get().agent
        legacy_registry_ids = {"devin": "devin", "grok": "grok-build", "antigravity": "antigravity-acp"}
        migrated = {name: dict(config) for name, config in agent_prefs.acp_runners.items()}
        changed = False
        for engine_id, registry_id in legacy_registry_ids.items():
            if engine_id in migrated and not migrated[engine_id].get("registry_id"):
                migrated[engine_id]["registry_id"] = registry_id
                changed = True
        if changed:
            agent_prefs = self.store.update_agent({"acp_runners": migrated})
        for adapter in (
            CodexEngine,
            ClaudeEngine,
        ):
            self.engines.register(
                adapter(
                    event_sink=self.engines.on_engine_event,
                    approval_sink=self.engines.approval_sink,
                    executable_override=self.store.get().agent.engines.get(adapter.id, {}).get("executable"),
                )
            )
        self.engines.register_configured_runners(agent_prefs)
        self.engines.acp_registry.load_cached()
        # ~/.agents root (overridable for tests/packaging) and the
        # file-authoritative custom-agent registry.
        self.agents_root = os.path.abspath(
            os.path.expanduser(os.environ.get("TERMX_AGENTS_DIR", "~/.agents"))
        )
        self.agent_registry = AgentRegistry(
            self.agent_store,
            agents_dir=os.path.join(self.agents_root, "agents"),
        )
        # MCP: connection defs + pooled clients + session-scoped gateway.
        self.mcp_loopback = LoopbackCallback()
        self.mcp_pool = McpPool(self.credentials)
        self.mcp_gateway = McpGateway(self.mcp_pool)
        self.mcp_auth_urls: dict[str, str] = {}
        # Engine sessions resolve agent mcp_connections through the registry;
        # secret env values come from the credential store at spawn time.
        self.engines.mcp_resolver = lambda conn_id: self.mcp_registry().get(conn_id)
        self.engines.credential_lookup = self.credentials.get
        self.notifications = NotificationCenter()
        self.notifications.bridge_agent(self.agent, self.agent_store)
        self.port = port
        self.request_shutdown = None
        from termx.development.debug import DebugService
        from termx.development.delivery import DeliveryService
        self.debug = DebugService()
        self.delivery = DeliveryService(config_dir() / "workspace-delivery")

    def mcp_registry(self) -> ConnectionRegistry:
        dirs = [os.path.join(self.agents_root, "mcp")]
        for p in self.projects.projects():
            if p.get("path"):
                dirs.append(os.path.join(p["path"], ".agents", "mcp"))
        return ConnectionRegistry(dirs)

    def extension_scan(self) -> tuple[dict[str, Any], dict[str, Any]]:
        """Fresh bounded scan of all roots with enable/trust overlay applied."""
        from termx.discovery.index import DiscoveryIndex
        from termx.discovery.roots import default_roots

        projects = {
            p["id"]: p["path"] for p in self.projects.projects() if p.get("path")
        }
        roots = default_roots(projects, user_agents_dir=self.agents_root)
        if getattr(self, "extensions", None):
            from termx.discovery.roots import ScanRoot
            roots.extend(ScanRoot(path=path, source="bundled", trusted=True,
                                  label="Installed workspace extension") for path in self.extensions.active_roots())
        entries, report = DiscoveryIndex(roots).scan()
        states = self.agent_store.all_extension_states()
        out: dict[str, Any] = {}
        for qid, entry in entries.items():
            st = states.get(qid, {})
            entry.enabled = bool(st.get("enabled", entry.source == "bundled"))
            if st.get("trusted"):
                entry.trusted = True
            out[qid] = entry.as_dict()
        return out, report.as_dict()








































































































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
        state.engines.recover()
        if os.environ.get("TERMX_ENGINE_STARTUP_REFRESH", "1") != "0":
            state.engines.catalogue.start()
            async def refresh_engine_catalogues() -> None:
                await state.engines.acp_registry.refresh()
                unscanned = [engine for engine in state.engines._registry_runner_ids
                             if engine not in state.engines.catalogue.entries]
                if unscanned:
                    await asyncio.gather(*(state.engines.catalogue.refresh(engine) for engine in unscanned))
            asyncio.create_task(refresh_engine_catalogues())
        # File authority for custom agents: one-time DB->file export for
        # device-era rows, then files->DB projection sync. Bounded + safe.
        try:
            await asyncio.to_thread(state.agent_registry.sync)
        except Exception:  # noqa: BLE001 - never block startup on sync
            log_event("agent_registry_sync_error")
        for task_id in state.agent.pending_recoveries():
            asyncio.create_task(state.agent.recover_task(task_id))
        state.automation.start()
        state.runners.start()
        state.previews.start()
        await state.runner_agents.startup_reconcile()
        yield
        await state.automation.close()
        await state.runner_agents.close()
        await state.runners.close()
        await state.previews.close()
        state.window_recording.close()
        await state.browser.close()
        await state.workspace.close()
        state.forwards.stop_all()
        await state.chatgpt.close()
        await state.mcp_pool.shutdown()
        await state.mcp_loopback.stop()
        await state.engines.shutdown()
        await shutdown_state(state)

    app = FastAPI(title="termx", docs_url=None, redoc_url=None, lifespan=lifespan)
    app.state.termx = state
    from termx.identity_guard import SessionGuard
    from termx.identity_http import mount_identity

    app.add_middleware(SessionGuard, state=state)
    mount_identity(app, state)
    from termx.authorization_http import mount_authorization
    from termx.development.http import mount_development
    mount_authorization(app, state)
    mount_development(app, state)
    from termx.workspace import mount_workspace
    mount_workspace(app, state)
    from termx.media.http import mount_media
    mount_media(app, state)
    from termx.runners.http import mount_runners
    mount_runners(app, state)
    from termx.runners.agent_http import mount_runner_agents
    mount_runner_agents(app, state)
    from termx.browser.service import BrowserService
    from termx.browser.router import browser_router

    def browser_session_valid(principal_id, session_id, policy_version):
        with state.identity._db() as db:
            row = db.execute("SELECT * FROM sessions WHERE id=? AND principal_id=?", (session_id, principal_id)).fetchone()
            principal = state.identity._live_principal(db, row)
            return principal is not None and principal.policy_version == policy_version

    state.browser = BrowserService(config_dir() / "browser", session_valid=browser_session_valid)
    state.agent.browser = state.browser
    state.engines.set_browser_service(state.browser)
    app.include_router(browser_router(state))
    from termx.desktop.recording_http import mount_window_recording
    mount_window_recording(app, state)
    from termx.development.preview import mount_previews
    mount_previews(app, state)
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

    # The app-facing API surface is GraphQL (queries/mutations over HTTP,
    # subscriptions over graphql-ws). Only binary transfers, the external MCP
    # gateway, and the duplex byte channels keep dedicated routes below.
    from termx.graphql.router import mount as mount_graphql

    mount_graphql(app, state)

    def provided(
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> str | None:
        return extract_passcode(x_termx_passcode, authorization, k)


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


    @app.get("/api/connect/qr.svg")
    def connect_qr(
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> Response:
        _require_scope(state, provided(x_termx_passcode, authorization, k), "host-admin")
        target, _tunnel = _connect_target()
        svg = qr_svg(connect_url(target, None if state.identity.configured else state.auth.passcode))
        return Response(content=svg, media_type="image/svg+xml", headers={"Cache-Control": "no-store"})















    # Agent -----------------------------------------------------------





    # Native engine integrations (engine-extensions P1) -------------------















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
        state.authorization.require(secret, "agent-view", resource_kind="task", resource_id=task_id)
        artifact = state.agent_store.get_artifact(task_id, artifact_id)
        if artifact is None:
            raise HTTPException(status_code=404, detail="artifact not found")
        suffix = {"image/jpeg": "jpg", "image/png": "png", "image/gif": "gif", "image/webp": "webp"}.get(artifact["mime"], "bin")
        return FileResponse(artifact["path"], media_type=artifact["mime"], filename=f"{artifact_id}.{suffix}")



    # ------------------------------------------------------------------
    # Activity Center (PROD-004): normalized Termx-owned activity.



    # ------------------------------------------------------------------
    # Port + process discovery (PROD-005).




    # ------------------------------------------------------------------
    # Runbooks (PROD-006): named multi-step workflows + run history.













    # Host-persisted conversations (PROD-001) -------------------------------







    # Host-persisted custom agents (PROD-002) -------------------------------











    # Extension catalog -------------------------------------------------





    # MCP connections ---------------------------------------------------









    # Session-scoped MCP gateway (capability-token auth, loopback only) --

    def _require_loopback(request: Request) -> None:
        host = (request.client.host if request.client else "") or ""
        if host not in {"127.0.0.1", "::1", "localhost"}:
            raise HTTPException(status_code=403, detail="gateway is loopback-only")

    @app.get("/api/mcp-gw/{token}/catalog")
    async def gw_catalog(token: str, request: Request) -> dict[str, object]:
        _require_loopback(request)
        try:
            return {"connections": await state.mcp_gateway.catalog(token)}
        except GatewayAuthError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

    @app.post("/api/mcp-gw/{token}/call")
    async def gw_call(token: str, request: Request, body: dict[str, Any]) -> dict[str, object]:
        _require_loopback(request)
        try:
            result = await state.mcp_gateway.call_tool(
                token,
                str(body.get("connection") or ""),
                str(body.get("tool") or ""),
                dict(body.get("arguments") or {}),
            )
        except GatewayAuthError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except McpConnectionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"result": result}














    @app.get("/api/fs/download")
    def download_file(
        path: str = Query(...),
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> FileResponse:
        _require_scope(state, provided(x_termx_passcode, authorization, k), "files-read")
        state.authorization.require_path(provided(x_termx_passcode, authorization, k), "files-read", path, state.projects.projects())
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
        state.authorization.require_path(provided(x_termx_passcode, authorization, k), "files-write", dir, state.projects.projects())
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







    @app.get("/api/projects/{project_id}/download")
    def project_file_download(
        project_id: str,
        path: str = Query(...),
        x_termx_passcode: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        k: str | None = Query(default=None),
    ) -> FileResponse:
        _require_scope(state, provided(x_termx_passcode, authorization, k), "files-read")
        state.authorization.require(provided(x_termx_passcode, authorization, k), "files-read", project_id=project_id)
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
        state.authorization.require(provided(x_termx_passcode, authorization, k), "files-write", project_id=project_id)
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



    # Git ------------------------------------------------------------
    def _project_root(project_id: str) -> str:
        return state.projects.project(project_id)["path"]









































    @app.websocket("/api/projects/{project_id}/lsp/{language}")
    async def project_lsp_socket(
        websocket: WebSocket,
        project_id: str,
        language: str,
        k: str | None = None,
        workspace_session: str | None = None,
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
            state.authorization.require(token, "agent-run", project_id=project_id)
            state.authorization.require(token, "files-read", project_id=project_id)
        except HTTPException:
            await websocket.close(code=4403)
            return
        try:
            root = state.projects.project(project_id)["path"]
            if workspace_session:
                origin=state.identity.resolve(token)
                if not origin:
                    raise HTTPException(401,'Managed workspace session required')
                selected=state.workspace.record(origin.principal,'conversation',workspace_session,scope='files-read')
                if selected.get('project_id')!=project_id or selected.get('runner_id'):
                    raise HTTPException(403,'Language server target must be the selected local execution project')
                state.workspace.record(origin.principal,'conversation',workspace_session,scope='agent-run')
                root=selected['cwd']
        except (HTTPException,PermissionError,KeyError,ValueError,OSError):
            await websocket.close(code=4404)
            return
        def authorize_language_message():
            state.authorization.require(token,'agent-run',project_id=project_id)
            state.authorization.require(token,'files-read',project_id=project_id)
            if workspace_session:
                current=state.identity.resolve(token)
                if not current:raise PermissionError('Managed session revoked')
                target=state.workspace.record(current.principal,'conversation',workspace_session,scope='files-read')
                if target.get('project_id')!=project_id or target.get('runner_id') or Path(target['cwd']).resolve()!=Path(root).resolve():
                    raise PermissionError('Language checkout changed; reconnect for the new target')
        await lsp.serve(websocket, str(root), language,authorize=authorize_language_message)

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
        try:
            state.authorization.require(token, "terminal-control", resource_kind="terminal", resource_id=session_id)
        except HTTPException:
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
        try:
            state.authorization.require(token, "desktop-view")
        except HTTPException:
            await websocket.close(code=4403)
            return
        await state.desktop.attach(websocket, authorize_control=lambda: state.authorization.can(token, "desktop-control"))

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
    from termx.workspace.ui_contract import compatibility, unavailable

    @app.get('/workspace-version.json')
    def workspace_version():
        return JSONResponse(compatibility(), headers=html_headers)

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
    if dist is None:
        checkout_bundle = Path(__file__).resolve().parents[2] / 'desktop' / 'workspace' / 'dist'
        if checkout_bundle.is_dir():
            dist = checkout_bundle
    index_html = (dist / "index.html") if dist else None
    compatible_bundle = bool(index_html and index_html.is_file() and re.search(
        r'<meta\s+name=["\']termx-ui-contract["\']\s+content=["\']3["\']', index_html.read_text()))

    @app.get("/")
    def root():
        if compatible_bundle:
            return FileResponse(index_html, headers=html_headers)
        return unavailable()

    if dist is not None and compatible_bundle:

        @app.get("/{path:path}")
        def spa(path: str) -> FileResponse:
            if path.startswith(("api/", "_/", "auth/", "graphql")):
                raise HTTPException(status_code=404)
            target = (dist / path).resolve()
            try:
                target.relative_to(dist.resolve())
            except ValueError:
                raise HTTPException(status_code=404) from None
            if target.is_file():
                return FileResponse(target)
            if index_html is not None and index_html.is_file():
                return FileResponse(index_html, headers=html_headers)
            return unavailable()
    else:

        @app.get("/{path:path}")
        def spa_fallback(path: str):
            if path.startswith(("api/", "_/", "auth/", "graphql")):
                raise HTTPException(status_code=404)
            return unavailable()

    return app
