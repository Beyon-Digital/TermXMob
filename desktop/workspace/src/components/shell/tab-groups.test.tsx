import {act, cleanup, fireEvent, render, screen, within} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {useEffect, useReducer, useRef, type ReactNode} from 'react';
import {afterEach, describe, expect, it, vi} from 'vitest';
import {
  defaultLayout,
  editorGroups,
  findTab,
  newGroup,
  newTab,
  type Group,
  type Layout,
  type Split,
  type Tab,
  type TabKind,
} from '../../lib/layout/model';
import {createState, reduceLayout, type LayoutAction, type LayoutState} from '../../lib/layout/reduce';
import {expectNoAxeViolations} from '../../test-axe';
import TabGroups from './TabGroups';
import {KIND_LABEL} from './tab-meta';

afterEach(cleanup);

const mounts: Record<string, number> = {};

/** Stands in for xterm or CodeMirror: it counts how often it is mounted and keeps a value typed into it. */
function Probe({tab, visible}: {tab: Tab; visible: boolean}) {
  const created = useRef(false);
  useEffect(() => {
    if (!created.current) {
      created.current = true;
      mounts[tab.kind] = (mounts[tab.kind] ?? 0) + 1;
    }
  }, [tab.kind]);
  return (
    <div>
      <textarea aria-label={KIND_LABEL[tab.kind] + ' input'} defaultValue="" />
      <span data-visible={visible}>{KIND_LABEL[tab.kind]} content</span>
    </div>
  );
}

const titleOf = (tab: Tab) => KIND_LABEL[tab.kind];

function stateFor(...rows: Tab[][]): LayoutState {
  const base = defaultLayout();
  const groups = rows.map(tabs => newGroup(tabs));
  const root = groups.length === 1 ? groups[0] : ({id: 'row', dir: 'row', sizes: groups.map(() => 1 / groups.length), children: groups} as Split);
  return createState({version: 2, current: {...base, root, focus: groups[0].id}, named: []});
}

type Harness = {current: LayoutState};

function Host({initial, probe, size = {width: 1000, height: 600}, phone, extra}: {initial: LayoutState; probe: Harness; size?: {width: number; height: number}; phone?: boolean; extra?: Partial<React.ComponentProps<typeof TabGroups>>}) {
  const [state, dispatch] = useReducer((value: LayoutState, action: LayoutAction) => reduceLayout(value, action), initial);
  probe.current = state;
  return (
    <TabGroups
      layout={state.book.current}
      dispatch={dispatch}
      titleOf={titleOf}
      render={(tab, {visible}) => <Probe tab={tab} visible={visible} />}
      size={size}
      phone={phone}
      label="Workspace"
      {...extra}
    />
  );
}

function setup(initial: LayoutState, options: {phone?: boolean; extra?: Partial<React.ComponentProps<typeof TabGroups>>; size?: {width: number; height: number}} = {}) {
  const probe: Harness = {current: initial};
  const view = render(<Host initial={initial} probe={probe} {...options} />);
  return {probe, ...view};
}

const layoutOf = (probe: Harness): Layout => probe.current.book.current;
const group = (layout: Layout, index: number): Group => editorGroups(layout.root)[index];

