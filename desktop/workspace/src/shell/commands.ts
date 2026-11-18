import type {DockPanel} from '../lib/docking';
import type {ChangeStep, ScmCommand} from '../features/scm/scm-bus';
import type {SideBarView} from '../lib/layout/model';
import {settingsCategories} from '../lib/settings-categories';
import type {Layout} from '../lib/use-layout';
import type {ShellEngine} from '../lib/shell-engine';
import type {WorkspaceMode} from '../lib/workspace-mode';

/**
 * The one command registry.
 *
 * The command palette, the keybinding settings, the shortcut dispatcher and (later) the title bar and native menus
 * all read this list. A command is data plus a `run` that talks to the shell only through `CommandActions`, so
 * adding a command never means editing a switch or a second table.
 */

export type Surface = 'review' | 'browser' | 'terminal' | 'editor' | 'computer' | 'artifacts';
export type PanelCommand = 'close' | 'reopen' | 'next' | 'previous';
export type BrowserCommand = 'browserNew' | 'browserClose' | 'browserReopen';

/** What the shell offers a command. The shell builds one of these per render; commands never hold state. */
export interface CommandActions {
  setManager: (value: string) => void;
  setWorkspace: (mode: WorkspaceMode) => void;
  openWorkflows: () => void;
  openMainAgent: () => void;
  openNewChat: () => void;
  refreshConversations: () => void;
  lockWorkspace: () => void;
  stopHost: () => void;
  chooseLayout: (layout: Layout) => void;
  openSurface: (surface: Surface) => void;
  focusPanel: (panel: DockPanel) => void;
  quickOpen: () => void;
  browserCommand: (command: BrowserCommand, explicit: boolean) => void;
  panelCommand: (command: PanelCommand) => void;
  toggleFullscreen: () => void;
  openSavedLayouts: () => void;
  toggleSidebar: () => void;
  toggleSidePanel: () => void;
  toggleBottomPanel: () => void;
  togglePalette: () => void;
  toggleFocusMode: () => void;
  undoLayout: () => void;
  resetLayout: () => void;
  /** Tab groups (the new layout): split the focused group, focus a group by position (0-based), maximize, move, new terminal. */
  splitGroup: (dir: 'row' | 'column') => void;
  focusGroup: (index: number) => void;
  maximizeGroup: () => void;
  moveTabToOtherGroup: () => void;
  newTerminal: () => void;
  /** Show a side bar view. The old dock has no views and shows the surface that matches. */
  showView: (view: SideBarView) => void;
  openNewChatWithOptions: () => void;
  /** The agent surfaces (new layout): the New CLI session dialog, the CLI tab that needs a person, the active page as a saved site, the host health popover. */
  newCliSession: () => void;
  focusCliSession: () => void;
  addCurrentSite: () => void;
  showHostStatus: () => void;
  detachWorkspace: () => void;
  returnToMain: () => void;
  recoverWindows: () => void;
  /** Register an existing folder as a project (the Add project dialog, which also offers Open project for a known folder). */
  openProject: () => void;
  /** Save the focused file tab, or the visible one when focus is elsewhere. */
  saveFile: () => void;
  /** Source control (the new layout): commit, stage everything or refresh in the view, and step between changes in the focused diff. */
  scmCommand: (command: ScmCommand) => void;
  changeStep: (step: ChangeStep) => void;
}

export type CommandSource = 'shortcut' | 'palette';
export interface CommandInvocation {
  source: CommandSource;
  /** The keyboard focus is inside a terminal when the command is invoked. */
  terminalFocused: boolean;
}

/** Facts a command's `when` is judged against. */
export interface CommandContext {
  workspace: WorkspaceMode;
  hostAdmin: boolean;
  /** The layout engine in force: the old dock or the tab-group tree. Absent means the old dock. */
  engine?: ShellEngine;
  /** This window is a detached surface that can return to the main workspace. */
  detached?: boolean;
  /** The native desktop shell is running (the browser keeps a recovery journal for unsaved windows instead). */
  native?: boolean;
}

/** `agent` commands act on the Agent workspace's panels; they are hidden and inert in Studio and Data. */
export type When = 'agent' | 'host-admin' | 'detached' | 'web';

