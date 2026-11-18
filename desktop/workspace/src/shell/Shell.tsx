import {
  routeInitialLayout,
  routeManager,
  routeWorkbenchProject,
  useWorkspaceHistory,
  useWorkspacePopstate,
  useWorkspaceThemeScope,
  workspaceRoute,
} from './routes';
import AuthGate, {useAuth} from './AuthGate';
import {useShellNavigation, type ShellNavigation} from './navigation-state';
import WorkspaceDataProvider, {useWorkspaceData} from './WorkspaceDataProvider';
import {ErrorProvider, useErrorMessage, useReport} from './errors';
import {useStudioProjects} from '../lib/studio-projects';
import {useStudioProjectGuard} from '../lib/studio-draft-state';
import StudioProjectSwitchDialog from '../features/StudioProjectSwitchDialog';
import SettingsSurface from '../features/SettingsSurface';
import {useBotsNeedYouCount} from '../features/bot-attention';
import {needsYouCount} from '../lib/bot-schedules';
import {leaveSettings, SETTINGS_LEAVE} from '../features/settings-forms';
import SettingsGeneral from '../features/SettingsGeneral';
import {isSettingsCategory, canonicalSettingsCategory, settingsCategories} from '../lib/settings-categories';
import {useObservationActive} from '../lib/use-observation-active';
import PairSignIn from '../components/PairSignIn';
import ControlPlane, {type ControlPage} from '../features/ControlPlane';

import {taskState} from '../lib/task-presentation';
import {sessionFence} from '../lib/session-fence';
import {preserveBeforeLock} from '../lib/lock-preservation';
import LockScreen from '../components/LockScreen';
import HostStop from '../components/HostStop';
import {
  restoreArtifactDrafts,
  persistArtifactDrafts,
  snapshotArtifactDrafts,
  receiveArtifactDrafts,
  freezeArtifactDrafts,
  unfreezeArtifactDrafts,
  dropArtifactDrafts,
} from '../lib/artifact-drafts';
import LayoutPresets from '../components/LayoutPresets';
import {LayoutPresetStore, defaultLayout, layoutSnapshot, type NamedLayout} from '../lib/layout-presets';
import {useTheme} from '../lib/theme';
import WorkspaceCommands from '../components/WorkspaceCommands';
import WorkspaceNavigation from '../components/WorkspaceNavigation';
import WorkspaceModeSwitch from '../components/WorkspaceModeSwitch';
import StudioNavigation from '../components/StudioNavigation';
import {
  readWorkspaceMode,
  readStudioSection,
  workspaceHistoryTarget,
  type WorkspaceMode,
  type StudioSection,
} from '../lib/workspace-mode';
import OrchestratorStatus, {type CoordinationSidebarSummary} from '../features/OrchestratorStatus';
import {latestExecutionTask} from '../lib/conversation-messages';
import WorkspaceMenu from '../components/WorkspaceMenu';
import {readSidebarPreference, saveSidebarPreference} from '../lib/sidebar-preferences';
import '../workspace-shell.css';
import '../agent-theme.css';
import {useSessionChanges} from '../lib/session-changes';
import {workspaceFullscreen} from '../lib/workspace-fullscreen';
import {workspaceFilePath} from '../lib/workspace-files';
import {
  persistWindowRecord,
  persistWindowDraft,
  restoreWindowDrafts,
  recoverableWindows,
  bufferCount,
  detachedBrowser,
  type WindowRecord,
  type WindowPanel,
} from '../lib/window-journal';
import {
  openBrowserWindow,
  listenBrowserRedock,
  requestBrowserRedock,
  cancelBrowserRedock,
  commitBrowserRedock,
} from '../lib/browser-windows';
import {snapshotWindow, flushRedockDraft, acceptTransferredDraft} from '../lib/redock-state';
import {
  validateWindowState,
  receiveBuffers,
  rememberTerminal,
  freezeWindowBuffers,
  unfreezeWindowBuffers,
  dropWindowBuffers,
  persistBuffers,
  snapshotBuffers,
} from '../lib/window-state';
import {sessionDrafts} from '../lib/drafts';
import {contextItem, type IncomingContext} from '../lib/context';
import {lazy, Suspense, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState} from 'react';
import {usePolling} from '../lib/use-polling';
import {useLayout, type Layout} from '../lib/use-layout';
import {useAuthSession, type Auth, type Methods} from '../lib/use-auth';
import {useSessionSnapshot} from '../lib/use-session-snapshot';
import {useVirtualizer} from '@tanstack/react-virtual';
import {
  Clapperboard,
  Maximize2,
  Minimize2,
  TerminalSquare,
  GitCompareArrows,
  Code2,
  Home,
  SlidersHorizontal,
  PanelRight,
  PanelBottom,
  Monitor,
  MessageSquare,
  Globe,
  Sun,
  Moon,
  Plus,
  Pin,
  Archive,
  Search,
  Settings,
  ExternalLink,
  ChevronRight,
  ChevronDown,
  Folder,
  Command,
  Shield,
  Cloud,
  Clock,
  GripVertical,
  Boxes,
  LayoutDashboard,
  Lock,
  X,
} from 'lucide-react';
import {Button} from '../components/ui/button';
import DockWorkspace from '../components/DockWorkspace';
import {loadBindings, defaults, type Bindings} from '../lib/keybindings';
import {
  commandAvailable,
  findCommand,
  paletteEntries,
  type BrowserCommand,
  type Command as RegisteredCommand,
  type CommandActions,
  type CommandContext,
  type CommandInvocation,
} from './commands';
import {useShortcuts} from './use-shortcuts';
import AgentSidebar, {useSidebarState} from './AgentSidebar';
import {CoordinationSidebar, InspectedTaskSupervisor} from './CoordinationSidebar';
import ManagerOverlay from './ManagerOverlay';
import SettingsHost from './SettingsHost';
import WorkflowsHost from './WorkflowsHost';
import WorkspaceHeader from './WorkspaceHeader';
import {managerTitle} from './manager-titles';
import {useCoordination} from './use-coordination';
import StatusBar from './StatusBar';
import AddProjectHost from './dialogs/AddProjectHost';
import CommandPaletteDialog from './dialogs/CommandPaletteDialog';
import LayoutsDialog from './dialogs/LayoutsDialog';
import NewChatDialog from './dialogs/NewChatDialog';
import RecoverWindowsDialog from './dialogs/RecoverWindowsDialog';
import {useNewChat} from './use-new-chat';
import {RedockCover, useWindowTransfer} from './WindowTransfer';
import {
  layouts,
  validateDockMap,
  defaultGeometry,
  focusDockMap,
  panelSurface,
  workspaceSurface,
  panels,
  type DockMap,
  type DockGeometry,
  type DockPanel,
} from '../lib/docking';
import PresetPicker, {type AgentPreset} from '../features/PresetPicker';
import ProjectGroup from '../features/ProjectGroup';
import {loadProjectPins, changeProjectPin, orderProjectGroups, type ProjectPins} from '../lib/project-pins';
import '../features/session-controls.css';
import {Input} from '../components/ui/input';
import {Select} from '../components/ui/select';
import {Dialog, DialogContent, DialogDescription, DialogTitle} from '../components/ui/dialog';
import AddProjectDialog from '../components/AddProjectDialog';
import {HostPathField} from '../components/HostPathPicker';
import {formatShortcut} from '../lib/shortcut-label';
import {folderName} from '../lib/path-label';
import {listSessions, SessionCache} from '../lib/workspace-data';
import {checkCompatibility} from '../lib/compatibility';
import {
  nativeWorkspace,
  nativeLogin,
  detachWorkspace,
  isDetachedWindow,
  listenNativeRedock,
  listenNativeLocks,
  requestRedock,
  acceptRedock,
  rejectRedock,
  commitRedock,
  cancelRedock,
} from '../lib/native';
import {scopeProjects} from '../lib/project-labels';
import {
  gql,
  json,
  request,
  signOut,
  login,
  lockWorkspace,
  lockState,
  ApiError,
  type LockState,
  type Engine,
  type Project,
  type Provider,
  type Session,
  type Task,
} from '../lib/api';
const Characters = lazy(() => import('../features/Characters'));
const CompanionHabitat = lazy(() => import('../features/CompanionHabitat'));
const CaptureStatus = lazy(() => import('../features/CaptureStatus'));
const Chat = lazy(() => import('../features/Chat'));
const SidebarTaskSupervisor = lazy(() => import('../features/TaskSupervisor'));
const Workbench = lazy(() => import('../features/Workbench'));
const ChangeReview = lazy(() => import('../features/ChangeReview'));
const TerminalPanel = lazy(() => import('../features/TerminalPanel'));
const BrowserSurface = lazy(() => import('../features/BrowserSurface'));
const SafetyManager = lazy(() => import('../features/SafetyManager'));
const Runners = lazy(() => import('../features/Runners'));
const MediaWorkspace = lazy(() => import('../features/MediaWorkspace'));
const DataStudio = lazy(() => import('../features/data/DataStudio'));
const Artifacts = lazy(() => import('../features/Media'));
const ComputerSurface = lazy(() => import('../features/ComputerSurface'));
const Managers = lazy(() => import('../features/Managers'));
const Workflows = lazy(() => import('../features/Workflows'));
type LayoutSnapshot = {
  layout: Layout;
  dock: DockMap;
  left: boolean;
  right: boolean;
  bottom: boolean;
  dockGeometry: DockGeometry;
  sidebarWidth: number;
  rightWidth: number;
};