describe('TabGroups: tab strip', () => {
  it('renders one tablist per group with automatic activation and a roving tab stop', async () => {
    const user = userEvent.setup();
    const {probe} = setup(stateFor([newTab('chat'), newTab('file'), newTab('browser')]));
    const list = screen.getByRole('tablist', {name: 'Editor group 1 tabs'});
    const tabs = within(list).getAllByRole('tab');
    expect(tabs.map(tab => tab.textContent)).toEqual(['Chat', 'Editor', 'Browser']);
    expect(tabs.map(tab => tab.getAttribute('aria-selected'))).toEqual(['true', 'false', 'false']);
    // Radix makes the list the one tab stop and hands focus to the selected tab, which then holds tabindex 0.
    await user.tab();
    expect(tabs[0]).toHaveFocus();
    expect(tabs.map(tab => tab.tabIndex)).toEqual([0, -1, -1]);
    await user.keyboard('{ArrowRight}');
    expect(screen.getByRole('tab', {name: 'Editor'})).toHaveAttribute('aria-selected', 'true');
    expect(group(layoutOf(probe), 0).active).toBe(group(layoutOf(probe), 0).tabs[1].id);
    await user.keyboard('{End}');
    expect(screen.getByRole('tab', {name: 'Browser'})).toHaveFocus();
    await user.keyboard('{Home}');
    expect(screen.getByRole('tab', {name: 'Chat'})).toHaveFocus();
    await user.keyboard('{ArrowLeft}');
    expect(screen.getByRole('tab', {name: 'Browser'})).toHaveFocus();
  });

  it('links each tab to its content panel', () => {
    setup(stateFor([newTab('chat'), newTab('file')]));
    const tab = screen.getByRole('tab', {name: 'Chat'});
    const panel = document.getElementById(tab.getAttribute('aria-controls')!)!;
    expect(panel).toHaveAttribute('role', 'tabpanel');
    expect(panel).toHaveAttribute('aria-labelledby', tab.id);
    expect(screen.getByRole('tabpanel', {name: 'Chat'})).toBe(panel);
  });

  it('hides and inerts the content of tabs that are not active, and never renders them before they are shown', async () => {
    const user = userEvent.setup();
    Object.keys(mounts).forEach(key => delete mounts[key]);
    setup(stateFor([newTab('chat'), newTab('file')]));
    expect(screen.queryByText('Editor content')).not.toBeInTheDocument();
    expect(mounts.file).toBeUndefined();
    await user.click(screen.getByRole('tab', {name: 'Editor'}));
    const editor = screen.getByText('Editor content').closest('[role=tabpanel]')!;
    const chat = screen.getByText('Chat content', {selector: 'span'}).closest('[role=tabpanel]')!;
    expect(editor).not.toHaveAttribute('hidden');
    expect(editor).not.toHaveAttribute('inert');
    expect(chat).toHaveAttribute('hidden');
    expect(chat).toHaveAttribute('inert');
    expect(mounts.file).toBe(1);
  });

  it('shows a dot and says so for a tab with unsaved changes', () => {
    const initial = stateFor([newTab('chat'), newTab('settings')]);
    const settings = (initial.book.current.root as Group).tabs[1];
    setup(initial, {extra: {dirty: new Set([settings.id])}});
    expect(screen.getByRole('tab', {name: /Settings\s*, unsaved changes/})).toBeInTheDocument();
    expect(screen.getByRole('tab', {name: 'Chat'})).toBeInTheDocument();
  });

  it('closes a tab with the close mark, the Delete key, middle click and the menu', async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    setup(stateFor([newTab('chat'), newTab('file'), newTab('browser'), newTab('computer')]), {extra: {onClose}});
    const tab = (name: string) => screen.getByRole('tab', {name});
    await user.click(tab('Editor').querySelector('.tab-close')!);
    expect(onClose).toHaveBeenLastCalledWith(expect.objectContaining({kind: 'file'}));
    tab('Browser').focus();
    await user.keyboard('{Delete}');
    expect(onClose).toHaveBeenLastCalledWith(expect.objectContaining({kind: 'browser'}));
    fireEvent(tab('Computer'), new MouseEvent('auxclick', {button: 1, bubbles: true, cancelable: true}));
    expect(onClose).toHaveBeenLastCalledWith(expect.objectContaining({kind: 'computer'}));
    await user.pointer({keys: '[MouseRight]', target: tab('Chat')});
    await user.click(await screen.findByRole('menuitem', {name: 'Close'}));
    expect(onClose).toHaveBeenLastCalledWith(expect.objectContaining({kind: 'chat'}));
    expect(onClose).toHaveBeenCalledTimes(4);
  });

  it('closes through the reducer by default', async () => {
    const user = userEvent.setup();
    const {probe} = setup(stateFor([newTab('chat'), newTab('file')]));
    await user.click(screen.getByRole('tab', {name: 'Editor'}).querySelector('.tab-close')!);
    expect(group(layoutOf(probe), 0).tabs.map(tab => tab.kind)).toEqual(['chat']);
  });

  it('opens a tab from the + menu, marking the ones that are open, and reveals an open one', async () => {
    const user = userEvent.setup();
    const {probe} = setup(stateFor([newTab('chat')]));
    await user.click(screen.getByRole('button', {name: 'New tab'}));
    const menu = await screen.findByRole('menu');
    expect(within(menu).getByRole('menuitemcheckbox', {name: 'Chat'})).toBeChecked();
    expect(within(menu).getByRole('menuitemcheckbox', {name: 'Browser'})).not.toBeChecked();
    await user.click(within(menu).getByRole('menuitemcheckbox', {name: 'Browser'}));
    expect(group(layoutOf(probe), 0).tabs.map(tab => tab.kind)).toEqual(['chat', 'browser']);
    await user.click(screen.getByRole('button', {name: 'New tab'}));
    await user.click(await screen.findByRole('menuitemcheckbox', {name: 'Chat'}));
    expect(group(layoutOf(probe), 0).tabs).toHaveLength(2);
    expect(screen.getByRole('tab', {name: 'Chat'})).toHaveAttribute('aria-selected', 'true');
  });
});

