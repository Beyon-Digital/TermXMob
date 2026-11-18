import {Search} from 'lucide-react';
import {lazy, Suspense, useEffect, useRef, useState, type ReactNode} from 'react';
import ActivityBar, {VIEW_META, activitySelection} from '../components/shell/ActivityBar';
import HostStatusPopover from '../components/shell/HostStatusPopover';
import {MobileNav, MobileTopBar} from '../components/shell/MobileChrome';
import SideBar from '../components/shell/SideBar';
import StatusBar from '../components/shell/StatusBar';
import {usePhone} from '../components/shell/TabGroups';
import TitleBar from '../components/shell/TitleBar';
import {Dialog, DialogContent, DialogDescription, DialogTitle} from '../components/ui/dialog';
import {DropdownMenuItem, DropdownMenuSeparator} from '../components/ui/dropdown-menu';
import {TooltipProvider} from '../components/ui/tooltip';
import {CliSessionsProvider, useCliSessions} from '../features/cli/CliSessionsProvider';
import NewCliSessionDialog from '../features/cli/NewCliSessionDialog';
import {OpenCliSessionContext} from '../features/cli/open-cli-context';
import {BufferProvider, useBuffers} from '../features/editor/buffer-store';
import {openFileTab} from '../features/editor/file-navigation';
import ExplorerView from '../features/explorer/ExplorerView';
import {useHostStatus} from '../features/host/use-host-status';
import SearchView from '../features/search/SearchView';
import {onAgentSurfaceRequest, type AgentSurfaceRequest} from '../lib/agent-surface-requests';
import {ScmView, diffRef, useBranchStatus} from '../features/scm';
import {signOut} from '../lib/api';
import {useBrowserTabTitles} from '../lib/browser-tab-titles';
import {needsPerson, sessionName} from '../lib/cli-sessions';
import {allTabs, type SideBarView} from '../lib/layout/model';
import {focusedTab} from '../lib/layout/reduce';
import {phoneStack} from '../lib/layout/rects';
import type {useSessionChanges} from '../lib/session-changes';
import {useHostName} from '../lib/use-host-name';
import {useAuth} from './AuthGate';
import type {SidebarState} from './AgentSidebar';
import QuickOpenDialog from './dialogs/QuickOpenDialog';
import {useReport} from './errors';
import type {TreeController} from './layout-controller';
import {tabTitle} from './tab-titles';
import type {Coordination} from './use-coordination';
import type {HostsState} from './use-hosts-state';
import type {useShellCommands} from './use-shell-commands';
import type {WorkspaceNavigation} from './use-workspace-navigation';
import AgentsView from './views/AgentsView';
import DataView from './views/DataView';
import SitesView from './views/SitesView';
import StudioView from './views/StudioView';
import {useNeedsYouItems} from './views/use-needs-you';
import {useWorkspaceData} from './WorkspaceDataProvider';
import '../components/shell/chrome.css';

const CaptureStatus = lazy(() => import('../features/CaptureStatus'));

/** What the body (the tab layout) needs from this frame. */
export type BodyExtras = {
  openFile: (path: string, line?: number) => void;
  findFile: () => void;
  hideStrip: boolean;
  chosenTab: string | null | undefined;
  onChosenTab: (id: string) => void;
};

export type Quick = {open: boolean; commandsFirst: boolean};

type Props = {
  nav: WorkspaceNavigation;
  hosts: HostsState;
  layout: TreeController;
  coordination: Coordination;
  changesSource: ReturnType<typeof useSessionChanges>;
  sidebar: SidebarState;
  commands: ReturnType<typeof useShellCommands>;
  botsWaiting: number;
  online: boolean;
  locked: boolean;
  redocking: boolean;
  registering: boolean;
  workspace: string;
  quick: Quick;
  setQuick: (next: Quick) => void;
  onNewChat: () => void;
  onAddProject: () => void;
  openCaptureSurface: (surface: 'browser' | 'computer') => void;
  /** The body: the tab layout inside the workspace host. */
  renderBody: (extras: BodyExtras) => ReactNode;
  /** Everything that floats over the workspace (dialogs, toasts). */
  overlays: ReactNode;
};

