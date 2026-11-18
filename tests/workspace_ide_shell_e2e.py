"""Opt-in Playwright proof of the tab-group layout (`?shell=ide`) against an isolated real workspace host.

Run from the repository root after `pnpm --dir desktop/workspace build`:
    python tests/workspace_ide_shell_e2e.py OUTPUT_DIR [AXE_CORE_4_10_3_JS]

Proves, in a real Chromium and against a real host: tabs open from the + menu, a split by keyboard and by
command, a drag to a drop zone with the pointer, a splitter resized by keyboard, maximize and restore, the
layout surviving a reload, the 375px projection, no horizontal overflow, axe clean in both themes, and no page
errors. Screenshots are written to OUTPUT_DIR (default, two-way split, three groups, panel, maximized, drag-over
zone, phone) in light and dark at 1280x900 and 375x812.

No agents or providers run, and no default configuration store is touched: everything lives in a temporary
directory.
"""
from __future__ import annotations

import json
import os
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


EDITOR_GROUPS = '.tab-group:not([data-panel-group])'


def groups(page: Page) -> int:
    """Editor-area groups on screen. (Panels such as the editor and the browser keep their own inner tab strips.)"""
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


def sign_in(page: Page, base: str, session_id: str, query: str = '') -> None:
    page.goto(f'{base}/?session={session_id}&shell=ide{query}')
    page.get_by_label('Username', exact=True).fill(OWNER)
    page.get_by_label('Password', exact=True).fill(PASSWORD)
    page.get_by_role('button', name='Continue with password').click()
    page.get_by_role('tablist', name='Editor group 1 tabs').wait_for(timeout=60000)


def settle(page: Page) -> None:
    page.wait_for_timeout(350)


