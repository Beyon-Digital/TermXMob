"""Skills domain: review a skill folder for install. Installing stays a host-admin click on the review."""

from __future__ import annotations

from typing import Callable

import strawberry
from strawberry.scalars import JSON
from strawberry.types import Info

from termx.graphql import types as T
from termx.graphql.context import TermxContext
from termx.graphql.domains.desktop_workspace import principal as session_principal
from termx.graphql.errors import fail, resolver
from termx.workspace.extensions import PROGRESSIVE_BYTES
from termx.workspace.store import Conflict

Ctx = Info[TermxContext, None]


@strawberry.type
class SkillImportFile(T.DictBacked):
    """One text file of an imported skill. Scripts and references are inert text that TermX does not run (`note`)."""

    path: str
    bytes: int
    kind: str
    note: str | None


@strawberry.type
class SkillImportSkill(T.DictBacked):
    id: str
    name: str
    description: str
    files: list[SkillImportFile]


@strawberry.type
class SkippedSkillFile(T.DictBacked):
    """A file left out of the import: binary, hidden, a dependency folder, or outside any skill."""

    path: str
    reason: str


@strawberry.type
class SkillImportPreview(T.DictBacked):
    """A pending review. `digest` is the value to pass when installing; the review expires after ten minutes.
    `progressive` is true when the skills are large enough to be listed by file and read on demand."""

    preview_id: str
    digest: str
    bundle_id: str
    version: str
    permissions_added: list[str]
    permissions_removed: list[str]
    file_count: int
    bytes: int
    progressive: bool
    expires_at: float
    previous_revision: int | None
    skills: list[SkillImportSkill]
    skipped: list[SkippedSkillFile]


@strawberry.type
class DetectedSkillSource(T.DictBacked):
    """A skill folder already on this host. `token` is the only way to choose it; host paths are never exposed."""

    token: str
    folder: str
    name: str
    description: str
    source: str
    project_id: str | None


@strawberry.input
class SkillImportInput:
    """Either `files` (a folder the browser read: relative path to text, with `name` the folder's name) or `token`."""

    version: str = "1"
    name: str | None = None
    files: JSON | None = None
    skipped: list[str] | None = None
    token: str | None = None
    project_id: str | None = None


def _run(fn: Callable, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except KeyError:
        fail(404, "Resource not found")
    except PermissionError as exc:
        fail(403, str(exc) or "Permission denied")
    except Conflict as exc:
        fail(409, str(exc))
    except (ValueError, OSError) as exc:
        fail(400, str(exc))


def _preview_view(row: dict) -> SkillImportPreview:
    imported = row["import"]
    skills = [{**skill, "files": SkillImportFile.wrap_all(skill["files"])} for skill in imported["skills"]]
    return SkillImportPreview.wrap({
        "preview_id": row["id"],
        "digest": row["digest"],
        "bundle_id": row["bundle"]["id"],
        "version": row["bundle"]["version"],
        "permissions_added": row["permission_diff"]["added"],
        "permissions_removed": row["permission_diff"]["removed"],
        "file_count": len(row["bundle"]["files"]),
        "bytes": imported["bytes"],
        "progressive": imported["bytes"] > PROGRESSIVE_BYTES,
        "expires_at": row["expires_at"],
        "previous_revision": row["previous_revision"],
        "skills": SkillImportSkill.wrap_all(skills),
        "skipped": SkippedSkillFile.wrap_all(imported["skipped"]),
    })


@strawberry.type
class SkillQueries:
    @strawberry.field
    @resolver
    def detected_skill_sources(self, info: Ctx, project_id: str | None = None) -> list[DetectedSkillSource]:
        ctx = info.context
        ctx.require("host-admin")
        rows = _run(ctx.state.skill_sources.detected, session_principal(info), project_id)
        return DetectedSkillSource.wrap_all(rows)


@strawberry.type
class SkillMutations:
    @strawberry.mutation
    @resolver
    def preview_skill_import(self, info: Ctx, input: SkillImportInput) -> SkillImportPreview:
        """Reads the folder, checks it and stores a review. Nothing is installed; install the review from Settings."""
        ctx = info.context
        ctx.require("host-admin")
        if input.files is not None and not isinstance(input.files, dict):
            fail(400, "files must map each relative path to its text")
        row = _run(
            ctx.state.skill_sources.preview_import, session_principal(info), files=input.files,
            skipped=input.skipped or [], token=input.token, name=input.name, project_id=input.project_id,
            version=input.version,
        )
        return _preview_view(row)
