"""Local, durable session checkout and title lifecycle; no model calls."""
from __future__ import annotations

import re
import json
from time import time
from pathlib import Path

from fastapi import HTTPException

from termx import git_ops
from termx.agent.policy import redact
from termx.workspace.store import Conflict


PLACEHOLDER_TITLES = {'', 'new conversation', 'new chat', 'new session', 'untitled conversation'}


def title_origin(title):
    return 'pending' if title.strip().casefold() in PLACEHOLDER_TITLES else 'manual'


def generated_title(prompt):
    text = re.sub(r'\s+', ' ', redact(prompt)).strip()
    text = re.sub(r'^[#>*`\s]+', '', text).strip()
    if not text:
        return 'New conversation'
    if len(text) <= 72:
        return text
    prefix = text[:69].rsplit(' ', 1)[0]
    return (prefix if len(prefix) >= 40 else text[:69]).rstrip(' ,;:-') + '…'


def apply_title(service, identifier, prompt):
    """A cross-process ownership fence covers both metadata and title writes."""
    with service.store.lock:
        service.store.db.execute('BEGIN IMMEDIATE')
        try:
            meta = service.store.get('conversation', identifier)
            conversation = service.agents.get_conversation(identifier)
            if not meta or not conversation:
                service.store.db.commit()
                return
            origin = meta.get('title_origin', title_origin(conversation.get('title', '')))
            if origin == 'manual':
                service.store.db.commit()
                return
            if origin == 'auto':
                if meta.get('auto_title') and conversation.get('title') != meta['auto_title']:
                    service.agents.update_conversation(identifier, title=meta['auto_title'])
                service.store.db.commit()
                return
            first = service.agents.workspace_turns_page(identifier, limit=1)
            title = generated_title(first[0]['prompt'] if first else prompt)
            clean = {key:value for key,value in meta.items()
                     if key not in {'id','owner','project_id','revision','created_at','updated_at'}}
            clean.update(title_origin='auto', auto_title=title)
            service.store.db.execute("UPDATE records SET body=?,revision=revision+1,updated=? WHERE kind='conversation' AND id=?",
                (json.dumps(clean), time(), identifier))
            service.agents.update_conversation(identifier, title=title)
            service.store.db.commit()
        except BaseException:
            service.store.db.rollback()
            raise


def metadata(row):
    """Public, inexpensive state; inspect Git only when preparing execution."""
    if row.get('worktree_id'):
        state = 'unavailable' if row.get('worktree_unavailable') else 'isolated'
        notice = row.get('worktree_unavailable') or row.get('checkout_notice')
    elif row.get('runner_id'):
        state, notice = 'remote', 'Execution uses the selected runner workspace.'
    elif row.get('workflow'):
        state, notice = 'browser', 'Browser sessions use their selected local folder.'
    elif row.get('scratch'):
        state, notice = 'scratch', 'This chat uses its own scratch folder; no Git worktree is created.'
    elif row.get('mode') not in {'agent', 'plan'}:
        state, notice = 'read-only', 'Ask sessions use the selected folder without creating a worktree.'
    elif row.get('checkout_state') == 'not-git':
        state, notice = 'not-git', 'This folder is not a Git repository; execution uses the selected folder.'
    elif row.get('checkout_policy') == 'auto' and row.get('project_id'):
        state, notice = 'pending', 'A separate Git worktree will be created from committed HEAD before planning or coding starts. Uncommitted source files stay in the original folder.'
    else:
        state, notice = 'direct', 'Execution uses the selected folder.'
    return {**row, **({'title': row['auto_title']} if row.get('title_origin') == 'auto' and row.get('auto_title') else {}), 'checkout_policy': row.get('checkout_policy', 'selected' if row.get('worktree_id') else 'direct'),
            'checkout_state': state, 'checkout_notice': notice,
            'title_origin': row.get('title_origin', title_origin(row.get('title', '')))}


def _git(folder, *args):
    # Status must not execute the repository's optional filesystem monitor.
    return git_ops._git(str(folder), '--no-optional-locks', '-c', 'core.fsmonitor=false', *args)


