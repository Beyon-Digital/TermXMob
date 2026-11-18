"""The reviewed `discard` delivery operation: what it restores and removes, what it refuses, and who may run it."""
from __future__ import annotations

import asyncio
import os
import subprocess
from pathlib import Path

import pytest
from fastapi import HTTPException

from cli_fake import posix_only
from termx.authorization import ROLES
from termx.development import discard
from termx.development.delivery import DeliveryService


def git(root, *args):
    result = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True)
    return result.stdout.strip()


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-b", "main")
    git(root, "config", "user.name", "Test")
    git(root, "config", "user.email", "test@example.invalid")
    (root / "tracked.txt").write_text("original\n")
    (root / "other.txt").write_text("other\n")
    (root / "src").mkdir()
    (root / "src" / "mod.py").write_text("x = 1\n")
    (root / ".gitignore").write_text("ignored.log\n")
    git(root, "add", "-A")
    git(root, "commit", "-m", "Initial")
    return root


@pytest.fixture
def service(tmp_path):
    return DeliveryService(tmp_path / "state")


def prepare(service, repo, paths, **extra):
    return service.prepare("owner", "project", str(repo), "discard", {"paths": paths, **extra})


def run(service, repo, paths, **extra):
    action = prepare(service, repo, paths, **extra)
    return service.execute(action["id"], "owner", "project", str(repo), confirmed=True)


def porcelain(root):
    return git(root, "status", "--porcelain=v1", "--untracked-files=all")


def test_a_tracked_edit_is_restored_from_the_index(repo, service):
    (repo / "tracked.txt").write_text("edited\n")
    outcome = run(service, repo, ["tracked.txt"])
    assert outcome["status"] == "completed"
    assert outcome["result"]["counts"] == {"modified": 1, "deleted": 0, "staged": 0, "untracked": 0}
    assert (repo / "tracked.txt").read_text() == "original\n"
    assert porcelain(repo) == ""


def test_a_deleted_file_comes_back(repo, service):
    (repo / "tracked.txt").unlink()
    action = prepare(service, repo, ["tracked.txt"])
    assert action["review"]["files"] == [{"path": "tracked.txt", "kind": "deleted", "staged": False, "worktree": True, "keeps_staged": False}]
    assert service.execute(action["id"], "owner", "project", str(repo), confirmed=True)["status"] == "completed"
    assert (repo / "tracked.txt").read_text() == "original\n"


def test_staged_changes_are_restored_from_head_in_the_index_and_the_tree(repo, service):
    (repo / "tracked.txt").write_text("staged\n")
    git(repo, "add", "tracked.txt")
    (repo / "tracked.txt").write_text("staged and edited\n")
    new = repo / "new.txt"
    new.write_text("new\n")
    git(repo, "add", "new.txt")
    outcome = run(service, repo, ["tracked.txt", "new.txt"], include_staged=True)
    assert outcome["status"] == "completed"
    assert outcome["result"]["counts"]["staged"] == 2
    assert (repo / "tracked.txt").read_text() == "original\n"
    assert not new.exists()
    assert porcelain(repo) == ""


def test_by_default_only_the_unstaged_side_is_discarded_and_staged_work_stays(repo, service):
    (repo / "tracked.txt").write_text("staged\n")
    git(repo, "add", "tracked.txt")
    (repo / "tracked.txt").write_text("staged and edited\n")
    (repo / "other.txt").write_text("only staged\n")
    git(repo, "add", "other.txt")
    action = prepare(service, repo, ["tracked.txt", "other.txt"])
    assert [item["path"] for item in action["review"]["files"]] == ["tracked.txt"]
    assert action["review"]["files"][0]["keeps_staged"] is True
    assert action["review"]["already_clean"] == ["other.txt"]
    assert service.execute(action["id"], "owner", "project", str(repo), confirmed=True)["status"] == "completed"
    assert (repo / "tracked.txt").read_text() == "staged\n"
    assert (repo / "other.txt").read_text() == "only staged\n"
    assert git(repo, "diff", "--cached", "--name-only").splitlines() == ["other.txt", "tracked.txt"]


def test_include_staged_must_be_a_boolean(repo, service):
    (repo / "tracked.txt").write_text("edited\n")
    with pytest.raises(HTTPException) as refused:
        prepare(service, repo, ["tracked.txt"], include_staged="yes")
    assert refused.value.status_code == 400


