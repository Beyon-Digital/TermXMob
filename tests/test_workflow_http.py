from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from test_workspace_workflows import OWNER, setup_service
from termx.workspace.store import Conflict
from termx.workspace.workflow_http import build_workflow_router


def client_for(workspace):
    def call(fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except KeyError:
            raise HTTPException(404, "Resource not found") from None
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from None
        except Conflict as exc:
            raise HTTPException(409, str(exc)) from None
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
    app = FastAPI()
    app.include_router(build_workflow_router(workspace, lambda request: OWNER, call), prefix="/api/workspace")
    return TestClient(app)


def test_rest_definition_conflicts_archive_and_fixed_routes(tmp_path):
    workspace, _, _ = setup_service(tmp_path)
    with client_for(workspace) as client:
        caps = client.get("/api/workspace/workflows/capabilities")
        assert caps.status_code == 200 and caps.json()["runtime"]["available"]
        created = client.post("/api/workspace/workflows", json={"request_id": "create-001", "name": "Review", "source": "return args;"})
        assert created.status_code == 200
        row = created.json()
        assert client.get("/api/workspace/workflows").json()["workflows"][0]["id"] == row["id"]
        assert client.get("/api/workspace/workflows/runs").json() == {"runs": []}
        changed = client.patch("/api/workspace/workflows/" + row["id"], json={"revision": 1, "changes": {"archived": True}})
        assert changed.status_code == 200 and changed.json()["archived"]
        stale = client.patch("/api/workspace/workflows/" + row["id"], json={"revision": 1, "changes": {"name": "Stale"}})
        assert stale.status_code == 409
        assert client.get("/api/workspace/workflows").json() == {"workflows": []}
        assert client.get("/api/workspace/workflows?archived=true").json()["workflows"][0]["id"] == row["id"]
    workspace.store.close()


def test_rest_rejects_trusted_fields_and_unsupported_schema(tmp_path):
    workspace, _, _ = setup_service(tmp_path)
    with client_for(workspace) as client:
        body = {"request_id": "run-00001", "source": "return 1;", "managed_session_id": "spoof"}
        assert client.post("/api/workspace/workflows/runs", json=body).status_code == 422
        body = {"source": "return 1;", "input_schema": {"type": "object", "$ref": "https://attacker/schema"}}
        assert client.post("/api/workspace/workflows/validate", json=body).status_code == 400
        assert client.get("/api/workspace/workflows/not-found").status_code == 404
    workspace.store.close()


def test_rest_async_start_idempotency_and_cancel(tmp_path):
    workspace, runtime, _ = setup_service(tmp_path)
    with client_for(workspace) as client:
        body = {"request_id": "run-00001", "source": "return args;", "args": {"n": 1}}
        first = client.post("/api/workspace/workflows/runs", json=body)
        assert first.status_code == 200
        run_id = first.json()["id"]
        second = client.post("/api/workspace/workflows/runs", json=body)
        assert second.status_code == 200 and second.json()["id"] == run_id
        body["args"]["n"] = 2
        assert client.post("/api/workspace/workflows/runs", json=body).status_code == 409
        detail = client.get("/api/workspace/workflows/runs/" + run_id)
        assert detail.status_code == 200 and detail.json()["status"] == "completed"
        assert client.post("/api/workspace/workflows/runs/" + run_id + "/cancel").json()["status"] == "completed"
        assert runtime.executions == 1
    workspace.store.close()