def run(destination: str, axe_path: str | None) -> None:
    out = Path(destination)
    out.mkdir(parents=True, exist_ok=True)
    axe_file = Path(axe_path) if axe_path else ROOT / 'desktop/workspace/node_modules/axe-core/axe.min.js'
    if not axe_file.is_file():
        raise ValueError('Pass the pinned axe-core 4.10.3 axe.min.js path')
    axe_source = axe_file.read_text()
    report: dict = {'steps': [], 'errors': [], 'overflow': [], 'axe': {}, 'axe_violations': []}
    os.environ['PATH'] = str(Path(sys.executable).parent) + os.pathsep + os.environ.get('PATH', '')
    with tempfile.TemporaryDirectory(prefix='termx-ide-shell-') as scratch:
        root = Path(scratch)
        os.environ['TERMX_CONFIG_DIR'] = str(root / 'config')
        os.environ['TERMX_AGENTS_DIR'] = str(root / 'agents')
        os.environ['TERMX_ENGINE_STARTUP_REFRESH'] = '0'
        from termx.agent.secrets import CredentialStore
        from termx.app import AppState, create_app
        from workspace_ui_snapshot import snapshot_ui

        (root / 'main.py').write_text('def greet(name):\n    # IDE shell fixture\n    return f"Hello {name}"\n')
        state = AppState(passcode=None, credentials=CredentialStore(memory={}))
        owner = state.identity.setup_owner(OWNER, PASSWORD)
        project = state.projects.register(str(root), name='IDE shell fixture')
        app = create_app(state, web_dir=snapshot_ui(root, ROOT / 'desktop/workspace/dist'))
        session = state.workspace.create_session(owner, title=TITLE, project_id=project['id'], cwd=str(root))
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

                # --- default layout: one group, the chat, the side bar open, no panel ---------------------------
                sign_in(page, base, session['id'])
                expect(page.get_by_role('tab', name=TITLE)).to_have_attribute('aria-selected', 'true')
                assert groups(page) == 1, 'default layout is one group'
                assert page.get_by_role('separator', name='Resize groups').count() == 0
                assert page.get_by_role('group', name='Panel').count() == 0
                expect(page.get_by_role('complementary', name='Chats and agents')).to_be_visible()
                step('default layout')
                shot('default')

                # --- open a tab from the + menu ----------------------------------------------------------------
                page.get_by_role('button', name='New tab').click()
                page.get_by_role('menuitemcheckbox', name='Browser').click()
                expect(page.get_by_role('tab', name='Browser')).to_have_attribute('aria-selected', 'true')
                page.get_by_role('button', name='New tab').click()
                page.get_by_role('menuitemcheckbox', name='Editor').click()
                expect(page.get_by_role('tab', name='Editor')).to_have_attribute('aria-selected', 'true')
                step('opened Browser and Editor from the + menu')

                # --- split right by keyboard: the active tab moves to a new group --------------------------------
                page.keyboard.press('ControlOrMeta+Backslash')
                expect(page.locator(EDITOR_GROUPS)).to_have_count(2)
                assert split_state(page) == ['Editor group 1', 'Editor group 2']
                expect(page.get_by_role('separator', name='Resize groups')).to_have_count(1)
                step('split right with Control+\\')
                shot('split-two-way')

                # --- resize the splitter by keyboard ------------------------------------------------------------
                separator = page.get_by_role('separator', name='Resize groups')
                before = int(separator.get_attribute('aria-valuenow'))
                separator.focus()
                for _ in range(5):
                    page.keyboard.press('ArrowRight')
                after = int(separator.get_attribute('aria-valuenow'))
                assert after > before, f'splitter did not move: {before} -> {after}'
                page.keyboard.press('Home')
                assert int(separator.get_attribute('aria-valuenow')) == 5
                page.keyboard.press('Enter')
                assert int(separator.get_attribute('aria-valuenow')) == 50
                page.keyboard.press('ArrowLeft')
                assert int(separator.get_attribute('aria-valuenow')) < 50
                step(f'resized the splitter by keyboard ({before} -> {after}, Home 5, Enter 50)')

                # --- split down by the two-step chord --------------------------------------------------------------
                page.get_by_role('tab', name='Browser').click()
                page.keyboard.press('ControlOrMeta+k')
                page.keyboard.press('ControlOrMeta+Backslash')
                expect(page.locator(EDITOR_GROUPS)).to_have_count(3)
                step('split down with the Control+K Control+\\ chord')
                shot('three-groups')

                # --- drag a tab to a drop zone with the pointer ---------------------------------------------------------
                chat_tab = page.get_by_role('tab', name=TITLE)
                chat_box = chat_tab.bounding_box()
                target = page.get_by_role('group', name='Editor group 2')
                target_box = target.bounding_box()
                page.mouse.move(chat_box['x'] + chat_box['width'] / 2, chat_box['y'] + chat_box['height'] / 2)
                page.mouse.down()
                page.mouse.move(target_box['x'] + 30, target_box['y'] + target_box['height'] / 2 + 40, steps=12)
                hint = page.locator('.tab-drop')
                expect(hint).to_have_attribute('data-zone', 'left')
                shot('drag-over-drop-zone', remember=False)
                page.mouse.up()
                expect(page.locator('.tab-drop')).to_have_count(0)
                step('dragged a tab to the left drop zone of another group')

                # --- maximize and restore -----------------------------------------------------------------------------------
                count = groups(page)
                page.get_by_role('tab', name='Editor').click()
                page.keyboard.press('ControlOrMeta+k')
                page.keyboard.press('z')
                expect(page.locator(EDITOR_GROUPS)).to_have_count(1)
                shot('maximized')
                page.keyboard.press('Escape')
                expect(page.locator(EDITOR_GROUPS)).to_have_count(count)
                step('maximized a group with Control+K Z and restored with Escape')

                # --- the bottom panel ------------------------------------------------------------------------------------------
                page.keyboard.press('ControlOrMeta+j')
                expect(page.get_by_role('group', name='Panel')).to_be_visible()
                expect(page.get_by_role('group', name='Panel').get_by_role('tab', name='Terminal')).to_be_visible()
                expect(page.get_by_role('separator', name='Resize panel')).to_be_visible()
                panel_before = int(page.get_by_role('separator', name='Resize panel').get_attribute('aria-valuenow'))
                page.get_by_role('separator', name='Resize panel').focus()
                page.keyboard.press('ArrowUp')
                assert int(page.get_by_role('separator', name='Resize panel').get_attribute('aria-valuenow')) > panel_before
                step('opened the bottom panel with Control+J and resized it by keyboard')
                shot('panel-with-terminal')

                # --- reload: the layout persists ---------------------------------------------------------------------------------
                page.wait_for_timeout(400)
                saved = stored_layout(page)
                assert saved and saved['version'] == 2 and saved['current']['panel']['visible'] is True
                expected_groups = groups(page)
                page.reload()
                page.get_by_role('tablist', name='Editor group 1 tabs').wait_for(timeout=60000)
                expect(page.locator(EDITOR_GROUPS)).to_have_count(expected_groups)
                expect(page.get_by_role('group', name='Panel')).to_be_visible()
                assert editor_group_count(stored_layout(page)) == editor_group_count(saved)
                step(f'reloaded: {expected_groups} groups and the panel came back')

                axe(page, f'{scheme}-1280-layout', report, axe_source)
                no_overflow(page, f'{scheme}-1280', report)
                context.close()

                # --- the 375px projection: every desktop state, folded into one tab stack ----------------------------------
                phone = browser.new_context(viewport={'width': 375, 'height': 812}, color_scheme=scheme, reduced_motion='reduce', has_touch=False)
                small = phone.new_page()
                small.on('pageerror', lambda error: report['errors'].append(str(error)))
                small.goto(f'{base}/?session={session["id"]}&shell=ide')
                small.get_by_label('Username', exact=True).fill(OWNER)
                small.get_by_label('Password', exact=True).fill(PASSWORD)
                small.get_by_role('button', name='Continue with password').click()
                small.locator('.tab-list').first.wait_for(timeout=60000)
                small.wait_for_timeout(600)  # let the first layout finish saving before it is replaced

                def seed(book: dict) -> None:
                    small.evaluate("(book) => { const key = Object.keys(localStorage).find(item => item.startsWith('termx-layout-v2:')) || 'termx-layout-v2:x'; localStorage.setItem(key, JSON.stringify(book)) }", book)
                    small.reload()
                    small.locator('.tab-list').first.wait_for(timeout=60000)
                    settle(small)

                for label, book in books.items():
                    seed(book)
                    assert small.locator('.tab-list').count() == 1, f'{label}: one stack on a phone'
                    assert small.get_by_role('separator', name='Resize groups').count() == 0
                    small.screenshot(path=str(out / f'{label}-{scheme}-375.png'))
                    no_overflow(small, f'{label}-{scheme}-375', report)
                    if label == 'panel-with-terminal':
                        axe(small, f'{scheme}-375-{label}', report, axe_source)

                seed(saved)
                small.get_by_role('button', name='Tab actions').click()
                items = small.get_by_role('menuitem').all_inner_texts()
                assert 'Split right' not in items and 'Split down' not in items, items
                small.keyboard.press('Escape')
                tabs_on_phone = small.get_by_role('tab').count()
                small.get_by_role('tab').nth(min(1, tabs_on_phone - 1)).click()
                settle(small)
                after_phone = stored_layout(small)
                assert editor_group_count(after_phone) == editor_group_count(saved), f'a phone session must not change the tree: {json.dumps(saved["current"]["root"])} -> {json.dumps(after_phone["current"]["root"])}'
                axe(small, f'{scheme}-375-layout', report, axe_source)
                step(f'375px: {len(books)} desktop layouts shown as one tab stack of {tabs_on_phone} tabs, tree unchanged')
                phone.close()
            browser.close()

    (out / 'workspace_ide_shell_e2e.json').write_text(json.dumps(report, indent=2))
    problems = report['errors'] + report['overflow'] + report['axe_violations']
    if problems:
        print(json.dumps(report, indent=2))
        raise SystemExit('IDE shell e2e found problems: ' + '; '.join(problems[:6]))
    print(json.dumps({'steps': report['steps'], 'errors': 0, 'overflow': 0, 'axe_violations': 0}, indent=2))


if __name__ == '__main__':
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    run(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
