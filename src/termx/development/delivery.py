"""Project Git delivery with durable action IDs and reviewable effects.

Actions are consumed before execution. A lost response is reconciled from its
record; publishing is never replayed automatically. Dirty worktrees are kept.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import sqlite3
import subprocess
import threading
import uuid
import os
from pathlib import Path
from time import time
from contextlib import contextmanager

from fastapi import HTTPException
from termx import git_ops
from termx.audit import log_event

PUBLISH = {"push", "pr-create", "pr-update", "pr-comment", "pr-review"}
OPERATIONS = {"stage", "unstage", "hunk", "commit", "branch", "fetch", "pull", "push",
              "worktree-create", "worktree-remove", *PUBLISH}


class GitHubPort:
    """GitHub CLI's authenticated API; no second credential store."""
    def call(self, root, args, payload=None):
        if not shutil.which("gh"):
            raise HTTPException(503, "GitHub CLI is required; install and sign in on the host")
        command = ["gh", *args]
        if payload is not None:
            command += ["--input", "-"]
        result = subprocess.run(command, cwd=root, capture_output=True, text=True,
                                input=json.dumps(payload) if payload is not None else None, timeout=60)
        if result.returncode:
            raise HTTPException(409, "GitHub operation failed; verify host authorization and repository permissions")
        return json.loads(result.stdout) if result.stdout.strip() else {"ok": True}

    def repository(self, root):
        return self.call(root, ["repo", "view", "--json", "nameWithOwner"])["nameWithOwner"]

    def read(self, root, number):
        return self.call(root, ["pr", "view", str(number), "--json",
            "number,title,body,url,state,headRefName,baseRefName,mergeable,statusCheckRollup,comments,reviews,files"])

    def mutate(self, root, operation, args):
        repo = self.repository(root)
        number = args.get("number")
        if operation != "pr-create" and (not isinstance(number, int) or number < 1):
            raise HTTPException(400, "A pull request number is required")
        endpoint = f"repos/{repo}/pulls"
        if operation == "pr-create":
            payload = {k: args[k] for k in ("title", "body", "head", "base")}
            payload["draft"] = bool(args.get("draft", True))
        elif operation == "pr-update":
            endpoint += f"/{number}"
            payload = {k: args[k] for k in ("title", "body", "base") if k in args}
        elif operation == "pr-comment":
            endpoint = f"repos/{repo}/issues/{number}/comments"
            payload = {"body": args["body"]}
        else:
            endpoint += f"/{number}/reviews"
            if args.get("event", "COMMENT") not in {"COMMENT", "APPROVE", "REQUEST_CHANGES"}:
                raise HTTPException(400, "Unknown review decision")
            payload = {"body": args.get("body", ""), "event": args.get("event", "COMMENT")}
            if args.get("comments"):
                payload["comments"] = args["comments"]
        return self.call(root, ["api", "--method", "PATCH" if operation == "pr-update" else "POST", endpoint], payload)


