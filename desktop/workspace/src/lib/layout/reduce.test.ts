import {describe, expect, it} from 'vitest';
import {
  LIMITS,
  allGroups,
  allTabs,
  defaultLayout,
  editorGroups,
  findGroup,
  findTab,
  isSplit,
  newGroup,
  newTab,
  treeDepth,
  type Group,
  type Layout,
  type Node,
  type Split,
  type Tab,
  type TabKind,
} from './model';
import {
  REOPEN_DEPTH,
  UNDO_DEPTH,
  adoptTabIds,
  checkLayoutName,
  createState,
  focusedTab,
  initialState,
  reduceLayout,
  type LayoutAction,
  type LayoutState,
} from './reduce';

const run = (state: LayoutState, ...actions: LayoutAction[]) => actions.reduce((value, action) => reduceLayout(value, action), state);
const layoutOf = (state: LayoutState) => state.book.current;
const kinds = (group: Group) => group.tabs.map(tab => tab.kind);
const tabOf = (state: LayoutState, kind: TabKind) => allTabs(layoutOf(state)).find(tab => tab.kind === kind)!;
const groupOf = (state: LayoutState, kind: TabKind) => findTab(layoutOf(state), tabOf(state, kind).id)!.group;
const MULTI = new Set<TabKind>(['file', 'chat']);
const runMulti = (state: LayoutState, ...actions: LayoutAction[]) =>
  actions.reduce((value, action) => reduceLayout(value, action, {multi: MULTI}), state);

/** One group with these tabs, and an empty hidden panel group. */
function stateWith(...tabs: Tab[]): LayoutState {
  const group = newGroup(tabs);
  const base = defaultLayout();
  return createState({version: 2, current: {...base, root: group, focus: group.id}, named: []});
}

/** Two groups side by side: chat | file, then a panel. */
function twoGroups() {
  const left = newGroup([newTab('chat')]);
  const right = newGroup([newTab('file', '/a.ts'), newTab('browser')]);
  const base = defaultLayout();
  const root: Split = {id: 'row', dir: 'row', sizes: [0.5, 0.5], children: [left, right]};
  return {state: createState({version: 2, current: {...base, root, focus: left.id}, named: []}), left, right};
}

