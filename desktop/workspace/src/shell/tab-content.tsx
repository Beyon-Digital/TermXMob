import {lazy, type Dispatch, type ReactNode, type SetStateAction} from 'react';
import {Button} from '../components/ui/button';
import ControlPlane, {type ControlPage} from '../features/ControlPlane';
import type {Project} from '../lib/api';
import type {DockPanel} from '../lib/docking';
import type {Bindings} from '../lib/keybindings';
import type {Tab, TabKind} from '../lib/layout/model';
import type {ThemePreference} from '../lib/theme';
import type {Layout} from '../lib/use-layout';
import {useAuth} from './AuthGate';
import SettingsHost, {type ManagerScope} from './SettingsHost';
import {useWorkspaceData} from './WorkspaceDataProvider';
import {workflowSessionId} from './WorkflowsHost';

const Workflows = lazy(() => import('../features/Workflows'));
const DataStudio = lazy(() => import('../features/data/DataStudio'));
const MediaWorkspace = lazy(() => import('../features/MediaWorkspace'));

/** The tab kinds whose content is one of the seven Agent panels, and which panel. */
const PANEL_OF_KIND: Partial<Record<TabKind, DockPanel>> = {
  chat: 'conversation',
  file: 'editor',
  diff: 'review',
  terminal: 'terminal',
  browser: 'browser',
  computer: 'computer',
  artifacts: 'artifacts',
};

/**
 * What a tab needs from the shell, beyond the panels themselves.
 *
 * Every kind is a singleton in this release, so a tab's `ref` retargets it: the Chat tab follows the selected
 * conversation, a Settings tab shows the category in its `ref`, a Projects tab one of Overview, Projects or Activity, a
 * Workflows tab a run. The panels wrapped here are the same components the old dock shows, unchanged.
 */
export type TabHost = {
  /** One of the seven Agent panels, as the old dock renders it. */
  panel: (name: DockPanel, visible: boolean) => ReactNode;
  project: Project | null;
  projectWorkbenchId: string;
  manager: {
    scope: ManagerScope;
    onScope: Dispatch<SetStateAction<ManagerScope>>;
    settingsProjectId: string;
    onSettingsProject: Dispatch<SetStateAction<string>>;
    waiting: number;
    theme: ThemePreference;
    onTheme: (theme: ThemePreference) => void;
    bindings: Bindings;
    onBindings: (bindings: Bindings) => void;
  };
  /** Open or retarget a settings, projects or workflows tab. */
  openManager: (page: string, ref?: string) => void;
  /** Open any kind of tab (Studio from the Projects page). */
  openKind: (kind: TabKind) => void;
  closeTab: (tab: Tab) => void;
  setDirty: (tab: string, dirty: boolean) => void;
  onNew: (projectId?: string) => void;
  onAddProject: () => void;
  /** Open a conversation from the Projects page, in a given view. */
  onOpenSession: (id: string, view: Layout) => void;
  onOpenProject: (project: Project) => void;
  onOpenConversation: (id: string) => void;
  onContext: (text: string, attachments: unknown[]) => void;
};

type Props = {tab: Tab; visible: boolean; host: TabHost};

export default function TabContent({tab, visible, host}: Props) {
  const panel = PANEL_OF_KIND[tab.kind];
  if (panel) return <>{host.panel(panel, visible)}</>;
  switch (tab.kind) {
    case 'settings':
      return <SettingsTab tab={tab} host={host} />;
    case 'projects':
      return <ProjectsTab tab={tab} host={host} />;
    case 'workflows':
      return <WorkflowsTab tab={tab} visible={visible} host={host} />;
    case 'data':
      return <DataTab visible={visible} />;
    case 'studio':
      return <StudioTab visible={visible} host={host} />;
    default:
      return <Unavailable kind={tab.kind} onClose={() => host.closeTab(tab)} />;
  }
}

