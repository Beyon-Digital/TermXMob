import {useCallback, useEffect, useReducer, useRef, useState} from 'react';
import {leaveSettings} from '../features/settings-forms';
import {
  allGroups,
  allTabs,
  defaultLayout,
  editorGroups,
  isEditorGroup,
  newGroup,
  newTab,
  type Book,
  type Layout as TreeLayout,
  type SideBarView,
  type Tab,
  type TabKind,
} from '../lib/layout/model';
import {
  checkLayoutName,
  createState,
  focusedTab,
  initialState,
  reduceLayout,
  type LayoutAction,
  type LayoutState,
} from '../lib/layout/reduce';
import {LayoutBookStore} from '../lib/layout/store';
import {isDetachedWindow} from '../lib/native';
import {isSettingsCategory, canonicalSettingsCategory} from '../lib/settings-categories';
import {requestNewTerminal} from '../lib/terminal-requests';
import type {Layout} from '../lib/use-layout';
import {detachedBrowser, type WindowPanel} from '../lib/window-journal';
import type {DockPanel} from '../lib/docking';
import type {PanelCommand, Surface} from './commands';
import type {CloseGuard, LayoutDeps, TreeController} from './layout-controller';
import {routeInitialLayout, workspaceRoute} from './routes';
import {useSidebarResize} from './use-sidebar-resize';
import {useSurfaceRequests} from './use-surface-requests';
import {kindForWindowPanel, windowPanelForKind} from './window-panel';

/** Does a tab's `ref` still mean something? Used when a stored layout is read, so a stale tab is dropped, not shown empty. */
export function resolveTabRef(tab: Tab): boolean {
  if (tab.ref === undefined) return true;
  switch (tab.kind) {
    case 'settings':
      return isSettingsCategory(tab.ref);
    case 'projects':
      return CONTROL_PAGES.includes(tab.ref);
    case 'workflows':
      return /^[a-f0-9]{32}$/.test(tab.ref);
    default:
      return true;
  }
}

const CONTROL_PAGES = ['overview', 'projects', 'tasks'];

/** The tab the old preset names stand for. `focus` is not a tab: it maximizes the focused group. */
const KIND_OF_LAYOUT: Record<Layout, TabKind | null> = {
  chat: 'chat',
  workbench: 'file',
  browser: 'browser',
  computer: 'computer',
  artifacts: 'artifacts',
  review: 'diff',
  terminal: 'terminal',
  focus: null,
};

const KIND_OF_SURFACE: Record<Surface, TabKind> = {
  review: 'diff',
  browser: 'browser',
  terminal: 'terminal',
  editor: 'file',
  computer: 'computer',
  artifacts: 'artifacts',
};

const KIND_OF_PANEL: Record<DockPanel, TabKind> = {
  conversation: 'chat',
  editor: 'file',
  review: 'diff',
  browser: 'browser',
  terminal: 'terminal',
  computer: 'computer',
  artifacts: 'artifacts',
};

/** What a manager or settings page opens as: the kind of tab and its `ref`. `null` for pages that are not tabs. */
export function managerTab(page: string): {kind: TabKind; ref?: string} | null {
  if (!page) return null;
  if (page === 'workflows') return {kind: 'workflows'};
  if (CONTROL_PAGES.includes(page)) return {kind: 'projects', ref: page};
  return {kind: 'settings', ref: canonicalSettingsCategory(page)};
}

type Action = LayoutAction | {type: 'hydrate'; book: Book};

function reducer(state: LayoutState, action: Action): LayoutState {
  return action.type === 'hydrate' ? createState(action.book) : reduceLayout(state, action);
}

/** Actions that can drop a Settings tab with unsaved edits without going through its own close. */
const DISCARDING: LayoutAction['type'][] = ['closeOthers', 'closeGroup', 'loadNamed', 'resetToDefault'];

