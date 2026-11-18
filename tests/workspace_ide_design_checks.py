"""The behavioural checks of tests/workspace_ide_design_e2e.py: one function per area, each taking a Case."""
from __future__ import annotations

import io
import re
from time import monotonic, sleep

from playwright.sync_api import Page, expect

ANSWER = '''## Local review fixture

This isolated conversation checks that the work stays easy to read. The assistant uses ordinary prose, while the request above stays visually distinct.

- Keep the next action close to the draft.
- Open activity only when the details are useful.

This is synthetic QA content. No model generated this answer and no external service was called.
'''
OWNER = 'ide-design-owner'
PASSWORD = 'ide-design-fixture-password-123'
SHELL_TEXT = 'Unsent synthetic draft: keep this text.'


def wait(check, page: Page | None = None, seconds: float = 20) -> None:
    end = monotonic() + seconds
    while not check():
        if monotonic() > end:
            raise AssertionError('Timed out waiting for fixture state')
        if page:
            page.wait_for_timeout(50)
        else:
            sleep(.05)


class Case:
    """One browser page at one theme and size, with the helpers every check shares."""

    def __init__(self, page: Page, base: str, report: dict, state, owner, sessions: dict, project: dict, axe_source: str, theme: str, width: int, height: int, out: Path):
        self.page, self.base, self.report, self.state, self.owner = page, base, report, state, owner
        self.sessions, self.project, self.axe_source, self.theme, self.width, self.height, self.out = sessions, project, axe_source, theme, width, height, out
        self.phone = width < 768

    def note(self, text: str) -> None:
        self.report['checks'].append(f'{self.theme} {self.width}x{self.height}: {text}')

    def open(self, label: str, signed_in: bool = True) -> None:
        page = self.page
        page.goto(f"{self.base}/?session={self.sessions[label]['id']}")
        if self.phone and signed_in:
            # A phone shows the stored active tab (a file opened earlier, say); bring the conversation to the front.
            page.get_by_role('banner', name='Window').wait_for(timeout=60000)
            if not page.get_by_role('textbox', name='Message', exact=True).is_visible():
                self.chat_tab()

    def sign_in(self, label: str) -> None:
        self.open(label, signed_in=False)
        self.page.get_by_label('Username', exact=True).fill(OWNER)
        self.page.get_by_label('Password', exact=True).fill(PASSWORD)
        self.page.get_by_role('button', name='Continue with password', exact=True).click()
        expect(self.page.get_by_role('textbox', name='Message', exact=True)).to_be_visible(timeout=60000)

    def chat_tab(self) -> None:
        """Bring the conversation back to the front: its tab is the first one (the switcher's first entry on a phone)."""
        page = self.page
        if self.phone:
            page.get_by_role('banner', name='Window').get_by_role('button', name=re.compile('Switch tab')).click()
            page.get_by_role('menuitem').first.click()
        else:
            page.get_by_role('tab').first.click()

    def persisted_draft(self, label: str, text: str) -> None:
        wait(lambda: self.state.workspace.session(self.owner, self.sessions[label]['id'], turns=False)['draft_text'] == text, page=self.page)

    def audit(self, label: str) -> None:
        """No horizontal overflow, nothing outside the viewport that matters, and axe clean (WCAG 2.1 AA)."""
        page = self.page
        overflow = page.evaluate('document.documentElement.scrollWidth > innerWidth')
        assert not overflow, f'{label}: horizontal overflow at {self.width}px'
        page.add_script_tag(content=self.axe_source) if not page.evaluate("typeof window.axe !== 'undefined'") else None
        found = page.evaluate("async () => (await axe.run(document, {runOnly: {type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21aa']}})).violations.map(v => v.id + ': ' + v.nodes.map(n => n.target.join(' ')).join(' | '))")
        self.report['axe'][f'{self.theme}-{self.width}x{self.height}-{label}'] = found
        assert not found, f'{label}: axe {found}'
        page.screenshot(path=str(self.out / f'{label}-{self.theme}-{self.width}x{self.height}.png'))

    def tools(self, view: str) -> None:
        """Show a side bar view (desktop) or its drawer / More entry (phone)."""
        page = self.page
        if not self.phone:
            bar = page.get_by_role('toolbar', name='Activity bar')
            if not page.get_by_role('complementary', name=view).is_visible():
                bar.get_by_role('button', name=view, exact=True).click()
            return
        nav = page.get_by_role('navigation', name='Primary')
        if view == 'Agents':
            page.get_by_role('banner', name='Window').get_by_role('button', name='Open side bar').click()
        else:
            nav.get_by_role('button', name='More').click()
            page.get_by_role('menuitem', name=view, exact=True).click()