describe('openTab', () => {
  it('adds a tab after the active one and focuses its group', () => {
    const state = run(initialState(), {type: 'openTab', kind: 'file', ref: '/a.ts'});
    const group = editorGroups(layoutOf(state).root)[0];
    expect(kinds(group)).toEqual(['chat', 'file']);
    expect(group.active).toBe(group.tabs[1].id);
    expect(layoutOf(state).focus).toBe(group.id);
  });

  it('reveals an existing singleton instead of adding a second', () => {
    const first = run(initialState(), {type: 'openTab', kind: 'browser'});
    const chat = tabOf(first, 'chat');
    const state = run(first, {type: 'setActive', group: layoutOf(first).focus, tab: chat.id}, {type: 'openTab', kind: 'browser'});
    expect(allTabs(layoutOf(state)).filter(tab => tab.kind === 'browser')).toHaveLength(1);
    expect(focusedTab(layoutOf(state))?.kind).toBe('browser');
  });

  it('retargets a singleton whose ref changed', () => {
    const state = run(initialState(), {type: 'openTab', kind: 'settings', ref: 'models'}, {type: 'openTab', kind: 'settings', ref: 'voice'});
    const settings = allTabs(layoutOf(state)).filter(tab => tab.kind === 'settings');
    expect(settings).toHaveLength(1);
    expect(settings[0].ref).toBe('voice');
  });

  it('does not retarget when no ref is given', () => {
    const state = run(initialState(), {type: 'openTab', kind: 'settings', ref: 'models'}, {type: 'openTab', kind: 'settings'});
    expect(tabOf(state, 'settings').ref).toBe('models');
  });

  it('puts a terminal in the panel and shows the panel', () => {
    const state = run(initialState(), {type: 'openTab', kind: 'terminal'});
    expect(layoutOf(state).panel.visible).toBe(true);
    expect(kinds(layoutOf(state).panel.group)).toEqual(['terminal']);
    expect(layoutOf(state).focus).toBe(layoutOf(state).panel.group.id);
  });

  it('reveals the existing terminal in a hidden panel without adding one', () => {
    const state = run(initialState(), {type: 'openTab', kind: 'terminal'});
    expect(allTabs(layoutOf(state)).filter(tab => tab.kind === 'terminal')).toHaveLength(1);
    const hidden = run(state, {type: 'toggleBottomPanel'}, {type: 'openTab', kind: 'terminal'});
    expect(layoutOf(hidden).panel.visible).toBe(true);
    expect(allTabs(layoutOf(hidden)).filter(tab => tab.kind === 'terminal')).toHaveLength(1);
  });

  it('opens into a named group', () => {
    const {state, left} = twoGroups();
    const next = run(state, {type: 'openTab', kind: 'computer', group: left.id});
    expect(kinds(findGroup(layoutOf(next), left.id)!)).toEqual(['chat', 'computer']);
  });

  it('dedupes multi-instance kinds by ref and honours forceNew', () => {
    let state = stateWith(newTab('chat'));
    state = runMulti(state, {type: 'openTab', kind: 'file', ref: '/a.ts'}, {type: 'openTab', kind: 'file', ref: '/b.ts'}, {type: 'openTab', kind: 'file', ref: '/a.ts'});
    expect(allTabs(layoutOf(state)).filter(tab => tab.kind === 'file').map(tab => tab.ref)).toEqual(['/a.ts', '/b.ts']);
    state = runMulti(state, {type: 'openTab', kind: 'file', ref: '/a.ts', forceNew: true});
    expect(allTabs(layoutOf(state)).filter(tab => tab.kind === 'file')).toHaveLength(3);
  });

  it('restores a maximized layout when revealing a tab elsewhere', () => {
    const {state, left} = twoGroups();
    const maximized = run(state, {type: 'maximize', group: left.id});
    expect(layoutOf(maximized).maximized).toBe(left.id);
    const next = run(maximized, {type: 'openTab', kind: 'browser'});
    expect(layoutOf(next).maximized).toBeNull();
  });

  it('refuses a 65th tab', () => {
    let state = stateWith(newTab('chat'));
    state = runMulti(state, ...Array.from({length: LIMITS.tabs + 4}, (_, index): LayoutAction => ({type: 'openTab', kind: 'file', ref: '/f' + index})));
    expect(allTabs(layoutOf(state))).toHaveLength(LIMITS.tabs);
  });

  it('does not touch the undo stack when it only reveals', () => {
    const state = run(initialState(), {type: 'openTab', kind: 'browser'});
    const again = run(state, {type: 'openTab', kind: 'browser'});
    expect(again.undo).toHaveLength(state.undo.length);
  });
});

