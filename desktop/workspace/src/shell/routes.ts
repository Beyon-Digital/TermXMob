import {useEffect, useLayoutEffect, useRef} from 'react';
import {SETTINGS_LEAVE} from '../features/settings-forms';
import {nativeWorkspace} from '../lib/native';
import {
  readStudioSection,
  readWorkspaceMode,
  workspaceHistoryTarget,
  type StudioSection,
  type WorkspaceMode,
} from '../lib/workspace-mode';

/**
 * URL <-> workspace state.
 *
 * Routes the shell understands:
 *   /session/<id>            a selected conversation (also ?session=<id> for detached windows)
 *   /manage/<category>       a control-plane page, a settings category, or the legacy media/data deep links
 *   ?workspace=studio|data   the workspace mode, with ?studio=<section> and the data_* parameters
 *   ?project=<id>            a project's files when no conversation is selected
 *   ?layout=<name>           the initial panel arrangement of a detached window
 */
export type WorkspaceRoute = {selected: string; manager: string; project: string};

export function workspaceRoute(pathname = location.pathname, search = location.search): WorkspaceRoute {
  const params = new URLSearchParams(search);
  const sessionRoute = pathname.match(/^\/session\/([^/]+)/);
  const manageRoute = pathname.match(/^\/manage\/([^/]+)/);
  return {
    selected: sessionRoute ? decodeURIComponent(sessionRoute[1]) : params.get('session') || '',
    manager: manageRoute ? decodeURIComponent(manageRoute[1]) : '',
    project: params.get('project') || '',
  };
}

/** Panel arrangements a detached window may ask for with `?layout=`. `focus` is never restored from a URL. */
export const ROUTE_LAYOUTS = ['chat', 'workbench', 'browser', 'computer', 'artifacts', 'review', 'terminal'] as const;
export type RouteLayout = (typeof ROUTE_LAYOUTS)[number];

export function routeLayout(search = location.search): RouteLayout | null {
  const value = new URLSearchParams(search).get('layout') || '';
  return (ROUTE_LAYOUTS as readonly string[]).includes(value) ? (value as RouteLayout) : null;
}

/** `/manage/media` and `/manage/data` are workspace modes, not manager pages. */
export function routeManager(route: Pick<WorkspaceRoute, 'manager'>): string {
  return ['media', 'data'].includes(route.manager) ? '' : route.manager;
}

/** A project is opened as a file workbench only while no conversation is selected. */
export function routeWorkbenchProject(route: Pick<WorkspaceRoute, 'selected' | 'project'>): string {
  return route.selected ? '' : route.project;
}

/** The layout a route opens before any saved arrangement applies, or `null` to use the saved one. */
export function routeInitialLayout(route: WorkspaceRoute, search = location.search): RouteLayout | null {
  if (route.project && !route.selected) return 'workbench';
  return routeLayout(search);
}

export function useWorkspaceThemeScope(workspace: WorkspaceMode = 'agent') {
  useLayoutEffect(() => {
    document.documentElement.dataset.appWorkspace = workspace;
    return () => {
      delete document.documentElement.dataset.appWorkspace;
    };
  }, [workspace]);
}

export function useWorkspaceHistory(
  authenticated: boolean,
  manager: string,
  selected: string,
  workspace: WorkspaceMode = 'agent',
  studioSection: StudioSection = 'create',
  project = '',
) {
  useEffect(() => {
    // Native IPC and media grants are bound to the exact root workspace URL.
    // Its windows use query parameters for initial selection, not browser routes.
    if (!authenticated || nativeWorkspace()) return;
    const target = workspaceHistoryTarget(manager, selected, workspace, location.search, studioSection, project);
    if (location.pathname + location.search === target) return;
    // `/` only exists to land on a conversation; keeping it as its own entry makes Back bounce forward again.
    if (location.pathname === '/' && !location.search && target.startsWith('/session/')) {
      history.replaceState(null, '', target);
    } else {
      history.pushState(null, '', target);
    }
  }, [authenticated, manager, selected, workspace, studioSection, project]);
}

export type PopstateTarget = {route: WorkspaceRoute; mode: WorkspaceMode; studioSection: StudioSection};

type PopstateOptions = {
  manager: string;
  selected: string;
  workspace: WorkspaceMode;
  studioSection: StudioSection;
  projectWorkbenchId: string;
  /** Applies the route the browser went back or forward to. Called at most once per navigation. */
  onRoute: (target: PopstateTarget) => void;
};

/**
 * Back and Forward. Unsaved settings can veto the navigation (the `SETTINGS_LEAVE` event is cancelable);
 * then the URL is put back where it was and `onRoute` runs only if the settings later let go.
 */
export function useWorkspacePopstate(options: PopstateOptions) {
  const latest = useRef(options);
  useLayoutEffect(() => {
    latest.current = options;
  });
  const {manager, selected, workspace, studioSection, projectWorkbenchId} = options;
  useEffect(() => {
    const onPop = () => {
      const route = workspaceRoute();
      const mode = readWorkspaceMode();
      // The studio section is read when the navigation is applied, as it always was, not when it was requested.
      const proceed = () => latest.current.onRoute({route, mode, studioSection: readStudioSection()});
      const allowed = window.dispatchEvent(new CustomEvent(SETTINGS_LEAVE, {cancelable: true, detail: {proceed}}));
      if (!allowed) {
        history.pushState(
          null,
          '',
          workspaceHistoryTarget(manager, selected, workspace, location.search, studioSection, projectWorkbenchId),
        );
        return;
      }
      proceed();
    };
    window.addEventListener('popstate', onPop);
    return () => window.removeEventListener('popstate', onPop);
  }, [manager, selected, workspace, studioSection, projectWorkbenchId]);
}