def test_a_staged_rename_is_undone_with_its_original(repo, service):
    git(repo, "mv", "other.txt", "renamed.txt")
    action = prepare(service, repo, ["renamed.txt"], include_staged=True)
    assert action["review"]["files"][0]["origin"] == "other.txt"
    assert service.execute(action["id"], "owner", "project", str(repo), confirmed=True)["status"] == "completed"
    assert (repo / "other.txt").read_text() == "other\n"
    assert not (repo / "renamed.txt").exists()


def test_untracked_files_are_removed_but_ignored_ones_and_others_stay(repo, service):
    (repo / "loose.txt").write_text("loose\n")
    (repo / "other-loose.txt").write_text("keep\n")
    (repo / "ignored.log").write_text("ignored\n")
    outcome = run(service, repo, ["loose.txt"])
    assert outcome["status"] == "completed"
    assert not (repo / "loose.txt").exists()
    assert (repo / "other-loose.txt").exists()
    assert (repo / "ignored.log").exists()


def test_an_untracked_folder_chosen_explicitly_is_removed_whole(repo, service):
    folder = repo / "scratch"
    (folder / "deep").mkdir(parents=True)
    (folder / "a.txt").write_text("a")
    (folder / "deep" / "b.txt").write_text("b")
    action = prepare(service, repo, ["scratch"])
    assert action["review"]["folders"] == ["scratch"]
    assert [item["path"] for item in action["review"]["files"]] == ["scratch/a.txt", "scratch/deep/b.txt"]
    assert service.execute(action["id"], "owner", "project", str(repo), confirmed=True)["status"] == "completed"
    assert not folder.exists()


def test_a_folder_with_tracked_changes_is_refused_and_nothing_is_touched(repo, service):
    (repo / "src" / "mod.py").write_text("x = 2\n")
    (repo / "src" / "new.py").write_text("new\n")
    with pytest.raises(HTTPException) as refused:
        prepare(service, repo, ["src"])
    assert refused.value.status_code == 400
    assert (repo / "src" / "mod.py").read_text() == "x = 2\n"
    assert (repo / "src" / "new.py").exists()


def test_a_mixed_selection_is_restored_and_removed_in_one_action(repo, service):
    (repo / "tracked.txt").write_text("edited\n")
    (repo / "other.txt").write_text("staged\n")
    git(repo, "add", "other.txt")
    (repo / "src" / "mod.py").unlink()
    (repo / "loose.txt").write_text("loose\n")
    paths = ["tracked.txt", "other.txt", "src/mod.py", "loose.txt", "never-existed.txt"]
    action = prepare(service, repo, paths, include_staged=True)
    assert action["review"]["counts"] == {"modified": 1, "deleted": 1, "staged": 1, "untracked": 1}
    assert action["review"]["already_clean"] == ["never-existed.txt"]
    outcome = service.execute(action["id"], "owner", "project", str(repo), confirmed=True)
    assert outcome["status"] == "completed"
    assert outcome["result"]["already_clean"] == ["never-existed.txt"]
    assert porcelain(repo) == ""
    assert (repo / "src" / "mod.py").read_text() == "x = 1\n"


def test_a_path_that_is_already_clean_is_reported_not_an_error(repo, service):
    outcome = run(service, repo, ["tracked.txt"])
    assert outcome["status"] == "completed"
    assert outcome["result"]["discarded"] == []
    assert outcome["result"]["already_clean"] == ["tracked.txt"]


def test_dry_run_changes_nothing_and_apply_needs_confirmation(repo, service):
    (repo / "tracked.txt").write_text("edited\n")
    (repo / "loose.txt").write_text("loose\n")
    action = prepare(service, repo, ["tracked.txt", "loose.txt"])
    assert action["requires_confirmation"] is True
    assert {item["kind"] for item in action["review"]["files"]} == {"modified", "untracked"}
    assert (repo / "tracked.txt").read_text() == "edited\n"
    assert (repo / "loose.txt").exists()
    with pytest.raises(HTTPException, match="requires confirmation"):
        service.execute(action["id"], "owner", "project", str(repo))
    assert (repo / "tracked.txt").read_text() == "edited\n"
    assert service.record(action["id"], "owner", "project")["status"] == "prepared"
    outcome = service.execute(action["id"], "owner", "project", str(repo), confirmed=True)
    assert outcome["status"] == "completed"
    assert (repo / "tracked.txt").read_text() == "original\n"
    assert not (repo / "loose.txt").exists()