export default function Shell() {
  const navigation = useShellNavigation();
  return (
    <WorkspaceDataProvider workspace={navigation.workspace} manager={navigation.manager}>
      <ShellBody navigation={navigation} />
    </WorkspaceDataProvider>
  );
}

function ShellBody({navigation}: {navigation: ShellNavigation}) {
  const {auth, authEpoch, authNow, fence, locked, lockedNow, beginLock, openHostStop} = useAuth();
  const report = useReport();
  const {error, clearError} = useErrorMessage();
  const {workspace, setWorkspaceState, manager, setManagerState, workspaceNow, managerNow} = navigation;
  const {
    projects,
    sessions,
    engines,
    providers,
    sessionsState,
    catalogState,
    configurationState,
    selected,
    setSelected,
    selection,
    projectId,
    setProjectId,
    session,
    setSession,
    sessionCache,
    archived,
    setArchived,
    workspaceActive,
    load,
    refresh,
    projectPins,
    pinsReady,
    pinBusy,
    pinProject,
  } = useWorkspaceData();
  const [theme, setTheme] = useTheme(),
    [addProjectOpen, setAddProjectOpen] = useState(false),
    [palette, setPalette] = useState(false),
    [incoming, setIncoming] = useState<IncomingContext | null>(null),
    [online, setOnline] = useState(navigator.onLine),
    [visited, setVisited] = useState<Set<string>>(new Set(['conversation']));
  const [studioVisited, setStudioVisited] = useState(() => readWorkspaceMode() === 'studio'),
    [dataVisited, setDataVisited] = useState(() => readWorkspaceMode() === 'data'),
    [studioProjectId, setStudioProjectId] = useState(''),
    [studioSection, setStudioSection] = useState<StudioSection>(() => readStudioSection()),
    [studioSidebarOpen, setStudioSidebarOpen] = useState(() => innerWidth > 850),
    [dataSidebarOpen, setDataSidebarOpen] = useState(() => innerWidth > 850),
    [dataSidebarHost, setDataSidebarHost] = useState<HTMLElement | null>(null);
  useWorkspaceThemeScope(workspace);
  const workspaceControlFocus = useRef<WorkspaceMode | null>(null);
  useLayoutEffect(() => {
    if (workspaceControlFocus.current !== workspace) return;
    workspaceControlFocus.current = null;
    document
      .querySelector<HTMLButtonElement>(
        '.workspace-shell[data-workspace="' + workspace + '"] [data-workspace-choice="' + workspace + '"]',
      )
      ?.focus();
  }, [workspace]);
  const {
    layout,
    dock,
    dockGeometry,
    left,
    right,
    bottom,
    sidebarWidth,
    rightWidth,
    sidebarHover,
    setLayout,
    setDock,
    setDockGeometry,
    setLeft,
    setRight,
    setBottom,
    setSidebarWidth,
    setRightWidth,
    setSidebarHover,
  } = useLayout();
  const [projectWorkbenchId, setProjectWorkbenchId] = useState(''),
    [controlSidebarOpen, setControlSidebarOpen] = useState(false);
  const [workflowsVisited, setWorkflowsVisited] = useState(() => workspaceRoute().manager === 'workflows'),
    [workflowRunId, setWorkflowRunId] = useState<string | null>(null);
  const projectWorkbenchNow = useRef('');
  useLayoutEffect(() => {
    projectWorkbenchNow.current = projectWorkbenchId;
  });
  const [sidebarOwner, setSidebarOwner] = useState('');
  const [panelActivation, setPanelActivation] = useState<{panel: DockPanel; nonce: number}>(),
    [fullscreen, setFullscreen] = useState(false),
    [fileRequest, setFileRequest] = useState<{
      id: number;
      sessionId: string;
      root: string;
      path: string;
      line?: number;
    } | null>(null);
  const coordination = useCoordination({workspace, manager});
  const previousManager = useRef('');
  const botsWaiting = useBotsNeedYouCount(!!auth && workspace !== 'data');
  const [primaryId, setPrimaryId] = useState(''),
    [mainState, setMainState] = useState<'loading' | 'ready' | 'failed'>('loading'),
    [homeRequest, setHomeRequest] = useState(0);
  const primaryFlight = useRef<Promise<Session> | null>(null),
    navigationIntent = useRef(0);
  async function openMainAgent(automatic = false, confirmed = false) {
    if (
      !automatic &&
      !confirmed &&
      isSettingsCategory(managerNow.current) &&
      !window.dispatchEvent(
        new CustomEvent(SETTINGS_LEAVE, {
          cancelable: true,
          detail: {proceed: () => void openMainAgent(false, true)},
        }),
      )
    )
      return;
    if (!automatic) setWorkspaceState('agent');
    const pending = fence(),
      intent = automatic ? navigationIntent.current : ++navigationIntent.current;
    setMainState('loading');
    if (!primaryFlight.current) {
      const flight = request<{session: Session}>('/api/workspace/orchestrator/ensure', json('POST', {})).then(
        value => value.session,
      );
      primaryFlight.current = flight;
      void flight.then(
        () => {
          if (primaryFlight.current === flight) primaryFlight.current = null;
        },
        () => {
          if (primaryFlight.current === flight) primaryFlight.current = null;
        },
      );
    }
    try {
      const value = await primaryFlight.current;
      if (!pending.current()) return;
      setPrimaryId(value.id);
      setMainState('ready');
      if (
        intent === navigationIntent.current &&
        !lockedNow.current &&
        (!automatic ||
          (!selection.current &&
            !projectWorkbenchNow.current &&
            !managerNow.current &&
            workspaceNow.current === 'agent'))
      ) {
        setProjectWorkbenchId('');
        setSession(value);
        setSelected(value.id);
        setManagerState('');
        if (!automatic || !new URLSearchParams(location.search).has('layout')) chooseLayout('chat');
        if (innerWidth <= 850) setLeft(false);
      }
      void load();
    } catch (reason) {
      if (pending.current()) {
        setMainState('failed');
        report(reason);
      }
    }
  }
  function openConversation(id: string) {
    const open = () => {
      navigationIntent.current++;
      setProjectWorkbenchId('');
      setWorkspaceState('agent');
      if (id !== selected) setSession(null);
      setSelected(id);
      setManagerState('');
      chooseLayout('chat');
      if (innerWidth <= 850) setLeft(false);
    };
    if (isSettingsCategory(manager)) leaveSettings(open);
    else open();
  }
  function setWorkspace(value: WorkspaceMode, afterOpen?: () => void) {
    const open = () => {
      navigationIntent.current++;
      setWorkspaceState(value);
      if (value === 'data') setDataVisited(true);
      if (value === 'studio') {
        if (!studioVisited) setStudioProjectId(project?.id || '');
        setStudioVisited(true);
      }
      setManagerState('');
      afterOpen?.();
      if (innerWidth <= 850) {
        if (value === 'studio') setStudioSidebarOpen(false);
        else setLeft(false);
      }
    };
    if (isSettingsCategory(manager)) leaveSettings(open);
    else open();
  }
  function selectWorkspace(value: WorkspaceMode) {
    setWorkspace(value, () => {
      workspaceControlFocus.current = value;
    });
  }
  function openStudioSection(value: StudioSection) {
    setWorkspace('studio', () => setStudioSection(value));
  }
  async function selectStudioProject(value: string): Promise<boolean> {
    const open = async () => {
      try {
        if (!(await studioProjectGuard.permit(value))) return false;
        setStudioProjectId(value);
        return true;
      } catch (reason) {
        report(reason);
        return false;
      }
    };
    if (isSettingsCategory(manager)) {
      leaveSettings(() => {
        void open();
      });
      return false;
    }
    return open();
  }
  function setManager(value: string) {
    if (value === 'workflows') setWorkflowRunId(null);
    if (value === 'media') {
      setWorkspace('studio');
      return;
    }
    if (value === 'data') {
      setWorkspace('data');
      return;
    }
    if (manager === value) return;
    const open = () => {
      if (!isSettingsCategory(manager)) previousManager.current = manager || '';
      navigationIntent.current++;
      setManagerState(value);
    };
    if (isSettingsCategory(manager)) leaveSettings(open);
    else open();
  }
  function openProject(value: Project) {
    navigationIntent.current++;
    projectWorkbenchNow.current = value.id;
    setProjectWorkbenchId(value.id);
    setSelected('');
    setSession(null);
    setProjectId(value.id);
    setManagerState('');
    chooseLayout('workbench');
  }
  const shellManager = isSettingsCategory(manager) ? previousManager.current : manager;
  const [settingsProjectId, setSettingsProjectId] = useState('');
  const [managerScope, setManagerScope] = useState<'global' | 'workspace'>('global');
  const [layoutsOpen, setLayoutsOpen] = useState(false),
    [namedLayouts, setNamedLayouts] = useState<NamedLayout[]>([]),
    [layoutOwner, setLayoutOwner] = useState(''),
    [visiblePanels, setVisiblePanels] = useState<DockPanel[]>(['conversation']);
  const layoutBook = useRef<LayoutPresetStore | null>(null);
  const studioVisible = !locked && !manager && workspace === 'studio';
  const dataVisible = !locked && !manager && workspace === 'data';
  // Data polls its own jobs; window focus must not tear down forms or SQL drafts.
  const studioActive = useObservationActive(studioVisible);
  useEffect(() => {
    if (auth && manager === 'workflows') setWorkflowsVisited(true);
    else setWorkflowRunId(null);
  }, [auth?.principal.id, manager]);
  useEffect(() => {
    function openRun(event: Event) {
      const runId = (event as CustomEvent<{runId?: unknown}>).detail?.runId;
      if (!authNow.current || lockedNow.current || typeof runId !== 'string' || !/^[a-f0-9]{32}$/.test(runId))
        return;
      const open = () => {
        navigationIntent.current++;
        setWorkspaceState('agent');
        setWorkflowsVisited(true);
        setWorkflowRunId(runId);
        setManagerState('workflows');
      };
      if (isSettingsCategory(managerNow.current)) leaveSettings(open);
      else open();
    }
    window.addEventListener('termx-open-workflow', openRun);
    return () => window.removeEventListener('termx-open-workflow', openRun);
  }, []);

  const studioProjects = useStudioProjects({ownerId: auth?.principal.id, enabled: studioVisible});
  const studioProjectGuard = useStudioProjectGuard(auth?.principal.id, studioProjectId || null, locked);
  const {
    redocking,
    redock,
    detach,
    detachSurface,
    recoverWindows,
    reopenRecovered,
    recoveryOpen,
    setRecoveryOpen,
    recoveryRows,
  } = useWindowTransfer({
    selected,
    surface: workspaceSurface(layout, dock),
    onArrive: async ({sessionId, panel, hasBuffers}) => {
      navigationIntent.current++;
      setProjectWorkbenchId('');
      setSelected(sessionId);
      setManager('');
      if (hasBuffers) setVisited(old => new Set([...old, 'editor']));
      chooseLayout(panel);
      setSession(await sessionCache.current.read(sessionId));
      clearError();
      await new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve())));
    },
  });
  const [bindings, setBindings] = useState<Bindings>({...defaults});
  useEffect(() => {
    const page = shellManager
      ? isSettingsCategory(manager)
        ? 'Settings'
        : managerTitle[shellManager] || 'Control plane'
      : workspace === 'data'
        ? 'Data'
        : workspace === 'studio'
          ? 'Studio'
          : session?.title || 'Main agent';
    document.title = page + ' · TermX';
  }, [manager, shellManager, workspace, session?.title]);
  const undo = useRef<LayoutSnapshot[]>([]),
    focusRestore = useRef<LayoutSnapshot | null>(null),
    browserCommand = useRef<BrowserCommand | null>(null),
    quickCommand = useRef(false);
  const [quickNonce, setQuickNonce] = useState(0);
  const project =
    projects.find(project => project.id === (session ? session.project_id : projectWorkbenchId || projectId)) ||
    null;
  const sidebar = useSidebarState({primaryId, scopeProjectId: project?.id || projectWorkbenchId});
  const studioProject =
    studioProjects.projects.find(row => row.id === studioProjectId) ||
    projects.find(row => row.id === studioProjectId) ||
    null;
  const studioProjectReadOnly = !!studioProject && 'archived' in studioProject && studioProject.archived;
  function attachStudioContext(text: string, attachments: unknown[]) {
    if (studioProjectId.startsWith('studio_')) {
      report(
        new Error(
          'Choose an Agent project in Studio before attaching media to an Agent conversation. Studio collections keep their media separate.',
        ),
      );
      return;
    }
    if (!selected || session?.id !== selected || (session.project_id || null) !== (studioProjectId || null)) {
      report(
        new Error(
          'Open an Agent conversation ' +
            (studioProject ? 'in ' + studioProject.name : 'without a project') +
            ' before attaching Studio media.',
        ),
      );
      return;
    }
    setIncoming({id: crypto.randomUUID(), sessionId: selected, text, attachments});
    setWorkspace('agent');
    chooseLayout('chat');
  }
  const changesSource = useSessionChanges({
    ownerId: auth?.principal.id,
    project,
    session,
    visible: !!auth && workspaceActive,
  });
  async function toggleFullscreen() {
    try {
      setFullscreen(await workspaceFullscreen(true));
    } catch (error) {
      report(error);
    }
  }
  useEffect(() => {
    if (!auth) return;
    let stopped = false;
    const read = () =>
      void workspaceFullscreen()
        .then(value => {
          if (!stopped) setFullscreen(value);
        })
        .catch(() => {});
    read();
    document.addEventListener('fullscreenchange', read);
    window.addEventListener('focus', read);
    return () => {
      stopped = true;
      document.removeEventListener('fullscreenchange', read);
      window.removeEventListener('focus', read);
    };
  }, [auth?.principal.id]);
  function openSurface(surface: 'review' | 'browser' | 'terminal' | 'editor' | 'computer' | 'artifacts') {
    setWorkspace('agent');
    const panel: DockPanel = surface === 'editor' ? 'editor' : surface;
    if (panel === 'editor') {
      chooseLayout('workbench', 'editor');
      setPanelActivation({panel, nonce: performance.now()});
      return;
    }
    rememberLayout();
    const next = {...dock};
    if (!Object.values(next).includes('main')) next.conversation = 'main';
    if (next[panel] === 'hidden') next[panel] = 'right';
    setDock(next);
    if (next[panel] === 'right') setRight(true);
    if (next[panel] === 'bottom') setBottom(true);
    setVisited(old => new Set([...old, panel]));
    if (next[panel] === 'right' && rightWidth < 480)
      setRightWidth(Math.min(800, Math.max(480, Math.round(innerWidth * 0.5))));
    setPanelActivation({panel, nonce: performance.now()});
  }
  function openFile(path: string, line?: number) {
    const root = session?.cwd || project?.path || '',
      relative = workspaceFilePath(path, root);
    if (!session || !relative) {
      report(Error('This file reference is outside the selected conversation checkout.'));
      return;
    }
    setFileRequest({id: performance.now(), sessionId: session.id, root, path: relative, line});
    openSurface('editor');
  }
  useEffect(() => {
    const narrow = matchMedia('(max-width:850px)');
    const changed = () => {
      if (narrow.matches) {
        setLeft(false);
        setStudioSidebarOpen(false);
      } else {
        const owner = authNow.current?.principal.id;
        if (owner) {
          const preference = readSidebarPreference(owner);
          setLeft(preference ? preference.open && !preference.hover : true);
        }
      }
    };
    narrow.addEventListener('change', changed);
    return () => narrow.removeEventListener('change', changed);
  }, []);
  useEffect(() => {
    const status = () => setOnline(navigator.onLine);
    window.addEventListener('online', status);
    window.addEventListener('offline', status);
    return () => {
      window.removeEventListener('online', status);
      window.removeEventListener('offline', status);
    };
  }, []);
  useEffect(() => {
    if (!auth) return;
    setStudioProjectId('');
    setStudioSection(readStudioSection());
    setBindings(loadBindings(localStorage.getItem('termx-keybindings:' + auth.principal.id)));
    void load();
    const book = new LayoutPresetStore(auth.principal.id);
    layoutBook.current = book;
    const stored = localStorage.getItem('termx-layout:' + auth.principal.id);
    if (stored) {
      try {
        const value = JSON.parse(stored),
          legacy = layoutSnapshot(value);
        if (legacy && !book.read().last) book.remember(legacy);
        setTheme(value.theme === 'light' ? 'light' : value.theme === 'system' ? 'system' : 'dark');
      } catch {}
    }
    const route = workspaceRoute(),
      mode = readWorkspaceMode();
    setSelected(route.selected);
    setProjectId(route.project);
    setProjectWorkbenchId(routeWorkbenchProject(route));
    projectWorkbenchNow.current = routeWorkbenchProject(route);
    setWorkspaceState(mode);
    setStudioVisited(mode === 'studio');
    setDataVisited(mode === 'data');
    setWorkflowsVisited(route.manager === 'workflows');
    setWorkflowRunId(null);
    setManager(route.manager);
    const saved = book.read();
    restoreLayout(book.arrangement(routeInitialLayout(route) || saved.last || 'chat'));
    const navigation = readSidebarPreference(auth.principal.id);
    setSidebarHover(navigation?.hover || false);
    setLeft(
      (navigation?.open ?? book.arrangement(saved.last || 'chat').left) &&
        !navigation?.hover &&
        innerWidth > 850,
    );
    setSidebarOwner(auth.principal.id);
    setNamedLayouts(saved.named);
    setLayoutOwner(auth.principal.id);
  }, [auth?.principal.id]);
  useEffect(() => {
    if (!auth || layoutOwner !== auth.principal.id || locked || workspace !== 'agent') return;
    const route = workspaceRoute();
    if (!route.selected && !projectWorkbenchId && !manager && !selection.current && !managerNow.current)
      void openMainAgent(true);
  }, [auth?.principal.id, layoutOwner, locked, homeRequest, manager, workspace, projectWorkbenchId]);
  useEffect(() => {
    setVisited(
      old =>
        new Set([
          ...old,
          ...Object.entries(dock)
            .filter(([, region]) => region !== 'hidden')
            .map(([panel]) => panel),
        ]),
    );
  }, [dock]);
  useEffect(() => {
    if (innerWidth > 850 && auth && sidebarOwner === auth.principal.id)
      saveSidebarPreference(auth.principal.id, {hover: sidebarHover, open: left});
  }, [sidebarOwner, sidebarHover, left, auth?.principal.id]);
  function showSearch() {
    setWorkspace('agent');
    sidebar.openSearch();
    setLeft(true);
  }
  function setSidebarBehavior(value: string) {
    setSidebarHover(value === 'hover');
    setLeft(value === 'pinned');
    if (auth && sidebarOwner === auth.principal.id)
      saveSidebarPreference(auth.principal.id, {hover: value === 'hover', open: value === 'pinned'});
  }
  function rememberLayout() {
    undo.current = [
      ...undo.current.slice(-19),
      {
        layout,
        dock: {...dock},
        left,
        right,
        bottom,
        dockGeometry: {...dockGeometry},
        sidebarWidth,
        rightWidth,
      },
    ];
  }
  function changeDock(value: DockMap) {
    rememberLayout();
    if (panels.some(panel => value[panel] === 'right' && dock[panel] !== 'right')) setRight(true);
    if (panels.some(panel => value[panel] === 'bottom' && dock[panel] !== 'bottom')) setBottom(true);
    setDock(value);
  }
  function chooseLayout(value: Layout, reveal?: DockPanel) {
    setWorkspaceState('agent');
    setManagerScope('workspace');
    const book = layoutBook.current;
    if (book && layout !== 'focus') book.remember(currentLayout());
    focusRestore.current = null;
    rememberLayout();
    let snapshot =
      value === 'focus'
        ? {...currentLayout(), layout: value, dock: layouts.focus}
        : book?.arrangement(value) || defaultLayout(value);
    if (reveal) {
      const region =
        snapshot.dock[reveal] === 'hidden'
          ? Object.values(snapshot.dock).includes('main')
            ? 'split'
            : 'main'
          : snapshot.dock[reveal];
      snapshot = {
        ...snapshot,
        dock: {...snapshot.dock, [reveal]: region},
        right: snapshot.right || region === 'right',
        bottom: snapshot.bottom || region === 'bottom',
      };
    }
    restoreLayout(snapshot);
  }
  function currentLayout(): LayoutSnapshot {
    return {
      layout,
      dock: {...dock},
      left,
      right,
      bottom,
      dockGeometry: {...dockGeometry},
      sidebarWidth,
      rightWidth,
    };
  }
  function resetLayout() {
    const value = layout === 'focus' ? focusRestore.current?.layout || 'chat' : layout;
    rememberLayout();
    focusRestore.current = null;
    restoreLayout(
      layoutBook.current?.reset(value === 'focus' ? 'chat' : value) ||
        defaultLayout(value === 'focus' ? 'chat' : value),
    );
  }
  function saveLayout(name: string) {
    try {
      layoutBook.current?.save(name, currentLayout());
      setNamedLayouts(layoutBook.current?.read().named || []);
    } catch (error) {
      report(error);
    }
  }
  function restoreLayout(snapshot: LayoutSnapshot) {
    setLayout(snapshot.layout);
    setDock(snapshot.dock);
    setLeft(snapshot.left && !sidebarHover && innerWidth > 850);
    setRight(snapshot.right);
    setBottom(snapshot.bottom);
    setDockGeometry(snapshot.dockGeometry);
    setSidebarWidth(snapshot.sidebarWidth);
    setRightWidth(snapshot.rightWidth);
  }
  function undoLayout() {
    const snapshot = undo.current.pop();
    if (snapshot) {
      focusRestore.current = null;
      setLeft(snapshot.left && innerWidth > 850);
      restoreLayout(snapshot);
    }
  }
  function focusPanel(panel: string) {
    if (!panels.includes(panel as DockPanel)) return;
    setWorkspace('agent');
    const target = panel as DockPanel;
    if (dock[target] === 'hidden') {
      const region =
        target === 'conversation' ? 'right' : Object.values(dock).includes('main') ? 'split' : 'main';
      changeDock({...dock, [target]: region});
      if (region === 'right') setRight(true);
    } else if (dock[target] === 'right') setRight(true);
    else if (dock[target] === 'bottom') setBottom(true);
    setPanelActivation({panel: target, nonce: performance.now()});
    setTimeout(() => {
      const element = document.querySelector<HTMLElement>('.dock-' + target);
      (element?.querySelector<HTMLElement>('textarea,input,[tabindex="0"],button') || element)?.focus();
    }, 350);
  }

  function toggleFocus() {
    if (focusRestore.current) {
      setLeft(focusRestore.current.left && innerWidth > 850);
      restoreLayout(focusRestore.current);
      focusRestore.current = null;
      return;
    }
    focusRestore.current = {
      layout,
      dock: {...dock},
      left,
      right,
      bottom,
      dockGeometry: {...dockGeometry},
      sidebarWidth,
      rightWidth,
    };
    rememberLayout();
    const panel =
      document.activeElement?.closest('.dock-pane')?.getAttribute('data-panel') ||
      Object.keys(dock).find(panel => dock[panel as keyof DockMap] === 'main') ||
      'conversation';
    setDock(focusDockMap(panels.includes(panel as DockPanel) ? (panel as DockPanel) : 'conversation'));
    setLayout('focus');
    setLeft(false);
    setRight(false);
    setBottom(false);
  }
  const commandContext: CommandContext = {workspace, hostAdmin: !!auth?.principal.scopes.includes('host-admin')};
  const commandActions: CommandActions = {
    setManager,
    setWorkspace: value => setWorkspace(value),
    openWorkflows: () => {
      setWorkspaceState('agent');
      setManager('workflows');
    },
    openMainAgent: () => void openMainAgent(),
    openNewChat: () => openNewChat(),
    refreshConversations: () => void load(),
    lockWorkspace: () => void beginLock(),
    stopHost: openHostStop,
    chooseLayout: value => chooseLayout(value),
    openSurface,
    focusPanel,
    quickOpen: () => {
      quickCommand.current = true;
      setQuickNonce(value => value + 1);
      setManager('');
      chooseLayout('workbench');
    },
    browserCommand: (command, explicit) => {
      if (explicit) {
        browserCommand.current = command;
        setManager('');
        chooseLayout('browser');
      } else window.dispatchEvent(new CustomEvent('termx-browser-command', {detail: {command}}));
    },
    panelCommand: command => window.dispatchEvent(new CustomEvent('termx-panel-command', {detail: {command}})),
    toggleFullscreen: () => void toggleFullscreen(),
    openSavedLayouts: () => setLayoutsOpen(true),
    toggleSidebar: () => {
      rememberLayout();
      window.dispatchEvent(new Event('termx-sidebar-toggle'));
    },
    toggleSidePanel: () => {
      rememberLayout();
      setRight(value => !value);
    },
    toggleBottomPanel: () => {
      rememberLayout();
      setBottom(value => !value);
    },
    togglePalette: () => setPalette(value => !value),
    toggleFocusMode: toggleFocus,
    undoLayout,
    resetLayout,
  };
  function runCommand(command: RegisteredCommand, invocation: CommandInvocation) {
    if (!commandAvailable(command, commandContext)) {
      if (command.when !== 'agent' || invocation.source !== 'palette') return;
      setWorkspace('agent');
    }
    if (command.leavesManager) setManager('');
    command.run(commandActions, invocation);
  }

  useEffect(() => {
    const ready = () => {
      if (!quickCommand.current) return;
      const root = document.querySelector<HTMLElement>('.workbench');
      if (!root || root.getBoundingClientRect().height === 0) return;
      quickCommand.current = false;
      window.dispatchEvent(new CustomEvent('termx-quick-file'));
    };
    window.addEventListener('termx-workbench-ready', ready);
    const frame = layout === 'workbench' ? requestAnimationFrame(ready) : 0;
    return () => {
      if (frame) cancelAnimationFrame(frame);
      window.removeEventListener('termx-workbench-ready', ready);
    };
  }, [layout, visited, palette, quickNonce]);
  useEffect(() => {
    const ready = () => {
      const command = browserCommand.current;
      if (!command) return;
      browserCommand.current = null;
      window.dispatchEvent(
        new CustomEvent('termx-browser-command', {detail: {command, targetSurface: 'browser'}}),
      );
    };
    window.addEventListener('termx-browser-ready', ready);
    if (layout === 'browser' && visited.has('browser')) {
      const frame = requestAnimationFrame(() => {
        const root = document.querySelector<HTMLElement>('.browser-workspace');
        if (root && root.getBoundingClientRect().height > 0) ready();
      });
      return () => {
        cancelAnimationFrame(frame);
        window.removeEventListener('termx-browser-ready', ready);
      };
    }
    return () => window.removeEventListener('termx-browser-ready', ready);
  }, [layout, visited, palette]);
  useEffect(() => {
    if (auth && layoutOwner === auth.principal.id) {
      if (layout !== 'focus') layoutBook.current?.remember(currentLayout());
      localStorage.setItem(
        'termx-layout:' + auth.principal.id,
        JSON.stringify({layout, left, right, bottom, theme, sidebarWidth, rightWidth, dock, dockGeometry}),
      );
    }
  }, [
    layoutOwner,
    layout,
    left,
    right,
    bottom,
    theme,
    sidebarWidth,
    rightWidth,
    dock,
    dockGeometry,
    auth?.principal.id,
  ]);
  useEffect(() => {
    if (workspace !== 'agent') {
      setLayoutsOpen(false);
      setNewOpen(false);
      setAddProjectOpen(false);
    }
  }, [workspace]);
  useWorkspaceHistory(
    !!auth && layoutOwner === auth.principal.id,
    manager,
    selected,
    workspace,
    studioSection,
    projectWorkbenchId,
  );
  useWorkspacePopstate({
    manager,
    selected,
    workspace,
    studioSection,
    projectWorkbenchId,
    onRoute: ({route, mode, studioSection: section}) => {
      navigationIntent.current++;
      setWorkspaceState(mode);
      setStudioSection(section);
      if (mode === 'data') setDataVisited(true);
      if (mode === 'studio') {
        if (!studioVisited) setStudioProjectId(project?.id || '');
        setStudioVisited(true);
      }
      setSelected(route.selected);
      setSession(null);
      setProjectId(route.project);
      setProjectWorkbenchId(routeWorkbenchProject(route));
      projectWorkbenchNow.current = routeWorkbenchProject(route);
      setManagerState(routeManager(route));
      if (mode === 'agent' && route.project && !route.selected && !route.manager) chooseLayout('workbench');
      if (mode === 'agent' && !route.selected && !route.project && !route.manager)
        setHomeRequest(value => value + 1);
    },
  });
  useShortcuts({
    bindings,
    suspended: () => lockedNow.current,
    run: (id, terminalFocused) => {
      const command = findCommand(id);
      if (command) runCommand(command, {source: 'shortcut', terminalFocused});
    },
    escape: () => {
      if (workspace !== 'agent' || !focusRestore.current) return false;
      toggleFocus();
      return true;
    },
  });
  useEffect(() => {
    if (auth) localStorage.setItem('termx-keybindings:' + auth.principal.id, JSON.stringify(bindings));
  }, [bindings, auth?.principal.id]);

  const {
    newOpen,
    setNewOpen,
    folder,
    setFolder,
    registering,
    newPreset,
    setNewPreset,
    newPresets,
    newPresetsLoading,
    newComposerFocus,
    openNewChat,
    openNewChatWithOptions,
    createConversation,
  } = useNewChat({
    navigationIntent,
    workspace,
    manager,
    workspaceNow,
    managerNow,
    setWorkspaceState,
    setProjectWorkbenchId,
    setManager,
    chooseLayout,
    setLeft,
  });
  function resize(event: React.PointerEvent<HTMLDivElement>, region: 'left' | 'right') {
    rememberLayout();
    event.currentTarget.setPointerCapture(event.pointerId);
    const start = event.clientX,
      width = region === 'left' ? sidebarWidth : rightWidth;
    const move = (move: PointerEvent) => {
      const value = Math.max(
        region === 'left' ? 200 : 300,
        Math.min(
          region === 'left' ? 420 : 640,
          width + (region === 'left' ? 1 : -1) * (move.clientX - start),
        ),
      );
      if (region === 'left') setSidebarWidth(value);
      else setRightWidth(value);
    };
    const end = () => {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', end);
    };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', end);
  }
  const controlPage = ['overview', 'projects', 'tasks'].includes(manager);
  const workspaceControlPage = controlPage || manager === 'workflows';
  const managerReturnLabel =
    isSettingsCategory(manager) && previousManager.current
      ? 'Back to ' + (managerTitle[previousManager.current] || 'workspace')
      : workspace === 'data'
        ? 'Back to Data'
        : workspace === 'studio'
          ? 'Back to Studio'
          : projectWorkbenchId && !selected
            ? 'Back to files'
            : 'Back to chat';
  const browserTask = latestExecutionTask(session?.turns || []);
  const taskId =
    browserTask &&
    ['planning', 'running', 'awaiting_approval', 'paused', 'recovering'].includes(browserTask.status)
      ? browserTask.id
      : null;
  const statusContext =
    workspace === 'data'
      ? 'Data workspace'
      : workspace === 'studio'
        ? studioProject?.name || (studioProjectId ? 'Project unavailable' : 'No project')
        : session?.runner_id
          ? 'Runner ' + session.runner_id
          : project?.name ||
            (session?.project_id ? folderName(session.cwd) : '') ||
            (projectWorkbenchId
              ? 'Project unavailable'
              : sessionsState === 'loading'
                ? 'Loading project context…'
                : 'No project open');
  const statusContextPath =
    workspace === 'data'
      ? undefined
      : workspace === 'studio'
        ? studioProject && 'path' in studioProject
          ? studioProject.path
          : undefined
        : session?.runner_id
          ? undefined
          : session?.project_id || project
            ? session?.cwd || project?.path || undefined
            : undefined;
  return (
    <>
      <div
        className={'workspace-root workspace-shell' + (locked ? ' workspace-locked' : '')}
        data-workspace={workspace}
        data-workspace-mode={
          manager
            ? 'utility'
            : workspace === 'data'
              ? 'data'
              : workspace === 'studio'
                ? 'studio'
                : ['workbench', 'review', 'focus'].includes(layout)
                  ? 'coding'
                  : 'chat'
        }
        inert={redocking || locked}
      >
        <a className="skip-link" href="#main-workspace">
          Skip to workspace
        </a>
        <WorkspaceNavigation
          railFirst
          sidebarAvailable
          sidebarLabel={
            workspace === 'data'
              ? 'Data navigation'
              : workspace === 'studio'
                ? 'Studio navigation'
                : 'Chats and agents'
          }
          open={
            workspace === 'data'
              ? dataSidebarOpen
              : workspace === 'studio'
                ? studioSidebarOpen
                : workspaceControlPage
                  ? controlSidebarOpen
                  : left
          }
          hover={workspace === 'agent' && !workspaceControlPage && sidebarHover}
          width={workspace === 'data' ? 260 : workspace === 'studio' ? 240 : sidebarWidth}
          onOpenChange={
            workspace === 'data'
              ? setDataSidebarOpen
              : workspace === 'studio'
                ? setStudioSidebarOpen
                : workspaceControlPage
                  ? setControlSidebarOpen
                  : setLeft
          }
          rail={
            <>
              <WorkspaceModeSwitch value={workspace} onChange={selectWorkspace} presentation="rail" />
              <Button
                variant="ghost"
                size="icon"
                aria-label="Projects"
                title="Projects"
                aria-current={workspaceControlPage ? 'page' : undefined}
                onClick={() => {
                  if (workspace !== 'agent') setWorkspace('agent', () => setManagerState('projects'));
                  else setManager('projects');
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
                aria-current={studioSection === 'settings' ? 'page' : undefined}
                aria-controls="studio-workspace"
                onClick={() => openStudioSection('settings')}
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
                aria-haspopup="dialog"
                onClick={() => {
                  setManagerScope('global');
                  setManager('preferences');
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
                aria-valuenow={sidebarWidth}
                aria-valuetext={sidebarWidth + ' pixels'}
                tabIndex={0}
                onPointerDown={event => resize(event, 'left')}
                onKeyDown={event => {
                  if (['ArrowLeft', 'ArrowRight'].includes(event.key)) rememberLayout();
                  if (event.key === 'ArrowLeft') setSidebarWidth(width => Math.max(200, width - 16));
                  if (event.key === 'ArrowRight') setSidebarWidth(width => Math.min(420, width + 16));
                }}
              />
            ) : null
          }
        >
          <div className="sidebar-brand">
            <strong>{workspace === 'agent' ? 'Chats' : workspace === 'data' ? 'Data' : 'TermX'}</strong>
            {workspace === 'agent' && sidebarHover && (
              <Button
                variant="ghost"
                size="icon"
                aria-label="Pin sidebar"
                title="Pin sidebar"
                onClick={() => setSidebarBehavior('pinned')}
              >
                <Pin size={15} />
              </Button>
            )}
          </div>
          {workspace === 'agent' ? (
            <>
              <AgentSidebar
                sidebar={sidebar}
                primaryId={primaryId}
                manager={manager}
                creating={registering}
                onNewChat={() => openNewChat()}
                onOpenMainAgent={() => void openMainAgent()}
                onOpenConversation={openConversation}
                onFindConversation={showSearch}
              />
            </>
          ) : workspace === 'data' ? (
            <div className="data-sidebar-host" ref={setDataSidebarHost} />
          ) : (
            <div className="studio-sidebar-context">
              <StudioNavigation
                key={auth.principal.id}
                section={studioSection}
                onSection={openStudioSection}
                projects={projects}
                projectId={studioProjectId || null}
                onProject={value => {
                  void selectStudioProject(value);
                }}
                managedProjects={studioProjects}
                projectChangeBlocked={
                  studioProjectGuard.activity.busy ? studioProjectGuard.activity.reason : undefined
                }
                onBeforeArchive={row =>
                  row.id === studioProjectId ? selectStudioProject('') : Promise.resolve(true)
                }
                loading={catalogState === 'loading'}
              />
            </div>
          )}
        </WorkspaceNavigation>
        <div className="workspace-main">
          <WorkspaceHeader
            workspace={workspace}
            studioSection={studioSection}
            manager={manager}
            shellManager={shellManager}
            managerReturnLabel={managerReturnLabel}
            project={project}
            projectWorkbenchId={projectWorkbenchId}
            primaryId={primaryId}
            mainState={mainState}
            layout={layout}
            online={online}
            statusContext={statusContext}
            fullscreen={fullscreen}
            registering={registering}
            redocking={redocking}
            left={left}
            sidebarHover={sidebarHover}
            bindings={bindings}
            coordination={coordination}
            onToggleFullscreen={() => void toggleFullscreen()}
            onNewChat={() => openNewChat()}
            onNewChatWithOptions={() => openNewChatWithOptions()}
            onManager={setManager}
            onReturnFromManager={() => {
              if (isSettingsCategory(manager)) {
                setManager(previousManager.current);
                return;
              }
              if (workspace !== 'agent' || selected || projectWorkbenchId) setManager('');
              else void openMainAgent();
            }}
            onBackToChat={() => {
              if (selected) chooseLayout('chat');
              else void openMainAgent();
            }}
            onOpenFiles={() => {
              chooseLayout('workbench');
              setManager('');
            }}
            onOpenSurface={openSurface}
            onSidebarBehavior={setSidebarBehavior}
            onToggleSidePanel={() => setRight(value => !value)}
            onToggleBottomPanel={() => setBottom(value => !value)}
            onOpenSavedLayouts={() => setLayoutsOpen(true)}
            onRedock={() => void redock()}
            onRecoverWindows={() => void recoverWindows()}
            onDetach={detach}
            onOpenPalette={() => setPalette(true)}
          />
          <main id="main-workspace" className="workspace-body">
            <Suspense fallback={<p className="panel-loading">Opening workspace…</p>}>
              <>
                {manager && manager !== 'workflows' && !isSettingsCategory(manager) && (
                  <ManagerOverlay
                    manager={manager}
                    controlPage={controlPage}
                    scope={managerScope}
                    settingsProjectId={settingsProjectId}
                    project={project}
                    projectWorkbenchId={projectWorkbenchId}
                    onPage={setManager}
                    onManage={section => {
                      setManagerScope('global');
                      setManager(section);
                    }}
                    onNew={id => openNewChat(id)}
                    onAddProject={() => setAddProjectOpen(true)}
                    onOpen={(id, view) => {
                      navigationIntent.current++;
                      setProjectWorkbenchId('');
                      if (id !== selected) setSession(null);
                      setSelected(id);
                      setManager('');
                      chooseLayout(view);
                    }}
                    onProject={openProject}
                    onSection={section => {
                      if (section === 'media') setWorkspace('studio');
                      else setManager(section);
                    }}
                    onBack={() => setManager(previousManager.current || 'projects')}
                  />
                )}
                {workflowsVisited && (
                  <WorkflowsHost
                    workspace={workspace}
                    manager={manager}
                    project={project}
                    projectWorkbenchId={projectWorkbenchId}
                    runId={workflowRunId}
                    onOpenSession={openConversation}
                    onNavigate={setManager}
                  />
                )}
                <div
                  id="studio-workspace"
                  className="workspace-studio-host"
                  role="region"
                  aria-label="Studio workspace"
                  hidden={workspace !== 'studio'}
                  inert={workspace !== 'studio' || !!manager}
                  aria-hidden={workspace !== 'studio' || !!manager ? true : undefined}
                >
                  {studioVisited && (
                    <MediaWorkspace
                      section={studioSection}
                      onSectionChange={setStudioSection}
                      presentation="standalone"
                      key={auth.principal.id}
                      ownerId={auth.principal.id}
                      projectId={studioProjectId || null}
                      projectName={studioProject?.name}
                      readOnly={studioProjectReadOnly}
                      onProjectActivity={studioProjectGuard.receive}
                      visible={studioVisible}
                      observing={studioActive}
                      locked={locked}
                      onConfigureProvider={() => openStudioSection('settings')}
                      onProvidersChanged={() => void load()}
                      onContext={attachStudioContext}
                    />
                  )}
                </div>
                <div
                  id="data-workspace"
                  className="workspace-data-host"
                  role="region"
                  aria-label="Data workspace"
                  hidden={workspace !== 'data'}
                  inert={workspace !== 'data' || !!manager || locked}
                  aria-hidden={workspace !== 'data' || !!manager || locked ? true : undefined}
                >
                  {dataVisited && (
                    <DataStudio
                      key={auth.principal.id}
                      ownerId={auth.principal.id}
                      visible={dataVisible}
                      observing={dataVisible}
                      locked={locked}
                      navigationHost={dataSidebarHost}
                      onNavigate={() => {
                        if (matchMedia('(max-width:850px)').matches) setDataSidebarOpen(false);
                      }}
                    />
                  )}
                </div>
                <div
                  id="agent-workspace"
                  className="workspace-dock-host"
                  role="region"
                  aria-label="Agent workspace"
                  hidden={workspace !== 'agent'}
                  inert={!!manager || workspace !== 'agent'}
                  aria-hidden={manager || workspace !== 'agent' ? true : undefined}
                >
                  <DockWorkspace
                    active={workspace === 'agent' && !manager && !locked}
                    activation={panelActivation}
                    onFullscreen={() => void toggleFullscreen()}
                    onVisiblePanels={setVisiblePanels}
                    map={dock}
                    onChange={changeDock}
                    rightVisible={right}
                    bottomVisible={bottom}
                    rightWidth={rightWidth}
                    onRightWidth={setRightWidth}
                    geometry={dockGeometry}
                    onGeometry={setDockGeometry}
                    onResizeStart={rememberLayout}
                    onDetach={panel => detachSurface(panelSurface(panel))}
                    children={{
                      editor: visited.has('editor') ? (
                        <Workbench
                          ownerId={auth.principal.id}
                          project={project}
                          onAddProject={() => setAddProjectOpen(true)}
                          session={session}
                          onTargetChange={refresh}
                          fileRequest={fileRequest}
                          reviewMode={false}
                          visible={workspaceActive && visiblePanels.includes('editor')}
                          bottom={bottom}
                          onError={report}
                        />
                      ) : null,
                      conversation: (
                        <div className="conversation-home">
                          <OrchestratorStatus
                            presentation="sidebar"
                            sidebarTarget={coordination.target}
                            selectedTasks={coordination.tasks}
                            sidebarVisible={coordination.visible}
                            onInspectTask={coordination.inspect}
                            onSummary={coordination.receiveSummary}
                            quietWhenIdle
                            ownerId={auth.principal.id}
                            selected={selected}
                            enabled={
                              workspaceActive && (visiblePanels.includes('conversation') || coordination.open)
                            }
                            onPrimary={setPrimaryId}
                            onOpen={openConversation}
                          />
                          {mainState === 'failed' && !selected && (
                            <div className="main-agent-error" role="alert">
                              <p>
                                Your main agent could not be opened. Your existing chats are still available.
                              </p>
                              <Button variant="outline" onClick={() => void openMainAgent()}>
                                Retry main agent
                              </Button>
                            </div>
                          )}
                          <Chat
                            coordinating={selected === primaryId}
                            onConfigureAgent={() => setManager('models')}
                            onOpenVoiceSettings={() => setManager('voice')}
                            visible={workspaceActive && visiblePanels.includes('conversation')}
                            ownerId={auth.principal.id}
                            session={session}
                            engines={engines}
                            providers={providers}
                            projects={projects}
                            onHistory={() => {
                              const epoch = authEpoch.current,
                                id = selected;
                              if (id)
                                sessionCache.current
                                  .history(id)
                                  .then(value => {
                                    if (epoch === authEpoch.current && selection.current === id)
                                      setSession(value);
                                  })
                                  .catch(reason => {
                                    if (epoch === authEpoch.current) report(reason);
                                  });
                            }}
                            onSelect={openConversation}
                            onRefresh={refresh}
                            onError={report}
                            incoming={incoming}
                            onOpenSurface={openSurface}
                            onOpenFile={openFile}
                            changes={
                              changesSource.data
                                ? {
                                    files: changesSource.data.files.length,
                                    ...(changesSource.data.counts_complete
                                      ? {
                                          additions: changesSource.data.additions,
                                          deletions: changesSource.data.deletions,
                                        }
                                      : {}),
                                    loading: changesSource.loading,
                                  }
                                : undefined
                            }
                          />
                        </div>
                      ),
                      review: visited.has('review') ? (
                        <ChangeReview
                          ownerId={auth.principal.id}
                          project={project}
                          session={session}
                          visible={workspaceActive && visiblePanels.includes('review')}
                          source={changesSource}
                          onError={report}
                          onOpenFile={openFile}
                        />
                      ) : null,
                      terminal: visited.has('terminal') ? (
                        <TerminalPanel
                          ownerId={auth.principal.id}
                          project={project}
                          workspaceSession={session?.id}
                          executionRoot={session?.cwd || project?.path}
                          onError={report}
                        />
                      ) : null,
                      browser: visited.has('browser') ? (
                        <BrowserSurface
                          visible={workspaceActive && visiblePanels.includes('browser')}
                          locked={locked}
                          projectId={project?.id}
                          taskId={taskId}
                          onContext={context => {
                            const item = contextItem(context);
                            if (!selected || !item) {
                              report(new Error('Select a conversation before attaching browser context.'));
                              return;
                            }
                            setIncoming({
                              id: crypto.randomUUID(),
                              sessionId: selected,
                              text: '',
                              attachments: [],
                              context: [item],
                            });
                            setRight(true);
                            focusPanel('conversation');
                          }}
                        />
                      ) : null,
                      computer: visited.has('computer') ? (
                        <ComputerSurface
                          visible={workspaceActive && visiblePanels.includes('computer')}
                          onError={report}
                          onSection={setManager}
                        />
                      ) : null,
                      artifacts: visited.has('artifacts') ? (
                        <Artifacts
                          key={auth.principal.id}
                          ownerId={auth.principal.id}
                          projectId={project?.id || null}
                          visible={workspaceActive && visiblePanels.includes('artifacts')}
                          onContext={(text, attachments) => {
                            setIncoming({id: crypto.randomUUID(), sessionId: selected, text, attachments});
                            chooseLayout('chat');
                          }}
                        />
                      ) : null,
                    }}
                  />
                </div>
                <CoordinationSidebar coordination={coordination} />
              </>
            </Suspense>
          </main>
          <StatusBar
            online={online}
            context={statusContext}
            contextPath={statusContextPath}
            onOpenSurface={surface => {
              setManager('');
              chooseLayout(surface);
            }}
            onError={report}
            onOpenPalette={() => setPalette(true)}
          />
        </div>
        {!locked && (
          <Suspense fallback={null}>
            <div hidden={workspace !== 'agent'} inert={workspace !== 'agent'}>
              <CompanionHabitat
                key={auth.principal.id}
                onOpen={openConversation}
                onSettings={() => setManager('characters')}
              />
            </div>
          </Suspense>
        )}
        <InspectedTaskSupervisor
          coordination={coordination}
          active={workspace === 'agent' && !manager && !locked}
          onError={report}
        />
        {error && (
          <div className="toast error-toast" role="alert">
            <span>{error}</span>
            <Button variant="ghost" size="sm" onClick={clearError}>
              Dismiss
            </Button>
          </div>
        )}
        {isSettingsCategory(manager) && !locked && (
          <SettingsHost
            manager={manager}
            onSection={setManager}
            onClose={() => setManager(previousManager.current)}
            scope={managerScope}
            onScope={setManagerScope}
            settingsProjectId={settingsProjectId}
            onSettingsProject={setSettingsProjectId}
            project={project}
            waiting={botsWaiting}
            theme={theme}
            onTheme={setTheme}
            bindings={bindings}
            onBindings={setBindings}
          />
        )}
        <RecoverWindowsDialog
          open={recoveryOpen}
          onOpenChange={setRecoveryOpen}
          rows={recoveryRows}
          sessions={sessions}
          onReopen={row => void reopenRecovered(row)}
        />
        <AddProjectHost
          open={addProjectOpen && workspace === 'agent'}
          onOpenChange={setAddProjectOpen}
          navigationIntent={() => navigationIntent.current}
          onProjectAdded={openProject}
        />
        <NewChatDialog
          open={newOpen && workspace === 'agent'}
          onOpenChange={setNewOpen}
          ownerId={auth.principal.id}
          keepFocusOnClose={() => !!newComposerFocus.current}
          projects={projects}
          projectId={projectId}
          onProject={setProjectId}
          presets={newPresets}
          presetsLoading={newPresetsLoading}
          preset={newPreset}
          onPreset={setNewPreset}
          folder={folder}
          onFolder={setFolder}
          creating={registering}
          locked={locked}
          onCreate={() => void createConversation()}
        />
        <LayoutsDialog
          open={layoutsOpen && workspace === 'agent'}
          onOpenChange={setLayoutsOpen}
          presets={namedLayouts}
          onSave={saveLayout}
          onRestore={id => {
            const saved = layoutBook.current?.read().named.find(item => item.id === id);
            if (saved) {
              rememberLayout();
              focusRestore.current = null;
              restoreLayout(saved.snapshot);
              setManager('');
              setLayoutsOpen(false);
            }
          }}
          onRemove={id => {
            layoutBook.current?.remove(id);
            setNamedLayouts(layoutBook.current?.read().named || []);
          }}
          onReset={resetLayout}
        />
        <CommandPaletteDialog
          open={palette}
          onOpenChange={setPalette}
          context={commandContext}
          bindings={bindings}
          onRun={command => runCommand(command, {source: 'palette', terminalFocused: false})}
        />
      </div>
      <StudioProjectSwitchDialog
        open={!!studioProjectGuard.pending && !locked}
        busy={studioProjectGuard.activity.busy}
        onDecision={studioProjectGuard.decide}
      />
      {redocking && !locked && <RedockCover />}
    </>
  );
}