describe('TabGroups: menus (the keyboard route to moving tabs)', () => {
  it('offers Close, Close others, Split right, Split down, Move to new window and Reveal on the context menu', async () => {
    const user = userEvent.setup();
    const onDetach = vi.fn();
    setup(stateFor([newTab('chat'), newTab('file')]), {extra: {onDetach}});
    await user.pointer({keys: '[MouseRight]', target: screen.getByRole('tab', {name: 'Chat'})});
    const menu = await screen.findByRole('menu');
    expect(within(menu).getAllByRole('menuitem').map(item => item.textContent)).toEqual([
      'Close',
      'Close others',
      'Split right',
      'Split down',
      'Move to new window',
      'Reveal',
    ]);
    await user.click(within(menu).getByRole('menuitem', {name: 'Move to new window'}));
    expect(onDetach).toHaveBeenCalledWith(expect.objectContaining({kind: 'chat'}));
  });

  it('hides Move to new window for a tab that cannot move', async () => {
    const user = userEvent.setup();
    setup(stateFor([newTab('chat'), newTab('file')]), {extra: {onDetach: vi.fn(), canDetach: tab => tab.kind !== 'chat'}});
    await user.pointer({keys: '[MouseRight]', target: screen.getByRole('tab', {name: 'Chat'})});
    expect(screen.queryByRole('menuitem', {name: 'Move to new window'})).not.toBeInTheDocument();
  });

  it('splits right from the Tab actions button, with the keyboard only', async () => {
    const user = userEvent.setup();
    const {probe} = setup(stateFor([newTab('chat'), newTab('file')]));
    await user.tab();
    screen.getByRole('button', {name: 'Tab actions'}).focus();
    await user.keyboard('{Enter}');
    await user.click(await screen.findByRole('menuitem', {name: 'Split right'}));
    const groups = editorGroups(layoutOf(probe).root);
    expect(groups.map(item => item.tabs.map(tab => tab.kind))).toEqual([['file'], ['chat']]);
  });

  it('splits down and moves a tab to the other group, keeping the content mounted', async () => {
    const user = userEvent.setup();
    Object.keys(mounts).forEach(key => delete mounts[key]);
    const {probe} = setup(stateFor([newTab('chat'), newTab('file')], [newTab('browser')]));
    await user.click(screen.getByRole('tab', {name: 'Editor'}));
    const panel = screen.getByText('Editor content').closest('[role=tabpanel]')!;
    await user.pointer({keys: '[MouseRight]', target: screen.getByRole('tab', {name: 'Editor'})});
    await user.click(await screen.findByRole('menuitem', {name: 'Move to other group'}));
    const layout = layoutOf(probe);
    expect(group(layout, 1).tabs.map(tab => tab.kind)).toEqual(['browser', 'file']);
    expect(screen.getByText('Editor content').closest('[role=tabpanel]')).toBe(panel);
    expect(mounts.file).toBe(1);
  });

  it('maximizes a group from its menu and restores it', async () => {
    const user = userEvent.setup();
    const {probe} = setup(stateFor([newTab('chat')], [newTab('browser')]));
    await user.pointer({keys: '[MouseRight]', target: screen.getByRole('tab', {name: 'Chat'})});
    await user.click(await screen.findByRole('menuitem', {name: 'Maximize group'}));
    expect(layoutOf(probe).maximized).toBe(group(layoutOf(probe), 0).id);
    expect(screen.getAllByRole('tablist')).toHaveLength(1);
    await user.pointer({keys: '[MouseRight]', target: screen.getByRole('tab', {name: 'Chat'})});
    await user.click(await screen.findByRole('menuitem', {name: 'Restore group size'}));
    expect(layoutOf(probe).maximized).toBeNull();
    expect(screen.getAllByRole('tablist')).toHaveLength(2);
  });
});