def test_executing_again_returns_the_receipt_and_never_replays(repo, service):
    (repo / "tracked.txt").write_text("edited\n")
    action = prepare(service, repo, ["tracked.txt"])
    first = service.execute(action["id"], "owner", "project", str(repo), confirmed=True)
    (repo / "tracked.txt").write_text("new work after the discard\n")
    again = DeliveryService(service.directory).execute(action["id"], "owner", "project", str(repo), confirmed=True)
    assert again == first
    assert (repo / "tracked.txt").read_text() == "new work after the discard\n"


def test_work_that_changed_after_review_is_not_discarded(repo, service):
    (repo / "tracked.txt").write_text("edited\n")
    action = prepare(service, repo, ["tracked.txt"])
    (repo / "tracked.txt").write_text("edited again, after the review\n")
    with pytest.raises(HTTPException, match="changed"):
        service.execute(action["id"], "owner", "project", str(repo), confirmed=True)
    assert (repo / "tracked.txt").read_text() == "edited again, after the review\n"


def test_another_principal_cannot_execute_the_action(repo, service):
    (repo / "tracked.txt").write_text("edited\n")
    action = prepare(service, repo, ["tracked.txt"])
    with pytest.raises(HTTPException) as denied:
        service.execute(action["id"], "mallory", "project", str(repo), confirmed=True)
    assert denied.value.status_code == 404
    assert (repo / "tracked.txt").read_text() == "edited\n"


@pytest.mark.parametrize("path", ["../outside.txt", "src/../../outside.txt", "/etc/passwd", "..", ".", ".git/config", ".git", "C:\\x"])
def test_paths_outside_the_checkout_are_refused(repo, service, path, tmp_path):
    (tmp_path / "outside.txt").write_text("not yours\n")
    with pytest.raises(HTTPException) as refused:
        prepare(service, repo, [path])
    assert refused.value.status_code == 403
    assert (tmp_path / "outside.txt").read_text() == "not yours\n"