def ensure_checkout(service, principal, row, *, delegated=False):
    """Called under the send lock, before hooks, extensions or engine startup.

    A Git failure always stops the send. Worktrees are retained, never applied
    or deleted here. A worktree isolates checkouts, not host processes.
    """
    if row.get('worktree_id'):
        target = service.worktree_target(row['project_id'], row['worktree_id'])
        if row.get('worktree_digest') != target['digest'] or not Path(row['cwd']).resolve().is_relative_to(Path(target['path'])):
            raise PermissionError('Enrolled worktree identity changed; renew the session target')
        canonical = service.agents.get_conversation(row['id'])
        if canonical and canonical.get('cwd') != row['cwd']:
            service.agents.update_conversation(row['id'], cwd=row['cwd'])
        return row
    if (row.get('checkout_policy') != 'auto' or not row.get('project_id') or
            row.get('mode') not in {'agent', 'plan'} or row.get('runner_id') or row.get('workflow') or row.get('scratch')):
        return row
    if service.orchestrator.relation(row['id']):
        # Delegated children already have a host-bound worktree/snapshot/read-only
        # target. A new session-level checkout must never replace that boundary.
        return row
    projects = getattr(service.state, 'projects', None)
    if not projects:
        raise ValueError('Automatic coding worktrees require an available execution project')
    source = Path(row.get('source_cwd') or row['cwd']).resolve(strict=True)
    project = projects.project(row['project_id'])
    project_root = Path(project['path']).resolve(strict=True)
    if not source.is_relative_to(project_root):
        raise PermissionError('Coding folder is outside the authorized execution project')
    service.record(principal, 'conversation', row['id'], scope='agent-run')
    result = _git(source, 'rev-parse', '--show-toplevel')
    if result.returncode:
        # A genuinely non-Git folder is a supported direct execution mode.
        # Corrupt/inaccessible Git metadata and missing Git are errors.
        if not any((folder / '.git').exists() or (folder / '.git').is_symlink()
                   for folder in [source, *source.parents]) and 'not a git repository' in result.stderr.lower():
            with service.store.lock:
                current = service.store.get('conversation', row['id'])
                service.store.update('conversation', row['id'], {**current, 'checkout_state': 'not-git'}, current['revision'])
            return service.session(principal, row['id'], turns=False)
        raise ValueError('Could not inspect Git for automatic isolation; repair repository access and retry')
    repo_root = Path(result.stdout.strip()).resolve(strict=True)
    if repo_root != project_root:
        raise ValueError('Automatic coding isolation requires the registered Git repository root; register that root as the execution project and retry')
    relative = source.relative_to(repo_root)
    if row['engine'] != 'internal' and service.agents.workspace_turns_page(row['id'], limit=1):
        raise Conflict('This native conversation already started in its original folder; create a new coding conversation for automatic isolation')
    if delegated:
        raise Conflict('Start this coding conversation once before granting scheduled execution so its worktree is included in the reviewed target')
    for scope in ('git-read', 'git-write'):
        service.require(principal, scope, row['project_id'], str(source), resource_kind='conversation', resource_id=row['id'])
    delivery = getattr(service.state, 'delivery', None)
    if not delivery:
        raise ValueError('Automatic coding worktree service is unavailable; retry when Git delivery is available')
    intent = row.get('worktree_intent')
    if not intent:
        try:
            base = git_ops._require_ok(_git(source, 'rev-parse', '--verify', 'HEAD')).strip()
            dirty = bool(git_ops._require_ok(_git(source, 'status', '--porcelain=v1', '-z', '--untracked-files=normal')))
        except HTTPException as exc:
            raise ValueError('Automatic isolation requires an accessible Git HEAD commit; commit the initial project state and retry') from exc
        intent = {'base_ref': base, 'source_cwd': str(source), 'relative': relative.as_posix(), 'source_dirty': dirty}
        intent = service.store.reserve_session_worktree_intent(row['id'], intent)
    try:
        tree = delivery.session_worktree(row['project_id'], str(project_root), row['id'], intent['base_ref'])
        target = service.worktree_target(row['project_id'], tree['id'])
        try:
            cwd = (Path(target['path']) / intent['relative']).resolve(strict=True)
        except OSError as exc:
            raise ValueError('Coding subfolder does not exist at committed HEAD; commit that folder and create a new coding conversation') from exc
        if not cwd.is_dir() or not cwd.is_relative_to(Path(target['path'])):
            raise ValueError('Coding subfolder does not exist at committed HEAD; commit that folder and create a new coding conversation')
        # Authorization can be revoked while Git is preparing the checkout.
        for scope in ('agent-run', 'git-read', 'git-write'):
            service.require(principal, scope, row['project_id'], str(source), resource_kind='conversation', resource_id=row['id'])
    except HTTPException as exc:
        raise ValueError('Automatic coding worktree could not be prepared; inspect Git permissions and retry. The original folder was retained.') from exc
    notice = ('Coding uses a separate worktree from committed HEAD. Uncommitted and untracked source files remain in the original folder and were not copied.'
              if intent['source_dirty'] else 'Coding uses a separate worktree from committed HEAD. Changes stay here for review.')
    with service.store.lock:
        current = service.store.get('conversation', row['id'])
        resolved = service.session(principal, row['id'], turns=False)
        if resolved.get('worktree_id'):
            if resolved['worktree_id'] == target['id'] and resolved['cwd'] == str(cwd):
                return resolved
            raise Conflict('The session checkout changed while preparing; review its current target')
        if service.execution_target(resolved) != service.execution_target(row):
            raise Conflict('Session execution settings changed while preparing its checkout; review and retry')
        if service.active(row['id']):
            raise Conflict('A turn started while the checkout was preparing; the running conversation was not moved')
        service.store.update('conversation', row['id'], {**current, 'cwd': str(cwd), 'worktree_id': target['id'],
            'worktree_digest': target['digest'], 'checkout_state': 'isolated', 'checkout_notice': notice,
            'source_cwd': intent['source_cwd'], 'source_dirty': intent['source_dirty'], 'worktree_base_ref': intent['base_ref']}, current['revision'])
        service.agents.update_conversation(row['id'], cwd=str(cwd))
    service.store.log(principal.id, 'conversation', row['id'], 'worktree.attached', {'worktree_id': target['id'], 'source_dirty': intent['source_dirty']})
    return service.session(principal, row['id'], turns=False)
