import type {Dispatch, PointerEvent as ReactPointerEvent, SetStateAction} from 'react';
import type {DockGeometry, DockMap, DockPanel} from '../lib/docking';
import type {Layout as TreeLayout, SideBarView, Tab} from '../lib/layout/model';
import type {LayoutAction} from '../lib/layout/reduce';
import type {ShellEngine} from '../lib/shell-engine';
import type {ThemePreference} from '../lib/theme';
import type {Layout} from '../lib/use-layout';
import type {WindowPanel} from '../lib/window-journal';
import type {WorkspaceMode} from '../lib/workspace-mode';
import type {PanelCommand, Surface} from './commands';

/**
 * The layout controller: everything the shell needs to know about, and to ask of, the panel arrangement.
 *
 * There are two implementations behind one interface. `use-legacy-layout.ts` is the old dock (seven panels, eight presets);
 * `use-tree-layout.ts` is the tab-group layout. The shell is written against `LayoutController` and never branches on the
 * engine except to hand the right workspace component the right view (`legacy` or `tree`).
 */

/** A saved layout as the Saved layouts dialog lists it. */
export type SavedLayoutRow = {id: string; name: string; detail: string};

/** What both implementations take from the shell. */
export interface LayoutDeps {
  /** The signed-in principal, or `undefined` before sign-in. The layout is restored when this changes. */
  ownerId: string | undefined;
  /** The latest principal, for listeners that outlive a render. */
  ownerNow: {current: string | undefined};
  locked: boolean;
  workspace: WorkspaceMode;
  /** Switch workspace (leaving unsaved settings first). */
  setWorkspace: (mode: WorkspaceMode) => void;
  setWorkspaceState: Dispatch<SetStateAction<WorkspaceMode>>;
  setManagerScope: Dispatch<SetStateAction<'global' | 'workspace'>>;
  report: (reason: unknown) => void;
  theme: ThemePreference;
  setTheme: (theme: ThemePreference) => void;
  /** The command palette is open; a surface command waits for it to close before it looks for its surface. */
  palette: boolean;
}

export interface LayoutCommon {
  engine: ShellEngine;
  /** The stored layout for this principal has been restored. */
  ready: boolean;

  // The side bar
  left: boolean;
  setLeft: Dispatch<SetStateAction<boolean>>;
  sidebarWidth: number;
  setSidebarWidth: Dispatch<SetStateAction<number>>;
  sidebarHover: boolean;
  setSidebarBehavior: (value: 'pinned' | 'collapsed' | 'hover') => void;
  /** Start dragging the side bar's edge. */
  startSidebarResize: (event: ReactPointerEvent<HTMLDivElement>) => void;

  // What the chrome reads
  /** `coding` when the focused surface is files or changes, else `chat`. */
  mode: 'coding' | 'chat';
  /** The name the header uses to decide whether to offer "Back to chat". */
  headerLayout: Layout;
  /** The surface a detached window shows, for the window journal and Detach workspace. */
  surface: WindowPanel;

  // Navigation
  chooseLayout: (layout: Layout, reveal?: DockPanel) => void;
  openSurface: (surface: Surface) => void;
  /**
   * Show a side bar view (Agents, Explorer, Search, Source control, Sites, Data, Studio). The tab layout switches its side
   * bar; the old dock has no views, so it shows the surface that matches (Files, Changes or the chat).
   */
  showSideBarView: (view: SideBarView) => void;
  focusPanel: (panel: DockPanel) => void;
  /**
   * Open a manager or settings page. The tab layout opens it as a tab and returns `true`; the old dock returns `false`
   * and the shell shows its overlay as before.
   */
  openManager: (page: string, ref?: string) => boolean;
  /** Show the conversation so attached context can be seen (the old dock opens its side panel for it). */
  revealConversation: () => void;
  /** A detached window arrived back in this one: show its surface. `hasBuffers` means it carried unsaved files. */
  arrive: (panel: WindowPanel, hasBuffers: boolean) => void;

  // Saved layouts, undo, reset
  saved: SavedLayoutRow[];
  saveLayout: (name: string) => void;
  restoreSaved: (id: string) => boolean;
  removeSaved: (id: string) => void;
  resetLayout: () => void;
  undoLayout: () => void;
  /** Focus mode in the old dock, maximize group in the new layout. */
  toggleFocus: () => void;
  /** Escape should leave focus mode or restore a maximized group. */
  canRestoreFocus: () => boolean;
  /** Remember the layout for Undo before a continuous change begins. */
  rememberLayout: () => void;

  // Commands that differ by engine
  commands: {
    toggleSidePanel: () => void;
    toggleBottomPanel: () => void;
    panelCommand: (command: PanelCommand) => void;
    splitGroup: (dir: 'row' | 'column') => void;
    focusGroup: (index: number) => void;
    maximizeGroup: () => void;
    moveTabToOtherGroup: () => void;
    newTerminal: () => void;
  };

  /** Queue a surface command that runs once its surface is on screen. */
  requestQuickOpen: () => void;
  requestBrowserCommand: (command: 'browserNew' | 'browserClose' | 'browserReopen') => void;
}

/** The old dock: seven panels placed in regions, eight presets. */
export interface LegacyView {
  layout: Layout;
  dock: DockMap;
  dockGeometry: DockGeometry;
  right: boolean;
  bottom: boolean;
  rightWidth: number;
  /** Panels that were ever shown stay mounted. */
  visited: Set<string>;
  visiblePanels: DockPanel[];
  setVisiblePanels: Dispatch<SetStateAction<DockPanel[]>>;
  panelActivation: {panel: DockPanel; nonce: number} | undefined;
  setRightWidth: Dispatch<SetStateAction<number>>;
  setDockGeometry: Dispatch<SetStateAction<DockGeometry>>;
  changeDock: (map: DockMap) => void;
}

/**
 * Asked before tabs are closed. It returns true when it took over (it asks the person, then calls `proceed`), and false
 * when the tabs can close now. File tabs with unsaved edits ask first; terminal tabs end their shell.
 */
export type CloseGuard = (tabs: Tab[], proceed: () => void) => boolean;

/** The tab-group layout. */
export interface TreeView {
  layout: TreeLayout;
  dispatch: (action: LayoutAction) => void;
  /** Close a tab, asking first when it has unsaved edits (Settings). */
  closeTab: (tab: Tab) => void;
  /** Tabs with unsaved edits. */
  dirty: ReadonlySet<string>;
  setDirty: (tab: string, dirty: boolean) => void;
  setCloseGuard: (guard: CloseGuard | null) => void;
}

export type LegacyController = LayoutCommon & {engine: 'legacy'; legacy: LegacyView};
export type TreeController = LayoutCommon & {engine: 'tree'; tree: TreeView};
export type LayoutController = LegacyController | TreeController;
