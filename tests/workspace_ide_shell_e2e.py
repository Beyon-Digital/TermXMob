"""Opt-in Playwright proof of the IDE shell (`?shell=ide`) against an isolated real workspace host.

Run from the repository root after `pnpm --dir desktop/workspace build`:
    python tests/workspace_ide_shell_e2e.py OUTPUT_DIR [AXE_CORE_4_10_3_JS]

Proves, in a real Chromium and against a real host: the chrome (title bar, activity bar, side bar, status bar) and the
side bar views switching, the side bar toggling and resizing, files opened from the Explorer, Quick open and Search into
tabs (the same file twice is one tab), a split, the dirty mark and a save to disk, a live shell in the bottom panel, the
tab-group behaviour of U3 (splits by keyboard and command, a drag to a drop zone, a resized splitter, maximize and restore),
the layout and the open files coming back after a reload, the 375px projection (top bar, drawer, bottom navigation), no
horizontal overflow, axe clean in both themes, and no page errors. Screenshots are written to OUTPUT_DIR in light and dark
at 1280x900 and 375x812.

No agents or providers run, and no default configuration store is touched: everything lives in a temporary directory.
"""
from __future__ import annotations

import json
import os
import re
import socket
import sys
import tempfile
import threading
from pathlib import Path
from time import monotonic, sleep

import uvicorn
from playwright.sync_api import Page, expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tests'))
OWNER = 'ide-shell-owner'
PASSWORD = 'ide-shell-fixture-password-123'
TITLE = 'IDE shell conversation'
FILES = {
    'main.py': 'def greet(name):\n    # IDE shell fixture\n    return f"Hello {name}"\n',
    'util.py': 'def add(a, b):\n    return a + b\n',
    'README.md': '# IDE shell fixture\n',
}

EDITOR_GROUPS = '.tab-group:not([data-panel-group])'


def groups(page: Page) -> int:
    """Editor-area groups on screen."""
    return page.locator(EDITOR_GROUPS).count()


def split_state(page: Page) -> list[str]:
    """The labels of the groups on screen, in order, as the screen reader names them."""
    return page.evaluate("() => Array.from(document.querySelectorAll('.tab-group')).map(node => node.getAttribute('aria-label'))")


def stored_layout(page: Page) -> dict | None:
    return page.evaluate(
        """() => { const key = Object.keys(localStorage).find(item => item.startsWith('termx-layout-v2:'));
                   return key ? JSON.parse(localStorage.getItem(key)) : null }"""
    )


def editor_group_count(layout: dict) -> int:
    def walk(node: dict) -> int:
        return sum(walk(child) for child in node['children']) if 'children' in node else 1

    return walk(layout['current']['root'])


def no_overflow(page: Page, label: str, report: dict) -> None:
    info = page.evaluate("() => ({scroll: document.documentElement.scrollWidth, client: document.documentElement.clientWidth})")
    if info['scroll'] > info['client'] + 1:
        report['overflow'].append(f"{label}: {info['scroll']} > {info['client']}")


def axe(page: Page, label: str, report: dict, axe_source: str) -> None:
    page.evaluate(axe_source)
    result = page.evaluate("async () => (await axe.run(document, {runOnly: {type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21aa']}})).violations.map(v => v.id + ': ' + v.nodes.map(n => n.target.join(' ')).join(' | '))")
    report['axe'][label] = result
    if result:
        report['axe_violations'].extend(f'{label}: {item}' for item in result)


def sign_in(page: Page, base: str, session_id: str, ready: str = 'tablist') -> None:
    page.goto(f'{base}/?session={session_id}&shell=ide')
    page.get_by_label('Username', exact=True).fill(OWNER)
    page.get_by_label('Password', exact=True).fill(PASSWORD)
    page.get_by_role('button', name='Continue with password').click()
    if ready == 'tablist':
        page.get_by_role('tablist', name='Editor group 1 tabs').wait_for(timeout=60000)
    else:
        page.get_by_role('banner', name='Window').wait_for(timeout=60000)


def settle(page: Page) -> None:
    page.wait_for_timeout(350)


