"""Atomic workflow receipts over the existing workspace ledger.

Definitions, runs and calls use normal owner-scoped workspace records. The
request reservation and run insertion share one SQLite transaction, so losing
the HTTP response cannot launch a second execution.
"""
from __future__ import annotations

import hashlib
import json
from time import time
from uuid import uuid4

from termx.workspace.store import Conflict

DEFINITION = "workflow"
RUN = "workflow_run"
CALL = "workflow_call"
TERMINAL = frozenset({"completed", "failed", "cancelled", "needs_attention"})


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False, allow_nan=False).encode()).hexdigest()


class WorkflowStore:
    def __init__(self, store):
        self.store = store

    @staticmethod
    def _body(value):
        return {key: item for key, item in value.items() if key not in
                {"id", "owner", "project_id", "revision", "created_at", "updated_at"}}

    def reserve(self, kind, owner, request_id, request_digest, body, project_id=None):
        key = "workflow:" + kind + ":" + request_id
        db = self.store.db
        with self.store.lock:
            db.execute("BEGIN IMMEDIATE")
            try:
                previous = db.execute("SELECT * FROM requests WHERE owner=? AND key=?", (owner, key)).fetchone()
                if previous:
                    if previous["digest"] != request_digest:
                        raise Conflict("Request ID was reused with different workflow content")
                    record = self.store.get(kind, previous["result"] or "")
                    if not record:
                        raise Conflict("Workflow request needs reconciliation; it will not be replayed")
                    db.commit()
                    return record, False
                identifier, now = uuid4().hex, time()
                db.execute("INSERT INTO records VALUES(?,?,?,?,?,?,?,?)",
                           (kind, identifier, owner, project_id, 1, json.dumps(body, allow_nan=False), now, now))
                db.execute("INSERT INTO requests VALUES(?,?,?,?,?,?)",
                           (owner, key, request_digest, "dispatched", identifier, now))
                db.commit()
                return self.store.get(kind, identifier), True
            except BaseException:
                db.rollback()
                raise

    def existing(self, kind, owner, request_id, request_digest):
        with self.store.lock:
            previous = self.store.db.execute("SELECT * FROM requests WHERE owner=? AND key=?",
                (owner, "workflow:" + kind + ":" + request_id)).fetchone()
            if not previous:
                return None
            if previous["digest"] != request_digest:
                raise Conflict("Request ID was reused with different workflow content")
            row = self.store.get(kind, previous["result"] or "")
            if not row:
                raise Conflict("Workflow request needs reconciliation; it will not be replayed")
            return row

    def patch(self, kind, identifier, **changes):
        with self.store.lock:
            current = self.store.get(kind, identifier)
            if not current:
                raise KeyError(identifier)
            return self.store.update(kind, identifier, {**current, **changes}, current["revision"])

    def event(self, run_id, kind, payload=None):
        with self.store.lock:
            run = self.store.get(RUN, run_id)
            if not run:
                raise KeyError(run_id)
            sequence = run.get("event_sequence", 0) + 1
            events = [*(run.get("events") or []), {"id": sequence, "type": kind,
                       "payload": payload or {}, "created_at": time()}]
            return self.patch(RUN, run_id, events=events[-1000:], event_sequence=sequence,
                              events_truncated=sequence > 1000)

    def call(self, run_id, worker_id, operation, arguments, name):
        """Reserve before dispatch; duplicate worker IDs never repeat effects."""
        identifier = hashlib.sha256((run_id + ":" + worker_id).encode()).hexdigest()
        call_digest = digest({"operation": operation, "arguments": arguments})
        with self.store.lock:
            previous = self.store.get(CALL, identifier)
            if previous:
                if previous["digest"] != call_digest:
                    raise Conflict("Workflow call ID was reused with different arguments")
                return previous, False
            run = self.store.get(RUN, run_id)
            if not run or run["status"] not in {"running", "awaiting_approval"}:
                raise Conflict("Workflow no longer accepts calls")
            ids = list(run.get("call_ids") or [])
            # Bound the hot history index, rather than stopping execution after
            # a lifetime number of calls. Every durable receipt remains in the
            # CALL ledger and is still found directly by its idempotency hash.
            known = [row for key in ids if (row := self.store.get(CALL, key))]
            completed = [row["id"] for row in known if row["status"] in TERMINAL]
            retained = set(completed[-512:])
            ids = [row["id"] for row in known if row["status"] not in TERMINAL or row["id"] in retained]
            count = run.get("call_count", len(known)) + 1
            agents = run.get("agent_count")
            if agents is None:
                agents = sum(row["operation"] == "agent" for row in self.calls(run))
            agents += operation == "agent"
            body = {
                "run_id": run_id, "worker_id": worker_id, "digest": call_digest,
                "sequence": count, "operation": operation, "name": name,
                "status": "queued", "session_id": None, "task_id": None,
                "output": None, "error": None, "started_at": None, "finished_at": None,
            }
            db, now = self.store.db, time()
            db.execute("BEGIN IMMEDIATE")
            try:
                db.execute("INSERT INTO records VALUES(?,?,?,?,?,?,?,?)", (CALL, identifier,
                    run["owner"], run.get("project_id"), 1, json.dumps(body), now, now))
                run_body = self._body({**run, "call_ids": [*ids, identifier], "call_count": count, "agent_count": agents})
                changed = db.execute("UPDATE records SET body=?,revision=revision+1,updated=? WHERE kind=? AND id=? AND revision=?",
                    (json.dumps(run_body), now, RUN, run_id, run["revision"]))
                if changed.rowcount != 1:
                    raise Conflict("Workflow changed before call reservation")
                db.commit()
                return self.store.get(CALL, identifier), True
            except BaseException:
                db.rollback()
                raise

    def calls(self, run, *, visible=False):
        with self.store.lock:
            if visible:
                # Apply retention in SQLite: an API read does not materialize
                # every old receipt from an indefinitely running workflow.
                rows = self.store.db.execute("""SELECT * FROM records WHERE kind=? AND json_extract(body,'$.run_id')=? AND (
                    json_extract(body,'$.status') NOT IN ('completed','failed','cancelled','needs_attention') OR id IN (
                        SELECT id FROM records WHERE kind=? AND json_extract(body,'$.run_id')=?
                        AND json_extract(body,'$.status') IN ('completed','failed','cancelled','needs_attention')
                        ORDER BY json_extract(body,'$.sequence') DESC LIMIT 512))
                    ORDER BY json_extract(body,'$.sequence')""", (CALL, run["id"], CALL, run["id"]))
            else:
                rows = self.store.db.execute("SELECT * FROM records WHERE kind=? AND json_extract(body,'$.run_id')=? ORDER BY json_extract(body,'$.sequence')",
                    (CALL, run["id"]))
            return [self.store.unpack(row) for row in rows]

    def interrupt(self):
        """A fresh host never replays an unfinished JavaScript program."""
        for run in self.store.list(RUN):
            if run["status"] in TERMINAL:
                continue
            error = {"code": "host_restarted", "message": "Host restarted before the workflow finished. Inspect child tasks before starting a new run."}
            for call in self.calls(run):
                if call["status"] not in TERMINAL:
                    self.patch(CALL, call["id"], status="needs_attention", error=error, finished_at=time())
            self.patch(RUN, run["id"], status="needs_attention", error=error, finished_at=time())
            self.event(run["id"], "workflow.interrupted", {"error": error})
