"""Chat domain: conversations, custom agents, extension catalog, MCP
connections + session-scoped gateway."""

from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path
from typing import Any

import strawberry
from fastapi import HTTPException
from strawberry.scalars import JSON
from strawberry.types import Info

from termx.agents.files import AgentFile, AgentFileError, validate_config_options, slugify
from termx.agents.registry import RevisionConflict
from termx.graphql.context import TermxContext
from termx.graphql.errors import resolver
from termx.graphql.inputs import (
    ConversationInput,
    ConversationPatchInput,
    ConversationTurnInput,
    CustomAgentImportInput,
    CustomAgentInput,
    CustomAgentPatchInput,
    ExtensionStateInput,
    McpConnectionInput,
    McpToolCallInput,
)
from termx.graphql import types as T
from termx.mcp.client import McpConnectionError
from termx.mcp.defs import ConnectionDef, ConnectionError_, validate_connection
from termx.mcp.gateway import GatewayAuthError
from termx.mcp.scope import authorize as authorize_mcp,validate_projects,definition_digest,validate_bindings

Ctx = Info[TermxContext, None]


_APPROVAL_MODES = {"standard", "remember", "autonomous"}
_SANDBOX_PROFILES = {"host", "workspace", "agent"}
_AGENT_ENGINES = {"internal", "codex", "devin", "grok", "claude", "inherit"}


def _validate_agent_enums(
    approval_mode: str | None, sandbox_profile: str | None, engine: str | None
) -> None:
    """REST enforced these via pydantic patterns; keep the contract here."""
    if approval_mode is not None and approval_mode not in _APPROVAL_MODES:
        raise ValueError("approval_mode must be standard|remember|autonomous")
    if sandbox_profile is not None and sandbox_profile not in _SANDBOX_PROFILES:
        raise ValueError("sandbox_profile must be host|workspace|agent")
    if engine is not None and engine not in _AGENT_ENGINES and not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", engine):
        raise ValueError("invalid engine")


def _agent_file_from_input(body: CustomAgentInput) -> AgentFile:
    skills = body.skills or {}
    _validate_agent_enums(body.approval_mode, body.sandbox_profile, body.engine)
    return AgentFile(
        slug=body.slug or "",
        name=body.name,
        description=body.description,
        instructions=body.instructions,
        engine=body.engine,
        model=body.model,
        engine_mode=body.engine_mode,
        config_options=validate_config_options(body.config_options if body.config_options is not None else {}),
        enabled=body.enabled,
        auto_use=body.auto_use,
        tools_mode="explicit" if body.tools is not None else "all",
        tools=list(body.tools or []),
        tools_omitted=body.tools is None,
        toolsets=list(body.toolsets or []),
        deny_tools=list(body.deny_tools or []),
        skills_mode=str(skills.get("mode", "auto")),
        skills_include=list(skills.get("include") or []),
        skills_exclude=list(skills.get("exclude") or []),
        workflows=list(body.workflows or []),
        mcp_connections=list(body.mcp_connections or []),
        delegation=dict(body.delegation or {}),
        approval_mode=body.approval_mode,
        sandbox_profile=body.sandbox_profile,
        limits=dict(body.limits or {}),
    )


def _slug_for_agent(state: Any, agent_id: str) -> str | None:
    if agent_id.startswith("agent."):
        return agent_id.removeprefix("agent.")
    row = state.agent_store.get_custom_agent(agent_id)
    if row and row.get("file_path"):
        return Path(row["file_path"]).name.removesuffix(".agent.md")
    return None


def _require_custom_agent(ctx, row, scope='agent-view'):
    ctx.require(scope)
    policy=ctx.state.authorization
    owner=policy.resource_owner('custom_agent',row['id'])
    if owner:
        ctx.require_resource(scope,'custom_agent',row['id'])
        return
    if policy.can(ctx.secret,'host-admin'):
        ctx.require_host(scope)
        return
    policy.require_creation(ctx.secret,scope)
    # Legacy installed definitions are shared explicitly, never assigned an
    # invented owner. Newly created/imported definitions have other sources
    # and become private through their resource ledger claims.
    file=row.get('file') or {}
    extra=(file.get('x_termx_extra') or {}) if isinstance(file,dict) else {}
    metadata=[row,file,extra]
    scoped=any(isinstance(item,dict) and any(item.get(key) for key in
        ('owner','owner_id','principal_id','project_id')) for item in metadata)
    if scope!='agent-view' or not row.get('enabled') or row.get('source') not in {'device','bundled'} or scoped:
        raise HTTPException(403,'custom agent is private or not shared')


