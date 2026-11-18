import {
  LIMITS,
  MULTI_INSTANCE_KINDS,
  allGroups,
  allTabs,
  defaultLayout,
  defaultZone,
  editorGroups,
  findGroup,
  findTab,
  isEditorGroup,
  isGroup,
  isSplit,
  mapGroups,
  newGroup,
  newId,
  newTab,
  normalizeLayout,
  normalizeSizes,
  sameTab,
  targetEditorGroup,
  treeDepth,
  validateLayout,
  type Book,
  type Group,
  type Layout,
  type NamedLayout,
  type Node,
  type SideBarView,
  type Split,
  type Tab,
  type TabKind,
} from './model';

/**
 * The layout reducer. Every action is a pure function from state to state: no storage, no DOM, no clock unless one is
 * passed in. After each edit the layout is normalised: empty groups and one-child splits collapse, the focus is always a
 * live group, and the root is never empty (it falls back to a Chat tab).
 */

export type ClosedTab = {tab: Tab; group: string; index: number};

export type LayoutState = {
  book: Book;
  /** The last 20 layouts before an edit, newest last. */
  undo: Layout[];
  /** The last 20 closed tabs, newest last, for Reopen closed tab. */
  closed: ClosedTab[];
  /** The continuous gesture the last action belonged to, so a drag of one splitter is one undo step. */
  gesture: string | null;
};

export type LayoutAction =
  | {type: 'openTab'; kind: TabKind; ref?: string; group?: string; forceNew?: boolean}
  | {type: 'closeTab'; tab: string; force?: boolean}
  | {type: 'closeOthers'; tab: string}
  | {type: 'closeGroup'; group: string}
  | {type: 'reopenTab'}
  | {type: 'moveTab'; tab: string; group: string; index?: number}
  | {type: 'splitGroup'; group: string; dir: 'row' | 'column'; side: 'before' | 'after'; tab?: string}
  | {type: 'resize'; split: string; sizes: number[]}
  | {type: 'setActive'; group: string; tab: string}
  | {type: 'cycleTab'; step: 1 | -1; group?: string}
  | {type: 'focusGroup'; group: string}
  | {type: 'focusGroupAt'; index: number}
  | {type: 'pinTab'; tab: string; pinned: boolean}
  | {type: 'maximize'; group?: string}
  | {type: 'restore'}
  | {type: 'toggleMaximize'; group?: string}
  | {type: 'toggleSideBar'}
  | {type: 'setSideBar'; visible: boolean}
  | {type: 'setSideBarView'; view: SideBarView}
  | {type: 'setSideBarSize'; size: number}
  | {type: 'toggleBottomPanel'}
  | {type: 'setBottomPanel'; visible: boolean}
  | {type: 'setPanelSize'; size: number}
  | {type: 'loadNamed'; id: string}
  | {type: 'saveNamed'; name: string}
  | {type: 'deleteNamed'; id: string}
  | {type: 'resetToDefault'}
  | {type: 'replace'; layout: Layout}
  | {type: 'undo'};

export type ReduceOptions = {
  /** Kinds that may have several tabs, one per `ref`. Defaults to `MULTI_INSTANCE_KINDS`. */
  multi?: ReadonlySet<TabKind>;
  now?: () => number;
};

export const UNDO_DEPTH = 20;
export const REOPEN_DEPTH = 20;

export function createState(book: Book): LayoutState {
  return {book, undo: [], closed: [], gesture: null};
}

export function initialState(): LayoutState {
  return createState({version: 2, current: defaultLayout(), named: []});
}

/** Why a layout cannot be saved under `name`, or `null` when it can. */
export function checkLayoutName(name: string, named: readonly NamedLayout[]): string | null {
  const trimmed = name.trim();
  if (!trimmed || trimmed.length > LIMITS.nameLength || /[\x00-\x1f\x7f]/.test(trimmed))
    return 'Name the current layout with 1–80 characters.';
  if (!named.some(row => row.name === trimmed) && named.length >= LIMITS.named)
    return 'Remove a saved layout before adding another; maximum 30.';
  return null;
}

// ---- tree edits --------------------------------------------------------------------------------------------------

function setGroup(layout: Layout, id: string, change: (group: Group) => Group): Layout {
  if (layout.panel.group.id === id) return {...layout, panel: {...layout.panel, group: change(layout.panel.group)}};
  return {...layout, root: mapGroups(layout.root, group => (group.id === id ? change(group) : group))};
}

function withActive(group: Group, tab: string | null): Group {
  return group.active === tab ? group : {...group, active: tab};
}

