"""Claude adapter — official claude-agent-sdk (runs the Claude Code loop).

Auth: Anthropic API key only (ANTHROPIC_API_KEY / `claude auth` env-based).
Third-party products may not resell claude.ai login — subscription tokens are
never read or forwarded.
"""

from __future__ import annotations

import asyncio
import os
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from .env import resolve_executable
from .types import (
    AUTH_AUTHENTICATED,
    AUTH_UNKNOWN,
    EffectiveRunConfiguration,
    EngineCapabilities,
    EngineDescriptor,
    EngineEvent,
    EngineSessionBinding,
)

EventSink = Callable[[str, EngineEvent], None]
ApprovalSink = Callable[[str, str, str, dict[str, Any], str], Awaitable[str]]

MAX_RAW_EVENT_BYTES = 8 * 1024


def _bounded(payload: Any) -> dict[str, Any]:
    import json
    data = payload if isinstance(payload, dict) else {"value": payload}
    if len(json.dumps(data, default=str)) <= MAX_RAW_EVENT_BYTES:
        return data
    return {"truncated": True}


class ClaudeEngine:
    id = "claude"
    label = "Claude"

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
        self._event_sink = event_sink or (lambda _b, _e: None)
        self._approval_sink = approval_sink
        self._spawn_env = spawn_env
        self._sessions: dict[str, dict[str, Any]] = {}  # native sid -> state
        self._bindings: dict[str, EngineSessionBinding] = {}
        self._pending_decisions: dict[str, tuple[asyncio.Future[Any], str, dict]] = {}

    def descriptor(self) -> EngineDescriptor:
        if self._executable is None:
            self._executable = resolve_executable("claude", override=self._override)
        return EngineDescriptor(
            id=self.id,
            label=self.label,
            installed=self._executable is not None,
            executable=self._executable,
            auth_state=self._auth_state(),
            auth_detail="ANTHROPIC_API_KEY" if os.environ.get("ANTHROPIC_API_KEY") else "",
            location="sdk-bundled",
            transport="sdk",
            protocol={"name": "claude-agent-sdk", "auth": "api-key"},
        )

    def _auth_state(self) -> str:
        return AUTH_AUTHENTICATED if os.environ.get("ANTHROPIC_API_KEY") else AUTH_UNKNOWN

    async def probe(self) -> EngineDescriptor:
        # The SDK bundles the CLI; a real auth check requires a turn, so probe
        # reports installation + credential presence honestly.
        desc = self.descriptor()
        if not desc.installed:
            desc.error = "claude executable not found (PATH + known dirs)"
        return desc

    def capabilities(self) -> EngineCapabilities:
        return EngineCapabilities(
            tools_filter="gateway",   # can_use_tool is a programmatic gate
            approvals="native",
            streaming=True,
            resume="supported",       # options.resume = session_id
            steer="unverified",
            fork="supported",         # options.fork_session
            subagents="supported",    # SDK agents/subagents
            skills_native="supported",
            mcp_native="supported",   # options.mcp_servers incl. in-process SDK
            models=[],
            notes={"auth": "Anthropic API key only — no claude.ai subscription reuse"},
        )

    # ------------------------------------------------------------- sessions

    def _options(self, cfg: EffectiveRunConfiguration, binding: EngineSessionBinding) -> Any:
        from claude_agent_sdk import ClaudeAgentOptions
        opts_kwargs: dict[str, Any] = {
            "cwd": cfg.cwd or None,
            "model": cfg.model or None,
            "can_use_tool": self._make_can_use_tool(binding),
            "include_partial_messages": True,
            "permission_mode": "default",
        }
        if self._executable:
            opts_kwargs["cli_path"] = self._executable
        env = dict(self._spawn_env or os.environ)
        env.pop("TERMX_PASSCODE", None)
        env.pop("TERMX_TOKEN", None)
        # Never forward another agent's credentials.
        env.pop("OPENAI_API_KEY", None)
        opts_kwargs["env"] = env
        if cfg.instructions:
            opts_kwargs["system_prompt"] = {
                "type": "preset", "preset": "claude_code",
                "append": cfg.instructions,
            }
        tools = cfg.tools or {}
        allowed = tools.get("allowed") or []
        denied = tools.get("denied") or []
        if allowed:
            opts_kwargs["allowed_tools"] = list(allowed)
        if denied:
            opts_kwargs["disallowed_tools"] = list(denied)
        mcp = self._mcp_servers(cfg)
        if mcp:
            opts_kwargs["mcp_servers"] = mcp
        limits = cfg.tools.get("limits") or {}
        if limits.get("max_steps"):
            opts_kwargs["max_turns"] = int(limits["max_steps"])
        return ClaudeAgentOptions(**{k: v for k, v in opts_kwargs.items() if v is not None})

    def _mcp_servers(self, cfg: EffectiveRunConfiguration) -> dict[str, Any]:
        out: dict[str, Any] = {}
        from claude_agent_sdk import McpStdioServerConfig, McpHttpServerConfig
        for binding in cfg.mcp_bindings:
            name = str(binding.get("connection_id") or "mcp")
            if binding.get("url"):
                out[name] = McpHttpServerConfig(type="http", url=str(binding["url"]))
            elif binding.get("command"):
                cmd = binding["command"]
                out[name] = McpStdioServerConfig(
                    type="stdio", command=str(cmd[0]),
                    args=[str(a) for a in cmd[1:]],
                    env={str(k): str(v) for k, v in (binding.get("env") or {}).items()})
        return out

    def _make_can_use_tool(self, binding: EngineSessionBinding):
        engine = self

        async def gate(tool_name: str, tool_input: dict[str, Any], context: Any) -> Any:
            from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny
            if engine._approval_sink is None:
                return PermissionResultAllow(updated_input=tool_input)
            token = f"claudereq_{uuid.uuid4().hex[:16]}"
            loop = asyncio.get_running_loop()
            fut: asyncio.Future[Any] = loop.create_future()
            engine._pending_decisions[token] = (fut, "can_use_tool",
                                                {"tool": tool_name})
            try:
                approval_id = await engine._approval_sink(
                    binding.binding_id, token, "claude.can_use_tool",
                    {"tool": tool_name, "input": _bounded(tool_input)}, "tool")
                engine._emit(binding, "engine.approval.requested", {
                    "request_id": token, "approval_id": approval_id,
                    "tool": tool_name, "input": _bounded(tool_input)})
                decision = await fut
                if decision == "allow":
                    return PermissionResultAllow(updated_input=tool_input)
                return PermissionResultDeny(message="Denied by TermX user")
            finally:
                engine._pending_decisions.pop(token, None)
        return gate

    async def create_session(self, cfg: EffectiveRunConfiguration) -> EngineSessionBinding:
        from claude_agent_sdk import ClaudeSDKClient
        binding = EngineSessionBinding.new(
            self.id, f"pending_{uuid.uuid4().hex[:8]}", cwd=cfg.cwd or "",
            profile_revision=cfg.agent_profile_revision)
        options = self._options(cfg, binding)
        client = ClaudeSDKClient(options=options)
        state = {
            "client": client,
            "options": options,
            "cfg": cfg,
            "reader": None,
            "pending_msgs": asyncio.Queue(),
        }
        self._bindings[binding.binding_id] = binding
        # The native session id arrives in the init SystemMessage after the
        # first query; keep the binding keyed by a placeholder until then.
        self._sessions[binding.binding_id] = state
        state["binding"] = binding
        return binding

    async def attach(self, binding: EngineSessionBinding) -> EngineSessionBinding:
        """Native resume: options.resume = prior session id on next connect."""
        binding.status = "idle"
        binding.updated_at = time.time()
        self._bindings[binding.binding_id] = binding
        self._sessions[binding.binding_id] = {
            "resume_id": binding.native_session_id,
            "binding": binding,
        }
        return binding

    async def send(self, binding: EngineSessionBinding, prompt: str,
                   attachments: list[dict[str, Any]] | None = None) -> str | None:
        state = self._sessions.get(binding.binding_id)
        if state is None:
            raise ValueError("no claude session state")
        binding.status = "active"
        self._emit(binding, "engine.turn.started", {"prompt_chars": len(prompt)})

        async def _run() -> None:
            try:
                await self._run_turn(binding, state, prompt)
            except Exception as exc:
                self._emit(binding, "engine.turn.completed",
                           {"status": "failed", "error": {"message": str(exc)}})
            finally:
                binding.status = "idle"
                binding.updated_at = time.time()

        state["reader"] = asyncio.create_task(_run())
        return binding.native_session_id

    async def _run_turn(self, binding: EngineSessionBinding,
                        state: dict[str, Any], prompt: str) -> None:
        from claude_agent_sdk import (
            AssistantMessage, ClaudeSDKClient, ResultMessage, SystemMessage,
        )
        client = state.get("client")
        if client is None:
            options = state.get("options")
            if options is None:
                cfg = state.get("cfg") or EffectiveRunConfiguration(cwd=binding.cwd)
                options = self._options(cfg, binding)
                state["options"] = options
            if state.get("resume_id"):
                options.resume = state["resume_id"]
            client = ClaudeSDKClient(options=options)
            state["client"] = client
        await client.connect()
        await client.query(prompt)
        async for message in client.receive_response():
            self._map_message(binding, message)
            if isinstance(message, SystemMessage) and getattr(message, "subtype", "") == "init":
                data = getattr(message, "data", {}) or {}
                sid = data.get("session_id")
                if sid and binding.native_session_id.startswith("pending_"):
                    binding.native_session_id = sid
                    self._emit(binding, "engine.session.bound",
                               {"native_session_id": sid})
            if isinstance(message, ResultMessage):
                self._emit(binding, "engine.turn.completed", {
                    "status": "completed" if getattr(message, "is_error", False) is False else "failed",
                    "stop_reason": getattr(message, "subtype", ""),
                    "result": getattr(message, "result", None),
                    "usage": _bounded(getattr(message, "usage", {}) or {}),
                    "cost_usd": getattr(message, "total_cost_usd", None),
                })
                return
        self._emit(binding, "engine.turn.completed", {"status": "completed"})

    def _map_message(self, binding: EngineSessionBinding, message: Any) -> None:
        from claude_agent_sdk import AssistantMessage, SystemMessage, UserMessage
        name = type(message).__name__
        if isinstance(message, AssistantMessage):
            for block in getattr(message, "content", []) or []:
                bname = type(block).__name__
                if bname == "TextBlock":
                    self._emit(binding, "engine.message.delta",
                               {"delta": getattr(block, "text", "")})
                elif bname == "ToolUseBlock":
                    self._emit(binding, "engine.tool.started", {
                        "tool": getattr(block, "name", ""),
                        "input": _bounded(getattr(block, "input", {}) or {})})
                elif bname == "ThinkingBlock":
                    self._emit(binding, "engine.reasoning.delta",
                               {"delta": getattr(block, "thinking", "")})
        elif isinstance(message, SystemMessage):
            self._emit(binding, "engine.session.info",
                       _bounded(getattr(message, "data", {}) or {}))
        else:
            self._emit(binding, "engine.raw", {"message": name,
                                               "data": _bounded(getattr(message, "__dict__", {}) or {})})

    async def cancel(self, binding: EngineSessionBinding) -> None:
        state = self._sessions.get(binding.binding_id)
        if state and state.get("client"):
            try:
                await state["client"].interrupt()
            except Exception:
                pass

    async def respond_approval(self, binding: EngineSessionBinding,
                               request_id: str, decision: str,
                               remember: bool = False,
                               content: Any = None) -> None:
        entry = self._pending_decisions.pop(request_id, None)
        if entry is None:
            raise KeyError(f"no pending engine request {request_id}")
        fut, _method, _params = entry
        if not fut.done():
            fut.set_result("allow" if decision in ("approve", "approve_always") else "deny")

    async def steer(self, binding: EngineSessionBinding, text: str) -> bool:
        # `query` during an active turn queues input natively in the SDK.
        state = self._sessions.get(binding.binding_id)
        if state is None or not state.get("client"):
            return False
        await state["client"].query(text)
        return True

    async def close(self, binding: EngineSessionBinding) -> None:
        state = self._sessions.pop(binding.binding_id, None)
        binding.status = "closed"
        binding.updated_at = time.time()
        self._bindings.pop(binding.binding_id, None)
        if state and state.get("client"):
            try:
                await state["client"].disconnect()
            except Exception:
                pass

    async def list_sessions(self) -> list[dict[str, Any]]:
        return []  # SDK session listing not exposed; honest empty.

    async def shutdown(self) -> None:
        for bid in list(self._sessions):
            state = self._sessions.get(bid)
            if state and state.get("binding"):
                await self.close(state["binding"])
        self._sessions.clear()

    def _emit(self, binding: EngineSessionBinding, etype: str,
              payload: dict[str, Any], native: dict[str, Any] | None = None) -> None:
        ids = {"session_id": binding.native_session_id}
        ids.update(native or {})
        self._event_sink(binding.binding_id, EngineEvent(etype, payload, ids))
