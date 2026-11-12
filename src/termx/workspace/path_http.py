"""Read-only path selection with host or existing project authority."""
from __future__ import annotations

from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request

from termx.auth import extract_passcode
from termx.identity_http import ACCESS_COOKIE
from termx.workspace.store import Conflict


PAGE_SIZE = 200


def build_workspace_paths_router(state):
    router = APIRouter(prefix='/api/workspace')

    @router.get('/paths')
    def paths(request: Request, path: str = Query(default='', max_length=4096),
              project_id: str | None = None, session_id: str | None = None,
              worktree_id: str | None = None, kind: Literal['directory', 'file'] = 'directory',
              query: str = Query(default='', max_length=200), show_hidden: bool = False,
              offset: int = Query(default=0, ge=0)):
        raw = extract_passcode(authorization=request.headers.get('authorization')) or request.cookies.get(ACCESS_COOKIE)

        def actor():
            session = state.identity.resolve(raw, allow_locked=True)
            if not session:
                raise HTTPException(401, 'Managed sign-in required')
            if session.locked:
                raise HTTPException(423, 'Session locked; unlock required')
            return session.principal

        def authorized():
            principal = actor()
            if session_id:
                bound, identifier = state.workspace.session_files(principal, session_id, expected_worktree=worktree_id)
                if project_id and identifier != project_id:
                    raise HTTPException(409, 'Project changed; choose the folder again')
                return bound, identifier
            if worktree_id:
                raise HTTPException(400, 'Choose a conversation for a worktree')
            if project_id:
                state.workspace.require(principal, 'files-read', project_id)
                return state.projects, project_id
            # Identical host authority to register_project. A project grant never
            # authorizes browsing the rest of the host, even with an absolute path.
            scopes = state.auth.scopes(raw)
            if scopes is None:
                raise HTTPException(401, 'Invalid session')
            if 'files-write' not in scopes:
                raise HTTPException(403, 'Missing scope: files-write')
            state.authorization.require(raw, 'files-write')
            return None, None

        try:
            bound, identifier = authorized()  # No filesystem observations before authorization.
            scoped = bound is not None
            if '\0' in path:
                raise HTTPException(400, 'Invalid folder path')
            if scoped:
                target = bound.resolve(identifier, path)
                root = bound.resolve(identifier, '')
            else:
                target = Path(path).expanduser() if path else Path.home()
                if not target.is_absolute():
                    raise HTTPException(400, 'Choose an absolute folder path')
                target = target.resolve(strict=True)
                root = Path(target.anchor)
            if not target.is_dir():
                raise HTTPException(400, 'Choose a folder')

            def public_path(item):
                return '' if scoped and item == root else item.relative_to(root).as_posix() if scoped else str(item)

            entries = []
            needle = query.casefold()
            for child in target.iterdir():
                if (not show_hidden and child.name.startswith('.')) or needle not in child.name.casefold():
                    continue
                try:
                    # Do not publish outside-project symlink targets or metadata.
                    # The regular file APIs repeat this canonical path boundary.
                    resolved = child.resolve(strict=True)
                    if scoped and not resolved.is_relative_to(root):
                        continue
                    directory = resolved.is_dir()
                    if not directory and (kind == 'directory' or not resolved.is_file()):
                        continue
                    entries.append({'name': child.name, 'path': public_path(child), 'dir': directory})
                except (OSError, RuntimeError):
                    continue
            entries.sort(key=lambda entry: (not entry['dir'], entry['name'].casefold(), entry['name']))
            crumbs = [{'name': root.name or str(root), 'path': public_path(root)}]
            current = root
            for part in target.relative_to(root).parts:
                current = current / part
                crumbs.append({'name': part, 'path': public_path(current)})
            result = {'path': public_path(target), 'parent': public_path(target.parent) if target != root else None,
                      'breadcrumbs': crumbs, 'entries': entries[offset:offset + PAGE_SIZE],
                      'next_offset': offset + PAGE_SIZE if offset + PAGE_SIZE < len(entries) else None,
                      'total': len(entries), 'home': None if scoped else str(Path.home())}
            authorized()  # A revoked/locked session cannot receive a completed listing.
            return result
        except KeyError:
            raise HTTPException(404, 'Project or conversation not found') from None
        except Conflict as exc:
            raise HTTPException(409, str(exc)) from None
        except PermissionError:
            raise HTTPException(403, 'This folder is not accessible') from None
        except FileNotFoundError:
            raise HTTPException(404, 'Folder no longer exists') from None
        except (ValueError, OSError, RuntimeError):
            raise HTTPException(400, 'This folder cannot be opened') from None

    return router


def mount_workspace_paths(app, state):
    app.include_router(build_workspace_paths_router(state))