export type CommandCategory =
  | 'Go'
  | 'View'
  | 'Layout'
  | 'Chat'
  | 'Source control'
  | 'Terminal'
  | 'Browser'
  | 'Window'
  | 'Settings'
  | 'Workspace'
  | 'Security';

export interface CommandSpec {
  title: string;
  category: CommandCategory;
  /** Stored binding grammar, for example `Mod+Shift+P` or the chord `Mod+K Z`. */
  defaultKeys?: string;
  when?: When;
  /** The command exists only in this layout engine: it is hidden from the palette and its key is not live elsewhere. */
  engine?: ShellEngine;
  /** The default key is live only in this layout engine. The command stays in the palette in both. */
  keysIn?: ShellEngine;
  /** Extra palette search terms. */
  keywords?: string;
  /** Leave a manager or settings page (back to the workspace) before running. */
  leavesManager?: boolean;
  /** Still runs while a terminal has focus. Every other key goes to the shell. */
  terminal?: boolean;
  run: (actions: CommandActions, invocation: CommandInvocation) => void;
}

export interface Command extends CommandSpec {
  id: string;
}

type KeyedSpec = CommandSpec & {defaultKeys: string};

function keyed<const T extends Record<string, KeyedSpec>>(specs: T): T {
  return specs;
}

