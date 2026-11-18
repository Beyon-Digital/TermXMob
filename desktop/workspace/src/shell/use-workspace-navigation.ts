import {useEffect, useLayoutEffect, useRef, useState} from 'react';
import {leaveSettings, SETTINGS_LEAVE} from '../features/settings-forms';
import {json, request, type Project, type Session} from '../lib/api';
import {isSettingsCategory} from '../lib/settings-categories';
import {readStudioSection, readWorkspaceMode, type StudioSection, type WorkspaceMode} from '../lib/workspace-mode';
import {workspaceFilePath} from '../lib/workspace-files';
import {openFileTab} from '../features/editor/file-navigation';
import type {IncomingContext} from '../lib/context';
import {useAuth} from './AuthGate';
import type {FileRequest} from './agent-panels';
import type {LayoutController} from './layout-controller';
import type {ShellNavigation} from './navigation-state';
import {
  routeManager,
  routeWorkbenchProject,
  useWorkspaceHistory,
  useWorkspacePopstate,
  workspaceRoute,
} from './routes';
import type {HostsState} from './use-hosts-state';
import {useWorkspaceData} from './WorkspaceDataProvider';

type Options = {
  navigation: ShellNavigation;
  hosts: HostsState;
  layout: LayoutController;
  report: (reason: unknown) => void;
};

/**
 * Moving around the workspace: which conversation, project, workspace and manager page is showing, and the entry points
 * for each (open the main agent, open a conversation, switch workspace, open a manager page, open a project or a file).
 *
 * Every move bumps `navigationIntent`, so an asynchronous move (opening the main agent) that finds the counter changed
 * knows it was overtaken. Leaving a settings page with unsaved edits is asked first, through `leaveSettings`.
 *
 * In the tab layout there is no manager overlay: manager, settings and Workflows pages are tabs, and `manager` stays
 * empty. `setManager` then opens the tab instead.
 */
