from pathlib import Path
import subprocess
import pytest
from fastapi import HTTPException
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
    (root / "code.txt").write_text("original\n")
    git(root, "add", "code.txt")
    git(root, "commit", "-m", "Initial")
    return root


def perform(service, root, operation, args, confirmed=False):
    prepared = service.prepare("owner", "project", str(root), operation, args)
    return service.execute(prepared["id"], "owner", "project", str(root), confirmed=confirmed)


def test_parallel_worktree_delivery_and_dirty_cleanup(repo, tmp_path):
    service = DeliveryService(tmp_path / "state")
    one = perform(service, repo, "worktree-create", {"branch": "termx/one"})["result"]
    two = perform(service, repo, "worktree-create", {"branch": "termx/two"})["result"]
    for item, text in [(one, "first"), (two, "second")]:
        (Path(item["path"]) / "code.txt").write_text(text)
        assert perform(service, repo, "stage", {"worktree_id": item["id"], "paths": ["code.txt"]})["status"] == "completed"
        assert perform(service, repo, "commit", {"worktree_id": item["id"], "message": text})["status"] == "completed"
    assert (repo / "code.txt").read_text() == "original\n"
    (Path(one["path"]) / "dirty.txt").write_text("keep")
    refused = perform(service, repo, "worktree-remove", {"worktree_id": one["id"]}, confirmed=True)
    assert refused["status"] == "failed"
    assert (Path(one["path"]) / "dirty.txt").read_text() == "keep"
    assert perform(service, repo, "worktree-remove", {"worktree_id": two["id"]}, confirmed=True)["status"] == "completed"
    assert git(repo, "show-ref", "--verify", "refs/heads/termx/two")


def test_durable_dedup_stale_confirmation_and_scope(repo, tmp_path):
    service = DeliveryService(tmp_path / "state")
    action = service.prepare("alice", "project", str(repo), "branch", {"name": "termx/new", "create": True})
    with pytest.raises(HTTPException) as denied:
        service.execute(action["id"], "bob", "project", str(repo))
    assert denied.value.status_code == 404
    first = service.execute(action["id"], "alice", "project", str(repo))
    assert first["status"] == "completed"
    assert DeliveryService(service.directory).execute(action["id"], "alice", "project", str(repo)) == first
    action = service.prepare("alice", "project", str(repo), "commit", {"message": "stale"})
    (repo / "code.txt").write_text("change")
    git(repo, "add", "code.txt")
    git(repo, "commit", "-m", "External change")
    with pytest.raises(HTTPException, match="HEAD changed"):
        service.execute(action["id"], "alice", "project", str(repo))


def test_publishing_requires_exact_confirmation_and_reconciles(repo, tmp_path):
    class GitHub:
        calls = []
        def mutate(self, root, operation, args):
            self.calls.append((operation, args))
            return {"number": 1, "html_url": "https://github.example/pr/1"}
    provider = GitHub()
    service = DeliveryService(tmp_path / "state", github=provider)
    action = service.prepare("alice", "project", str(repo), "pr-create", {"title": "Change", "body": "Tests", "head": "main", "base": "base"})
    with pytest.raises(HTTPException, match="requires confirmation"):
        service.execute(action["id"], "alice", "project", str(repo))
    first = service.execute(action["id"], "alice", "project", str(repo), confirmed=True)
    assert service.execute(action["id"], "alice", "project", str(repo), confirmed=True) == first
    assert len(provider.calls) == 1


def test_local_bare_remote_push_and_no_replay(repo, tmp_path):
    remote = tmp_path / "remote.git"
    remote.mkdir()
    git(remote, "init", "--bare")
    git(repo, "remote", "add", "origin", str(remote))
    git(repo, "push", "--set-upstream", "origin", "main")
    service = DeliveryService(tmp_path / "state")
    (repo / "code.txt").write_text("published")
    perform(service, repo, "stage", {"paths": ["code.txt"]})
    perform(service, repo, "commit", {"message": "Publish"})
    result = perform(service, repo, "push", {}, confirmed=True)
    assert result["status"] == "completed"
    assert git(remote, "rev-parse", "main") == git(repo, "rev-parse", "HEAD")
    with pytest.raises(HTTPException):
        service.paths(str(repo), ["../private"])


