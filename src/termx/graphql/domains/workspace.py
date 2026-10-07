"""Workspace domain: terminal sessions, saved workspace, preferences,
saved commands, saved directories."""

from __future__ import annotations

import strawberry
from fastapi import HTTPException
from strawberry.types import Info

from termx.audit import log_event
from termx.config import WorkspaceSession, validate_cwd, validate_shell
from termx.graphql.context import TermxContext
from termx.graphql.errors import resolver
from termx.graphql.inputs import (
    CommandInput,
    CommandPatchInput,
    CreateSessionInput,
    DirectoryInput,
    PreferencesInput,
    WorkspaceSessionInput,
)
from termx.graphql import types as T
from termx.machine import machine_snapshot
from termx.sessions import DEFAULT_COLS, DEFAULT_ROWS, default_argv
from termx.terminals import TerminalError

Ctx = Info[TermxContext, None]


@strawberry.type
class WorkspaceQueries:
    @strawberry.field
    @resolver
    def sessions(self, info: Ctx) -> list[T.SessionInfo]:
        info.context.require("terminal-view")
        return T.SessionInfo.wrap_all(
            info.context.visible('terminal-view', 'terminal', [s.snapshot() for s in info.context.state.sessions.list()])
        )

    @strawberry.field
    @resolver
    def workspace(self, info: Ctx) -> T.Workspace:
        info.context.require_host('machine-view')
        workspace = info.context.state.store.get_workspace()
        return T.Workspace.wrap(
            {
                "sessions": [item.public() for item in workspace.sessions],
                "saved_at": workspace.saved_at,
            }
        )

    @strawberry.field
    @resolver
    def preferences(self, info: Ctx) -> T.Preferences:
        ctx = info.context
        ctx.require_host('machine-view')
        prefs = ctx.state.store.get().terminal
        return T.Preferences.wrap(
            {
                "shell": prefs.shell,
                "cwd": prefs.cwd,
                "shells": machine_snapshot(ctx.state.store)["shells"],
            }
        )

    @strawberry.field
    @resolver
    def commands(self, info: Ctx) -> list[T.SavedCommand]:
        info.context.require_host('machine-view')
        return T.SavedCommand.wrap_all(
            [item.public() for item in info.context.state.store.list_commands()]
        )

    @strawberry.field
    @resolver
    def directories(self, info: Ctx) -> T.DirectoriesResult:
        ctx = info.context
        ctx.require_host('machine-view')
        prefs = ctx.state.store.get().terminal
        return T.DirectoriesResult.wrap(
            {
                "cwd": prefs.cwd,
                "directories": [item.public() for item in ctx.state.store.list_directories()],
            }
        )