/** The tabs an action would close, for the close guard. A forced close (a shell that already ended) is never asked about. */
export function closing(book: TreeLayout, action: LayoutAction): Tab[] {
  if (action.type === 'closeTab') {
    if (action.force) return [];
    const found = allGroups(book).flatMap(group => group.tabs).find(tab => tab.id === action.tab);
    return found && !found.pinned ? [found] : [];
  }
  if (action.type === 'closeGroup') {
    return allGroups(book).find(group => group.id === action.group)?.tabs.filter(tab => !tab.pinned) ?? [];
  }
  if (action.type === 'closeOthers') {
    const group = allGroups(book).find(item => item.tabs.some(tab => tab.id === action.tab));
    return group?.tabs.filter(tab => tab.id !== action.tab && !tab.pinned) ?? [];
  }
  return [];
}

/** A layout of one tab, for a window that was detached to show a single surface. */
export function oneTabLayout(kind: TabKind): TreeLayout {
  const group = newGroup([newTab(kind)]);
  const base = defaultLayout();
  return {...base, root: group, focus: group.id, sideBar: {...base.sideBar, visible: false}};
}

function focusContent(tab: Tab | undefined) {
  if (!tab) return;
  requestAnimationFrame(() => {
    const content = document.getElementById('panel-' + tab.id);
    const target = content?.querySelector<HTMLElement>('textarea,input,[tabindex="0"],button');
    (target ?? document.getElementById('tab-' + tab.id))?.focus();
  });
}

/**
 * The new layout engine: the tab-group tree behind the layout controller interface.
 *
 * State is `lib/layout/reduce.ts`. It is restored from `termx-layout-v2:<owner>:<device>` (migrating the v1 presets the
 * first time), written back after each change, and never written from a detached window, which shows one tab and must not
 * replace the main window's layout.
 */
