"""Shared ACP (Agent Client Protocol) engine adapter.

Used by Devin, Grok, Antigravity and configured stdio agents. Wraps the official
agent-client-protocol Python SDK: one agent subprocess per native session —
an ACP session is bound to its connection, so multiplexing is not allowed.
Sessions persist engine-side where the agent supports `session/load`.
"""

from __future__ import annotations

import asyncio
import copy
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

    def __init__(self, engine: "AcpEngine", session_id: str,
                 state: dict[str, Any] | None = None) -> None:
        self._engine = engine
        self._session_id = session_id
        self._state = state
        self._terminals: dict[str, dict[str, Any]] = {}
        self._review_calls = {}

    # ------------------------------------------------------------- updates

    def _binding(self, session_id: str | None = None) -> EngineSessionBinding | None:
        if self._session_id and session_id and session_id != self._session_id:
            raise PermissionError("ACP callback belongs to another session")
        return self._state.get("binding") if self._state is not None else None

    def _emit(self, etype: str, payload: dict[str, Any],
              session_id: str | None = None) -> None:
        binding = self._binding(session_id)
        if binding is not None:
            self._engine._emit(binding, etype, payload,
                               {"session_id": session_id or self._session_id})

    async def session_update(self, session_id: str, update: Any, **kw: Any) -> None:
        tag = getattr(update, "session_update", None) or type(update).__name__
        data = _model_dump(update)
        self._binding(session_id)
        state = self._state
        if data.get('status') in {'completed', 'failed'} and self._engine.browser_service:
            identifier = self._review_calls.pop(str(data.get('toolCallId') or data.get('tool_call_id') or ''), None)
            if identifier: self._engine.browser_service.review.complete_external(identifier, success=data['status'] == 'completed')
        if state is not None:
            if tag == "config_option_update":
                state["config_options"] = data.get("configOptions", [])
                self._engine._remember_configuration(state)
            elif tag == "current_mode_update":
                state.setdefault("modes", {})["currentModeId"] = data.get("currentModeId")
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
        if binding is None or self._engine._approval_sink is None or (self._state or {}).get("discovery"):
            outcome = _cancelled_outcome()
            return RequestPermissionResponse(outcome=outcome)
        if self._engine.browser_service:
            from termx.engines.action_review import authorize
            from acp.schema import AllowedOutcome
            call = _model_dump(tool_call)
            try:
                checked = await authorize(self._engine, binding, 'native_permission', call, str(call.get('toolCallId') or call.get('tool_call_id') or uuid.uuid4().hex))
                if checked:
                    option = next((option for option in opts if option.get('kind') == 'allow_once'), None)
                    if not option: raise PermissionError('ACP runner did not offer a single-use approval')
                    envelope, validate, permit = checked
                    await self._engine.browser_service.review.consume_external(envelope, permit['permit'], validate=validate)
                    self._review_calls[str(call.get('toolCallId') or call.get('tool_call_id') or '')] = envelope.action_id
                    return RequestPermissionResponse(outcome=AllowedOutcome(outcome='selected', option_id=option.get('optionId') or option.get('option_id')))
            except (PermissionError, ValueError):
                return RequestPermissionResponse(outcome=_cancelled_outcome())
        token = f"acpreq_{uuid.uuid4().hex[:16]}"
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[Any] = loop.create_future()
        self._engine._pending_decisions[token] = (fut, "session/request_permission",
                                                  {"options": opts, "binding_id": binding.binding_id})
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
        if binding is None:
            raise PermissionError("ACP callback has no bound session")
        root = Path(binding.cwd or ".").resolve()
        target = (root / path).resolve()
        if root != target and root not in target.parents:
            raise PermissionError(f"fs path outside session root: {path}")
        return target

    async def read_text_file(self, session_id: str, path: str,
                             line: int | None = None,
                             limit: int | None = None, **kw: Any) -> Any:
        from acp.schema import ReadTextFileResponse
        target = self._path_ok(path, session_id)
        async def read():
            with target.open("rb") as handle:
                data = handle.read(FS_IO_MAX)
            text = data.decode("utf-8", errors="replace")
            if line is not None or limit is not None:
                lines = text.splitlines(keepends=True)
                start = max(0, (line or 1) - 1)
                end = start + (limit or len(lines))
                text = "".join(lines[start:end])
            return ReadTextFileResponse(content=text)
        from termx.engines.action_review import execute
        return await execute(self._engine, self._binding(session_id), 'read_file', {'path': str(target), 'line': line, 'limit': limit}, uuid.uuid4().hex, read)

    async def write_text_file(self, session_id: str, path: str,
                              content: str, **kw: Any) -> Any:
        from acp.schema import WriteTextFileResponse
        target = self._path_ok(path, session_id)
        if len(content.encode("utf-8")) > FS_IO_MAX:
            raise ValueError("ACP file write exceeds the supported size limit")
        binding = self._binding(session_id)
        async def write():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            return WriteTextFileResponse()
        from termx.engines.action_review import execute
        return await execute(self._engine, binding, 'write_file', {'path': str(target), 'content': content}, uuid.uuid4().hex, write)

    # -------------------------------------------------------- terminal bridge

    async def create_terminal(self, session_id: str, command: str,
                              args: list[str] | None = None,
                              env: list[Any] | None = None,
                              cwd: str | None = None,
                              output_byte_limit: int | None = None,
                              **kw: Any) -> Any:
        from acp.schema import CreateTerminalResponse
        binding = self._binding(session_id)
        if binding is None:
            raise PermissionError("ACP callback has no bound session")
        run_cwd = str(self._path_ok(cwd or '.', session_id))
        env_map = self._engine._launch_env()
        for entry in env or []:
            name = getattr(entry, "name", None) or (entry.get("name") if isinstance(entry, dict) else None)
            value = entry.get("value") if isinstance(entry, dict) else getattr(entry, "value", None)
            if name and value is not None:
                env_map[str(name)] = str(value)
        from termx.engines.action_review import authorize
        checked = await authorize(self._engine, binding, 'run_shell', {'command': command, 'args': args or [], 'cwd': run_cwd, 'env': [str(entry) for entry in env or []]}, uuid.uuid4().hex)
        if checked:
            envelope, validate, permit = checked
            await self._engine.browser_service.review.consume_external(envelope, permit['permit'], validate=validate)
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
            "review_envelope": checked[0] if checked else None,
            "output": bytearray(),
            "limit": min(TERMINAL_OUTPUT_CAP, max(0, output_byte_limit if output_byte_limit is not None else TERMINAL_OUTPUT_CAP)),
            "truncated": False,
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
                buf.extend(chunk)
                if len(buf) > entry["limit"]:
                    entry["truncated"] = True
                    del buf[:len(buf) - entry["limit"]]
        except (OSError, ValueError):
            pass

    async def terminal_output(self, session_id: str, terminal_id: str, **kw: Any) -> Any:
        self._binding(session_id)
        from acp.schema import TerminalOutputResponse
        entry = self._terminals.get(terminal_id)
        if not entry:
            raise ValueError(f"unknown terminal {terminal_id}")
        proc = entry["proc"]
        return TerminalOutputResponse(
            output=bytes(entry["output"]).decode("utf-8", errors="replace"),
            truncated=entry["truncated"],
            exit_status=None if proc.returncode is None else _exit_status(proc.returncode),
        )

    async def wait_for_terminal_exit(self, session_id: str,
                                     terminal_id: str, **kw: Any) -> Any:
        self._binding(session_id)
        from acp.schema import WaitForTerminalExitResponse
        entry = self._terminals.get(terminal_id)
        if not entry:
            raise ValueError(f"unknown terminal {terminal_id}")
        code = await entry["proc"].wait()
        await entry["task"]
        if entry.get('review_envelope'):
            self._engine.browser_service.review.complete_external(entry['review_envelope'].action_id, success=code == 0)
        return WaitForTerminalExitResponse(**_exit_status(code))

    async def kill_terminal(self, session_id: str, terminal_id: str, **kw: Any) -> Any:
        self._binding(session_id)
        entry = self._terminals.get(terminal_id)
        if entry and entry["proc"].returncode is None:
            entry["proc"].kill()
        from acp.schema import KillTerminalResponse
        return KillTerminalResponse()

    async def release_terminal(self, session_id: str, terminal_id: str, **kw: Any) -> Any:
        self._binding(session_id)
        entry = self._terminals.pop(terminal_id, None)
        if entry:
            if entry["proc"].returncode is None:
                entry["proc"].kill()
            await entry["proc"].wait()
            entry["task"].cancel()
            await asyncio.gather(entry["task"], return_exceptions=True)
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


