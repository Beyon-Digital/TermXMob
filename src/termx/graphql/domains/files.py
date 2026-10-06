"""Files domain: filesystem listing, projects, project files, git, previews,
LSP server info, ports + process discovery.

Binary transfer (fs download/upload, project download/upload, agent artifact
download, task export) stays REST — GraphQL is not a byte transport.
"""

from __future__ import annotations

import strawberry
from fastapi import HTTPException
from strawberry.scalars import JSON
from strawberry.types import Info

from termx import git_ops, lsp
from termx.audit import log_event
from termx.config import list_dir_entries
from termx.graphql.context import TermxContext
from termx.graphql.errors import resolver
from termx.graphql.inputs import (
    FileActionInput,
    FileSaveInput,
    FileSearchInput,
    GitBranchInput,
    GitHunkInput,
    GitStageInput,
    PreviewFromPortInput,
    PreviewInput,
    ProjectInput,
)
from termx.graphql import types as T

Ctx = Info[TermxContext, None]


@strawberry.type
class FilesQueries:
    @strawberry.field
    @resolver
    def fs(self, info: Ctx, path: str | None = None, files: bool = False) -> T.FsListing:
        ctx = info.context
        ctx.require("files-read")
        try:
            return T.FsListing.wrap(list_dir_entries(path, include_files=files))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @strawberry.field
    @resolver
    def projects(self, info: Ctx) -> list[T.Project]:
        info.context.require("files-read")
        return T.Project.wrap_all(info.context.state.projects.projects())

    @strawberry.field
    @resolver
    def project(self, info: Ctx, project_id: str) -> T.Project:
        ctx = info.context
        ctx.require("files-read")
        try:
            return T.Project.wrap(ctx.state.projects.project(project_id))
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=404, detail="project not found") from exc

    @strawberry.field
    @resolver
    def project_tree(
        self, info: Ctx, project_id: str, path: str = "", offset: int = 0, limit: int = 200
    ) -> T.ProjectTree:
        ctx = info.context
        ctx.require("files-read")
        return T.ProjectTree.wrap(ctx.state.projects.listing(project_id, path, offset, limit))

    @strawberry.field
    @resolver
    def project_file(self, info: Ctx, project_id: str, path: str) -> T.ProjectFile:
        ctx = info.context
        ctx.require("files-read")
        return T.ProjectFile.wrap(ctx.state.projects.read(project_id, path))

    @strawberry.field
    @resolver
    def project_search(
        self, info: Ctx, project_id: str, input: FileSearchInput
    ) -> T.SearchResults:
        ctx = info.context
        ctx.require("files-read")
        results = ctx.state.projects.search(
            project_id, input.query, content=input.content, case_sensitive=input.case_sensitive
        )
        return T.SearchResults.wrap(results if isinstance(results, dict) else {"results": results})

    @strawberry.field
    @resolver
    def project_previews(self, info: Ctx, project_id: str) -> list[T.ProjectPreview]:
        ctx = info.context
        ctx.require("files-read")
        return T.ProjectPreview.wrap_all(ctx.state.projects.previews(project_id))

    @strawberry.field
    @resolver
    def lsp_servers(self, info: Ctx, project_id: str) -> JSON:
        ctx = info.context
        ctx.require("files-read")
        ctx.state.projects.project(project_id)
        return {"servers": lsp.server_snapshot()}

    @strawberry.field
    @resolver
    def git_status(self, info: Ctx, project_id: str) -> T.GitStatus:
        ctx = info.context
        ctx.require("git-read")
        return T.GitStatus.wrap(git_ops.status(_project_root(ctx, project_id)))

    @strawberry.field
    @resolver
    def git_diff(self, info: Ctx, project_id: str, path: str, staged: bool = False) -> JSON:
        ctx = info.context
        ctx.require("git-read")
        return git_ops.diff(_project_root(ctx, project_id), path, staged=staged)

    @strawberry.field
    @resolver
    def git_branches(self, info: Ctx, project_id: str) -> JSON:
        ctx = info.context
        ctx.require("git-read")
        return git_ops.branches(_project_root(ctx, project_id))

    @strawberry.field
    @resolver
    def ports(self, info: Ctx, project_id: str | None = None) -> T.PortsResult:
        ctx = info.context
        ctx.require("machine-view")
        from termx.activity import _project_for
        from termx.processes import listeners, supported

        try:
            projects = ctx.state.projects.projects()
        except Exception:
            projects = []
        entries = listeners()
        for entry in entries:
            entry["project_id"] = _project_for(projects, entry.get("cwd"))
        if project_id:
            entries = [entry for entry in entries if entry["project_id"] == project_id]
        return T.PortsResult.wrap({"ports": entries, "supported": supported()})

    @strawberry.field
    @resolver
    def processes(self, info: Ctx, project_id: str | None = None) -> T.ProcessesResult:
        ctx = info.context
        ctx.require("machine-view")
        from termx.activity import _project_for
        from termx.processes import supported, termx_processes

        try:
            projects = ctx.state.projects.projects()
        except Exception:
            projects = []
        roots = [str(project["path"]) for project in projects if project.get("path")]
        procs = termx_processes(roots)
        for proc in procs:
            proc["project_id"] = _project_for(projects, proc.get("cwd"))
        if project_id:
            procs = [proc for proc in procs if proc["project_id"] == project_id]
        return T.ProcessesResult.wrap({"processes": procs, "supported": supported()})