describe('TabGroups: keep-alive', () => {
  it('keeps the same element and one mount when a tab moves through every kind of edit', async () => {
    const user = userEvent.setup();
    Object.keys(mounts).forEach(key => delete mounts[key]);
    const {probe} = setup(stateFor([newTab('chat'), newTab('terminal')]));
    await user.click(screen.getByRole('tab', {name: 'Terminal'}));
    const panel = screen.getByText('Terminal content').closest('[role=tabpanel]')!;
    const input = screen.getByLabelText('Terminal input') as HTMLTextAreaElement;
    await user.type(input, 'ls -la');
    const terminal = (layoutOf(probe).root as Group).tabs[1];

    act(() => {
      probe.current = probe.current; // no-op: the reducer is driven through the UI below
    });
    // Split right: the terminal moves to a new group.
    await user.pointer({keys: '[MouseRight]', target: screen.getByRole('tab', {name: 'Terminal'})});
    await user.click(await screen.findByRole('menuitem', {name: 'Split right'}));
    expect(findTab(layoutOf(probe), terminal.id)!.group.id).not.toBe(group(layoutOf(probe), 1)?.id === undefined ? '' : group(layoutOf(probe), 0).id);
    expect(screen.getByText('Terminal content').closest('[role=tabpanel]')).toBe(panel);
    expect((screen.getByLabelText('Terminal input') as HTMLTextAreaElement).value).toBe('ls -la');

    // Move it back, then into the bottom panel and out again.
    await user.pointer({keys: '[MouseRight]', target: screen.getByRole('tab', {name: 'Terminal'})});
    await user.click(await screen.findByRole('menuitem', {name: 'Move to other group'}));
    expect(screen.getByText('Terminal content').closest('[role=tabpanel]')).toBe(panel);
    expect(mounts.terminal).toBe(1);
    expect((screen.getByLabelText('Terminal input') as HTMLTextAreaElement).value).toBe('ls -la');
  });

  it('keeps a hidden tab mounted and unmounts it only when it closes', async () => {
    const user = userEvent.setup();
    Object.keys(mounts).forEach(key => delete mounts[key]);
    setup(stateFor([newTab('chat'), newTab('browser')]));
    await user.click(screen.getByRole('tab', {name: 'Browser'}));
    expect(screen.getByText('Browser content')).toBeInTheDocument();
    await user.click(screen.getByRole('tab', {name: 'Chat'}));
    const hidden = screen.getByText('Browser content', {ignore: 'nothing'});
    expect(hidden.closest('[role=tabpanel]')).toHaveAttribute('hidden');
    await user.click(screen.getByRole('tab', {name: 'Browser'}).querySelector('.tab-close')!);
    expect(screen.queryByText('Browser content')).not.toBeInTheDocument();
    expect(mounts.browser).toBe(1);
  });

  it('does not reorder DOM nodes when tabs move, so an iframe would not reload', async () => {
    const user = userEvent.setup();
    setup(stateFor([newTab('chat'), newTab('file'), newTab('browser')]));
    await user.click(screen.getByRole('tab', {name: 'Editor'}));
    await user.click(screen.getByRole('tab', {name: 'Browser'}));
    const order = () => Array.from(document.querySelectorAll('.tab-content')).map(node => node.getAttribute('data-kind'));
    const before = order();
    await user.pointer({keys: '[MouseRight]', target: screen.getByRole('tab', {name: 'Chat'})});
    await user.click(await screen.findByRole('menuitem', {name: 'Split right'}));
    expect(order()).toEqual(before);
  });
});

