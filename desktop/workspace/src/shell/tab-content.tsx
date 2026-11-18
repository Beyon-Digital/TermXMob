import {lazy, type Dispatch, type ReactNode, type SetStateAction} from 'react';
import {Button} from '../components/ui/button';
import ControlPlane, {type ControlPage} from '../features/ControlPlane';
import CliTab from '../features/cli/CliTab';
import FileTab from '../features/editor/FileTab';
import ProblemsTab from '../features/editor/ProblemsTab';
import TerminalTab from '../features/terminal/TerminalTab';
import type {Project} from '../lib/api';
import type {DockPanel} from '../lib/docking';
import type {Bindings} from '../lib/keybindings';
import type {Tab, TabKind} from '../lib/layout/model';
import type {LayoutAction} from '../lib/layout/reduce';
import type {StudioProjectActivity} from '../lib/studio-draft-state';
import type {StudioSection} from '../lib/workspace-mode';
import type {ThemePreference} from '../lib/theme';
import type {Layout} from '../lib/use-layout';
import {useAuth} from './AuthGate';
import type {PanelOptions} from './agent-panels';
import {useReport} from './errors';
import SettingsHost, {type ManagerScope} from './SettingsHost';
import {useWorkspaceData} from './WorkspaceDataProvider';
import {workflowSessionId} from './WorkflowsHost';

const Workflows = lazy(() => import('../features/Workflows'));
const DataStudio = lazy(() => import('../features/data/DataStudio'));
const MediaWorkspace = lazy(() => import('../features/MediaWorkspace'));
const Preview = lazy(() => import('../features/Preview'));
const Debugger = lazy(() => import('../features/Debugger'));

/** The tab kinds whose content is one of the seven Agent panels, and which panel. */
const PANEL_OF_KIND: Partial<Record<TabKind, DockPanel>> = {
  chat: 'conversation',
  diff: 'review',
  browser: 'browser',
  computer: 'computer',
  artifacts: 'artifacts',
};

/**
 * What a tab needs from the shell, beyond the panels themselves.
 *
 * File, terminal, CLI and browser tabs are multi-instance: a file tab shows the buffer named by its `ref`, a terminal tab the shell
 * named by its `ref`, a CLI tab the CLI session named by its `ref`, a browser tab the built-in tab named by its `ref` (with no `ref`
 * it is the whole browser). Every other kind is a singleton, so its `ref` retargets it: the Chat tab follows the selected conversation, a
 * Settings tab shows the category in its `ref`, a Projects tab one of Overview, Projects or Activity, a Workflows tab a run.
 * The panels wrapped here are the same components the old dock shows, unchanged.
 */
export type TabHost = {
  /** One of the seven Agent panels, as the old dock renders it. */
  panel: (name: DockPanel, visible: boolean, options?: PanelOptions) => ReactNode;
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
  dispatch: (action: LayoutAction) => void;
  /** The shells that terminal tabs already show, so an unattached terminal tab never takes one of them. */
  terminalRefs: readonly string[];
  /** Open a file in a tab of its own, at a line when one is given. */
  openFile: (path: string, line?: number) => void;
  /** Show Quick open. */
  findFile: () => void;
  /** Data and Studio draw their navigation in the side bar. */
  data: {navigationHost: HTMLElement | null};
  studio: {
    section: StudioSection;
    onSection: (section: StudioSection) => void;
    projectId: string;
    projectName?: string;
    readOnly: boolean;
    onProjectActivity: (activity: StudioProjectActivity) => void;
    onConfigureProvider: () => void;
    onContext: (text: string, attachments: unknown[]) => void;
  };
  onNew: (projectId?: string) => void;
  onAddProject: () => void;
  /** Open a conversation from the Projects page, in a given view. */
  onOpenSession: (id: string, view: Layout) => void;
  onOpenProject: (project: Project) => void;
  onOpenConversation: (id: string) => void;
  onContext: (text: string, attachments: unknown[]) => void;
};

/** What the shell hands the tab layout; the layout adds the panels, the close and dirty hooks, `dispatch` and the shell list. */
export type TabHostInput = Omit<TabHost, 'panel' | 'closeTab' | 'setDirty' | 'dispatch' | 'terminalRefs'>;

type Props = {tab: Tab; visible: boolean; host: TabHost};