export function useTreeLayout(deps: LayoutDeps): TreeController {
  const {ownerId, ownerNow, setWorkspace, setWorkspaceState, setManagerScope, report, palette} = deps;
  const [state, reduce] = useReducer(reducer, undefined, initialState);
  const [openCount, setOpenCount] = useState(0);
  const dispatchRaw = useCallback((action: Action) => {
    if (action.type === 'openTab') setOpenCount(count => count + 1);
    reduce(action);
  }, []);
  const [restoredFor, setRestoredFor] = useState('');
  const [dirty, setDirtyTabs] = useState<ReadonlySet<string>>(() => new Set());
  const store = useRef<LayoutBookStore | null>(null);
  const latest = useRef(state);
  latest.current = state;
  const layout = state.book.current;
  const detached = isDetachedWindow() || detachedBrowser;

  // The host asks before tabs with unsaved files or live shells close (see `CloseGuard`).
  const guard = useRef<CloseGuard | null>(null);
  const setCloseGuard = useCallback((next: CloseGuard | null) => {
    guard.current = next;
  }, []);

  const dispatch = useCallback((action: LayoutAction) => {
    const hasSettings = allTabs(latest.current.book.current).some(tab => tab.kind === 'settings');
    const run = () => {
      if (hasSettings && DISCARDING.includes(action.type)) leaveSettings(() => dispatchRaw(action));
      else dispatchRaw(action);
    };
    const tabs = closing(latest.current.book.current, action);
    if (tabs.length && guard.current?.(tabs, run)) return;
    run();
  }, []);

  const closeTab = useCallback((tab: Tab) => {
    const close = () => dispatchRaw({type: 'closeTab', tab: tab.id});
    // Settings keeps its dirty-form guard: closing the tab asks first, with the same discard dialog.
    const run = () => (tab.kind === 'settings' ? leaveSettings(close) : close());
    if (guard.current?.([tab], run)) return;
    run();
  }, []);

  // Restore this principal's layout.
  useEffect(() => {
    if (!ownerId) return;
    const book = new LayoutBookStore(ownerId, localStorage, undefined, {resolveRef: resolveTabRef});
    store.current = book;
    let next = book.read();
    const route = workspaceRoute();
    const asked = routeInitialLayout(route);
    const kind = asked ? kindForWindowPanel(asked as WindowPanel) : null;
    if (detached) {
      next = {...next, current: oneTabLayout(kind ?? 'chat')};
    } else if (kind) {
      next = reduceLayout(createState(next), {type: 'openTab', kind}).book;
    }
    if (innerWidth <= 850) next = {...next, current: {...next.current, sideBar: {...next.current.sideBar, visible: false}}};
    dispatchRaw({type: 'hydrate', book: next});
    setRestoredFor(ownerId);
  }, [ownerId]);

  // Write the book back shortly after each change, and at once when the page goes away or this engine is swapped out.
  const unsaved = useRef<Book | null>(null);
  const flush = useCallback(() => {
    if (unsaved.current && store.current && !detached) store.current.write(unsaved.current);
    unsaved.current = null;
  }, [detached]);
  useEffect(() => {
    if (!ownerId || restoredFor !== ownerId || detached) return;
    unsaved.current = state.book;
    const timer = setTimeout(flush, 160);
    return () => clearTimeout(timer);
  }, [state.book, restoredFor, ownerId, detached, flush]);
  useEffect(() => {
    window.addEventListener('pagehide', flush);
    return () => {
      window.removeEventListener('pagehide', flush);
      flush();
    };
  }, [flush]);

  // A narrow window hides the side bar; a wide one shows it again.
  useEffect(() => {
    const narrow = matchMedia('(max-width:850px)');
    const changed = () => {
      if (ownerNow.current) dispatchRaw({type: 'setSideBar', visible: !narrow.matches});
    };
    narrow.addEventListener('change', changed);
    return () => narrow.removeEventListener('change', changed);
  }, []);

  const focused = focusedTab(layout);
  const open = useCallback((kind: TabKind, ref?: string) => dispatch({type: 'openTab', kind, ref}), [dispatch]);

  function chooseLayout(value: Layout) {
    setWorkspaceState('agent');
    setManagerScope('workspace');
    const kind = KIND_OF_LAYOUT[value];
    if (kind) open(kind);
    else dispatch({type: 'toggleMaximize'});
  }
  function openSurface(surface: Surface) {
    setWorkspace('agent');
    open(KIND_OF_SURFACE[surface]);
  }
  function focusPanel(panel: DockPanel) {
    setWorkspace('agent');
    const kind = KIND_OF_PANEL[panel];
    if (!kind) return;
    open(kind);
    setTimeout(() => {
      const element = document.querySelector<HTMLElement>('.dock-' + panel);
      (element?.querySelector<HTMLElement>('textarea,input,[tabindex="0"],button') || element)?.focus();
    }, 350);
  }
  function showSideBarView(view: SideBarView) {
    setWorkspaceState('agent');
    dispatchRaw({type: 'setSideBarView', view});
    dispatchRaw({type: 'setSideBar', visible: true});
    if (view === 'data' || view === 'studio') open(view);
  }
  function openManager(page: string, ref?: string) {
    if (!page) return true;
    const target = managerTab(page);
    if (!target) return false;
    setWorkspaceState('agent');
    open(target.kind, ref ?? target.ref);
    return true;
  }
  function currentFocus() {
    const book = latest.current.book.current;
    return {book, tab: focusedTab(book)};
  }

  const setLeft: TreeController['setLeft'] = value => {
    const visible = typeof value === 'function' ? value(latest.current.book.current.sideBar.visible) : value;
    dispatchRaw({type: 'setSideBar', visible});
  };
  const setSidebarWidth: TreeController['setSidebarWidth'] = value => {
    const size = typeof value === 'function' ? value(latest.current.book.current.sideBar.size) : value;
    dispatchRaw({type: 'setSideBarSize', size});
  };
  const startSidebarResize = useSidebarResize({width: layout.sideBar.size, setWidth: setSidebarWidth, onStart: () => {}});

  const {requestQuickOpen, requestBrowserCommand} = useSurfaceRequests({
    quickReady: focused?.kind === 'file',
    browserReady: focused?.kind === 'browser',
    signature: [focused?.id, layout.panel.visible, layout.maximized, layout.root && allGroups(layout).length, palette].join('|'),
  });

  return {
    engine: 'tree',
    ready: !!ownerId && restoredFor === ownerId,
    left: layout.sideBar.visible,
    setLeft,
    sidebarWidth: layout.sideBar.size,
    setSidebarWidth,
    sidebarHover: false,
    // The new layout has no floating side bar: choosing it pins or collapses.
    setSidebarBehavior: value => dispatchRaw({type: 'setSideBar', visible: value === 'pinned'}),
    startSidebarResize,
    mode: focused && (focused.kind === 'file' || focused.kind === 'diff') ? 'coding' : 'chat',
    headerLayout: focused?.kind === 'chat' ? 'chat' : 'workbench',
    surface: (focused && windowPanelForKind(focused.kind)) || 'chat',
    chooseLayout,
    openSurface,
    showSideBarView,
    focusPanel,
    openManager,
    revealConversation: () => focusPanel('conversation'),
    arrive: (panel, hasBuffers) => {
      void hasBuffers;
      chooseLayout(panel);
    },
    saved: state.book.named.map(item => {
      const tabs = allTabs(item.layout).length;
      return {id: item.id, name: item.name, detail: tabs + (tabs === 1 ? ' tab' : ' tabs')};
    }),
    saveLayout: name => {
      const problem = checkLayoutName(name, latest.current.book.named);
      if (problem) report(new Error(problem));
      else dispatchRaw({type: 'saveNamed', name});
    },
    restoreSaved: id => {
      if (!latest.current.book.named.some(item => item.id === id)) return false;
      dispatch({type: 'loadNamed', id});
      return true;
    },
    removeSaved: id => dispatchRaw({type: 'deleteNamed', id}),
    resetLayout: () => dispatch({type: 'resetToDefault'}),
    undoLayout: () => dispatchRaw({type: 'undo'}),
    toggleFocus: () => dispatch({type: 'toggleMaximize'}),
    canRestoreFocus: () => !!latest.current.book.current.maximized,
    rememberLayout: () => {},
    commands: {
      toggleSidePanel: () => {},
      toggleBottomPanel: () => dispatchRaw({type: 'toggleBottomPanel'}),
      panelCommand: (command: PanelCommand) => {
        const {tab} = currentFocus();
        if (command === 'close' && tab) closeTab(tab);
        else if (command === 'reopen') dispatchRaw({type: 'reopenTab'});
        else if (command === 'next') dispatchRaw({type: 'cycleTab', step: 1});
        else if (command === 'previous') dispatchRaw({type: 'cycleTab', step: -1});
      },
      splitGroup: dir => {
        const {book} = currentFocus();
        const group = isEditorGroup(book, book.focus) ? book.focus : editorGroups(book.root)[0].id;
        dispatchRaw({type: 'splitGroup', group, dir, side: 'after'});
      },
      focusGroup: index => {
        const {book} = currentFocus();
        const group = editorGroups(book.root)[index];
        if (!group) return;
        dispatchRaw({type: 'focusGroupAt', index});
        focusContent(group.tabs.find(tab => tab.id === group.active));
      },
      maximizeGroup: () => dispatchRaw({type: 'toggleMaximize'}),
      moveTabToOtherGroup: () => {
        const {book, tab} = currentFocus();
        if (!tab) return;
        const groups = allGroups(book).filter(group => group.id !== book.panel.group.id || book.panel.visible);
        const from = groups.findIndex(group => group.tabs.some(item => item.id === tab.id));
        const to = groups[(from + 1) % groups.length];
        if (to && groups.length > 1) dispatchRaw({type: 'moveTab', tab: tab.id, group: to.id});
      },
      // The terminal host starts the shell and opens its tab; it may not be mounted yet, so the request waits for it.
      newTerminal: () => requestNewTerminal(),
    },
    requestQuickOpen,
    requestBrowserCommand,
    tree: {
      layout,
      dispatch,
      openCount,
      closeTab,
      dirty,
      setCloseGuard,
      setDirty: (tab, value) =>
        setDirtyTabs(previous => {
          if (previous.has(tab) === value) return previous;
          const next = new Set(previous);
          if (value) next.add(tab);
          else next.delete(tab);
          return next;
        }),
    },
  };
}