describe('closeTab and reopen', () => {
  it('moves the active tab to the neighbour that takes its place', () => {
    let state = stateWith(newTab('chat'), newTab('file'), newTab('browser'));
    const [, file] = (layoutOf(state).root as Group).tabs;
    state = run(state, {type: 'setActive', group: layoutOf(state).focus, tab: file.id}, {type: 'closeTab', tab: file.id});
    expect(focusedTab(layoutOf(state))?.kind).toBe('browser');
  });

  it('falls back to the previous tab when the last one closes', () => {
    let state = stateWith(newTab('chat'), newTab('file'));
    const file = tabOf(state, 'file');
    state = run(state, {type: 'setActive', group: layoutOf(state).focus, tab: file.id}, {type: 'closeTab', tab: file.id});
    expect(focusedTab(layoutOf(state))?.kind).toBe('chat');
  });

  it('collapses a group that lost its last tab and focuses its neighbour', () => {
    const {state, right} = twoGroups();
    const next = run(state, {type: 'closeTab', tab: right.tabs[0].id}, {type: 'closeTab', tab: right.tabs[1].id});
    expect(isSplit(layoutOf(next).root)).toBe(false);
    expect(kinds(layoutOf(next).root as Group)).toEqual(['chat']);
    expect(layoutOf(next).focus).toBe((layoutOf(next).root as Group).id);
  });

  it('hides the panel when its last tab closes', () => {
    const state = run(initialState(), {type: 'openTab', kind: 'terminal'});
    const next = run(state, {type: 'closeTab', tab: tabOf(state, 'terminal').id});
    expect(layoutOf(next).panel.visible).toBe(false);
    expect(layoutOf(next).panel.group.tabs).toHaveLength(0);
    expect(layoutOf(next).focus).not.toBe(layoutOf(next).panel.group.id);
  });

  it('refuses to close the lone chat tab', () => {
    const state = initialState();
    expect(run(state, {type: 'closeTab', tab: tabOf(state, 'chat').id})).toBe(state);
  });

  it('never leaves an empty root: closing the lone non-chat tab brings the chat back', () => {
    const state = stateWith(newTab('browser'));
    const next = run(state, {type: 'closeTab', tab: tabOf(state, 'browser').id});
    expect(kinds(layoutOf(next).root as Group)).toEqual(['chat']);
    expect(layoutOf(next).focus).toBe((layoutOf(next).root as Group).id);
  });

  it('keeps pinned tabs open unless forced', () => {
    const pinned: Tab = {...newTab('file'), pinned: true};
    const state = stateWith(newTab('chat'), pinned);
    expect(run(state, {type: 'closeTab', tab: pinned.id})).toBe(state);
    expect(kinds(layoutOf(run(state, {type: 'closeTab', tab: pinned.id, force: true})).root as Group)).toEqual(['chat']);
  });

  it('ignores an unknown tab', () => {
    const state = initialState();
    expect(run(state, {type: 'closeTab', tab: 'nope'})).toBe(state);
  });

  it('reopens the last closed tab where it was, newest first', () => {
    let state = stateWith(newTab('chat'), newTab('file'), newTab('browser'));
    const file = tabOf(state, 'file');
    const browser = tabOf(state, 'browser');
    state = run(state, {type: 'closeTab', tab: file.id}, {type: 'closeTab', tab: browser.id});
    expect(state.closed).toHaveLength(2);
    state = run(state, {type: 'reopenTab'});
    expect(kinds(layoutOf(state).root as Group)).toEqual(['chat', 'browser']);
    expect(focusedTab(layoutOf(state))?.id).toBe(browser.id);
    state = run(state, {type: 'reopenTab'});
    expect(kinds(layoutOf(state).root as Group)).toEqual(['chat', 'file', 'browser']);
    expect(state.closed).toHaveLength(0);
    expect(run(state, {type: 'reopenTab'})).toBe(state);
  });

  it('reopens into the focused group when the original group is gone', () => {
    const {state, right} = twoGroups();
    const closed = run(state, {type: 'closeTab', tab: right.tabs[0].id}, {type: 'closeTab', tab: right.tabs[1].id});
    const next = run(closed, {type: 'reopenTab'});
    expect(allTabs(layoutOf(next)).some(tab => tab.id === right.tabs[1].id)).toBe(true);
  });

  it('reveals an already open singleton instead of reopening a copy', () => {
    let state = stateWith(newTab('chat'), newTab('browser'));
    const browser = tabOf(state, 'browser');
    state = run(state, {type: 'closeTab', tab: browser.id}, {type: 'openTab', kind: 'browser'}, {type: 'reopenTab'});
    expect(allTabs(layoutOf(state)).filter(tab => tab.kind === 'browser')).toHaveLength(1);
  });

  it('remembers at most 20 closed tabs', () => {
    let state = stateWith(newTab('chat'));
    state = runMulti(state, ...Array.from({length: 30}, (_, index): LayoutAction => ({type: 'openTab', kind: 'file', ref: '/f' + index})));
    for (const tab of allTabs(layoutOf(state)).filter(item => item.kind === 'file')) state = runMulti(state, {type: 'closeTab', tab: tab.id});
    expect(state.closed).toHaveLength(REOPEN_DEPTH);
    expect(state.closed.at(-1)?.tab.ref).toBe('/f29');
  });

  it('closes other tabs but keeps pinned ones', () => {
    const pinned: Tab = {...newTab('settings'), pinned: true};
    let state = stateWith(newTab('chat'), newTab('file'), pinned, newTab('browser'));
    const keep = tabOf(state, 'browser');
    state = run(state, {type: 'closeOthers', tab: keep.id});
    expect(kinds(layoutOf(state).root as Group)).toEqual(['settings', 'browser']);
    expect(state.closed).toHaveLength(2);
    expect((layoutOf(state).root as Group).active).toBe(keep.id);
  });

  it('closes a whole group, pushing its tabs onto the reopen stack', () => {
    const {state, right} = twoGroups();
    const next = run(state, {type: 'closeGroup', group: right.id});
    expect(isSplit(layoutOf(next).root)).toBe(false);
    expect(next.closed.map(row => row.tab.kind)).toEqual(['file', 'browser']);
  });
});