describe('TabGroups: splitters', () => {
  it('is a 1px separator, a keyboard-resizable control with a percentage value', async () => {
    const user = userEvent.setup();
    const {probe} = setup(stateFor([newTab('chat')], [newTab('file')]));
    const separator = screen.getByRole('separator', {name: 'Resize groups'});
    expect(separator).toHaveAttribute('aria-orientation', 'vertical');
    expect(separator).toHaveAttribute('aria-valuenow', '50');
    expect(separator).toHaveAttribute('aria-valuetext', '50 percent');
    expect(separator).toHaveAttribute('tabindex', '0');
    expect(separator.style.width).toBe('8px');
    separator.focus();
    await user.keyboard('{ArrowRight}');
    let sizes = (layoutOf(probe).root as Split).sizes;
    expect(sizes[0]).toBeCloseTo(0.5 + 16 / 1000);
    await user.keyboard('{Shift>}{ArrowLeft}{/Shift}');
    sizes = (layoutOf(probe).root as Split).sizes;
    expect(sizes[0]).toBeCloseTo(0.5 + 16 / 1000 - 64 / 1000);
    await user.keyboard('{Home}');
    expect((layoutOf(probe).root as Split).sizes[0]).toBeCloseTo(0.05);
    expect(screen.getByRole('separator', {name: 'Resize groups'})).toHaveAttribute('aria-valuenow', '5');
    await user.keyboard('{End}');
    expect((layoutOf(probe).root as Split).sizes[1]).toBeCloseTo(0.05);
    await user.keyboard('{Enter}');
    expect((layoutOf(probe).root as Split).sizes).toEqual([0.5, 0.5]);
  });

  it('drags with the pointer', () => {
    const {probe} = setup(stateFor([newTab('chat')], [newTab('file')]));
    const separator = screen.getByRole('separator', {name: 'Resize groups'});
    fireEvent(separator, new MouseEvent('pointerdown', {button: 0, clientX: 500, clientY: 100, bubbles: true, cancelable: true}));
    fireEvent(separator, new MouseEvent('pointermove', {clientX: 700, clientY: 100, bubbles: true}));
    expect((layoutOf(probe).root as Split).sizes[0]).toBeCloseTo(0.7);
    fireEvent(separator, new MouseEvent('pointerup', {clientX: 700, clientY: 100, bubbles: true}));
    fireEvent(separator, new MouseEvent('pointermove', {clientX: 200, clientY: 100, bubbles: true}));
    expect((layoutOf(probe).root as Split).sizes[0]).toBeCloseTo(0.7);
  });

  it('resizes the bottom panel with arrows (up grows it)', async () => {
    const user = userEvent.setup();
    const initial = stateFor([newTab('chat')]);
    const withPanel = reduceLayout(initial, {type: 'toggleBottomPanel'});
    const {probe} = setup(withPanel);
    const separator = screen.getByRole('separator', {name: 'Resize panel'});
    expect(separator).toHaveAttribute('aria-orientation', 'horizontal');
    expect(separator).toHaveAttribute('aria-valuenow', '260');
    separator.focus();
    await user.keyboard('{ArrowUp}');
    expect(layoutOf(probe).panel.size).toBe(276);
    await user.keyboard('{ArrowDown}{ArrowDown}');
    expect(layoutOf(probe).panel.size).toBe(244);
    await user.keyboard('{Enter}');
    expect(layoutOf(probe).panel.size).toBe(260);
  });
});