function insertTab(group: Group, tab: Tab, index?: number): Group {
  const at = index === undefined ? group.tabs.length : Math.max(0, Math.min(index, group.tabs.length));
  const tabs = [...group.tabs.slice(0, at), tab, ...group.tabs.slice(at)];
  return {...group, tabs, active: tab.id};
}

/** Remove a tab from its group; the group's active tab moves to the neighbour that takes its place. */
function takeTab(layout: Layout, tabId: string): {layout: Layout; tab: Tab; group: Group; index: number} | null {
  const found = findTab(layout, tabId);
  if (!found) return null;
  const {tab, group, index} = found;
  const tabs = group.tabs.filter(item => item.id !== tabId);
  const active =
    group.active !== tabId ? group.active : (tabs[index] ?? tabs[index - 1] ?? null)?.id ?? null;
  return {layout: setGroup(layout, group.id, current => ({...current, tabs, active})), tab, group, index};
}

function insertBeside(root: Node, target: string, added: Group, dir: 'row' | 'column', side: 'before' | 'after'): Node {
  const place = (node: Node): Node => {
    if (isGroup(node)) {
      if (node.id !== target) return node;
      return {id: newId('s'), dir, sizes: [0.5, 0.5], children: side === 'before' ? [added, node] : [node, added]};
    }
    const index = node.children.findIndex(child => isGroup(child) && child.id === target);
    if (index >= 0 && node.dir === dir) {
      const share = node.sizes[index] / 2;
      const at = side === 'before' ? index : index + 1;
      const children = [...node.children.slice(0, at), added, ...node.children.slice(at)];
      const sizes = node.sizes.flatMap((size, i) => (i === index ? [share, share] : [size]));
      return {...node, children, sizes: normalizeSizes(sizes, children.length)};
    }
    return {...node, children: node.children.map(place)};
  };
  return place(root);
}

function findSplit(node: Node, id: string): Split | undefined {
  if (!isSplit(node)) return undefined;
  if (node.id === id) return node;
  for (const child of node.children) {
    const found = findSplit(child, id);
    if (found) return found;
  }
  return undefined;
}

function replaceSplit(node: Node, id: string, next: Split): Node {
  if (!isSplit(node)) return node;
  if (node.id === id) return next;
  return {...node, children: node.children.map(child => replaceSplit(child, id, next))};
}

/**
 * Re-use the ids of tabs that are already open when a whole layout is swapped in (a named layout, a reset), so their
 * content keeps its state instead of remounting. Matches by kind, and by ref for multi-instance kinds.
 */
export function adoptTabIds(next: Layout, current: Layout, multi: ReadonlySet<TabKind> = MULTI_INSTANCE_KINDS): Layout {
  const pool = [...allTabs(current)];
  const adopt = (group: Group): Group => {
    const tabs = group.tabs.map(tab => {
      const index = pool.findIndex(candidate => sameTab(candidate, tab, multi));
      if (index < 0) return tab;
      const [match] = pool.splice(index, 1);
      return {...tab, id: match.id};
    });
    const position = group.tabs.findIndex(tab => tab.id === group.active);
    return {...group, tabs, active: tabs[position >= 0 ? position : 0]?.id ?? null};
  };
  return {...next, root: mapGroups(next.root, adopt), panel: {...next.panel, group: adopt(next.panel.group)}};
}

// ---- the reducer -------------------------------------------------------------------------------------------------

function commit(state: LayoutState, layout: Layout, options: {undo?: boolean; gesture?: string | null} = {}): LayoutState {
  const next = normalizeLayout(layout);
  const record = options.undo !== false;
  return {
    ...state,
    book: {...state.book, current: next},
    undo: record ? [...state.undo.slice(-(UNDO_DEPTH - 1)), state.book.current] : state.undo,
    gesture: options.gesture ?? null,
  };
}

/** Collapse a tab's group after it lost its last tab: move focus to a neighbour first so the empty group goes away. */
function afterTabLeft(layout: Layout, group: Group): Layout {
  if (group.id === layout.panel.group.id) {
    const emptied = layout.panel.group.tabs.length === 0;
    return emptied
      ? {
          ...layout,
          panel: {...layout.panel, visible: false},
          focus: layout.focus === group.id ? targetEditorGroup(layout).id : layout.focus,
          maximized: layout.maximized === group.id ? null : layout.maximized,
        }
      : layout;
  }
  const current = findGroup(layout, group.id);
  if (!current || current.tabs.length > 0) return layout;
  const groups = editorGroups(layout.root);
  const index = groups.findIndex(item => item.id === group.id);
  const neighbour = groups[index + 1] ?? groups[index - 1];
  return {
    ...layout,
    focus: layout.focus === group.id ? (neighbour?.id ?? '') : layout.focus,
    maximized: layout.maximized === group.id ? null : layout.maximized,
  };
}