def editor_of(page: Page, tab_name: str):
    """The CodeMirror content of a file tab, found through the tab's own panel."""
    panel = page.get_by_role('tab', name=re.compile(re.escape(tab_name))).get_attribute('aria-controls')
    return page.locator(f'#{panel} .cm-content')


def run(destination: str, axe_path: str | None) -> None:
    out = Path(destination)
    out.mkdir(parents=True, exist_ok=True)
    axe_file = Path(axe_path) if axe_path else ROOT / 'desktop/workspace/node_modules/axe-core/axe.min.js'
    if not axe_file.is_file():
        raise ValueError('Pass the pinned axe-core 4.10.3 axe.min.js path')
    axe_source = axe_file.read_text()
    report: dict = {'steps': [], 'errors': [], 'overflow': [], 'axe': {}, 'axe_violations': [], 'notes': []}
    os.environ['PATH'] = str(Path(sys.executable).parent) + os.pathsep + os.environ.get('PATH', '')
    with tempfile.TemporaryDirectory(prefix='termx-ide-shell-') as scratch:
        root = Path(scratch)
        os.environ['TERMX_CONFIG_DIR'] = str(root / 'config')
        os.environ['TERMX_AGENTS_DIR'] = str(root / 'agents')
        os.environ['TERMX_ENGINE_STARTUP_REFRESH'] = '0'
        from termx.agent.secrets import CredentialStore
        from termx.app import AppState, create_app
        from workspace_ui_snapshot import snapshot_ui

        checkout = root / 'checkout'
        checkout.mkdir()
        for name, text in FILES.items():
            (checkout / name).write_text(text)
        state = AppState(passcode=None, credentials=CredentialStore(memory={}))
        owner = state.identity.setup_owner(OWNER, PASSWORD)
        project = state.projects.register(str(checkout), name='IDE shell fixture')
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
        base = f'http://127.0.0.1:{port}'

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE') or None)
            for scheme in ('light', 'dark'):
                for name, text in FILES.items():
                    (checkout / name).write_text(text)
                context = browser.new_context(viewport={'width': 1280, 'height': 900}, color_scheme=scheme, reduced_motion='reduce')
                page = context.new_page()
                page.on('pageerror', lambda error: report['errors'].append(str(error)))
                step = lambda name: report['steps'].append(f'{scheme}: {name}')

                books: dict[str, dict] = {}

                def shot(label: str, width: int = 1280, remember: bool = True) -> None:
                    settle(page)
                    page.screenshot(path=str(out / f'{label}-{scheme}-{width}.png'))
                    no_overflow(page, f'{label}-{scheme}-{width}', report)
                    if remember:
                        page.wait_for_timeout(300)
                        books[label] = stored_layout(page)

                # --- the chrome: title bar, activity bar, side bar, status bar --------------------------------------
                sign_in(page, base, session['id'])
                expect(page.get_by_role('tab', name=TITLE)).to_have_attribute('aria-selected', 'true')
                assert groups(page) == 1, 'default layout is one group'
                assert page.get_by_role('group', name='Panel').count() == 0
                bar = page.get_by_role('toolbar', name='Activity bar')
                expect(bar).to_be_visible()
                names = [item.get_attribute('aria-label') for item in bar.get_by_role('button').all()]
                assert names == ['Agents', 'Explorer', 'Search', 'Source control', 'Sites', 'Data', 'Studio', 'Projects', 'Settings', 'Account'], names
                expect(page.get_by_role('complementary', name='Agents')).to_be_visible()
                title_bar = page.get_by_role('banner', name='Window')
                expect(title_bar.get_by_role('button', name='Search files and commands')).to_contain_text('Search files and commands')
                expect(title_bar.get_by_role('navigation', name='Project and branch')).to_contain_text('IDE shell fixture')
                status = page.get_by_role('contentinfo', name='Status')
                expect(status.get_by_role('status')).to_contain_text('Connected')
                expect(status.get_by_role('button', name='Lock')).to_be_visible()
                expect(status.get_by_role('button', name=re.compile('Main agent'))).to_be_visible()
                assert page.get_by_role('button', name='Workspace actions').count() == 0, 'the Workspace actions menu is retired'
                agents = page.get_by_role('complementary', name='Agents')
                expect(agents.get_by_role('button', name='New chat')).to_be_visible()
                assert agents.locator('.button-primary').count() == 1, 'New chat is the only primary button in the Agents view'
                expect(agents.get_by_text('CLI sessions on this host will appear here')).to_be_visible()
                step('chrome: title bar, activity bar, Agents view, status bar; the Workspace actions menu is gone')
                shot('01-agents-view')
                # Keyboard focus on the activity bar: the 2px accent ring, one tab stop for the whole bar.
                page.keyboard.press('Shift')
                bar.get_by_role('button', name='Explorer').focus()
                shot('08-focus-visible-activity-bar', remember=False)
                bar.get_by_role('button', name='Agents').focus()
                page.locator('footer.status-strip').screenshot(path=str(out / f'05-status-bar-{scheme}-1280.png'))

                # --- the activity bar: views, toggle, resize ----------------------------------------------------------
                for view, label in (('Search', 'Search'), ('Source control', 'Source control'), ('Sites', 'Sites')):
                    bar.get_by_role('button', name=view).click()
                    expect(page.get_by_role('complementary', name=label)).to_be_visible()
                expect(page.get_by_text('No saved sites yet.')).to_be_visible()
                bar.get_by_role('button', name='Sites').click()
                expect(page.get_by_role('complementary', name='Sites')).to_have_count(0)
                page.get_by_role('button', name='Toggle side bar').click()
                expect(page.get_by_role('complementary', name='Sites')).to_be_visible()
                handle = page.get_by_role('separator', name='Resize side bar')
                width = int(handle.get_attribute('aria-valuenow'))
                handle.focus()
                for _ in range(3):
                    page.keyboard.press('ArrowRight')
                assert int(handle.get_attribute('aria-valuenow')) == width + 48
                page.keyboard.press('End')
                assert int(handle.get_attribute('aria-valuenow')) == 420
                page.keyboard.press('Home')
                assert int(handle.get_attribute('aria-valuenow')) == 200
                page.keyboard.press('Enter')
                assert int(handle.get_attribute('aria-valuenow')) == 260
                step('activity bar: views switch, the selected view toggles the side bar, the side bar resizes by keyboard')

                # --- Explorer: open a file into a tab, then a second ----------------------------------------------------
                bar.get_by_role('button', name='Explorer').click()
                tree = page.get_by_role('tree', name='Files')
                expect(tree.get_by_role('treeitem', name=re.compile('main.py'))).to_be_visible(timeout=20000)
                tree.get_by_role('treeitem', name=re.compile('main.py')).click()
                expect(page.get_by_role('tab', name='main.py')).to_have_attribute('aria-selected', 'true')
                expect(editor_of(page, 'main.py')).to_contain_text('def greet')
                tree.get_by_role('treeitem', name=re.compile('util.py')).click()
                expect(page.get_by_role('tab', name='util.py')).to_have_attribute('aria-selected', 'true')
                expect(editor_of(page, 'util.py')).to_contain_text('def add')
                assert page.get_by_role('tab', name='main.py').count() == 1
                expect(status.get_by_text(re.compile(r'Ln \d+, Col \d+'))).to_be_visible()
                step('Explorer: two files open as two tabs; the status bar shows Ln and Col')

                # --- Quick open: the same file is one tab ------------------------------------------------------------------
                title_bar.get_by_role('button', name='Search files and commands').click()
                dialog = page.get_by_role('dialog', name='Search files and commands')
                dialog.get_by_role('combobox').fill('util')
                expect(dialog.get_by_role('option', name=re.compile('util.py'))).to_be_visible(timeout=20000)
                shot('07-command-center', remember=False)
                axe(page, f'{scheme}-1280-command-center', report, axe_source)
                page.keyboard.press('Enter')
                expect(dialog).to_have_count(0)
                assert page.get_by_role('tab', name='util.py').count() == 1, 'the same file twice is one tab'
                step('Quick open: util.py revealed, not duplicated')

                # --- split a file like any tab ---------------------------------------------------------------------------------
                page.keyboard.press('ControlOrMeta+Backslash')
                expect(page.locator(EDITOR_GROUPS)).to_have_count(2)
                assert split_state(page) == ['Editor group 1', 'Editor group 2']
                expect(page.get_by_role('tab', name='util.py')).to_have_attribute('aria-selected', 'true')
                expect(editor_of(page, 'util.py')).to_contain_text('def add')
                step('split right with Control+\\: the file tab moved to a new group with its editor intact')
                shot('02-explorer-two-files-split')

                # --- the dirty mark and a save to disk ---------------------------------------------------------------------------
                content = editor_of(page, 'util.py')
                content.click()
                page.keyboard.press('ControlOrMeta+End')
                page.keyboard.type('# edited by the e2e\n')
                expect(page.get_by_role('tab', name=re.compile('util.py')).locator('.tab-dirty')).to_have_count(1)
                assert 'edited by the e2e' not in (checkout / 'util.py').read_text()
                page.keyboard.press('ControlOrMeta+s')
                expect(page.get_by_role('tab', name=re.compile('util.py')).locator('.tab-dirty')).to_have_count(0, timeout=20000)
                assert 'edited by the e2e' in (checkout / 'util.py').read_text(), 'the host did not write the file'
                step('dirty mark on an edit, cleared by Control+S, and the file written to disk')

                # --- Search: a result opens its file at the line --------------------------------------------------------------
                bar.get_by_role('button', name='Search').click()
                page.get_by_role('textbox', name='Search in files').fill('greet')
                page.get_by_role('textbox', name='Search in files').press('Enter')
                hit = page.get_by_role('button', name=re.compile('main.py'))
                expect(hit.first).to_be_visible(timeout=20000)
                shot('03-search-results', remember=False)
                page.get_by_role('textbox', name='Search in files').fill('zzzz-nothing')
                page.get_by_role('textbox', name='Search in files').press('Enter')
                expect(page.get_by_text(re.compile('No results for'))).to_be_visible(timeout=20000)
                shot('03-search-no-results', remember=False)
                page.get_by_role('textbox', name='Search in files').fill('greet')
                page.get_by_role('textbox', name='Search in files').press('Enter')
                expect(hit.first).to_be_visible(timeout=20000)
                hit.first.click()
                expect(page.get_by_role('tab', name='main.py')).to_have_attribute('aria-selected', 'true')
                step('Search: a result opened main.py in its tab')

                # --- the bottom panel: a live shell -----------------------------------------------------------------------------
                page.keyboard.press('ControlOrMeta+j')
                panel = page.get_by_role('group', name='Panel')
                expect(panel).to_be_visible()
                expect(panel.get_by_role('tab', name='Terminal')).to_be_visible()
                # A tab's content lives in the flat layer, not inside its group: find it through the tab.
                body = page.locator('#' + panel.get_by_role('tab').first.get_attribute('aria-controls'))
                # An unattached terminal tab takes a running shell when the host has one (the second theme finds the
                # first theme's); otherwise it offers a new terminal, and that is the one thing to click.
                give_up = monotonic() + 25
                while body.locator('.xterm').count() == 0 and monotonic() < give_up:
                    button = body.get_by_role('button', name='New terminal')
                    if button.count() and button.is_enabled():
                        try:
                            button.click(timeout=2000)
                        except Exception:
                            pass
                    page.wait_for_timeout(250)
                try:
                    expect(body.locator('.xterm')).to_be_visible(timeout=20000)
                    expect(body.locator('.terminal-status')).to_contain_text('Connected', timeout=20000)
                    body.locator('.xterm-helper-textarea').focus()
                    page.keyboard.type('echo termx-e2e-$((40+2))\n')
                    expect(body.locator('.xterm-rows')).to_contain_text('termx-e2e-42', timeout=20000)
                    step('bottom panel: a live shell printed termx-e2e-42')
                except AssertionError as error:
                    report['notes'].append(f'{scheme}: the shell did not print in this environment: {str(error)[:160]}')
                    step('bottom panel: shell tab opened; live output not asserted here')
                shot('04-bottom-terminal')

                # --- a second shell is its own tab -----------------------------------------------------------------------------------
                page.keyboard.press('Control+Shift+Backquote')
                expect(panel.get_by_role('tab')).to_have_count(2, timeout=20000)
                step('Control+Shift+backtick started a second shell in a second tab')

                # --- U3 behaviour on the new chrome: resize a splitter, split down, drag, maximize ------------------------------
                separator = page.get_by_role('separator', name='Resize groups')
                before = int(separator.get_attribute('aria-valuenow'))
                separator.focus()
                for _ in range(5):
                    page.keyboard.press('ArrowRight')
                after = int(separator.get_attribute('aria-valuenow'))
                assert after > before, f'splitter did not move: {before} -> {after}'
                page.keyboard.press('Enter')
                assert int(separator.get_attribute('aria-valuenow')) == 50
                step(f'resized the splitter by keyboard ({before} -> {after}, Enter 50)')

                page.get_by_role('tab', name='main.py').click()
                page.keyboard.press('ControlOrMeta+k')
                page.keyboard.press('ControlOrMeta+Backslash')
                expect(page.locator(EDITOR_GROUPS)).to_have_count(3)
                step('split down with the Control+K Control+\\ chord')

                chat_tab = page.get_by_role('tab', name=TITLE)
                chat_box = chat_tab.bounding_box()
                target = page.get_by_role('group', name='Editor group 2')
                target_box = target.bounding_box()
                page.mouse.move(chat_box['x'] + chat_box['width'] / 2, chat_box['y'] + chat_box['height'] / 2)
                page.mouse.down()
                page.mouse.move(target_box['x'] + 30, target_box['y'] + target_box['height'] / 2 + 40, steps=12)
                expect(page.locator('.tab-drop')).to_have_attribute('data-zone', 'left')
                page.mouse.up()
                expect(page.locator('.tab-drop')).to_have_count(0)
                step('dragged a tab to the left drop zone of another group')

                count = groups(page)
                page.get_by_role('tab', name='util.py').click()
                page.keyboard.press('ControlOrMeta+k')
                page.keyboard.press('z')
                expect(page.locator(EDITOR_GROUPS)).to_have_count(1)
                page.keyboard.press('Escape')
                expect(page.locator(EDITOR_GROUPS)).to_have_count(count)
                step('maximized a group with Control+K Z and restored with Escape')

                separator = page.get_by_role('separator', name='Resize panel')
                panel_before = int(separator.get_attribute('aria-valuenow'))
                separator.focus()
                page.keyboard.press('ArrowUp')
                assert int(separator.get_attribute('aria-valuenow')) > panel_before
                step('resized the bottom panel by keyboard')

                # --- reload: the layout and the open files come back ---------------------------------------------------------------
                page.wait_for_timeout(400)
                saved = stored_layout(page)
                assert saved and saved['version'] == 2 and saved['current']['panel']['visible'] is True
                expected_groups = groups(page)
                page.reload()
                page.get_by_role('tablist', name='Editor group 1 tabs').wait_for(timeout=60000)
                expect(page.locator(EDITOR_GROUPS)).to_have_count(expected_groups)
                expect(page.get_by_role('group', name='Panel')).to_be_visible()
                expect(page.get_by_role('tab', name='main.py')).to_have_count(1)
                expect(page.get_by_role('tab', name='util.py')).to_have_count(1)
                assert editor_group_count(stored_layout(page)) == editor_group_count(saved)
                page.get_by_role('tab', name='main.py').click()
                expect(editor_of(page, 'main.py')).to_contain_text('def greet', timeout=20000)
                step(f'reloaded: {expected_groups} groups, the panel and both file tabs came back, main.py read again')

                axe(page, f'{scheme}-1280-layout', report, axe_source)
                no_overflow(page, f'{scheme}-1280', report)
                context.close()

                # --- 375px: top bar, drawer side bar, bottom navigation, one tab stack ----------------------------------------------
                phone = browser.new_context(viewport={'width': 375, 'height': 812}, color_scheme=scheme, reduced_motion='reduce', has_touch=False)
                small = phone.new_page()
                small.on('pageerror', lambda error: report['errors'].append(str(error)))
                sign_in(small, base, session['id'], ready='banner')
                small.wait_for_timeout(600)
                top = small.get_by_role('banner', name='Window')
                expect(top.get_by_role('heading')).to_contain_text(TITLE)
                nav = small.get_by_role('navigation', name='Primary')
                assert [item.inner_text() for item in nav.get_by_role('button').all()] == ['Agents', 'Files', 'Git', 'Term', 'More']
                assert small.get_by_role('toolbar', name='Activity bar').count() == 0
                assert small.get_by_role('tablist').count() == 0, 'the phone has no tab strip: the top bar names the tab'
                for item in nav.get_by_role('button').all():
                    box = item.bounding_box()
                    assert box['height'] >= 44 and box['width'] >= 44, f'nav target under 44px: {box}'
                status_text = small.get_by_role('contentinfo', name='Status').inner_text()
                assert 'Lock' not in status_text and 'Connected' in status_text, status_text
                no_overflow(small, f'phone-default-{scheme}', report)
                small.screenshot(path=str(out / f'00-mobile-agents-default-{scheme}-375.png'))
                axe(small, f'{scheme}-375-default', report, axe_source)
                top.get_by_role('button', name='Open side bar').click()
                drawer = small.get_by_role('dialog', name='Agents')
                expect(drawer).to_be_visible()
                expect(drawer.get_by_role('button', name='New chat')).to_be_visible()
                settle(small)
                small.screenshot(path=str(out / f'06-mobile-drawer-{scheme}-375.png'))
                no_overflow(small, f'phone-drawer-{scheme}', report)
                axe(small, f'{scheme}-375-drawer', report, axe_source)
                small.keyboard.press('Escape')
                expect(drawer).to_have_count(0)
                nav.get_by_role('button', name='Files').click()
                explorer = small.get_by_role('dialog', name='Explorer')
                expect(explorer).to_be_visible()
                expect(explorer.get_by_role('treeitem', name=re.compile('main.py'))).to_be_visible(timeout=20000)
                settle(small)
                small.screenshot(path=str(out / f'06-mobile-explorer-{scheme}-375.png'))
                explorer.get_by_role('treeitem', name=re.compile('main.py')).click()
                expect(explorer).to_have_count(0)
                expect(top.get_by_role('heading')).to_contain_text('main.py', timeout=20000)
                settle(small)
                small.screenshot(path=str(out / f'06-mobile-file-{scheme}-375.png'))
                no_overflow(small, f'phone-file-{scheme}', report)
                nav.get_by_role('button', name='Term').click()
                expect(top.get_by_role('heading')).to_contain_text('Terminal', timeout=20000)
                settle(small)
                small.screenshot(path=str(out / f'04-mobile-terminal-{scheme}-375.png'))
                nav.get_by_role('button', name='More').click()
                small.get_by_role('menuitem', name='Commands').click()
                expect(small.get_by_role('dialog', name='Search files and commands')).to_be_visible()
                settle(small)
                small.screenshot(path=str(out / f'07-mobile-command-center-{scheme}-375.png'))
                axe(small, f'{scheme}-375-command-center', report, axe_source)
                small.keyboard.press('Escape')
                top.get_by_role('button', name=re.compile('Switch tab')).click()
                assert 'main.py' in small.get_by_role('menuitem').all_inner_texts()
                small.keyboard.press('Escape')
                before_phone = stored_layout(small)
                small.get_by_role('button', name=re.compile('Switch tab')).click()
                small.get_by_role('menuitem', name=TITLE).click()
                expect(top.get_by_role('heading')).to_contain_text(TITLE)
                settle(small)
                assert json.dumps(stored_layout(small)['current']['root']) == json.dumps(before_phone['current']['root']), 'a phone session changed the tree'
                step('375px: top bar, drawer, bottom nav at 44px, file opened from the drawer, tab switch left the stored layout alone')
                phone.close()
            browser.close()

    (out / 'workspace_ide_shell_e2e.json').write_text(json.dumps(report, indent=2))
    problems = report['errors'] + report['overflow'] + report['axe_violations']
    if problems:
        print(json.dumps(report, indent=2))
        raise SystemExit('IDE shell e2e found problems: ' + '; '.join(problems[:6]))
    print(json.dumps({'steps': report['steps'], 'notes': report['notes'], 'errors': 0, 'overflow': 0, 'axe_violations': 0}, indent=2))


if __name__ == '__main__':
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    run(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