const specs = keyed({
  chat: {
    title: 'Chat',
    category: 'View',
    defaultKeys: 'Mod+1',
    keysIn: 'legacy',
    leavesManager: true,
    run: a => a.chooseLayout('chat'),
  },
  workbench: {
    title: 'Workbench',
    category: 'View',
    defaultKeys: 'Mod+2',
    keysIn: 'legacy',
    leavesManager: true,
    run: a => a.chooseLayout('workbench'),
  },
  browser: {
    title: 'Browser',
    category: 'View',
    defaultKeys: 'Mod+3',
    keysIn: 'legacy',
    leavesManager: true,
    run: a => a.openSurface('browser'),
  },
  review: {
    title: 'Review file changes',
    category: 'View',
    defaultKeys: 'Mod+4',
    leavesManager: true,
    run: a => a.openSurface('review'),
  },
  computer: {
    title: 'Computer',
    category: 'View',
    defaultKeys: 'Mod+5',
    leavesManager: true,
    run: a => a.openSurface('computer'),
  },
  artifacts: {
    title: 'Artifacts canvas',
    category: 'View',
    defaultKeys: 'Mod+6',
    leavesManager: true,
    run: a => a.openSurface('artifacts'),
  },
  terminal: {
    title: 'Terminal tab',
    category: 'Terminal',
    defaultKeys: 'Mod+7',
    run: a => a.openSurface('terminal'),
  },
  closeTab: {
    title: 'Close current tab',
    category: 'Layout',
    defaultKeys: 'Mod+W',
    when: 'agent',
    run: a => a.panelCommand('close'),
  },
  reopenTab: {
    title: 'Reopen closed tab',
    category: 'Layout',
    defaultKeys: 'Mod+Shift+T',
    when: 'agent',
    run: a => a.panelCommand('reopen'),
  },
  saveFile: {
    // The editor binds the same key itself and handles it first; this entry makes File > Save and the key work from anywhere.
    title: 'Save file',
    category: 'Workspace',
    defaultKeys: 'Mod+S',
    when: 'agent',
    run: a => a.saveFile(),
  },
  fullscreen: {
    title: 'Toggle full screen',
    category: 'Window',
    defaultKeys: 'Ctrl+Meta+F',
    run: a => a.toggleFullscreen(),
  },
  savedLayouts: {
    title: 'Saved layout presets',
    category: 'Layout',
    defaultKeys: 'Mod+Alt+L',
    when: 'agent',
    run: a => a.openSavedLayouts(),
  },
  left: {
    title: 'Toggle left panel',
    category: 'Layout',
    defaultKeys: 'Mod+B',
    terminal: true,
    run: a => a.toggleSidebar(),
  },
  right: {
    title: 'Toggle right panel',
    category: 'Layout',
    defaultKeys: 'Mod+Alt+B',
    when: 'agent',
    engine: 'legacy',
    terminal: true,
    run: a => a.toggleSidePanel(),
  },
  bottom: {
    title: 'Toggle bottom panel',
    category: 'Layout',
    defaultKeys: 'Mod+J',
    when: 'agent',
    terminal: true,
    run: a => a.toggleBottomPanel(),
  },
  palette: {
    title: 'Workspace commands',
    category: 'Go',
    defaultKeys: 'Mod+Shift+P',
    terminal: true,
    run: a => a.togglePalette(),
  },
  quickFile: {
    title: 'Quick file open',
    category: 'Go',
    defaultKeys: 'Mod+P',
    run: a => a.quickOpen(),
  },
  newConversation: {
    title: 'New conversation',
    category: 'Chat',
    defaultKeys: 'Mod+Shift+N',
    run: a => a.openNewChat(),
  },
  focus: {
    title: 'Focus mode / restore layout',
    category: 'Layout',
    defaultKeys: 'Mod+K Z',
    when: 'agent',
    engine: 'legacy',
    run: a => a.toggleFocusMode(),
  },
  undoLayout: {
    title: 'Undo layout',
    category: 'Layout',
    defaultKeys: 'Mod+Alt+Z',
    when: 'agent',
    run: a => a.undoLayout(),
  },
  resetLayout: {
    title: 'Reset layout',
    category: 'Layout',
    defaultKeys: 'Mod+Alt+R',
    when: 'agent',
    run: a => a.resetLayout(),
  },
  focusConversation: {
    title: 'Focus conversation',
    category: 'View',
    defaultKeys: 'Mod+Alt+1',
    leavesManager: true,
    run: a => a.focusPanel('conversation'),
  },
  focusEditor: {
    title: 'Focus editor',
    category: 'View',
    defaultKeys: 'Mod+Alt+2',
    leavesManager: true,
    run: a => a.focusPanel('editor'),
  },
  focusBrowser: {
    title: 'Focus browser',
    category: 'View',
    defaultKeys: 'Mod+Alt+3',
    leavesManager: true,
    run: a => a.focusPanel('browser'),
  },
  focusComputer: {
    title: 'Focus computer',
    category: 'View',
    defaultKeys: 'Mod+Alt+5',
    leavesManager: true,
    run: a => a.focusPanel('computer'),
  },
  browserNew: {
    title: 'New built-in browser tab',
    category: 'Browser',
    defaultKeys: 'Mod+Alt+T',
    when: 'agent',
    run: (a, {source}) => a.browserCommand('browserNew', source === 'palette'),
  },
  browserClose: {
    title: 'Close built-in browser tab',
    category: 'Browser',
    defaultKeys: 'Mod+Alt+W',
    when: 'agent',
    run: (a, {source}) => a.browserCommand('browserClose', source === 'palette'),
  },
  browserReopen: {
    title: 'Reopen closed built-in browser tab',
    category: 'Browser',
    defaultKeys: 'Mod+Alt+Shift+T',
    when: 'agent',
    run: (a, {source}) => a.browserCommand('browserReopen', source === 'palette'),
  },
  voiceTalk: {
    title: 'Hold to talk, tap to start and stop',
    category: 'Chat',
    defaultKeys: 'Mod+Shift+M',
    when: 'agent',
    // The composer listens for this key itself; the shell only reserves it so it is never taken by another command.
    run: () => {},
  },
  focusTerminal: {
    title: 'Focus terminal',
    category: 'Terminal',
    defaultKeys: 'Ctrl+`',
    terminal: true,
    // A toggle: from inside a terminal it hands focus back to the conversation.
    run: (a, {terminalFocused}) =>
      terminalFocused ? a.focusPanel('conversation') : a.openSurface('terminal'),
  },
  explorer: {
    title: 'Show Explorer',
    category: 'View',
    defaultKeys: 'Mod+Shift+E',
    leavesManager: true,
    run: a => a.showView('explorer'),
  },
  search: {
    // The old editor's search tab has no external entry point, so there this opens the files surface.
    title: 'Search files',
    category: 'View',
    defaultKeys: 'Mod+Shift+F',
    leavesManager: true,
    run: a => a.showView('search'),
  },
  sourceControl: {
    title: 'Show Source control',
    category: 'View',
    defaultKeys: 'Ctrl+Shift+G',
    leavesManager: true,
    run: a => a.showView('scm'),
  },
  nextChange: {
    title: 'Go to next change',
    category: 'Source control',
    defaultKeys: 'F7',
    when: 'agent',
    engine: 'tree',
    keywords: 'diff hunk next change review',
    run: a => a.changeStep('next'),
  },
  previousChange: {
    title: 'Go to previous change',
    category: 'Source control',
    defaultKeys: 'Shift+F7',
    when: 'agent',
    engine: 'tree',
    keywords: 'diff hunk previous change review',
    run: a => a.changeStep('previous'),
  },
  settings: {
    title: 'Open Settings',
    category: 'Settings',
    defaultKeys: 'Mod+,',
    run: a => a.setManager('preferences'),
  },
  splitRight: {
    title: 'Split group right',
    category: 'Layout',
    defaultKeys: 'Mod+\\',
    when: 'agent',
    engine: 'tree',
    terminal: true,
    run: a => a.splitGroup('row'),
  },
  splitDown: {
    title: 'Split group down',
    category: 'Layout',
    defaultKeys: 'Mod+K Mod+\\',
    when: 'agent',
    engine: 'tree',
    run: a => a.splitGroup('column'),
  },
  focusGroup1: {
    title: 'Focus first group',
    category: 'Layout',
    defaultKeys: 'Mod+1',
    when: 'agent',
    engine: 'tree',
    terminal: true,
    run: a => a.focusGroup(0),
  },
  focusGroup2: {
    title: 'Focus second group',
    category: 'Layout',
    defaultKeys: 'Mod+2',
    when: 'agent',
    engine: 'tree',
    terminal: true,
    run: a => a.focusGroup(1),
  },
  focusGroup3: {
    title: 'Focus third group',
    category: 'Layout',
    defaultKeys: 'Mod+3',
    when: 'agent',
    engine: 'tree',
    terminal: true,
    run: a => a.focusGroup(2),
  },
  maximizeGroup: {
    title: 'Maximize group / restore layout',
    category: 'Layout',
    defaultKeys: 'Mod+K Z',
    when: 'agent',
    engine: 'tree',
    keywords: 'zoom focus fullscreen group editor area',
    run: a => a.maximizeGroup(),
  },
  newTerminal: {
    title: 'New terminal',
    category: 'Terminal',
    defaultKeys: 'Ctrl+Shift+`',
    when: 'agent',
    engine: 'tree',
    terminal: true,
    run: a => a.newTerminal(),
  },
  nextTab: {
    title: 'Next tab',
    category: 'Layout',
    defaultKeys: 'Ctrl+Tab',
    when: 'agent',
    terminal: true,
    run: a => a.panelCommand('next'),
  },
  previousTab: {
    title: 'Previous tab',
    category: 'Layout',
    defaultKeys: 'Ctrl+Shift+Tab',
    when: 'agent',
    terminal: true,
    run: a => a.panelCommand('previous'),
  },
});