@strawberry.type
class WorkspaceMutations:
    @strawberry.mutation
    @resolver
    async def create_session(self, info: Ctx, input: CreateSessionInput | None = None) -> T.SessionInfo:
        ctx = info.context
        ctx.require("terminal-control")
        input = input or CreateSessionInput()
        prefs = ctx.state.store.get().terminal
        if input.sandbox_profile not in (None, "host", "workspace"):
            raise HTTPException(
                status_code=422, detail="sandbox_profile must be host or workspace"
            )
        try:
            shell = validate_shell(input.shell or prefs.shell)
            cwd = validate_cwd(input.cwd or prefs.cwd)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        project_id = ctx.require_path('terminal-control', cwd)
        try:
            session = ctx.state.sessions.create(
                cols=input.cols,
                rows=input.rows,
                title=input.title,
                argv=default_argv(shell),
                cwd=cwd,
                shell=shell,
                sandbox_profile=input.sandbox_profile,
            )
        except TerminalError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        ctx.claim('terminal', session.id, project_id)
        return T.SessionInfo.wrap(session.snapshot())

    @strawberry.mutation
    @resolver
    def rename_session(self, info: Ctx, session_id: str, title: str) -> T.SessionInfo:
        ctx = info.context
        ctx.require_resource('terminal-control', 'terminal', session_id)
        session = ctx.state.sessions.rename(session_id, title)
        if session is None:
            raise HTTPException(status_code=404, detail="session not found")
        return T.SessionInfo.wrap(session.snapshot())

    @strawberry.mutation
    @resolver
    def delete_session(self, info: Ctx, session_id: str) -> T.Ok:
        ctx = info.context
        ctx.require_resource('terminal-control', 'terminal', session_id)
        if not ctx.state.sessions.kill(session_id):
            raise HTTPException(status_code=404, detail="session not found")
        return T.Ok.wrap({"ok": True})

    @strawberry.mutation
    @resolver
    def save_workspace(self, info: Ctx, sessions: list[WorkspaceSessionInput]) -> T.Workspace:
        ctx = info.context
        ctx.require_host('host-admin')
        saved = ctx.state.store.save_workspace(
            [
                WorkspaceSession(title=item.title, shell=item.shell, cwd=item.cwd)
                for item in sessions
            ]
        )
        return T.Workspace.wrap(
            {
                "sessions": [item.public() for item in saved.sessions],
                "saved_at": saved.saved_at,
            }
        )

    @strawberry.mutation
    @resolver
    def restore_workspace(self, info: Ctx) -> T.WorkspaceRestoreResult:
        ctx = info.context
        ctx.require_host('host-admin')
        # Return host-owned live sessions unchanged. Replaying persisted specs
        # on every new client connection duplicated PTYs and then compounded
        # the duplicates when the client saved its next workspace snapshot.
        with ctx.state.workspace_restore_lock:
            live = ctx.state.sessions.list()
            if live:
                return T.WorkspaceRestoreResult.wrap(
                    {
                        "sessions": [session.snapshot() for session in live],
                        "restored": 0,
                        "already_running": len(live),
                    }
                )

            prefs = ctx.state.store.get().terminal
            workspace = ctx.state.store.get_workspace()
            restored = []
            for item in workspace.sessions:
                try:
                    shell = validate_shell(item.shell or prefs.shell)
                except ValueError:
                    shell = prefs.shell
                try:
                    cwd = validate_cwd(item.cwd or prefs.cwd)
                except ValueError:
                    cwd = prefs.cwd
                session = ctx.state.sessions.create(
                    cols=DEFAULT_COLS,
                    rows=DEFAULT_ROWS,
                    title=item.title or None,
                    argv=default_argv(shell),
                    cwd=cwd,
                    shell=shell,
                )
                restored.append(session.snapshot())
        if restored:
            log_event("workspace_restore", count=len(restored))
        return T.WorkspaceRestoreResult.wrap(
            {"sessions": restored, "restored": len(restored), "already_running": 0}
        )

    @strawberry.mutation
    @resolver
    def set_preferences(self, info: Ctx, input: PreferencesInput) -> T.Preferences:
        ctx = info.context
        ctx.require_host('host-admin')
        try:
            prefs = ctx.state.store.update_terminal(shell=input.shell, cwd=input.cwd)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return T.Preferences.wrap({"shell": prefs.shell, "cwd": prefs.cwd})

    @strawberry.mutation
    @resolver
    def create_command(self, info: Ctx, input: CommandInput) -> T.SavedCommand:
        ctx = info.context
        ctx.require_host('host-admin')
        try:
            item = ctx.state.store.add_command(input.name, input.command, input.confirm)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        log_event("command_create", command_id=item.id, name=item.name)
        return T.SavedCommand.wrap(item.public())

    @strawberry.mutation
    @resolver
    def patch_command(self, info: Ctx, command_id: str, input: CommandPatchInput) -> T.SavedCommand:
        ctx = info.context
        ctx.require_host('host-admin')
        try:
            item = ctx.state.store.patch_command(
                command_id, input.name, input.command, input.confirm
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if item is None:
            raise HTTPException(status_code=404, detail="command not found")
        return T.SavedCommand.wrap(item.public())

    @strawberry.mutation
    @resolver
    def delete_command(self, info: Ctx, command_id: str) -> T.Ok:
        ctx = info.context
        ctx.require_host('host-admin')
        if not ctx.state.store.delete_command(command_id):
            raise HTTPException(status_code=404, detail="command not found")
        log_event("command_delete", command_id=command_id)
        return T.Ok.wrap({"ok": True})

    @strawberry.mutation
    @resolver
    def reorder_commands(self, info: Ctx, order: list[str]) -> list[T.SavedCommand]:
        ctx = info.context
        ctx.require_host('host-admin')
        items = ctx.state.store.reorder_commands(order)
        return T.SavedCommand.wrap_all([item.public() for item in items])

    @strawberry.mutation
    @resolver
    def create_directory(self, info: Ctx, input: DirectoryInput) -> T.SavedDirectory:
        ctx = info.context
        ctx.require_host('host-admin')
        try:
            item = ctx.state.store.add_directory(input.name, input.path)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        log_event("directory_create", directory_id=item.id, name=item.name)
        return T.SavedDirectory.wrap(item.public())

    @strawberry.mutation
    @resolver
    def delete_directory(self, info: Ctx, directory_id: str) -> T.Ok:
        ctx = info.context
        ctx.require_host('host-admin')
        if not ctx.state.store.delete_directory(directory_id):
            raise HTTPException(status_code=404, detail="directory not found")
        log_event("directory_delete", directory_id=directory_id)
        return T.Ok.wrap({"ok": True})

    @strawberry.mutation
    @resolver
    def use_directory(self, info: Ctx, directory_id: str) -> T.UseDirectoryResult:
        ctx = info.context
        ctx.require_host('host-admin')
        try:
            item = ctx.state.store.use_directory(directory_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="directory not found") from None
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        log_event("directory_use", directory_id=item.id, path=item.path)
        return T.UseDirectoryResult.wrap({"directory": item.public(), "cwd": item.path})