export default function TabContent({tab, visible, host}: Props) {
  // A browser tab with a ref shows that one built-in tab; with no ref it is the whole browser, as before.
  if (tab.kind === 'browser' && tab.ref) return <>{host.panel('browser', visible, {browserTab: tab.ref, onClose: () => host.closeTab(tab)})}</>;
  const panel = PANEL_OF_KIND[tab.kind];
  if (panel) return <>{host.panel(panel, visible)}</>;
  switch (tab.kind) {
    case 'file':
      return (
        <FileTab
          fileKey={tab.ref}
          visible={visible}
          project={host.project}
          onFindFile={host.findFile}
          onOpenFile={host.openFile}
          onClose={() => host.closeTab(tab)}
        />
      );
    case 'terminal':
      return <TerminalTabHost tab={tab} visible={visible} host={host} />;
    case 'cli':
      return <CliTabHost tab={tab} visible={visible} host={host} />;
    case 'problems':
      return <ProblemsTab project={host.project} onOpenFile={host.openFile} />;
    case 'preview':
      return <PreviewTab host={host} />;
    case 'debug':
      return <DebugTab host={host} />;
    case 'settings':
      return <SettingsTab tab={tab} host={host} />;
    case 'projects':
      return <ProjectsTab tab={tab} host={host} />;
    case 'workflows':
      return <WorkflowsTab tab={tab} visible={visible} host={host} />;
    case 'data':
      return <DataTab visible={visible} host={host} />;
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

function TerminalTabHost({tab, visible, host}: {tab: Tab; visible: boolean; host: TabHost}) {
  const {auth} = useAuth();
  const {session} = useWorkspaceData();
  const report = useReport();
  return (
    <TerminalTab
      ptyId={tab.ref}
      tabDomId={'tab-' + tab.id}
      visible={visible}
      ownerId={auth.principal.id}
      project={host.project}
      session={session}
      taken={new Set(host.terminalRefs.filter(ref => ref !== tab.ref))}
      onAttach={id => host.dispatch({type: 'setRef', tab: tab.id, ref: id})}
      onEnded={() => host.dispatch({type: 'closeTab', tab: tab.id, force: true})}
      onError={report}
    />
  );
}

function CliTabHost({tab, visible, host}: {tab: Tab; visible: boolean; host: TabHost}) {
  const {auth, locked} = useAuth();
  const report = useReport();
  if (!tab.ref) return <Unavailable kind="cli" onClose={() => host.closeTab(tab)} />;
  return (
    <CliTab
      sessionId={tab.ref}
      tabDomId={'tab-' + tab.id}
      visible={visible}
      ownerId={auth.principal.id}
      locked={locked}
      onClose={() => host.closeTab(tab)}
      onOpenSession={id => host.dispatch({type: 'openTab', kind: 'cli', ref: id})}
      onError={report}
    />
  );
}

function PreviewTab({host}: {host: TabHost}) {
  const report = useReport();
  return (
    <div className="panel-pane">
      <Preview project={host.project} onError={report} />
    </div>
  );
}

function DebugTab({host}: {host: TabHost}) {
  const report = useReport();
  const {session} = useWorkspaceData();
  return (
    <div className="panel-pane">
      <Debugger project={host.project} workspaceSession={session?.id} cloudRunner={!!session?.runner_id} onError={report} />
    </div>
  );
}

function DataTab({visible, host}: {visible: boolean; host: TabHost}) {
  const {auth, locked} = useAuth();
  return (
    <div className="workspace-data-host">
      <DataStudio
        ownerId={auth.principal.id}
        visible={visible}
        observing={visible}
        locked={locked}
        navigationHost={host.data.navigationHost ?? undefined}
      />
    </div>
  );
}

function StudioTab({visible, host}: {visible: boolean; host: TabHost}) {
  const {auth, locked} = useAuth();
  const {load} = useWorkspaceData();
  const {studio} = host;
  return (
    <div className="workspace-studio-host">
      <MediaWorkspace
        key={auth.principal.id}
        section={studio.section}
        onSectionChange={studio.onSection}
        presentation="standalone"
        ownerId={auth.principal.id}
        projectId={studio.projectId || null}
        projectName={studio.projectName}
        readOnly={studio.readOnly}
        onProjectActivity={studio.onProjectActivity}
        visible={visible}
        observing={visible}
        locked={locked}
        onConfigureProvider={studio.onConfigureProvider}
        onProvidersChanged={() => void load()}
        onContext={studio.onContext}
      />
    </div>
  );
}

/** A tab that has nothing to show (a CLI tab with no session). One sentence and one action. */
function Unavailable({kind, onClose}: {kind: TabKind; onClose: () => void}) {
  return (
    <div className="tab-empty" role="status">
      <p>{kind === 'cli' ? 'Open a CLI session from the Agents view.' : 'This tab has nothing to show.'}</p>
      <Button variant="outline" onClick={onClose}>
        Close tab
      </Button>
    </div>
  );
}