def _claim_custom_agent_before_write(ctx, definition):
    # Reload authority at the final file boundary, and reserve the canonical
    # identity before it can be published with a caller-supplied source label.
    ctx.require_host('agent-control')
    ctx.claim('custom_agent',definition.qid_id)
    ctx.require_host('agent-control')


def _conn_or_404(state: Any, conn_id: str) -> ConnectionDef:
    conn = state.mcp_registry().get(conn_id)
    if conn is None:
        raise HTTPException(status_code=404, detail="connection not found")
    return conn


@strawberry.type
class ChatQueries:
    @strawberry.field
    @resolver
    def conversations(self, info: Ctx, archived: str | None = None) -> list[T.HostConversation]:
        ctx = info.context
        ctx.require("agent-view")
        flag: bool | None = False
        if archived in {"1", "true", "all"}:
            flag = None if archived == "all" else True
        return T.HostConversation.wrap_all(
            ctx.visible('agent-view', 'conversation', ctx.state.agent_store.list_conversations(archived=flag))
        )

    @strawberry.field
    @resolver
    def conversation(self, info: Ctx, conversation_id: str) -> T.HostConversation:
        ctx = info.context
        ctx.require_resource('agent-view', 'conversation', conversation_id)
        conversation = ctx.state.agent_store.get_conversation(
            conversation_id, include_turns=True
        )
        if conversation is None:
            raise HTTPException(status_code=404, detail="conversation not found")
        return T.HostConversation.wrap(conversation)

    @strawberry.field
    @resolver
    def custom_agents(self, info: Ctx) -> list[T.HostCustomAgent]:
        ctx = info.context
        ctx.require('agent-view')
        ctx.state.authorization.require_creation(ctx.secret,'agent-view')
        visible=[]
        for row in ctx.state.agent_store.list_custom_agents():
            try:_require_custom_agent(ctx,row)
            except HTTPException as error:
                if error.status_code==403:continue
                raise
            visible.append(row)
        return T.HostCustomAgent.wrap_all(visible)

    @strawberry.field
    @resolver
    def custom_agent(self, info: Ctx, agent_id: str) -> T.HostCustomAgent:
        ctx = info.context
        agent = ctx.state.agent_store.get_custom_agent(agent_id)
        if agent is None:
            raise HTTPException(status_code=404, detail="custom agent not found")
        _require_custom_agent(ctx,agent)
        return T.HostCustomAgent.wrap(agent)

    @strawberry.field
    @resolver
    def custom_agent_export(self, info: Ctx, agent_id: str) -> T.CustomAgentFile:
        ctx = info.context
        row=ctx.state.agent_store.get_custom_agent(agent_id)
        if row is None:raise HTTPException(404,'custom agent not found')
        _require_custom_agent(ctx,row)
        slug = _slug_for_agent(ctx.state, agent_id)
        raw = ctx.state.agent_registry.read_raw(slug) if slug else None
        if raw is None:
            raise HTTPException(status_code=404, detail="agent file not found")
        return T.CustomAgentFile.wrap({"id": agent_id, "slug": slug, "markdown": raw})

    @strawberry.field
    @resolver
    def extensions(
        self,
        info: Ctx,
        kind: str | None = None,
        source: str | None = None,
        enabled: bool | None = None,
    ) -> T.ExtensionsResult:
        ctx = info.context
        ctx.require_host('agent-view')
        entries, report = ctx.state.extension_scan()
        items = list(entries.values())
        if kind:
            items = [e for e in items if e["kind"] == kind]
        if source:
            items = [e for e in items if e["source"] == source]
        if enabled is not None:
            items = [e for e in items if e["enabled"] is enabled]
        return T.ExtensionsResult.wrap({"extensions": items, "report": report})

    @strawberry.field
    @resolver
    def extension(self, info: Ctx, qid: str) -> T.ExtensionEntry:
        ctx = info.context
        ctx.require_host('agent-view')
        entries, _ = ctx.state.extension_scan()
        entry = entries.get(qid)
        if entry is None:
            raise HTTPException(status_code=404, detail="extension not found")
        from termx.discovery.safety import safe_read_text

        entry = dict(entry)
        try:
            entry["body"] = safe_read_text(entry["path"])
        except Exception as exc:  # noqa: BLE001
            entry["body_error"] = str(exc)
        return T.ExtensionEntry.wrap(entry)

    @strawberry.field
    @resolver
    def mcp_connections(self, info: Ctx,project_id: str | None = None) -> list[T.McpConnectionInfo]:
        ctx = info.context
        if project_id:ctx.require_project('agent-view',project_id)
        else:ctx.require_host('agent-view')
        status = ctx.state.mcp_pool.status()
        out = []
        for conn in ctx.state.mcp_registry().list():
            if project_id:
                try:authorize_mcp(ctx.state,conn,'agent-view',credential=ctx.secret,project_id=project_id)
                except (PermissionError,HTTPException,ConnectionError_):continue
            d = conn.as_dict()
            d['definition']=dict(d)
            d['definition_digest']=definition_digest(conn)
            d['credential_configured']=any(bool(ctx.state.credentials.get(ref)) for ref in [*conn.secret_refs,*conn.header_secret_refs.values(),*([conn.client_secret_ref] if conn.client_secret_ref else [])])
            d["runtime"] = status.get(conn.id, {"connected": False})
            d['catalog']=ctx.state.mcp_pool.catalog(conn.id,project_id=project_id if project_id else (conn.allowed_projects[0] if conn.allowed_projects else None))
            if conn.id in ctx.state.mcp_auth_urls:
                d["auth_url_pending"] = ctx.state.mcp_auth_urls[conn.id]
            out.append(d)
        return T.McpConnectionInfo.wrap_all(out)


