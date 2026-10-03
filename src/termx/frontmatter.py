"""Minimal YAML-frontmatter split used by discovery and agent files.

``split_frontmatter`` returns ``(metadata_dict, body)``; files without a
``---`` fence return ``({}, text)``. YAML is loaded with the safe loader and
must produce a mapping — anything else is a parse error surfaced to the
caller (never executed).
"""

from __future__ import annotations

from typing import Any

import yaml

FENCE = "---"


def split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith(FENCE):
        return {}, text
    lines = text.split("\n")
    end = -1
    for i in range(1, len(lines)):
        if lines[i].strip() == FENCE:
            end = i
            break
    if end < 0:
        return {}, text
    raw = "\n".join(lines[1:end])
    data = yaml.safe_load(raw)
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ValueError("frontmatter must be a YAML mapping")
    body = "\n".join(lines[end + 1 :])
    if body.startswith("\n"):
        body = body[1:]
    return data, body


def dump_frontmatter(meta: dict[str, Any], body: str) -> str:
    head = yaml.safe_dump(meta, sort_keys=False, allow_unicode=True).rstrip()
    return f"{FENCE}\n{head}\n{FENCE}\n{body if body.endswith(chr(10)) or not body else body + chr(10)}"
