import {cleanup, render, screen, within} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {afterEach, describe, expect, it, vi} from 'vitest';
import StatusBar from '../components/shell/StatusBar';
import TitleBar from '../components/shell/TitleBar';
import {TooltipProvider} from '../components/ui/tooltip';
import {defaults} from '../lib/keybindings';
import {commandAvailable, findCommand} from './commands';
import WorkspaceHeader from './WorkspaceHeader';

vi.mock('./AuthGate', () => ({useAuth: () => ({beginLock: vi.fn()})}));
vi.mock('./WorkspaceDataProvider', () => ({useWorkspaceData: () => ({session: null, selected: ''})}));
vi.mock('../lib/native', async original => ({
  ...(await original<typeof import('../lib/native')>()),
  isDetachedWindow: () => true,
  nativeWorkspace: () => false,
}));

afterEach(cleanup);

/**
 * Where every item of the retired "Workspace actions" menu went. A row names the old item's label and either the command
 * that does it (palette, key, menus) or the place in the new chrome that shows it.
 */
const PARITY: {label: string; command?: string; home?: 'status-bar' | 'command-center' | 'agents-view'}[] = [
  {label: 'New chat', command: 'newConversation'},
  {label: 'New chat with options', command: 'new-conversation-options'},
  {label: 'Subagents', command: 'view-agents', home: 'agents-view'},
  {label: 'Projects', command: 'control-plane'},
  {label: 'Overview', command: 'projects-overview'},
  {label: 'Activity', command: 'projects-activity'},
  {label: 'Files & code', command: 'explorer'},
  {label: 'Browser', command: 'browser'},
  {label: 'Terminal', command: 'terminal'},
  {label: 'Computer', command: 'computer'},
  {label: 'Artifacts', command: 'artifacts'},
  {label: 'Review changes', command: 'review'},
  {label: 'Pinned', command: 'left'},
  {label: 'Collapsed', command: 'left'},
  {label: 'Toggle bottom panel', command: 'bottom'},
  {label: 'Saved layouts', command: 'savedLayouts'},
  {label: 'Return to main workspace', command: 'return-to-main'},
  {label: 'Recover unsaved windows', command: 'recover-windows'},
  {label: 'Detach workspace', command: 'detach-workspace'},
  {label: 'Commands', command: 'palette', home: 'command-center'},
  {label: 'Action review', command: 'manager-safety'},
  {label: 'Appearance', command: 'manager-preferences'},
  {label: 'Lock workspace', command: 'lock'},
];

/** Retired outright: the new layout has no floating side bar and no right dock. */
const RETIRED = ['Float on hover', 'Toggle side panel'];

const noop = () => {};
const header = (engine: 'legacy' | 'tree') => (
  <WorkspaceHeader
    engine={engine}
    workspace="agent"
    studioSection="create"
    manager=""
    shellManager=""
    managerReturnLabel="Back to chat"
    project={null}
    projectWorkbenchId=""
    primaryId="main"
    mainState="ready"
    layout="chat"
    online
    statusContext="Project"
    fullscreen={false}
    registering={false}
    redocking={false}
    left
    sidebarHover={false}
    bindings={defaults}
    coordination={{open: false, summary: {active: 0, attention: 0, done: 0}, show: noop, toggleRef: {current: null}} as never}
    onToggleFullscreen={noop}
    onNewChat={noop}
    onNewChatWithOptions={noop}
    onManager={noop}
    onReturnFromManager={noop}
    onBackToChat={noop}
    onOpenFiles={noop}
    onOpenSurface={noop}
    onSidebarBehavior={noop}
    onToggleSidePanel={noop}
    onToggleBottomPanel={noop}
    onOpenSavedLayouts={noop}
    onRedock={noop}
    onRecoverWindows={noop}
    onDetach={noop}
    onOpenPalette={noop}
  />
);

async function menuLabels(engine: 'legacy' | 'tree') {
  const user = userEvent.setup();
  render(header(engine));
  await user.click(screen.getByRole('button', {name: 'Workspace actions'}));
  const menu = await screen.findByRole('dialog', {name: 'Workspace actions'});
  return within(menu)
    .getAllByRole('button')
    .map(button => (button.textContent ?? '').replace(/[⌘⌃⌥⇧].*$|Ctrl.*$/, '').trim());
}

describe('the retired Workspace actions menu', () => {
  it('has every item accounted for: a command id or a new home', async () => {
    const labels = await menuLabels('legacy');
    const known = new Set([...PARITY.map(row => row.label), ...RETIRED]);
    const missing = labels.filter(label => !known.has(label));
    expect(missing).toEqual([]);
    // The table lists only what the menu really had, so it cannot drift into a wish list.
    const have = new Set(labels);
    expect(PARITY.filter(row => !have.has(row.label)).map(row => row.label)).toEqual([]);
  });

  it('maps every item to a command in the registry that exists in the new layout', () => {
    for (const row of PARITY) {
      if (!row.command) continue;
      const command = findCommand(row.command);
      expect(command, row.label + ' -> ' + row.command).toBeDefined();
      // Available somewhere the tab layout can reach: not an old-dock-only command.
      expect(command!.engine, row.label).not.toBe('legacy');
    }
  });

  it('makes each command available to the palette in a window where it applies', () => {
    const tree = {workspace: 'agent' as const, hostAdmin: false, engine: 'tree' as const, detached: true, native: false};
    for (const row of PARITY) {
      if (!row.command) continue;
      expect(commandAvailable(findCommand(row.command)!, tree), row.label).toBe(true);
    }
  });

  it('keeps Return to main workspace for detached windows and Recover for the browser', () => {
    const main = {workspace: 'agent' as const, hostAdmin: false, engine: 'tree' as const, detached: false, native: true};
    expect(commandAvailable(findCommand('return-to-main')!, main)).toBe(false);
    expect(commandAvailable(findCommand('recover-windows')!, main)).toBe(false);
    expect(commandAvailable(findCommand('return-to-main')!, {...main, detached: true})).toBe(true);
    expect(commandAvailable(findCommand('recover-windows')!, {...main, native: false})).toBe(true);
  });

  it('shows the connection and context line in the status bar and the commands entry in the title bar', async () => {
    const user = userEvent.setup();
    const open = vi.fn();
    render(
      <TooltipProvider>
        <TitleBar
          project="Project"
          quickOpenKeys="Mod+P"
          sideBarVisible
          panelVisible={false}
          needYou={0}
          account="Owner"
          online
          onToggleSideBar={noop}
          onTogglePanel={noop}
          onOpenCommandCenter={open}
          onOpenApprovals={noop}
          onLock={noop}
          onSignOut={noop}
          onSettings={noop}
        />
      </TooltipProvider>,
    );
    await user.click(screen.getByRole('button', {name: 'Search files and commands'}));
    expect(open).toHaveBeenCalled();
    cleanup();
    render(
      <StatusBar online hostName="this Mac" problems={0} needYou={0} mainAgent="idle" cursor={null} onOpenSourceControl={noop} onOpenProblems={noop} onOpenAgents={noop} onOpenMainAgent={noop} onLock={noop} />,
    );
    expect(screen.getByRole('status')).toHaveTextContent('Connected · this Mac');
  });

  it('leaves out of the tab-layout variant what that layout retired', async () => {
    const labels = await menuLabels('tree');
    expect(labels).not.toContain('Float on hover');
    expect(labels).not.toContain('Toggle side panel');
  });
});
