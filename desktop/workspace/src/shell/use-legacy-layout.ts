import {useEffect, useRef, useState} from 'react';
import {
  focusDockMap,
  layouts,
  panels,
  workspaceSurface,
  type DockMap,
  type DockPanel,
} from '../lib/docking';
import {LayoutPresetStore, defaultLayout, layoutSnapshot, type NamedLayout} from '../lib/layout-presets';
import {readSidebarPreference, saveSidebarPreference} from '../lib/sidebar-preferences';
import {useLayout, type Layout} from '../lib/use-layout';
import type {PanelCommand, Surface} from './commands';
import type {LayoutDeps, LegacyController} from './layout-controller';
import {routeInitialLayout, workspaceRoute} from './routes';
import {useSidebarResize} from './use-sidebar-resize';
import {useSurfaceRequests} from './use-surface-requests';

/** A snapshot of the dock for Undo, Focus mode and the saved layouts. */
type LayoutSnapshot = {
  layout: Layout;
  dock: DockMap;
  left: boolean;
  right: boolean;
  bottom: boolean;
  dockGeometry: {left: number; split: number; bottom: number};
  sidebarWidth: number;
  rightWidth: number;
};

/**
 * The old layout engine: seven single-instance panels placed in regions (`left`, `main`, `split`, `right`, `bottom`),
 * eight presets, Focus mode and a 20-step layout undo. This is the shell's layout code as it was before the layout
 * controller was extracted; it is moved, not changed.
 */
