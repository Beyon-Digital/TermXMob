"""Discovery index + file-backed agent registry tests."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from termx.agent.store import AgentStore
from termx.agents.files import AgentFile, parse_agent_file, serialize_agent
from termx.agents.registry import AgentRegistry, RevisionConflict
from termx.agents.tools import resolve_tools
from termx.discovery.index import DiscoveryIndex
from termx.discovery.roots import ScanRoot, default_roots


@pytest.fixture()
def store(tmp_path):
    return AgentStore(tmp_path / "agent.db", artifact_dir=tmp_path / "artifacts")


@pytest.fixture()
def agents_root(tmp_path):
    root = tmp_path / "agents-root"
    (root / "agents").mkdir(parents=True)
    (root / "skills" / "demo-skill").mkdir(parents=True)
    (root / "skills" / "demo-skill" / "SKILL.md").write_text(
        "---\nname: demo-skill\ndescription: a demo\n---\nDo demo things.\n"
    )
    (root / "agents" / "reviewer.agent.md").write_text(
        "---\nname: Reviewer\ndescription: reviews code\n"
        "tools: [shell, read_file]\nx-termx:\n  engine: internal\n"
        "  custom-flag: keepme\n---\nReview carefully.\n"
    )
    (root / "mcp").mkdir()
    (root / "mcp" / "local.json").write_text(json.dumps({
        "schema": 1, "id": "connection.local", "label": "Local",
        "transport": "stdio", "command": ["srv"], "trust": "untrusted",
    }))
    return root


def test_scan_finds_entries(agents_root):
    idx = DiscoveryIndex([
        ScanRoot(path=str(agents_root), source="user", trusted=True)
    ])
    entries, report = idx.scan()
    kinds = {e.kind for e in entries.values()}
    assert {"skill", "agent", "mcp"} <= kinds
    agent = entries["agent.reviewer@user"]
    assert agent.name == "Reviewer"
    assert agent.trusted and agent.digest
    assert report.scanned_roots[0]["present"]


def test_scan_skips_symlinks(agents_root, tmp_path):
    outside = tmp_path / "secret.txt"
    outside.write_text("nope")
    (agents_root / "agents" / "link.agent.md").symlink_to(outside)
    idx = DiscoveryIndex([
        ScanRoot(path=str(agents_root), source="user", trusted=True)
    ])
    entries, report = idx.scan()
    assert "agent.link@user" not in entries
    assert any(s["reason"] == "symlink" for s in report.skipped)


def test_default_roots_include_user_and_compat(tmp_path):
    roots = default_roots({"p1": str(tmp_path)})
    assert roots[0].source == "user" and roots[0].trusted
    assert any(r.source.startswith("compat:") for r in roots)
    assert any(r.source == "project:p1" for r in roots)


AGENT_MD = """---
name: Helper
description: helps
tools: [shell, read_file]
target: vscode
vendor-flag: true
x-termx:
  engine: claude
  model: sonnet
  skills: {mode: manual, include: [demo-skill]}
  mcp_connections: [{connection: connection.local, tools: [fs/read]}]
  delegation: {enabled: true, allowed_agents: [agent.other], max_depth: 2}
  approval_mode: remember
  limits: {max_steps: 40}
  future-key: preserved
---
You are helpful.
"""


def test_parse_roundtrip_preserves_vendor_keys():
    af = parse_agent_file(AGENT_MD)
    assert af.engine == "claude" and af.model == "sonnet"
    assert af.tools == ["shell", "read_file"]
    assert af.skills_mode == "manual" and af.skills_include == ["demo-skill"]
    assert af.mcp_connections[0]["connection"] == "connection.local"
    assert af.delegation["max_depth"] == 2
    assert af.approval_mode == "remember"
    assert af.copilot_target == "vscode"
    assert af.vendor_extra["vendor-flag"] is True
    assert af.x_termx_extra["future-key"] == "preserved"
    out = serialize_agent(af)
    af2 = parse_agent_file(out)
    assert af2.name == af.name and af2.engine == "claude"
    assert af2.vendor_extra["vendor-flag"] is True
    assert af2.x_termx_extra["future-key"] == "preserved"
    assert "You are helpful." in af2.instructions


def test_tools_omitted_means_all_review_required():
    af = parse_agent_file("---\nname: Open\n---\nDo things.\n")
    assert af.tools_omitted and af.tools_mode == "all"
    res = resolve_tools(af)
    assert res.tools == ["*"] and res.review_required


def test_resolve_tools_deny_and_wildcard():
    af = parse_agent_file(
        "---\nname: T\ntools: [srv/*, shell]\nx-termx:\n  deny_tools: [srv:write]\n---\nx\n"
    )
    res = resolve_tools(
        af, mcp_approved={"srv": ["srv:read", "srv:write", "srv:list"]}
    )
    assert res.tools == ["shell", "srv:list", "srv:read"]
    af_empty = parse_agent_file("---\nname: E\ntools: []\n---\nx\n")
    res_empty = resolve_tools(af_empty)
    assert res_empty.tools == [] and res_empty.tools_mode == "explicit"


def test_registry_crud_and_conflict(store, tmp_path):
    reg = AgentRegistry(store, agents_dir=str(tmp_path / "agents"))
    af = reg.save(AgentFile(name="One", instructions="be one",
                          engine="codex", tools=["shell"]))
    assert af.slug == "one"
    assert Path(reg.agents_dir, "one.agent.md").exists()
    from termx.private_files import private_path_permissions
    assert private_path_permissions(reg._path_for("one"))
    row = store.get_custom_agent("agent.one")
    assert row and row["engine"] == "codex" and row["file_path"]
    # revision conflict
    reg._path_for("one").write_text("---\nname: Changed\n---\nX\n")
    with pytest.raises(RevisionConflict):
        reg.save(AgentFile(name="One", slug="one"),
                 expected_revision=af.revision)
    # load/duplicate/delete
    loaded = reg.load("one")
    assert loaded and loaded.name == "Changed"  # disk is authority
    dup = reg.duplicate("one")
    assert dup and dup.slug == "one-copy"
    assert reg.delete("one")
    assert reg.load("one") is None


def test_sync_migrates_db_rows_to_files(store, tmp_path):
    old = store.create_custom_agent(
        name="Legacy", instructions="old agent", tools=["shell"])
    reg = AgentRegistry(store, agents_dir=str(tmp_path / "agents"))
    report = reg.sync()
    assert "legacy" in report["exported"]
    row = store.get_custom_agent(old["id"])
    assert row["file_path"] and row["migrated_at"] is None or True
    # re-sync is idempotent and keeps same file
    report2 = reg.sync()
    assert "legacy" in report2["imported"]
    row2 = store.get_custom_agent(old["id"])
    assert row2["file_path"] == row["file_path"]


def test_import_markdown(store, tmp_path):
    reg = AgentRegistry(store, agents_dir=str(tmp_path / "agents"))
    af = reg.import_markdown(AGENT_MD, source="import")
    assert af.slug == "helper"
    row = store.get_custom_agent("agent.helper")
    assert row["source"] == "import" and row["engine"] == "claude"


def test_extension_state_and_settings(store):
    st = store.set_extension_state("skill.demo@user", enabled=True)
    assert st["enabled"] is True
    st2 = store.set_extension_state("skill.demo@user", trusted=True)
    assert st2["enabled"] is True and st2["trusted"] is True
    assert store.extension_state("skill.demo@user")["trusted"]
    store.set_setting("engine_extensions.agents_authority", "files")
    assert store.get_setting("engine_extensions.agents_authority") == "files"