def empty_state(case: Case) -> None:
    """No conversation yet: one sentence, the composer, a gated Send, and exactly one primary action in the view."""
    page = case.page
    case.sign_in('welcome')
    expect(page.get_by_role('heading', name='What can we work on?')).to_be_visible()
    message = page.get_by_role('textbox', name='Message', exact=True)
    expect(page.get_by_role('button', name='Send message', exact=True)).to_be_disabled()
    expect(page.get_by_text('Connect this chat', exact=True)).to_be_visible()
    case.tools('Agents')
    side = page.get_by_role('dialog', name='Agents') if case.phone else page.get_by_role('complementary', name='Agents')
    expect(side.get_by_role('button', name='New chat')).to_be_visible()
    assert side.locator('.button-primary:visible').count() == 1, 'New chat is the only primary button in the Agents view'
    if case.phone:
        page.keyboard.press('Escape')
        expect(page.get_by_role('dialog', name='Agents')).to_have_count(0)
    message.focus()
    case.audit('welcome')
    case.note('empty state: heading, gated Send, connection guidance, one primary action')


def composer(case: Case) -> None:
    """The composer grows, gates Send, and the draft and an attachment survive views and tabs."""
    page = case.page
    case.open('configured')
    message = page.get_by_role('textbox', name='Message', exact=True)
    expect(message).to_be_enabled()
    send = page.get_by_role('button', name='Send message', exact=True)
    one_line = message.evaluate('e => e.getBoundingClientRect().height')
    message.fill('line one\nline two\nline three\nline four')
    wait(lambda: message.evaluate('e => e.getBoundingClientRect().height') > one_line, page=page, seconds=5)
    box = page.locator('.composer').bounding_box()
    assert box and box['x'] >= -1 and box['x'] + box['width'] <= case.width + 1 and box['y'] + box['height'] <= case.height + 1, box
    message.fill('')
    expect(send).to_be_disabled()
    message.fill(SHELL_TEXT)
    expect(send).to_be_enabled()
    case.persisted_draft('configured', SHELL_TEXT)
    pixels = io.BytesIO()
    from PIL import Image

    Image.new('RGB', (48, 32), (176, 80, 45)).save(pixels, 'PNG')
    page.locator('.composer input[type=file]').set_input_files({'name': 'design-qa-local.png', 'mimeType': 'image/png', 'buffer': pixels.getvalue()})
    expect(page.get_by_role('button', name='Remove design-qa-local.png', exact=True)).to_be_visible()
    # Away to Explorer, to a file tab and back: nothing is lost, and the file is never written.
    case.tools('Explorer' if not case.phone else 'Files') if not case.phone else page.get_by_role('navigation', name='Primary').get_by_role('button', name='Files').click()
    explorer = page.get_by_role('dialog', name='Explorer') if case.phone else page.get_by_role('tree', name='Files')
    explorer.get_by_role('treeitem', name=re.compile('design-review.txt')).click()
    if case.phone:
        expect(page.get_by_role('banner', name='Window').get_by_role('heading')).to_contain_text('design-review.txt')
        case.chat_tab()
    else:
        expect(page.get_by_role('tab', name=re.compile('design-review.txt'))).to_have_attribute('aria-selected', 'true')
        case.chat_tab()
    expect(message).to_have_value(SHELL_TEXT)
    expect(page.get_by_role('button', name='Remove design-qa-local.png', exact=True)).to_be_visible()
    expect(send).to_be_enabled()
    case.note('composer grows, gates Send, keeps the draft and attachment across a file tab')