/** The new frame: the buffers of the window above everything that shows or edits a file. */
export default function IdeFrame(props: Props) {
  const {auth} = useAuth();
  const {session, projects} = useWorkspaceData();
  const report = useReport();
  return (
    <BufferProvider ownerId={auth.principal.id} session={session} onError={report}>
      <CliSessionsProvider enabled={!props.locked && auth.principal.scopes.includes('terminal-view')} projects={projects}>
        <Frame {...props} />
      </CliSessionsProvider>
    </BufferProvider>
  );
}

function Frame(props: Props) {
  const {nav, hosts, layout, coordination, changesSource, commands} = props;
  const {auth, beginLock} = useAuth();
  const {session, selected, projects, refresh, sessions} = useWorkspaceData();
  const report = useReport();
  const store = useBuffers();
  const phone = usePhone();
  const [drawer, setDrawer] = useState(false);
  const [drawerView, setDrawerView] = useState<SideBarView>('agents');
  const [phoneTab, setPhoneTab] = useState<string | null>(null);
  // Opening a tab (Settings from More, a file from the drawer) takes the single stack, even when the tab chosen from the
  // switcher is not the one the layout has active.
  useEffect(() => setPhoneTab(null), [layout.tree.openCount]);
  const cli = useCliSessions();
  const browserTitles = useBrowserTabTitles();
  const [cliDialog, setCliDialog] = useState(false);
  const [hostOpen, setHostOpen] = useState(false);
  const hostHealth = useHostStatus(!props.locked && auth.principal.scopes.includes('machine-view'));
  const book = layout.tree.layout;
  const dispatch = layout.tree.dispatch;
  const project = nav.project;
  const view = phone ? drawerView : book.sideBar.view;
  const focused = focusedTab(book);
  const git = useBranchStatus(project, !props.locked);
  const hostName = useHostName(!props.locked);
  const needs = useNeedsYouItems(!props.locked && view === 'agents' && (phone ? drawer : book.sideBar.visible));
  const attention = props.botsWaiting + coordination.summary.attention + (coordination.summary.blocked ?? 0) + cli.attention;
  const problems = Object.values(store.diagnostics).reduce((sum, items) => sum + items.length, 0);
  const target = {project, session};

  const openFile = (path: string, line?: number) => openFileTab(dispatch, target, path, line);
  const findFile = () => props.setQuick({open: true, commandsFirst: false});
  const showView = (next: SideBarView) => {
    if (phone) {
      setDrawerView(next);
      setDrawer(true);
      if (next === 'data' || next === 'studio') dispatch({type: 'openTab', kind: next});
      return;
    }
    layout.showSideBarView(next);
  };
  const selectView = (next: SideBarView) => {
    // Choosing the view that is already showing hides the side bar, as in VS Code.
    if (!phone && !activitySelection({view: book.sideBar.view, visible: book.sideBar.visible}, next).visible) dispatch({type: 'setSideBar', visible: false});
    else showView(next);
  };
  const toggleSideBar = () => (phone ? setDrawer(open => !open) : dispatch({type: 'toggleSideBar'}));

  /** Show a browser tab or a CLI session in the editor area. On a phone it takes over the single stack. */
  const openAgentTab = (kind: 'browser' | 'cli', ref: string) => {
    dispatch({type: 'openTab', kind, ref});
    if (phone) {
      setPhoneTab(null);
      setDrawer(false);
    }
  };
  const openCliSession = (id: string) => openAgentTab('cli', id);
  const openCliList = () => {
    showView('agents');
    setTimeout(() => document.getElementById('agents-cli')?.scrollIntoView?.({block: 'nearest'}), 60);
  };
  const focusCliSession = () => {
    const target = cli.sessions.find(needsPerson) ?? cli.sessions.find(item => item.status !== 'done' && item.status !== 'failed' && item.status !== 'lost');
    if (target) openCliSession(target.id);
    else openCliList();
  };
  const addCurrentSite = () => {
    const browsers = allTabs(book).filter(tab => tab.kind === 'browser');
    const target = focused?.kind === 'browser' ? focused : browsers[0];
    if (!target) {
      dispatch({type: 'openTab', kind: 'browser'});
      return;
    }
    dispatch({type: 'openTab', kind: 'browser', ref: target.ref});
    setTimeout(() => window.dispatchEvent(new Event('termx-site-add-current')), 160);
  };

  // Commands raise requests; this frame owns the dialogs and views they act on.
  const answer = useRef<(request: AgentSurfaceRequest) => void>(() => {});
  answer.current = request => {
    if (request === 'cli.new') setCliDialog(true);
    else if (request === 'cli.focus') focusCliSession();
    else if (request === 'host.status') setHostOpen(true);
    else addCurrentSite();
  };
  useEffect(() => onAgentSurfaceRequest(request => answer.current(request)), []);

  const mainStatus = (() => {
    if (nav.mainState === 'loading') return 'starting';
    if (nav.mainState === 'failed') return 'unavailable';
    const status = (session?.id === nav.primaryId ? session.latest_status : sessions.find(row => row.id === nav.primaryId)?.latest_status) ?? '';
    if (['running', 'planning', 'recovering'].includes(status)) return 'working';
    if (status === 'awaiting_approval') return 'needs approval';
    if (status === 'failed') return 'failed';
    return 'idle';
  })();

  const cursor = focused?.kind === 'file' && focused.ref ? (store.cursors[focused.ref] ?? null) : null;
  const activePath = focused?.kind === 'file' && focused.ref ? store.get(focused.ref)?.path : undefined;
  const tabs = allTabs(book);
  const titles = (tab: (typeof tabs)[number]) =>
    tabTitle(tab, {
      tabs,
      primaryId: nav.primaryId,
      selected,
      sessionTitle: session?.title,
      cli: id => {
        const row = cli.get(id);
        return row ? sessionName(row) : undefined;
      },
      browser: id => browserTitles.get(id),
      file: key => {
        const buffer = store.get(key);
        return buffer && {path: buffer.path, recovered: buffer.key.includes(':handoff:')};
      },
    });
  const stack = phoneStack(book, phoneTab);
  const activeTitle = stack.tabs.find(item => item.tab.id === stack.active)?.tab;

  const sideBody = (() => {
    switch (view) {
      case 'agents':
        return (
          <AgentsView
            sidebar={props.sidebar}
            primaryId={nav.primaryId}
            mainSelected={!!nav.primaryId && selected === nav.primaryId}
            mainStatus={mainStatus}
            creating={props.registering}
            coordination={coordination}
            needs={needs}
            manager=""
            onNewChat={props.onNewChat}
            onOpenMainAgent={() => void nav.openMainAgent()}
            onOpenConversation={nav.openConversation}
            onFindConversation={props.sidebar.openSearch}
            onReviewNeeds={() => nav.setManager('bots')}
            onNewCliSession={() => {
              setDrawer(false);
              setCliDialog(true);
            }}
            onOpenCliSession={openCliSession}
          />
        );
      case 'explorer':
        return (
          <ExplorerView
            project={project}
            projects={projects}
            session={session}
            git={git}
            activePath={activePath}
            onOpenFile={path => openFile(path)}
            onOpenProject={nav.openProject}
            onAddProject={props.onAddProject}
            onCheckoutChanged={refresh}
            onError={report}
          />
        );
      case 'search':
        return <SearchView project={project} session={session} onOpenFile={openFile} onError={report} />;
      case 'scm':
        return (
          <ScmView
            project={project}
            session={session}
            source={changesSource}
            onOpenDiff={(path, side) => dispatch({type: 'openTab', kind: 'diff', ref: diffRef(side, path)})}
            onOpenFile={path => openFile(path)}
            onCheckoutChanged={refresh}
          />
        );
      case 'sites':
        return <SitesView projectId={project?.id ?? ''} onOpenTab={id => openAgentTab('browser', id)} />;
      case 'data':
        return <DataView host={hosts.setDataSidebarHost} />;
      case 'studio':
        return <StudioView hosts={hosts} nav={nav} />;
    }
  })();

  const sideBar = (drawerMode: boolean) => (
    <SideBar
      title={VIEW_META[view].label}
      width={book.sideBar.size}
      drawer={drawerMode}
      onResizeStart={layout.startSidebarResize}
      onResizeTo={size => dispatch({type: 'setSideBarSize', size})}
    >
      {sideBody}
    </SideBar>
  );

  const account = auth.principal.display_name || auth.principal.id;
  const lock = () => void beginLock();
  const quickOpenKeys = commands.bindings.quickFile;
  const body = props.renderBody({
    openFile,
    findFile,
    hideStrip: phone,
    chosenTab: phone ? phoneTab : undefined,
    onChosenTab: setPhoneTab,
  });

  return (
    <OpenCliSessionContext.Provider value={openCliSession}>
    <TooltipProvider delayDuration={400}>
      <div
        className="workspace-root workspace-shell ide-shell"
        data-workspace={props.workspace}
        data-workspace-mode="chat"
        data-phone={phone || undefined}
        inert={props.redocking || props.locked}
      >
        <a className="skip-link" href="#main-workspace">
          Skip to workspace
        </a>
        {phone ? (
          <MobileTopBar
            title={activeTitle ? titles(activeTitle) : 'TermX'}
            tabs={stack.tabs.map(item => ({id: item.tab.id, title: titles(item.tab)}))}
            activeTab={stack.active}
            attention={attention}
            onMenu={() => {
              setDrawerView(book.sideBar.view);
              setDrawer(true);
            }}
            onPickTab={setPhoneTab}
            onCloseTab={activeTitle ? () => layout.tree.closeTab(activeTitle) : undefined}
          />
        ) : (
          <TitleBar
            project={project?.name ?? ''}
            branch={git?.branch}
            quickOpenKeys={quickOpenKeys}
            sideBarVisible={book.sideBar.visible}
            panelVisible={book.panel.visible}
            needYou={attention}
            account={account}
            online={props.online}
            onToggleSideBar={toggleSideBar}
            onTogglePanel={layout.commands.toggleBottomPanel}
            onOpenCommandCenter={findFile}
            onOpenApprovals={() => showView('agents')}
            onLock={lock}
            onSignOut={() => void signOut().catch(report)}
            onSettings={() => nav.setManager('preferences')}
          />
        )}
        <div className="ide-body">
          {!phone && (
            <ActivityBar
              view={book.sideBar.view}
              sideBarVisible={book.sideBar.visible}
              attention={attention}
              projectsOpen={focused?.kind === 'projects'}
              settingsOpen={focused?.kind === 'settings'}
              account={account}
              onSelectView={selectView}
              onProjects={() => nav.setManager('projects')}
              onSettings={() => nav.setManager('preferences')}
              onLock={lock}
              onSignOut={() => void signOut().catch(report)}
            />
          )}
          {!phone && book.sideBar.visible && sideBar(false)}
          <div className="ide-main">{body}</div>
        </div>
        <StatusBar
          online={props.online}
          hostName={hostName}
          branch={git?.branch}
          dirty={git?.dirty}
          problems={problems}
          needYou={attention}
          mainAgent={mainStatus}
          cursor={cursor}
          compact={phone}
          disconnected={hostHealth.disconnected}
          hostOpen={hostOpen}
          onHostOpenChange={setHostOpen}
          hostDetails={
            hostHealth.availability === 'ready' ? (
              <HostStatusPopover
                health={hostHealth}
                hostName={hostName}
                onOpenAgents={() => {
                  setHostOpen(false);
                  openCliList();
                }}
              />
            ) : undefined
          }
          capture={
            <Suspense fallback={null}>
              <CaptureStatus
                enabled={!props.locked && auth.principal.scopes.includes('desktop-view')}
                onOpen={props.openCaptureSurface}
                onError={report}
              />
            </Suspense>
          }
          onOpenSourceControl={() => showView('scm')}
          onOpenProblems={() => dispatch({type: 'openTab', kind: 'problems'})}
          onOpenAgents={() => showView('agents')}
          onOpenMainAgent={() => void nav.openMainAgent()}
          onLock={lock}
        />
        {phone && (
          <MobileNav
            view={drawerView}
            drawerOpen={drawer}
            terminalOpen={focused?.kind === 'terminal'}
            onAgents={() => showView('agents')}
            onFiles={() => showView('explorer')}
            onGit={() => showView('scm')}
            onTerminal={() => layout.openSurface('terminal')}
            more={
              <>
                <DropdownMenuItem onSelect={() => showView('search')}>
                  <Search size={16} strokeWidth={1.5} aria-hidden="true" />
                  Search
                </DropdownMenuItem>
                <DropdownMenuItem onSelect={() => showView('sites')}>Sites</DropdownMenuItem>
                {cli.available && <DropdownMenuItem onSelect={openCliList}>CLI sessions</DropdownMenuItem>}
                {hostHealth.availability === 'ready' && <DropdownMenuItem onSelect={() => setHostOpen(true)}>Host status</DropdownMenuItem>}
                <DropdownMenuItem onSelect={() => showView('data')}>Data</DropdownMenuItem>
                <DropdownMenuItem onSelect={() => showView('studio')}>Studio</DropdownMenuItem>
                <DropdownMenuSeparator />
                <DropdownMenuItem onSelect={() => nav.setManager('projects')}>Projects</DropdownMenuItem>
                <DropdownMenuItem onSelect={() => nav.setManager('preferences')}>Settings</DropdownMenuItem>
                <DropdownMenuItem onSelect={() => props.setQuick({open: true, commandsFirst: true})}>Commands</DropdownMenuItem>
                <DropdownMenuSeparator />
                <DropdownMenuItem onSelect={lock}>Lock workspace</DropdownMenuItem>
                <DropdownMenuItem onSelect={() => void signOut().catch(report)}>Sign out</DropdownMenuItem>
              </>
            }
          />
        )}
        {phone && (
          <Dialog open={drawer} onOpenChange={setDrawer}>
            <DialogContent className="side-drawer" aria-describedby={undefined}>
              <DialogTitle className="visually-hidden">{VIEW_META[view].label}</DialogTitle>
              <DialogDescription className="visually-hidden">Navigate agents, files and source control.</DialogDescription>
              <div
                className="drawer-body"
                onClick={event => {
                  const hit = (event.target as HTMLElement).closest('.session-row > button, .tree-row, .search-hit, .scm-file-main, .main-agent-row, .view-primary, .site-open:not(.site-static), .cli-row, .needs-cli');
                  if (hit) setDrawer(false);
                }}
              >
                {sideBar(true)}
              </div>
            </DialogContent>
          </Dialog>
        )}
        {props.overlays}
        <NewCliSessionDialog
          open={cliDialog}
          onOpenChange={setCliDialog}
          projects={projects}
          projectId={project?.id ?? ''}
          onCreated={session => openCliSession(session.id)}
        />
        <QuickOpenDialog
          open={props.quick.open}
          onOpenChange={open => props.setQuick({open, commandsFirst: props.quick.commandsFirst})}
          commandsFirst={props.quick.commandsFirst}
          target={target}
          context={commands.context}
          bindings={commands.bindings}
          onRun={command => commands.runCommand(command, {source: 'palette', terminalFocused: false})}
          onOpenFile={path => openFile(path)}
        />
      </div>
    </TooltipProvider>
    </OpenCliSessionContext.Provider>
  );
}
