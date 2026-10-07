"""Codex adapter — `codex app-server` (stdio JSONL, JSON-RPC sans jsonrpc field).

Docs: https://developers.openai.com/codex/app-server — threads/turns/items,
server→client approvals, account/* auth surface. One app-server process hosts
all Codex threads (matching the VS Code extension's model); a process crash
marks every live binding `lost` — threads persist on disk and re-attach via
`thread/resume`.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from .. import __version__ as TERMX_VERSION
from .env import probe_version, resolve_executable
from .jsonrpc import JsonlProcess, JsonRpcError, TransportClosed, platform_spawn_env
from .types import (
    AUTH_AUTHENTICATED,
    AUTH_UNAUTHENTICATED,
    AUTH_UNKNOWN,
    EffectiveRunConfiguration,
    EngineCapabilities,
    EngineDescriptor,
    EngineEvent,
    EngineSessionBinding,
    SESSION_LOST,
)

MAX_RAW_EVENT_BYTES = 8 * 1024

EventSink = Callable[[str, EngineEvent], None]
# (binding_id, request_token, wire_method, params, approval_kind) -> TermX approval id
ApprovalSink = Callable[[str, str, str, dict[str, Any], str], Awaitable[str]]

_ITEM_EVENTS = {
    "item/started": "engine.item.started",
    "item/completed": "engine.item.completed",
    "item/agentMessage/delta": "engine.message.delta",
    "item/plan/delta": "engine.plan.delta",
    "item/reasoning/summaryTextDelta": "engine.reasoning.delta",
    "item/reasoning/textDelta": "engine.reasoning.delta",
    "item/commandExecution/outputDelta": "engine.command.output",
}


def _bounded(payload: Any) -> dict[str, Any]:
    """Fit a notification payload into the raw-event bound."""
    data = payload if isinstance(payload, dict) else {"value": payload}
    if len(json.dumps(data, default=str)) <= MAX_RAW_EVENT_BYTES:
        return data
    return {"truncated": True, "preview": json.dumps(data, default=str)[:MAX_RAW_EVENT_BYTES]}


class CodexEngine:
    id = "codex"
    label = "Codex"

    def __init__(
        self,
        *,
        executable_override: str | None = None,
        event_sink: EventSink | None = None,
        approval_sink: ApprovalSink | None = None,
        spawn_env: dict[str, str] | None = None,
    ) -> None:
        self._override = executable_override
        self._executable: str | None = None
        self._version: str | None = None
        self._conn: JsonlProcess | None = None
        self._conn_lock = asyncio.Lock()
        self._event_sink = event_sink or (lambda _b, _e: None)
        self._approval_sink = approval_sink
        self._spawn_env = spawn_env
        # thread_id -> binding_id
        self._threads: dict[str, str] = {}
        self._bindings: dict[str, EngineSessionBinding] = {}
        # request token -> (future answered with wire result, wire method, params)
        self._pending_decisions: dict[str, tuple[asyncio.Future[Any], str, dict[str, Any]]] = {}
        self._initialized = False
        self._account: dict[str, Any] = {}
        self._models: list[str] = []
        self._model_rows: list[dict[str,Any]] = []
        self.browser_service = None
        self._review_items = {}

    # ------------------------------------------------------------------ probe

    def descriptor(self) -> EngineDescriptor:
        return EngineDescriptor(
            id=self.id,
            label=self.label,
            installed=self._executable is not None,
            executable=self._executable,
            version=self._version,
            auth_state=self._auth_state(),
            auth_detail=self._auth_detail(),
            location="local-process",
            transport="stdio-jsonl",
            protocol={"name": "codex-app-server", "schema": "jsonl-rpc"},
            error=None,
        )

    def _auth_state(self) -> str:
        if not self._account:
            return AUTH_UNKNOWN
        if self._account.get("account"):
            return AUTH_AUTHENTICATED
        return AUTH_UNAUTHENTICATED if self._account.get("requiresOpenaiAuth") else AUTH_UNKNOWN

    def _auth_detail(self) -> str:
        acct = self._account.get("account")
        if isinstance(acct, dict):
            kind = acct.get("type", "?")
            plan = acct.get("planType")
            return f"{kind}" + (f"/{plan}" if plan else "")
        return ""

    async def probe(self) -> EngineDescriptor:
        desc = self.descriptor()
        self._executable = resolve_executable("codex", override=self._override)
        desc.installed = self._executable is not None
        desc.executable = self._executable
        if not self._executable:
            desc.error = "codex executable not found (checked PATH + known install dirs)"
            desc.auth_state = AUTH_UNKNOWN
            return desc
        self._version = await probe_version(self._executable, ["--version"])
        desc.version = self._version
        try:
            await self._ensure_conn()
            self._account = await self._conn.request("account/read", {"refreshToken": False}, timeout=15) or {}
            models = await self._conn.request("model/list", {"limit": 20, "includeHidden": False}, timeout=15)
            self._model_rows = [m for m in (models or {}).get("data", []) if isinstance(m,dict) and m.get("id")]
            self._models = [m["id"] for m in self._model_rows]
        except (TransportClosed, JsonRpcError, TimeoutError, OSError) as exc:
            desc.error = f"probe failed: {exc}"
        desc.auth_state = self._auth_state()
        desc.auth_detail = self._auth_detail()
        desc.version = self._version
        return desc

    def capabilities(self) -> EngineCapabilities:
        return EngineCapabilities(
            tools_filter="advisory",
            approvals="native",
            streaming=True,
            resume="supported",
            steer="supported",
            fork="supported",
            subagents="unverified",
            skills_native="supported",  # `{type:"skill"}` turn input items
            mcp_native="supported",     # codex config MCP; per-session TBD
            models=list(self._models),
            notes={
                "attachments": "image data URLs via the native turn/start input contract; model entitlement applies",
                "tools_filter": "tool-name filtering is advisory only; enforcement via approvalPolicy + sandboxPolicy",
                "auto_review": "managed tasks: command/file/permission approval requests use durable host broker; native sandbox fast paths remain native policy, not per-tool host hooks",
                "restricted_browser": "refused: no verified mechanism removes every alternate native tool",
            },
        )

    async def discover_catalogue(self, cwd=None):
        # Values come from this installed app-server model/list response, never
        # a hard-coded model name or another provider's effort enumeration.
        variants={}
        default=None
        for row in self._model_rows:
            values=[{'value':item['reasoningEffort'],'name':item.get('description') or item['reasoningEffort']}
                    for item in row.get('supportedReasoningEfforts',[]) if isinstance(item,dict) and isinstance(item.get('reasoningEffort'),str)]
            option={'id':'reasoning_effort','name':'Reasoning','category':'thought_level','type':'select',
                    'currentValue':row.get('defaultReasoningEffort'),'options':values}
            variants[row['id']]={'config_options':[option] if values else [],'models':list(self._models)}
            if row.get('isDefault'):default=row['id']
        return {'models':list(self._models),'default_model':default,'model_configurations':variants,
                'config_options':variants.get(default,{}).get('config_options',[]),'source':'codex:model/list'}

    def _turn_configuration(self,cfg):
        if cfg.mode not in {None,'ask','agent'}:raise ValueError('Unsupported Codex conversation mode')
        if set(cfg.config_options)-{'reasoning_effort'}:raise ValueError('Unsupported Codex config option')
        selected=next((m for m in self._model_rows if m['id']==cfg.model or m.get('model')==cfg.model),None) if cfg.model else next((m for m in self._model_rows if m.get('isDefault')),None)
        effort=cfg.config_options.get('reasoning_effort')
        if effort is not None:
            values=[m.get('reasoningEffort') for m in (selected or {}).get('supportedReasoningEfforts',[])]
            if not isinstance(effort,str) or effort not in values:raise ValueError('Reasoning effort is not advertised for this Codex model; refresh the engine catalogue')
        if effort is None:effort=(selected or {}).get('defaultReasoningEffort')
        sandbox={'type':'readOnly'} if cfg.mode=='ask' or cfg.sandbox_profile=='read-only' else (
            {'type':'workspaceWrite','writableRoots':[cfg.cwd],'networkAccess':False,'excludeSlashTmp':True,'excludeTmpdirEnvVar':True}
            if cfg.sandbox_profile in {'agent','workspace'} else {'type':'dangerFullAccess'} if cfg.sandbox_profile=='host' else None)
        return {**({'model':(selected or {}).get('model') or cfg.model} if selected or cfg.model else {}),
                **({'effort':effort} if effort is not None else {}),
                **({'sandboxPolicy':sandbox} if sandbox else {})}

    # ------------------------------------------------------------- connection

    async def _ensure_conn(self) -> None:
        async with self._conn_lock:
            if self._conn and self._conn.running and self._initialized:
                return
            if self._conn:
                await self._conn.close()
            self._initialized = False
            self._conn = JsonlProcess(
                [self._executable or "codex", "app-server"],
                env=self._spawn_env or platform_spawn_env(),
                on_notification=self._on_notification,
                on_server_request=self._on_server_request,
                on_close=self._on_conn_lost,
            )
            await self._conn.start()
            try:
                await self._conn.request(
                    "initialize",
                    {
                        "clientInfo": {
                            "name": "termx",
                            "title": "TermX",
                            "version": TERMX_VERSION,
                        },
                    },
                    timeout=20,
                )
                await self._conn.notify("initialized", {})
            except Exception:
                await self._conn.close()
                raise
            self._initialized = True

    def _on_conn_lost(self) -> None:
        for binding in self._bindings.values():
            if binding.status not in ("closed",):
                binding.status = SESSION_LOST
                binding.updated_at = time.time()
                self._emit(binding, "engine.session.lost", {"reason": "app-server exited"})

    # ---------------------------------------------------------------- session

    async def create_session(self, cfg: EffectiveRunConfiguration) -> EngineSessionBinding:
        await self._ensure_conn()
        turn_configuration=self._turn_configuration(cfg)
        params: dict[str, Any] = {
            "cwd": cfg.cwd or None,
            "serviceName": "termx",
        }
        if cfg.model:
            params["model"] = cfg.model
        sandbox = "read-only" if cfg.mode=="ask" else _codex_sandbox(cfg.sandbox_profile)
        if sandbox:
            params["sandbox"] = sandbox
        approval = _codex_approval_policy(cfg.approval_mode)
        if approval:
            params["approvalPolicy"] = approval
        if cfg.tools.get('managed_task_id') and self.browser_service:
            params['approvalsReviewer'] = 'user'
            params['approvalPolicy'] = 'untrusted'
        result = await self._conn.request(
            "thread/start", {k: v for k, v in params.items() if v is not None}, timeout=30
        )
        thread = (result or {}).get("thread") or {}
        thread_id = thread.get("id")
        if not thread_id:
            raise JsonRpcError(-32000, "thread/start returned no thread id")
        binding = EngineSessionBinding.new(
            self.id,
            thread_id,
            cwd=cfg.cwd or "",
            profile_revision=cfg.agent_profile_revision,
            account_scope=self._auth_detail(),
            extensions_snapshot={
                "skills": [s.get("id") for s in cfg.skills],
                "managed_task_id": cfg.tools.get("managed_task_id"), "review_read_only": cfg.mode == "ask",
                "turn_configuration":turn_configuration,
                "mcp": [m.get("connection_id") for m in cfg.mcp_bindings],
            },
        )
        self._threads[thread_id] = binding.binding_id
        self._bindings[binding.binding_id] = binding
        return binding

    async def attach(self, binding: EngineSessionBinding, *, cfg=None) -> EngineSessionBinding:
        """Re-attach to a persisted thread after host restart (native resume)."""
        await self._ensure_conn()
        result = await self._conn.request(
            "thread/resume", {"threadId": binding.native_session_id}, timeout=30
        )
        thread = (result or {}).get("thread") or {}
        if not thread.get("id"):
            raise JsonRpcError(-32000, "thread/resume returned no thread")
        binding.status = "idle"
        binding.updated_at = time.time()
        self._threads[binding.native_session_id] = binding.binding_id
        self._bindings[binding.binding_id] = binding
        return binding

    async def configure_session(self, binding, cfg):
        if binding.status=="active":raise ValueError("Native turn is still running")
        binding.extensions_snapshot.update(managed_task_id=cfg.tools.get("managed_task_id"), review_read_only=cfg.mode == "ask",turn_configuration=self._turn_configuration(cfg))

    async def send(self, binding: EngineSessionBinding, prompt: str,
                   attachments: list[dict[str, Any]] | None = None) -> str | None:
        await self._ensure_conn()
        from termx.engines.attachments import image_attachments
        inputs: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        inputs.extend({"type":"image", "url":f"data:{item['mime']};base64,{item['data']}"} for item in image_attachments(attachments))
        effective=dict(binding.extensions_snapshot.get("turn_configuration",{}))
        self._emit(binding,"engine.configuration.applied",effective)
        params: dict[str, Any] = {"threadId": binding.native_session_id, "input": inputs,**effective}
        if binding.extensions_snapshot.get('managed_task_id') and self.browser_service:
            params.update(approvalsReviewer='user', approvalPolicy='untrusted')
        # A fast completion notification may arrive before the RPC response.
        # Admit active state before sending, never overwrite its completed state.
        binding.status = "active"
        result = await self._conn.request("turn/start", params, timeout=30)
        turn = (result or {}).get("turn") or {}
        binding.native_turn_id = turn.get("id")
        binding.updated_at = time.time()
        return binding.native_turn_id

    async def steer(self, binding: EngineSessionBinding, text: str) -> bool:
        if not binding.native_turn_id:
            return False
        try:
            await self._conn.request(
                "turn/steer",
                {
                    "threadId": binding.native_session_id,
                    "input": [{"type": "text", "text": text}],
                    "expectedTurnId": binding.native_turn_id,
                },
                timeout=15,
            )
            return True
        except JsonRpcError:
            return False

    async def cancel(self, binding: EngineSessionBinding) -> None:
        if not (self._conn and self._conn.running):
            return
        if binding.native_turn_id:
            try:
                await self._conn.request(
                    "turn/interrupt",
                    {"threadId": binding.native_session_id, "turnId": binding.native_turn_id},
                    timeout=15,
                )
            except (JsonRpcError, TransportClosed):
                pass

    async def respond_approval(self, binding: EngineSessionBinding,
                               request_id: str, decision: str,
                               remember: bool = False,
                               content: Any = None) -> None:
        """Resolve a pending server→client request by its TermX request token."""
        entry = self._pending_decisions.pop(request_id, None)
        if entry is None:
            raise KeyError(f"no pending engine request {request_id}")
        fut, method, params = entry
        if fut.done():
            return
        approved = decision in ("approve", "approve_always")
        if method == "browser.review":
            from termx.engines.action_review import respond_review
            respond_review(self, binding, entry, approved)
            return
        if method in ("item/commandExecution/requestApproval",
                      "item/fileChange/requestApproval"):
            wire = {
                "decision": (
                    "acceptForSession" if (approved and remember)
                    else "accept" if approved
                    else "cancel" if decision == "cancel"
                    else "decline"
                )
            }
        elif method == "item/permissions/requestApproval":
            requested = params.get("permissions") or {}
            wire = {
                "permissions": content if approved and content is not None
                else (requested if approved else {}),
                "scope": "session" if remember else "turn",
            }
        else:  # elicitation / requestUserInput / dynamic tool call
            wire = {
                "action": "accept" if approved else ("cancel" if decision == "cancel" else "decline"),
                "content": content if approved else None,
            }
        fut.set_result(wire)

    async def close(self, binding: EngineSessionBinding) -> None:
        """Detach from the thread; the native thread persists for resume."""
        binding.status = "closed"
        binding.updated_at = time.time()
        self._threads.pop(binding.native_session_id, None)
        self._bindings.pop(binding.binding_id, None)

    async def list_sessions(self) -> list[dict[str, Any]]:
        await self._ensure_conn()
        result = await self._conn.request("thread/list", {"limit": 50}, timeout=20)
        return list((result or {}).get("data", []))

    async def shutdown(self) -> None:
        if self._conn:
            await self._conn.close()
            process = getattr(self._conn, '_proc', None)
            if process is not None and process.returncode is None:
                raise RuntimeError('Codex transport process remains active after shutdown')
        self._initialized = False

    # ----------------------------------------------------------------- events

    def _emit(self, binding: EngineSessionBinding, etype: str,
              payload: dict[str, Any], native: dict[str, Any] | None = None) -> None:
        native_ids = {"session_id": binding.native_session_id}
        if binding.native_turn_id:
            native_ids["turn_id"] = binding.native_turn_id
        native_ids.update(native or {})
        self._event_sink(binding.binding_id, EngineEvent(etype, payload, native_ids))

    def _binding_for(self, params: dict[str, Any]) -> EngineSessionBinding | None:
        thread_id = params.get("threadId") or (params.get("thread") or {}).get("id")
        turn = params.get("turn") or {}
        if not thread_id and turn.get("threadId"):
            thread_id = turn["threadId"]
        binding_id = self._threads.get(str(thread_id)) if thread_id else None
        return self._bindings.get(binding_id) if binding_id else None

    def _on_notification(self, method: str, params: dict[str, Any]) -> None:
        if method == "thread/started":
            return
        binding = self._binding_for(params)
        turn = params.get("turn") or {}
        if binding is not None and turn.get("id"):
            binding.native_turn_id = turn.get("id") or binding.native_turn_id

        if method == "turn/started":
            if binding:
                binding.status = "active"
                self._emit(binding, "engine.turn.started", _bounded(params))
            return
        if method == "turn/completed":
            if binding:
                status = turn.get("status", "completed")
                binding.status = "idle"
                self._emit(binding, "engine.turn.completed",
                           {"status": status, "error": turn.get("error")})
            return
        if method == "serverRequest/resolved":
            if binding:
                self._emit(binding, "engine.approval.resolved", _bounded(params))
            return
        mapped = _ITEM_EVENTS.get(method)
        if mapped and binding:
            native: dict[str, Any] = {}
            item = params.get("item") or {}
            if method == 'item/completed' and self.browser_service:
                reviewed = self._review_items.pop((binding.binding_id, item.get('id')), None)
                if reviewed:
                    self.browser_service.review.complete_external(reviewed.action_id, success=item.get('status') not in {'failed', 'declined', 'cancelled'})
            if isinstance(item, dict) and item.get("id"):
                native["item_id"] = item["id"]
            if params.get("itemId"):
                native["item_id"] = params["itemId"]
            self._emit(binding, mapped, _bounded(params), native)
            return
        if method in ("turn/plan/updated", "turn/diff/updated", "thread/tokenUsage/updated"):
            if binding:
                name = {"turn/plan/updated": "engine.plan.updated",
                        "turn/diff/updated": "engine.diff.updated",
                        "thread/tokenUsage/updated": "engine.usage"}[method]
                self._emit(binding, name, _bounded(params))
            return
        if method.startswith("account/"):
            if method == "account/updated":
                self._account["account"] = {"type": params.get("authMode"), "planType": params.get("planType")}
            for b in self._bindings.values():
                self._emit(b, "engine.account", _bounded(params))
            return
        if binding:
            self._emit(binding, "engine.raw", {"method": method, "params": _bounded(params)})

    async def _on_server_request(self, method: str, params: dict[str, Any]) -> Any:
        binding = self._binding_for(params)
        kind = {
            "item/commandExecution/requestApproval": "tool",
            "item/fileChange/requestApproval": "tool",
            "item/permissions/requestApproval": "capability",
            "item/tool/requestUserInput": "elicitation",
            "item/tool/call": "tool",
            "mcpServer/elicitation/request": "elicitation",
        }.get(method)
        if kind is None:
            raise JsonRpcError(-32601, f"unsupported server request {method}")
        if binding is None or self._approval_sink is None:
            return ({"decision": "decline"} if kind != "elicitation"
                    else {"action": "decline", "content": None})
        if method in {"item/commandExecution/requestApproval", "item/fileChange/requestApproval", "item/permissions/requestApproval"} and self.browser_service:
            from termx.engines.action_review import authorize,consume_reviewed
            from termx.auto_review import ActionBlocked
            try:
                checked = await authorize(self, binding, 'run_shell' if method == 'item/commandExecution/requestApproval' else 'native_permission', params, str(params.get('approvalId') or params.get('itemId') or uuid.uuid4().hex))
                if checked:
                    envelope, validate, permit = checked
                    await consume_reviewed(self,binding,envelope,validate,permit,tool='run_shell' if method=='item/commandExecution/requestApproval' else 'native_permission',args=params)
                    self._review_items[(binding.binding_id, params.get('itemId'))] = envelope
                    return {'permissions': params.get('permissions', {}), 'scope': 'turn'} if method == 'item/permissions/requestApproval' else {'decision': 'accept'}
            except (PermissionError, ActionBlocked):
                return {'permissions': {}, 'scope': 'turn'} if method == 'item/permissions/requestApproval' else {'decision': 'decline'}
        token = f"engreq_{uuid.uuid4().hex[:16]}"
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[Any] = loop.create_future()
        self._pending_decisions[token] = (fut, method, params)
        try:
            approval_id = await self._approval_sink(
                binding.binding_id, token, method, _bounded(params), kind)
            self._emit(binding, "engine.approval.requested",
                       {"method": method, "kind": kind, "request_id": token,
                        "approval_id": approval_id, "request": _bounded(params)})
            return await fut
        finally:
            self._pending_decisions.pop(token, None)


def _codex_sandbox(profile: str) -> str | None:
    # thread/start `sandbox` enum (kebab-case per app-server schema)
    return {
        "host": "danger-full-access",
        "workspace": "workspace-write",
        "agent": "workspace-write",
        "read-only": "read-only",
    }.get(profile)


def _codex_approval_policy(mode: str) -> str | None:
    return {
        "standard": "untrusted",
        "remember": "on-request",
        "autonomous": "never",
    }.get(mode)