describe('moveTab', () => {
  it('moves a tab between groups without changing its id', () => {
    const {state, left, right} = twoGroups();
    const chat = left.tabs[0];
    const next = run(state, {type: 'moveTab', tab: right.tabs[0].id, group: left.id, index: 0});
    const group = findGroup(layoutOf(next), left.id)!;
    expect(group.tabs.map(tab => tab.id)).toEqual([right.tabs[0].id, chat.id]);
    expect(group.active).toBe(right.tabs[0].id);
    expect(layoutOf(next).focus).toBe(left.id);
  });

  it('collapses the source group when it becomes empty', () => {
    const {state, left, right} = twoGroups();
    const next = run(state, {type: 'moveTab', tab: left.tabs[0].id, group: right.id});
    expect(isSplit(layoutOf(next).root)).toBe(false);
    expect(kinds(layoutOf(next).root as Group)).toEqual(['file', 'browser', 'chat']);
    expect(layoutOf(next).focus).toBe(right.id);
  });

  it('reorders inside a group in both directions', () => {
    let state = stateWith(newTab('chat'), newTab('file'), newTab('browser'));
    const [chat, , browser] = (layoutOf(state).root as Group).tabs;
    const group = (layoutOf(state).root as Group).id;
    state = run(state, {type: 'moveTab', tab: chat.id, group, index: 3});
    expect(kinds(layoutOf(state).root as Group)).toEqual(['file', 'browser', 'chat']);
    state = run(state, {type: 'moveTab', tab: browser.id, group, index: 0});
    expect(kinds(layoutOf(state).root as Group)).toEqual(['browser', 'file', 'chat']);
  });

  it('is a no-op for a drop onto the same position', () => {
    const state = stateWith(newTab('chat'), newTab('file'));
    const group = layoutOf(state).root as Group;
    const next = run(state, {type: 'moveTab', tab: group.tabs[0].id, group: group.id, index: 0});
    expect(kinds(layoutOf(next).root as Group)).toEqual(['chat', 'file']);
    expect(next.undo).toHaveLength(state.undo.length);
  });

  it('moves a tab into the panel and shows it', () => {
    const state = stateWith(newTab('chat'), newTab('browser'));
    const next = run(state, {type: 'moveTab', tab: tabOf(state, 'browser').id, group: layoutOf(state).panel.group.id});
    expect(layoutOf(next).panel.visible).toBe(true);
    expect(kinds(layoutOf(next).panel.group)).toEqual(['terminal', 'browser']);
  });

  it('hides the panel when its last tab moves out', () => {
    const base = run(initialState(), {type: 'openTab', kind: 'terminal'});
    const next = run(base, {type: 'moveTab', tab: tabOf(base, 'terminal').id, group: editorGroups(layoutOf(base).root)[0].id});
    expect(layoutOf(next).panel.visible).toBe(false);
    expect(kinds(editorGroups(layoutOf(next).root)[0])).toEqual(['chat', 'terminal']);
  });

  it('ignores unknown tabs and groups', () => {
    const {state, left} = twoGroups();
    expect(run(state, {type: 'moveTab', tab: 'nope', group: left.id })).toBe(state);
    expect(run(state, {type: 'moveTab', tab: left.tabs[0].id, group: 'nope'})).toBe(state);
  });
});

