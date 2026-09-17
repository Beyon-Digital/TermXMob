"""Project-scoped file access for the remote editor.

Paths in this API are relative to a registered root. Revision checks serialize
Termx writers, and atomic replacement preserves the original file's mode.
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
import stat
import tempfile
import threading
import uuid
from pathlib import Path
from time import time
from urllib.parse import urlsplit

from fastapi import HTTPException

from termx.config import config_dir, validate_cwd

EDIT_LIMIT = 2 * 1024 * 1024


class ProjectFiles:
    def __init__(self) -> None:
        directory = config_dir()
        directory.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(directory / "workspace.sqlite3", check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS projects (
                id TEXT PRIMARY KEY, path TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
                updated_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS session_projects (
                session_id TEXT PRIMARY KEY, project_id TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS previews (
                id TEXT PRIMARY KEY, project_id TEXT NOT NULL, name TEXT NOT NULL,
                url TEXT NOT NULL
            );
        """)
        self.db.commit()

    def projects(self) -> list[dict]:
        with self.lock:
            return [dict(row) for row in self.db.execute("SELECT * FROM projects ORDER BY updated_at DESC")]

    def register(self, path: str, name: str = "") -> dict:
        root = validate_cwd(path)
        project_id = hashlib.sha256(os.path.normcase(root).encode()).hexdigest()[:24]
        with self.lock, self.db:
            self.db.execute(
                "INSERT INTO projects VALUES (?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET updated_at=excluded.updated_at",
                (project_id, root, name.strip() or Path(root).name or root, time()),
            )
        return self.project(project_id)

    def project(self, project_id: str) -> dict:
        with self.lock:
            row = self.db.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "Project not found")
        return dict(row)

    def update_project(self, project_id: str, name: str) -> dict:
        self.project(project_id)
        with self.lock, self.db:
            self.db.execute("UPDATE projects SET name=? WHERE id=?", (name.strip(), project_id))
        return self.project(project_id)

    def forget(self, project_id: str) -> None:
        with self.lock, self.db:
            self.db.execute("DELETE FROM previews WHERE project_id=?", (project_id,))
            self.db.execute("DELETE FROM session_projects WHERE project_id=?", (project_id,))
            self.db.execute("DELETE FROM projects WHERE id=?", (project_id,))

    def resolve(self, project_id: str, path: str, *, exists: bool = True) -> Path:
        root = Path(self.project(project_id)["path"]).resolve(strict=True)
        relative = Path(path)
        if relative.is_absolute() or ".." in relative.parts:
            raise HTTPException(403, "Path must stay inside the project")
        target = (root / relative).resolve(strict=False)
        if not target.is_relative_to(root):
            raise HTTPException(403, "Symlink leaves the project")
        if exists and not target.exists():
            raise HTTPException(404, "File no longer exists")
        return target

    @staticmethod
    def revision(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    def listing(self, project_id: str, path: str = "", offset: int = 0, limit: int = 200) -> dict:
        target = self.resolve(project_id, path)
        if not target.is_dir():
            raise HTTPException(400, "Not a directory")
        root = Path(self.project(project_id)["path"])
        entries = []
        for child in target.iterdir():
            try:
                resolved = child.resolve()
                outside = not resolved.is_relative_to(root)
                info = child.stat()
                entries.append({"name": child.name, "path": child.relative_to(root).as_posix(),
                                "dir": child.is_dir(), "size": info.st_size,
                                "mtime": info.st_mtime_ns, "symlink": child.is_symlink(), "outside": outside})
            except OSError:
                continue
        entries.sort(key=lambda item: (not item["dir"], item["name"].casefold()))
        return {"path": path, "entries": entries[offset:offset + limit],
                "next_offset": offset + limit if offset + limit < len(entries) else None,
                "total": len(entries)}

    def read(self, project_id: str, path: str) -> dict:
        target = self.resolve(project_id, path)
        if not target.is_file():
            raise HTTPException(400, "Not a regular file")
        size = target.stat().st_size
        base = {"path": path, "size": size, "editable": False, "content": "", "revision": ""}
        if size > EDIT_LIMIT:
            return {**base, "reason": "This file exceeds the 2 MiB editor limit. Download it to inspect it."}
        data = target.read_bytes()
        revision = self.revision(data)
        try:
            text = data.decode("utf-8-sig")
            if "\0" in text:
                raise UnicodeError()
        except UnicodeError:
            return {**base, "revision": revision, "reason": "Binary or unsupported encoding. Download the original file."}
        return {**base, "content": text, "revision": revision, "editable": os.access(target, os.W_OK),
                "encoding": "utf-8-sig" if data.startswith(b"\xef\xbb\xbf") else "utf-8",
                "line_ending": "\r\n" if "\r\n" in text else "\n"}

    def save(self, project_id: str, path: str, content: str, revision: str) -> dict:
        with self.lock:
            target = self.resolve(project_id, path)
            previous = target.read_bytes()
            if self.revision(previous) != revision:
                raise HTTPException(409, "File changed on the host. Compare it with your draft before saving.")
            encoding = "utf-8-sig" if previous.startswith(b"\xef\xbb\xbf") else "utf-8"
            data = content.encode(encoding)
            if len(data) > EDIT_LIMIT:
                raise HTTPException(413, "Text exceeds the 2 MiB editor limit")
            mode = stat.S_IMODE(target.stat().st_mode)
            fd, temporary = tempfile.mkstemp(dir=target.parent, prefix=".termx-edit-")
            try:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(data)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.chmod(temporary, mode)
                # Recheck after writing the temp file, before the replacement.
                if self.revision(target.read_bytes()) != revision:
                    raise HTTPException(409, "File changed during save. Your draft is preserved.")
                os.replace(temporary, target)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
            return self.read(project_id, path)

    def mutate(self, project_id: str, action: str, path: str, destination: str = "", revision: str = "") -> dict:
        with self.lock:
            target = self.resolve(project_id, path, exists=action not in {"file", "directory"})
            if target == Path(self.project(project_id)["path"]):
                raise HTTPException(400, "Choose a file or subdirectory")
            if action == "file":
                with target.open("xb"):
                    pass
            elif action == "directory":
                target.mkdir()
            elif action == "move":
                new = self.resolve(project_id, destination, exists=False)
                if new.exists():
                    raise HTTPException(409, "Destination already exists")
                target.rename(new)
            elif action == "delete":
                if target.is_dir():
                    target.rmdir()  # Non-empty directories are never recursively deleted.
                else:
                    if not revision or self.revision(target.read_bytes()) != revision:
                        raise HTTPException(409, "Reload this file before deleting it")
                    target.unlink()
            else:
                raise HTTPException(400, "Unknown file action")
            return {"ok": True}

    def search(self, project_id: str, query: str, *, content: bool = True, case_sensitive: bool = False) -> dict:
        root = self.resolve(project_id, "")
        results = []
        needle = query if case_sensitive else query.casefold()
        visited = 0
        deadline = time() + 5
        for directory, dirs, files in os.walk(root, followlinks=False):
            dirs[:] = [d for d in dirs if d not in {".git", "node_modules", ".venv", "dist", "build", "target"}
                       and not Path(directory, d).is_symlink()]
            for name in files:
                visited += 1
                if visited > 20000 or time() > deadline or len(results) >= 500:
                    return {"results": results, "truncated": True}
                candidate = Path(directory, name)
                relative = candidate.relative_to(root).as_posix()
                if not content:
                    if needle in (relative if case_sensitive else relative.casefold()):
                        results.append({"path": relative, "line": 1, "text": relative})
                    continue
                try:
                    document = self.read(project_id, relative)
                except (OSError, HTTPException):
                    continue
                if not document.get("revision") or not document.get("content"):
                    continue
                for number, line in enumerate(document["content"].splitlines(), 1):
                    if needle in (line if case_sensitive else line.casefold()):
                        results.append({"path": relative, "line": number, "text": line[:500],
                                        "revision": document["revision"]})
                        if len(results) >= 500:
                            return {"results": results, "truncated": True}
        return {"results": results, "truncated": False}

    def previews(self, project_id: str) -> list[dict]:
        self.project(project_id)
        with self.lock:
            rows = self.db.execute(
                "SELECT id, name, url FROM previews WHERE project_id=? ORDER BY rowid",
                (project_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def add_preview(self, project_id: str, name: str, url: str) -> dict:
        self.project(project_id)
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise HTTPException(400, "Preview URL must use http or https")
        preview_id = uuid.uuid4().hex[:24]
        record = {"id": preview_id, "name": name.strip() or parsed.netloc, "url": url}
        with self.lock, self.db:
            self.db.execute(
                "INSERT INTO previews (id, project_id, name, url) VALUES (?, ?, ?, ?)",
                (preview_id, project_id, record["name"], url),
            )
        return record

    def delete_preview(self, project_id: str, preview_id: str) -> None:
        self.project(project_id)
        with self.lock, self.db:
            cursor = self.db.execute(
                "DELETE FROM previews WHERE id=? AND project_id=?",
                (preview_id, project_id),
            )
            if cursor.rowcount == 0:
                raise HTTPException(404, "Preview not found")

    def assign_session(self, session_id: str, project_id: str) -> None:
        self.project(project_id)
        with self.lock, self.db:
            self.db.execute("INSERT OR REPLACE INTO session_projects VALUES (?, ?)", (session_id, project_id))

    def session_projects(self) -> dict[str, str]:
        with self.lock:
            return dict(self.db.execute("SELECT session_id, project_id FROM session_projects"))

    def close(self) -> None:
        self.db.close()