export function useWorkspaceNavigation({navigation, hosts, layout, report}: Options) {
  const {auth, authNow, fence, lockedNow, locked} = useAuth();
  const {workspace, setWorkspaceState, manager, setManagerState, workspaceNow, managerNow} = navigation;
  const {
    projects,
    sessionsState,
    selected,
    setSelected,
    selection,
    projectId,
    setProjectId,
    session,
    setSession,
    sessionCache,
    load,
  } = useWorkspaceData();
  const {studioProjectGuard, studioProject} = hosts;
  const [projectWorkbenchId, setProjectWorkbenchId] = useState('');
  const [primaryId, setPrimaryId] = useState('');
  const [mainState, setMainState] = useState<'loading' | 'ready' | 'failed'>('loading');
  const [homeRequest, setHomeRequest] = useState(0);
  const [incoming, setIncoming] = useState<IncomingContext | null>(null);
  const [fileRequest, setFileRequest] = useState<FileRequest | null>(null);
  const projectWorkbenchNow = useRef('');
  const previousManager = useRef('');
  const primaryFlight = useRef<Promise<Session> | null>(null);
  const navigationIntent = useRef(0);
  const workspaceControlFocus = useRef<WorkspaceMode | null>(null);
  const pendingView = useRef<'data' | 'studio' | null>(null);
  // A manager page named by the address in the tab layout: opened as a tab once the layout is restored.
  const pendingManager = useRef('');
  // The address asked for a view or a page in the tab layout: opening the main agent in the background must not take
  // the front from it.
  const addressTab = useRef(false);
  useLayoutEffect(() => {
    projectWorkbenchNow.current = projectWorkbenchId;
  });
  useLayoutEffect(() => {
    if (workspaceControlFocus.current !== workspace) return;
    workspaceControlFocus.current = null;
    document
      .querySelector<HTMLButtonElement>(
        '.workspace-shell[data-workspace="' + workspace + '"] [data-workspace-choice="' + workspace + '"]',
      )
      ?.focus();
  }, [workspace]);

  const project =
    projects.find(row => row.id === (session ? session.project_id : projectWorkbenchId || projectId)) || null;
  const tree = layout.engine === 'tree';

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
        const keepFront = automatic && addressTab.current;
        addressTab.current = false;
        if (!keepFront && (!automatic || !new URLSearchParams(location.search).has('layout'))) layout.chooseLayout('chat');
        if (innerWidth <= 850) layout.setLeft(false);
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
      layout.chooseLayout('chat');
      if (innerWidth <= 850) layout.setLeft(false);
    };
    if (isSettingsCategory(manager)) leaveSettings(open);
    else open();
  }

  function setWorkspace(value: WorkspaceMode, afterOpen?: () => void) {
    // In the tab layout, Studio and Data are a side bar view and a tab; the workspace stays the tab layout.
    if (tree && (value === 'data' || value === 'studio')) {
      const show = () => {
        navigationIntent.current++;
        if (value === 'studio' && !hosts.studioProjectId && project) hosts.setStudioProjectId(project.id);
        setWorkspaceState('agent');
        afterOpen?.();
        layout.showSideBarView(value);
      };
      if (isSettingsCategory(manager)) leaveSettings(show);
      else show();
      return;
    }
    const open = () => {
      navigationIntent.current++;
      setWorkspaceState(value);
      if (value === 'data') hosts.setDataVisited(true);
      if (value === 'studio') {
        if (!hosts.studioVisited) hosts.setStudioProjectId(project?.id || '');
        hosts.setStudioVisited(true);
      }
      setManagerState('');
      afterOpen?.();
      if (innerWidth <= 850) {
        if (value === 'studio') hosts.setStudioSidebarOpen(false);
        else layout.setLeft(false);
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
    setWorkspace('studio', () => hosts.setStudioSection(value));
  }

  async function selectStudioProject(value: string): Promise<boolean> {
    const open = async () => {
      try {
        if (!(await studioProjectGuard.permit(value))) return false;
        hosts.setStudioProjectId(value);
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

  /** Open a manager, settings or Workflows page. Studio and Data are workspaces, not pages. */
  function setManager(value: string) {
    if (value === 'workflows') hosts.setWorkflowRunId(null);
    if (value === 'media') {
      setWorkspace('studio');
      return;
    }
    if (value === 'data') {
      setWorkspace('data');
      return;
    }
    if (tree) {
      if (value) layout.openManager(value);
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
    layout.chooseLayout('workbench');
  }

  function openFile(path: string, line?: number) {
    const root = session?.cwd || project?.path || '',
      relative = workspaceFilePath(path, root);
    if (!session || !relative) {
      report(Error('This file reference is outside the selected conversation checkout.'));
      return;
    }
    if (layout.engine === 'tree') {
      openFileTab(layout.tree.dispatch, {project, session}, relative, line);
      return;
    }
    setFileRequest({id: performance.now(), sessionId: session.id, root, path: relative, line});
    layout.openSurface('editor');
  }

  function attachStudioContext(text: string, attachments: unknown[]) {
    if (hosts.studioProjectId.startsWith('studio_')) {
      report(
        new Error(
          'Choose an Agent project in Studio before attaching media to an Agent conversation. Studio collections keep their media separate.',
        ),
      );
      return;
    }
    if (
      !selected ||
      session?.id !== selected ||
      (session.project_id || null) !== (hosts.studioProjectId || null)
    ) {
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
    layout.chooseLayout('chat');
  }

  // The Workflows page is mounted once it has been opened; a run id only lasts while that page is showing.
  useEffect(() => {
    if (auth && manager === 'workflows') hosts.setWorkflowsVisited(true);
    else hosts.setWorkflowRunId(null);
  }, [auth?.principal.id, manager]);

  useEffect(() => {
    function openRun(event: Event) {
      const runId = (event as CustomEvent<{runId?: unknown}>).detail?.runId;
      if (!authNow.current || lockedNow.current || typeof runId !== 'string' || !/^[a-f0-9]{32}$/.test(runId))
        return;
      const open = () => {
        navigationIntent.current++;
        setWorkspaceState('agent');
        if (tree) {
          layout.openManager('workflows', runId);
          return;
        }
        hosts.setWorkflowsVisited(true);
        hosts.setWorkflowRunId(runId);
        setManagerState('workflows');
      };
      if (isSettingsCategory(managerNow.current)) leaveSettings(open);
      else open();
    }
    window.addEventListener('termx-open-workflow', openRun);
    return () => window.removeEventListener('termx-open-workflow', openRun);
  }, [tree]);

  // Sign-in: start from the address (a session, a project, a workspace mode, a manager page).
  useEffect(() => {
    if (!auth) return;
    hosts.setStudioProjectId('');
    hosts.setStudioSection(readStudioSection());
    void load();
    const route = workspaceRoute(),
      mode = readWorkspaceMode();
    setSelected(route.selected);
    setProjectId(route.project);
    setProjectWorkbenchId(routeWorkbenchProject(route));
    projectWorkbenchNow.current = routeWorkbenchProject(route);
    // In the tab layout `?workspace=data` and `?workspace=studio` open the view and its tab once the layout is restored.
    const viewMode =
      tree && (mode === 'data' || mode === 'studio')
        ? mode
        : tree && route.manager === 'media'
          ? 'studio'
          : tree && route.manager === 'data'
            ? 'data'
            : null;
    pendingView.current = viewMode;
    setWorkspaceState(viewMode ? 'agent' : mode);
    hosts.setStudioVisited(!tree && mode === 'studio');
    hosts.setDataVisited(!tree && mode === 'data');
    hosts.setWorkflowsVisited(route.manager === 'workflows');
    hosts.setWorkflowRunId(null);
    if (tree) {
      // There is no manager overlay in the tab layout: /manage/<page> opens its tab once the layout is restored.
      pendingManager.current = routeManager(route);
      addressTab.current = !!(viewMode || pendingManager.current);
      setManagerState('');
    } else {
      setManager(route.manager);
    }
  }, [auth?.principal.id]);

  useEffect(() => {
    if (!auth || !layout.ready || !pendingView.current) return;
    const view = pendingView.current;
    pendingView.current = null;
    layout.showSideBarView(view);
  }, [auth?.principal.id, layout.ready]);

  useEffect(() => {
    if (!auth || !layout.ready || !pendingManager.current) return;
    const page = pendingManager.current;
    pendingManager.current = '';
    setManager(page);
  }, [auth?.principal.id, layout.ready]);

  // With nothing selected and nothing asked for in the address, open the main agent.
  useEffect(() => {
    if (!auth || !layout.ready || locked || workspace !== 'agent') return;
    const route = workspaceRoute();
    if (!route.selected && !projectWorkbenchId && !manager && !selection.current && !managerNow.current)
      void openMainAgent(true);
  }, [auth?.principal.id, layout.ready, locked, homeRequest, manager, workspace, projectWorkbenchId]);

  useWorkspaceHistory(
    !!auth && layout.ready,
    manager,
    selected,
    workspace,
    hosts.studioSection,
    projectWorkbenchId,
  );
  useWorkspacePopstate({
    manager,
    selected,
    workspace,
    studioSection: hosts.studioSection,
    projectWorkbenchId,
    onRoute: ({route, mode, studioSection: section}) => {
      navigationIntent.current++;
      hosts.setStudioSection(section);
      if (tree && (mode === 'data' || mode === 'studio')) {
        // The tab layout shows Data and Studio as a side bar view and a tab, not as a workspace.
        setWorkspaceState('agent');
        layout.showSideBarView(mode);
      } else {
        setWorkspaceState(mode);
        if (mode === 'data') hosts.setDataVisited(true);
        if (mode === 'studio') {
          if (!hosts.studioVisited) hosts.setStudioProjectId(project?.id || '');
          hosts.setStudioVisited(true);
        }
      }
      setSelected(route.selected);
      setSession(null);
      setProjectId(route.project);
      setProjectWorkbenchId(routeWorkbenchProject(route));
      projectWorkbenchNow.current = routeWorkbenchProject(route);
      if (tree) {
        const page = routeManager(route);
        if (page) layout.openManager(page);
      } else {
        setManagerState(routeManager(route));
      }
      if (mode === 'agent' && route.project && !route.selected && !route.manager) layout.chooseLayout('workbench');
      if (mode === 'agent' && !route.selected && !route.project && !route.manager)
        setHomeRequest(value => value + 1);
    },
  });

  return {
    project,
    projectWorkbenchId,
    setProjectWorkbenchId,
    primaryId,
    setPrimaryId,
    mainState,
    incoming,
    setIncoming,
    fileRequest,
    sessionsState,
    previousManager,
    navigationIntent,
    openMainAgent,
    openConversation,
    setWorkspace,
    selectWorkspace,
    openStudioSection,
    selectStudioProject,
    setManager,
    openProject,
    openFile,
    attachStudioContext,
  };
}

export type WorkspaceNavigation = ReturnType<typeof useWorkspaceNavigation>;