describe('splitGroup', () => {
  it('moves the active tab of a multi-tab group into a new group on the right', () => {
    const base = stateWith(newTab('chat'), newTab('file'));
    const group = layoutOf(base).root as Group;
    const state = run(base, {type: 'setActive', group: group.id, tab: group.tabs[1].id});
    const next = run(state, {type: 'splitGroup', group: group.id, dir: 'row', side: 'after'});
    const root = layoutOf(next).root as Split;
    expect(root.dir).toBe('row');
    expect(root.children.map(child => kinds(child as Group))).toEqual([['chat'], ['file']]);
    expect(root.sizes).toEqual([0.5, 0.5]);
    expect(layoutOf(next).focus).toBe((root.children[1] as Group).id);
  });

  it('puts the new group on the left or above for side before', () => {
    const base = stateWith(newTab('chat'), newTab('file'));
    const group = layoutOf(base).root as Group;
    const state = run(base, {type: 'setActive', group: group.id, tab: group.tabs[1].id});
    const next = run(state, {type: 'splitGroup', group: group.id, dir: 'column', side: 'before'});
    const root = layoutOf(next).root as Split;
    expect(root.dir).toBe('column');
    expect(root.children.map(child => kinds(child as Group))).toEqual([['file'], ['chat']]);
  });

  it('moves a named tab from another group', () => {
    const {state, left, right} = twoGroups();
    const next = run(state, {type: 'splitGroup', group: left.id, dir: 'column', side: 'after', tab: right.tabs[1].id});
    const row = layoutOf(next).root as Split;
    expect(row.dir).toBe('row');
    const column = row.children[0] as Split;
    expect(column.dir).toBe('column');
    expect(column.children.map(child => kinds(child as Group))).toEqual([['chat'], ['browser']]);
  });

  it('splits a lone tab into an empty group the focus follows, and a later focus change collapses it', () => {
    const state = initialState();
    const group = layoutOf(state).root as Group;
    const next = run(state, {type: 'splitGroup', group: group.id, dir: 'row', side: 'after'});
    const groups = editorGroups(layoutOf(next).root);
    expect(groups).toHaveLength(2);
    expect(groups[1].tabs).toHaveLength(0);
    expect(layoutOf(next).focus).toBe(groups[1].id);
    const opened = run(next, {type: 'openTab', kind: 'browser'});
    expect(kinds(findGroup(layoutOf(opened), groups[1].id)!)).toEqual(['browser']);
    const away = run(next, {type: 'focusGroup', group: group.id});
    expect(editorGroups(layoutOf(away).root)).toHaveLength(1);
    const collapsed = run(next, {type: 'closeGroup', group: groups[1].id});
    expect(editorGroups(layoutOf(collapsed).root)).toHaveLength(1);
  });

  it('merges a same-direction split into its parent', () => {
    let state = stateWith(newTab('chat'), newTab('file'), newTab('browser'));
    const group = layoutOf(state).root as Group;
    state = run(state, {type: 'splitGroup', group: group.id, dir: 'row', side: 'after'});
    const first = editorGroups(layoutOf(state).root)[0];
    state = run(state, {type: 'splitGroup', group: first.id, dir: 'row', side: 'after', tab: first.tabs[0].id});
    // Splitting a group with one tab onto itself is refused; with two tabs it adds a third pane in the same row.
    expect(editorGroups(layoutOf(state).root).length).toBeGreaterThanOrEqual(2);
    for (const node of [layoutOf(state).root]) {
      if (isSplit(node)) for (const child of node.children) if (isSplit(child)) expect(child.dir).not.toBe(node.dir);
    }
  });

  it('is a no-op when a group splits against its own only tab', () => {
    const state = initialState();
    const group = layoutOf(state).root as Group;
    const next = run(state, {type: 'splitGroup', group: group.id, dir: 'row', side: 'after', tab: group.tabs[0].id});
    expect(next).toBe(state);
  });

  it('cannot split the panel group', () => {
    const state = run(initialState(), {type: 'openTab', kind: 'terminal'});
    expect(run(state, {type: 'splitGroup', group: layoutOf(state).panel.group.id, dir: 'row', side: 'after'})).toBe(state);
  });

  it('stops at 12 groups', () => {
    let state = stateWith(newTab('chat'));
    state = runMulti(state, ...Array.from({length: 14}, (_, index): LayoutAction => ({type: 'openTab', kind: 'file', ref: '/f' + index})));
    for (let index = 0; index < 20; index++) {
      const groups = editorGroups(layoutOf(state).root);
      const target = groups.find(group => group.tabs.length > 1) ?? groups[0];
      state = run(state, {type: 'splitGroup', group: target.id, dir: index % 2 ? 'row' : 'column', side: 'after'});
    }
    expect(allGroups(layoutOf(state)).length).toBeLessThanOrEqual(LIMITS.groups);
  });

  it('stops at depth 6', () => {
    let state = stateWith(newTab('chat'));
    state = runMulti(state, ...Array.from({length: 12}, (_, index): LayoutAction => ({type: 'openTab', kind: 'file', ref: '/f' + index})));
    for (let index = 0; index < 10; index++) {
      const last = editorGroups(layoutOf(state).root).at(-1)!;
      const target = last.tabs.length > 1 ? last : editorGroups(layoutOf(state).root).find(group => group.tabs.length > 1);
      if (!target) break;
      state = run(state, {type: 'splitGroup', group: target.id, dir: index % 2 ? 'row' : 'column', side: 'after'});
    }
    expect(treeDepth(layoutOf(state).root)).toBeLessThanOrEqual(LIMITS.depth);
  });
});

