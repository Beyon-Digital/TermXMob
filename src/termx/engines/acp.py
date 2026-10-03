"""Shared ACP (Agent Client Protocol) engine adapter.

Used by `devin acp` and `grok agent stdio`. Wraps the official
agent-client-protocol Python SDK: one agent subprocess per native session —
an ACP session is bound to its connection, so multiplexing is not allowed.
Sessions persist engine-side where the agent supports `session/load`.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from .env import probe_version, resolve_executable
from .types import (
    AUTH_UNKNOWN,
    EffectiveRunConfiguration,
    EngineCapabilities,
    EngineDescriptor,
    EngineEvent,
    EngineSessionBinding,
    SESSION_LOST,
)

MAX_RAW_EVENT_BYTES = 8 * 1024
FS_IO_MAX = 2 * 1024 * 1024
TERMINAL_OUTPUT_CAP = 1 * 1024 * 1024

EventSink = Callable[[str, EngineEvent], None]
ApprovalSink = Callable[[str, str, str, dict[str, Any], str], Awaitable[str]]


def _bounded(payload: Any) -> dict[str, Any]:
    data = payload if isinstance(payload, dict) else {"value": payload}
    if len(json.dumps(data, default=str)) <= MAX_RAW_EVENT_BYTES:
        return data
    return {"truncated": True, "preview": json.dumps(data, default=str)[:MAX_RAW_EVENT_BYTES]}


def _model_dump(obj: Any) -> dict[str, Any]:
    if obj is None:
        return {}
    dump = getattr(obj, "model_dump", None)
    if callable(dump):
        return dump(mode="json", exclude_none=True, by_alias=True)
    return {"value": str(obj)}


class _SessionClient:
    """ACP Client callbacks for one bound session."""

    def __init__(self, engine: "AcpEngine", session_id: str) -> None:
        self._engine = engine
        self._session_id = session_id
        self._terminals: dict[str, dict[str, Any]] = {}

    # ------------------------------------------------------------- updates

    def _binding(self, session_id: str | None = None) -> EngineSessionBinding | None:
        return self._engine._by_native(session_id or self._session_id)

    def _emit(self, etype: str, payload: dict[str, Any],
              session_id: str | None = None) -> None:
        binding = self._binding(session_id)
        if binding is not None:
            self._engine._emit(binding, etype, payload,
                               {"session_id": session_id or self._session_id})

    async def session_update(self, session_id: str, update: Any, **kw: Any) -> None:
        tag = getattr(update, "session_update", None) or type(update).__name__
        data = _model_dump(update)
        mapped = {
            "agent_message_chunk": "engine.message.delta",
            "agent_thought_chunk": "engine.reasoning.delta",
            "user_message_chunk": "engine.user.echo",
            "tool_call": "engine.tool.started",
            "tool_call_update": "engine.tool.progress",
            "plan": "engine.plan.updated",
            "available_commands_update": "engine.commands",
            "current_mode_update": "engine.mode",
            "config_option_update": "engine.config",
            "usage_update": "engine.usage",
            "session_info_update": "engine.session.info",
        }.get(str(tag))
        if mapped in ("engine.message.delta", "engine.reasoning.delta",
                      "engine.user.echo"):
            content = data.get("content") or {}
            text = content.get("text") if isinstance(content, dict) else None
            self._emit(mapped, {"delta": text or "", "update": _bounded(data)},
                       session_id=session_id)
        elif mapped:
            self._emit(mapped, _bounded(data), session_id=session_id)
        else:
            self._emit("engine.raw", {"update": str(tag), "data": _bounded(data)},
                       session_id=session_id)

    # ----------------------------------------------------------- permission

    async def request_permission(self, session_id: str, tool_call: Any,
                                 options: Any, **kw: Any) -> Any:
        from acp.schema import RequestPermissionResponse
        binding = self._binding(session_id)
        opts = _model_dump(options) if not isinstance(options, list) else [
            _model_dump(o) for o in options]
        if binding is None or self._engine._approval_sink is None:
            outcome = _cancelled_outcome()
            return RequestPermissionResponse(outcome=outcome)
        token = f"acpreq_{uuid.uuid4().hex[:16]}"
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[Any] = loop.create_future()
        self._engine._pending_decisions[token] = (fut, "session/request_permission",
                                                  {"options": opts})
        try:
            approval_id = await self._engine._approval_sink(
                binding.binding_id, token, "session/request_permission",
                {"tool_call": _bounded(_model_dump(tool_call)), "options": opts},
                "tool")
            self._emit("engine.approval.requested", session_id=session_id, payload={
                "request_id": token, "approval_id": approval_id,
                "options": opts,
                "tool_call": _bounded(_model_dump(tool_call)),
            })
            decision = await fut
            return RequestPermissionResponse(outcome=decision)
        finally:
            self._engine._pending_decisions.pop(token, None)

    # ------------------------------------------------------------ fs bridge

    def _path_ok(self, path: str, session_id: str | None = None) -> Path:
        """Only allow fs access inside the session's cwd tree."""
        binding = self._binding(session_id)
        root = Path(binding.cwd or ".").resolve() if binding else Path.cwd()
        target = Path(path).resolve()
        if root != target and root not in target.parents:
            raise PermissionError(f"fs path outside session root: {path}")
        return target

    async def read_text_file(self, session_id: str, path: str,
                             line: int | None = None,
                             limit: int | None = None, **kw: Any) -> Any:
        from acp.schema import ReadTextFileResponse
        target = self._path_ok(path, session_id)
        data = target.read_bytes()[:FS_IO_MAX]
        text = data.decode("utf-8", errors="replace")
        if line is not None or limit is not None:
            lines = text.splitlines(keepends=True)
            start = max(0, (line or 1) - 1)
            end = start + (limit or len(lines))
            text = "".join(lines[start:end])
        return ReadTextFileResponse(content=text)

    async def write_text_file(self, session_id: str, path: str,
                              content: str, **kw: Any) -> Any:
        from acp.schema import WriteTextFileResponse
        target = self._path_ok(path, session_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content[:FS_IO_MAX], encoding="utf-8")
        return WriteTextFileResponse()

    # -------------------------------------------------------- terminal bridge

    async def create_terminal(self, session_id: str, command: str,
                              args: list[str] | None = None,
                              env: list[Any] | None = None,
                              cwd: str | None = None,
                              output_byte_limit: int | None = None,
                              **kw: Any) -> Any:
        from acp.schema import CreateTerminalResponse
        binding = self._binding(session_id)
        run_cwd = cwd or (binding.cwd if binding else None)
        env_map = dict(os.environ)
        for entry in env or []:
            name = getattr(entry, "name", None) or (entry.get("name") if isinstance(entry, dict) else None)
            value = getattr(entry, "value", None) or (entry.get("value") if isinstance(entry, dict) else None)
            if name and value is not None:
                env_map[str(name)] = str(value)
        proc = await asyncio.create_subprocess_exec(
            command, *(args or []),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=run_cwd,
            env=env_map,
        )
        terminal_id = f"term_{uuid.uuid4().hex[:12]}"
        self._terminals[terminal_id] = {
            "proc": proc,
            "output": bytearray(),
            "limit": output_byte_limit or TERMINAL_OUTPUT_CAP,
            "task": asyncio.create_task(self._drain(terminal_id)),
        }
        self._emit("engine.terminal.created",
                   {"terminal_id": terminal_id, "command": command},
                   session_id=session_id)
        return CreateTerminalResponse(terminal_id=terminal_id)

    async def _drain(self, terminal_id: str) -> None:
        entry = self._terminals.get(terminal_id)
        if not entry:
            return
        proc = entry["proc"]
        try:
            while True:
                chunk = await proc.stdout.read(4096)
                if not chunk:
                    break
                buf: bytearray = entry["output"]
                buf.extend(chunk[: max(0, entry["limit"] - len(buf))])
        except (OSError, ValueError):
            pass

    async def terminal_output(self, session_id: str, terminal_id: str, **kw: Any) -> Any:
        from acp.schema import TerminalOutputResponse
        entry = self._terminals.get(terminal_id)
        if not entry:
            raise ValueError(f"unknown terminal {terminal_id}")
        proc = entry["proc"]
        return TerminalOutputResponse(
            output=bytes(entry["output"]).decode("utf-8", errors="replace"),
            truncated=len(entry["output"]) >= entry["limit"],
            exit_status=None if proc.returncode is None else {
                "exit_code": proc.returncode, "signal": None},
        )

    async def wait_for_terminal_exit(self, session_id: str,
                                     terminal_id: str, **kw: Any) -> Any:
        from acp.schema import WaitForTerminalExitResponse
        entry = self._terminals.get(terminal_id)
        if not entry:
            raise ValueError(f"unknown terminal {terminal_id}")
        code = await entry["proc"].wait()
        return WaitForTerminalExitResponse(exit_status={"exit_code": code, "signal": None})

    async def kill_terminal(self, session_id: str, terminal_id: str, **kw: Any) -> Any:
        entry = self._terminals.get(terminal_id)
        if entry and entry["proc"].returncode is None:
            entry["proc"].kill()
        from acp.schema import KillTerminalResponse
        return KillTerminalResponse()

    async def release_terminal(self, session_id: str, terminal_id: str, **kw: Any) -> Any:
        entry = self._terminals.pop(terminal_id, None)
        if entry:
            if entry["proc"].returncode is None:
                entry["proc"].kill()
            entry["task"].cancel()
        from acp.schema import ReleaseTerminalResponse
        return ReleaseTerminalResponse()

    # ------------------------------------------------------------ ext/other

    async def ext_method(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        raise ValueError(f"unsupported extension method {method}")

    async def ext_notification(self, method: str, params: dict[str, Any]) -> None:
        self._emit("engine.raw", {"ext": method, "params": _bounded(params)})

    def on_connect(self, conn: Any) -> None:
        # SDK invokes this synchronously at connection setup.
        pass

    async def cleanup(self) -> None:
        for tid in list(self._terminals):
            await self.release_terminal(self._session_id, tid)


def _cancelled_outcome() -> Any:
    from acp.schema import DeniedOutcome
    return DeniedOutcome(outcome="cancelled")


class AcpEngine:
    """Shared adapter; subclasses set command/args/env quirks per vendor."""

    id = "acp"
    label = "ACP agent"
    executable_name = ""
    acp_args: list[str] = []
    version_args: list[str] = ["--version"]

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
        self._event_sink = event_sink or (lambda _b, _e: None)
        self._approval_sink = approval_sink
        self._spawn_env = spawn_env
        self._sessions: dict[str, dict[str, Any]] = {}   # native sid -> state
        self._bindings: dict[str, EngineSessionBinding] = {}
        self._pending_decisions: dict[str, tuple[asyncio.Future[Any], str, dict]] = {}
        self._auth_methods: list[dict[str, Any]] = []
        self._agent_info: dict[str, Any] = {}
        self._agent_capabilities: dict[str, Any] = {}

    # ------------------------------------------------------------- describe

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
            transport="acp-stdio",
            protocol={
                "name": "acp",
                "version": 1,
                "agent_info": self._agent_info,
                "auth_methods": self._auth_methods,
                "agent_capabilities": self._agent_capabilities,
            },
        )

    def _auth_state(self) -> str:
        return AUTH_UNKNOWN  # subclasses override with real detection

    def _auth_detail(self) -> str:
        return ""

    async def probe(self) -> EngineDescriptor:
        desc = self.descriptor()
        self._executable = resolve_executable(
            self.executable_name, override=self._override)
        desc.installed = self._executable is not None
        desc.executable = self._executable
        if not self._executable:
            desc.error = f"{self.executable_name} not found (PATH + known dirs)"
            return desc
        self._version = await probe_version(self._executable, self.version_args)
        desc.version = self._version
        # Cheap protocol handshake: spawn, initialize, inspect auth methods.
        try:
            async with self._spawn() as (conn, _proc):
                resp = await conn.initialize(
                    protocol_version=1,
                    client_info=_client_info(),
                    client_capabilities=_client_capabilities(),
                )
                self._agent_info = _model_dump(getattr(resp, "agent_info", None))
                self._auth_methods = [
                    _model_dump(m) for m in (getattr(resp, "auth_methods", None) or [])]
                self._agent_capabilities = _model_dump(
                    getattr(resp, "agent_capabilities", None))
                desc.protocol["agent_info"] = self._agent_info
                desc.protocol["auth_methods"] = self._auth_methods
                desc.protocol["agent_capabilities"] = self._agent_capabilities
        except Exception as exc:
            desc.error = f"probe failed: {exc}"
        desc.auth_state = self._auth_state()
        desc.auth_detail = self._auth_detail()
        desc.version = self._version
        return desc

    def capabilities(self) -> EngineCapabilities:
        caps = self._agent_capabilities
        return EngineCapabilities(
            tools_filter="advisory",
            approvals="native",
            streaming=True,
            resume="supported" if caps.get("loadSession") or caps.get("load_session") else "unverified",
            steer="unverified",
            fork="supported" if caps.get("forkSession") or caps.get("fork_session") else "unsupported",
            subagents="unverified",
            skills_native="unverified",
            mcp_native="supported",
            models=[],
        )

    # ------------------------------------------------------------- plumbing

    def _spawn(self):
        from acp.stdio import spawn_agent_process
        argv = [self._executable or self.executable_name, *self.acp_args]
        client = _ProcessOwner(self)
        return spawn_agent_process(
            client, argv[0], *argv[1:],
            env=self._spawn_env or _child_env(),
        )

    def _by_native(self, native_id: str) -> EngineSessionBinding | None:
        state = self._sessions.get(native_id)
        return state["binding"] if state else None

    def _emit(self, binding: EngineSessionBinding, etype: str,
              payload: dict[str, Any], native: dict[str, Any] | None = None) -> None:
        ids = {"session_id": binding.native_session_id}
        ids.update(native or {})
        self._event_sink(binding.binding_id, EngineEvent(etype, payload, ids))

    # ------------------------------------------------------------- sessions

    async def create_session(self, cfg: EffectiveRunConfiguration) -> EngineSessionBinding:
        import acp.schema as schema
        state: dict[str, Any] = {}
        ctx = self._spawn_for(state)
        conn, proc = await ctx.__aenter__()
        state["ctx"] = ctx
        state["conn"] = conn
        state["proc"] = proc
        try:
            resp = await conn.initialize(
                protocol_version=1,
                client_info=_client_info(),
                client_capabilities=_client_capabilities(),
            )
            self._agent_info = _model_dump(getattr(resp, "agent_info", None))
            self._auth_methods = [
                _model_dump(m) for m in (getattr(resp, "auth_methods", None) or [])]
            self._agent_capabilities = _model_dump(getattr(resp, "agent_capabilities", None))
            mcp_servers = self._mcp_servers(cfg)
            session = await conn.new_session(cwd=cfg.cwd or ".", mcp_servers=mcp_servers or None)
        except Exception:
            await ctx.__aexit__(None, None, None)
            raise
        sid = session.session_id
        binding = EngineSessionBinding.new(
            self.id, sid, cwd=cfg.cwd or "",
            profile_revision=cfg.agent_profile_revision,
            extensions_snapshot={
                "skills": [s.get("id") for s in cfg.skills],
                "mcp": [m.get("connection_id") for m in cfg.mcp_bindings],
            })
        binding.project_id = cfg.agent_id
        state["binding"] = binding
        state["client"] = _SessionClient(self, sid)
        self._sessions[sid] = state
        self._bindings[binding.binding_id] = binding
        modes = _model_dump(getattr(session, "modes", None))
        if modes:
            self._emit(binding, "engine.session.info", {"modes": modes})
        return binding

    def _spawn_for(self, state: dict[str, Any]):
        from acp.stdio import spawn_agent_process
        client = _SessionClient(self, session_id="")  # rebound below
        state["client"] = client
        argv = [self._executable or self.executable_name, *self.acp_args]
        return spawn_agent_process(
            client, argv[0], *argv[1:],
            env=self._spawn_env or _child_env(),
        )

    def _mcp_servers(self, cfg: EffectiveRunConfiguration) -> list[Any]:
        # P4 wires real TermX MCP bindings; for now pass through explicit defs.
        out: list[Any] = []
        try:
            from acp.schema import McpServerStdio, EnvVariable
        except ImportError:
            return out
        for binding in cfg.mcp_bindings:
            cmd = binding.get("command") or []
            if not cmd:
                continue
            env_vars = [
                EnvVariable(name=str(k), value=str(v))
                for k, v in (binding.get("env") or {}).items()
            ]
            out.append(McpServerStdio(
                name=str(binding.get("connection_id") or "mcp"),
                command=str(cmd[0]),
                args=[str(a) for a in cmd[1:]],
                env=env_vars,
            ))
        return out

    async def attach(self, binding: EngineSessionBinding) -> EngineSessionBinding:
        caps = self._agent_capabilities
        if not (caps.get("loadSession") or caps.get("load_session")):
            raise ValueError(f"{self.id} does not support session/load")
        state: dict[str, Any] = {}
        ctx = self._spawn_for(state)
        conn, proc = await ctx.__aenter__()
        state["ctx"] = ctx
        state["conn"] = conn
        state["proc"] = proc
        try:
            await conn.initialize(
                protocol_version=1, client_info=_client_info(),
                client_capabilities=_client_capabilities())
            await conn.load_session(cwd=binding.cwd or ".",
                                    session_id=binding.native_session_id)
        except Exception:
            await ctx.__aexit__(None, None, None)
            raise
        client = _SessionClient(self, binding.native_session_id)
        client._session_id = binding.native_session_id
        state["binding"] = binding
        state["client"] = client
        binding.status = "idle"
        binding.updated_at = time.time()
        self._sessions[binding.native_session_id] = state
        self._bindings[binding.binding_id] = binding
        return binding

    async def send(self, binding: EngineSessionBinding, prompt: str,
                   attachments: list[dict[str, Any]] | None = None) -> str | None:
        from acp.schema import TextContentBlock
        state = self._sessions.get(binding.native_session_id)
        if state is None:
            raise ValueError("no live ACP session")
        binding.status = "active"
        self._emit(binding, "engine.turn.started", {"prompt_chars": len(prompt)})

        async def _run_prompt() -> None:
            try:
                resp = await state["conn"].prompt(
                    session_id=binding.native_session_id,
                    prompt=[TextContentBlock(type="text", text=prompt)],
                )
                stop = str(getattr(resp, "stop_reason", "") or "")
                usage = _model_dump(getattr(resp, "usage", None))
                self._emit(binding, "engine.turn.completed", {
                    "status": {"end_turn": "completed", "cancelled": "interrupted",
                               "refusal": "failed", "max_tokens": "failed",
                               "max_turn_requests": "failed"}.get(stop, "completed"
                               if stop in ("", "end_turn") else stop),
                    "stop_reason": stop,
                    "usage": usage,
                })
            except Exception as exc:
                self._emit(binding, "engine.turn.completed",
                           {"status": "failed", "error": {"message": str(exc)}})
            finally:
                binding.status = "idle"
                binding.updated_at = time.time()

        # session/prompt resolves at turn end — run it in a task so callers
        # (and the HTTP request) return once the turn has started.
        state["turn_task"] = asyncio.create_task(_run_prompt())
        return binding.native_session_id

    async def cancel(self, binding: EngineSessionBinding) -> None:
        state = self._sessions.get(binding.native_session_id)
        if state is not None:
            try:
                await state["conn"].cancel(session_id=binding.native_session_id)
            except Exception:
                pass

    async def respond_approval(self, binding: EngineSessionBinding,
                               request_id: str, decision: str,
                               remember: bool = False,
                               content: Any = None) -> None:
        entry = self._pending_decisions.pop(request_id, None)
        if entry is None:
            raise KeyError(f"no pending engine request {request_id}")
        fut, _method, params = entry
        if fut.done():
            return
        from acp.schema import AllowedOutcome, DeniedOutcome
        options = params.get("options") or []
        approved = decision in ("approve", "approve_always")
        option_id = None
        if approved and options:
            want = "allow_always" if remember else "allow_once"
            for opt in options:
                if opt.get("kind") == want or opt.get("kind", "").startswith("allow"):
                    option_id = opt.get("option_id") or opt.get("optionId")
                    if opt.get("kind") == want:
                        break
            if option_id is None:
                option_id = options[0].get("option_id") or options[0].get("optionId")
        if approved and option_id:
            fut.set_result(AllowedOutcome(outcome="selected", option_id=option_id))
        else:
            fut.set_result(DeniedOutcome(outcome="cancelled"))

    async def steer(self, binding: EngineSessionBinding, text: str) -> bool:
        # ACP has no steer; queueing semantics are agent-defined. Try prompt
        # anyway only when idle — never fabricate support.
        if binding.status == "active":
            return False
        await self.send(binding, text)
        return True

    async def close(self, binding: EngineSessionBinding) -> None:
        state = self._sessions.pop(binding.native_session_id, None)
        binding.status = "closed"
        binding.updated_at = time.time()
        self._bindings.pop(binding.binding_id, None)
        if state is not None:
            client = state.get("client")
            if client is not None:
                try:
                    await client.cleanup()
                except Exception:
                    pass
            ctx = state.get("ctx")
            if ctx is not None:
                try:
                    await ctx.__aexit__(None, None, None)
                except Exception:
                    pass

    async def list_sessions(self) -> list[dict[str, Any]]:
        # Spawn a transient agent and ask — ACP list is capability-gated.
        try:
            async with self._spawn() as (conn, _proc):
                await conn.initialize(
                    protocol_version=1, client_info=_client_info(),
                    client_capabilities=_client_capabilities())
                resp = await conn.list_sessions()
                return [
                    _model_dump(s) for s in (getattr(resp, "sessions", None) or [])]
        except Exception:
            return []

    async def shutdown(self) -> None:
        for sid in list(self._sessions):
            state = self._sessions.get(sid)
            if state and state.get("binding"):
                await self.close(state["binding"])
        self._sessions.clear()


class _ProcessOwner(_SessionClient):
    """Client for the transient probe connection (no session bound)."""

    def __init__(self, engine: AcpEngine) -> None:
        super().__init__(engine, session_id="")


def _client_info() -> Any:
    from acp.schema import Implementation
    from .. import __version__
    return Implementation(name="termx", title="TermX", version=__version__)


def _client_capabilities() -> Any:
    from acp.client.connection import ClientCapabilities
    return ClientCapabilities.model_validate({
        "fs": {"readTextFile": True, "writeTextFile": True},
        "terminal": True,
    })


def _child_env() -> dict[str, str]:
    env = dict(os.environ)
    env.pop("TERMX_PASSCODE", None)
    env.pop("TERMX_TOKEN", None)
    return env