describe('TabGroups: drag and drop', () => {
  const press = (tab: HTMLElement, x: number, y: number) =>
    fireEvent(tab, new MouseEvent('pointerdown', {button: 0, clientX: x, clientY: y, bubbles: true, cancelable: true}));
  const moveTo = (x: number, y: number) => fireEvent(window, new MouseEvent('pointermove', {clientX: x, clientY: y, bubbles: true}));
  const release = (x: number, y: number) => fireEvent(window, new MouseEvent('pointerup', {clientX: x, clientY: y, bubbles: true}));

  it('shows the zone it would drop into, and a chip with the tab title', () => {
    setup(stateFor([newTab('chat'), newTab('file')]));
    press(screen.getByRole('tab', {name: 'Editor'}), 80, 18);
    expect(document.querySelector('.tab-drop')).toBeNull();
    moveTo(900, 300);
    expect(document.querySelector('.tab-drop')).toHaveAttribute('data-zone', 'right');
    expect(document.querySelector('.tab-drag-chip')).toHaveTextContent('Editor');
    moveTo(500, 300);
    expect(document.querySelector('.tab-drop')).toHaveAttribute('data-zone', 'center');
    moveTo(500, 560);
    expect(document.querySelector('.tab-drop')).toHaveAttribute('data-zone', 'bottom');
    moveTo(500, 60);
    expect(document.querySelector('.tab-drop')).toHaveAttribute('data-zone', 'top');
    moveTo(20, 300);
    expect(document.querySelector('.tab-drop')).toHaveAttribute('data-zone', 'left');
    release(20, 300);
    expect(document.querySelector('.tab-drop')).toBeNull();
    expect(document.querySelector('.tab-workspace')).not.toHaveAttribute('data-dragging');
  });

  it.each([
    ['right', 900, 300, 'row', ['chat'], ['file']],
    ['left', 20, 300, 'row', ['file'], ['chat']],
    ['bottom', 500, 560, 'column', ['chat'], ['file']],
    ['top', 500, 60, 'column', ['file'], ['chat']],
  ])('dropping on the %s zone splits that side', (_zone, x, y, dir, first, second) => {
    const {probe} = setup(stateFor([newTab('chat'), newTab('file')]));
    press(screen.getByRole('tab', {name: 'Editor'}), 80, 18);
    moveTo(x as number, y as number);
    release(x as number, y as number);
    const root = layoutOf(probe).root as Split;
    expect(root.dir).toBe(dir);
    expect(root.children.map(child => (child as Group).tabs.map(tab => tab.kind))).toEqual([first, second]);
  });

  it('dropping in the middle of another group moves the tab there', () => {
    const {probe} = setup(stateFor([newTab('chat'), newTab('file')], [newTab('browser')]));
    press(screen.getByRole('tab', {name: 'Editor'}), 80, 18);
    moveTo(750, 300);
    release(750, 300);
    expect(editorGroups(layoutOf(probe).root).map(item => item.tabs.map(tab => tab.kind))).toEqual([['chat'], ['browser', 'file']]);
  });

  it('dropping on a tab strip reorders by position', () => {
    const {probe} = setup(stateFor([newTab('chat'), newTab('file'), newTab('browser')]));
    const tabs = screen.getAllByRole('tab');
    tabs.forEach((tab, index) => {
      tab.getBoundingClientRect = () => ({left: index * 100, right: index * 100 + 100, top: 0, bottom: 36, width: 100, height: 36, x: index * 100, y: 0, toJSON() {}}) as DOMRect;
    });
    press(screen.getByRole('tab', {name: 'Browser'}), 250, 18);
    moveTo(20, 18);
    release(20, 18);
    expect((layoutOf(probe).root as Group).tabs.map(tab => tab.kind)).toEqual(['browser', 'chat', 'file']);
  });

  it('a press and release without moving is a click, not a drag', async () => {
    const {probe} = setup(stateFor([newTab('chat'), newTab('file')]));
    press(screen.getByRole('tab', {name: 'Editor'}), 80, 18);
    release(80, 18);
    expect(editorGroups(layoutOf(probe).root)).toHaveLength(1);
    expect(document.querySelector('.tab-drag-chip')).toBeNull();
  });

  it('Escape cancels a drag', () => {
    const {probe} = setup(stateFor([newTab('chat'), newTab('file')]));
    press(screen.getByRole('tab', {name: 'Editor'}), 80, 18);
    moveTo(900, 300);
    fireEvent.keyDown(window, {key: 'Escape'});
    expect(document.querySelector('.tab-drop')).toBeNull();
    release(900, 300);
    expect(editorGroups(layoutOf(probe).root)).toHaveLength(1);
  });

  it('dropping the only tab of a group on its own edge does nothing', () => {
    const {probe} = setup(stateFor([newTab('chat')]));
    press(screen.getByRole('tab', {name: 'Chat'}), 80, 18);
    moveTo(900, 300);
    release(900, 300);
    expect(editorGroups(layoutOf(probe).root)).toHaveLength(1);
  });

  it('ignores a touch pointer (touch users move tabs from the menu)', () => {
    setup(stateFor([newTab('chat'), newTab('file')]));
    const down = new MouseEvent('pointerdown', {button: 0, clientX: 80, clientY: 18, bubbles: true, cancelable: true});
    Object.defineProperty(down, 'pointerType', {value: 'touch'});
    fireEvent(screen.getByRole('tab', {name: 'Editor'}), down);
    moveTo(900, 300);
    expect(document.querySelector('.tab-drop')).toBeNull();
  });
});