export function useLegacyLayout(deps: LayoutDeps): LegacyController {
  const {ownerId, ownerNow, setWorkspace, setWorkspaceState, setManagerScope, report, theme, setTheme, palette} = deps;
  const {
    layout,
    dock,
    dockGeometry,
    left,
    right,
    bottom,
    sidebarWidth,
    rightWidth,
    sidebarHover,
    setLayout,
    setDock,
    setDockGeometry,
    setLeft,
    setRight,
    setBottom,
    setSidebarWidth,
    setRightWidth,
    setSidebarHover,
  } = useLayout();
  /** Panels that were ever shown stay mounted, so a terminal or an editor keeps its state when hidden. */
  const [visited, setVisited] = useState<Set<string>>(new Set(['conversation']));
  const [panelActivation, setPanelActivation] = useState<{panel: DockPanel; nonce: number}>();
  const [sidebarOwner, setSidebarOwner] = useState('');
  const [namedLayouts, setNamedLayouts] = useState<NamedLayout[]>([]);
  const [layoutOwner, setLayoutOwner] = useState('');
  const [visiblePanels, setVisiblePanels] = useState<DockPanel[]>(['conversation']);
  const layoutBook = useRef<LayoutPresetStore | null>(null);
  const undo = useRef<LayoutSnapshot[]>([]);
  const focusRestore = useRef<LayoutSnapshot | null>(null);

  function rememberLayout() {
    undo.current = [
      ...undo.current.slice(-19),
      {
        layout,
        dock: {...dock},
        left,
        right,
        bottom,
        dockGeometry: {...dockGeometry},
        sidebarWidth,
        rightWidth,
      },
    ];
  }
  function currentLayout(): LayoutSnapshot {
    return {
      layout,
      dock: {...dock},
      left,
      right,
      bottom,
      dockGeometry: {...dockGeometry},
      sidebarWidth,
      rightWidth,
    };
  }
  function restoreLayout(snapshot: LayoutSnapshot) {
    setLayout(snapshot.layout);
    setDock(snapshot.dock);
    setLeft(snapshot.left && !sidebarHover && innerWidth > 850);
    setRight(snapshot.right);
    setBottom(snapshot.bottom);
    setDockGeometry(snapshot.dockGeometry);
    setSidebarWidth(snapshot.sidebarWidth);
    setRightWidth(snapshot.rightWidth);
  }
  function changeDock(value: DockMap) {
    rememberLayout();
    if (panels.some(panel => value[panel] === 'right' && dock[panel] !== 'right')) setRight(true);
    if (panels.some(panel => value[panel] === 'bottom' && dock[panel] !== 'bottom')) setBottom(true);
    setDock(value);
  }
  function chooseLayout(value: Layout, reveal?: DockPanel) {
    setWorkspaceState('agent');
    setManagerScope('workspace');
    const book = layoutBook.current;
    if (book && layout !== 'focus') book.remember(currentLayout());
    focusRestore.current = null;
    rememberLayout();
    let snapshot =
      value === 'focus'
        ? {...currentLayout(), layout: value, dock: layouts.focus}
        : book?.arrangement(value) || defaultLayout(value);
    if (reveal) {
      const region =
        snapshot.dock[reveal] === 'hidden'
          ? Object.values(snapshot.dock).includes('main')
            ? 'split'
            : 'main'
          : snapshot.dock[reveal];
      snapshot = {
        ...snapshot,
        dock: {...snapshot.dock, [reveal]: region},
        right: snapshot.right || region === 'right',
        bottom: snapshot.bottom || region === 'bottom',
      };
    }
    restoreLayout(snapshot);
  }
  function resetLayout() {
    const value = layout === 'focus' ? focusRestore.current?.layout || 'chat' : layout;
    rememberLayout();
    focusRestore.current = null;
    restoreLayout(
      layoutBook.current?.reset(value === 'focus' ? 'chat' : value) ||
        defaultLayout(value === 'focus' ? 'chat' : value),
    );
  }
  function saveLayout(name: string) {
    try {
      layoutBook.current?.save(name, currentLayout());
      setNamedLayouts(layoutBook.current?.read().named || []);
    } catch (error) {
      report(error);
    }
  }
  function restoreSaved(id: string) {
    const saved = layoutBook.current?.read().named.find(item => item.id === id);
    if (!saved) return false;
    rememberLayout();
    focusRestore.current = null;
    restoreLayout(saved.snapshot);
    return true;
  }
  function removeSaved(id: string) {
    layoutBook.current?.remove(id);
    setNamedLayouts(layoutBook.current?.read().named || []);
  }
  function undoLayout() {
    const snapshot = undo.current.pop();
    if (snapshot) {
      focusRestore.current = null;
      setLeft(snapshot.left && innerWidth > 850);
      restoreLayout(snapshot);
    }
  }
  function openSurface(surface: Surface) {
    setWorkspace('agent');
    const panel: DockPanel = surface === 'editor' ? 'editor' : surface;
    if (panel === 'editor') {
      chooseLayout('workbench', 'editor');
      setPanelActivation({panel, nonce: performance.now()});
      return;
    }
    rememberLayout();
    const next = {...dock};
    if (!Object.values(next).includes('main')) next.conversation = 'main';
    if (next[panel] === 'hidden') next[panel] = 'right';
    setDock(next);
    if (next[panel] === 'right') setRight(true);
    if (next[panel] === 'bottom') setBottom(true);
    setVisited(old => new Set([...old, panel]));
    if (next[panel] === 'right' && rightWidth < 480)
      setRightWidth(Math.min(800, Math.max(480, Math.round(innerWidth * 0.5))));
    setPanelActivation({panel, nonce: performance.now()});
  }
  function focusPanel(target: DockPanel) {
    if (!panels.includes(target)) return;
    setWorkspace('agent');
    if (dock[target] === 'hidden') {
      const region =
        target === 'conversation' ? 'right' : Object.values(dock).includes('main') ? 'split' : 'main';
      changeDock({...dock, [target]: region});
      if (region === 'right') setRight(true);
    } else if (dock[target] === 'right') setRight(true);
    else if (dock[target] === 'bottom') setBottom(true);
    setPanelActivation({panel: target, nonce: performance.now()});
    setTimeout(() => {
      const element = document.querySelector<HTMLElement>('.dock-' + target);
      (element?.querySelector<HTMLElement>('textarea,input,[tabindex="0"],button') || element)?.focus();
    }, 350);
  }
  function toggleFocus() {
    if (focusRestore.current) {
      setLeft(focusRestore.current.left && innerWidth > 850);
      restoreLayout(focusRestore.current);
      focusRestore.current = null;
      return;
    }
    focusRestore.current = {
      layout,
      dock: {...dock},
      left,
      right,
      bottom,
      dockGeometry: {...dockGeometry},
      sidebarWidth,
      rightWidth,
    };
    rememberLayout();
    const panel =
      document.activeElement?.closest('.dock-pane')?.getAttribute('data-panel') ||
      Object.keys(dock).find(panel => dock[panel as keyof DockMap] === 'main') ||
      'conversation';
    setDock(focusDockMap(panels.includes(panel as DockPanel) ? (panel as DockPanel) : 'conversation'));
    setLayout('focus');
    setLeft(false);
    setRight(false);
    setBottom(false);
  }
  function setSidebarBehavior(value: 'pinned' | 'collapsed' | 'hover') {
    setSidebarHover(value === 'hover');
    setLeft(value === 'pinned');
    if (ownerId && sidebarOwner === ownerId) saveSidebarPreference(ownerId, {hover: value === 'hover', open: value === 'pinned'});
  }

  // A narrow window hides the sidebar; a wide one brings back what the person last chose.
  useEffect(() => {
    const narrow = matchMedia('(max-width:850px)');
    const changed = () => {
      if (narrow.matches) {
        setLeft(false);
      } else {
        const owner = ownerNow.current;
        if (owner) {
          const preference = readSidebarPreference(owner);
          setLeft(preference ? preference.open && !preference.hover : true);
        }
      }
    };
    narrow.addEventListener('change', changed);
    return () => narrow.removeEventListener('change', changed);
  }, []);

  // Restore this principal's layout: the last arrangement, the sidebar preference and the saved layouts.
  useEffect(() => {
    if (!ownerId) return;
    const book = new LayoutPresetStore(ownerId);
    layoutBook.current = book;
    const stored = localStorage.getItem('termx-layout:' + ownerId);
    if (stored) {
      try {
        const value = JSON.parse(stored),
          legacy = layoutSnapshot(value);
        if (legacy && !book.read().last) book.remember(legacy);
        setTheme(value.theme === 'light' ? 'light' : value.theme === 'system' ? 'system' : 'dark');
      } catch {}
    }
    const route = workspaceRoute();
    const saved = book.read();
    restoreLayout(book.arrangement(routeInitialLayout(route) || saved.last || 'chat'));
    const navigation = readSidebarPreference(ownerId);
    setSidebarHover(navigation?.hover || false);
    setLeft(
      (navigation?.open ?? book.arrangement(saved.last || 'chat').left) &&
        !navigation?.hover &&
        innerWidth > 850,
    );
    setSidebarOwner(ownerId);
    setNamedLayouts(saved.named);
    setLayoutOwner(ownerId);
  }, [ownerId]);

  useEffect(() => {
    setVisited(
      old =>
        new Set([
          ...old,
          ...Object.entries(dock)
            .filter(([, region]) => region !== 'hidden')
            .map(([panel]) => panel),
        ]),
    );
  }, [dock]);

  useEffect(() => {
    if (innerWidth > 850 && ownerId && sidebarOwner === ownerId)
      saveSidebarPreference(ownerId, {hover: sidebarHover, open: left});
  }, [sidebarOwner, sidebarHover, left, ownerId]);

  useEffect(() => {
    if (ownerId && layoutOwner === ownerId) {
      if (layout !== 'focus') layoutBook.current?.remember(currentLayout());
      localStorage.setItem(
        'termx-layout:' + ownerId,
        JSON.stringify({layout, left, right, bottom, theme, sidebarWidth, rightWidth, dock, dockGeometry}),
      );
    }
  }, [layoutOwner, layout, left, right, bottom, theme, sidebarWidth, rightWidth, dock, dockGeometry, ownerId]);

  const {requestQuickOpen, requestBrowserCommand} = useSurfaceRequests({
    quickReady: layout === 'workbench',
    browserReady: layout === 'browser' && visited.has('browser'),
    signature: [layout, [...visited].join(','), JSON.stringify(dock), palette].join('|'),
  });
  const startSidebarResize = useSidebarResize({width: sidebarWidth, setWidth: setSidebarWidth, onStart: rememberLayout});

  return {
    engine: 'legacy',
    ready: !!ownerId && layoutOwner === ownerId,
    left,
    setLeft,
    sidebarWidth,
    setSidebarWidth,
    sidebarHover,
    setSidebarBehavior,
    startSidebarResize,
    mode: ['workbench', 'review', 'focus'].includes(layout) ? 'coding' : 'chat',
    headerLayout: layout,
    surface: workspaceSurface(layout, dock),
    chooseLayout,
    openSurface,
    focusPanel,
    showSideBarView: view => {
      if (view === 'explorer' || view === 'search') chooseLayout('workbench');
      else if (view === 'scm') openSurface('review');
      else chooseLayout('chat');
    },
    openManager: () => false,
    revealConversation: () => {
      setRight(true);
      focusPanel('conversation');
    },
    arrive: (panel, hasBuffers) => {
      if (hasBuffers) setVisited(old => new Set([...old, 'editor']));
      chooseLayout(panel);
    },
    saved: namedLayouts.map(item => ({id: item.id, name: item.name, detail: item.snapshot.layout})),
    saveLayout,
    restoreSaved,
    removeSaved,
    resetLayout,
    undoLayout,
    toggleFocus,
    canRestoreFocus: () => !!focusRestore.current,
    rememberLayout,
    commands: {
      toggleSidePanel: () => {
        rememberLayout();
        setRight(value => !value);
      },
      toggleBottomPanel: () => {
        rememberLayout();
        setBottom(value => !value);
      },
      panelCommand: (command: PanelCommand) =>
        window.dispatchEvent(new CustomEvent('termx-panel-command', {detail: {command}})),
      // Tab groups do not exist in the old dock; these commands are hidden there.
      splitGroup: () => {},
      focusGroup: () => {},
      maximizeGroup: () => {},
      moveTabToOtherGroup: () => {},
      newTerminal: () => {},
    },
    requestQuickOpen,
    requestBrowserCommand,
    legacy: {
      layout,
      dock,
      dockGeometry,
      right,
      bottom,
      rightWidth,
      visited,
      visiblePanels,
      setVisiblePanels,
      panelActivation,
      setRightWidth,
      setDockGeometry,
      changeDock,
    },
  };
}