@strawberry.type
class ChatMutations:
    @strawberry.mutation
    @resolver
    def create_conversation(self, info: Ctx, input: ConversationInput) -> T.HostConversation:
        ctx = info.context
        ctx.require('agent-control')
        ctx.state.authorization.require_creation(ctx.secret, 'agent-control')
        if input.custom_agent_id:
            selected=ctx.state.agent_store.get_custom_agent(input.custom_agent_id)
            if selected is None:raise HTTPException(404,'custom agent not found')
            _require_custom_agent(ctx,selected)
        if input.project_id:
            ctx.require_project('agent-control', input.project_id)
        if input.cwd:
            actual_project = ctx.require_path('agent-control', input.cwd)
            if input.project_id and input.project_id != actual_project:
                raise HTTPException(403, 'conversation path/project mismatch')
        conversation = ctx.state.agent_store.create_conversation(
            title=input.title,
            project_id=input.project_id,
            cwd=input.cwd,
            mode=input.mode,
            custom_agent_id=input.custom_agent_id,
            provider_id=input.provider_id,
            model=input.model,
            pinned=input.pinned,
            archived=input.archived,
            draft=input.draft,
        )
        ctx.claim('conversation', conversation['id'], conversation.get('project_id'))
        return T.HostConversation.wrap(conversation)

    @strawberry.mutation
    @resolver
    def patch_conversation(
        self, info: Ctx, conversation_id: str, input: ConversationPatchInput
    ) -> T.HostConversation:
        ctx = info.context
        ctx.require_resource('agent-control', 'conversation', conversation_id)
        if input.project_id:
            ctx.require_project('agent-control', input.project_id)
        if input.cwd:
            ctx.require_path('agent-control', input.cwd)
        existing = ctx.state.agent_store.get_conversation(conversation_id)
        if existing and input.project_id is not None and input.project_id != existing.get('project_id'):
            ctx.require_host('host-admin')
        if input.custom_agent_id:
            selected=ctx.state.agent_store.get_custom_agent(input.custom_agent_id)
            if selected is None:raise HTTPException(404,'custom agent not found')
            _require_custom_agent(ctx,selected)
        updates = {
            key: value
            for key, value in {
                "title": input.title,
                "project_id": input.project_id,
                "cwd": input.cwd,
                "mode": input.mode,
                "custom_agent_id": input.custom_agent_id,
                "provider_id": input.provider_id,
                "model": input.model,
                "pinned": input.pinned,
                "archived": input.archived,
                "draft": input.draft,
            }.items()
            if value is not None
        }
        conversation = ctx.state.agent_store.update_conversation(conversation_id, **updates)
        if conversation is None:
            raise HTTPException(status_code=404, detail="conversation not found")
        return T.HostConversation.wrap(conversation)

    @strawberry.mutation
    @resolver
    def delete_conversation(self, info: Ctx, conversation_id: str) -> T.Deleted:
        ctx = info.context
        ctx.require_resource('agent-control', 'conversation', conversation_id)
        if not ctx.state.agent_store.delete_conversation(conversation_id):
            raise HTTPException(status_code=404, detail="conversation not found")
        return T.Deleted.wrap({"deleted": conversation_id})

    @strawberry.mutation
    @resolver
    def add_conversation_turn(
        self, info: Ctx, conversation_id: str, input: ConversationTurnInput
    ) -> T.ConversationTurn:
        ctx = info.context
        ctx.require_resource('agent-control', 'conversation', conversation_id)
        if input.task_id:
            ctx.require_resource('agent-view', 'task', input.task_id)
        try:
            turn = ctx.state.agent_store.add_conversation_turn(
                conversation_id,
                prompt=input.prompt,
                task_id=input.task_id,
                mode=input.mode,
                provider_id=input.provider_id,
                model=input.model,
                context_refs=input.context_refs,
                attachment_refs=input.attachment_refs,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="conversation not found") from exc
        return T.ConversationTurn.wrap(turn)

    @strawberry.mutation
    @resolver
    def create_custom_agent(self, info: Ctx, input: CustomAgentInput) -> T.HostCustomAgent:
        ctx = info.context
        ctx.require_host('agent-control')
        try:
            definition=_agent_file_from_input(input)
            definition.mcp_connections=validate_bindings(ctx.state,definition.mcp_connections)
            definition.slug=slugify(definition.slug or definition.name)
            if ctx.state.agent_store.get_custom_agent(definition.qid_id) or ctx.state.agent_registry.load(definition.slug):
                raise HTTPException(409,'custom agent already exists; patch its current revision')
            existing=ctx.state.authorization.resource_owner('custom_agent',definition.qid_id)
            live=ctx.state.identity.resolve(ctx.secret)
            if existing and (not live or existing['principal_id']!=live.principal.id):
                raise HTTPException(409,'custom agent identity already claimed')
            agent = ctx.state.agent_registry.save(definition,
                before_write=lambda row: _claim_custom_agent_before_write(ctx,row))
        except (ValueError, AgentFileError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        row = ctx.state.agent_store.get_custom_agent(agent.qid_id)
        return T.HostCustomAgent.wrap(row or agent.as_dict())

    @strawberry.mutation
    @resolver
    def patch_custom_agent(
        self, info: Ctx, agent_id: str, input: CustomAgentPatchInput
    ) -> T.HostCustomAgent:
        ctx = info.context
        ctx.require_host('agent-control')
        state = ctx.state
        row=state.agent_store.get_custom_agent(agent_id)
        if row is None:raise HTTPException(404,'custom agent not found')
        _require_custom_agent(ctx,row,'agent-control')
        slug = _slug_for_agent(state, agent_id)
        if slug is None:
            raise HTTPException(status_code=404, detail="custom agent not found")
        af = state.agent_registry.load(slug)
        if af is None:
            raise HTTPException(status_code=404, detail="agent file not found")
        try:
            _validate_agent_enums(
                input.approval_mode, input.sandbox_profile, input.engine
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        updates = {
            key: value
            for key, value in {
                "name": input.name,
                "description": input.description,
                "instructions": input.instructions,
                "model": input.model,
                "tools": input.tools,
                "limits": input.limits,
                "approval_mode": input.approval_mode,
                "sandbox_profile": input.sandbox_profile,
                "engine": input.engine,
                "enabled": input.enabled,
                "auto_use": input.auto_use,
                "toolsets": input.toolsets,
                "deny_tools": input.deny_tools,
                "skills": input.skills,
                "workflows": input.workflows,
                "mcp_connections": input.mcp_connections,
                "delegation": input.delegation,
            }.items()
            if value is not None
        }
        if "name" in updates:
            af.name = updates["name"]
        if "description" in updates:
            af.description = updates["description"]
        if "instructions" in updates:
            af.instructions = updates["instructions"]
        if "model" in updates:
            af.model = updates["model"]
        if input.engine_mode is not None:
            af.engine_mode = input.engine_mode or None
        if input.config_options is not None:
            try:
                af.config_options = validate_config_options(input.config_options)
            except AgentFileError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        if "tools" in updates:
            af.tools = list(updates["tools"])
            af.tools_omitted = False
            af.tools_mode = "explicit"
        if input.tools_mode is not None:
            if input.tools_mode not in {"all", "explicit"}:
                raise HTTPException(400, "tools_mode must be all|explicit")
            if input.tools_mode == "all" and input.tools:
                raise HTTPException(400, "Inherited tools cannot also supply an explicit tool list")
            af.tools_mode = input.tools_mode
            af.tools_omitted = input.tools_mode == "all"
            if af.tools_omitted:
                af.tools = []
        if "limits" in updates:
            af.limits = dict(updates["limits"])
        if "approval_mode" in updates:
            af.approval_mode = updates["approval_mode"]
        if "sandbox_profile" in updates:
            af.sandbox_profile = updates["sandbox_profile"]
        if "engine" in updates:
            af.engine = updates["engine"]
        if "enabled" in updates:
            af.enabled = updates["enabled"]
        if "auto_use" in updates:
            af.auto_use = updates["auto_use"]
        if "toolsets" in updates:
            af.toolsets = list(updates["toolsets"])
        if "deny_tools" in updates:
            af.deny_tools = list(updates["deny_tools"])
        if "skills" in updates:
            skills = updates["skills"]
            af.skills_mode = str(skills.get("mode", af.skills_mode))
            af.skills_include = list(skills.get("include", af.skills_include))
            af.skills_exclude = list(skills.get("exclude", af.skills_exclude))
        if "workflows" in updates:
            af.workflows = list(updates["workflows"])
        if "mcp_connections" in updates:
            try:af.mcp_connections = validate_bindings(ctx.state,updates["mcp_connections"])
            except ValueError as exc:raise HTTPException(400,str(exc)) from exc
        if "delegation" in updates:
            af.delegation = dict(updates["delegation"])
        try:
            saved = state.agent_registry.save(
                af, expected_revision=input.revision or af.revision,
            )
        except RevisionConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except (ValueError, AgentFileError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        row = state.agent_store.get_custom_agent(saved.qid_id)
        return T.HostCustomAgent.wrap(row or saved.as_dict())

    @strawberry.mutation
    @resolver
    def delete_custom_agent(self, info: Ctx, agent_id: str) -> T.Deleted:
        ctx = info.context
        ctx.require_host('agent-control')
        state = ctx.state
        row=state.agent_store.get_custom_agent(agent_id)
        if row is None:raise HTTPException(404,'custom agent not found')
        _require_custom_agent(ctx,row,'agent-control')
        slug = _slug_for_agent(state, agent_id)
        if slug is not None and state.agent_registry.delete(slug):
            return T.Deleted.wrap({"deleted": agent_id})
        if not state.agent_store.delete_custom_agent(agent_id):
            raise HTTPException(status_code=404, detail="custom agent not found")
        return T.Deleted.wrap({"deleted": agent_id})

    @strawberry.mutation
    @resolver
    def duplicate_custom_agent(self, info: Ctx, agent_id: str) -> T.HostCustomAgent:
        ctx = info.context
        ctx.require_host('agent-control')
        state = ctx.state
        row=state.agent_store.get_custom_agent(agent_id)
        if row is None:raise HTTPException(404,'custom agent not found')
        _require_custom_agent(ctx,row)
        slug = _slug_for_agent(state, agent_id)
        dup = state.agent_registry.duplicate(slug,
            before_write=lambda row: _claim_custom_agent_before_write(ctx,row)) if slug else None
        if dup is None:
            raise HTTPException(status_code=404, detail="custom agent not found")
        row = state.agent_store.get_custom_agent(dup.qid_id)
        return T.HostCustomAgent.wrap(row or dup.as_dict())

    @strawberry.mutation
    @resolver
    def import_custom_agent(
        self, info: Ctx, input: CustomAgentImportInput
    ) -> T.HostCustomAgent:
        ctx = info.context
        ctx.require_host('agent-control')
        try:
            agent = ctx.state.agent_registry.import_markdown(
                input.markdown, source=input.source or "import",
                before_write=lambda row: _claim_custom_agent_before_write(ctx,row),
            )
        except (ValueError, AgentFileError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        row = ctx.state.agent_store.get_custom_agent(agent.qid_id)
        return T.HostCustomAgent.wrap(row or agent.as_dict())

    @strawberry.mutation
    @resolver
    def rescan_extensions(self, info: Ctx) -> T.ExtensionsResult:
        ctx = info.context
        ctx.require_host('agent-view')
        ctx.state.agent_registry.sync()
        entries, report = ctx.state.extension_scan()
        return T.ExtensionsResult.wrap(
            {"extensions": list(entries.values()), "report": report}
        )

    @strawberry.mutation
    @resolver
    def set_extension_state(
        self, info: Ctx, qid: str, input: ExtensionStateInput
    ) -> JSON:
        ctx = info.context
        ctx.require_host('agent-control')
        entries, _ = ctx.state.extension_scan()
        if qid not in entries:
            raise HTTPException(status_code=404, detail="extension not found")
        st = ctx.state.agent_store.set_extension_state(
            qid, enabled=input.enabled, trusted=input.trusted, note=input.note
        )
        return {"state": st}

    @strawberry.mutation
    @resolver
    def create_mcp_connection(self, info: Ctx, input: McpConnectionInput) -> JSON:
        ctx = info.context
        ctx.require_host('agent-control')
        try:
            conn = validate_connection(dict(input.data or {}))
            validate_projects(ctx.state,conn)
        except ConnectionError_ as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        existing=ctx.state.mcp_registry().get(conn.id)
        if existing and input.expected_digest != definition_digest(existing):
            raise HTTPException(409,'MCP definition changed; reload before updating')
        if conn.transport in {"http", "sse"}:
            from termx.mcp.ssrf import SSRFError, validate_url

            try:
                validate_url(conn.url, lan=conn.lan)
            except SSRFError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        try:path = ctx.state.mcp_registry().save(conn,expected_digest=input.expected_digest)
        except ConnectionError_ as exc:raise HTTPException(409,str(exc)) from exc
        return {"connection": conn.as_dict(), "path": path}

    @strawberry.mutation
    @resolver
    async def set_mcp_credential(self,info: Ctx,conn_id: str,binding: str,value: str | None,expected_digest: str,kind: str = 'env') -> JSON:
        """Write-only host credential binding; null removes the exact binding."""
        ctx=info.context;ctx.require_host('agent-control')
        conn=_conn_or_404(ctx.state,conn_id)
        if definition_digest(conn)!=expected_digest:raise HTTPException(409,'MCP definition changed; reload before updating')
        if kind not in {'env','header','oauth_client_secret'}:raise HTTPException(400,'Unknown MCP credential binding kind')
        if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,63}' if kind=='env' else r'[A-Za-z][A-Za-z0-9_-]{0,63}',binding):
            raise HTTPException(400,'Invalid credential binding name')
        if kind=='env' and (conn.transport!='stdio' or binding!=binding.upper()):raise HTTPException(400,'Environment bindings require stdio and uppercase variable names')
        if kind=='header' and (conn.transport=='stdio' or binding.lower() in {'host','connection','content-length','cookie','origin','referer','transfer-encoding'}):raise HTTPException(400,'Invalid HTTP credential header')
        if kind=='oauth_client_secret' and (conn.auth_method!='oauth' or binding!='client_secret'):raise HTTPException(400,'OAuth secret binding must be client_secret for an OAuth connection')
        if value is not None and (not value or len(value)>16384 or any(c in value for c in '\r\n\0')):raise HTTPException(400,'Credential must be bounded single-line text')
        ref=f'mcp.{conn.slug}.{kind}.{binding.upper() if kind=="env" else binding.lower()}'
        from uuid import uuid4
        conn.credential_revision=uuid4().hex
        if kind=='env':
            conn.secret_refs=[r for r in conn.secret_refs if r!=ref]
            if value is not None:conn.secret_refs.append(ref)
        elif kind=='header':
            conn.header_secret_refs={k:v for k,v in conn.header_secret_refs.items() if k.lower()!=binding.lower()}
            if value is not None:conn.header_secret_refs[binding]=ref
        else:conn.client_secret_ref=ref if value is not None else None
        # Tear down the old pooled transport before rotating credentials.
        await ctx.state.mcp_pool.disconnect(conn.id)
        ctx.require_host('agent-control')
        registry=ctx.state.mcp_registry()
        from termx.mcp.registry import _WRITE_LOCK
        def persist():
            with _WRITE_LOCK:
                current=_conn_or_404(ctx.state,conn.id)
                if definition_digest(current)!=expected_digest:raise HTTPException(409,'MCP definition changed; reload before updating')
                ctx.require_host('agent-control')
                try:
                    if value is None:ctx.state.credentials.delete(ref)
                    else:ctx.state.credentials.set(ref,value)
                except RuntimeError:raise HTTPException(503,'The host credential store is unavailable; unlock or configure the operating-system credential store') from None
                registry.save(conn,expected_digest=expected_digest)
        await asyncio.to_thread(persist)
        return {'connection':conn.as_dict(),'definition_digest':definition_digest(conn),'configured':value is not None,'binding':binding,'kind':kind}

    @strawberry.mutation
    @resolver
    def trust_mcp_connection(self, info: Ctx, conn_id: str,expected_digest: str) -> JSON:
        """Explicit user consent: flip a def's ``trust`` to trusted on disk."""
        ctx = info.context
        ctx.require_host('agent-control')
        conn = _conn_or_404(ctx.state, conn_id)
        if definition_digest(conn)!=expected_digest:raise HTTPException(409,'MCP definition changed; inspect the exact configuration before granting trust')
        conn.trust = "trusted"
        try:ctx.state.mcp_registry().save(conn,expected_digest=expected_digest)
        except ConnectionError_:raise HTTPException(409,'MCP definition changed; reload before granting trust') from None
        return {"connection": conn.as_dict()}

    @strawberry.mutation
    @resolver
    async def connect_mcp_connection(self, info: Ctx, conn_id: str,project_id: str | None = None) -> T.McpConnectResult:
        ctx = info.context
        ctx.require('agent-control')
        ctx.require('agent-run')
        state = ctx.state
        conn = _conn_or_404(state, conn_id)
        authorize_mcp(state,conn,'agent-run',credential=ctx.secret,project_id=project_id)
        if not conn.enabled:
            raise HTTPException(status_code=400, detail="connection is disabled")
        if not conn.trusted:
            raise HTTPException(
                status_code=403,
                detail="connection is untrusted — POST .../trust first",
            )
        auth_url_ready = asyncio.Event()

        async def on_auth_url(url: str) -> None:
            authorize_mcp(state,conn,'agent-run',credential=ctx.secret,project_id=project_id)
            state.mcp_auth_urls[conn.id] = url
            auth_url_ready.set()

        task = asyncio.create_task(
            state.mcp_pool.connect(
                conn, on_auth_url=on_auth_url, loopback=state.mcp_loopback,project_id=project_id
            )
        )
        waiter=asyncio.create_task(auth_url_ready.wait())
        try:
            await asyncio.wait({task,waiter},timeout=30.0,return_when=asyncio.FIRST_COMPLETED)
        finally:
            waiter.cancel();await asyncio.gather(waiter,return_exceptions=True)
        if auth_url_ready.is_set():
            authorize_mcp(state,conn,'agent-run',credential=ctx.secret,project_id=project_id)
            def finished(result):
                state.mcp_auth_urls.pop(conn.id,None)
                if not result.cancelled():result.exception()
            task.add_done_callback(finished)
            return T.McpConnectResult.wrap(
                {
                    "connection": conn.id,
                    "status": "auth_required",
                    "auth_url": state.mcp_auth_urls[conn.id],
                    "note": "open this URL on the host to complete sign-in",
                }
            )
        if not task.done():
            task.cancel();await asyncio.gather(task,return_exceptions=True)
            raise HTTPException(502,'MCP connection startup timed out; no transport was retained')
        try:
            catalog = task.result()
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        state.mcp_auth_urls.pop(conn.id, None)
        authorize_mcp(state,conn,'agent-run',credential=ctx.secret,project_id=project_id)
        return T.McpConnectResult.wrap(
            {
                "connection": conn.id,
                "status": "connected",
                "catalog": catalog,
                "fingerprint": state.mcp_pool._connections[conn.id].fingerprint,
            }
        )

    @strawberry.mutation
    @resolver
    async def disconnect_mcp_connection(self, info: Ctx, conn_id: str) -> JSON:
        ctx = info.context
        ctx.require_host('agent-control')
        await ctx.state.mcp_pool.disconnect(
            conn_id if conn_id.startswith("connection.") else f"connection.{conn_id}"
        )
        return {"disconnected": conn_id}

    @strawberry.mutation
    @resolver
    async def call_mcp_tool(
        self, info: Ctx, conn_id: str, tool: str, input: McpToolCallInput | None = None,project_id: str | None = None
    ) -> JSON:
        ctx = info.context
        ctx.require('agent-control')
        ctx.require('agent-run')
        conn = _conn_or_404(ctx.state, conn_id)
        authorize_mcp(ctx.state,conn,'agent-run',credential=ctx.secret,project_id=project_id)
        fq = conn.id
        catalog = ctx.state.mcp_pool.catalog(fq,project_id=project_id) or {}
        approved = conn.approved_tools
        tool_names = {t["name"] for t in catalog.get("tools", [])}
        if tool not in tool_names:
            raise HTTPException(status_code=404, detail="tool not in catalog")
        if "*" not in approved and tool not in approved:
            raise HTTPException(status_code=403, detail="tool not in approved_tools")
        try:
            arguments=(input.arguments if input else None) or {}
            import json
            if not isinstance(arguments,dict) or len(json.dumps(arguments).encode())>64000:raise HTTPException(400,'Provide bounded MCP tool arguments')
            result = await ctx.state.mcp_pool.call_tool(
                fq, tool, dict(arguments),project_id=project_id,
                authorize=lambda current:authorize_mcp(ctx.state,current,'agent-run',credential=ctx.secret,project_id=project_id)
            )
        except McpConnectionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except PermissionError:raise HTTPException(403,'MCP connection project or tool authority changed') from None
        return {"result": result}

    @strawberry.mutation
    @resolver
    async def delete_mcp_connection(self, info: Ctx, conn_id: str,expected_digest: str) -> JSON:
        ctx = info.context
        ctx.require_host('agent-control')
        conn = _conn_or_404(ctx.state, conn_id)
        if definition_digest(conn)!=expected_digest:raise HTTPException(409,'MCP definition changed; reload before deleting')
        if not conn.path:
            raise HTTPException(status_code=409, detail="connection has no file to delete")
        await ctx.state.mcp_pool.disconnect(conn.id)
        def remove():
            from termx.mcp.registry import _WRITE_LOCK
            with _WRITE_LOCK:
                ctx.require_host('agent-control')
                current=_conn_or_404(ctx.state,conn.id)
                if definition_digest(current)!=expected_digest:raise HTTPException(409,'MCP definition changed; reload before deleting')
                os.unlink(conn.path)
        await asyncio.to_thread(remove)
        return {"deleted": conn.id}