describe('TabGroups: accessibility and empty groups', () => {
  it('has no axe violations with one group, with two, with the panel and with a menu open', async () => {
    const user = userEvent.setup();
    const one = setup(stateFor([newTab('chat'), newTab('file')]));
    await expectNoAxeViolations(one.container);
    cleanup();
    const two = setup(reduceLayout(stateFor([newTab('chat')], [newTab('browser'), newTab('computer')]), {type: 'toggleBottomPanel'}));
    await expectNoAxeViolations(two.container);
    await user.pointer({keys: '[MouseRight]', target: screen.getByRole('tab', {name: 'Chat'})});
    await expectNoAxeViolations(await screen.findByRole('menu'));
  });

  it('shows a sentence and one action in an empty group', async () => {
    const user = userEvent.setup();
    const initial = stateFor([newTab('chat')]);
    const emptied = reduceLayout(initial, {type: 'splitGroup', group: (initial.book.current.root as Group).id, dir: 'row', side: 'after'});
    const {probe} = setup(emptied);
    expect(screen.getByText('No tabs open in this group.')).toBeInTheDocument();
    await user.click(screen.getByRole('button', {name: 'Open a tab'}));
    await user.click(await screen.findByRole('menuitemcheckbox', {name: 'Browser'}));
    expect(editorGroups(layoutOf(probe).root)[1].tabs.map(tab => tab.kind)).toEqual(['browser']);
  });
});