def _exit_status(code: int) -> dict[str, Any]:
    import signal
    return {"exit_code": code if code >= 0 else None,
            "signal": signal.Signals(-code).name if code < 0 else None}


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
        launch_config: dict[str, Any] | None = None,
    ) -> None:
        self._override = executable_override
        self._initial_override = executable_override
        self._executable: str | None = None
        self._version: str | None = None
        self._event_sink = event_sink or (lambda _b, _e: None)
        self._approval_sink = approval_sink
        self.browser_service = None
        self._spawn_env = spawn_env
        # Native IDs are scoped to the ACP connection and may collide across
        # processes. TermX binding IDs provide the cross-connection identity.
        self._sessions: dict[str, dict[str, Any]] = {}   # binding id -> state
        self._bindings: dict[str, EngineSessionBinding] = {}
        self._pending_decisions: dict[str, tuple[asyncio.Future[Any], str, dict]] = {}
        self._auth_methods: list[dict[str, Any]] = []
        self._agent_info: dict[str, Any] = {}
        self._agent_capabilities: dict[str, Any] = {}
        self._configuration: dict[str, Any] = {}
        self.configure_launch(launch_config or {})

    def configure_launch(self, config: dict[str, Any]) -> None:
        self._launch_config = dict(config)
        self._startup_timeout = float(config.get("startup_timeout_s", 30))
        self._cancel_timeout = float(config.get("cancel_timeout_s", 5))
        override = config.get("executable") or self._initial_override
        if override != self._override:
            self._executable = None
        self._override = override

    def _launch_env(self) -> dict[str, str]:
        env = dict(self._spawn_env) if self._spawn_env is not None else _child_env()
        if "env_names" in self._launch_config:
            runtime_names = {"PATH", "HOME", "USER", "LOGNAME", "SHELL", "LANG", "LC_ALL",
                             "TMPDIR", "TMP", "TEMP", "SYSTEMROOT", "SystemRoot", "COMSPEC",
                             "APPDATA", "LOCALAPPDATA", "USERPROFILE", "PATHEXT",
                             "XDG_CONFIG_HOME", "XDG_DATA_HOME", "SSL_CERT_FILE", "SSL_CERT_DIR"}
            allowed = runtime_names | set(self._launch_config["env_names"])
            env = {k: v for k, v in env.items() if k in allowed}
        for name in self._launch_config.get("env_names", []):
            if name in os.environ:
                env[name] = os.environ[name]
        env.pop("TERMX_PASSCODE", None)
        env.pop("TERMX_TOKEN", None)
        return env

    async def _initialize(self, conn: Any) -> Any:
        resp = await asyncio.wait_for(conn.initialize(
            protocol_version=1, client_info=_client_info(),
            client_capabilities=_client_capabilities()), self._startup_timeout)
        if resp.protocol_version != 1:
            raise ValueError(f"unsupported ACP protocol version {resp.protocol_version}; expected 1")
        self._agent_info = _model_dump(getattr(resp, "agent_info", None))
        self._auth_methods = [_model_dump(m) for m in (resp.auth_methods or [])]
        self._agent_capabilities = _model_dump(resp.agent_capabilities)
        return resp

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
        # Each probe is a fresh source of truth. Do not leak protocol metadata
        # from a previous executable/configuration into a failed handshake.
        self._agent_info = {}
        self._auth_methods = []
        self._agent_capabilities = {}
        self._version = None
        self._executable = resolve_executable(
            self.executable_name, override=self._override)
        desc.installed = self._executable is not None
        desc.executable = self._executable
        if not self._executable:
            desc.error = f"{self.executable_name} not found (PATH + known dirs)"
            return desc
        self._version = await probe_version(self._executable, self.version_args) if self.version_args else None
        desc.version = self._version
        # Cheap protocol handshake: spawn, initialize, inspect auth methods.
        try:
            async with self._spawn() as (conn, _proc):
                resp = await self._initialize(conn)
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
        self._version = self._version or self._agent_info.get("version")
        desc.version = self._version
        return desc

    async def authenticate(self, method_id: str) -> dict[str, Any]:
        # Authentication is user initiated and capability driven. Never
        # invent vendor method IDs or trigger login during discovery.
        async with self._spawn() as (conn, _proc):
            response = await self._initialize(conn)
            self._auth_methods = [
                _model_dump(m) for m in (getattr(response, "auth_methods", None) or [])]
            if method_id not in {m.get("id") for m in self._auth_methods}:
                raise ValueError("agent did not advertise this authentication method")
            await asyncio.wait_for(conn.authenticate(method_id), self._startup_timeout)
            return {"ok": True, "method": method_id}

    def capabilities(self) -> EngineCapabilities:
        caps = self._agent_capabilities
        return EngineCapabilities(
            tools_filter="advisory",
            approvals="native",
            streaming=True,
            resume="supported" if caps.get("loadSession") or caps.get("load_session") else "unverified",
            steer="unsupported",
            fork="unsupported",
            subagents="unverified",
            skills_native="unverified",
            mcp_native="supported",
            models=self._configuration.get("models", []),
            modes=self._configuration.get("modes", {}).get("availableModes", []),
            config_options=self._configuration.get("config_options", []),
        )

    # ------------------------------------------------------------- plumbing

    def _spawn(self):
        from acp.stdio import spawn_agent_process
        argv = self._argv()
        client = _ProcessOwner(self)
        return spawn_agent_process(
            client, argv[0], *argv[1:],
            env=self._launch_env(),
        )

    def _argv(self) -> list[str]:
        executable = (resolve_executable(self.executable_name, override=self._override)
                      if self._override else self._executable or resolve_executable(self.executable_name))
        if not executable:
            raise ValueError(f"{self._override or self.executable_name} not found or not executable")
        self._executable = executable
        return [executable, *self._launch_config.get("args", self.acp_args)]

    def _emit(self, binding: EngineSessionBinding, etype: str,
              payload: dict[str, Any], native: dict[str, Any] | None = None) -> None:
        ids = {"session_id": binding.native_session_id}
        ids.update(native or {})
        self._event_sink(binding.binding_id, EngineEvent(etype, payload, ids))

    # ------------------------------------------------------------- sessions

    async def create_session(self, cfg: EffectiveRunConfiguration, *, discovery: bool = False) -> EngineSessionBinding:
        state: dict[str, Any] = {"discovery": discovery}
        ctx = self._spawn_for(state, cfg.cwd)
        conn, proc = await ctx.__aenter__()
        state["ctx"] = ctx
        state["conn"] = conn
        state["proc"] = proc
        try:
            await self._initialize(conn)
            mcp_servers = self._mcp_servers(cfg)
            session = await asyncio.wait_for(conn.new_session(
                cwd=str(Path(cfg.cwd or ".").resolve()), mcp_servers=mcp_servers), self._startup_timeout)
        except BaseException:
            await ctx.__aexit__(None, None, None)
            raise
        sid = session.session_id
        binding = EngineSessionBinding.new(
            self.id, sid, cwd=cfg.cwd or "",
            profile_revision=cfg.agent_profile_revision,
            extensions_snapshot={
                "skills": [s.get("id") for s in cfg.skills],
                "managed_task_id": cfg.tools.get("managed_task_id"), "review_read_only": cfg.mode == "ask",
                "mcp": [m.get("connection_id") for m in cfg.mcp_bindings],
            })
        binding.project_id = cfg.agent_id
        state["binding"] = binding
        state["client"]._session_id = sid
        self._sessions[binding.binding_id] = state
        self._bindings[binding.binding_id] = binding
        state["process_watch"] = asyncio.create_task(self._watch_process(state))
        self._read_configuration(state, session)
        try:
            await self.configure_session(binding, cfg)
        except BaseException:
            await self.close(binding)
            raise
        return binding

    def _spawn_for(self, state: dict[str, Any], cwd: str | None = None):
        from acp.stdio import spawn_agent_process
        client = _SessionClient(self, session_id="", state=state)  # rebound below
        state["client"] = client
        argv = self._argv()
        return spawn_agent_process(
            client, argv[0], *argv[1:],
            env=self._launch_env(), cwd=cwd or None,
        )

    @staticmethod
    def _option_values(option: dict[str, Any]) -> list[str]:
        values = []
        for item in option.get("options", []):
            if "options" in item:
                values.extend(str(v["value"]) for v in item["options"])
            elif "value" in item:
                values.append(str(item["value"]))
        return values

    def _read_configuration(self, state: dict[str, Any], response: Any) -> None:
        state["modes"] = _model_dump(getattr(response, "modes", None))
        state["config_options"] = [
            _model_dump(o) for o in (getattr(response, "config_options", None) or [])]
        self._remember_configuration(state)

    def _remember_configuration(self, state: dict[str, Any]) -> None:
        options = state.get("config_options", [])
        mode = next((o for o in options if o.get("category") == "mode"
                     or o["id"] == "mode"), None)
        if mode:
            state.setdefault("modes", {})["currentModeId"] = mode["currentValue"]
        model = next((o for o in options if o.get("category") == "model"
                      or o["id"] == "model"), None)
        self._configuration = {
            "modes": state.get("modes", {}), "config_options": options,
            "models": self._option_values(model) if model else [],
        }

    def session_configuration(self, binding: EngineSessionBinding) -> dict[str, Any]:
        state = self._sessions.get(binding.binding_id)
        if state is None:
            raise ValueError("no live ACP session")
        options = state.get("config_options", [])
        model = next((o for o in options if o.get("category") == "model"
                      or o["id"] == "model"), None)
        return {"session_id": binding.native_session_id, "modes": state.get("modes", {}),
                "config_options": options, "models": self._option_values(model) if model else []}

    async def discover_configuration(self, cwd: str | None = None) -> dict[str, Any]:
        return await self.discover_catalogue(cwd, include_models=False)

    async def discover_catalogue(self, cwd: str | None = None, *, include_models: bool = True) -> dict[str, Any]:
        if not self._executable:
            desc = await self.probe()
            if desc.error:
                raise ValueError(desc.error)
        # Options are session-scoped in ACP, not part of initialize. This
        # temporary session requests metadata only; it never sends a prompt.
        root = str(Path(cwd or Path.home()).expanduser().resolve())
        if not Path(root).is_dir():
            raise ValueError("project folder is not a directory")
        # Apply the saved selectors before returning choices, so a composer
        # opened on a new chat reflects the configured host defaults and any
        # dependent options for that model. No prompt is sent.
        binding = await self.create_session(EffectiveRunConfiguration(
            engine=self.id, cwd=root,
            model=self._launch_config.get("model"),
            mode=self._launch_config.get("mode"),
            config_options=dict(self._launch_config.get("config_options", {})),
        ), discovery=True)
        try:
            configuration = copy.deepcopy(self.session_configuration(binding))
            configuration.pop("session_id", None)
            # Reasoning choices may depend on the selected model. Discover
            # those branches once, without prompts, on this same connection.
            if include_models:
                variants = {}
                for model in configuration["models"]:
                    try:
                        selected = await self.configure_session(binding, EffectiveRunConfiguration(model=model))
                        variant = copy.deepcopy(selected)
                        variant.pop("session_id", None)
                        variants[model] = variant
                    except Exception as exc:
                        # Never advertise another model's reasoning options
                        # when a model's configuration cannot be inspected.
                        variants[model] = {"models": configuration["models"], "config_options": [],
                                           "refresh_error": str(exc)}
                configuration["model_configurations"] = variants
            return configuration
        finally:
            await self.close(binding)

    async def configure_session(self, binding: EngineSessionBinding,
                                cfg: EffectiveRunConfiguration) -> dict[str, Any]:
        state = self._sessions.get(binding.binding_id)
        if state is None:
            raise ValueError("no live ACP session")
        turn = state.get("turn_task")
        if turn is not None and not turn.done():
            raise ValueError("ACP session already has an active turn")
        requested = dict(cfg.config_options)
        if any(not isinstance(k, str) or not isinstance(v, (str, bool))
               for k, v in requested.items()):
            raise ValueError("config_options must map IDs to strings or booleans")
        for category, value in (("mode", cfg.mode), ("model", cfg.model)):
            if value is None:
                continue
            option = next((o for o in state.get("config_options", [])
                           if o.get("category") == category or o["id"] == category), None)
            if option:
                if option["id"] in requested and requested[option["id"]] != value:
                    raise ValueError(f"conflicting {category} selections")
                requested[option["id"]] = value
            elif category == "mode" and state.get("modes") and not state.get("config_options"):
                modes = state["modes"]
                if value not in [m["id"] for m in modes.get("availableModes", [])]:
                    raise ValueError(f"unsupported ACP mode: {value}")
                if value != modes.get("currentModeId"):
                    await asyncio.wait_for(state["conn"].set_session_mode(
                        session_id=binding.native_session_id, mode_id=value), self._startup_timeout)
                    modes["currentModeId"] = value
            else:
                raise ValueError(f"{self.id} did not advertise a {category} selector")
        # Re-read after every response: selecting a model can change the
        # available reasoning values and other dependent options.
        selector_ids = [o["id"] for o in state.get("config_options", [])
                        if o.get("category") in ("mode", "model") or o["id"] in ("mode", "model")]
        ordered_ids = [k for k in selector_ids if k in requested]
        ordered_ids.extend(k for k in requested if k not in ordered_ids)
        for config_id in ordered_ids:
            value = requested[config_id]
            option = next((o for o in state.get("config_options", []) if o["id"] == config_id), None)
            if option is None:
                raise ValueError(f"unknown ACP config option: {config_id}")
            if option.get("type") == "boolean":
                valid = isinstance(value, bool)
            else:
                valid = isinstance(value, str) and value in self._option_values(option)
            if not valid:
                raise ValueError(f"invalid value for ACP config option '{config_id}': {value}")
            if value == option.get("currentValue"):
                continue
            resp = await asyncio.wait_for(state["conn"].set_config_option(
                session_id=binding.native_session_id, config_id=config_id, value=value),
                self._startup_timeout)
            state["config_options"] = [_model_dump(o) for o in resp.config_options]
        self._remember_configuration(state)
        binding.extensions_snapshot.update(managed_task_id=cfg.tools.get("managed_task_id"), review_read_only=cfg.mode == "ask")
        configuration = self.session_configuration(binding)
        binding.extensions_snapshot["configuration"] = configuration
        self._emit(binding, "engine.session.info", configuration)
        return configuration

    def _mcp_servers(self, cfg: EffectiveRunConfiguration) -> list[Any]:
        out: list[Any] = []
        try:
            from acp.schema import (
                EnvVariable,
                HttpMcpServer,
                McpServerStdio,
                SseMcpServer,
            )
        except ImportError:
            return out
        for binding in cfg.mcp_bindings:
            name = str(binding.get("connection_id") or "mcp")
            # OAuth-bound connections are brokered TermX-side; the engine is
            # not handed credentials it can't consent to.
            if binding.get("auth_method") == "oauth":
                continue
            env_vars = [
                EnvVariable(name=str(k), value=str(v))
                for k, v in (binding.get("env") or {}).items()
            ]
            if binding.get("url") and binding.get("transport") == "sse":
                if not self._agent_capabilities.get("mcpCapabilities", {}).get("sse"):
                    raise ValueError(f"{self.id} did not advertise SSE MCP support")
                out.append(SseMcpServer(type="sse", name=name, url=str(binding["url"]), headers=[]))
            elif binding.get("url"):
                if not self._agent_capabilities.get("mcpCapabilities", {}).get("http"):
                    raise ValueError(f"{self.id} did not advertise HTTP MCP support")
                out.append(HttpMcpServer(type="http", name=name, url=str(binding["url"]), headers=[]))
            else:
                cmd = binding.get("command") or []
                if not cmd:
                    continue
                out.append(McpServerStdio(
                    name=name,
                    command=str(cmd[0]),
                    args=[str(a) for a in cmd[1:]],
                    env=env_vars,
                ))
        return out

    async def attach(self, binding: EngineSessionBinding,
                     cfg: EffectiveRunConfiguration | None = None) -> EngineSessionBinding:
        if binding.binding_id in self._sessions:
            old = self._sessions[binding.binding_id]
            if old["proc"].returncode is None:
                return old["binding"]
            await self.close(old["binding"])
        state: dict[str, Any] = {}
        ctx = self._spawn_for(state, binding.cwd)
        conn, proc = await ctx.__aenter__()
        state["ctx"] = ctx
        state["conn"] = conn
        state["proc"] = proc
        state["binding"] = binding
        state["client"]._session_id = binding.native_session_id
        self._sessions[binding.binding_id] = state
        self._bindings[binding.binding_id] = binding
        try:
            await self._initialize(conn)
            if not self._agent_capabilities.get("loadSession"):
                raise ValueError(f"{self.id} does not support session/load")
            response = await asyncio.wait_for(conn.load_session(
                cwd=binding.cwd or ".", session_id=binding.native_session_id,
                mcp_servers=self._mcp_servers(cfg) if cfg else []), self._startup_timeout)
            self._read_configuration(state, response)
        except BaseException:
            self._sessions.pop(binding.binding_id, None)
            self._bindings.pop(binding.binding_id, None)
            await state["client"].cleanup()
            await ctx.__aexit__(None, None, None)
            raise
        binding.status = "idle"
        binding.updated_at = time.time()
        self._sessions[binding.binding_id] = state
        self._bindings[binding.binding_id] = binding
        state["process_watch"] = asyncio.create_task(self._watch_process(state))
        return binding

    async def _watch_process(self, state: dict[str, Any]) -> None:
        await state["proc"].wait()
        binding = state["binding"]
        if binding.status == "closed":
            return
        state["process_lost"] = True
        self._cancel_permissions(binding)
        turn = state.get("turn_task")
        if turn is not None and not turn.done():
            turn.cancel()
            await asyncio.gather(turn, return_exceptions=True)
        binding.status = "lost"
        self._emit(binding, "engine.session.lost", {"message": "ACP agent process exited"})

    async def send(self, binding: EngineSessionBinding, prompt: str,
                   attachments: list[dict[str, Any]] | None = None) -> str | None:
        from acp.schema import TextContentBlock, ImageContentBlock
        from termx.engines.attachments import image_attachments
        images = image_attachments(attachments)
        state = self._sessions.get(binding.binding_id)
        if state is None:
            raise ValueError("no live ACP session")
        if state.get("turn_task") is not None and not state["turn_task"].done():
            raise ValueError("ACP session already has an active turn")
        if images and not self._agent_capabilities.get("promptCapabilities", {}).get("image", False):
            raise ValueError("ACP agent does not advertise image input capability")
        content = [TextContentBlock(type="text", text=prompt)]
        content.extend(ImageContentBlock(type="image",data=item['data'],mimeType=item['mime']) for item in images)
        binding.status = "active"
        state["cancel_requested"] = False
        state["prompt_started"] = asyncio.Event()
        self._emit(binding, "engine.turn.started", {"prompt_chars": len(prompt)})

        async def _run_prompt() -> None:
            try:
                state["prompt_started"].set()
                resp = await state["conn"].prompt(
                    session_id=binding.native_session_id,
                    prompt=content,
                )
                stop = str(getattr(resp, "stop_reason", "") or "")
                usage = _model_dump(getattr(resp, "usage", None))
                status = {"end_turn": "completed", "cancelled": "interrupted"}.get(stop, "failed")
                if state.get("cancel_requested"):
                    status = "interrupted"
                self._emit(binding, "engine.turn.completed", {
                    "status": status,
                    "stop_reason": stop,
                    "usage": usage,
                    **({"error": {"message": f"ACP agent stopped: {stop}. Check the agent's native configuration and run budget."}}
                       if status == "failed" else {}),
                })
            except asyncio.CancelledError:
                self._emit(binding, "engine.turn.completed", {
                    "status": "failed" if state.get("process_lost") else "interrupted",
                    **({"error": {"message": "ACP agent process exited during this turn"}}
                       if state.get("process_lost") else {}),
                })
                raise
            except Exception as exc:
                self._emit(binding, "engine.turn.completed",
                           {"status": "interrupted" if state.get("cancel_requested") else "failed",
                            "error": {"message": str(exc)}})
            finally:
                if binding.status != "closed":
                    binding.status = "idle"
                binding.updated_at = time.time()

        # session/prompt resolves at turn end — run it in a task so callers
        # (and the HTTP request) return once the turn has started.
        state["turn_task"] = asyncio.create_task(_run_prompt())
        return binding.native_session_id

    async def cancel(self, binding: EngineSessionBinding) -> None:
        state = self._sessions.get(binding.binding_id)
        if state is not None:
            state["cancel_requested"] = True
            self._cancel_permissions(binding)
            try:
                if state.get("prompt_started") is not None:
                    await asyncio.wait_for(state["prompt_started"].wait(), self._cancel_timeout)
                await asyncio.wait_for(state["conn"].cancel(
                    session_id=binding.native_session_id), self._cancel_timeout)
                turn = state.get("turn_task")
                if turn is not None:
                    await asyncio.wait_for(asyncio.shield(turn), self._cancel_timeout)
            except (Exception, asyncio.CancelledError):
                await self.close(binding)

    def _cancel_permissions(self, binding: EngineSessionBinding) -> None:
        for fut, _method, params in list(self._pending_decisions.values()):
            if params.get("binding_id") == binding.binding_id and not fut.done():
                fut.set_result(_cancelled_outcome())

    async def respond_approval(self, binding: EngineSessionBinding,
                               request_id: str, decision: str,
                               remember: bool = False,
                               content: Any = None) -> None:
        entry = self._pending_decisions.pop(request_id, None)
        if entry is None:
            raise KeyError(f"no pending engine request {request_id}")
        fut, _method, params = entry
        if params.get("binding_id") != binding.binding_id:
            self._pending_decisions[request_id] = entry
            raise PermissionError("approval belongs to another ACP session")
        if fut.done():
            return
        if _method == 'browser.review':
            from termx.engines.action_review import respond_review
            respond_review(self, binding, entry, decision in ('approve', 'approve_always'))
            return
        from acp.schema import AllowedOutcome, DeniedOutcome
        options = params.get("options") or []
        approved = decision in ("approve", "approve_always")
        option_id = None
        if approved and options:
            want = "allow_always" if remember else "allow_once"
            for opt in options:
                if opt.get("kind") == want or (remember and opt.get("kind") == "allow_once"):
                    option_id = opt.get("option_id") or opt.get("optionId")
                    if opt.get("kind") == want:
                        break
        if approved and option_id:
            fut.set_result(AllowedOutcome(outcome="selected", option_id=option_id))
        else:
            fut.set_result(DeniedOutcome(outcome="cancelled"))

    async def steer(self, binding: EngineSessionBinding, text: str) -> bool:
        # ACP v1 has no steering method; follow-ups are new serialized turns.
        return False

    async def close(self, binding: EngineSessionBinding) -> None:
        state = self._sessions.pop(binding.binding_id, None)
        binding.status = "closed"
        binding.updated_at = time.time()
        self._bindings.pop(binding.binding_id, None)
        if state is not None:
            self._cancel_permissions(binding)
            watch = state.get("process_watch")
            if watch is not None and watch is not asyncio.current_task():
                watch.cancel()
                await asyncio.gather(watch, return_exceptions=True)
            turn = state.get("turn_task")
            if turn is not None and not turn.done():
                turn.cancel()
                await asyncio.gather(turn, return_exceptions=True)
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
                await self._initialize(conn)
                if self._agent_capabilities.get("sessionCapabilities", {}).get("list") is None:
                    return []
                resp = await asyncio.wait_for(conn.list_sessions(), self._startup_timeout)
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
        "session": {"configOptions": {"boolean": {}}},
    })


def _child_env() -> dict[str, str]:
    from .env import engine_search_path
    env = dict(os.environ)
    env["PATH"] = os.pathsep.join(engine_search_path(env))
    env.pop("TERMX_PASSCODE", None)
    env.pop("TERMX_TOKEN", None)
    return env