def keyboard_paths(case: Case) -> None:
    """Command center, side bar, dialogs, roving focus and focus-visible, all by keyboard."""
    page = case.page
    message = page.get_by_role('textbox', name='Message', exact=True)
    options = page.get_by_role('button', name='Session options', exact=True)
    options.focus()
    page.keyboard.press('Enter')
    dialog = page.get_by_role('dialog', name='Session settings', exact=True)
    expect(dialog).to_be_visible()
    for key in ('Tab', 'Shift+Tab', 'Tab', 'Tab', 'Shift+Tab'):
        page.keyboard.press(key)
        assert dialog.evaluate('e => e.contains(document.activeElement)'), 'dialog focus escaped'
    box = dialog.bounding_box()
    assert box and box['x'] >= -1 and box['x'] + box['width'] <= case.width + 1 and box['y'] + box['height'] <= case.height + 1, box
    page.keyboard.press('Escape')
    expect(dialog).not_to_be_visible()
    expect(options).to_be_focused()
    expect(message).to_have_value(SHELL_TEXT)
    model = page.get_by_role('button', name='Model and effort', exact=True)
    model.focus()
    page.keyboard.press('Enter')
    model_dialog = page.get_by_role('dialog', name='Model and effort', exact=True)
    expect(model_dialog).to_be_visible()
    page.keyboard.press('Escape')
    expect(model_dialog).not_to_be_visible()
    expect(model).to_be_focused()
    page.keyboard.press('Shift')
    ring = options.evaluate('e => { e.focus(); const s = getComputedStyle(e); return [s.outlineStyle, s.outlineWidth] }')
    assert ring[0] != 'none' and ring[1] != '0px', f'focus ring missing: {ring}'
    if case.phone:
        case.note('keyboard: dialogs trap focus, Escape returns it, the focus ring shows')
        return
    page.keyboard.press('ControlOrMeta+p')
    center = page.get_by_role('dialog', name='Search files and commands')
    expect(center).to_be_visible()
    center.get_by_role('combobox').fill('design-review')
    expect(center.get_by_role('option', name=re.compile('design-review.txt'))).to_be_visible(timeout=20000)
    page.keyboard.press('Escape')
    expect(center).to_have_count(0)
    bar = page.get_by_role('toolbar', name='Activity bar')
    first = bar.get_by_role('button').first
    first.focus()
    page.keyboard.press('ArrowDown')
    assert page.evaluate('document.activeElement.getAttribute("aria-label")') != first.get_attribute('aria-label'), 'arrow keys do not move through the activity bar'
    shown = page.get_by_role('complementary').first.is_visible()
    page.keyboard.press('ControlOrMeta+b')
    expect(page.get_by_role('complementary', name='Agents')).to_be_visible() if not shown else expect(page.get_by_role('complementary', name='Agents')).to_have_count(0)
    page.keyboard.press('ControlOrMeta+b')
    message.focus()
    case.note('keyboard: Session options and Model dialogs, command center, roving activity bar, side bar toggle')


