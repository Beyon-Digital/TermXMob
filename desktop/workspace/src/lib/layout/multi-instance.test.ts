import {describe, expect, it} from 'vitest';
import {closing} from '../../shell/use-tree-layout';
import {
  MULTI_INSTANCE_KINDS,
  allTabs,
  defaultLayout,
  defaultZone,
  editorGroups,
  findTab,
  newGroup,
  newTab,
  type Layout,
  type TabKind,
} from './model';
import {createState, initialState, reduceLayout, type LayoutAction, type LayoutState} from './reduce';

const run = (state: LayoutState, ...actions: LayoutAction[]) => actions.reduce((value, action) => reduceLayout(value, action), state);
const layoutOf = (state: LayoutState) => state.book.current;
const ofKind = (state: LayoutState, kind: TabKind) => allTabs(layoutOf(state)).filter(tab => tab.kind === kind);

describe('multi-instance kinds', () => {
  it('are files, terminals and diffs; chat stays a singleton', () => {
    expect([...MULTI_INSTANCE_KINDS].sort()).toEqual(['diff', 'file', 'terminal']);
    expect(MULTI_INSTANCE_KINDS.has('chat')).toBe(false);
  });

  it('puts the bottom panel kinds in the panel and everything else in the editor area', () => {
    for (const kind of ['terminal', 'problems', 'preview', 'debug'] as const) expect(defaultZone(kind)).toBe('panel');
    for (const kind of ['chat', 'file', 'diff', 'browser', 'settings'] as const) expect(defaultZone(kind)).toBe('editor');
  });
});

describe('file tabs', () => {
  it('opening the same file twice gives one tab, and a second file a second tab', () => {
    const once = run(initialState(), {type: 'openTab', kind: 'file', ref: 'p:/work:src/a.ts'});
    const twice = run(once, {type: 'openTab', kind: 'file', ref: 'p:/work:src/a.ts'});
    expect(ofKind(twice, 'file')).toHaveLength(1);
    const other = run(twice, {type: 'openTab', kind: 'file', ref: 'p:/work:src/b.ts'});
    expect(ofKind(other, 'file').map(tab => tab.ref)).toEqual(['p:/work:src/a.ts', 'p:/work:src/b.ts']);
    expect(editorGroups(layoutOf(other).root)[0].tabs.map(tab => tab.kind)).toEqual(['chat', 'file', 'file']);
  });

  it('reveals the open tab of a file that is open elsewhere instead of opening a copy', () => {
    let state = run(initialState(), {type: 'openTab', kind: 'file', ref: 'k1'}, {type: 'openTab', kind: 'file', ref: 'k2'});
    const first = ofKind(state, 'file')[0];
    state = run(state, {type: 'splitGroup', group: findTab(layoutOf(state), first.id)!.group.id, dir: 'row', side: 'after', tab: first.id});
    state = run(state, {type: 'openTab', kind: 'file', ref: 'k1'});
    expect(ofKind(state, 'file')).toHaveLength(2);
    expect(findTab(layoutOf(state), first.id)).toBeDefined();
  });

  it('asking for the Editor without a file reveals an open file tab rather than adding an empty one', () => {
    const state = run(initialState(), {type: 'openTab', kind: 'file', ref: 'k1'}, {type: 'openTab', kind: 'file'});
    expect(ofKind(state, 'file')).toHaveLength(1);
    const none = run(initialState(), {type: 'openTab', kind: 'file'});
    expect(ofKind(none, 'file')).toEqual([expect.objectContaining({kind: 'file'})]);
    expect(ofKind(none, 'file')[0].ref).toBeUndefined();
  });

  it('keeps a tab\'s id (so its editor stays mounted) when it moves to another group', () => {
    let state = run(initialState(), {type: 'openTab', kind: 'file', ref: 'k1'});
    const tab = ofKind(state, 'file')[0];
    state = run(state, {type: 'splitGroup', group: findTab(layoutOf(state), tab.id)!.group.id, dir: 'row', side: 'after', tab: tab.id});
    const moved = findTab(layoutOf(state), tab.id)!;
    expect(moved.tab.id).toBe(tab.id);
    expect(editorGroups(layoutOf(state).root)).toHaveLength(2);
    const back = run(state, {type: 'moveTab', tab: tab.id, group: editorGroups(layoutOf(state).root)[0].id});
    expect(findTab(layoutOf(back), tab.id)!.tab.id).toBe(tab.id);
  });

  it('reopens a closed file tab with its file', () => {
    let state = run(initialState(), {type: 'openTab', kind: 'file', ref: 'k1'});
    state = run(state, {type: 'closeTab', tab: ofKind(state, 'file')[0].id});
    expect(ofKind(state, 'file')).toHaveLength(0);
    state = run(state, {type: 'reopenTab'});
    expect(ofKind(state, 'file').map(tab => tab.ref)).toEqual(['k1']);
  });
});

