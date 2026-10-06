"""Runbooks domain: named multi-step workflows + run history."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import strawberry
from fastapi import HTTPException
from strawberry.types import Info

from termx.audit import log_event
from termx.graphql.context import TermxContext
from termx.graphql.errors import resolver
from termx.graphql.inputs import RunbookInput, RunbookPatchInput
from termx.graphql import types as T

Ctx = Info[TermxContext, None]


@strawberry.type
class RunbookQueries:
    @strawberry.field
    @resolver
    def runbooks(self, info: Ctx, project_id: str | None = None) -> list[T.Runbook]:
        ctx = info.context
        ctx.require("machine-view")
        return T.Runbook.wrap_all(ctx.state.agent_store.list_runbooks(project_id))

    @strawberry.field
    @resolver
    def runbook(self, info: Ctx, runbook_id: str) -> T.RunbookDetail:
        ctx = info.context
        ctx.require("machine-view")
        runbook = ctx.state.agent_store.get_runbook(runbook_id)
        if runbook is None:
            raise HTTPException(status_code=404, detail="runbook not found")
        return T.RunbookDetail.wrap(
            {
                "runbook": runbook,
                "runs": ctx.state.agent_store.list_runbook_runs(runbook_id, limit=20),
            }
        )

    @strawberry.field
    @resolver
    def runbook_runs(self, info: Ctx, runbook_id: str | None = None) -> list[T.RunbookRun]:
        ctx = info.context
        ctx.require("machine-view")
        return T.RunbookRun.wrap_all(ctx.state.agent_store.list_runbook_runs(runbook_id))

    @strawberry.field
    @resolver
    def runbook_run(self, info: Ctx, run_id: str) -> T.RunbookRun:
        ctx = info.context
        ctx.require("machine-view")
        run = ctx.state.agent_store.get_runbook_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="run not found")
        return T.RunbookRun.wrap(run)


@strawberry.type
class RunbookMutations:
    @strawberry.mutation
    @resolver
    def create_runbook(self, info: Ctx, input: RunbookInput) -> T.Runbook:
        from termx.runbooks import validate_steps

        ctx = info.context
        ctx.require("terminal-control")
        if input.project_id:
            ctx.state.projects.project(input.project_id)
        try:
            steps = validate_steps(input.steps)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        runbook = ctx.state.agent_store.create_runbook(
            name=input.name.strip(), project_id=input.project_id, steps=steps
        )
        log_event("runbook_create", runbook_id=runbook["id"])
        return T.Runbook.wrap(runbook)

    @strawberry.mutation
    @resolver
    def update_runbook(
        self, info: Ctx, runbook_id: str, input: RunbookPatchInput
    ) -> T.Runbook:
        from termx.runbooks import validate_steps

        ctx = info.context
        ctx.require("terminal-control")
        if ctx.state.agent_store.get_runbook(runbook_id) is None:
            raise HTTPException(status_code=404, detail="runbook not found")
        updates: dict[str, Any] = {}
        if input.name is not None:
            if not input.name.strip():
                raise HTTPException(status_code=422, detail="name is required")
            updates["name"] = input.name.strip()
        if input.project_id is not None:
            ctx.state.projects.project(input.project_id)
            updates["project_id"] = input.project_id
        if input.steps is not None:
            try:
                updates["steps"] = validate_steps(input.steps)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
        runbook = ctx.state.agent_store.update_runbook(runbook_id, **updates)
        return T.Runbook.wrap(runbook)

    @strawberry.mutation
    @resolver
    def delete_runbook(self, info: Ctx, runbook_id: str) -> T.Ok:
        ctx = info.context
        ctx.require("terminal-control")
        if not ctx.state.agent_store.delete_runbook(runbook_id):
            raise HTTPException(status_code=404, detail="runbook not found")
        return T.Ok.wrap({"ok": True})

    @strawberry.mutation
    @resolver
    async def run_runbook(self, info: Ctx, runbook_id: str, profile: str = "host") -> T.RunbookRun:
        ctx = info.context
        ctx.require("terminal-control")
        runbook = ctx.state.agent_store.get_runbook(runbook_id)
        if runbook is None:
            raise HTTPException(status_code=404, detail="runbook not found")
        cwd = str(Path.home())
        if runbook.get("project_id"):
            cwd = str(ctx.state.projects.project(runbook["project_id"])["path"])
        profile = profile or "host"
        if profile not in ("host", "workspace"):
            raise HTTPException(
                status_code=422, detail="runbook profile must be host or workspace"
            )
        run = ctx.state.agent.runbooks.start(
            runbook, cwd, profile=profile, project_id=runbook.get("project_id")
        )
        log_event("runbook_run", runbook_id=runbook_id, run_id=run["id"], profile=profile)
        return T.RunbookRun.wrap(run)

    @strawberry.mutation
    @resolver
    def confirm_runbook_run(self, info: Ctx, run_id: str) -> T.RunbookRun:
        ctx = info.context
        ctx.require("terminal-control")
        try:
            run = ctx.state.agent.runbooks.confirm(run_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="run not found") from exc
        return T.RunbookRun.wrap(run)

    @strawberry.mutation
    @resolver
    async def cancel_runbook_run(self, info: Ctx, run_id: str) -> T.RunbookRun:
        ctx = info.context
        ctx.require("terminal-control")
        try:
            run = await ctx.state.agent.runbooks.cancel(run_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="run not found") from exc
        return T.RunbookRun.wrap(run)