describe('resize', () => {
  it('normalises the sizes and coalesces a drag into one undo step', () => {
    const {state} = twoGroups();
    let next = run(state, {type: 'resize', split: 'row', sizes: [3, 1]});
    expect((layoutOf(next).root as Split).sizes[0]).toBeCloseTo(0.75);
    expect(next.undo).toHaveLength(1);
    next = run(next, {type: 'resize', split: 'row', sizes: [0.6, 0.4]}, {type: 'resize', split: 'row', sizes: [0.55, 0.45]});
    expect(next.undo).toHaveLength(1);
    next = run(next, {type: 'undo'});
    expect((layoutOf(next).root as Split).sizes).toEqual([0.5, 0.5]);
  });

  it('starts a new undo step after another action', () => {
    const {state, left} = twoGroups();
    let next = run(state, {type: 'resize', split: 'row', sizes: [0.6, 0.4]}, {type: 'maximize', group: left.id}, {type: 'restore'}, {type: 'resize', split: 'row', sizes: [0.7, 0.3]});
    expect(next.undo.length).toBeGreaterThanOrEqual(3);
    next = run(next, {type: 'undo'});
    expect((layoutOf(next).root as Split).sizes[0]).toBeCloseTo(0.6);
  });

  it('keeps every pane above the floor', () => {
    const {state} = twoGroups();
    const sizes = (layoutOf(run(state, {type: 'resize', split: 'row', sizes: [0.001, 0.999]})).root as Split).sizes;
    expect(sizes[0]).toBeGreaterThanOrEqual(LIMITS.minFraction - 1e-9);
  });

  it('ignores unknown splits, wrong lengths and no-ops', () => {
    const {state} = twoGroups();
    expect(run(state, {type: 'resize', split: 'nope', sizes: [1, 1]})).toBe(state);
    expect(run(state, {type: 'resize', split: 'row', sizes: [1]})).toBe(state);
    expect(run(state, {type: 'resize', split: 'row', sizes: [0.5, 0.5]})).toBe(state);
  });

  it('clamps the side bar and the panel and coalesces their drags', () => {
    let state = run(initialState(), {type: 'setSideBarSize', size: 5000});
    expect(layoutOf(state).sideBar.size).toBe(LIMITS.sideBar.max);
    state = run(state, {type: 'setSideBarSize', size: 10});
    expect(layoutOf(state).sideBar.size).toBe(LIMITS.sideBar.min);
    expect(state.undo).toHaveLength(1);
    state = run(state, {type: 'setPanelSize', size: 9999});
    expect(layoutOf(state).panel.size).toBe(LIMITS.panel.max);
    expect(run(state, {type: 'setPanelSize', size: LIMITS.panel.max })).toBe(state);
  });
});

describe('focus, active and cycling', () => {
  it('sets the active tab and focuses its group', () => {
    const {state, left, right} = twoGroups();
    const next = run(state, {type: 'setActive', group: right.id, tab: right.tabs[1].id});
    expect(findGroup(layoutOf(next), right.id)!.active).toBe(right.tabs[1].id);
    expect(layoutOf(next).focus).toBe(right.id);
    expect(layoutOf(next).focus).not.toBe(left.id);
    expect(next.undo).toHaveLength(0);
  });

  it('ignores a tab that is not in the group', () => {
    const {state, left, right} = twoGroups();
    expect(run(state, {type: 'setActive', group: left.id, tab: right.tabs[0].id})).toBe(state);
  });

  it('cycles tabs forwards and backwards with wraparound', () => {
    let state = stateWith(newTab('chat'), newTab('file'), newTab('browser'));
    state = run(state, {type: 'cycleTab', step: 1});
    expect(focusedTab(layoutOf(state))?.kind).toBe('file');
    state = run(state, {type: 'cycleTab', step: 1}, {type: 'cycleTab', step: 1});
    expect(focusedTab(layoutOf(state))?.kind).toBe('chat');
    state = run(state, {type: 'cycleTab', step: -1});
    expect(focusedTab(layoutOf(state))?.kind).toBe('browser');
  });

  it('does nothing when cycling a one-tab group', () => {
    const state = initialState();
    expect(run(state, {type: 'cycleTab', step: 1})).toBe(state);
  });

  it('focuses a group by id and by position', () => {
    const {state, left, right} = twoGroups();
    expect(layoutOf(run(state, {type: 'focusGroup', group: right.id})).focus).toBe(right.id);
    expect(layoutOf(run(state, {type: 'focusGroupAt', index: 1})).focus).toBe(right.id);
    expect(run(state, {type: 'focusGroupAt', index: 2})).toBe(state);
    expect(run(state, {type: 'focusGroup', group: left.id})).toBe(state);
    expect(run(state, {type: 'focusGroup', group: 'nope'})).toBe(state);
  });

  it('cannot focus a hidden panel', () => {
    const state = initialState();
    expect(run(state, {type: 'focusGroup', group: layoutOf(state).panel.group.id})).toBe(state);
  });

  it('pins and unpins a tab', () => {
    const state = initialState();
    const chat = tabOf(state, 'chat');
    const pinned = run(state, {type: 'pinTab', tab: chat.id, pinned: true});
    expect(tabOf(pinned, 'chat').pinned).toBe(true);
    expect(tabOf(run(pinned, {type: 'pinTab', tab: chat.id, pinned: false}), 'chat').pinned).toBeUndefined();
  });
});

