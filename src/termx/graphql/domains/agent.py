"""Agent domain: providers, engines, tasks, storage, policy rules."""

from __future__ import annotations

import asyncio
import os
import subprocess
from pathlib import Path
from dataclasses import asdict
from time import time
from typing import Any

import strawberry
from fastapi import HTTPException
from strawberry.scalars import JSON
from strawberry.types import Info

from termx.audit import log_event
from termx.engines.env import env_diff_report, resolve_executable
from termx.graphql.context import TermxContext
from termx.graphql.errors import resolver
from termx.graphql.inputs import (
    AgentApprovalInput,
    AgentProviderInput,
    AgentRetentionInput,
    AgentTaskInput,
    PolicyRuleInput,
    PolicyRulePatchInput,
    WorktreeActionInput,
)
from termx.graphql import types as T

Ctx = Info[TermxContext, None]

_APPROVAL_DECISIONS = {"approved", "denied", "cancel"}
_APPROVAL_REMEMBER = {"once", "task", "project", "custom_agent", "session", "always"}


@strawberry.type
class AgentQueries:
    @strawberry.field
    @resolver
    def agent_configuration(self, info: Ctx) -> JSON:
        info.context.require_host('ai-settings')
        return asdict(info.context.state.store.get().agent)

    @strawberry.field
    @resolver
    async def engine_configuration(self, info: Ctx, engine_id: str,
                                   cwd: str | None = None) -> JSON:
        info.context.require_path('agent-view', cwd) if cwd else info.context.require_host('agent-view')
        try:
            return await info.context.state.engines.configuration(engine_id, cwd)
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @strawberry.field
    @resolver
    def engine_task_configuration(self, info: Ctx, task_id: str) -> JSON:
        info.context.require_resource('agent-view', 'task', task_id)
        try:
            return info.context.state.engines.task_configuration(task_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @strawberry.field
    @resolver
    def agent_providers(self, info: Ctx) -> list[T.AgentProvider]:
        info.context.require("agent-view")
        return T.AgentProvider.wrap_all(info.context.state.agent.list_providers())

    @strawberry.field
    @resolver
    async def engines(self, info: Ctx) -> list[T.EngineDescriptor]:
        ctx = info.context
        ctx.require("agent-view")
        engines: list[dict[str, object]] = [
            {
                "id": "internal",
                "label": "TermX internal",
                "installed": True,
                "transport": "in-process",
                "auth_state": "not_required",
                "capabilities": {
                    "tools_filter": "native",
                    "approvals": "native",
                    "streaming": True,
                    "resume": "supported",
                    "steer": "supported",
                    "subagents": "supported",
                },
            }
        ]
        engines.extend(await ctx.state.engines.describe_all())
        return T.EngineDescriptor.wrap_all(engines)

    @strawberry.field
    @resolver
    async def acp_registry(self, info: Ctx) -> JSON:
        info.context.require("agent-view")
        return await info.context.state.engines.acp_registry.list()

    @strawberry.field
    @resolver
    async def engine_probe(self, info: Ctx, engine_id: str) -> T.EngineDescriptor:
        ctx = info.context
        ctx.require("agent-view")
        try:
            return T.EngineDescriptor.wrap(await ctx.state.engines.probe(engine_id))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @strawberry.field
    @resolver
    async def engine_models(self, info: Ctx, engine_id: str) -> list[str]:
        ctx = info.context
        ctx.require("agent-view")
        try:
            return await ctx.state.engines.models(engine_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @strawberry.field
    @resolver
    def engine_diagnostics(self, info: Ctx) -> JSON:
        info.context.require_host('host-admin')
        report = env_diff_report(dict(os.environ))
        report["resolutions"] = {
            name: resolve_executable(name) for name in ("codex", "devin", "grok", "claude")
        }
        return report

    @strawberry.field
    @resolver
    def agent_tasks(self, info: Ctx, limit: int = 100) -> list[T.AgentTask]:
        info.context.require("agent-view")
        return T.AgentTask.wrap_all(info.context.visible('agent-view', 'task', info.context.state.agent_store.list_tasks(limit=limit)))

    @strawberry.field
    @resolver
    def agent_task(self, info: Ctx, task_id: str) -> T.AgentTask:
        ctx = info.context
        ctx.require_resource('agent-view', 'task', task_id)
        task = ctx.state.agent_store.get_task(task_id, include_events=True)
        if task is None:
            raise HTTPException(status_code=404, detail="task not found")
        return T.AgentTask.wrap(task)

    @strawberry.field
    @resolver
    def agent_task_worktree(self, info: Ctx, task_id: str) -> T.TaskWorktree:
        ctx = info.context
        ctx.require_resource('agent-view', 'task', task_id)
        try:
            return T.TaskWorktree.wrap(ctx.state.agent.task_worktree(task_id))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="task not found") from exc

    @strawberry.field
    @resolver
    def agent_task_export(self, info: Ctx, task_id: str) -> JSON:
        """The export payload as data (the raw file download stays REST)."""
        ctx = info.context
        ctx.require_resource('agent-view', 'task', task_id)
        task = ctx.state.agent_store.export_task(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="task not found")
        return task

    @strawberry.field
    @resolver
    def agent_storage(self, info: Ctx) -> T.AgentStorageStatus:
        info.context.require_host('ai-settings')
        return T.AgentStorageStatus.wrap(info.context.state.agent_store.storage_status())

    @strawberry.field
    @resolver
    def agent_policies(
        self,
        info: Ctx,
        scope_type: str | None = None,
        scope_id: str | None = None,
        effect: str | None = None,
        include_revoked: bool = False,
    ) -> list[T.PolicyRule]:
        from termx.agent.policies.models import rule_public

        ctx = info.context
        ctx.require_host('agent-view')
        rules = ctx.state.agent_store.list_policy_rules(
            scope_type=scope_type,
            scope_id=scope_id,
            effect=effect,
            include_revoked=include_revoked,
        )
        return T.PolicyRule.wrap_all([rule_public(rule) for rule in rules])

    @strawberry.field
    @resolver
    def effective_agent_policies(
        self, info: Ctx, project_id: str | None = None, custom_agent_id: str | None = None
    ) -> T.EffectivePolicies:
        from termx.agent.policies.models import rule_public

        ctx = info.context
        ctx.require_project('agent-view', project_id)
        scopes: list[tuple[str, str]] = [("host", "")]
        if custom_agent_id:
            scopes.append(("custom_agent", custom_agent_id))
        if project_id:
            scopes.append(("project", project_id))
        rules = ctx.state.agent_store.list_policy_rules(limit=1000)
        now = time()
        matched = [
            rule
            for rule in rules
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
            custom = ctx.state.agent_store.get_custom_agent(custom_agent_id)
            if custom is None:raise HTTPException(404,'custom agent not found')
            from termx.graphql.domains.chat import _require_custom_agent
            _require_custom_agent(ctx,custom)
        from termx.sandbox import runner_for

        profile = str((custom or {}).get("sandbox_profile") or "agent")
        return T.EffectivePolicies.wrap(
            {
                "rules": [rule_public(rule) for rule in matched],
                "approval_mode": str((custom or {}).get("approval_mode") or "standard"),
                "sandbox_profile": profile,
                "sandbox_capabilities": sorted(runner_for(profile).capabilities().granted),
            }
        )


@strawberry.type
class AgentMutations:
    @strawberry.mutation
    @resolver
    async def refresh_acp_registry(self, info: Ctx) -> JSON:
        info.context.require_host('host-admin')
        return await info.context.state.engines.acp_registry.refresh()

    @strawberry.mutation
    @resolver
    async def install_acp_runner(self, info: Ctx, registry_id: str) -> JSON:
        ctx = info.context
        ctx.require_host('host-admin')
        registry = ctx.state.engines.acp_registry
        try:
            engine_id, executable, args, entry = await registry.install(registry_id)
            async with ctx.state.engines._lock:
                latest = ctx.state.store.get().agent
                runners = {key: dict(value) for key, value in latest.acp_runners.items()}
                try:
                    _kind, selected = registry._distribution(entry)
                except ValueError:
                    selected = {}
                registry_env = selected.get("env", {}) if isinstance(selected.get("env", {}), dict) else {}
                runners[engine_id] = {
                    **runners.get(engine_id, {}),
                    "label": entry.get("name", engine_id),
                    "executable": executable,
                    "args": args,
                    "env_names": list(registry_env),
                    "registry_env": registry_env,
                    "registry_id": registry_id,
                    "registry_version": entry.get("version", ""),
                    "enabled": True,
                    "transport": "stdio",
                }
                prefs = ctx.state.store.update_agent({"acp_runners": runners})
                await ctx.state.engines.configure_runners(prefs)
            discovered = await ctx.state.engines.catalogue.refresh(engine_id)
            result = discovered[engine_id]
            return {"registry_id": registry_id, "engine_id": engine_id,
                    "installed": True, "registered": True, **result}
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (ValueError, OSError, TimeoutError, asyncio.TimeoutError,
                subprocess.SubprocessError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @strawberry.mutation
    @resolver
    async def update_agent_configuration(self, info: Ctx, input: JSON) -> JSON:
        # Launch settings select host executables, so only the host admin may
        # change them. Credentials remain environment/secure-store owned.
        ctx = info.context
        ctx.require_host('host-admin')
        try:
            if not isinstance(input, dict):
                raise ValueError("agent configuration must be an object")
            from termx.config import _agent_prefs
            prefs = _agent_prefs({**asdict(ctx.state.store.get().agent), **input})
            async with ctx.state.engines._lock:
                ctx.state.engines.validate_runner_changes(prefs.acp_runners)
                prefs = ctx.state.store.update_agent(input)
                await ctx.state.engines.configure_runners(prefs)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        for provider in ctx.state.agent.list_providers():
            ctx.state.agent._http.evict(provider["id"])
        return asdict(prefs)

    @strawberry.mutation
    @resolver
    async def refresh_engine_catalogue(self, info: Ctx, engine_id: str | None = None,
                                       cwd: str | None = None) -> JSON:
        info.context.require_host('agent-view')
        try:
            return await info.context.state.engines.catalogue.refresh(engine_id, cwd)
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @strawberry.mutation
    @resolver
    async def authenticate_engine(self, info: Ctx, engine_id: str, method_id: str) -> JSON:
        info.context.require_host('agent-run')
        try:
            adapter = info.context.state.engines.adapter(engine_id)
            authenticate = getattr(adapter, "authenticate", None)
            if authenticate is None:
                raise ValueError("engine does not expose ACP authentication")
            return await authenticate(method_id)
        except (KeyError, ValueError, TimeoutError) as exc:
            raise HTTPException(status_code=400, detail=str(exc) or "Authentication timed out") from exc

    @strawberry.mutation
    @resolver
    def save_agent_provider(self, info: Ctx, input: AgentProviderInput) -> T.AgentProvider:
        ctx = info.context
        ctx.require_host('ai-settings')
        try:
            provider = ctx.state.agent.save_provider(
                provider_id=input.id,
                kind=input.kind,
                name=input.name,
                base_url=input.base_url,
                model=input.model,
                capabilities=input.capabilities,
                api_key=input.api_key,
            )
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        log_event("agent_provider_save", provider_id=input.id, provider_kind=input.kind)
        return T.AgentProvider.wrap(provider)

    @strawberry.mutation
    @resolver
    def delete_agent_provider(self, info: Ctx, provider_id: str) -> T.Ok:
        ctx = info.context
        ctx.require_host('ai-settings')
        try:
            deleted = ctx.state.agent.delete_provider(provider_id)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if not deleted:
            raise HTTPException(status_code=404, detail="provider not found")
        log_event("agent_provider_delete", provider_id=provider_id)
        return T.Ok.wrap({"ok": True})

    @strawberry.mutation
    @resolver
    async def test_agent_provider(self, info: Ctx, provider_id: str) -> JSON:
        ctx = info.context
        ctx.require_host('ai-settings')
        try:
            return await ctx.state.agent.test_provider(provider_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="provider not found") from exc
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @strawberry.mutation
    @resolver
    async def create_agent_task(self, info: Ctx, input: AgentTaskInput) -> T.AgentTask:
        ctx = info.context
        ctx.require("agent-run")
        state = ctx.state
        body = input
        project_id = ctx.require_path('agent-run', body.cwd)
        if body.conversation_id:
            ctx.require_resource('agent-control', 'conversation', body.conversation_id)
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
        if custom_agent:
            from termx.graphql.domains.chat import _require_custom_agent
            _require_custom_agent(ctx,custom_agent)
            if not custom_agent.get('enabled'):
                raise HTTPException(409,'custom agent is disabled')
        attachments = body.attachments or []
        profile_engine = ((custom_agent or {}).get("file") or {}).get("engine")
        previous = state.agent_store.engine_session_for_conversation(body.conversation_id) if body.conversation_id else None
        engine = body.engine or (profile_engine if profile_engine not in (None, "inherit") else None) or (
            previous["engine"] if previous else "internal")
        if engine != "internal":
            try:
                from termx.mcp.scope import authorize as authorize_mcp
                task = await state.engines.create_task(
                    prompt=body.prompt,
                    cwd=body.cwd,
                    engine=engine,
                    model=body.model,
                    # TermX chat mode is independent of the agent's optional
                    # ACP selectors. Native mode IDs come from explicit
                    # selections or saved defaults, never from body.mode.
                    mode=body.engine_mode,
                    config_options=body.config_options,
                    custom_agent=custom_agent,
                    mcp_project_id=project_id,mcp_authorize=lambda conn:authorize_mcp(state,conn,'agent-run',credential=ctx.secret,project_id=project_id,cwd=body.cwd),
                    conversation_id=body.conversation_id or None,
                    limits=body.limits,
                    sandbox_profile=str(
                        (custom_agent or {}).get("sandbox_profile") or "agent"),
                    approval_mode=str(
                        (custom_agent or {}).get("approval_mode") or "standard"),
                )
            except KeyError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            if body.conversation_id:
                state.agent_store.add_conversation_turn(
                    body.conversation_id,
                    prompt=body.turn_prompt or body.prompt,
                    task_id=task["id"],
                    mode=body.mode,
                    provider_id=None,
                    model=body.model,
                    context_refs=body.context_refs,
                    attachment_refs=[{"ref": item.name} for item in attachments],
                )
            ctx.claim('task', task['id'], project_id)
            log_event("agent_task_create", task_id=task["id"], engine=engine)
            return T.AgentTask.wrap(task)
        if body.engine_mode is not None or body.config_options is not None:
            raise HTTPException(status_code=400, detail="internal tasks use mode and limits; ACP config selectors require an ACP engine")
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
        mcp_created=None
        if ((custom_agent or {}).get('file') or {}).get('mcp_connections'):
            live=state.identity.resolve(ctx.secret)
            if not live:raise HTTPException(403,'MCP tools require a managed user session; sign in or pair this device')
            if body.execution_mode=='worktree':raise HTTPException(400,'Use a workspace conversation to bind MCP to an enrolled worktree')
            from termx.mcp.client import McpConnectionError
            try:pinned=state.agent.mcp.preflight(live.principal,project_id,body.cwd,body.conversation_id,custom_agent)
            except PermissionError as exc:raise HTTPException(403,str(exc)) from exc
            except McpConnectionError as exc:raise HTTPException(409,str(exc)) from exc
            except ValueError as exc:raise HTTPException(400,str(exc)) from exc
            def mcp_created(tid):
                current=state.identity.resolve(ctx.secret)
                if not current or current.session_id!=live.session_id or current.principal.id!=live.principal.id:raise HTTPException(401,'MCP originating session changed')
                ctx.claim('task',tid,project_id)
                state.workspace.store.create('task',live.principal.id,{'conversation_id':body.conversation_id,'cwd':str(Path(body.cwd).resolve()),'mcp_snapshot':pinned},project_id,tid)
                state.browser.records.put('agent-task-authority',tid,{'id':tid,'principal_id':live.principal.id,'session_id':live.session_id,'project_id':project_id or '', 'policy_version':live.principal.policy_version})
        try:
            task = await state.agent.create_task(
                prompt=body.prompt,
                cwd=body.cwd,
                provider_id=provider_id,
                limits=body.limits,
                mode=body.mode,
                model=model,
                attachments=[{"name": item.name, "mime": item.mime, "data": item.data} for item in attachments],
                execution_mode=body.execution_mode,
                conversation_id=body.conversation_id or None,
                custom_agent_id=custom_agent_id,
                custom_agent_snapshot=custom_agent,on_created=mcp_created,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="provider not found") from exc
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if body.conversation_id:
            state.agent_store.add_conversation_turn(
                body.conversation_id,
                prompt=body.turn_prompt or body.prompt,
                task_id=task["id"],
                mode=body.mode,
                provider_id=body.provider_id,
                model=body.model,
                context_refs=body.context_refs,
                attachment_refs=[{"ref": item.name} for item in attachments],
            )
        ctx.claim('task', task['id'], project_id)
        log_event("agent_task_create", task_id=task["id"], provider_id=body.provider_id)
        return T.AgentTask.wrap(task)

    @strawberry.mutation
    @resolver
    def delete_agent_task(self, info: Ctx, task_id: str) -> T.Ok:
        ctx = info.context
        ctx.require_resource('agent-control', 'task', task_id)
        try:
            deleted = ctx.state.agent.delete_task(task_id)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if not deleted:
            raise HTTPException(status_code=404, detail="task not found")
        return T.Ok.wrap({"ok": True})

    @strawberry.mutation
    @resolver
    async def resolve_agent_approval(
        self, info: Ctx, task_id: str, approval_id: str, input: AgentApprovalInput
    ) -> JSON:
        ctx = info.context
        ctx.require_resource('agent-control', 'task', task_id)
        if input.decision not in _APPROVAL_DECISIONS:
            raise HTTPException(status_code=422, detail="decision must be approved, denied or cancel")
        if input.remember is not None and input.remember not in _APPROVAL_REMEMBER:
            raise HTTPException(status_code=422, detail=f"remember must be one of {sorted(_APPROVAL_REMEMBER)}")
        try:
            runners=getattr(ctx.state,'runner_agents',None)
            if runners and runners.owns(task_id):
                if input.decision=='cancel':return runners.cancel(task_id)
                if input.remember or input.limits:
                    raise HTTPException(409,'Runner decisions are exact once; renew the explicit runner grant to change budgets or policy')
                return await runners.resolve_approval(task_id,approval_id,input.decision)
            resolved = await ctx.state.engines.resolve_approval(
                task_id, approval_id, input.decision,
                remember=input.remember, content=input.content,
            )
            if resolved is not None:
                return resolved
            return await ctx.state.agent.resolve_approval(
                task_id, approval_id, input.decision, remember=input.remember,
                limits=input.limits,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="approval not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @strawberry.mutation
    @resolver
    async def steer_agent_task(self, info: Ctx, task_id: str, message: str) -> JSON:
        ctx = info.context
        ctx.require_resource('agent-control', 'task', task_id)
        try:
            runners=getattr(ctx.state,'runner_agents',None)
            if runners and runners.owns(task_id):
                return runners.steer(task_id,message)
            steered = await ctx.state.engines.steer(task_id, message)
            if steered is not None:
                return steered
            return ctx.state.agent.steer(task_id, message)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="task not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @strawberry.mutation
    @resolver
    async def cancel_agent_task(self, info: Ctx, task_id: str) -> JSON:
        ctx = info.context
        ctx.require_resource('agent-control', 'task', task_id)
        try:
            runners=getattr(ctx.state,'runner_agents',None)
            if runners and runners.owns(task_id):
                return runners.cancel(task_id)
            cancelled = await ctx.state.engines.cancel(task_id)
            if cancelled is not None:
                return cancelled
            return ctx.state.agent.cancel(task_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="task not found") from exc

    @strawberry.mutation
    @resolver
    async def recover_agent_task(self, info: Ctx, task_id: str, confirm: bool = False) -> JSON:
        ctx = info.context
        ctx.require_resource('agent-control', 'task', task_id)
        if getattr(ctx.state,'runner_agents',None) and ctx.state.runner_agents.owns(task_id):
            raise HTTPException(409,'Inspect the runner outcome, then explicitly retry in its selected workspace session; cloud tasks never replay on the local host')
        try:
            return await ctx.state.agent.recover_task(task_id, confirm=confirm)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="task not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @strawberry.mutation
    @resolver
    async def takeover_agent_task(self, info: Ctx, task_id: str) -> JSON:
        ctx = info.context
        ctx.require_resource('agent-control', 'task', task_id)
        if getattr(ctx.state,'runner_agents',None) and ctx.state.runner_agents.owns(task_id):
            raise HTTPException(409,'This network-isolated coding runner has no Computer control surface; steer or cancel the exact remote task')
        try:
            return await ctx.state.agent.takeover(task_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="task not found") from exc

    @strawberry.mutation
    @resolver
    def resolve_task_worktree(
        self, info: Ctx, task_id: str, input: WorktreeActionInput
    ) -> T.TaskWorktree:
        ctx = info.context
        ctx.require_resource('agent-control', 'task', task_id)
        from termx.agent.worktrees import WorktreeConfirmRequired

        try:
            worktree = ctx.state.agent.resolve_worktree(
                task_id, input.action, confirm=input.confirm
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
        log_event("agent_worktree", task_id=task_id, action=input.action)
        return T.TaskWorktree.wrap(worktree)

    @strawberry.mutation
    @resolver
    def prune_agent_storage(
        self, info: Ctx, input: AgentRetentionInput | None = None
    ) -> T.AgentStoragePruneResult:
        ctx = info.context
        ctx.require_host('ai-settings')
        input = input or AgentRetentionInput()
        ctx.state.agent_store.set_retention_policy(
            retention_days=input.retention_days,
            max_bytes=input.max_bytes,
        )
        removed = ctx.state.agent.prune_storage(
            retention_days=input.retention_days, max_bytes=input.max_bytes
        )
        return T.AgentStoragePruneResult.wrap(
            {
                "removed": removed,
                "storage": ctx.state.agent_store.storage_status(
                    retention_days=input.retention_days, max_bytes=input.max_bytes
                ),
            }
        )

    @strawberry.mutation
    @resolver
    def create_agent_policy(self, info: Ctx, input: PolicyRuleInput) -> T.PolicyRule:
        from termx.agent.policies.models import rule_public

        ctx = info.context
        ctx.require_host('agent-control')
        try:
            rule = ctx.state.agent_store.create_policy_rule(
                effect=input.effect,
                scope_type=input.scope_type,
                scope_id=input.scope_id,
                action_type=input.action_type,
                tool=input.tool,
                fingerprint=input.fingerprint,
                fingerprint_kind=input.fingerprint_kind,
                matcher=input.matcher or {},
                capabilities=input.capabilities or [],
                sandbox_profile=input.sandbox_profile,
                display=input.display,
                task_id=input.task_id,
                project_id=input.project_id,
                expires_at=input.expires_at,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return T.PolicyRule.wrap(rule_public(rule))

    @strawberry.mutation
    @resolver
    def patch_agent_policy(
        self, info: Ctx, rule_id: str, input: PolicyRulePatchInput
    ) -> T.PolicyRule:
        from termx.agent.policies.models import rule_public

        ctx = info.context
        ctx.require_host('agent-control')
        updates = {
            key: value
            for key, value in {
                "effect": input.effect,
                "expires_at": input.expires_at,
                "display": input.display,
                "capabilities": input.capabilities,
            }.items()
            if value is not None
        }
        try:
            rule = ctx.state.agent_store.update_policy_rule(rule_id, **updates)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="policy rule not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return T.PolicyRule.wrap(rule_public(rule))

    @strawberry.mutation
    @resolver
    def revoke_agent_policy(self, info: Ctx, rule_id: str) -> T.Ok:
        ctx = info.context
        ctx.require_host('agent-control')
        try:
            ctx.state.agent_store.revoke_policy_rule(rule_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="policy rule not found") from exc
        return T.Ok.wrap({"ok": True})