export type CommandId = keyof typeof specs;

/** Commands a person can rebind. Their order is the order of the keyboard shortcut settings. */
export const keyedSpecs: Record<CommandId, KeyedSpec> = specs;

/** Palette-only entries (no default key), in the order the palette shows them. */
const before: Command[] = [
  {
    id: 'lock',
    title: 'Lock workspace',
    category: 'Security',
    keywords: 'security session pause hide',
    run: a => a.lockWorkspace(),
  },
  {
    id: 'stop-host',
    title: 'Stop this host',
    category: 'Security',
    when: 'host-admin',
    keywords: 'shutdown disconnect terminals schedules',
    run: a => a.stopHost(),
  },
  {
    id: 'main-agent',
    title: 'Open main agent',
    category: 'Go',
    keywords: 'home primary orchestrator coordinator chat',
    run: a => a.openMainAgent(),
  },
  {
    id: 'control-plane',
    title: 'Projects',
    category: 'Go',
    keywords: 'overview projects runs activity operations dashboard',
    run: a => a.setManager('projects'),
  },
  {
    id: 'workflows',
    title: 'Workflows',
    category: 'Go',
    keywords: 'custom workflow scripts runners history',
    run: a => a.openWorkflows(),
  },
  {
    id: 'refresh',
    title: 'Refresh conversations',
    category: 'Chat',
    keywords: 'chat sessions reconnect',
    run: a => a.refreshConversations(),
  },
];