describe('maximize, side bar and panel', () => {
  it('maximizes a group and restores', () => {
    const {state, right} = twoGroups();
    const maximized = run(state, {type: 'maximize', group: right.id});
    expect(layoutOf(maximized).maximized).toBe(right.id);
    expect(layoutOf(maximized).focus).toBe(right.id);
    expect(layoutOf(run(maximized, {type: 'restore'})).maximized).toBeNull();
    expect(layoutOf(run(maximized, {type: 'toggleMaximize'})).maximized).toBeNull();
  });

  it('has nothing to maximize with a single group and no panel', () => {
    const state = initialState();
    expect(run(state, {type: 'maximize'})).toBe(state);
  });

  it('maximizes the panel when it is visible', () => {
    const state = run(initialState(), {type: 'openTab', kind: 'terminal'}, {type: 'maximize'});
    expect(layoutOf(state).maximized).toBe(layoutOf(state).panel.group.id);
  });

  it('drops maximize when the maximized group closes', () => {
    const {state, right} = twoGroups();
    let next = run(state, {type: 'maximize', group: right.id});
    next = run(next, {type: 'closeGroup', group: right.id});
    expect(layoutOf(next).maximized).toBeNull();
  });

  it('toggles the side bar and sets its view', () => {
    let state = run(initialState(), {type: 'toggleSideBar'});
    expect(layoutOf(state).sideBar.visible).toBe(false);
    state = run(state, {type: 'setSideBarView', view: 'explorer'});
    expect(layoutOf(state).sideBar).toMatchObject({view: 'explorer', visible: true});
    expect(run(state, {type: 'setSideBar', visible: true})).toBe(state);
    expect(layoutOf(run(state, {type: 'setSideBar', visible: false})).sideBar.visible).toBe(false);
  });

  it('opens the panel with a terminal when it is empty and focuses it', () => {
    let state = run(initialState(), {type: 'openTab', kind: 'terminal'});
    state = run(state, {type: 'closeTab', tab: tabOf(state, 'terminal').id});
    expect(layoutOf(state).panel.group.tabs).toHaveLength(0);
    state = run(state, {type: 'toggleBottomPanel'});
    expect(layoutOf(state).panel.visible).toBe(true);
    expect(kinds(layoutOf(state).panel.group)).toEqual(['terminal']);
    expect(layoutOf(state).focus).toBe(layoutOf(state).panel.group.id);
  });

  it('hides the panel and moves focus back to the editor area', () => {
    let state = run(initialState(), {type: 'toggleBottomPanel'});
    expect(layoutOf(state).panel.visible).toBe(true);
    state = run(state, {type: 'toggleBottomPanel'});
    expect(layoutOf(state).panel.visible).toBe(false);
    expect(editorGroups(layoutOf(state).root).some(group => group.id === layoutOf(state).focus)).toBe(true);
    expect(run(state, {type: 'setBottomPanel', visible: false})).toBe(state);
  });
});

