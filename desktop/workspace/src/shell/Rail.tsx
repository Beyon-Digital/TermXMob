/**
 * @deprecated Classic workspace layout (the dock of seven panels and eight presets), kept for one release as a rollback
 * path behind `?shell=classic` or Settings > General > "Use the classic workspace layout (deprecated)". The default is
 * the tab-group IDE layout (`lib/layout/`, `shell/IdeFrame.tsx`). Removal scheduled for the release after this one.
 */
import {Folder, Pin, Settings} from 'lucide-react';
import StudioNavigation from '../components/StudioNavigation';
import WorkspaceModeSwitch from '../components/WorkspaceModeSwitch';
import WorkspaceNavigation from '../components/WorkspaceNavigation';
import {Button} from '../components/ui/button';
import {needsYouCount} from '../lib/bot-schedules';
import {useAuth} from './AuthGate';
import AgentSidebar, {type useSidebarState} from './AgentSidebar';
import type {LayoutController} from './layout-controller';
import type {ShellNavigation} from './navigation-state';
import type {HostsState} from './use-hosts-state';
import type {WorkspaceNavigation as Navigation} from './use-workspace-navigation';
import {useWorkspaceData} from './WorkspaceDataProvider';

type Props = {
  navigation: ShellNavigation;
  nav: Navigation;
  hosts: HostsState;
  layout: LayoutController;
  sidebar: ReturnType<typeof useSidebarState>;
  /** A conversation is being created. */
  registering: boolean;
  /** Bots waiting for an answer, shown on the Settings button. */
  botsWaiting: number;
  /** The manager page is one of the control-plane pages or Workflows. */
  controlPage: boolean;
  onNewChat: () => void;
  onFindConversation: () => void;
};

/**
 * The activity rail and the side bar: workspace switch, Projects, Settings, and the navigation of the workspace that is
 * showing (conversations and agents, Data, or Studio).
 */
export default function Rail(props: Props) {
  const {navigation, nav, hosts, layout, sidebar, registering, botsWaiting, controlPage} = props;
  const {auth} = useAuth();
  const {projects, catalogState} = useWorkspaceData();
  const {workspace, manager, setManagerState} = navigation;
  const tree = layout.engine === 'tree';
  const workspaceControlPage = controlPage || manager === 'workflows';
  const width = workspace === 'data' ? 260 : workspace === 'studio' ? 240 : layout.sidebarWidth;
  return (
    <WorkspaceNavigation
      railFirst
      sidebarAvailable
      sidebarLabel={
        workspace === 'data' ? 'Data navigation' : workspace === 'studio' ? 'Studio navigation' : 'Chats and agents'
      }
      open={
        workspace === 'data'
          ? hosts.dataSidebarOpen
          : workspace === 'studio'
            ? hosts.studioSidebarOpen
            : workspaceControlPage
              ? hosts.controlSidebarOpen
              : layout.left
      }
      hover={workspace === 'agent' && !workspaceControlPage && layout.sidebarHover}
      width={width}
      onOpenChange={
        workspace === 'data'
          ? hosts.setDataSidebarOpen
          : workspace === 'studio'
            ? hosts.setStudioSidebarOpen
            : workspaceControlPage
              ? hosts.setControlSidebarOpen
              : layout.setLeft
      }
      rail={
        <>
          <WorkspaceModeSwitch value={workspace} onChange={nav.selectWorkspace} presentation="rail" />
          <Button
            variant="ghost"
            size="icon"
            aria-label="Projects"
            title="Projects"
            aria-current={workspaceControlPage ? 'page' : undefined}
            onClick={() => {
              if (workspace !== 'agent')
                nav.setWorkspace('agent', () => (tree ? nav.setManager('projects') : setManagerState('projects')));
              else nav.setManager('projects');
            }}
          >
            <Folder size={20} />
          </Button>
        </>
      }
      footer={
        workspace === 'studio' ? (
          <Button
            variant="ghost"
            size="icon"
            className="settings-launcher"
            title="Settings"
            aria-current={hosts.studioSection === 'settings' ? 'page' : undefined}
            aria-controls="studio-workspace"
            onClick={() => nav.openStudioSection('settings')}
          >
            <Settings size={18} />
            <span className="visually-hidden">Settings</span>
          </Button>
        ) : (
          <Button
            variant="ghost"
            size="icon"
            className="settings-launcher"
            title="Settings"
            aria-haspopup={tree ? undefined : 'dialog'}
            onClick={() => {
              hosts.setManagerScope('global');
              nav.setManager('preferences');
            }}
          >
            <Settings size={18} />
            <span className="visually-hidden">
              Settings{botsWaiting > 0 ? ', ' + needsYouCount(botsWaiting) : ''}
            </span>
            {botsWaiting > 0 && (
              <span className="settings-launcher-count" aria-hidden="true">
                {botsWaiting}
              </span>
            )}
          </Button>
        )
      }
      resize={
        workspace === 'agent' ? (
          <div
            className="resize-handle"
            role="separator"
            aria-label="Resize sidebar"
            aria-orientation="vertical"
            aria-valuemin={200}
            aria-valuemax={420}
            aria-valuenow={layout.sidebarWidth}
            aria-valuetext={layout.sidebarWidth + ' pixels'}
            tabIndex={0}
            onPointerDown={layout.startSidebarResize}
            onKeyDown={event => {
              if (['ArrowLeft', 'ArrowRight'].includes(event.key)) layout.rememberLayout();
              if (event.key === 'ArrowLeft') layout.setSidebarWidth(value => Math.max(200, value - 16));
              if (event.key === 'ArrowRight') layout.setSidebarWidth(value => Math.min(420, value + 16));
            }}
          />
        ) : null
      }
    >
      <div className="sidebar-brand">
        <strong>{workspace === 'agent' ? 'Chats' : workspace === 'data' ? 'Data' : 'TermX'}</strong>
        {workspace === 'agent' && layout.sidebarHover && (
          <Button
            variant="ghost"
            size="icon"
            aria-label="Pin sidebar"
            title="Pin sidebar"
            onClick={() => layout.setSidebarBehavior('pinned')}
          >
            <Pin size={15} />
          </Button>
        )}
      </div>
      {workspace === 'agent' ? (
        <AgentSidebar
          sidebar={sidebar}
          primaryId={nav.primaryId}
          manager={manager}
          creating={registering}
          onNewChat={props.onNewChat}
          onOpenMainAgent={() => void nav.openMainAgent()}
          onOpenConversation={nav.openConversation}
          onFindConversation={props.onFindConversation}
        />
      ) : workspace === 'data' ? (
        <div className="data-sidebar-host" ref={hosts.setDataSidebarHost} />
      ) : (
        <div className="studio-sidebar-context">
          <StudioNavigation
            key={auth.principal.id}
            section={hosts.studioSection}
            onSection={nav.openStudioSection}
            projects={projects}
            projectId={hosts.studioProjectId || null}
            onProject={value => {
              void nav.selectStudioProject(value);
            }}
            managedProjects={hosts.studioProjects}
            projectChangeBlocked={
              hosts.studioProjectGuard.activity.busy ? hosts.studioProjectGuard.activity.reason : undefined
            }
            onBeforeArchive={row =>
              row.id === hosts.studioProjectId ? nav.selectStudioProject('') : Promise.resolve(true)
            }
            loading={catalogState === 'loading'}
          />
        </div>
      )}
    </WorkspaceNavigation>
  );
}
