import {useEffect, useLayoutEffect, useRef, useState} from 'react';
import StudioProjectSwitchDialog from '../features/StudioProjectSwitchDialog';
import {useBotsNeedYouCount} from '../features/bot-attention';
import {isSettingsCategory} from '../lib/settings-categories';
import {useSessionChanges} from '../lib/session-changes';
import {useShellEngine, type ShellEngine} from '../lib/shell-engine';
import {useTheme} from '../lib/theme';
import '../workspace-shell.css';
import '../agent-theme.css';
import '../features/session-controls.css';
import {useSidebarState} from './AgentSidebar';
import {useAuth} from './AuthGate';
import {useErrorMessage, useReport} from './errors';
import Rail from './Rail';
import ShellOverlays from './ShellOverlays';
import StatusBar from './StatusBar';
import WorkspaceBody from './WorkspaceBody';
import WorkspaceDataProvider, {useWorkspaceData} from './WorkspaceDataProvider';
import WorkspaceHeader from './WorkspaceHeader';
import {useShellNavigation, type ShellNavigation} from './navigation-state';
import IdeFrame, {type Quick} from './IdeFrame';
import {useWorkspaceThemeScope} from './routes';
import {
  isControlPage,
  managerReturnLabel,
  pageBehindSettings,
  runningBrowserTaskId,
  statusContext,
  statusContextPath,
} from './shell-context';
import {useCoordination} from './use-coordination';
import {useHostsState} from './use-hosts-state';
import {useLayoutController} from './use-layout-controller';
import {useNewChat} from './use-new-chat';
import {useShellCommands} from './use-shell-commands';
import {useWindowChrome} from './use-window-chrome';
import {useWorkspaceNavigation, type WorkspaceNavigation} from './use-workspace-navigation';
import {RedockCover, useWindowTransfer} from './WindowTransfer';

/**
 * The workspace shell, as a composition. Each concern lives in its own module:
 * - the layout engine (`use-layout-controller.ts`): the old dock or the tab groups, behind one interface;
 * - moving around (`use-workspace-navigation.ts`) and what the hosts remember (`use-hosts-state.ts`);
 * - commands and keys (`use-shell-commands.ts`), the window itself (`use-window-chrome.ts`), moving between windows
 *   (`WindowTransfer.tsx`);
 * - what is drawn: the rail and side bar (`Rail.tsx`), the header, the body (`WorkspaceBody.tsx`), the status bar and
 *   the floating parts (`ShellOverlays.tsx`).
 */
export default function Shell() {
  const navigation = useShellNavigation();
  const engine = useShellEngine();
  // A page that was open in the old layout's overlay has no meaning in the other layout: start from the workspace.
  const shown = useRef(engine);
  useLayoutEffect(() => {
    if (shown.current === engine) return;
    shown.current = engine;
    navigation.setManagerState('');
  }, [engine]);
  return (
    <WorkspaceDataProvider workspace={navigation.workspace} manager={navigation.manager}>
      {/* Keyed by engine: switching layouts remounts the shell, so the hooks of one engine never run beside the other's. */}
      <ShellBody key={engine} engine={engine} navigation={navigation} />
    </WorkspaceDataProvider>
  );
}

