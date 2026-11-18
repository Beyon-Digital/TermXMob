"""Capture U1 (editor and terminal polish) screenshots of the built workspace UI against an isolated host.

Run from the repository root after `pnpm --dir desktop/workspace build`:
    uv run --no-sync python docs/evidence/ide-shell/u1/capture_u1.py docs/evidence/ide-shell/u1

For each of 375 x 812 and 1280 x 900, light and dark (the system colour scheme drives the theme):
  editor-ts      a TypeScript file with syntax colours, the find and replace panel open on a term, and one block folded
  editor-md      a markdown file
  terminal       two terminal sessions as tabs, each printing ANSI colour and a link
Nothing outside a temporary directory is touched; no agents or providers run.
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
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / 'tests'))

VIEWPORTS = {'mobile': (375, 812), 'desktop': (1280, 900)}
TS = '''export interface User {
  id: number;
  name: string;
}

// Greets a user by name; returns a plain string.
export function greet(user: User): string {
  if (!user.name) {
    return "Hello, stranger";
  }
  return `Hello, ${user.name}`;
}

export const users: User[] = [
  {id: 1, name: "Ada"},
  {id: 2, name: "Grace"},
];

export function roster(): string[] {
  const names = users.map((user) => greet(user));
  return names.sort();
}
'''
MD = '''# Evidence notes

The editor now picks a grammar from the **file extension**.

- Markdown headings, `inline code` and lists are coloured from tokens.
- A fold gutter and breadcrumbs sit around the text.

```ts
const answer = 42;
```

> Quotes stay quiet and use the muted text colour.
'''
ANSI_ONE = r"printf '\033[31mred\033[0m \033[32mgreen\033[0m \033[33myellow\033[0m \033[34mblue\033[0m \033[35mmagenta\033[0m \033[36mcyan\033[0m \033[90mgrey\033[0m \033[91mbright-red\033[0m\n'; echo ready-$((6*7))"
ANSI_TWO = r"printf '\033[1mbold\033[0m \033[4munderline\033[0m \033[92mbright-green\033[0m \033[93mbright-yellow\033[0m \033[94mbright-blue\033[0m \033[95mbright-magenta\033[0m \033[96mbright-cyan\033[0m https://example.com/docs\n'; echo ready-$((7*7))"


def main(destination: str) -> None:
    out = Path(destination)
    out.mkdir(parents=True, exist_ok=True)
    report = {'states': [], 'errors': [], 'overflow': [], 'console': []}
    with tempfile.TemporaryDirectory(prefix='termx-u1-') as scratch:
        root = Path(scratch)
        os.environ['TERMX_CONFIG_DIR'] = str(root / 'config')
        os.environ['TERMX_AGENTS_DIR'] = str(root / 'agents')
        os.environ['TERMX_ENGINE_STARTUP_REFRESH'] = '0'
        from termx.app import AppState, create_app
        from termx.agent.secrets import CredentialStore
        project_dir = root / 'project'
        (project_dir / 'src').mkdir(parents=True)
        (project_dir / 'docs').mkdir()
        (project_dir / 'src' / 'greet.ts').write_text(TS)
        (project_dir / 'docs' / 'NOTES.md').write_text(MD)
        (project_dir / 'main.py').write_text('def greet(name):\n    return f"Hello {name}"\n')
        state = AppState(passcode=None, credentials=CredentialStore(memory={}))
        owner = state.identity.setup_owner('evidence-owner', 'evidence-fixture-password-123')
        project = state.projects.register(str(project_dir), name='Evidence fixture')
        from workspace_ui_snapshot import snapshot_ui
        app = create_app(state, web_dir=snapshot_ui(root, ROOT / 'desktop/workspace/dist'))
        session = state.workspace.create_session(owner, title='Evidence conversation', project_id=project['id'], cwd=str(project_dir))
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
                for name, (width, height) in VIEWPORTS.items():
                    state.sessions.kill_all()  # every state starts with no terminals, so the strip shows only this run's two
                    context = browser.new_context(viewport={'width': width, 'height': height}, color_scheme=scheme, reduced_motion='reduce')
                    page = context.new_page()
                    page.on('pageerror', lambda error: report['errors'].append(str(error)))
                    page.on('console', lambda message: report['console'].append(message.text) if message.type == 'error' else None)

                    def shot(label: str, **extra) -> None:
                        page.wait_for_timeout(300)
                        page.screenshot(path=str(out / f'{label}-{scheme}-{width}.png'))
                        info = page.evaluate("""()=>{const r=document.documentElement,s=getComputedStyle(r),cm=document.querySelector('.cm-editor'),term=document.querySelector('.terminal-pane:not([hidden]) .xterm');return {theme:r.dataset.theme,overflow:r.scrollWidth>r.clientWidth+1,
                          editorFont:cm?getComputedStyle(cm.querySelector('.cm-content')).fontFamily.slice(0,30):null,editorSize:cm?getComputedStyle(cm.querySelector('.cm-content')).fontSize+'/'+getComputedStyle(cm.querySelector('.cm-content')).lineHeight:null,
                          editorBg:cm?getComputedStyle(cm).backgroundColor:null,terminalFont:term?getComputedStyle(term).fontSize:null,terminalBg:term?getComputedStyle(term.querySelector('.xterm-viewport')).backgroundColor:null}}""")
                        report['states'].append({'label': label, 'scheme': scheme, 'viewport': [width, height], **info, **extra})
                        if info['overflow']:
                            report['overflow'].append(f'{label}-{scheme}-{width}')

                    page.goto(f'{base}/?session=' + session['id'])
                    page.get_by_label('Username', exact=True).fill('evidence-owner')
                    page.get_by_label('Password', exact=True).fill('evidence-fixture-password-123')
                    page.get_by_role('button', name='Continue with password').click()
                    page.wait_for_function("()=>!!document.querySelector('textarea[aria-label=\"Message\"]')", timeout=60000)
                    page.keyboard.press('Control+2')
                    page.wait_for_timeout(500)

                    def clear_toasts() -> None:
                        # The isolated host has no language server on PATH; its notice is unrelated to this slice.
                        for dismiss in page.get_by_role('button', name='Dismiss', exact=True).all():
                            if dismiss.is_visible():
                                dismiss.click()

                    # TypeScript with find and a folded block.
                    page.get_by_role('button', name='src', exact=True).click()
                    page.get_by_role('button', name='greet.ts', exact=True).click()
                    page.wait_for_function("()=>!!document.querySelector('.cm-foldGutter span[title=\"Fold line\"]')", timeout=30000)
                    folded = page.locator('.cm-foldGutter span[title="Fold line"]')
                    folded.nth(1).click()  # the greet() function
                    editor = page.locator('.cm-content[contenteditable=true]').first
                    editor.click()
                    page.keyboard.press('ControlOrMeta+F')
                    page.locator('.cm-search input[name=search]').wait_for()
                    page.locator('.cm-search input[name=search]').fill('greet')
                    page.keyboard.press('Enter')
                    page.wait_for_function("()=>document.querySelectorAll('.cm-searchMatch').length>0")
                    clear_toasts()
                    if width < 768:
                        page.get_by_role('button', name='Hide explorer').click()
                    shot('editor-ts', fold_placeholders=page.locator('.cm-foldPlaceholder').count(), matches=page.locator('.cm-searchMatch').count())
                    page.keyboard.press('Escape')

                    # Markdown.
                    if width < 768:
                        page.get_by_role('button', name='Show explorer').click()
                    page.get_by_role('button', name='docs', exact=True).click()
                    page.get_by_role('button', name='NOTES.md', exact=True).click()
                    if width < 768:
                        page.get_by_role('button', name='Hide explorer').click()
                    page.wait_for_function("()=>document.querySelector('.editor-breadcrumbs')?.textContent.includes('NOTES.md')")
                    page.wait_for_timeout(500)
                    clear_toasts()
                    shot('editor-md')

                    # Terminal: two sessions with ANSI colour.
                    clear_toasts()
                    new_terminal = page.get_by_role('button', name='New terminal', exact=True)
                    new_terminal.click()
                    page.wait_for_function("()=>!!document.querySelector('.terminal-region')?.dataset.connectedSession", timeout=30000)
                    page.wait_for_function("()=>document.querySelector('.terminal-pane:not([hidden]) .xterm-rows')?.innerText.trim().length>0", timeout=30000)
                    page.locator('.terminal-pane:not([hidden]) .xterm-helper-textarea').focus()
                    page.keyboard.type(ANSI_ONE, delay=2)
                    page.keyboard.press('Enter')
                    page.wait_for_function("()=>document.querySelector('.terminal-pane:not([hidden]) .xterm-rows')?.innerText.includes('ready-42')", timeout=15000)
                    first = page.locator('.terminal-region').get_attribute('data-connected-session')
                    new_terminal.click()
                    page.wait_for_function("(old)=>{const id=document.querySelector('.terminal-region')?.dataset.connectedSession;return !!id&&id!==old}", arg=first, timeout=30000)
                    page.wait_for_function("()=>document.querySelector('.terminal-pane:not([hidden]) .xterm-rows')?.innerText.trim().length>0", timeout=30000)
                    page.locator('.terminal-pane:not([hidden]) .xterm-helper-textarea').focus()
                    page.keyboard.type(ANSI_TWO, delay=2)
                    page.keyboard.press('Enter')
                    page.wait_for_function("()=>document.querySelector('.terminal-pane:not([hidden]) .xterm-rows')?.innerText.includes('ready-49')", timeout=15000)
                    page.keyboard.press('ControlOrMeta+F')
                    page.get_by_role('textbox', name='Find in terminal').fill('bright')
                    page.wait_for_timeout(300)
                    shot('terminal', sessions=page.locator('.terminal-tabs [role=tab]').count(), mounted_xterms=page.locator('.xterm').count())
                    report['states'].append({'label': 'terminal-tab-style', 'scheme': scheme, 'viewport': [width, height], 'style': page.evaluate("""()=>{const t=document.querySelector('.terminal-tabs [role=tab][data-state=active]');if(!t)return null;const c=getComputedStyle(t),b=document.querySelector('.terminal-toolbar');return {state:t.dataset.state,height:c.height,padding:c.padding,borderBottom:c.borderBottom,background:c.backgroundColor,toolbarHeight:getComputedStyle(b).height,closeOpacity:getComputedStyle(t.querySelector('.terminal-tab-close')).opacity,layers:[...document.styleSheets].length}}""")})
                    # Switching tabs keeps both emulators mounted.
                    page.locator('.terminal-tabs [role=tab]').first.click()
                    page.wait_for_timeout(300)
                    report['states'].append({'label': 'terminal-switch', 'scheme': scheme, 'viewport': [width, height], 'mounted_xterms': page.locator('.xterm').count(), 'first_tab_text': page.locator('.terminal-pane:not([hidden]) .xterm-rows').inner_text()[:80]})
                    context.close()
            browser.close()
        server.should_exit = True
    (out / 'report.json').write_text(json.dumps(report, indent=2))
    print(json.dumps({'states': len(report['states']), 'errors': report['errors'], 'overflow': report['overflow'], 'console_errors': len(report['console'])}))


if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else 'docs/evidence/ide-shell/u1')