function SettingsTab({tab, host}: {tab: Tab; host: TabHost}) {
  const {manager} = host;
  const {locked} = useAuth();
  if (locked) return null;
  return (
    <SettingsHost
      presentation="page"
      manager={tab.ref ?? 'preferences'}
      onSection={category => host.openManager(category)}
      onClose={() => host.closeTab(tab)}
      onDirtyChange={dirty => host.setDirty(tab.id, dirty)}
      scope={manager.scope}
      onScope={manager.onScope}
      settingsProjectId={manager.settingsProjectId}
      onSettingsProject={manager.onSettingsProject}
      project={host.project}
      waiting={manager.waiting}
      theme={manager.theme}
      onTheme={manager.onTheme}
      bindings={manager.bindings}
      onBindings={manager.onBindings}
    />
  );
}

/** Overview, Projects and Activity: the control-plane pages, without the overlay's own way back. */
function ProjectsTab({tab, host}: {tab: Tab; host: TabHost}) {
  const {auth, locked} = useAuth();
  const {projects, engines, providers, catalogState, configurationState, load} = useWorkspaceData();
  const page = (tab.ref && ['overview', 'projects', 'tasks'].includes(tab.ref) ? tab.ref : 'projects') as ControlPage;
  return (
    <div className="manager-overlay control-overlay">
      <ControlPlane
        key={auth.principal.id}
        page={page}
        ownerId={auth.principal.id}
        enabled={!locked}
        projects={projects}
        engines={engines}
        providers={providers}
        catalogState={catalogState}
        configurationState={configurationState}
        currentProjectId={host.project?.id || host.projectWorkbenchId}
        onPage={next => host.openManager(next)}
        onManage={section => {
          host.manager.onScope('global');
          host.openManager(section);
        }}
        onRefresh={() => void load()}
        onNew={host.onNew}
        onAddProject={host.onAddProject}
        onOpen={host.onOpenSession}
        onProject={host.onOpenProject}
      />
    </div>
  );
}

function WorkflowsTab({tab, visible, host}: {tab: Tab; visible: boolean; host: TabHost}) {
  const {auth, locked} = useAuth();
  const {projects, session, selected} = useWorkspaceData();
  return (
    <div className="manager-overlay control-overlay">
      <Workflows
        key={auth.principal.id}
        ownerId={auth.principal.id}
        projectId={host.project?.id || host.projectWorkbenchId || null}
        projects={projects}
        sessionId={workflowSessionId(session, selected)}
        runId={tab.ref ?? null}
        visible={visible}
        locked={locked}
        onOpenSession={host.onOpenConversation}
        onNavigate={page => host.openManager(page)}
      />
    </div>
  );
}

function DataTab({visible}: {visible: boolean}) {
  const {auth, locked} = useAuth();
  return (
    <div className="workspace-data-host">
      <DataStudio ownerId={auth.principal.id} visible={visible} observing={visible} locked={locked} />
    </div>
  );
}

function StudioTab({visible, host}: {visible: boolean; host: TabHost}) {
  const {auth, locked} = useAuth();
  const {load} = useWorkspaceData();
  return (
    <div className="workspace-studio-host">
      <MediaWorkspace
        key={auth.principal.id}
        ownerId={auth.principal.id}
        projectId={host.project?.id ?? null}
        projectName={host.project?.name}
        visible={visible}
        observing={visible}
        locked={locked}
        onProvidersChanged={() => void load()}
        onContext={host.onContext}
      />
    </div>
  );
}

/** A tab kind that has no content yet (CLI sessions arrive with the host support). One sentence and one action. */
function Unavailable({kind, onClose}: {kind: TabKind; onClose: () => void}) {
  return (
    <div className="tab-empty" role="status">
      <p>{kind === 'cli' ? 'CLI sessions open here once this host supports them.' : 'This tab has nothing to show.'}</p>
      <Button variant="outline" onClick={onClose}>
        Close tab
      </Button>
    </div>
  );
}
