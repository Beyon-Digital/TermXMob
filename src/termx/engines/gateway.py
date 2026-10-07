"""EngineGateway — binds native engine sessions to TermX tasks/events/approvals.

The gateway never re-implements an agent loop. A task row is the persistence
and streaming envelope for a native engine session: the adapter owns the
engine process, the gateway owns correlation (binding ↔ task ↔ conversation),
event persistence, and approval routing.
"""

from __future__ import annotations

import asyncio
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
        self, mcp_connections: list[dict[str, Any]]
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
            conn_id = str(link.get("connection") or "")
            conn = self.mcp_resolver(conn_id) if conn_id else None
            if conn is None:
                continue
            if not getattr(conn, "trusted", False) or not getattr(conn, "enabled", True):
                continue  # untrusted defs cannot reach an engine session
            env: dict[str, str] = {}
            import os
            for name in getattr(conn, "env_names", []) or []:
                if name in os.environ:
                    env[name] = os.environ[name]
            if self.credential_lookup:
                for ref in getattr(conn, "secret_refs", []) or []:
                    value = self.credential_lookup(ref)
                    if value:
                        env[ref.rsplit(".", 1)[-1].upper()] = value
            out.append({
                "connection_id": conn_id,
                "transport": getattr(conn, "transport", "http"),
                "command": list(getattr(conn, "command", []) or []),
                "url": getattr(conn, "url", ""),
                "env": env,
                "tools": link.get("tools"),
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
        config_options: dict[str, str | bool] | None = None,
        custom_agent: dict[str, Any] | None = None,
        conversation_id: str | None = None,
        limits: dict[str, Any] | None = None,
        sandbox_profile: str = "agent",
        approval_mode: str = "standard",
        attachments: list[dict[str, Any]] | None = None,
        workflow: str | None = None,
        on_created: Callable[[str],None] | None = None,
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
        mcp_bindings = self._resolve_mcp_bindings(file_cfg.get("mcp_connections") or [])

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
                try:
                    if hasattr(adapter, "configure_session"):
                        binding = await adapter.attach(binding, cfg=cfg)
                    else:
                        binding = await adapter.attach(binding)
                except Exception as exc:
                    self._store.update_engine_session(
                        existing["binding_id"], status="lost")
                    raise ValueError(f"{engine} session could not be resumed: {exc}") from exc
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
        )
        task_id = task["id"]
        if on_created:on_created(task_id)
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
                binding = await adapter.create_session(cfg)
            except Exception as exc:
                self._store.update_task(task_id, status="failed", error=str(exc))
                self._emit(task_id, "task.failed", {"message": str(exc)})
                raise ValueError(f"{engine} session failed to start: {exc}") from exc
            binding.conversation_id = conversation_id
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
            self._binding_task[binding.binding_id] = task_id
            self._store.update_task(task_id, engine_session_id=binding.binding_id,
                                    engine_native_id=binding.native_session_id)
            configure = getattr(adapter, "configure_session", None)
            if configure:
                try:
                    await configure(binding, cfg)
                except Exception as exc:
                    self._store.update_task(task_id, status="failed", error=str(exc))
                    self._store.update_engine_session(binding.binding_id, task_id=task_id,
                                                      status="idle", payload=binding.as_dict())
                    self._emit(task_id, "task.failed", {"message": str(exc)})
                    raise ValueError(f"{engine} configuration failed: {exc}") from exc
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
            self._store.update_task(task_id, status="failed", error=str(exc))
            self._emit(task_id, "task.failed", {"message": str(exc)})
            raise ValueError(f"{engine} turn failed to start: {exc}") from exc
        return self._store.get_task(task_id, include_events=True) or task

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
        resolved = self._store.resolve_approval(
            approval_id, "approved" if decision in {"approved", "approve"} else decision)
        self._emit(task_id, "approval.resolved", {"approval": resolved})
        if payload.get('engine_method')=='browser.review':
            self._store.update_task(task_id,status='running')
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
            except KeyError:
                pass  # request already expired engine-side; row stays resolved
        return resolved

    # ----------------------------------------------------------- event flow

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
        payload = dict(event.payload)
        if event.native:
            payload["native"] = event.native
        self._emit(task_id, event.type, payload)

        if event.type == "engine.message.delta":
            delta = event.payload.get("delta") or event.payload.get("text") or ""
            self._result_buffers.setdefault(binding_id, []).append(str(delta))
        elif event.type == "engine.item.completed":
            item = event.payload.get("item") or {}
            if item.get("type") == "agentMessage" and item.get("text"):
                buf = self._result_buffers.setdefault(binding_id, [])
                if not buf or not "".join(buf).strip():
                    buf.append(str(item["text"]))
        elif event.type == "engine.turn.completed":
            self._finish_turn(binding_id, task_id, event.payload)
        elif event.type=='engine.approval.expired':
            for approval in self._store.approvals(task_id):
                if approval['status']=='pending' and approval['payload'].get('engine_request_id')==event.payload.get('request_id'):
                    resolved=self._store.resolve_approval(approval['id'],'denied')
                    self._emit(task_id,'approval.resolved',{'approval':resolved})
            self._store.update_task(task_id,status='running')
        elif event.type == "engine.session.lost":
            self._store.update_engine_session(binding_id, status="lost")
            task = self._store.get_task(task_id)
            if task and task["status"] in {"running", "planning", "awaiting_approval", "cancelling"}:
                self._store.update_task(
                    task_id, status="failed",
                    error="Engine process exited; session may be resumable",
                )
                self._emit(task_id, "task.failed", {"message": "Engine process exited; session may be resumable"})

    def _finish_turn(self, binding_id: str, task_id: str,
                     payload: dict[str, Any]) -> None:
        status = str(payload.get("status") or "completed")
        result = "".join(self._result_buffers.pop(binding_id, [])) or None
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
            self._store.update_engine_session(binding_id, status="idle")

    async def approval_sink(self, binding_id: str, token: str, method: str,
                            params: dict[str, Any], kind: str) -> str:
        """Create the TermX approval row backing a native server→client request."""
        task_id = self._binding_task.get(binding_id)
        if task_id is None:
            raise ValueError("no task bound to engine session")
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
        self._emit(task_id, "approval.requested", {"approval": approval})
        if method=='browser.review':self._store.update_task(task_id,status='awaiting_approval')
        return str(approval["id"])

    # ------------------------------------------------------------ lifecycle

    def recover(self) -> int:
        """On host start: live bindings are gone — mark them resumable-lost."""
        marked = 0
        for record in self._store.list_engine_sessions():
            if record["status"] in _ENGINE_ACTIVE:
                self._store.update_engine_session(
                    record["binding_id"], status="lost")
                task = self._store.get_task(record["task_id"])
                if task and task["status"] in {"running", "planning", "awaiting_approval", "cancelling"}:
                    message = "Host restarted during this turn; resume the native session with a new task"
                    self._store.update_task(task["id"], status="failed", error=message)
                    self._emit(task["id"], "task.failed", {"message": message})
                marked += 1
        return marked

    async def shutdown(self) -> None:
        await self.catalogue.stop()
        registry = getattr(self, "acp_registry", None)
        if registry:
            await registry.stop()
        for adapter in self._adapters.values():
            try:
                await adapter.shutdown()
            except Exception:
                pass


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
