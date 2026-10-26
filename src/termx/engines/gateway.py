"""EngineGateway — binds native engine sessions to TermX tasks/events/approvals.

The gateway never re-implements an agent loop. A task row is the persistence
and streaming envelope for a native engine session: the adapter owns the
engine process, the gateway owns correlation (binding ↔ task ↔ conversation),
event persistence, and approval routing.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from .base import EngineAdapter
from .catalogue import EngineCatalogue
from .types import (
    EffectiveRunConfiguration,
    EngineDescriptor,
    EngineEvent,
    EngineSessionBinding,
)

EmitFn = Callable[[str, str, dict[str, Any]], dict[str, Any]]

_ENGINE_ACTIVE = {"active", "idle"}


class EngineGateway:
    def __init__(self, store: Any, emit: EmitFn) -> None:
        self._store = store
        self._emit = emit
        self._adapters: dict[str, EngineAdapter] = {}
        self.browser_service = None
        # set by AppState: resolve connection.<id> -> ConnectionDef-like obj
        self.mcp_resolver: Any = None
        self.credential_lookup: Any = None  # ref -> secret
        self.settings: Any = None
        # binding_id -> live binding (in-memory; engine_sessions table persists)
        self._bindings: dict[str, EngineSessionBinding] = {}
        # binding_id -> task_id (the task currently owning the native session)
        self._binding_task: dict[str, str] = {}
        # binding_id -> accumulated assistant text for result recording
        self._result_buffers: dict[str, list[str]] = {}
        # Unknown wire events await actual turn/start or RPC-response proof.
        # Raw content stays private and bounded until it has a source owner.
        self._pending_turn_events: dict[str, dict[str, Any]] = {}
        self._lock = asyncio.Lock()
        self.catalogue = EngineCatalogue(self)
        self._custom_runner_ids: set[str] = set()
        self._registry_runner_ids: set[str] = set()
        self._configured_registry_runner_ids: set[str] = set()

    # ------------------------------------------------------------ registry

    def register(self, adapter: EngineAdapter) -> None:
        if self.browser_service is not None:
            adapter.browser_service = self.browser_service
        self._adapters[adapter.descriptor().id] = adapter

    def set_browser_service(self, service) -> None:
        self.browser_service = service
        from termx.agent.policy import redact
        service.task_summary = lambda task_id: redact(str((self._store.get_task(task_id) or {}).get('prompt', '')))[:1600]
        from termx.agent.store import ACTIVE_STATUSES
        service.task_live = lambda task_id: (self._store.get_task(task_id) or {}).get('status') in ACTIVE_STATUSES - {'cancelling'}
        for adapter in self._adapters.values():
            adapter.browser_service = service

    def adapter(self, engine: str) -> EngineAdapter:
        try:
            return self._adapters[engine]
        except KeyError:
            raise KeyError(f"engine '{engine}' is not available") from None

    def engines(self) -> list[str]:
        return sorted(self._adapters)

    async def describe_all(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for name in self.engines():
            out.append((await self.catalogue.read(name))["descriptor"])
        return out

    async def probe(self, engine: str) -> dict[str, Any]:
        return (await self.catalogue.read(engine))["descriptor"]

    async def models(self, engine: str) -> list[str]:
        return (await self.catalogue.read(engine))["configuration"].get("models", [])

    async def configuration(self, engine: str, cwd: str | None = None) -> dict[str, Any]:
        entry = await self.catalogue.read(engine)
        return {**entry["configuration"], "refreshed_at": entry["refreshed_at"],
                "stale": entry["stale"], "refresh_error": entry["refresh_error"],
                "transport": entry["descriptor"]["transport"]}

    def register_configured_runners(self, prefs: Any) -> None:
        from .custom_acp import CustomAcpEngine
        from .acp_registry import RegistryAcpEngine
        for name, runner in prefs.acp_runners.items():
            if not runner.get("enabled", True):
                continue
            config = self._runner_config(runner, prefs.engines.get(name, {}))
            if config.get("registry_id"):
                adapter = RegistryAcpEngine(name, config.get("label") or name,
                    config["executable"], config.get("args", []),
                    environment=config.get("registry_env", {}),
                    event_sink=self.on_engine_event, approval_sink=self.approval_sink)
                self._registry_runner_ids.add(name)
                self._configured_registry_runner_ids.add(name)
            else:
                adapter = CustomAcpEngine(name, config, event_sink=self.on_engine_event,
                                          approval_sink=self.approval_sink)
                self._custom_runner_ids.add(name)
            self.register(adapter)

    def validate_runner_changes(self, runners: dict[str, Any]) -> None:
        removing = (self._custom_runner_ids | self._configured_registry_runner_ids) - {
            n for n, c in runners.items() if c.get("enabled", True)}
        for binding in self._bindings.values():
            if binding.engine in removing and binding.status == "active":
                raise ValueError(f"Stop the active {binding.engine} task before removing or disabling its runner")

    @staticmethod
    def _runner_config(runner: dict[str, Any], engine_defaults: dict[str, Any]) -> dict[str, Any]:
        config = {**runner, **engine_defaults}
        if runner.get("registry_id"):
            # Registry distribution metadata owns launch details. Per-engine
            # preferences can still set model/mode and ACP config options.
            for key in ("executable", "args", "env_names", "registry_env", "registry_id", "registry_version", "label"):
                if key in runner:
                    config[key] = runner[key]
        return config

    async def configure_runners(self, prefs: Any) -> None:
        enabled = {n for n, c in prefs.acp_runners.items() if c.get("enabled", True)}
        managed = self._custom_runner_ids | self._configured_registry_runner_ids
        for name in managed - enabled:
            job = self.catalogue.jobs.get(name)
            if job:
                job.cancel()
                await asyncio.gather(job, return_exceptions=True)
            await self.adapter(name).shutdown()
            self._adapters.pop(name)
            self.catalogue.entries.pop(name, None)
            self._registry_runner_ids.discard(name)
        self._custom_runner_ids &= enabled
        self._configured_registry_runner_ids &= enabled
        for name in enabled - self._custom_runner_ids - self._configured_registry_runner_ids:
            config = self._runner_config(prefs.acp_runners[name], prefs.engines.get(name, {}))
            if config.get("registry_id"):
                from .acp_registry import RegistryAcpEngine
                adapter = RegistryAcpEngine(name, config.get("label") or name,
                    config["executable"], config.get("args", []),
                    environment=config.get("registry_env", {}),
                    event_sink=self.on_engine_event, approval_sink=self.approval_sink)
                self.register(adapter)
                self._registry_runner_ids.add(name)
                self._configured_registry_runner_ids.add(name)
            else:
                from .custom_acp import CustomAcpEngine
                self.register(CustomAcpEngine(name, config, event_sink=self.on_engine_event,
                                              approval_sink=self.approval_sink))
                self._custom_runner_ids.add(name)
        for name in self.engines():
            adapter = self.adapter(name)
            runner = prefs.acp_runners.get(name, {})
            defaults = prefs.engines.get(name, {})
            if name in self._registry_runner_ids and not runner:
                config = {**getattr(adapter, "_launch_config", {}), **{
                    key: value for key, value in defaults.items()
                    if key in {"model", "mode", "config_options", "startup_timeout_s", "cancel_timeout_s", "catalogue_timeout_s"}
                }}
            else:
                config = self._runner_config(runner, defaults)
            configure = getattr(adapter, "configure_launch", None)
            if configure:
                if config != getattr(adapter, "_launch_config", {}):
                    self.catalogue.invalidate(name)
                configure(config)
                if name in enabled:
                    adapter.label = prefs.acp_runners[name].get("label") or name

    def task_configuration(self, task_id: str) -> dict[str, Any]:
        binding = self._binding_for_task(task_id)
        if binding is None:
            raise ValueError("no live engine session for this task")
        read = getattr(self.adapter(binding.engine), "session_configuration", None)
        if read is None:
            raise ValueError(f"{binding.engine} does not expose ACP configuration options")
        configuration = read(binding)
        cached = self.catalogue.entries.get(binding.engine, {}).get("configuration", {})
        return {**configuration, "model_configurations": cached.get("model_configurations", {})}

    # ------------------------------------------------------------ lifecycle

    def _resolve_mcp_bindings(
        self, mcp_connections: list[dict[str, Any]], *, project_id=None, authorize=None
    ) -> list[dict[str, Any]]:
        """Agent mcp_connections → per-session binding dicts for adapters.

        Only trusted+enabled defs are passed through — discovery alone never
        authorizes. Secret env values are resolved from CredentialStore at
        spawn time and never written to the binding's persisted payload.
        """
        out: list[dict[str, Any]] = []
        if self.mcp_resolver is None:
            return out
        for link in mcp_connections:
            if not isinstance(link,dict) or set(link)-{'connection','tools'}:
                raise ValueError('MCP preset bindings contain only connection and tool selections')
            conn_id = str(link.get("connection") or "")
            conn = self.mcp_resolver(conn_id) if conn_id else None
            if conn is None:
                raise ValueError('Selected MCP connection no longer exists')
            if not getattr(conn, "trusted", False) or not getattr(conn, "enabled", True):
                raise ValueError('Selected MCP connection is disabled or untrusted')
            from termx.mcp.scope import require_scope,projects,definition_digest
            require_scope(conn,project_id)
            if projects(conn):
                raise ValueError('Project-scoped MCP requires the Internal broker with live per-tool authority; direct native MCP cannot enforce revocation')
            if authorize:authorize(conn)
            elif projects(conn):raise PermissionError('Scoped MCP connection requires current caller authorization')
            approved=list(getattr(conn,'approved_tools',['*']))
            selected=link.get('tools')
            if selected is not None and (not isinstance(selected,list) or any(not isinstance(t,str) for t in selected)):
                raise ValueError('MCP tools must be a list')
            if '*' not in approved:
                if selected and '*' not in selected and not set(selected)<=set(approved):
                    raise PermissionError('MCP tool selection exceeds the approved tools')
                selected=approved if not selected or '*' in selected else selected
            env: dict[str, str] = {}
            headers: dict[str,str] = {}
            import os
            for name in getattr(conn, "env_names", []) or []:
                if name in os.environ:
                    env[name] = os.environ[name]
            if self.credential_lookup:
                for ref in getattr(conn, "secret_refs", []) or []:
                    value = self.credential_lookup(ref)
                    if value:
                        env[ref.rsplit(".", 1)[-1].upper()] = value
                for name,ref in getattr(conn,'header_secret_refs',{}).items():
                    value=self.credential_lookup(ref)
                    if value:headers[name]=value
            out.append({
                "connection_id": conn_id,
                "definition_digest":definition_digest(conn),
                "project_id":project_id,
                "transport": getattr(conn, "transport", "http"),
                "command": list(getattr(conn, "command", []) or []),
                "url": getattr(conn, "url", ""),
                "env": env,
                "headers":headers,
                "tools": selected,
                "auth_method": getattr(conn, "auth_method", "none"),
            })
        return out

    async def create_task(self, **kwargs: Any) -> dict[str, Any]:
        # Check/reuse/bind/start is atomic: a second request must never steal
        # event ownership from an already running native turn.
        async with self._lock:
            return await self._create_task(**kwargs)

    async def _create_task(
        self,
        *,
        prompt: str,
        cwd: str,
        engine: str,
        model: str | None = None,
        mode: str | None = None,
        workspace_mode: str | None = None,
        config_options: dict[str, str | bool] | None = None,
        custom_agent: dict[str, Any] | None = None,
        conversation_id: str | None = None,
        limits: dict[str, Any] | None = None,
        sandbox_profile: str = "agent",
        approval_mode: str = "standard",
        attachments: list[dict[str, Any]] | None = None,
        workflow: str | None = None,
        on_created: Callable[[str],None] | None = None,
        parent_id: str | None = None,
        dispatch_guard: Callable[[], None] | None = None,
        cleanup_guard: Callable[[], bool] | None = None,
        mcp_project_id: str | None = None,
        mcp_authorize: Any = None,
    ) -> dict[str, Any]:
        from termx.engines.attachments import image_attachments
        attachments = image_attachments(attachments)
        prompt = prompt.strip()
        if not prompt:
            raise ValueError("task prompt is required")
        root = Path(cwd).expanduser().resolve()
        if not root.is_dir():
            raise ValueError("project folder is not a directory")
        adapter = self.adapter(engine)
        if dispatch_guard:
            dispatch_guard()
        if workspace_mode is not None:
            if workspace_mode not in {'ask','agent'}:raise ValueError('Unknown workspace conversation mode')
            if engine in {'codex','claude'}:mode=workspace_mode
            else:
                # Host conversation mode is not an arbitrary ACP mode ID.
                # Ask requires the actual advertised Ask selector. Agent uses
                # the native default unless a separate native mode was chosen.
                mode='ask' if workspace_mode=='ask' else (None if mode=='agent' else mode)
        if workflow not in {None,'browser'}:
            raise ValueError('Unknown native workflow')
        if workflow=='browser' and (engine!='claude' or not getattr(adapter,'browser_service',None)):
            raise ValueError('This engine cannot enforce a broker-only browser session; choose Claude browser workflow or Internal')
        defaults = self.settings().engines.get(engine, {}) if self.settings else {}
        if config_options is not None and not isinstance(config_options, dict):
            raise ValueError("config_options must be an object")
        if limits is not None and not isinstance(limits, dict):
            raise ValueError("limits must be an object")
        limits = {**((custom_agent or {}).get("limits") or {}), **(limits or {})}

        instructions = ""
        if custom_agent:
            instructions = str(custom_agent.get("instructions") or "").strip()

        file_cfg = dict((custom_agent or {}).get("file") or {})
        skills = file_cfg.get("skills") or {}
        if file_cfg.get('mcp_connections') and adapter.capabilities().mcp_native == 'unsupported':
            raise ValueError(f'{engine} does not apply TermX per-session MCP bindings; use the Internal broker')
        mcp_bindings = self._resolve_mcp_bindings(file_cfg.get("mcp_connections") or [],project_id=mcp_project_id,authorize=mcp_authorize)
        mcp_snapshot=[{k:b.get(k) for k in ('connection_id','definition_digest','project_id','tools')} for b in mcp_bindings]

        # Resolved tool profile → cfg.tools (enforcement level varies by
        # engine and is labelled in capabilities, never overclaimed).
        from termx.agents.tools import resolve_tools
        tools_resolution = resolve_tools(
            SimpleNamespace(
                tools_omitted=file_cfg.get("tools_omitted", False),
                tools_mode=file_cfg.get("tools_mode", "explicit"),
                tools=file_cfg.get("tools") or (custom_agent or {}).get("tools") or [],
                toolsets=file_cfg.get("toolsets") or [],
                mcp_connections=file_cfg.get("mcp_connections") or [],
                deny_tools=file_cfg.get("deny_tools") or [],
            )
        )

        cfg = EffectiveRunConfiguration(
            engine=engine,
            model=model or (str(custom_agent["model"]) if custom_agent and custom_agent.get("model") else None) or defaults.get("model"),
            mode=mode or file_cfg.get("engine_mode") or defaults.get("mode"),
            config_options={**defaults.get("config_options", {}), **file_cfg.get("config_options", {}),
                            **(config_options or {})},
            agent_id=custom_agent["id"] if custom_agent else None,
            instructions=instructions,
            cwd=str(root),
            sandbox_profile=sandbox_profile,
            approval_mode=approval_mode,
            agent_profile_revision=str((custom_agent or {}).get("file_revision") or ""),
            skills=[{"id": s} for s in (skills.get("include") or [])],
            mcp_bindings=mcp_bindings,
            tools={
                "allowed": tools_resolution.tools if tools_resolution.tools_mode == "explicit" else [],
                "denied": file_cfg.get("deny_tools") or [],
                "tools_mode": tools_resolution.tools_mode,
                "review_required": tools_resolution.review_required,
                "limits": limits or {},
                "read_only":workspace_mode=="ask",
            },
            workflow=workflow,
        )
        if (cfg.mode or cfg.config_options) and not hasattr(adapter, "configure_session"):
            raise ValueError(f"{engine} does not expose ACP mode/config selectors")

        # Reuse a live native session for this conversation when one exists —
        # the engine owns conversation continuity natively.
        binding: EngineSessionBinding | None = None
        existing = (
            self._store.engine_session_for_conversation(conversation_id)
            if conversation_id
            else None
        )
        if existing and existing["engine"] == engine:
            previous_mcp=(existing.get('payload') or {}).get('extensions_snapshot',{}).get('mcp_scope',[])
            if previous_mcp != mcp_snapshot:
                raise ValueError('MCP connection scope/configuration changed; start a linked native session')
            previous_workflow=(existing.get('payload') or {}).get('extensions_snapshot',{}).get('workflow')
            if previous_workflow!=workflow:
                raise ValueError('Browser and coding tool availability cannot change within a native session; create a linked conversation')
            # Host defaults apply to new sessions. Omitted selections on a
            # follow-up preserve the live agent's current configuration.
            cfg.mode = mode or file_cfg.get("engine_mode")
            cfg.model = model or ((custom_agent or {}).get("model") or None)
            cfg.config_options = {**file_cfg.get("config_options", {}), **(config_options or {})}
            if Path(existing["cwd"]).resolve() != root:
                raise ValueError("conversation is bound to a different project folder")
            binding = self._bindings.get(existing["binding_id"])
            if binding is not None and binding.status in {"closed", "lost"}:
                binding = None
            if binding is not None and binding.status == "active":
                raise ValueError("conversation already has an active engine turn")
            if binding is None:
                binding = EngineSessionBinding.new(
                    engine, existing["native_session_id"],
                    cwd=existing["cwd"], conversation_id=conversation_id,
                    status="idle",
                )
                binding.binding_id = existing["binding_id"]
                binding.extensions_snapshot = (existing.get("payload") or {}).get("extensions_snapshot", {})
                if dispatch_guard:
                    dispatch_guard()
                try:
                    if hasattr(adapter, "configure_session"):
                        binding = await adapter.attach(binding, cfg=cfg)
                    else:
                        binding = await adapter.attach(binding)
                except Exception as exc:
                    self._store.update_engine_session(
                        existing["binding_id"], status="lost")
                    raise ValueError(f"{engine} session could not be resumed: {exc}") from exc
                await self._check_dispatch(dispatch_guard, adapter, binding)
            if binding is not None:
                binding.conversation_id = conversation_id
                self._bindings[binding.binding_id] = binding

        # Strict profiles must not launch when the engine cannot enforce them
        # (spec §6): an explicit tool allowlist requires at least gateway-level
        # enforcement; purely advisory engines get an advisory event instead.
        caps = adapter.capabilities()
        if tools_resolution.tools_mode == "explicit" and tools_resolution.tools:
            if caps.tools_filter == "unsupported":
                raise ValueError(
                    f"engine '{engine}' cannot enforce a tool allowlist "
                    f"(tools_filter=unsupported) — refusing strict profile"
                )
            if caps.tools_filter == "advisory":
                # Launch allowed but disclose: filtering is advisory-only.
                pass

        await self._check_dispatch(dispatch_guard, adapter, binding)
        task = self._store.create_task(
            prompt=prompt,
            cwd=str(root),
            provider_id=f"engine:{engine}",
            model=cfg.model or "",
            mode=cfg.mode or "agent",
            limits=limits or {},
            custom_agent_id=custom_agent["id"] if custom_agent else None,
            engine=engine,
            status="running",
            parent_id=parent_id,
        )
        task_id = task["id"]
        self._emit(task_id,"engine.configuration.resolved",cfg.as_dict())
        if on_created:on_created(task_id)
        if await self._abort_stopped_start(task_id, adapter, binding):
            return self._store.get_task(task_id, include_events=True) or task
        await self._check_dispatch(dispatch_guard, adapter, binding, task_id)
        cfg.tools['managed_task_id']=task_id
        if workflow=='browser':
            # Trusted identity is bound later by the explicit browser handoff.
            # This field is never accepted from model tool arguments.
            cfg.tools['browser_task_id']=task_id
        if attachments:
            import base64
            for item in attachments:
                self._store.save_artifact(task_id, 'upload', item['mime'], base64.b64decode(item['data']))

        if binding is None:
            try:
                if dispatch_guard:
                    dispatch_guard()
                binding = await adapter.create_session(cfg)
            except Exception as exc:
                if await self._abort_stopped_start(task_id, adapter):
                    return self._store.get_task(task_id, include_events=True) or task
                self._store.update_task(task_id, status="failed", error=str(exc))
                self._emit(task_id, "task.failed", {"message": str(exc)})
                raise ValueError(f"{engine} session failed to start: {exc}") from exc
            # Stop can arrive before a new binding exists. Its late session
            # must be closed rather than enrolled and sent the cancelled prompt.
            if await self._abort_stopped_start(task_id, adapter, binding):
                return self._store.get_task(task_id, include_events=True) or task
            await self._check_dispatch(dispatch_guard, adapter, binding, task_id)
            binding.conversation_id = conversation_id
            binding.extensions_snapshot['mcp_scope']=mcp_snapshot
            self._bindings[binding.binding_id] = binding
            self._store.save_engine_session(
                binding.binding_id,
                task_id=task_id,
                engine=engine,
                native_session_id=binding.native_session_id,
                conversation_id=conversation_id,
                cwd=binding.cwd,
                status=binding.status,
                payload=binding.as_dict(),
            )
        else:
            previous_task_id = self._binding_task.get(binding.binding_id) or (existing or {}).get('task_id')
            if previous_task_id and (not binding.extensions_snapshot.get('termx_dispatch_id')
                    or binding.native_turn_id in (binding.extensions_snapshot.get('termx_turn_owners') or {})):
                self._remember_turn(binding, previous_task_id, binding.native_turn_id)
            self._binding_task[binding.binding_id] = task_id
            self._store.update_task(task_id, engine_session_id=binding.binding_id,
                                    engine_native_id=binding.native_session_id)
            configure = getattr(adapter, "configure_session", None)
            if configure:
                if await self._abort_stopped_start(task_id, adapter, binding):
                    return self._store.get_task(task_id, include_events=True) or task
                await self._check_dispatch(dispatch_guard, adapter, binding, task_id)
                try:
                    await configure(binding, cfg)
                except Exception as exc:
                    if await self._abort_stopped_start(task_id, adapter, binding):
                        return self._store.get_task(task_id, include_events=True) or task
                    self._store.update_task(task_id, status="failed", error=str(exc))
                    self._store.update_engine_session(binding.binding_id, task_id=task_id,
                                                      status="idle", payload=binding.as_dict())
                    self._emit(task_id, "task.failed", {"message": str(exc)})
                    raise ValueError(f"{engine} configuration failed: {exc}") from exc
            if await self._abort_stopped_start(task_id, adapter, binding):
                return self._store.get_task(task_id, include_events=True) or task
            await self._check_dispatch(dispatch_guard, adapter, binding, task_id)
            self._store.update_engine_session(
                binding.binding_id, task_id=task_id, status="active",
                payload=binding.as_dict())

        self._binding_task[binding.binding_id] = task_id
        self._store.update_task(
            task_id,
            engine_session_id=binding.binding_id,
            engine_native_id=binding.native_session_id,
        )
        read_config = getattr(adapter, "session_configuration", None)
        configuration = None
        if read_config:
            configuration = read_config(binding)
            # Store the selected native IDs, including agent defaults, so
            # task metadata agrees with what actually ran.
            for option in configuration.get("config_options", []):
                if option.get("category") == "model" or option.get("id") == "model":
                    self._store.update_task(task_id, model=option["currentValue"])
                if option.get("category") == "mode" or option.get("id") == "mode":
                    self._store.update_task(task_id, mode=option["currentValue"])
            if not configuration.get("config_options") and configuration.get("modes"):
                self._store.update_task(task_id, mode=configuration["modes"]["currentModeId"])
        task = self._store.get_task(task_id) or task
        self._emit(task_id, "task.created", {"task": task})
        self._emit(task_id, "engine.session.bound", {
            "engine": engine,
            "binding_id": binding.binding_id,
            "native_session_id": binding.native_session_id,
            "resumed": existing is not None,
        })
        if configuration is not None:
            self._emit(task_id, "engine.session.info", configuration)
        if await self._abort_stopped_start(task_id, adapter, binding):
            return self._store.get_task(task_id, include_events=True) or task
        await self._check_dispatch(dispatch_guard, adapter, binding, task_id)
        self._prepare_turn_dispatch(binding, task_id)
        try:
            text = prompt if not instructions else (
                f'You are the "{custom_agent["name"]}" agent. Follow these '
                f"instructions:\n{instructions}\n\nTask: {prompt}"
            )
            if attachments:
                await adapter.send(binding, text, attachments=attachments)
            else:
                await adapter.send(binding, text)
        except Exception as exc:
            self._pending_turn_events.pop(binding.binding_id, None)
            if await self._abort_stopped_start(task_id, adapter, binding):
                return self._store.get_task(task_id, include_events=True) or task
            self._store.update_task(task_id, status="failed", error=str(exc))
            self._emit(task_id, "task.failed", {"message": str(exc)})
            raise ValueError(f"{engine} turn failed to start: {exc}") from exc
        if binding.extensions_snapshot.get('termx_correlation_failed') == task_id:
            await self._abort_stopped_start(task_id, adapter, binding)
            return self._store.get_task(task_id, include_events=True) or task
        await self._check_dispatch(dispatch_guard, adapter, binding, task_id, cleanup_guard=cleanup_guard)
        return self._store.get_task(task_id, include_events=True) or task

    async def _check_dispatch(self, guard: Callable[[], None] | None,
                              adapter: EngineAdapter,
                              binding: EngineSessionBinding | None = None,
                              task_id: str | None = None, *,
                              cleanup_guard: Callable[[], bool] | None = None) -> None:
        if guard is None:
            return
        try:
            guard()
        except Exception:
            if cleanup_guard is not None:
                should_cleanup = True
                try:
                    should_cleanup = cleanup_guard() is not False
                except Exception:
                    # A failed ownership check must remain fail-closed.
                    pass
                if not should_cleanup:
                    # Send was attempted and the user now owns its authorized
                    # work. The old coordinator cannot close that human turn.
                    raise
            # A native resume/configuration can yield after the coordinator or
            # project grant changes. Stop only this attempt and never replay it.
            current = self._store.get_task(task_id) if task_id else None
            if current and current['status'] in {'running', 'planning', 'awaiting_approval'}:
                message = 'Native dispatch authority changed. This attempt was stopped; inspect its task before retrying.'
                self._store.update_task(task_id, status='failed', error=message)
                self._emit(task_id, 'task.failed', {'message':message})
            await self._abort_stopped_start(task_id, adapter, binding)
            raise

    async def _abort_stopped_start(self, task_id: str | None, adapter: EngineAdapter,
                                  binding: EngineSessionBinding | None = None) -> bool:
        """Recheck durable Stop after native startup/configuration yields.

        Cancellation may have no binding to interrupt yet. A session returned
        later belongs to that stopped attempt and must never receive its prompt.
        Cleanup failures remain visible; they never restore dispatch authority.
        """
        task = self._store.get_task(task_id) if task_id else None
        if task and task['status'] in {'running', 'planning', 'awaiting_approval'}:
            return False
        failure = None
        if binding is not None:
            live_binding = self._bindings.get(binding.binding_id)
            record = self._store.get_engine_session(binding.binding_id)
            owner_ids = {self._binding_task.get(binding.binding_id), (record or {}).get('task_id')} - {None,task_id}
            foreign_active = any((self._store.get_task(identifier) or {}).get('status') in {'running','planning','awaiting_approval','cancelling','paused','recovering','recovery_confirmation_required'} for identifier in owner_ids)
            if live_binding is not None and live_binding is not binding or foreign_active:
                binding = None  # Cleanup must not touch a different live turn.
        if binding is not None:
            if binding.status != 'closed':
                try:
                    await adapter.cancel(binding)
                except Exception as exc:
                    failure = exc
                if binding.status != 'closed':
                    try:
                        await adapter.close(binding)
                    except Exception as exc:
                        failure = exc
                    else:
                        failure = None  # A confirmed close also settles a failed interrupt.
                else:
                    failure = None
            self._bindings.pop(binding.binding_id, None)
            self._binding_task.pop(binding.binding_id, None)
            self._result_buffers.pop(binding.binding_id, None)
            self._pending_turn_events.pop(binding.binding_id, None)
            record = self._store.get_engine_session(binding.binding_id)
            if record:
                self._store.update_engine_session(binding.binding_id, status='lost', payload=binding.as_dict())
        current = self._store.get_task(task_id) if task_id else None
        if current and current['status'] == 'cancelling':
            self._store.update_task(task_id, status='cancelled', error='Cancelled before the native turn started')
            self._emit(task_id, 'task.cancelled', {'message':'Cancelled before the native turn started'})
        if failure is not None:
            message = 'Task stopped before dispatch; native session cleanup could not be confirmed. Restart the engine before continuing.'
            if current:
                self._emit(task_id, 'engine.session.cleanup.failed', {'message':message, 'error_type':type(failure).__name__})
            raise ValueError(message) from failure
        return True

    async def cancel(self, task_id: str) -> dict[str, Any] | None:
        task = self._store.get_task(task_id)
        if task is None or task.get("engine") in (None, "internal"):
            return None
        if task["status"] in {"completed", "failed", "cancelled"}:
            return task
        binding = self._binding_for_task(task_id)
        if binding is not None:
            self._store.update_task(task_id, status="cancelling")
            self._emit(task_id, "task.status", {"status": "cancelling"})
            await self.adapter(binding.engine).cancel(binding)
            if binding.status == "closed":
                self._store.update_engine_session(binding.binding_id, status="lost")
                self._bindings.pop(binding.binding_id, None)
        else:
            self._store.update_task(
                task_id, status="cancelled", error="Cancelled by user")
            self._emit(task_id, "task.cancelled", {"message": "Cancelled by user"})
        return self._store.get_task(task_id)

    async def steer(self, task_id: str, message: str) -> dict[str, Any] | None:
        task = self._store.get_task(task_id)
        if task is None or task.get("engine") in (None, "internal"):
            return None
        binding = self._binding_for_task(task_id)
        if binding is None:
            raise ValueError("no live engine session for this task")
        ok = await self.adapter(binding.engine).steer(binding, message)
        if not ok:
            raise ValueError(f"{binding.engine} does not support steering right now")
        self._emit(task_id, "task.steered", {"message": message})
        return self._store.get_task(task_id)

    async def resolve_approval(
        self,
        task_id: str,
        approval_id: str,
        decision: str,
        *,
        remember: str | None = None,
        content: Any = None,
    ) -> dict[str, Any] | None:
        """Route a resolved approval to the engine's pending server request.

        Returns None when the task is not engine-backed (caller falls back to
        the internal manager path).
        """
        task = self._store.get_task(task_id)
        if task is None or task.get("engine") in (None, "internal"):
            return None
        approval = self._store.get_approval(approval_id)
        if approval is None or approval["task_id"] != task_id:
            raise KeyError(approval_id)
        payload = approval.get("payload") or {}
        token = payload.get("engine_request_id")
        binding = self._binding_for_task(task_id)
        if task['status'] not in {'running','awaiting_approval'} or binding is None or binding.status in {'closed','lost'} or not token:
            raise ValueError('This native approval no longer has an active engine request. Inspect the task and resume with an explicit new instruction.')
        resolved = self._store.resolve_approval(
            approval_id, "approved" if decision in {"approved", "approve"} else decision)
        self._emit(task_id, "approval.resolved", {"approval": resolved})
        if binding is not None and token:
            wire_decision = {
                "approved": "approve",
                "approve": "approve",
                "denied": "deny",
                "deny": "deny",
                "cancel": "cancel",
            }.get(decision, "deny")
            try:
                await self.adapter(binding.engine).respond_approval(
                    binding, token, wire_decision,
                    remember=remember in {"session", "always", "project"},
                    content=content,
                )
            except (Exception, asyncio.CancelledError) as exc:
                expired = isinstance(exc, KeyError)
                message = ('This native permission request expired before the decision could be delivered. Inspect the task before sending a new instruction.'
                    if expired else 'Native permission delivery could not be confirmed. The decision will not be sent again; inspect the engine and actual task outcome before continuing.')
                # The durable approval row records the user's choice, not proof
                # the native process received it. Never revert that row or
                # retransmit an ambiguous decision on a repeated HTTP request.
                self._emit(task_id, 'engine.approval.delivery.failed', {
                    'approval_id':approval_id,'decision':resolved['status'],
                    'delivery':'expired' if expired else 'uncertain','message':message})
                self._emit(task_id, 'task.recovery.blocked', {'message':message})
                current = self._store.get_task(task_id)
                if current and current['status'] in {'running','planning','awaiting_approval'}:
                    self._store.update_task(task_id, status='failed', error=message)
                    self._emit(task_id, 'task.failed', {'message':message})
                    await self._abort_stopped_start(task_id, self.adapter(binding.engine), binding)
                elif current and current['status'] == 'cancelling':
                    await self._abort_stopped_start(task_id, self.adapter(binding.engine), binding)
                # A final outcome, or a binding reassigned to a newer user turn,
                # remains authoritative even when the old response is lost.
                if isinstance(exc, asyncio.CancelledError):
                    raise
                raise ValueError(message) from exc
            current = self._store.get_task(task_id)
            if (current and current['status'] == 'awaiting_approval'
                    and self._binding_task.get(binding.binding_id) == task_id
                    and binding.status not in {'closed','lost'}
                    and not any(item['status'] == 'pending' for item in self._store.approvals(task_id))):
                self._store.update_task(task_id, status='running')
                self._emit(task_id, 'task.status', {'status':'running'})
        return resolved

    # ----------------------------------------------------------- event flow

    @staticmethod
    def _actual_turn_id(payload: dict[str, Any], native: dict[str, Any] | None = None) -> str | None:
        turn = payload.get('turn')
        identifier = ((native or {}).get('turn_id') or payload.get('turnId')
                      or (turn.get('id') if isinstance(turn, dict) else None))
        return identifier if isinstance(identifier, str) and identifier else None

    def _persist_turn_owners(self, binding: EngineSessionBinding) -> None:
        if self._store.get_engine_session(binding.binding_id):
            self._store.update_engine_session(binding.binding_id, payload=binding.as_dict())

    def _remember_turn(self, binding: EngineSessionBinding, task_id: str,
                       turn_id: str | None) -> None:
        if not isinstance(turn_id, str) or not turn_id:
            return
        owners = binding.extensions_snapshot.setdefault('termx_turn_owners', {})
        # Turn IDs are native facts. Once observed, a later task cannot claim
        # one, even after the same session binding is reused or restored.
        if turn_id not in owners:
            owners[turn_id] = task_id
            self._persist_turn_owners(binding)

    def _prepare_turn_dispatch(self, binding: EngineSessionBinding, task_id: str) -> None:
        binding.extensions_snapshot['termx_dispatch_id'] = task_id
        binding.extensions_snapshot.pop('termx_correlation_failed', None)
        binding.native_turn_id = None
        self._result_buffers.pop(binding.binding_id, None)
        self._pending_turn_events.pop(binding.binding_id, None)
        self._persist_turn_owners(binding)

    def _quarantine_turn_event(self, binding_id: str, task_id: str, event: EngineEvent) -> None:
        pending = self._pending_turn_events.setdefault(binding_id, {
            'task_id':task_id, 'events':[], 'bytes':0, 'overflow':False})
        if pending['task_id'] != task_id:
            return
        size = len(json.dumps({'payload':event.payload, 'native':event.native}, default=str).encode())
        if pending['overflow'] or len(pending['events']) >= 128 or pending['bytes'] + size > 1024 * 1024:
            pending['overflow'] = True
            return
        pending['events'].append(event)
        pending['bytes'] += size

    def _flush_turn_events(self, binding_id: str, task_id: str, turn_id: str) -> None:
        pending = self._pending_turn_events.pop(binding_id, None)
        if not pending or pending['task_id'] != task_id:
            return
        if pending['overflow']:
            self._fail_turn_correlation(binding_id, task_id)
            return
        for buffered in pending['events']:
            if self._actual_turn_id(buffered.payload, buffered.native) == turn_id:
                self.on_engine_event(binding_id, buffered)

    def _fail_turn_correlation(self, binding_id: str, task_id: str) -> None:
        message = 'Native turn correlation exceeded its safe event buffer. The request was not replayed; inspect the actual engine outcome before continuing.'
        current = self._store.get_task(task_id)
        if current and current['status'] in {'running','planning','awaiting_approval'}:
            self._store.update_task(task_id, status='failed', error=message)
            self._emit(task_id, 'task.failed', {'message':message})
            binding = self._bindings.get(binding_id)
            if binding:
                binding.extensions_snapshot['termx_correlation_failed'] = task_id
                self._persist_turn_owners(binding)
            self._emit(task_id, 'task.recovery.blocked', {'message':message})

    def _event_task(self, binding_id: str, task_id: str, event: EngineEvent) -> tuple[str, bool]:
        """Correlate native turn IDs or captured host attempts before mutation.

        A vendor turn ID is never synthesized from a cached binding. Drivers
        without wire turn IDs may supply the host dispatch captured by their
        own prompt coroutine; unsolicited session-only chunks remain unscoped.
        """
        binding = self._bindings.get(binding_id)
        if binding is None:
            return task_id, True
        turn_id = self._actual_turn_id(event.payload, event.native)
        dispatch_id = (event.native or {}).get('dispatch_id')
        owners = binding.extensions_snapshot.get('termx_turn_owners') or {}
        owner = owners.get(turn_id) if turn_id else None
        if owner:
            return owner, owner == task_id and (not dispatch_id or dispatch_id == task_id)
        if dispatch_id and dispatch_id != task_id:
            return str(dispatch_id), False
        turn_scoped = (event.type.startswith(('engine.turn.', 'engine.message.', 'engine.item.',
                        'engine.tool.', 'engine.reasoning.', 'engine.command.'))
                       or event.type in {'engine.plan.updated', 'engine.diff.updated'})
        if turn_id:
            # Only a previously correlated actual ID is authority here. An
            # adapter's cached ID may itself have come from an unknown replay.
            expected = next((identifier for identifier, owner in owners.items() if owner == task_id), None)
            if expected and expected != turn_id:
                return task_id, False
            if (binding.extensions_snapshot.get('termx_dispatch_id') == task_id
                    and turn_scoped and event.type not in {'engine.turn.started','engine.turn.bound'}
                    and not dispatch_id):
                self._quarantine_turn_event(binding_id, task_id, event)
                return task_id, False
            self._remember_turn(binding, task_id, turn_id)
            binding.native_turn_id = turn_id
            self._persist_turn_owners(binding)
        elif turn_scoped and any(owner == task_id for owner in owners.values()) and not dispatch_id:
            # A notification without its actual turn cannot borrow the current
            # native ID. Keep it inspectable as an uncorrelated diagnostic.
            return task_id, False
        return task_id, True

    def _binding_for_task(self, task_id: str) -> EngineSessionBinding | None:
        record = self._store.engine_session_for_task(task_id)
        if record is None:
            return None
        return self._bindings.get(record["binding_id"])

    def on_engine_event(self, binding_id: str, event: EngineEvent) -> None:
        """Adapter event sink — persist, update state, fan out to subscribers."""
        task_id = self._binding_task.get(binding_id)
        if task_id is None:
            # session created before task row link or orphaned — still record
            record = self._store.get_engine_session(binding_id)
            task_id = record["task_id"] if record else None
        if task_id is None:
            return
        current_task_id = task_id
        task_id, applicable = self._event_task(binding_id, task_id, event)
        payload = dict(event.payload)
        if event.native:
            payload["native"] = event.native
        if not applicable:
            if task_id != current_task_id and self._store.get_task(task_id):
                self._emit(task_id, event.type, payload)
            else:
                self._emit(current_task_id, 'engine.event.uncorrelated', {
                    'type':event.type, 'turn_id':self._actual_turn_id(event.payload,event.native),
                    'payload_bytes':len(json.dumps(payload, default=str).encode()),
                    'message':'Native activity awaits an actual turn correlation or belongs to an unmatched turn; ambiguous content was withheld and did not change the current task.'})
            return
        self._emit(task_id, event.type, payload)

        if event.type == 'engine.turn.correlation.failed':
            self._pending_turn_events.pop(binding_id, None)
            self._fail_turn_correlation(binding_id, task_id)
        elif event.type == "engine.message.delta":
            delta = event.payload.get("delta") or event.payload.get("text") or ""
            if not event.payload.get("parent_call_id"):
                self._result_buffers.setdefault(binding_id, []).append(str(delta))
        elif event.type == "engine.session.bound":
            # Some engines learn their durable ID only after their first
            # prompt. Persist it immediately so restart/resume never sends
            # Claude's temporary pending_* placeholder back to the engine.
            binding = self._bindings.get(binding_id)
            native_id = event.payload.get("native_session_id")
            if binding is not None and native_id == binding.native_session_id:
                self._store.update_engine_session(binding_id, native_session_id=native_id,
                                                  payload=binding.as_dict())
                self._store.update_task(task_id, engine_native_id=native_id)
        elif event.type == "engine.item.completed":
            item = event.payload.get("item") or {}
            if item.get("type") == "agentMessage" and item.get("text") and not event.payload.get("parent_call_id"):
                buf = self._result_buffers.setdefault(binding_id, [])
                if not buf or not "".join(buf).strip():
                    buf.append(str(item["text"]))
        elif event.type == "engine.turn.completed":
            self._finish_turn(binding_id, task_id, event.payload)
        elif event.type=='engine.approval.expired':
            settled_request = False
            for approval in self._store.approvals(task_id):
                if approval['status']=='pending' and approval['payload'].get('engine_request_id')==event.payload.get('request_id'):
                    resolved=self._store.resolve_approval(approval['id'],'denied')
                    self._emit(task_id,'approval.resolved',{'approval':resolved})
                    settled_request = True
            task = self._store.get_task(task_id)
            if settled_request and task and task["status"] == "awaiting_approval" and not any(
                approval["status"] == "pending" for approval in self._store.approvals(task_id)
            ):
                self._store.update_task(task_id,status='running')
                self._emit(task_id,'task.status',{'status':'running'})
        elif event.type == "engine.session.lost":
            self._store.update_engine_session(binding_id, status="lost")
            task = self._store.get_task(task_id)
            if task and task["status"] in {"running", "planning", "awaiting_approval", "cancelling"}:
                self._finish_turn(binding_id, task_id, {
                    "status": "failed",
                    "error": {"message": "Engine connection closed; session may be resumable"},
                })
        if event.type in {'engine.turn.started','engine.turn.bound'}:
            turn_id = self._actual_turn_id(event.payload, event.native)
            if turn_id:
                self._flush_turn_events(binding_id, task_id, turn_id)

    def _finish_turn(self, binding_id: str, task_id: str,
                     payload: dict[str, Any]) -> None:
        if self._binding_task.get(binding_id) not in {None, task_id}:
            return  # A late old turn must not clear a newer turn's buffer.
        pending = self._pending_turn_events.get(binding_id)
        if pending and pending['task_id'] == task_id:
            self._pending_turn_events.pop(binding_id, None)
        current = self._store.get_task(task_id)
        if current is None or current['status'] in {'completed', 'failed', 'cancelled'}:
            # The raw engine event is already persisted by on_engine_event.
            # A delayed/duplicate completion cannot replace a confirmed outcome
            # or produce another task terminal event for orchestration listeners.
            self._result_buffers.pop(binding_id, None)
            return
        status = str(payload.get("status") or "completed")
        final_text = payload.get("result")
        result = "".join(self._result_buffers.pop(binding_id, [])) or (final_text if isinstance(final_text, str) else None)
        for approval in self._store.approvals(task_id):
            if approval["status"] == "pending":
                resolved = self._store.resolve_approval(approval["id"], "denied")
                self._emit(task_id, "approval.resolved", {"approval": resolved})
        if status == "completed":
            self._store.update_task(task_id, status="completed", result=result)
            self._emit(task_id, "task.completed", {"result": result})
        elif status == "interrupted":
            self._store.update_task(
                task_id, status="cancelled", error="Interrupted", result=result)
            self._emit(task_id, "task.cancelled", {"message": "Interrupted"})
        else:
            err = payload.get("error") or {}
            message = err.get("message") if isinstance(err, dict) else str(err)
            self._store.update_task(
                task_id, status="failed", error=message or "engine turn failed",
                result=result)
            self._emit(task_id, "task.failed",
                       {"message": message or "engine turn failed"})
        record = self._store.get_engine_session(binding_id)
        if record:
            binding = self._bindings.get(binding_id)
            lost = record["status"] == "lost" or (binding is not None and binding.status in {"lost", "closed"})
            self._store.update_engine_session(binding_id, status="lost" if lost else "idle")

    async def approval_sink(self, binding_id: str, token: str, method: str,
                            params: dict[str, Any], kind: str) -> str:
        """Create the TermX approval row backing a native server→client request."""
        task_id = self._binding_task.get(binding_id)
        if task_id is None:
            raise ValueError("no task bound to engine session")
        task = self._store.get_task(task_id)
        binding = self._bindings.get(binding_id)
        if not task or task['status'] not in {'running','awaiting_approval'} or binding is None or binding.status in {'closed','lost'}:
            raise ValueError('Native permission request belongs to a stopped or unavailable turn')
        turn_id = self._actual_turn_id(params)
        owner = (binding.extensions_snapshot.get('termx_turn_owners') or {}).get(turn_id) if turn_id else None
        if turn_id and ((owner and owner != task_id)
                or (binding.native_turn_id and binding.native_turn_id != turn_id)):
            raise ValueError('Native permission request belongs to an earlier turn; it cannot grant access to the current task')
        approval = self._store.create_approval(
            task_id,
            kind,
            {
                "engine_request_id": token,
                "engine_method": method,
                "title": _approval_title(method, params),
                "request": params,
                "consequence": "Sent to the engine process when resolved",
            },
        )
        if task['status'] != 'awaiting_approval':
            self._store.update_task(task_id,status='awaiting_approval')
            self._emit(task_id, 'task.status', {'status':'awaiting_approval'})
        self._emit(task_id, "approval.requested", {"approval": approval})
        return str(approval["id"])

    # ------------------------------------------------------------ lifecycle

    def recover(self) -> int:
        """On host start: live bindings are gone — mark them resumable-lost."""
        affected = set()
        for record in self._store.list_engine_sessions():
            if record["status"] in _ENGINE_ACTIVE:
                self._store.update_engine_session(
                    record["binding_id"], status="lost")
                affected.add(record['binding_id'])
            # A persisted approval cannot answer a process from an earlier host
            # lifetime, even if its task was already terminal when the host died.
            self._deny_lost_native_approvals(record['task_id'])
        from termx.agent.store import ACTIVE_STATUSES
        before = None
        while True:
            page = self._store.task_page(limit=100, before=before, statuses=tuple(ACTIVE_STATUSES))
            if not page:
                break
            before = (page[-1]['updated_at'], page[-1]['id'])
            for task in page:
                if task.get('engine') in (None,'internal'):
                    continue
                record = self._store.engine_session_for_task(task['id'])
                cancelling = task['status'] == 'cancelling'
                message = ('Cancellation was recorded before the host restarted. No native work was replayed.'
                    if cancelling else 'Host restarted during this turn; resume the native session with a new task'
                    if record else 'Host restarted during native startup. Inspect the engine before sending a new instruction; no work was replayed.')
                self._deny_lost_native_approvals(task['id'])
                self._store.update_task(task['id'], status='cancelled' if cancelling else 'failed', error=message)
                self._emit(task['id'], 'task.cancelled' if cancelling else 'task.failed', {'message':message})
                affected.add(record['binding_id'] if record else 'task:'+task['id'])
        return len(affected)

    def _deny_lost_native_approvals(self, task_id: str) -> None:
        for approval in self._store.approvals(task_id):
            if approval['status'] == 'pending':
                resolved = self._store.resolve_approval(approval['id'], 'denied')
                self._emit(task_id, 'approval.resolved', {'approval':resolved})

    async def shutdown(self, *, strict: bool = False) -> None:
        await self.catalogue.stop()
        registry = getattr(self, "acp_registry", None)
        if registry:
            await registry.stop()
        failed = False
        for adapter in self._adapters.values():
            try:
                await adapter.shutdown()
            except Exception:
                failed = True
        if strict and failed:
            raise RuntimeError('Native execution shutdown failed; private observation barriers remain active')


def _approval_title(method: str, params: dict[str, Any]) -> str:
    if method == "item/commandExecution/requestApproval":
        cmd = params.get("command")
        if isinstance(cmd, list):
            cmd = " ".join(str(c) for c in cmd)
        return f"Approve command: {str(cmd or '(command)')[:120]}"
    if method == "item/fileChange/requestApproval":
        return "Approve file changes"
    if method == "item/permissions/requestApproval":
        return "Approve requested permissions"
    if method == "mcpServer/elicitation/request":
        return f"MCP server request: {params.get('serverName', '')}"
    return "Engine approval requested"