def conversation(case: Case) -> None:
    """Assistant prose on the page, a distinct user message, a 720px column, collapsed activity opened by keyboard."""
    page = case.page
    case.open('conversation')
    expect(page.get_by_role('heading', name='Local review fixture')).to_be_visible()
    page.get_by_role('log').evaluate('e => e.scrollTop = 0')
    reply = page.locator('.message-assistant .message-content').first
    user = page.locator('.message-user .message-content').first
    clear = 'rgba(0, 0, 0, 0)'
    assert reply.evaluate('e => getComputedStyle(e).backgroundColor') == clear, 'assistant output has a bubble'
    assert user.evaluate('e => getComputedStyle(e).backgroundColor') != clear, 'the user message is not distinct'
    width = page.locator('.message-assistant .message-response').first.evaluate('e => e.getBoundingClientRect().width')
    assert width <= 721, f'reading column is {width}px'
    activity = page.get_by_role('button', name='View activity', exact=False)
    expect(activity).to_have_attribute('data-state', 'closed')
    activity.focus()
    page.keyboard.press('Enter')
    tool = page.get_by_role('button', name=re.compile('Read file.*design-review.txt'))
    expect(tool).to_have_attribute('data-state', 'closed')
    tool.focus()
    page.keyboard.press('Enter')
    expect(page.get_by_label('Read file · design-review.txt result')).to_be_visible()
    assert 'tool.finished' not in page.get_by_role('log').inner_text(), 'raw event data is shown'
    activity.click()
    case.audit('conversation')
    case.note('conversation: no assistant bubble, 720px column, activity and tool detail by keyboard')


def states(case: Case) -> None:
    """An inline error with its recovery, a held approval, the streaming caret in both motion settings, capture status."""
    page = case.page
    case.open('error')
    expect(page.get_by_role('alert').filter(has_text='Synthetic offline error')).to_be_visible()
    assert page.locator('.error-toast:visible').count() == 0, 'a full-page error banner is showing'
    case.audit('error')
    case.open('approval')
    review = page.get_by_role('button', name='Review action', exact=True)
    expect(review).to_be_visible()
    review.focus()
    page.keyboard.press('Enter')
    review_dialog = page.get_by_role('dialog', name='Review action', exact=True)
    expect(review_dialog.get_by_role('button', name='Allow once', exact=True)).to_be_enabled()
    page.keyboard.press('Escape')
    expect(review_dialog).not_to_be_visible()
    case.open('streaming')
    stream = page.locator('.message-response.is-streaming')
    expect(stream).to_contain_text('This is a synthetic streaming fixture.')
    caret = lambda: stream.evaluate("e => getComputedStyle(e.lastElementChild, '::after').animationName")
    assert caret() == 'none', 'the caret animates under reduced motion'
    page.emulate_media(reduced_motion='no-preference')
    assert caret() == 'response-caret', 'the caret does not animate in normal motion'
    page.emulate_media(reduced_motion='reduce')
    case.note('states: inline error, held approval review, streaming caret (reduced and normal motion)')


def capture(case: Case) -> None:
    """Capture status stays visible and reachable by keyboard in the status bar, and is never mutated by the test."""
    page = case.page
    fixture = {'mode': 'control'}

    def status(route):
        assert route.request.method == 'GET', 'the capture probe must never mutate the host'
        row = {'id': 'synthetic-capture-only', 'kind': 'computer', 'state': fixture['mode'], 'stop_allowed': True}
        route.fulfill(json={'captures': [row]})

    page.context.route('**/api/desktop/recording/status', status)
    case.open('conversation')
    expect(page.get_by_role('textbox', name='Message', exact=True)).to_be_visible()
    page.evaluate("window.dispatchEvent(new Event('termx-capture-changed'))")
    group = page.get_by_role('group', name='Active capture controls', exact=True)
    expect(group.get_by_text('1 computer control')).to_be_visible()
    stop = group.get_by_role('button', name='Stop capture', exact=True)
    stop.focus()
    expect(stop).to_be_focused()
    assert stop.evaluate('e => { const r = e.getBoundingClientRect(); return e.contains(document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2)) }'), 'Stop capture is covered'
    page.context.unroute('**/api/desktop/recording/status', status)
    case.note('capture status visible, focusable and uncovered')