function reveal(layout: Layout, group: Group, tabId: string): Layout {
  let next = setGroup(layout, group.id, current => withActive(current, tabId));
  next = {...next, focus: group.id};
  if (group.id === layout.panel.group.id) next = {...next, panel: {...next.panel, visible: true}};
  if (next.maximized && next.maximized !== group.id) next = {...next, maximized: null};
  return next;
}

function pushClosed(closed: ClosedTab[], rows: ClosedTab[]): ClosedTab[] {
  return [...closed, ...rows].slice(-REOPEN_DEPTH);
}

export function reduceLayout(state: LayoutState, action: LayoutAction, options: ReduceOptions = {}): LayoutState {
  const multi = options.multi ?? MULTI_INSTANCE_KINDS;
  const layout = state.book.current;

  switch (action.type) {
    case 'openTab': {
      const existing = action.forceNew ? undefined : allTabs(layout).find(tab => sameTab(tab, action, multi));
      if (existing) {
        const found = findTab(layout, existing.id)!;
        let next = reveal(layout, found.group, existing.id);
        const retarget = !multi.has(action.kind) && action.ref !== undefined && existing.ref !== action.ref;
        if (retarget) {
          next = setGroup(next, found.group.id, group => ({
            ...group,
            tabs: group.tabs.map(tab => (tab.id === existing.id ? {...tab, ref: action.ref} : tab)),
          }));
        }
        return commit(state, next, {undo: false});
      }
      if (allTabs(layout).length >= LIMITS.tabs) return state;
      const target =
        (action.group && findGroup(layout, action.group)) ||
        (defaultZone(action.kind) === 'panel' ? layout.panel.group : targetEditorGroup(layout));
      const tab = newTab(action.kind, action.ref);
      const after = target.tabs.findIndex(item => item.id === target.active);
      const at = after >= 0 ? after + 1 : target.tabs.length;
      let next = setGroup(layout, target.id, group => insertTab(group, tab, at));
      next = reveal(next, target, tab.id);
      return commit(state, next);
    }

    case 'closeTab': {
      const found = findTab(layout, action.tab);
      if (!found) return state;
      if (found.tab.pinned && !action.force) return state;
      // The last tab of the last group is the Chat tab; closing it would only bring it straight back.
      const groups = editorGroups(layout.root);
      if (groups.length === 1 && groups[0].id === found.group.id && found.group.tabs.length === 1 && found.tab.kind === 'chat') return state;
      const taken = takeTab(layout, action.tab)!;
      const next = afterTabLeft(taken.layout, taken.group);
      const row = {tab: taken.tab, group: taken.group.id, index: taken.index};
      return {...commit(state, next), closed: pushClosed(state.closed, [row])};
    }

    case 'closeOthers': {
      const found = findTab(layout, action.tab);
      if (!found) return state;
      let next = layout;
      const rows: ClosedTab[] = [];
      found.group.tabs.forEach((tab, index) => {
        if (tab.id === action.tab || tab.pinned) return;
        rows.push({tab, group: found.group.id, index});
        next = setGroup(next, found.group.id, group => ({...group, tabs: group.tabs.filter(item => item.id !== tab.id)}));
      });
      if (!rows.length) return state;
      next = setGroup(next, found.group.id, group => withActive(group, action.tab));
      return {...commit(state, next), closed: pushClosed(state.closed, rows)};
    }

    case 'closeGroup': {
      const group = findGroup(layout, action.group);
      if (!group) return state;
      const rows = group.tabs.filter(tab => !tab.pinned).map(tab => ({tab, group: group.id, index: group.tabs.indexOf(tab)}));
      let next = setGroup(layout, group.id, current => {
        const tabs = current.tabs.filter(tab => tab.pinned);
        return {...current, tabs, active: tabs[0]?.id ?? null};
      });
      next = afterTabLeft(next, group);
      return {...commit(state, next), closed: pushClosed(state.closed, rows)};
    }

    case 'reopenTab': {
      const last = state.closed.at(-1);
      if (!last) return state;
      const closed = state.closed.slice(0, -1);
      const open = allTabs(layout).find(tab => sameTab(tab, last.tab, multi));
      if (open) {
        const found = findTab(layout, open.id)!;
        return {...commit(state, reveal(layout, found.group, open.id), {undo: false}), closed};
      }
      if (allTabs(layout).length >= LIMITS.tabs) return state;
      const target =
        findGroup(layout, last.group) ??
        (defaultZone(last.tab.kind) === 'panel' ? layout.panel.group : targetEditorGroup(layout));
      let next = setGroup(layout, target.id, group => insertTab(group, last.tab, last.index));
      next = reveal(next, target, last.tab.id);
      return {...commit(state, next), closed};
    }

    case 'moveTab': {
      const found = findTab(layout, action.tab);
      const destination = findGroup(layout, action.group);
      if (!found || !destination) return state;
      if (found.group.id === destination.id) {
        const from = found.index;
        const wanted = action.index === undefined ? destination.tabs.length : action.index;
        // `wanted` counts positions before the tab is lifted out, so a move to the right shifts by one.
        const to = Math.max(0, Math.min(wanted > from ? wanted - 1 : wanted, destination.tabs.length - 1));
        if (to === from) return commit(state, reveal(layout, destination, action.tab), {undo: false});
        const tabs = destination.tabs.filter(tab => tab.id !== action.tab);
        tabs.splice(to, 0, found.tab);
        const next = reveal(setGroup(layout, destination.id, group => ({...group, tabs})), destination, action.tab);
        return commit(state, next);
      }
      const taken = takeTab(layout, action.tab)!;
      let next = setGroup(taken.layout, destination.id, group => insertTab(group, found.tab, action.index));
      next = reveal(next, destination, action.tab);
      next = afterTabLeft(next, taken.group);
      return commit(state, next);
    }

    case 'splitGroup': {
      const target = findGroup(layout, action.group);
      if (!target || !isEditorGroup(layout, target.id)) return state;
      const tabId =
        action.tab ?? (target.tabs.length >= 2 ? (target.active ?? undefined) : undefined);
      const source = tabId ? findTab(layout, tabId) : undefined;
      if (tabId && !source) return state;
      // Dragging a group's only tab onto its own edge would leave nothing behind.
      if (source && source.group.id === target.id && target.tabs.length === 1) return state;
      if (allGroups(layout).length >= LIMITS.groups) return state;
      let next = layout;
      let moved: Tab | undefined;
      let from: Group | undefined;
      if (source) {
        const taken = takeTab(layout, source.tab.id)!;
        next = taken.layout;
        moved = taken.tab;
        from = taken.group;
      }
      const added = newGroup(moved ? [moved] : []);
      const root = insertBeside(next.root, target.id, added, action.dir, action.side);
      if (treeDepth(root) > LIMITS.depth) return state;
      next = {...next, root, focus: added.id};
      if (next.maximized) next = {...next, maximized: null};
      if (from) next = afterTabLeft({...next, focus: added.id}, from);
      return commit(state, next);
    }

    case 'resize': {
      const split = findSplit(layout.root, action.split);
      if (!split || action.sizes.length !== split.children.length) return state;
      const sizes = normalizeSizes(action.sizes, split.children.length);
      if (sizes.every((size, index) => Math.abs(size - split.sizes[index]) < 1e-9)) return state;
      const key = 'resize:' + split.id;
      const next = {...layout, root: replaceSplit(layout.root, split.id, {...split, sizes})};
      return commit(state, next, {undo: state.gesture !== key, gesture: key});
    }

    case 'setActive': {
      const group = findGroup(layout, action.group);
      if (!group || !group.tabs.some(tab => tab.id === action.tab)) return state;
      return commit(state, reveal(layout, group, action.tab), {undo: false});
    }

    case 'cycleTab': {
      const group = findGroup(layout, action.group ?? layout.focus);
      if (!group || group.tabs.length < 2) return state;
      const index = group.tabs.findIndex(tab => tab.id === group.active);
      const to = group.tabs[(index + action.step + group.tabs.length) % group.tabs.length];
      return commit(state, reveal(layout, group, to.id), {undo: false});
    }

    case 'focusGroup': {
      const group = findGroup(layout, action.group);
      if (!group || layout.focus === group.id) return state;
      if (group.id === layout.panel.group.id && !layout.panel.visible) return state;
      return commit(state, {...layout, focus: group.id}, {undo: false});
    }

    case 'focusGroupAt': {
      const group = editorGroups(layout.root)[action.index];
      if (!group || layout.focus === group.id) return state;
      return commit(state, {...layout, focus: group.id, maximized: layout.maximized && layout.maximized !== group.id ? null : layout.maximized}, {undo: false});
    }

    case 'pinTab': {
      const found = findTab(layout, action.tab);
      if (!found || !!found.tab.pinned === action.pinned) return state;
      const next = setGroup(layout, found.group.id, group => ({
        ...group,
        tabs: group.tabs.map(tab => {
          if (tab.id !== action.tab) return tab;
          const {pinned: _pinned, ...rest} = tab;
          return action.pinned ? {...rest, pinned: true} : rest;
        }),
      }));
      return commit(state, next);
    }

    case 'maximize':
    case 'toggleMaximize': {
      const id = action.group ?? layout.focus;
      const group = findGroup(layout, id);
      if (!group) return state;
      if (layout.maximized === id) {
        return action.type === 'toggleMaximize' ? commit(state, {...layout, maximized: null}) : state;
      }
      const visible = editorGroups(layout.root).length + (layout.panel.visible ? 1 : 0);
      if (visible < 2 || (group.id === layout.panel.group.id && !layout.panel.visible)) return state;
      return commit(state, {...layout, maximized: id, focus: id});
    }

    case 'restore':
      return layout.maximized ? commit(state, {...layout, maximized: null}) : state;

    case 'toggleSideBar':
      return commit(state, {...layout, sideBar: {...layout.sideBar, visible: !layout.sideBar.visible}});

    case 'setSideBar':
      return layout.sideBar.visible === action.visible
        ? state
        : commit(state, {...layout, sideBar: {...layout.sideBar, visible: action.visible}});

    case 'setSideBarView':
      return commit(state, {...layout, sideBar: {...layout.sideBar, view: action.view, visible: true}});

    case 'setSideBarSize': {
      const size = Math.max(LIMITS.sideBar.min, Math.min(LIMITS.sideBar.max, Math.round(action.size)));
      if (size === layout.sideBar.size) return state;
      return commit(state, {...layout, sideBar: {...layout.sideBar, size}}, {undo: state.gesture !== 'sideBar', gesture: 'sideBar'});
    }

    case 'toggleBottomPanel':
    case 'setBottomPanel': {
      const show = action.type === 'toggleBottomPanel' ? !layout.panel.visible : action.visible;
      if (show === layout.panel.visible) return state;
      if (!show) {
        const next = {
          ...layout,
          panel: {...layout.panel, visible: false},
          focus: layout.focus === layout.panel.group.id ? targetEditorGroup(layout).id : layout.focus,
          maximized: layout.maximized === layout.panel.group.id ? null : layout.maximized,
        };
        return commit(state, next);
      }
      let group = layout.panel.group;
      if (group.tabs.length === 0) group = insertTab(group, newTab('terminal'));
      return commit(state, {...layout, panel: {...layout.panel, group, visible: true}, focus: group.id});
    }

    case 'setPanelSize': {
      const size = Math.max(LIMITS.panel.min, Math.min(LIMITS.panel.max, Math.round(action.size)));
      if (size === layout.panel.size) return state;
      return commit(state, {...layout, panel: {...layout.panel, size}}, {undo: state.gesture !== 'panel', gesture: 'panel'});
    }

    case 'loadNamed': {
      const entry = state.book.named.find(row => row.id === action.id);
      if (!entry) return state;
      return commit(state, adoptTabIds(entry.layout, layout, multi));
    }

    case 'saveNamed': {
      if (checkLayoutName(action.name, state.book.named)) return state;
      const name = action.name.trim();
      const old = state.book.named.find(row => row.name === name);
      const entry: NamedLayout = {id: old?.id ?? newId('l'), name, layout, updated: (options.now ?? Date.now)()};
      return {...state, book: {...state.book, named: [...state.book.named.filter(row => row.id !== entry.id), entry]}};
    }

    case 'deleteNamed':
      return {...state, book: {...state.book, named: state.book.named.filter(row => row.id !== action.id)}};

    case 'resetToDefault':
      return commit(state, adoptTabIds(defaultLayout(), layout, multi));

    case 'replace': {
      const next = validateLayout(action.layout);
      return next ? {...state, book: {...state.book, current: next}, gesture: null} : state;
    }

    case 'undo': {
      const previous = state.undo.at(-1);
      if (!previous) return state;
      return {...state, book: {...state.book, current: previous}, undo: state.undo.slice(0, -1), gesture: null};
    }
  }
}

// ---- selectors ---------------------------------------------------------------------------------------------------

/** The tab with keyboard focus: the active tab of the focused group. */
export function focusedTab(layout: Layout): Tab | undefined {
  const group = findGroup(layout, layout.focus);
  return group?.tabs.find(tab => tab.id === group.active);
}
