"""Opt-in Playwright proof of source control in the IDE shell (`?shell=ide`) against an isolated real workspace host.

Run from the repository root after `pnpm --dir desktop/workspace build`:
    python tests/workspace_scm_e2e.py OUTPUT_DIR [AXE_CORE_4_10_3_JS]

The fixture is a real scratch Git repository (with a bare "origin" for push) registered as a project. Proves, in a real
Chromium: the Source control view lists modified, added (including a file in a new folder) and deleted files; stage, unstage
and stage-all change the index on disk; a hunk is staged and unstaged from the diff tab; diff tabs open inline and side by
side, are one tab per file side, and move between changes with F7; the commit box commits with Control+Enter and the commit is
in `git log`; the status bar shows the dirty mark and clears it; the primary button becomes Push when the checkout is clean
and ahead, confirms the exact push, and the commit reaches the remote; the pull request and branch forms open; there is no
discard. It also checks the 375px layout (drawer view and an inline diff tab), no horizontal overflow, axe in both themes and
no page errors. Screenshots go to OUTPUT_DIR in light and dark at 1280x900 and 375x812.

No agents or providers run, no GitHub call is made, and no default configuration store is touched.
"""
from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from time import monotonic, sleep

import uvicorn
from playwright.sync_api import Page, expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tests'))
from workspace_ide_shell_e2e import OWNER, PASSWORD, axe, no_overflow, settle, sign_in  # noqa: E402

TITLE = 'Source control conversation'
APP = ''.join(f'line_{number} = {number}\n' for number in range(1, 41))
FILES = {
    'app.py': APP,
    'util.py': 'def add(a, b):\n    return a + b\n',
    'README.md': '# Source control fixture\n',
    'docs/notes.md': 'Notes to delete.\n',
}


def git(where: Path, *args: str) -> str:
    return subprocess.run(['git', '-C', str(where), *args], check=True, capture_output=True, text=True).stdout


def edit_files(checkout: Path) -> None:
    """Two hunks in app.py, a new file, a file in a new folder, and a deletion."""
    lines = APP.splitlines(keepends=True)
    lines[2] = 'line_3 = "changed three"\n'
    lines[35] = 'line_36 = "changed thirty-six"\n'
    (checkout / 'app.py').write_text(''.join(lines))
    (checkout / 'new_feature.py').write_text('def feature():\n    return 1\n')
    (checkout / 'pkg').mkdir(exist_ok=True)
    (checkout / 'pkg' / 'mod.py').write_text('VALUE = 1\n')
    (checkout / 'docs' / 'notes.md').unlink()


def reset(checkout: Path, base: str) -> None:
    git(checkout, 'reset', '--hard', base)
    git(checkout, 'clean', '-fdq')
    git(checkout, 'push', '--force', 'origin', f'{base}:main')
    git(checkout, 'fetch', 'origin')