describe('named layouts', () => {
  it('saves, overwrites by name, loads and deletes', () => {
    let state = run(initialState(), {type: 'openTab', kind: 'browser'});
    state = reduceLayout(state, {type: 'saveNamed', name: ' Review '}, {now: () => 7});
    expect(state.book.named).toHaveLength(1);
    expect(state.book.named[0]).toMatchObject({name: 'Review', updated: 7});
    const id = state.book.named[0].id;
    state = run(state, {type: 'closeTab', tab: tabOf(state, 'browser').id});
    state = reduceLayout(state, {type: 'saveNamed', name: 'Review'}, {now: () => 9});
    expect(state.book.named).toHaveLength(1);
    expect(state.book.named[0]).toMatchObject({id, updated: 9});
    expect(allTabs(state.book.named[0].layout).some(tab => tab.kind === 'browser')).toBe(false);
    state = run(state, {type: 'openTab', kind: 'computer'}, {type: 'loadNamed', id});
    expect(allTabs(layoutOf(state)).some(tab => tab.kind === 'computer')).toBe(false);
    state = run(state, {type: 'deleteNamed', id});
    expect(state.book.named).toHaveLength(0);
  });

  it('keeps the ids of open singleton tabs when a named layout loads, so their content stays mounted', () => {
    let state = run(initialState(), {type: 'openTab', kind: 'browser'});
    state = run(state, {type: 'saveNamed', name: 'With browser'});
    const id = state.book.named[0].id;
    const browser = tabOf(state, 'browser');
    const chat = tabOf(state, 'chat');
    state = run(state, {type: 'moveTab', tab: browser.id, group: layoutOf(state).panel.group.id}, {type: 'loadNamed', id});
    expect(tabOf(state, 'browser').id).toBe(browser.id);
    expect(tabOf(state, 'chat').id).toBe(chat.id);
  });

  it('validates names and the 30 layout limit', () => {
    expect(checkLayoutName('', [])).toMatch(/1–80/);
    expect(checkLayoutName('x'.repeat(81), [])).toMatch(/1–80/);
    expect(checkLayoutName('a\u0001b', [])).toMatch(/1–80/);
    expect(checkLayoutName('ok', [])).toBeNull();
    let state = initialState();
    for (let index = 0; index < 35; index++) state = run(state, {type: 'saveNamed', name: 'n' + index});
    expect(state.book.named).toHaveLength(LIMITS.named);
    expect(checkLayoutName('n0', state.book.named)).toBeNull();
    expect(checkLayoutName('another', state.book.named)).toMatch(/maximum 30/);
    expect(run(state, {type: 'saveNamed', name: ''})).toBe(state);
  });

  it('ignores an unknown named layout', () => {
    const state = initialState();
    expect(run(state, {type: 'loadNamed', id: 'nope'})).toBe(state);
  });
});

describe('resetToDefault, replace, undo', () => {
  it('resets to the default layout while keeping open tab ids', () => {
    let state = run(initialState(), {type: 'openTab', kind: 'browser'}, {type: 'toggleSideBar'});
    const chat = tabOf(state, 'chat');
    state = run(state, {type: 'resetToDefault'});
    expect(kinds(layoutOf(state).root as Group)).toEqual(['chat']);
    expect(tabOf(state, 'chat').id).toBe(chat.id);
    expect(layoutOf(state).sideBar.visible).toBe(true);
  });

  it('replaces the layout with a validated one without an undo step', () => {
    const state = initialState();
    const next = run(state, {type: 'replace', layout: defaultLayout()});
    expect(next.undo).toHaveLength(0);
    expect(layoutOf(next).root).not.toBe(layoutOf(state).root);
    const bad = run(state, {type: 'replace', layout: {version: 1} as never});
    expect(bad).toBe(state);
  });

  it('undoes up to 20 steps', () => {
    let state = initialState();
    for (let index = 0; index < 30; index++) state = run(state, {type: 'toggleSideBar'});
    expect(state.undo).toHaveLength(UNDO_DEPTH);
    for (let index = 0; index < 25; index++) state = run(state, {type: 'undo'});
    expect(state.undo).toHaveLength(0);
    expect(run(state, {type: 'undo'})).toBe(state);
  });

  it('undo restores the previous layout exactly', () => {
    const state = run(initialState(), {type: 'openTab', kind: 'browser'});
    const before = layoutOf(state);
    const edited = run(state, {type: 'toggleSideBar'}, {type: 'toggleBottomPanel'});
    const back = run(edited, {type: 'undo'}, {type: 'undo'});
    expect(layoutOf(back)).toEqual(before);
  });
});

describe('adoptTabIds', () => {
  it('matches by kind for singletons and by kind and ref for multi-instance kinds', () => {
    const current: Layout = (() => {
      const base = defaultLayout();
      const files = newGroup([newTab('file', '/a'), newTab('file', '/b')]);
      return {...base, root: files, focus: files.id};
    })();
    const next = (() => {
      const base = defaultLayout();
      const files = newGroup([newTab('file', '/b'), newTab('file', '/c')]);
      return {...base, root: files, focus: files.id};
    })();
    const adopted = adoptTabIds(next, current, new Set<TabKind>(['file']));
    const ids = (root: Node) => (root as Group).tabs.map(tab => tab.id);
    const currentTabs = (current.root as Group).tabs;
    expect(ids(adopted.root)[0]).toBe(currentTabs[1].id);
    expect(ids(adopted.root)[1]).not.toBe(currentTabs[0].id);
    expect((adopted.root as Group).active).toBe(ids(adopted.root)[0]);
  });
});
