import {
  useLayoutEffect,
  useRef,
  useState,
  type Dispatch,
  type MutableRefObject,
  type SetStateAction,
} from 'react';
import {readWorkspaceMode, type WorkspaceMode} from '../lib/workspace-mode';
import {routeManager, workspaceRoute} from './routes';

/**
 * Which workspace is showing and which manager or settings page covers it. These two values sit above the data
 * provider because what the provider observes depends on them; everything that moves between them (leaving
 * settings, choosing a layout, opening a conversation) stays in the shell.
 */
export interface ShellNavigation {
  workspace: WorkspaceMode;
  setWorkspaceState: Dispatch<SetStateAction<WorkspaceMode>>;
  /** `''` when no manager or settings page is open. */
  manager: string;
  setManagerState: Dispatch<SetStateAction<string>>;
  /** The latest values, for async callbacks and event handlers. */
  workspaceNow: MutableRefObject<WorkspaceMode>;
  managerNow: MutableRefObject<string>;
}

export function useShellNavigation(): ShellNavigation {
  const [workspace, setWorkspaceState] = useState<WorkspaceMode>(() => readWorkspaceMode());
  const [manager, setManagerState] = useState(() => routeManager(workspaceRoute()));
  const workspaceNow = useRef(workspace);
  const managerNow = useRef(manager);
  useLayoutEffect(() => {
    workspaceNow.current = workspace;
  });
  useLayoutEffect(() => {
    managerNow.current = manager;
  });
  return {workspace, setWorkspaceState, manager, setManagerState, workspaceNow, managerNow};
}