/** Palette-only entries for the tab-group layout. */
const treeOnly: Command[] = [
  {
    id: 'view-agents',
    title: 'Show Agents',
    category: 'View',
    when: 'agent',
    engine: 'tree',
    keywords: 'needs you inbox main agent sub-agents subagents cli sessions chats conversations',
    leavesManager: true,
    run: a => a.showView('agents'),
  },
  {
    id: 'view-sites',
    title: 'Show Sites',
    category: 'View',
    when: 'agent',
    engine: 'tree',
    keywords: 'saved sites browser pinned',
    leavesManager: true,
    run: a => a.showView('sites'),
  },
  {
    id: 'cli-new',
    title: 'New CLI session',
    category: 'Terminal',
    when: 'agent',
    engine: 'tree',
    keywords: 'claude codex gemini aider cli coding agent terminal session start',
    run: a => a.newCliSession(),
  },
  {
    id: 'cli-focus',
    title: 'Focus CLI session',
    category: 'Terminal',
    when: 'agent',
    engine: 'tree',
    keywords: 'claude codex cli session needs approval waiting input attention',
    run: a => a.focusCliSession(),
  },
  {
    id: 'sites-add-current',
    title: 'Add current page to Sites',
    category: 'Browser',
    when: 'agent',
    engine: 'tree',
    keywords: 'saved sites pin bookmark browser page',
    run: a => a.addCurrentSite(),
  },
  {
    id: 'host-status',
    title: 'Show host status',
    category: 'Workspace',
    engine: 'tree',
    keywords: 'health uptime service holder terminals keep awake heartbeat recovery connection',
    run: a => a.showHostStatus(),
  },
  {
    id: 'view-data',
    title: 'Show Data',
    category: 'View',
    when: 'agent',
    engine: 'tree',
    keywords: 'database bi analytics queries reports',
    leavesManager: true,
    run: a => a.showView('data'),
  },
  {
    id: 'view-studio',
    title: 'Show Studio',
    category: 'View',
    when: 'agent',
    engine: 'tree',
    keywords: 'creative image video cinema music',
    leavesManager: true,
    run: a => a.showView('studio'),
  },
  {
    id: 'scm-commit',
    title: 'Commit staged changes',
    category: 'Source control',
    when: 'agent',
    engine: 'tree',
    keywords: 'git commit message stage source control',
    leavesManager: true,
    run: a => {
      a.showView('scm');
      a.scmCommand('commit');
    },
  },
  {
    id: 'scm-stage-all',
    title: 'Stage all changes',
    category: 'Source control',
    when: 'agent',
    engine: 'tree',
    keywords: 'git add stage everything source control',
    leavesManager: true,
    run: a => {
      a.showView('scm');
      a.scmCommand('stageAll');
    },
  },
  {
    id: 'scm-refresh',
    title: 'Refresh source control',
    category: 'Source control',
    when: 'agent',
    engine: 'tree',
    keywords: 'git status reload changes',
    leavesManager: true,
    run: a => {
      a.showView('scm');
      a.scmCommand('refresh');
    },
  },
  {
    id: 'move-tab-other-group',
    title: 'Move tab to other group',
    category: 'Layout',
    when: 'agent',
    engine: 'tree',
    keywords: 'tab group split move editor area',
    run: a => a.moveTabToOtherGroup(),
  },
];

/** Entries that stand in for the retired Workspace actions menu, in both layouts. */
const windowAndWork: Command[] = [
  {
    id: 'new-conversation-options',
    title: 'New chat with options',
    category: 'Chat',
    keywords: 'new conversation project agent preset folder',
    run: a => a.openNewChatWithOptions(),
  },
  {
    id: 'open-project',
    title: 'Open project',
    category: 'Go',
    when: 'agent',
    keywords: 'add project folder register open directory',
    leavesManager: true,
    run: a => a.openProject(),
  },
  {
    id: 'projects-overview',
    title: 'Projects overview',
    category: 'Go',
    keywords: 'overview dashboard control plane',
    run: a => a.setManager('overview'),
  },
  {
    id: 'projects-activity',
    title: 'Activity',
    category: 'Go',
    keywords: 'runs tasks operations history',
    run: a => a.setManager('tasks'),
  },
  {
    id: 'detach-workspace',
    title: 'Detach workspace',
    category: 'Window',
    when: 'agent',
    keywords: 'new window popout move',
    run: a => a.detachWorkspace(),
  },
  {
    id: 'return-to-main',
    title: 'Return to main workspace',
    category: 'Window',
    when: 'detached',
    keywords: 'redock dock back window',
    run: a => a.returnToMain(),
  },
  {
    id: 'recover-windows',
    title: 'Recover unsaved windows',
    category: 'Window',
    when: 'web',
    keywords: 'crash closed unsaved files drafts',
    run: a => a.recoverWindows(),
  },
];

