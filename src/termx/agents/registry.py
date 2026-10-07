"""File-authoritative custom-agent registry.

``*.agent.md`` files under ``~/.agents/agents/`` are the source of truth;
the ``custom_agents`` table is a synced projection so existing API/UI reads
keep working. Writes are atomic (tempfile + os.replace, mode 600), keep a
``.bak`` of the previous file, and enforce an expected-revision
precondition so concurrent edits conflict instead of clobbering.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

from ..discovery.safety import slugify
from .files import AgentFile, AgentFileError, parse_agent_file, serialize_agent

AUTHORITY_SETTING = "engine_extensions.agents_authority"


class RevisionConflict(RuntimeError):
    def __init__(self, slug: str, expected: str, actual: str):
        super().__init__(
            f"agent '{slug}' changed on disk (expected {expected[:8]}, "
            f"found {actual[:8]})"
        )
        self.slug = slug
        self.expected = expected
        self.actual = actual


class AgentRegistry:
    def __init__(self, store: Any, agents_dir: str | None = None):
        self._store = store
        self._dir = Path(
            agents_dir or os.path.expanduser("~/.agents/agents")
        ).resolve()
        self._write_token: str | None = None

    # ------------------------------------------------------------ paths

    @property
    def agents_dir(self) -> str:
        return str(self._dir)

    def _path_for(self, slug: str) -> Path:
        return self._dir / f"{slug}.agent.md"

    def _assert_inside(self, path: Path) -> None:
        root = self._dir
        parent = path.resolve().parent
        if parent != root:
            raise AgentFileError(f"path escapes agents dir: {path}")

    def _read_file(self, path: Path) -> str:
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd = os.open(str(path), flags)
        try:
            with os.fdopen(fd, "rb", closefd=False) as fh:
                data = fh.read(512 * 1024 + 1)
        finally:
            os.close(fd)
        if len(data) > 512 * 1024:
            raise AgentFileError(f"agent file too large: {path}")
        return data.decode("utf-8", errors="replace")

    def _atomic_write(self, path: Path, content: str) -> None:
        self._dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        from termx.private_files import protect_private_path
        if os.name == 'nt':
            protect_private_path(self._dir, directory=True)
        fd, tmp = tempfile.mkstemp(dir=str(self._dir), prefix=".write-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                protect_private_path(Path(tmp))
                fh.write(content)
            if path.exists():
                bak = path.with_suffix(path.suffix + ".bak")
                try:
                    os.replace(str(path), str(bak))
                except OSError:
                    pass
            os.replace(tmp, str(path))
            protect_private_path(path)
        finally:
            try:
                os.unlink(tmp)
            except OSError:
                pass

    # ------------------------------------------------------------ reads

    def load(self, slug: str) -> AgentFile | None:
        path = self._path_for(slug)
        if not path.is_file() or path.is_symlink():
            return None
        return parse_agent_file(self._read_file(path), slug_hint=slug)

    def list(self) -> list[AgentFile]:
        if not self._dir.is_dir():
            return []
        out: list[AgentFile] = []
        for child in sorted(self._dir.glob("*.agent.md")):
            if child.is_symlink() or not child.is_file():
                continue
            try:
                slug = child.name[: -len(".agent.md")]
                out.append(parse_agent_file(self._read_file(child), slug_hint=slug))
            except AgentFileError:
                continue
        return out

    def read_raw(self, slug: str) -> str | None:
        path = self._path_for(slug)
        if not path.is_file() or path.is_symlink():
            return None
        return self._read_file(path)

    # ------------------------------------------------------------ writes

    def save(
        self,
        agent: AgentFile,
        *,
        expected_revision: str | None = None,
        source: str = "user",
        migrated_at: float | None = None,
        agent_id: str | None = None,
        before_write: Callable[[AgentFile], None] | None = None,
    ) -> AgentFile:
        if not agent.name.strip():
            raise AgentFileError("custom agent name is required")
        agent.slug = slugify(agent.slug or agent.name)
        path = self._path_for(agent.slug)
        self._assert_inside(path)
        if expected_revision is not None and path.is_file():
            actual = hashlib.sha256(
                self._read_file(path).encode("utf-8", "replace")
            ).hexdigest()
            if actual != expected_revision:
                raise RevisionConflict(agent.slug, expected_revision, actual)
        content = serialize_agent(agent)
        # Server entry points can bind canonical ownership before either the
        # authoritative file or its database projection becomes discoverable.
        if before_write is not None:
            before_write(agent)
        token = f"write-{time.time_ns()}"
        self._write_token = token
        try:
            self._atomic_write(path, content)
        finally:
            self._write_token = None
        parsed = parse_agent_file(content, slug_hint=agent.slug)
        self._project(
            parsed, path, source=source, migrated_at=migrated_at,
            agent_id=agent_id,
        )
        return parsed

    def delete(self, slug: str) -> bool:
        path = self._path_for(slug)
        existed = path.is_file() and not path.is_symlink()
        if existed:
            try:
                os.replace(str(path), str(path) + ".bak")
            except OSError:
                return False
        row = self._store.custom_agent_by_file(str(path))
        if row:
            self._store.delete_custom_agent(row["id"])
        return existed or row is not None

    def duplicate(self, slug: str, *, name: str | None = None,
                  before_write: Callable[[AgentFile], None] | None = None) -> AgentFile | None:
        src = self.load(slug)
        if src is None:
            return None
        base = slugify(f"{slug}-copy")
        new_slug, i = base, 2
        while self._path_for(new_slug).exists():
            new_slug = f"{base}-{i}"
            i += 1
        dup = AgentFile(**{**src.__dict__})
        dup.slug = new_slug
        dup.name = name or f"{src.name} (copy)"
        dup.revision = ""
        return self.save(dup, source="user", before_write=before_write)

    def import_markdown(
        self, text: str, *, source: str = "import",
        before_write: Callable[[AgentFile], None] | None = None,
    ) -> AgentFile:
        parsed = parse_agent_file(text)
        parsed.slug = slugify(parsed.slug or parsed.name)
        if self._path_for(parsed.slug).exists():
            base, i = parsed.slug, 2
            while self._path_for(f"{base}-{i}").exists():
                i += 1
            parsed.slug = f"{base}-{i}"
        return self.save(parsed, source=source, before_write=before_write)

    # ------------------------------------------------------------ sync

    def _project(
        self,
        agent: AgentFile,
        path: Path,
        *,
        source: str,
        migrated_at: float | None = None,
        agent_id: str | None = None,
    ) -> None:
        self._store.upsert_agent_file(
            agent_id=agent_id or agent.qid_id,
            name=agent.name,
            description=agent.description,
            instructions=agent.instructions,
            model=agent.model,
            tools=agent.tools,
            limits=agent.limits,
            approval_mode=agent.approval_mode,
            sandbox_profile=agent.sandbox_profile,
            engine=agent.engine,
            enabled=agent.enabled,
            file_path=str(path),
            file_revision=agent.revision,
            file_json=json.dumps(agent.as_dict()),
            source=source,
            sync_state="synced",
            migrated_at=migrated_at,
        )

    def sync(self) -> dict[str, Any]:
        """Reconcile files <-> DB projection.

        - Every ``*.agent.md`` on disk upserts its projection row.
        - DB rows with no file (pre-migration ``source=device``) are exported
          to files once (id-preserving), then marked ``migrated_at``.
        - Projection rows whose file vanished are marked ``sync_state=missing``.
        """
        report = {"imported": [], "exported": [], "missing": [], "errors": []}
        on_disk: dict[str, AgentFile] = {}
        for agent in self.list():
            on_disk[agent.slug] = agent
            path = self._path_for(agent.slug)
            existing = self._store.custom_agent_by_file(str(path))
            src = existing["source"] if existing else "user"
            try:
                self._project(agent, path, source=src)
                report["imported"].append(agent.slug)
            except Exception as exc:  # noqa: BLE001
                report["errors"].append(f"{agent.slug}: {exc}")

        for row in self._store.custom_agents_needing_files():
            slug = slugify(row["name"])
            path = self._path_for(slug)
            if path.exists():
                base, i = slug, 2
                while self._path_for(f"{base}-{i}").exists():
                    i += 1
                slug = f"{base}-{i}"
            agent = AgentFile(
                slug=slug,
                name=row["name"],
                description=row["description"],
                instructions=row["instructions"],
                model=row["model"],
                engine="inherit",
                tools=row["tools"] or [],
                tools_omitted=not row["tools"],
                approval_mode=row["approval_mode"],
                sandbox_profile=row["sandbox_profile"],
                limits=row["limits"] or {},
            )
            try:
                self.save(
                    agent,
                    source=row["source"] or "device",
                    migrated_at=time.time(),
                    agent_id=row["id"],
                )
                report["exported"].append(slug)
            except Exception as exc:  # noqa: BLE001
                report["errors"].append(f"migrate {row['id']}: {exc}")

        for row in self._store.list_custom_agents():
            fp = row.get("file_path")
            if fp and not Path(fp).is_file():
                self._store.mark_agent_sync(row["id"], "missing")
                report["missing"].append(row["id"])
        return report