def _project_root(ctx: TermxContext, project_id: str) -> str:
    return ctx.state.projects.project(project_id)["path"]


@strawberry.type
class FilesMutations:
    @strawberry.mutation
    @resolver
    def register_project(self, info: Ctx, input: ProjectInput) -> T.Project:
        ctx = info.context
        ctx.require("files-write")
        try:
            project = ctx.state.projects.register(input.path, input.name)
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        log_event("project_register", project_id=project["id"])
        return T.Project.wrap(project)

    @strawberry.mutation
    @resolver
    def rename_project(self, info: Ctx, project_id: str, name: str) -> T.Project:
        ctx = info.context
        ctx.require("files-write")
        return T.Project.wrap(ctx.state.projects.update_project(project_id, name))

    @strawberry.mutation
    @resolver
    def forget_project(self, info: Ctx, project_id: str) -> T.Ok:
        ctx = info.context
        ctx.require("files-write")
        ctx.state.projects.forget(project_id)
        return T.Ok.wrap({"ok": True})

    @strawberry.mutation
    @resolver
    def save_project_file(self, info: Ctx, project_id: str, input: FileSaveInput) -> JSON:
        ctx = info.context
        ctx.require("files-write")
        result = ctx.state.projects.save(
            project_id, input.path, input.content, input.revision
        )
        log_event(
            "project_file_save",
            project_id=project_id,
            path=input.path,
            size=result.get("size"),
        )
        return result

    @strawberry.mutation
    @resolver
    def project_file_action(self, info: Ctx, project_id: str, input: FileActionInput) -> JSON:
        ctx = info.context
        ctx.require("terminal-control")
        result = ctx.state.projects.mutate(
            project_id, input.action, input.path, input.destination, input.revision
        )
        log_event(
            "project_file_action", project_id=project_id, action=input.action, path=input.path
        )
        return result

    @strawberry.mutation
    @resolver
    def git_stage(self, info: Ctx, project_id: str, input: GitStageInput) -> JSON:
        ctx = info.context
        ctx.require("git-write")
        return git_ops.stage(_project_root(ctx, project_id), input.paths or [], input.stage)

    @strawberry.mutation
    @resolver
    def git_stage_hunk(self, info: Ctx, project_id: str, input: GitHunkInput) -> JSON:
        ctx = info.context
        ctx.require("git-write")
        return git_ops.stage_hunk(_project_root(ctx, project_id), input.patch, input.stage)

    @strawberry.mutation
    @resolver
    def git_commit(self, info: Ctx, project_id: str, message: str) -> JSON:
        ctx = info.context
        ctx.require("git-write")
        result = git_ops.commit(_project_root(ctx, project_id), message)
        log_event("git_commit", project_id=project_id)
        return result

    @strawberry.mutation
    @resolver
    def git_branch(self, info: Ctx, project_id: str, input: GitBranchInput) -> JSON:
        ctx = info.context
        ctx.require("git-write")
        result = git_ops.switch_branch(
            _project_root(ctx, project_id), input.name, input.create
        )
        log_event("git_branch", project_id=project_id, create=input.create)
        return result

    @strawberry.mutation
    @resolver
    def git_remote_op(self, info: Ctx, project_id: str, operation: str) -> JSON:
        ctx = info.context
        ctx.require("git-write")
        root = _project_root(ctx, project_id)
        if operation == "fetch":
            return git_ops.fetch(root)
        if operation == "pull":
            return git_ops.pull(root)
        if operation == "push":
            log_event("git_push", project_id=project_id)
            return git_ops.push(root)
        raise HTTPException(status_code=404, detail="unknown git operation")

    @strawberry.mutation
    @resolver
    def create_project_preview(
        self, info: Ctx, project_id: str, input: PreviewInput
    ) -> T.ProjectPreview:
        ctx = info.context
        ctx.require("files-write")
        result = ctx.state.projects.add_preview(project_id, input.name, input.url)
        log_event("project_preview_create", project_id=project_id)
        return T.ProjectPreview.wrap(result)

    @strawberry.mutation
    @resolver
    def delete_project_preview(self, info: Ctx, project_id: str, preview_id: str) -> T.Ok:
        ctx = info.context
        ctx.require("files-write")
        ctx.state.projects.delete_preview(project_id, preview_id)
        return T.Ok.wrap({"ok": True})

    @strawberry.mutation
    @resolver
    def preview_from_port(
        self, info: Ctx, project_id: str, input: PreviewFromPortInput
    ) -> T.PreviewFromPortResult:
        ctx = info.context
        ctx.require("files-write")
        from termx.processes import listeners

        match = next((entry for entry in listeners() if entry["port"] == input.port), None)
        if match is None:
            raise HTTPException(status_code=404, detail="no listening process on that port")
        url = match.get("url") or f"http://127.0.0.1:{input.port}"
        result = ctx.state.projects.add_preview(project_id, input.name, str(url))
        log_event("project_preview_from_port", project_id=project_id, port=input.port)
        return T.PreviewFromPortResult.wrap({"preview": result, "listener": match})