class DeliveryService:
    def __init__(self, directory: Path, github=None):
        directory.mkdir(parents=True, exist_ok=True)
        self.directory = directory
        self.path = directory / "delivery.sqlite3"
        self.lock = threading.RLock()
        self.github = github or GitHubPort()
        with self.db() as db:
            db.executescript("""CREATE TABLE IF NOT EXISTS actions (
                id TEXT PRIMARY KEY, principal TEXT, project TEXT, operation TEXT, arguments TEXT,
                digest TEXT, head TEXT, status TEXT, result TEXT, created REAL);
                CREATE TABLE IF NOT EXISTS worktrees (id TEXT PRIMARY KEY, project TEXT, root TEXT, path TEXT, branch TEXT);""")
            if 'state_digest' not in {row['name'] for row in db.execute('PRAGMA table_info(actions)')}:
                db.execute('ALTER TABLE actions ADD COLUMN state_digest TEXT')
            db.execute("UPDATE actions SET status='unknown' WHERE status='executing'")
        self.path.chmod(0o600)

    @contextmanager
    def db(self):
        connection = sqlite3.connect(self.path, timeout=20)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    @staticmethod
    def head(root):
        return git_ops._require_ok(git_ops._git(root, "rev-parse", "HEAD")).strip()

    @staticmethod
    def state_digest(root, operation):
        """Bind review to index, branch and the content this operation can use.

        Only hashes are persisted. A changed index with unchanged HEAD must not
        turn an already reviewed commit into a different commit.
        """
        digest = hashlib.sha256()
        for args in [('symbolic-ref', '-q', 'HEAD'), ('show-ref',),
                     ('config', '--get-regexp', r'^(remote\.|branch\.|push\.)')]:
            result = git_ops._git(root, *args)
            if result.returncode not in (0, 1):
                git_ops._require_ok(result)
            digest.update(result.stdout.encode())
            digest.update(b'\0')
        if operation in {'commit', 'stage', 'unstage', 'hunk', 'branch', 'pull', 'worktree-remove'}:
            digest.update(git_ops._require_ok(git_ops._git(root, 'ls-files', '--stage', '-z')).encode())
        if operation in {'stage', 'hunk', 'branch', 'pull', 'worktree-remove'}:
            # -z avoids quoting ambiguities for spaces, newlines and renames.
            changed = git_ops._require_ok(git_ops._git(root, 'status', '--porcelain=v1', '-z', '--untracked-files=all'))
            digest.update(changed.encode())
            entries = iter(changed.split('\0'))
            paths = []
            for entry in entries:
                if not entry:
                    continue
                paths.append(entry[3:])
                if 'R' in entry[:2] or 'C' in entry[:2]:
                    next(entries, None)
            if len(paths) > 10000:
                raise HTTPException(413, 'Too many changed files to review this operation')
            remaining = 50 * 1024 * 1024
            for name in sorted(paths):
                path = Path(root) / name
                digest.update(name.encode())
                if path.is_symlink():
                    digest.update(os.readlink(path).encode())
                elif path.is_file():
                    with path.open('rb') as content:
                        while chunk := content.read(65536):
                            remaining -= len(chunk)
                            if remaining < 0:
                                raise HTTPException(413, 'Changed content exceeds the review bound')
                            digest.update(chunk)
                digest.update(b'\0')
        return digest.hexdigest()

    def prepare(self, principal, project, root, operation, arguments):
        if operation not in OPERATIONS:
            raise HTTPException(400, "Unsupported delivery operation")
        body = json.dumps(arguments, sort_keys=True, separators=(",", ":"))
        if len(body) > 1_100_000:
            raise HTTPException(413, "Delivery request is too large")
        identifier = uuid.uuid4().hex
        target = self.target(project, root, arguments.get("worktree_id"))
        head = self.head(target)
        state_digest = self.state_digest(target, operation)
        with self.lock, self.db() as db:
            db.execute("INSERT INTO actions (id,principal,project,operation,arguments,digest,head,status,result,created,state_digest) VALUES (?, ?, ?, ?, ?, ?, ?, 'prepared', NULL, ?, ?)",
                       (identifier, principal, project, operation, body, hashlib.sha256(body.encode()).hexdigest(), head, time(), state_digest))
        return {"id": identifier, "operation": operation, "arguments": arguments, "head": head,
                "requires_confirmation": operation in PUBLISH or operation == "worktree-remove"}

    def record(self, identifier, principal, project):
        with self.db() as db:
            row = db.execute("SELECT * FROM actions WHERE id=? AND principal=? AND project=?", (identifier, principal, project)).fetchone()
        if row is None:
            raise HTTPException(404, "Delivery action not found")
        return dict(row)

    def records(self, principal, project):
        with self.db() as db:
            return [dict(row) for row in db.execute('SELECT * FROM actions WHERE principal=? AND project=? ORDER BY created DESC LIMIT 20', (principal,project))]

    def execute(self, identifier, principal, project, root, *, confirmed=False):
        # Serialize app delivery actions through validation and the effect.
        # External editors/Git still invalidate the snapshot on the next check.
        with self.lock:
            return self._execute(identifier, principal, project, root, confirmed=confirmed)

    def _execute(self, identifier, principal, project, root, *, confirmed=False):
        with self.lock, self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM actions WHERE id=? AND principal=? AND project=?", (identifier, principal, project)).fetchone()
            if row is None:
                raise HTTPException(404, "Delivery action not found")
            if row["status"] != "prepared":
                return {"status": row["status"], "result": json.loads(row["result"]) if row["result"] else None}
            if row["created"] + 300 <= time():
                raise HTTPException(409, "Delivery confirmation expired; prepare again")
            if (row["operation"] in PUBLISH or row["operation"] == "worktree-remove") and not confirmed:
                raise HTTPException(409, "This exact publication/removal requires confirmation")
            target = self.target(project, root, json.loads(row["arguments"]).get("worktree_id"))
            if self.head(target) != row["head"]:
                raise HTTPException(409, "Repository HEAD changed; review the operation again")
            if not row['state_digest'] or self.state_digest(target, row['operation']) != row['state_digest']:
                raise HTTPException(409, 'Repository content or delivery target changed; review the operation again')
            db.execute("UPDATE actions SET status='executing' WHERE id=?", (identifier,))
        args = json.loads(row["arguments"])
        try:
            result = self.effect(project, root, row["operation"], args)
            status = "completed"
        except Exception as exc:
            result = {"error": exc.detail if isinstance(exc, HTTPException) else "Delivery operation interrupted; inspect repository state"}
            # A publication can succeed before the CLI loses its response.
            # Preserve uncertainty and its consumed ID rather than suggesting
            # the remote operation is safe to repeat.
            status = 'unknown' if row['operation'] in PUBLISH or not isinstance(exc, HTTPException) else 'failed'
        with self.lock, self.db() as db:
            db.execute("UPDATE actions SET status=?, result=? WHERE id=?", (status, json.dumps(result), identifier))
        log_event("delivery_action", action_id=identifier, project_id=project, operation=row["operation"], outcome=status)
        return {"status": status, "result": result}

    def worktrees(self, project):
        with self.db() as db:
            return [dict(row) for row in db.execute("SELECT * FROM worktrees WHERE project=?", (project,))]

    def target(self, project, root, identifier=None):
        if identifier is None:
            return root
        item = next((w for w in self.worktrees(project) if w["id"] == identifier), None)
        if not item or Path(item["root"]).resolve() != Path(root).resolve():
            raise HTTPException(404, "Worktree not found for this project")
        return item["path"]

    @staticmethod
    def paths(root, paths):
        base = Path(root).resolve()
        if not isinstance(paths, list) or not paths:
            raise HTTPException(400, "Select project files")
        for value in paths:
            if not isinstance(value, str) or not (base / value).resolve().is_relative_to(base):
                raise HTTPException(403, "Git paths must stay inside the project")
        return paths

    def effect(self, project, root, operation, args):
        target = self.target(project, root, args.get("worktree_id"))
        if operation in {"stage", "unstage"}:
            return git_ops.stage(target, self.paths(target, args.get("paths")), operation == "stage")
        if operation == "hunk":
            return git_ops.stage_hunk(target, args["patch"], args.get("stage", True))
        if operation == "commit":
            if not isinstance(args.get("message"), str) or not args["message"].strip():
                raise HTTPException(400, "Commit message is required")
            return git_ops.commit(target, args["message"])
        if operation == "branch":
            name = args["name"]
            git_ops._require_ok(git_ops._git(target, "check-ref-format", "--branch", name))
            return git_ops.switch_branch(target, name, bool(args.get("create")))
        if operation == 'push':
            return git_ops.push(target,args.get('remote'),args.get('branch'))
        if operation in {"fetch", "pull"}:
            return getattr(git_ops, operation)(target)
        if operation == "worktree-create":
            branch, base = args["branch"], args.get("base", "HEAD")
            if not isinstance(base, str) or base.startswith("-"):
                raise HTTPException(400, "Invalid base ref")
            git_ops._require_ok(git_ops._git(root, "check-ref-format", "--branch", branch))
            identifier = uuid.uuid4().hex
            path = self.directory / "worktrees" / identifier
            path.parent.mkdir(parents=True, exist_ok=True)
            git_ops._require_ok(git_ops._git(root, "worktree", "add", "-b", branch, str(path), base))
            with self.lock, self.db() as db:
                db.execute("INSERT INTO worktrees VALUES (?, ?, ?, ?, ?)", (identifier, project, root, str(path), branch))
            return {"id": identifier, "path": str(path), "branch": branch}
        if operation == "worktree-remove":
            if not args.get("worktree_id") or git_ops.status(target)["files"]:
                raise HTTPException(409, "Dirty worktree retained; commit or preserve changes before removal")
            git_ops._require_ok(git_ops._git(root, "worktree", "remove", target))
            with self.lock, self.db() as db:
                db.execute("DELETE FROM worktrees WHERE id=? AND project=?", (args["worktree_id"], project))
            return {"ok": True, "branch_retained": True}
        if operation in PUBLISH:
            return self.github.mutate(target, operation, args)
        raise HTTPException(400, "Unsupported delivery effect")