@posix_only
def test_a_symlink_that_leaves_the_checkout_is_refused(repo, service, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("secret\n")
    (repo / "escape").symlink_to(outside)
    (repo / "link.txt").symlink_to(outside / "secret.txt")
    for path in ("escape/secret.txt", "escape", "link.txt"):
        with pytest.raises(HTTPException) as refused:
            prepare(service, repo, [path])
        assert refused.value.status_code == 403
    assert (outside / "secret.txt").read_text() == "secret\n"


@posix_only
def test_a_symlink_that_stays_inside_is_removed_as_a_link_not_followed(repo, service):
    (repo / "inside-link").symlink_to(repo / "tracked.txt")
    outcome = run(service, repo, ["inside-link"])
    assert outcome["status"] == "completed"
    assert not (repo / "inside-link").exists() and not os.path.lexists(repo / "inside-link")
    assert (repo / "tracked.txt").read_text() == "original\n"


def test_pathspec_magic_and_globs_are_literal(repo, service):
    (repo / "tracked.txt").write_text("edited\n")
    (repo / "other.txt").write_text("edited\n")
    for path in ("*.txt", ":(glob)**", ":!tracked.txt", "tr*"):
        action = prepare(service, repo, [path])
        assert action["review"]["files"] == []
        assert action["review"]["already_clean"] == [path]
    assert (repo / "tracked.txt").read_text() == "edited\n"
    assert (repo / "other.txt").read_text() == "edited\n"


def test_merge_conflicts_are_never_discarded(repo, service):
    git(repo, "checkout", "-b", "side")
    (repo / "tracked.txt").write_text("side\n")
    git(repo, "commit", "-am", "side")
    git(repo, "checkout", "main")
    (repo / "tracked.txt").write_text("main\n")
    git(repo, "commit", "-am", "main")
    subprocess.run(["git", "-C", str(repo), "merge", "side"], capture_output=True, text=True)
    with pytest.raises(HTTPException) as refused:
        prepare(service, repo, ["tracked.txt"])
    assert refused.value.status_code == 409
    assert "<<<<<<<" in (repo / "tracked.txt").read_text()


def test_the_number_of_paths_is_capped(repo, service):
    too_many = [f"f{index}.txt" for index in range(discard.MAX_PATHS + 1)]
    with pytest.raises(HTTPException) as refused:
        prepare(service, repo, too_many)
    assert refused.value.status_code == 413
    at_the_cap = [f"f{index}.txt" for index in range(discard.MAX_PATHS)]
    assert prepare(service, repo, at_the_cap)["review"]["files"] == []


@pytest.mark.parametrize("paths", [None, [], "tracked.txt", [3], [""], ["a\0b"], ["x" * 5000]])
def test_malformed_paths_are_refused(repo, service, paths):
    with pytest.raises(HTTPException) as refused:
        prepare(service, repo, paths)
    assert refused.value.status_code in (400, 413)


def test_a_linked_worktree_is_discarded_in_place(repo, service):
    made = service.execute(service.prepare("owner", "project", str(repo), "worktree-create", {"branch": "termx/tree"})["id"],
                           "owner", "project", str(repo))["result"]
    tree = Path(made["path"])
    (tree / "tracked.txt").write_text("edited in the worktree\n")
    (repo / "tracked.txt").write_text("edited in the main checkout\n")
    action = service.prepare("owner", "project", str(repo), "discard", {"paths": ["tracked.txt"], "worktree_id": made["id"]})
    assert service.execute(action["id"], "owner", "project", str(repo), confirmed=True)["status"] == "completed"
    assert (tree / "tracked.txt").read_text() == "original\n"
    assert (repo / "tracked.txt").read_text() == "edited in the main checkout\n"


def test_the_audit_log_records_the_discard(repo, service, monkeypatch):
    events = []
    monkeypatch.setattr("termx.development.delivery.log_event", lambda name, **fields: events.append((name, fields)))
    (repo / "tracked.txt").write_text("edited\n")
    run(service, repo, ["tracked.txt"])
    names = [name for name, _ in events]
    assert "delivery_action" in names and "delivery_discard" in names
    fields = dict(events)["delivery_discard"]
    assert fields["count"] == 1 and fields["paths"] == ["tracked.txt"] and fields["outcome"] == "completed"


def test_the_prepared_action_is_listed_as_confirmable_in_the_records(repo, service):
    (repo / "tracked.txt").write_text("edited\n")
    action = prepare(service, repo, ["tracked.txt"])
    rows = service.records("owner", "project")
    assert rows[0]["id"] == action["id"] and rows[0]["operation"] == "discard" and rows[0]["status"] == "prepared"


def test_it_needs_git_write_over_rest(tmp_path):
    from cli_fake import git_repo
    from test_authorization import PASSWORD, client_fixture

    identity, state, client, owner = client_fixture(tmp_path)
    repo = git_repo(tmp_path / "rest-repo")
    project = state.projects.register(str(repo))["id"]
    (repo / "README.md").write_text("edited\n")
    principal = identity.create_local_user("bob", PASSWORD, list(ROLES["operator"]))
    state.authorization.set_role(principal.id, "operator", trusted_execution=True)
    token = asyncio.run(identity.login("local-password", {"username": "bob", "password": PASSWORD}, peer="local")).access_token
    bob = {"Authorization": f"Bearer {token}"}
    base = f"/api/development/projects/{project}/delivery"
    body = {"operation": "discard", "arguments": {"paths": ["README.md"]}}
    with client:
        assert client.post(base + "/prepare", headers=bob, json=body).status_code == 403
        state.authorization.grant_project(principal.id, project, ["git-read"])
        assert client.post(base + "/prepare", headers=bob, json=body).status_code == 403
        state.authorization.grant_project(principal.id, project, ["git-read", "git-write"])
        prepared = client.post(base + "/prepare", headers=bob, json=body)
        assert prepared.status_code == 200, prepared.text
        action = prepared.json()
        assert action["requires_confirmation"] is True
        assert action["review"]["files"][0]["path"] == "README.md"
        run_url = f"{base}/actions/{action['id']}/execute"
        assert client.post(run_url, headers=bob, json={"confirmed": False}).status_code == 409
        assert (repo / "README.md").read_text() == "edited\n"
        done = client.post(run_url, headers=bob, json={"confirmed": True})
        assert done.status_code == 200 and done.json()["status"] == "completed"
        assert (repo / "README.md").read_text() == "hello\n"
        # Without git-write again, the execute step is refused as well.
        state.authorization.grant_project(principal.id, project, ["git-read"])
        (repo / "README.md").write_text("edited again\n")
        owner_action = client.post(base + "/prepare", headers=owner, json=body).json()
        denied = client.post(f"{base}/actions/{owner_action['id']}/execute", headers=bob, json={"confirmed": True})
        assert denied.status_code == 403
        assert (repo / "README.md").read_text() == "edited again\n"
