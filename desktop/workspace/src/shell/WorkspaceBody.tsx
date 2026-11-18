import {Suspense, type Dispatch, type SetStateAction} from 'react';
import type {IncomingContext} from '../lib/context';
import type {Bindings} from '../lib/keybindings';
import {isSettingsCategory} from '../lib/settings-categories';
import type {useSessionChanges} from '../lib/session-changes';
import type {ThemePreference} from '../lib/theme';
import type {WindowPanel} from '../lib/window-journal';
import AgentWorkspace from './AgentWorkspace';
import {CoordinationSidebar} from './CoordinationSidebar';
import DataHost from './DataHost';
import type {LayoutController} from './layout-controller';
import ManagerOverlay from './ManagerOverlay';
import type {ShellNavigation} from './navigation-state';
import StudioHost from './StudioHost';
import type {TabHost} from './tab-content';
import type {Coordination} from './use-coordination';
import type {HostsState} from './use-hosts-state';
import type {WorkspaceNavigation as Navigation} from './use-workspace-navigation';
import {useWorkspaceData} from './WorkspaceDataProvider';
import WorkflowsHost from './WorkflowsHost';

type Props = {
  navigation: ShellNavigation;
  nav: Navigation;
  hosts: HostsState;
  layout: LayoutController;
  coordination: Coordination;
  changesSource: ReturnType<typeof useSessionChanges>;
  taskId: string | null;
  botsWaiting: number;
  theme: ThemePreference;
  setTheme: (theme: ThemePreference) => void;
  bindings: Bindings;
  setBindings: Dispatch<SetStateAction<Bindings>>;
  /** A manager page is one of the control-plane pages (Overview, Projects, Activity). */
  controlPage: boolean;
  openNewChat: (projectId?: string) => void;
  openAddProject: () => void;
  toggleFullscreen: () => void;
  onDetach: (panel: WindowPanel) => void;
  report: (reason: unknown) => void;
};

/**
 * What fills the workspace: the manager and Workflows pages (old layout only; the tab layout shows them as tabs), Studio,
 * Data, and the Agent workspace, which is the old dock or the tab groups.
 */
export default function WorkspaceBody(props: Props) {
  const {navigation, nav, hosts, layout, coordination, controlPage, report} = props;
  const {workspace, manager} = navigation;
  const {setSession, selected, setSelected} = useWorkspaceData();
  const tree = layout.engine === 'tree';

  /** Open a conversation from a project page, in a given view. */
  function openSession(id: string, view: Parameters<LayoutController['chooseLayout']>[0]) {
    nav.navigationIntent.current++;
    nav.setProjectWorkbenchId('');
    if (id !== selected) setSession(null);
    setSelected(id);
    nav.setManager('');
    layout.chooseLayout(view);
  }

  const tabHost: Omit<TabHost, 'panel' | 'closeTab' | 'setDirty'> = {
    project: nav.project,
    projectWorkbenchId: nav.projectWorkbenchId,
    manager: {
      scope: hosts.managerScope,
      onScope: hosts.setManagerScope,
      settingsProjectId: hosts.settingsProjectId,
      onSettingsProject: hosts.setSettingsProjectId,
      waiting: props.botsWaiting,
      theme: props.theme,
      onTheme: props.setTheme,
      bindings: props.bindings,
      onBindings: props.setBindings,
    },
    openManager: (page, ref) => void layout.openManager(page, ref),
    openKind: kind => {
      if (layout.engine === 'tree') layout.tree.dispatch({type: 'openTab', kind});
    },
    onNew: props.openNewChat,
    onAddProject: props.openAddProject,
    onOpenSession: openSession,
    onOpenProject: nav.openProject,
    onOpenConversation: nav.openConversation,
    onContext: (text, attachments) => {
      nav.setIncoming({id: crypto.randomUUID(), sessionId: selected, text, attachments});
      layout.chooseLayout('chat');
    },
  };

  return (
    <main id="main-workspace" className="workspace-body">
      <Suspense fallback={<p className="panel-loading">Opening workspace…</p>}>
        <>
          {manager && manager !== 'workflows' && !isSettingsCategory(manager) && (
            <ManagerOverlay
              manager={manager}
              controlPage={controlPage}
              scope={hosts.managerScope}
              settingsProjectId={hosts.settingsProjectId}
              project={nav.project}
              projectWorkbenchId={nav.projectWorkbenchId}
              onPage={nav.setManager}
              onManage={section => {
                hosts.setManagerScope('global');
                nav.setManager(section);
              }}
              onNew={id => props.openNewChat(id)}
              onAddProject={props.openAddProject}
              onOpen={openSession}
              onProject={nav.openProject}
              onSection={section => {
                if (section === 'media') nav.setWorkspace('studio');
                else nav.setManager(section);
              }}
              onBack={() => nav.setManager(nav.previousManager.current || 'projects')}
            />
          )}
          {!tree && hosts.workflowsVisited && (
            <WorkflowsHost
              workspace={workspace}
              manager={manager}
              project={nav.project}
              projectWorkbenchId={nav.projectWorkbenchId}
              runId={hosts.workflowRunId}
              onOpenSession={nav.openConversation}
              onNavigate={nav.setManager}
            />
          )}
          <StudioHost
            workspace={workspace}
            manager={manager}
            visited={hosts.studioVisited}
            section={hosts.studioSection}
            onSectionChange={hosts.setStudioSection}
            projectId={hosts.studioProjectId}
            project={hosts.studioProject}
            readOnly={hosts.studioProjectReadOnly}
            onProjectActivity={hosts.studioProjectGuard.receive}
            visible={hosts.studioVisible}
            observing={hosts.studioActive}
            onConfigureProvider={() => nav.openStudioSection('settings')}
            onContext={nav.attachStudioContext}
          />
          <DataHost
            workspace={workspace}
            manager={manager}
            visited={hosts.dataVisited}
            visible={hosts.dataVisible}
            navigationHost={hosts.dataSidebarHost}
            onNavigate={() => {
              if (matchMedia('(max-width:850px)').matches) hosts.setDataSidebarOpen(false);
            }}
          />
          <AgentWorkspace
            workspace={workspace}
            manager={manager}
            layout={layout}
            onFullscreen={props.toggleFullscreen}
            onDetach={props.onDetach}
            tabHost={tabHost}
            panels={{
              project: nav.project,
              fileRequest: nav.fileRequest,
              changesSource: props.changesSource,
              taskId: props.taskId,
              incoming: nav.incoming,
              onIncoming: nav.setIncoming,
              mainState: nav.mainState,
              primaryId: nav.primaryId,
              onPrimary: nav.setPrimaryId,
              coordination,
              onAddProject: props.openAddProject,
              onOpenConversation: nav.openConversation,
              onOpenMainAgent: () => void nav.openMainAgent(),
              onManager: nav.setManager,
              onOpenSurface: layout.openSurface,
              onOpenFile: nav.openFile,
              onRevealConversation: layout.revealConversation,
              onShowChat: () => layout.chooseLayout('chat'),
              onError: report,
            }}
          />
          <CoordinationSidebar coordination={coordination} />
        </>
      </Suspense>
    </main>
  );
}