def views_and_tabs(case: Case) -> None:
    """Studio and Data are views with tabs, Projects is a tab; a Studio draft and the chat draft survive moving between them."""
    page = case.page
    case.open('welcome')
    message = page.get_by_role('textbox', name='Message', exact=True)
    expect(message).to_be_visible()
    message.fill(SHELL_TEXT)
    case.tools('Studio')
    if case.phone:
        # On a phone the view opens in the drawer over its tab; Escape shows the tab.
        page.keyboard.press('Escape')
    studio = page.get_by_role('region', name='Media studio', exact=True)
    expect(studio).to_be_visible(timeout=30000)
    expect(studio.get_by_role('button', name='Generate image', exact=True)).to_be_disabled()
    prompt = studio.get_by_label('Prompt', exact=True)
    prompt.fill('Unsent synthetic Studio draft; never generate.')
    case.audit('studio')
    case.tools('Data')
    if case.phone:
        page.keyboard.press('Escape')
    else:
        expect(page.get_by_role('tab', name='Data')).to_have_attribute('aria-selected', 'true')
    case.audit('data')
    if case.phone:
        page.get_by_role('banner', name='Window').get_by_role('button', name=re.compile('Switch tab')).click()
        page.get_by_role('menuitem', name='Studio').click()
    else:
        page.get_by_role('tab', name='Studio').click()
    expect(prompt).to_have_value('Unsent synthetic Studio draft; never generate.')
    case.chat_tab()
    expect(message).to_have_value(SHELL_TEXT)
    case.persisted_draft('welcome', SHELL_TEXT)
    message.fill('')
    case.persisted_draft('welcome', '')
    # Projects is a tab: search, clear, expand, and open a conversation.
    if case.phone:
        page.get_by_role('navigation', name='Primary').get_by_role('button', name='More').click()
        page.get_by_role('menuitem', name='Projects', exact=True).click()
    else:
        page.get_by_role('toolbar', name='Activity bar').get_by_role('button', name='Projects', exact=True).click()
        expect(page.get_by_role('tab', name='Projects')).to_have_attribute('aria-selected', 'true')
    panel = page.get_by_role('region', name='Projects workspace', exact=True)
    search = panel.get_by_role('searchbox', name='Search projects', exact=True)
    expect(search).to_be_visible()
    search.fill('explicit-unmatched-synthetic-project')
    toggle = panel.get_by_role('button', name=case.project['name'], exact=True)
    expect(toggle).to_have_count(0)
    panel.get_by_role('button', name='Clear search', exact=True).click()
    expect(search).to_have_value('')
    toggle.focus()
    page.keyboard.press('Enter')
    expect(toggle).to_have_attribute('aria-expanded', 'true')
    child = panel.get_by_role('list', name=case.project['name'] + ' conversations', exact=True).get_by_role('button', name='Open Design QA fixture · conversation', exact=True)
    child.focus()
    page.keyboard.press('Enter')
    expect(page.get_by_role('heading', name='Local review fixture', exact=True)).to_be_visible()
    case.note('Studio and Data are views with tabs, drafts survive moving between tabs, Projects searches and opens a chat')


def responsive(case: Case) -> None:
    """Narrow and short windows keep the composer inside and the page free of horizontal scroll."""
    page = case.page
    case.open('conversation')
    message = page.get_by_role('textbox', name='Message', exact=True)
    expect(message).to_be_visible()
    for width, height in ((320, 812), (375, 420)):
        page.set_viewport_size({'width': width, 'height': height})
        message.fill('Synthetic narrow layout fixture; never send.')
        # The layout follows the new size on the next frames: wait for it to settle before measuring.
        page.wait_for_function('([w]) => document.querySelector(".composer").getBoundingClientRect().right <= w + 1', arg=[width], timeout=5000)
        box = page.locator('.composer').bounding_box()
        assert box and box['x'] >= -1 and box['x'] + box['width'] <= width + 1, box
        controls = page.locator('.composer-controls button:visible').evaluate_all('rows => rows.map(e => e.getBoundingClientRect().toJSON())')
        assert all(item['x'] >= -1 and item['right'] <= width + 1 for item in controls), controls
        assert not page.evaluate('document.documentElement.scrollWidth > innerWidth'), f'overflow at {width}x{height}'
    message.fill('')
    case.persisted_draft('conversation', '')
    page.set_viewport_size({'width': case.width, 'height': case.height})
    case.note('narrow (320) and short (375x420) windows: composer inside, no overflow')