function ShellBody({navigation, engine}: {navigation: ShellNavigation; engine: ShellEngine}) {
  const {auth, authNow, locked} = useAuth();
  const report = useReport();
  const {error, clearError} = useErrorMessage();
  const {workspace, manager} = navigation;
  const {session, selected, setSelected, setSession, sessionCache, sessionsState, workspaceActive} =
    useWorkspaceData();
  const [theme, setTheme] = useTheme();
  const treeEngine = engine === 'tree';
  // The old layout's palette lists commands; the new layout's command center finds files too (QuickOpenDialog).
  const [palette, setPalette] = useState(false);
  const [quick, setQuick] = useState<Quick>({open: false, commandsFirst: false});
  const transferRef = useRef<ReturnType<typeof useWindowTransfer> | null>(null);
  const [addProjectOpen, setAddProjectOpen] = useState(false);
  const [layoutsOpen, setLayoutsOpen] = useState(false);
  useWorkspaceThemeScope(workspace);

  const hosts = useHostsState({workspace, manager});
  // The layout needs to switch workspaces, and navigation needs the layout: navigation is filled in below.
  const navigationRef = useRef<WorkspaceNavigation | null>(null);
  const layout = useLayoutController(engine, {
    ownerId: auth.principal.id,
    ownerNow: {
      get current() {
        return authNow.current?.principal.id;
      },
    },
    locked,
    workspace,
    setWorkspace: mode => navigationRef.current?.setWorkspace(mode),
    setWorkspaceState: navigation.setWorkspaceState,
    setManagerScope: hosts.setManagerScope,
    report,
    theme,
    setTheme,
    palette: treeEngine ? quick.open : palette,
  });
  const nav = useWorkspaceNavigation({navigation, hosts, layout, report});
  navigationRef.current = nav;

  const newChat = useNewChat({
    navigationIntent: nav.navigationIntent,
    workspace,
    manager,
    workspaceNow: navigation.workspaceNow,
    managerNow: navigation.managerNow,
    setWorkspaceState: navigation.setWorkspaceState,
    setProjectWorkbenchId: nav.setProjectWorkbenchId,
    setManager: nav.setManager,
    chooseLayout: layout.chooseLayout,
    setLeft: layout.setLeft,
  });
  const chrome = useWindowChrome({
    workspace,
    manager,
    shellManager: pageBehindSettings(manager, nav.previousManager.current),
    sessionTitle: session?.title,
    report,
  });
  const commands = useShellCommands({
    navigation,
    nav,
    layout,
    openNewChat: () => newChat.openNewChat(),
    togglePalette: () =>
      treeEngine ? setQuick(value => ({open: !value.open, commandsFirst: true})) : setPalette(value => !value),
    openSavedLayouts: () => setLayoutsOpen(true),
    toggleFullscreen: () => void chrome.toggleFullscreen(),
    openNewChatWithOptions: () => newChat.openNewChatWithOptions(),
    openQuickOpen: () => setQuick({open: true, commandsFirst: false}),
    detachWorkspace: () => transferRef.current?.detach(),
    returnToMain: () => void transferRef.current?.redock(),
    recoverWindows: () => void transferRef.current?.recoverWindows(),
  });
  const transfer = useWindowTransfer({
    selected,
    surface: layout.surface,
    onArrive: async ({sessionId, panel, hasBuffers}) => {
      nav.navigationIntent.current++;
      nav.setProjectWorkbenchId('');
      setSelected(sessionId);
      nav.setManager('');
      layout.arrive(panel, hasBuffers);
      setSession(await sessionCache.current.read(sessionId));
      clearError();
      await new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve())));
    },
  });
  transferRef.current = transfer;
  const coordination = useCoordination({workspace, manager, pinned: treeEngine});
  const botsWaiting = useBotsNeedYouCount(!!auth && workspace !== 'data');
  const sidebar = useSidebarState({
    primaryId: nav.primaryId,
    scopeProjectId: nav.project?.id || nav.projectWorkbenchId,
    compact: treeEngine,
  });
  const changesSource = useSessionChanges({
    ownerId: auth?.principal.id,
    project: nav.project,
    session,
    visible: !!auth && workspaceActive,
  });

  useEffect(() => {
    if (workspace !== 'agent') {
      setLayoutsOpen(false);
      newChat.setNewOpen(false);
      setAddProjectOpen(false);
    }
  }, [workspace]);

  const controlPage = isControlPage(manager);
  const facts = {
    workspace,
    manager,
    previousManager: nav.previousManager.current,
    session,
    selected,
    project: nav.project,
    projectWorkbenchId: nav.projectWorkbenchId,
    sessionsState,
    studioProject: hosts.studioProject,
    studioProjectId: hosts.studioProjectId,
  };
  const context = statusContext(facts);
  const shellManager = pageBehindSettings(manager, nav.previousManager.current);

  const overlays = (
    <ShellOverlays
      navigation={navigation}
      nav={nav}
      hosts={hosts}
      layout={layout}
      coordination={coordination}
      newChat={newChat}
      transfer={transfer}
      error={error}
      clearError={clearError}
      report={report}
      botsWaiting={botsWaiting}
      theme={theme}
      setTheme={setTheme}
      bindings={commands.bindings}
      setBindings={commands.setBindings}
      commandContext={commands.context}
      onRunCommand={command => commands.runCommand(command, {source: 'palette', terminalFocused: false})}
      palette={treeEngine ? false : palette}
      setPalette={setPalette}
      addProjectOpen={addProjectOpen}
      setAddProjectOpen={setAddProjectOpen}
      layoutsOpen={layoutsOpen}
      setLayoutsOpen={setLayoutsOpen}
    />
  );

  if (layout.engine === 'tree') {
    return (
      <>
        <IdeFrame
          nav={nav}
          hosts={hosts}
          layout={layout}
          coordination={coordination}
          changesSource={changesSource}
          sidebar={sidebar}
          commands={commands}
          botsWaiting={botsWaiting}
          online={chrome.online}
          locked={locked}
          redocking={transfer.redocking}
          registering={newChat.registering}
          workspace={workspace}
          quick={quick}
          setQuick={setQuick}
          onNewChat={() => newChat.openNewChat()}
          onAddProject={() => setAddProjectOpen(true)}
          openCaptureSurface={surface => {
            nav.setManager('');
            layout.chooseLayout(surface);
          }}
          renderBody={extras => (
            <WorkspaceBody
              navigation={navigation}
              nav={nav}
              hosts={hosts}
              layout={layout}
              coordination={coordination}
              changesSource={changesSource}
              taskId={runningBrowserTaskId(session)}
              botsWaiting={botsWaiting}
              theme={theme}
              setTheme={setTheme}
              bindings={commands.bindings}
              setBindings={commands.setBindings}
              controlPage={controlPage}
              openNewChat={id => newChat.openNewChat(id)}
              openAddProject={() => setAddProjectOpen(true)}
              toggleFullscreen={() => void chrome.toggleFullscreen()}
              onDetach={transfer.detachSurface}
              report={report}
              openFile={extras.openFile}
              findFile={extras.findFile}
              hideStrip={extras.hideStrip}
              chosenTab={extras.chosenTab}
              onChosenTab={extras.onChosenTab}
            />
          )}
          overlays={overlays}
        />
        <StudioProjectSwitchDialog
          open={!!hosts.studioProjectGuard.pending && !locked}
          busy={hosts.studioProjectGuard.activity.busy}
          onDecision={hosts.studioProjectGuard.decide}
        />
        {transfer.redocking && !locked && <RedockCover />}
      </>
    );
  }

  return (
    <>
      <div
        className={'workspace-root workspace-shell' + (locked ? ' workspace-locked' : '')}
        data-workspace={workspace}
        data-workspace-mode={
          manager ? 'utility' : workspace === 'data' ? 'data' : workspace === 'studio' ? 'studio' : layout.mode
        }
        inert={transfer.redocking || locked}
      >
        <a className="skip-link" href="#main-workspace">
          Skip to workspace
        </a>
        <Rail
          navigation={navigation}
          nav={nav}
          hosts={hosts}
          layout={layout}
          sidebar={sidebar}
          registering={newChat.registering}
          botsWaiting={botsWaiting}
          controlPage={controlPage}
          onNewChat={() => newChat.openNewChat()}
          onFindConversation={() => {
            nav.setWorkspace('agent');
            sidebar.openSearch();
            layout.setLeft(true);
          }}
        />
        <div className="workspace-main">
          <WorkspaceHeader
            engine={engine}
            workspace={workspace}
            studioSection={hosts.studioSection}
            manager={manager}
            shellManager={shellManager}
            managerReturnLabel={managerReturnLabel(facts)}
            project={nav.project}
            projectWorkbenchId={nav.projectWorkbenchId}
            primaryId={nav.primaryId}
            mainState={nav.mainState}
            layout={layout.headerLayout}
            online={chrome.online}
            statusContext={context}
            fullscreen={chrome.fullscreen}
            registering={newChat.registering}
            redocking={transfer.redocking}
            left={layout.left}
            sidebarHover={layout.sidebarHover}
            bindings={commands.bindings}
            coordination={coordination}
            onToggleFullscreen={() => void chrome.toggleFullscreen()}
            onNewChat={() => newChat.openNewChat()}
            onNewChatWithOptions={() => newChat.openNewChatWithOptions()}
            onManager={nav.setManager}
            onReturnFromManager={() => {
              if (isSettingsCategory(manager)) {
                nav.setManager(nav.previousManager.current);
                return;
              }
              if (workspace !== 'agent' || selected || nav.projectWorkbenchId) nav.setManager('');
              else void nav.openMainAgent();
            }}
            onBackToChat={() => {
              if (selected) layout.chooseLayout('chat');
              else void nav.openMainAgent();
            }}
            onOpenFiles={() => {
              layout.chooseLayout('workbench');
              nav.setManager('');
            }}
            onOpenSurface={layout.openSurface}
            onSidebarBehavior={layout.setSidebarBehavior}
            onToggleSidePanel={layout.commands.toggleSidePanel}
            onToggleBottomPanel={layout.commands.toggleBottomPanel}
            onOpenSavedLayouts={() => setLayoutsOpen(true)}
            onRedock={() => void transfer.redock()}
            onRecoverWindows={() => void transfer.recoverWindows()}
            onDetach={transfer.detach}
            onOpenPalette={() => setPalette(true)}
          />
          <WorkspaceBody
            navigation={navigation}
            nav={nav}
            hosts={hosts}
            layout={layout}
            coordination={coordination}
            changesSource={changesSource}
            taskId={runningBrowserTaskId(session)}
            botsWaiting={botsWaiting}
            theme={theme}
            setTheme={setTheme}
            bindings={commands.bindings}
            setBindings={commands.setBindings}
            controlPage={controlPage}
            openNewChat={id => newChat.openNewChat(id)}
            openAddProject={() => setAddProjectOpen(true)}
            toggleFullscreen={() => void chrome.toggleFullscreen()}
            onDetach={transfer.detachSurface}
            report={report}
          />
          <StatusBar
            online={chrome.online}
            context={context}
            contextPath={statusContextPath(facts)}
            onOpenSurface={surface => {
              nav.setManager('');
              layout.chooseLayout(surface);
            }}
            onError={report}
            onOpenPalette={() => setPalette(true)}
          />
        </div>
        {overlays}
      </div>
      <StudioProjectSwitchDialog
        open={!!hosts.studioProjectGuard.pending && !locked}
        busy={hosts.studioProjectGuard.activity.busy}
        onDecision={hosts.studioProjectGuard.decide}
      />
      {transfer.redocking && !locked && <RedockCover />}
    </>
  );
}