describe('terminal tabs', () => {
  it('holds one tab per shell in the bottom panel, and shows the panel', () => {
    const state = run(initialState(), {type: 'openTab', kind: 'terminal', ref: 'pty-1'}, {type: 'openTab', kind: 'terminal', ref: 'pty-2'});
    expect(layoutOf(state).panel.group.tabs.map(tab => tab.ref)).toEqual(['pty-1', 'pty-2']);
    expect(layoutOf(state).panel.visible).toBe(true);
    expect(layoutOf(state).panel.group.active).toBe(layoutOf(state).panel.group.tabs[1].id);
  });

  it('shows an open shell again instead of adding a second tab for it', () => {
    const state = run(
      initialState(),
      {type: 'openTab', kind: 'terminal', ref: 'pty-1'},
      {type: 'openTab', kind: 'terminal', ref: 'pty-2'},
      {type: 'openTab', kind: 'terminal', ref: 'pty-1'},
    );
    expect(ofKind(state, 'terminal')).toHaveLength(2);
    expect(findTab(layoutOf(state), layoutOf(state).panel.group.active!)!.tab.ref).toBe('pty-1');
  });

  it('Focus terminal, which names no shell, reveals one that is open', () => {
    const state = run(initialState(), {type: 'openTab', kind: 'terminal', ref: 'pty-1'}, {type: 'toggleBottomPanel'}, {type: 'openTab', kind: 'terminal'});
    expect(ofKind(state, 'terminal')).toHaveLength(1);
    expect(layoutOf(state).panel.visible).toBe(true);
  });

  it('gives an unattached tab its shell, and drops it when another tab already shows that shell', () => {
    let state = run(initialState(), {type: 'openTab', kind: 'terminal', ref: 'pty-1'}, {type: 'openTab', kind: 'terminal', ref: 'pty-2', forceNew: true});
    const loose = newTab('terminal');
    const base = layoutOf(state);
    state = createState({
      version: 2,
      current: {...base, panel: {...base.panel, group: {...base.panel.group, tabs: [...base.panel.group.tabs, loose], active: loose.id}}},
      named: [],
    });
    state = run(state, {type: 'setRef', tab: loose.id, ref: 'pty-3'});
    expect(findTab(layoutOf(state), loose.id)!.tab.ref).toBe('pty-3');
    const clash = run(state, {type: 'setRef', tab: loose.id, ref: 'pty-1'});
    expect(findTab(layoutOf(clash), loose.id)).toBeUndefined();
    expect(ofKind(clash, 'terminal').map(tab => tab.ref)).toEqual(['pty-1', 'pty-2']);
  });
});

describe('placeholder tabs', () => {
  it('lets the empty Editor tab take the first file, and the panel\'s starting Terminal the first shell', () => {
    const empty = run(initialState(), {type: 'openTab', kind: 'file'});
    const filled = run(empty, {type: 'openTab', kind: 'file', ref: 'k1'});
    expect(ofKind(filled, 'file').map(tab => tab.ref)).toEqual(['k1']);
    expect(ofKind(filled, 'file')[0].id).toBe(ofKind(empty, 'file')[0].id);
    const shell = run(initialState(), {type: 'openTab', kind: 'terminal', ref: 'pty-1'});
    expect(ofKind(shell, 'terminal').map(tab => tab.ref)).toEqual(['pty-1']);
  });

  it('opens a new tab instead when the placeholder is in another group than the one asked for', () => {
    const empty = run(initialState(), {type: 'openTab', kind: 'file'});
    const next = run(empty, {type: 'openTab', kind: 'file', ref: 'k1', group: layoutOf(empty).panel.group.id});
    expect(ofKind(next, 'file')).toHaveLength(2);
  });
});

describe('closing, for the close guard', () => {
  const layout = (): Layout => {
    const base = defaultLayout();
    const group = newGroup([newTab('chat'), newTab('file', 'k1'), newTab('file', 'k2')]);
    return {...base, root: group, focus: group.id, panel: {...base.panel, group: newGroup([newTab('terminal', 'pty-1')])}};
  };

  it('names the tabs each close action would drop', () => {
    const book = layout();
    const [chat, one, two] = (book.root as {tabs: {id: string}[]}).tabs;
    expect(closing(book, {type: 'closeTab', tab: one.id}).map(tab => tab.ref)).toEqual(['k1']);
    expect(closing(book, {type: 'closeOthers', tab: one.id}).map(tab => tab.id)).toEqual([chat.id, two.id]);
    expect(closing(book, {type: 'closeGroup', group: (book.root as {id: string}).id})).toHaveLength(3);
    expect(closing(book, {type: 'closeTab', tab: book.panel.group.tabs[0].id}).map(tab => tab.ref)).toEqual(['pty-1']);
  });

  it('never asks about a forced close, a pinned tab or an action that closes nothing', () => {
    const book = layout();
    const one = (book.root as {tabs: {id: string}[]}).tabs[1];
    expect(closing(book, {type: 'closeTab', tab: one.id, force: true})).toEqual([]);
    expect(closing(book, {type: 'toggleSideBar'})).toEqual([]);
    const keep = {...newTab('file', 'k9'), pinned: true};
    const group = newGroup([newTab('chat'), keep]);
    const pinned: Layout = {...book, root: group, focus: group.id};
    expect(closing(pinned, {type: 'closeTab', tab: keep.id})).toEqual([]);
  });
});
