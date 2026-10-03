"""Scan-root definitions for extension discovery.

A scan root is a directory whose *direct contents* TermX trusts to declare
extensions. Trust is per-root: the user's ``~/.agents`` is trusted because it
is the user's own directory; a project's ``<root>/.agents`` is trusted when
the project is authorized; compat locations are metadata-only.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

USER_AGENTS_DIR = "~/.agents"


@dataclass(frozen=True)
class ScanRoot:
    """One discovery root.

    ``source`` is ``user`` | ``project:<id>`` | ``compat:<vendor>`` |
    ``bundled``. ``trusted`` roots may contribute executable definitions
    (agents, workflows); untrusted roots contribute metadata only.
    """

    path: str
    source: str
    trusted: bool
    label: str = ""
    compat: bool = False
    # subdir -> extension kind layout mapping (None = scan all)
    kinds: tuple[str, ...] = (
        "skills",
        "agents",
        "workflows",
        "toolsets",
        "mcp",
        "acp",
    )

    def as_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "source": self.source,
            "trusted": self.trusted,
            "label": self.label or self.path,
            "compat": self.compat,
            "kinds": list(self.kinds),
        }


def _expand(p: str) -> str:
    return os.path.abspath(os.path.expanduser(p))


def default_roots(
    project_paths: dict[str, str] | None = None,
    *,
    user_agents_dir: str | None = None,
    include_compat: bool = True,
) -> list[ScanRoot]:
    """Build the default ordered root list.

    ``project_paths`` maps project_id -> absolute path for projects the user
    has authorized in TermX; each contributes ``<path>/.agents``.
    """
    roots: list[ScanRoot] = []
    user_root = _expand(user_agents_dir or USER_AGENTS_DIR)
    roots.append(
        ScanRoot(path=user_root, source="user", trusted=True, label="~/.agents")
    )
    for project_id, path in sorted((project_paths or {}).items()):
        agents_dir = _expand(os.path.join(path, ".agents"))
        roots.append(
            ScanRoot(
                path=agents_dir,
                source=f"project:{project_id}",
                trusted=True,
                label=f"{path}/.agents",
            )
        )
    if include_compat:
        home = Path.home()
        for path, vendor in (
            (home / ".claude" / "skills", "claude"),
            (home / ".config" / "devin" / "skills", "devin"),
            (home / ".agents.d" / "compat", "generic"),
        ):
            roots.append(
                ScanRoot(
                    path=str(path),
                    source=f"compat:{vendor}",
                    trusted=False,
                    compat=True,
                    label=f"~/{path.relative_to(home)}" if path.is_relative_to(home) else str(path),
                    kinds=("skills",),
                )
            )
        for project_id, path in sorted((project_paths or {}).items()):
            root = Path(path)
            for rel, vendor in (
                (".claude/skills", "claude"),
                (".github/agents", "copilot"),
                (".devin/skills", "devin"),
                (".codex", "codex"),
            ):
                roots.append(
                    ScanRoot(
                        path=str(root / rel),
                        source=f"compat:{vendor}",
                        trusted=False,
                        compat=True,
                        label=f"{root.name}/{rel}",
                        kinds=(
                            ("skills",) if rel.endswith("skills")
                            else ("agents",) if rel.endswith("agents")
                            else ()
                        ),
                    )
                )
    return roots
