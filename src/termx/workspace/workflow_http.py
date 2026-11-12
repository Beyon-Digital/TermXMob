"""Managed-session workflow routes; authentication and CSRF stay at the host."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from termx.workspace.store import Conflict


class _Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WorkflowInput(_Input):
    request_id: str = Field(min_length=8, max_length=128)
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)
    project_id: str | None = None
    language: str = "javascript"
    source: str = Field(min_length=1, max_length=65536)
    input_schema: dict | None = None
    limits: dict | None = None


class WorkflowPatch(_Input):
    revision: int = Field(ge=1)
    changes: dict


class WorkflowValidation(_Input):
    source: str = Field(min_length=1, max_length=65536)
    input_schema: dict | None = None
    limits: dict | None = None


class WorkflowStart(_Input):
    request_id: str = Field(min_length=8, max_length=128)
    workflow_id: str | None = None
    revision: int | None = Field(default=None, ge=1)
    source: str | None = Field(default=None, max_length=65536)
    name: str | None = Field(default=None, max_length=120)
    args: dict = Field(default_factory=dict)
    project_id: str | None = None
    parent_session_id: str | None = None
    limits: dict | None = None


def build_workflow_router(workspace, principal, call):
    """Return relative routes for inclusion below ``/api/workspace``."""
    router = APIRouter(prefix="/workflows")
    service = workspace.workflows

    async def invoke(fn, *args, **kwargs):
        try:
            return await fn(*args, **kwargs)
        except KeyError:
            raise HTTPException(404, "Resource not found") from None
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from None
        except Conflict as exc:
            raise HTTPException(409, str(exc)) from None
        except (ValueError, OSError) as exc:
            raise HTTPException(400, str(exc)) from None

    # Fixed routes must precede the definition identifier route.
    @router.get("/capabilities")
    def capabilities(request: Request):
        return call(service.capabilities, principal(request))

    @router.get("")
    def definitions(request: Request, project_id: str | None = None, archived: bool = False):
        return {"workflows": call(service.list, principal(request), project_id=project_id, archived=archived)}

    @router.post("/validate")
    async def validate(request: Request, body: WorkflowValidation):
        return await invoke(service.validate, principal(request), **body.model_dump())

    @router.post("")
    def create(request: Request, body: WorkflowInput):
        return call(service.create, principal(request), **body.model_dump())

    @router.post("/runs")
    async def start(request: Request, body: WorkflowStart):
        actor = principal(request)
        return await invoke(service.start, actor, **body.model_dump(),
                            managed_session_id=getattr(actor, "authority_session_id", None))

    @router.get("/runs")
    def runs(request: Request, workflow_id: str | None = None, project_id: str | None = None):
        return {"runs": call(service.runs, principal(request), workflow_id=workflow_id, project_id=project_id)}

    @router.get("/runs/{run_id}")
    def run(request: Request, run_id: str):
        return call(service.get_run, principal(request), run_id)

    @router.post("/runs/{run_id}/cancel")
    async def cancel(request: Request, run_id: str):
        return await invoke(service.cancel, principal(request), run_id)

    @router.get("/{identifier}")
    def definition(request: Request, identifier: str):
        return call(service.get, principal(request), identifier)

    @router.patch("/{identifier}")
    def update(request: Request, identifier: str, body: WorkflowPatch):
        return call(service.update, principal(request), identifier, **body.model_dump())

    return router