const settingsPages: Command[] = settingsCategories.map(({id, label}) => ({
  id: 'manager-' + id,
  title: label,
  category: 'Settings',
  keywords: 'settings manager ' + id,
  run: a => a.setManager(id),
}));

const middle: Command[] = [
  {
    id: 'manager-shortcuts',
    title: 'Edit keyboard shortcuts',
    category: 'Settings',
    keywords: 'settings manager shortcuts keybindings preferences',
    run: a => a.setManager('preferences'),
  },
  {
    id: 'workspace-agent',
    title: 'Open Agent workspace',
    category: 'Workspace',
    keywords: 'agent chat conversation workspace',
    run: a => a.setWorkspace('agent'),
  },
  {
    id: 'workspace-studio',
    title: 'Open Studio workspace',
    category: 'Workspace',
    keywords: 'studio creative image video cinema workspace',
    run: a => a.setWorkspace('studio'),
  },
  {
    id: 'workspace-data',
    title: 'Open Data workspace',
    category: 'Workspace',
    keywords: 'database bi analytics queries reports dashboards copilot workspace',
    run: a => a.setWorkspace('data'),
  },
  {
    id: 'manager-media',
    title: 'Media studio & artifacts',
    category: 'Workspace',
    keywords: 'media studio image video cinema lip sync files artifacts',
    run: a => a.setWorkspace('studio'),
  },
];

const keyedCommands: Command[] = Object.entries(keyedSpecs).map(([id, spec]) => ({
  ...spec,
  id,
  keywords: spec.keywords ?? 'command view layout ' + id,
}));

/** Every command, in palette order. */
export const commands: readonly Command[] = [...before, ...settingsPages, ...middle, ...windowAndWork, ...treeOnly, ...keyedCommands];

const byId = new Map(commands.map(command => [command.id, command]));

export function findCommand(id: string): Command | undefined {
  return byId.get(id);
}

export function isCommandId(id: string): id is CommandId {
  return Object.hasOwn(keyedSpecs, id);
}

export function commandAvailable(command: Pick<Command, 'when' | 'engine'>, context: CommandContext): boolean {
  if (command.engine && command.engine !== (context.engine ?? 'legacy')) return false;
  if (command.when === 'agent') return context.workspace === 'agent';
  if (command.when === 'host-admin') return context.hostAdmin;
  if (command.when === 'detached') return !!context.detached;
  if (command.when === 'web') return !context.native;
  return true;
}

export type PaletteEntry = {id: string; label: string; keywords: string; shortcut?: string; run: () => void};

/** What the command palette lists: exactly the commands available in this context, in registry order. */
export function paletteEntries(
  context: CommandContext,
  bindings: Partial<Record<string, string>>,
  run: (command: Command) => void,
): PaletteEntry[] {
  return commands
    .filter(command => commandAvailable(command, context))
    .map(command => ({
      id: command.id,
      label: command.title,
      keywords: command.keywords ?? '',
      shortcut: bindings[command.id],
      run: () => run(command),
    }));
}

/** The layout engines a command's key is live in. */
export function keyEngines(command: Pick<Command, 'engine' | 'keysIn'>): ShellEngine[] {
  const only = command.engine ?? command.keysIn;
  return only ? [only] : ['legacy', 'tree'];
}

/** Whether two default keys could both be live at once, so they would shadow each other. */
export function sharesContext(
  a: Pick<Command, 'when' | 'engine' | 'keysIn'>,
  b: Pick<Command, 'when' | 'engine' | 'keysIn'>,
): boolean {
  const engines = keyEngines(b);
  return (!a.when || !b.when || a.when === b.when) && keyEngines(a).some(engine => engines.includes(engine));
}

/** Whether a command's key does anything in this engine. */
export function keyLive(command: Pick<Command, 'engine' | 'keysIn'>, engine: ShellEngine): boolean {
  return keyEngines(command).includes(engine);
}