def test_hunk_roundtrip_and_diverged_pull_preserve_local_work(repo, tmp_path):
    remote=tmp_path/'remote.git';remote.mkdir();git(remote,'init','--bare')
    git(repo,'remote','add','origin',str(remote));git(repo,'push','--set-upstream','origin','main')
    other=tmp_path/'other'
    subprocess.run(['git','clone','--branch','main',str(remote),str(other)],capture_output=True,check=True)
    git(other,'config','user.name','Remote fixture');git(other,'config','user.email','remote@example.invalid')
    (other/'code.txt').write_text('remote change\n');git(other,'add','code.txt');git(other,'commit','-m','Remote change');git(other,'push')
    service=DeliveryService(tmp_path/'delivery')
    (repo/'code.txt').write_text('local change\n')
    patch=git(repo,'diff','--','code.txt')+'\n'
    assert perform(service,repo,'hunk',{'patch':patch,'stage':True})['status']=='completed'
    assert git(repo,'diff','--cached','--','code.txt')
    assert perform(service,repo,'hunk',{'patch':patch,'stage':False})['status']=='completed'
    assert git(repo,'diff','--cached','--','code.txt')==''
    perform(service,repo,'stage',{'paths':['code.txt']});perform(service,repo,'commit',{'message':'Local change'})
    (repo/'keep.txt').write_text('unsaved user file')
    before=git(repo,'rev-parse','HEAD')
    failure=perform(service,repo,'pull',{})
    assert failure['status']=='failed'
    assert git(repo,'rev-parse','HEAD')==before
    assert (repo/'code.txt').read_text()=='local change\n'
    assert (repo/'keep.txt').read_text()=='unsaved user file'


def test_confirmation_binds_staged_content_branch_and_working_file(repo, tmp_path):
    service = DeliveryService(tmp_path / 'delivery')
    (repo / 'code.txt').write_text('reviewed\n')
    git(repo, 'add', 'code.txt')
    action = service.prepare('owner', 'project', str(repo), 'commit', {'message': 'Reviewed change'})
    old_head = git(repo, 'rev-parse', 'HEAD')
    (repo / 'code.txt').write_text('changed in another window\n')
    git(repo, 'add', 'code.txt')
    with pytest.raises(HTTPException, match='content or delivery target changed'):
        service.execute(action['id'], 'owner', 'project', str(repo))
    assert git(repo, 'rev-parse', 'HEAD') == old_head
    action = service.prepare('owner', 'project', str(repo), 'stage', {'paths': ['code.txt']})
    (repo / 'code.txt').write_text('another edit with identical porcelain status\n')
    with pytest.raises(HTTPException, match='content or delivery target changed'):
        service.execute(action['id'], 'owner', 'project', str(repo))
    action = service.prepare('owner', 'project', str(repo), 'push', {})
    git(repo, 'switch', '-c', 'other-branch-same-head')
    assert git(repo, 'rev-parse', 'HEAD') == old_head
    with pytest.raises(HTTPException, match='content or delivery target changed'):
        service.execute(action['id'], 'owner', 'project', str(repo), confirmed=True)


def test_lost_publication_response_retains_unknown_and_never_replays(repo,tmp_path):
    class GitHub:
        calls=0
        def mutate(self,*_):
            self.calls+=1
            raise subprocess.TimeoutExpired('gh',60)
    provider=GitHub();service=DeliveryService(tmp_path/'delivery',github=provider)
    action=service.prepare('owner','project',str(repo),'pr-create',{'title':'Reviewed','body':'Exact','head':'main','base':'main'})
    first=service.execute(action['id'],'owner','project',str(repo),confirmed=True)
    assert first['status']=='unknown'
    assert service.execute(action['id'],'owner','project',str(repo),confirmed=True)==first
    assert provider.calls==1


def test_new_reviewed_branch_push_establishes_explicit_upstream(repo,tmp_path):
    remote=tmp_path/'remote.git';remote.mkdir();git(remote,'init','--bare')
    git(repo,'remote','add','origin',str(remote));git(repo,'switch','-c','codex/new-reviewed-branch')
    service=DeliveryService(tmp_path/'delivery')
    args={'remote':'origin','branch':'codex/new-reviewed-branch'}
    result=perform(service,repo,'push',args,confirmed=True)
    assert result['status']=='completed'
    assert git(remote,'rev-parse','refs/heads/codex/new-reviewed-branch')==git(repo,'rev-parse','HEAD')
    assert git(repo,'rev-parse','--abbrev-ref','@{upstream}')=='origin/codex/new-reviewed-branch'
    with pytest.raises(HTTPException,match='not configured'):__import__('termx.git_ops',fromlist=['push']).push(str(repo),'other','codex/new-reviewed-branch')