describe('TabGroups: phone projection', () => {
  const stored = () => stateFor([newTab('chat'), newTab('file')], [newTab('browser')]);

  it('flattens every group into one tablist, with no splitters and no split commands', async () => {
    const user = userEvent.setup();
    setup(stored(), {phone: true, size: {width: 375, height: 700}});
    expect(screen.getAllByRole('tablist')).toHaveLength(1);
    expect(screen.getAllByRole('tab').map(tab => tab.textContent)).toEqual(['Chat', 'Editor', 'Browser', 'Terminal']);
    expect(screen.queryByRole('separator')).not.toBeInTheDocument();
    await user.pointer({keys: '[MouseRight]', target: screen.getByRole('tab', {name: 'Chat'})});
    const items = within(await screen.findByRole('menu')).getAllByRole('menuitem').map(item => item.textContent);
    expect(items).not.toContain('Split right');
    expect(items).not.toContain('Split down');
    expect(items).not.toContain('Move to other group');
    expect(items).toEqual(['Close', 'Close others', 'Reveal']);
  });

  it('switching tabs on a phone never writes to the stored layout', async () => {
    const user = userEvent.setup();
    const initial = stored();
    const {probe} = setup(initial, {phone: true, size: {width: 375, height: 700}});
    const before = JSON.stringify(layoutOf(probe));
    await user.click(screen.getByRole('tab', {name: 'Editor'}));
    expect(screen.getByRole('tab', {name: 'Editor'})).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByText('Editor content').closest('[role=tabpanel]')).not.toHaveAttribute('hidden');
    await user.click(screen.getByRole('tab', {name: 'Browser'}));
    expect(screen.getByText('Browser content').closest('[role=tabpanel]')).not.toHaveAttribute('hidden');
    expect(JSON.stringify(layoutOf(probe))).toBe(before);
    expect(editorGroups(layoutOf(probe).root)).toHaveLength(2);
  });

  it('keeps the same content mounted when the window grows from a phone to a desktop', async () => {
    Object.keys(mounts).forEach(key => delete mounts[key]);
    const initial = stored();
    const probe: Harness = {current: initial};
    const view = render(<Host initial={initial} probe={probe} phone size={{width: 375, height: 700}} />);
    const panel = screen.getByText('Chat content', {selector: 'span'}).closest('[role=tabpanel]')!;
    view.rerender(<Host initial={initial} probe={probe} phone={false} size={{width: 1000, height: 600}} />);
    expect(screen.getByText('Chat content', {selector: 'span'}).closest('[role=tabpanel]')).toBe(panel);
    expect(screen.getAllByRole('tablist')).toHaveLength(2);
    expect(mounts.chat).toBe(1);
  });

  it('follows a tab opened by a command', () => {
    const initial = stored();
    const probe: Harness = {current: initial};
    function Wrapper(): ReactNode {
      return <Host initial={initial} probe={probe} phone size={{width: 375, height: 700}} />;
    }
    render(<Wrapper />);
    expect(screen.getByRole('tab', {name: 'Chat'})).toHaveAttribute('aria-selected', 'true');
  });

  it('has no axe violations', async () => {
    const view = setup(stored(), {phone: true, size: {width: 375, height: 700}});
    await expectNoAxeViolations(view.container);
  });
});

describe('TabGroups: labels', () => {
  it('names groups by position and the panel as Panel', () => {
    const initial = reduceLayout(stateFor([newTab('chat')], [newTab('file')]), {type: 'toggleBottomPanel'});
    setup(initial);
    expect(screen.getByRole('group', {name: 'Editor group 1'})).toBeInTheDocument();
    expect(screen.getByRole('group', {name: 'Editor group 2'})).toBeInTheDocument();
    expect(screen.getByRole('group', {name: 'Panel'})).toBeInTheDocument();
  });
  it('uses each kind\'s canonical name', () => {
    const kinds: TabKind[] = ['chat', 'file', 'diff', 'terminal', 'browser', 'computer', 'artifacts', 'data', 'studio', 'settings', 'projects', 'workflows'];
    expect(kinds.map(kind => KIND_LABEL[kind])).toEqual(['Chat', 'Editor', 'Changes', 'Terminal', 'Browser', 'Computer', 'Artifacts', 'Data', 'Studio', 'Settings', 'Projects', 'Workflows']);
  });
});