def run(destination: str, axe_path: str | None) -> None:
    out = Path(destination)
    out.mkdir(parents=True, exist_ok=True)
    axe_file = Path(axe_path) if axe_path else ROOT / 'desktop/workspace/node_modules/axe-core/axe.min.js'
    if not axe_file.is_file():
        raise ValueError('Pass the pinned axe-core 4.10.3 axe.min.js path')
    axe_source = axe_file.read_text()
    report: dict = {'steps': [], 'errors': [], 'overflow': [], 'axe': {}, 'axe_violations': [], 'notes': []}
    os.environ['PATH'] = str(Path(sys.executable).parent) + os.pathsep + os.environ.get('PATH', '')
    with tempfile.TemporaryDirectory(prefix='termx-scm-') as scratch:
        root = Path(scratch)
        os.environ['TERMX_CONFIG_DIR'] = str(root / 'config')
        os.environ['TERMX_AGENTS_DIR'] = str(root / 'agents')
        os.environ['TERMX_ENGINE_STARTUP_REFRESH'] = '0'
        os.environ['GIT_CONFIG_GLOBAL'] = str(root / 'gitconfig')
        os.environ['GIT_CONFIG_NOSYSTEM'] = '1'
        from termx.agent.secrets import CredentialStore
        from termx.app import AppState, create_app
        from workspace_ui_snapshot import snapshot_ui

        checkout = root / 'checkout'
        origin = root / 'origin.git'
        subprocess.run(['git', 'init', '-q', '--bare', '-b', 'main', str(origin)], check=True)
        checkout.mkdir()
        subprocess.run(['git', 'init', '-q', '-b', 'main', str(checkout)], check=True)
        git(checkout, 'config', 'user.name', 'Fixture Author')
        git(checkout, 'config', 'user.email', 'fixture@example.test')
        for name, text in FILES.items():
            (checkout / name).parent.mkdir(exist_ok=True)
            (checkout / name).write_text(text)
        git(checkout, 'add', '-A')
        git(checkout, 'commit', '-q', '-m', 'Start the fixture')
        base = git(checkout, 'rev-parse', 'HEAD').strip()
        git(checkout, 'remote', 'add', 'origin', str(origin))
        git(checkout, 'push', '-q', '-u', 'origin', 'main')

        state = AppState(passcode=None, credentials=CredentialStore(memory={}))
        owner = state.identity.setup_owner(OWNER, PASSWORD)
        project = state.projects.register(str(checkout), name='Source control fixture')
        app = create_app(state, web_dir=snapshot_ui(root, ROOT / 'desktop/workspace/dist'))
        session = state.workspace.create_session(owner, title=TITLE, project_id=project['id'], cwd=str(checkout))
        listener = socket.socket()
        listener.bind(('127.0.0.1', 0))
        listener.listen()
        port = listener.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(app, log_level='error', lifespan='on'))
        threading.Thread(target=lambda: server.run(sockets=[listener]), daemon=True).start()
        deadline = monotonic() + 15
        while not server.started:
            if monotonic() > deadline:
                raise RuntimeError('Host did not start')
            sleep(.05)
        base_url = f'http://127.0.0.1:{port}'

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE') or None)
            for scheme in ('light', 'dark'):
                reset(checkout, base)
                context = browser.new_context(viewport={'width': 1280, 'height': 900}, color_scheme=scheme, reduced_motion='reduce')
                page = context.new_page()
                page.on('pageerror', lambda error: report['errors'].append(str(error)))
                step = lambda name: report['steps'].append(f'{scheme}: {name}')

                def shot(label: str, width: int = 1280, target: Page | None = None) -> None:
                    target = target or page
                    settle(target)
                    target.screenshot(path=str(out / f'{label}-{scheme}-{width}.png'))
                    no_overflow(target, f'{label}-{scheme}-{width}', report)

                sign_in(page, base_url, session['id'])
                bar = page.get_by_role('toolbar', name='Activity bar')
                status = page.get_by_role('contentinfo', name='Status')
                bar.get_by_role('button', name='Source control').click()
                scm = page.get_by_role('complementary', name='Source control')

                # --- a clean checkout ------------------------------------------------------------------------------------
                expect(scm.get_by_text('No changes. The working tree matches the last commit.')).to_be_visible(timeout=30000)
                expect(scm.get_by_role('button', name='Commit')).to_be_disabled()
                expect(scm.get_by_text('No changes to commit.')).to_be_visible()
                assert scm.locator('.button-primary').count() == 1, 'Commit is the only primary button'
                expect(scm.locator('.scm-branch-name')).to_have_text('main')
                expect(status.get_by_role('button', name=re.compile(r'^main'))).not_to_contain_text('uncommitted')
                step('clean checkout: one sentence, Commit disabled with its reason, branch main, status bar not dirty')
                shot('01-scm-clean')
                axe(page, f'{scheme}-1280-scm-clean', report, axe_source)

                # --- modify, add, delete ----------------------------------------------------------------------------------
                edit_files(checkout)
                scm.get_by_role('button', name='Refresh source control').click()
                changes = scm.get_by_role('list', name='Changes', exact=True)
                expect(changes.get_by_role('listitem')).to_have_count(4, timeout=30000)
                text = changes.inner_text()
                for name in ('app.py', 'new_feature.py', 'mod.py', 'notes.md', 'pkg'):
                    assert name in text, f'{name} missing from Changes: {text}'
                letters = changes.locator('[data-letter]').all_inner_texts()
                assert sorted(letters) == ['D', 'M', 'U', 'U'], letters
                expect(status.get_by_role('button', name=re.compile(r'^main'))).to_contain_text('uncommitted', timeout=30000)
                expect(scm.get_by_text('Nothing is staged. Stage changes first.')).to_be_visible()
                expect(scm.get_by_role('button', name=re.compile('discard', re.I))).to_have_count(0)
                step('edits listed: M, two U (one in a new folder), D; status bar dirty; commit reason; no discard')
                shot('02-scm-changes')
                axe(page, f'{scheme}-1280-scm-changes', report, axe_source)

                # --- stage, unstage, stage all (the index on disk is the proof) ------------------------------------------------
                scm.get_by_role('button', name='Stage app.py').click()
                staged = scm.get_by_role('list', name='Staged changes')
                expect(staged.get_by_role('listitem')).to_have_count(1, timeout=20000)
                assert git(checkout, 'diff', '--cached', '--name-only').split() == ['app.py']
                scm.get_by_role('button', name='Unstage app.py').click()
                expect(staged).to_have_count(0, timeout=20000)
                assert git(checkout, 'diff', '--cached', '--name-only').split() == []
                scm.get_by_role('button', name='Stage all changes').click()
                expect(staged.get_by_role('listitem')).to_have_count(4, timeout=20000)
                assert sorted(git(checkout, 'diff', '--cached', '--name-only').split()) == ['app.py', 'docs/notes.md', 'new_feature.py', 'pkg/mod.py']
                scm.get_by_role('button', name='Unstage all staged changes').click()
                expect(staged).to_have_count(0, timeout=20000)
                assert git(checkout, 'diff', '--cached', '--name-only').split() == []
                step('stage, unstage, stage all and unstage all changed the index on disk')

                # --- diff tabs: inline, side by side, one tab per side -----------------------------------------------------------
                row = changes.locator('[data-scm-file]', has_text='app.py')
                row.click()
                expect(page.get_by_role('tab', name='app.py')).to_have_attribute('aria-selected', 'true')
                inline = page.get_by_role('region', name='Inline diff for app.py')
                expect(inline).to_contain_text('changed three', timeout=20000)
                expect(inline).to_contain_text('changed thirty-six')
                added = inline.locator('.diff-addition').first
                assert 'rgba' in added.evaluate('node => getComputedStyle(node).backgroundColor') or 'rgb' in added.evaluate('node => getComputedStyle(node).backgroundColor')
                shot('03-diff-inline')
                axe(page, f'{scheme}-1280-diff-inline', report, axe_source)
                row.click()
                assert page.get_by_role('tab', name='app.py').count() == 1, 'the same file side is one tab'
                page.get_by_role('button', name='Side by side').click()
                split = page.get_by_role('region', name='Side by side diff for app.py')
                expect(split).to_be_visible()
                expect(split.locator('.diff-split-row').first).to_be_visible()
                shot('04-diff-split')
                axe(page, f'{scheme}-1280-diff-split', report, axe_source)
                page.get_by_role('button', name='Inline').click()
                expect(inline).to_be_visible()
                step('diff tab opened inline and side by side; the same file side stayed one tab')

                # --- F7 between changes, then stage and unstage one hunk ----------------------------------------------------------
                inline.focus()
                page.keyboard.press('F7')
                expect(inline.locator('[data-hunk="0"]')).to_be_focused()
                page.keyboard.press('F7')
                expect(inline.locator('[data-hunk="1"]')).to_be_focused()
                page.keyboard.press('Shift+F7')
                expect(inline.locator('[data-hunk="0"]')).to_be_focused()
                inline.get_by_role('group', name=re.compile('Change 1 of 2')).get_by_role('button', name='Stage hunk').click()
                expect(inline.get_by_role('group', name=re.compile('Change 1 of 1'))).to_be_visible(timeout=20000)
                cached = git(checkout, 'diff', '--cached', '--', 'app.py')
                assert 'changed three' in cached and 'changed thirty-six' not in cached, cached
                scm.get_by_role('list', name='Staged changes').locator('[data-scm-file]', has_text='app.py').click()
                expect(page.get_by_role('tab', name='app.py (staged)')).to_have_attribute('aria-selected', 'true')
                staged_region = page.get_by_role('region', name='Inline diff for app.py').filter(has_text='changed three')
                expect(staged_region).to_be_visible(timeout=20000)
                staged_region.get_by_role('button', name='Unstage hunk').click()
                expect(page.get_by_text('Nothing is staged for this file.')).to_be_visible(timeout=20000)
                assert git(checkout, 'diff', '--cached', '--name-only').split() == []
                step('F7 and Shift+F7 moved between changes; one hunk was staged and unstaged from its diff tab')

                # --- stage everything, then commit from the box with Control+Enter ---------------------------------------------------
                scm.get_by_role('button', name='Stage all changes').click()
                expect(scm.get_by_role('list', name='Staged changes').get_by_role('listitem')).to_have_count(4, timeout=20000)
                expect(scm.get_by_text('Enter a commit message.')).to_be_visible()
                box = scm.get_by_role('textbox', name='Commit message')
                box.fill('Add the feature and edit app.py\n\nTwo hunks, a new module and a removed note.')
                expect(scm.get_by_text(re.compile(r'Subject \d+ of 72'))).to_be_visible()
                expect(scm.get_by_role('button', name='Commit')).to_be_enabled()
                shot('05-scm-staged-commit-box')
                axe(page, f'{scheme}-1280-scm-commit-box', report, axe_source)
                box.press('Control+Enter')
                expect(scm.get_by_text('Committed.')).to_be_visible(timeout=30000)
                assert git(checkout, 'log', '-1', '--pretty=%s').strip() == 'Add the feature and edit app.py'
                assert git(checkout, 'status', '--porcelain').strip() == ''
                expect(box).to_have_value('')
                expect(status.get_by_role('button', name=re.compile(r'^main'))).not_to_contain_text('uncommitted', timeout=30000)
                step('Control+Enter committed in the box: the commit is in git log, the box cleared, the status bar is clean again')

                # --- clean and ahead: Push, with the exact push confirmed ---------------------------------------------------------------
                expect(scm.get_by_text('1 ahead, 0 behind')).to_be_visible(timeout=30000)
                push = scm.get_by_role('button', name='Push')
                expect(push).to_be_enabled(timeout=30000)
                assert scm.locator('.button-primary').count() == 1
                expect(scm.get_by_text('1 commit ahead of the upstream branch.')).to_be_visible()
                shot('06-scm-push')
                push.click()
                dialog = page.get_by_role('dialog', name='Push main to origin')
                expect(dialog).to_be_visible()
                shot('07-confirm-push')
                axe(page, f'{scheme}-1280-confirm-push', report, axe_source)
                dialog.get_by_role('button', name='Push').click()
                expect(scm.get_by_text('Pushed.')).to_be_visible(timeout=60000)
                assert git(origin, 'log', '-1', '--pretty=%s', 'main').strip() == 'Add the feature and edit app.py', 'the commit did not reach the remote'
                expect(scm.get_by_role('button', name='Commit')).to_be_visible(timeout=30000)
                step('Push became the primary button, showed the exact push, and the commit reached the remote')

                # --- the forms ------------------------------------------------------------------------------------------------------------
                scm.get_by_role('button', name='Pull request').click()
                expect(scm.get_by_role('button', name='Create draft PR')).to_be_disabled()
                scm.get_by_label('Title').fill('Add the feature')
                expect(scm.get_by_role('button', name='Create draft PR')).to_be_enabled()
                shot('08-scm-pull-request')
                axe(page, f'{scheme}-1280-scm-pull-request', report, axe_source)
                scm.get_by_role('button', name='Pull request').click()
                scm.get_by_role('button', name='Branches and worktrees').click()
                expect(scm.get_by_label('Branch name')).to_be_visible()
                shot('09-scm-branches')
                scm.get_by_role('button', name='Branches and worktrees').click()
                step('the pull request and branch forms open and close; Create draft PR waits for a title')

                # --- commands -----------------------------------------------------------------------------------------------------------
                page.keyboard.press('ControlOrMeta+Shift+p')
                palette = page.get_by_role('dialog', name='Search files and commands')
                expect(palette).to_be_visible()
                palette.get_by_role('combobox').fill('Stage all changes')
                expect(palette.get_by_role('option', name=re.compile('Stage all changes'))).to_be_visible()
                page.keyboard.press('Escape')
                step('the command palette lists the source control commands')
                context.close()

                # --- 375px: the drawer view and an inline diff tab -----------------------------------------------------------------------
                reset(checkout, base)
                edit_files(checkout)
                git(checkout, 'add', 'new_feature.py')
                phone = browser.new_context(viewport={'width': 375, 'height': 812}, color_scheme=scheme, reduced_motion='reduce')
                small = phone.new_page()
                small.on('pageerror', lambda error: report['errors'].append(str(error)))
                sign_in(small, base_url, session['id'], ready='banner')
                small.wait_for_timeout(600)
                nav = small.get_by_role('navigation', name='Primary')
                nav.get_by_role('button', name='Git').click()
                drawer = small.get_by_role('dialog', name='Source control')
                expect(drawer).to_be_visible()
                expect(drawer.get_by_role('list', name='Staged changes').get_by_role('listitem')).to_have_count(1, timeout=30000)
                expect(drawer.get_by_role('list', name='Changes', exact=True).get_by_role('listitem')).to_have_count(3, timeout=30000)
                for target in drawer.get_by_role('button').all():
                    if not target.is_visible():
                        continue
                    box_ = target.bounding_box()
                    if box_ and (box_['height'] < 24 or box_['width'] < 24):
                        report['notes'].append(f'{scheme}: a 375px control under 24px: {target.get_attribute("aria-label") or target.inner_text()}')
                drawer.get_by_role('textbox', name='Commit message').fill('Phone commit')
                small.screenshot(path=str(out / f'01-mobile-scm-{scheme}-375.png'))
                no_overflow(small, f'phone-scm-{scheme}', report)
                axe(small, f'{scheme}-375-scm', report, axe_source)
                drawer.get_by_role('button', name='Pull request').click()
                small.wait_for_timeout(300)
                small.screenshot(path=str(out / f'08-mobile-scm-pull-request-{scheme}-375.png'))
                no_overflow(small, f'phone-scm-pr-{scheme}', report)
                drawer.get_by_role('button', name='Pull request').click()
                drawer.get_by_role('list', name='Changes', exact=True).locator('[data-scm-file]', has_text='app.py').click()
                expect(drawer).to_have_count(0)
                top = small.get_by_role('banner', name='Window')
                expect(top.get_by_role('heading')).to_contain_text('app.py', timeout=20000)
                expect(small.get_by_role('region', name='Inline diff for app.py')).to_contain_text('changed three', timeout=20000)
                expect(small.get_by_role('button', name='Side by side')).to_be_disabled()
                small.wait_for_timeout(300)
                small.screenshot(path=str(out / f'03-mobile-diff-{scheme}-375.png'))
                no_overflow(small, f'phone-diff-{scheme}', report)
                axe(small, f'{scheme}-375-diff', report, axe_source)
                step('375px: Git opens the view in the drawer, a row opens an inline diff tab, Side by side is off')
                phone.close()
                git(checkout, 'reset', '-q')
            browser.close()

    (out / 'workspace_scm_e2e.json').write_text(json.dumps(report, indent=2))
    problems = report['errors'] + report['overflow'] + report['axe_violations']
    if problems:
        print(json.dumps(report, indent=2))
        raise SystemExit('Source control e2e found problems: ' + '; '.join(problems[:6]))
    print(json.dumps({'steps': report['steps'], 'notes': report['notes'], 'errors': 0, 'overflow': 0, 'axe_violations': 0}, indent=2))


if __name__ == '__main__':
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    run(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
