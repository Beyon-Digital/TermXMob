import {useEffect, useState, type Dispatch, type SetStateAction} from 'react';
import {defaults, loadBindings, type Bindings} from '../lib/keybindings';
import {
  commandAvailable,
  findCommand,
  type Command,
  type CommandActions,
  type CommandContext,
  type CommandInvocation,
} from './commands';
import {useAuth} from './AuthGate';
import type {LayoutController} from './layout-controller';
import type {ShellNavigation} from './navigation-state';
import type {WorkspaceNavigation} from './use-workspace-navigation';
import {useShortcuts} from './use-shortcuts';
import {useWorkspaceData} from './WorkspaceDataProvider';

type Options = {
  navigation: ShellNavigation;
  nav: WorkspaceNavigation;
  layout: LayoutController;
  openNewChat: () => void;
  togglePalette: () => void;
  openSavedLayouts: () => void;
  toggleFullscreen: () => void;
};

/**
 * Commands and keys: the bindings (stored per principal), what the shell offers a command (`CommandActions`), and the one
 * keyboard dispatcher. What each command does is in `commands.ts`; this is the wiring between those and the shell.
 */
export function useShellCommands(options: Options) {
  const {navigation, nav, layout, openNewChat, togglePalette, openSavedLayouts, toggleFullscreen} = options;
  const {auth, lockedNow, beginLock, openHostStop} = useAuth();
  const {load} = useWorkspaceData();
  const {workspace, setWorkspaceState} = navigation;
  const [bindings, setBindings] = useState<Bindings>({...defaults});

  useEffect(() => {
    if (auth) setBindings(loadBindings(localStorage.getItem('termx-keybindings:' + auth.principal.id)));
  }, [auth?.principal.id]);
  useEffect(() => {
    if (auth) localStorage.setItem('termx-keybindings:' + auth.principal.id, JSON.stringify(bindings));
  }, [bindings, auth?.principal.id]);

  const context: CommandContext = {
    workspace,
    hostAdmin: !!auth?.principal.scopes.includes('host-admin'),
    engine: layout.engine,
  };
  const actions: CommandActions = {
    setManager: nav.setManager,
    setWorkspace: value => nav.setWorkspace(value),
    openWorkflows: () => {
      setWorkspaceState('agent');
      nav.setManager('workflows');
    },
    openMainAgent: () => void nav.openMainAgent(),
    openNewChat,
    refreshConversations: () => void load(),
    lockWorkspace: () => void beginLock(),
    stopHost: openHostStop,
    chooseLayout: value => layout.chooseLayout(value),
    openSurface: layout.openSurface,
    focusPanel: layout.focusPanel,
    quickOpen: () => {
      layout.requestQuickOpen();
      nav.setManager('');
      layout.chooseLayout('workbench');
    },
    browserCommand: (command, explicit) => {
      if (explicit) {
        layout.requestBrowserCommand(command);
        nav.setManager('');
        layout.chooseLayout('browser');
      } else window.dispatchEvent(new CustomEvent('termx-browser-command', {detail: {command}}));
    },
    panelCommand: layout.commands.panelCommand,
    toggleFullscreen,
    openSavedLayouts,
    toggleSidebar: () => {
      layout.rememberLayout();
      window.dispatchEvent(new Event('termx-sidebar-toggle'));
    },
    toggleSidePanel: layout.commands.toggleSidePanel,
    toggleBottomPanel: layout.commands.toggleBottomPanel,
    togglePalette,
    toggleFocusMode: layout.toggleFocus,
    undoLayout: layout.undoLayout,
    resetLayout: layout.resetLayout,
    splitGroup: layout.commands.splitGroup,
    focusGroup: layout.commands.focusGroup,
    maximizeGroup: layout.commands.maximizeGroup,
    moveTabToOtherGroup: layout.commands.moveTabToOtherGroup,
    newTerminal: layout.commands.newTerminal,
  };

  function runCommand(command: Command, invocation: CommandInvocation) {
    // A command of the other layout engine does nothing, even from the palette.
    if (command.engine && command.engine !== layout.engine) return;
    if (!commandAvailable(command, context)) {
      if (command.when !== 'agent' || invocation.source !== 'palette') return;
      nav.setWorkspace('agent');
    }
    if (command.leavesManager) nav.setManager('');
    command.run(actions, invocation);
  }

  useShortcuts({
    bindings,
    engine: layout.engine,
    suspended: () => lockedNow.current,
    run: (id, terminalFocused) => {
      const command = findCommand(id);
      if (command) runCommand(command, {source: 'shortcut', terminalFocused});
    },
    escape: () => {
      if (workspace !== 'agent' || !layout.canRestoreFocus()) return false;
      layout.toggleFocus();
      return true;
    },
  });

  return {bindings, setBindings: setBindings as Dispatch<SetStateAction<Bindings>>, context, runCommand};
}
