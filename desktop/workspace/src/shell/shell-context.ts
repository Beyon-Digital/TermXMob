import type {Project, Session} from '../lib/api';
import {latestExecutionTask} from '../lib/conversation-messages';
import {folderName} from '../lib/path-label';
import {isSettingsCategory} from '../lib/settings-categories';
import type {WorkspaceMode} from '../lib/workspace-mode';
import {managerTitle} from './manager-titles';

/** Facts the header and status bar show, derived from where the person is. Pure: no state of its own. */

export type ShellContextInput = {
  workspace: WorkspaceMode;
  manager: string;
  /** The page behind settings, or the manager itself when it is not settings. */
  previousManager: string;
  session: Session | null;
  selected: string;
  project: Project | null;
  projectWorkbenchId: string;
  sessionsState: string;
  studioProject: ({name: string} & {path?: string}) | null;
  studioProjectId: string;
};

export function isControlPage(manager: string) {
  return ['overview', 'projects', 'tasks'].includes(manager);
}

/** The page the Settings overlay sits on top of: the previous page, or the manager itself. */
export function pageBehindSettings(manager: string, previousManager: string) {
  return isSettingsCategory(manager) ? previousManager : manager;
}

/** The label of the way back from a manager page. */
export function managerReturnLabel(input: ShellContextInput): string {
  const {manager, previousManager, workspace, projectWorkbenchId, selected} = input;
  return isSettingsCategory(manager) && previousManager
    ? 'Back to ' + (managerTitle[previousManager] || 'workspace')
    : workspace === 'data'
      ? 'Back to Data'
      : workspace === 'studio'
        ? 'Back to Studio'
        : projectWorkbenchId && !selected
          ? 'Back to files'
          : 'Back to chat';
}

/** The running browser task of the selected conversation, for the Browser surface to follow. */
export function runningBrowserTaskId(session: Session | null): string | null {
  const task = latestExecutionTask(session?.turns || []);
  return task && ['planning', 'running', 'awaiting_approval', 'paused', 'recovering'].includes(task.status)
    ? task.id
    : null;
}

/** Where this window is, in words: the project, Studio project or Data workspace. */
export function statusContext(input: ShellContextInput): string {
  const {workspace, session, project, projectWorkbenchId, sessionsState, studioProject, studioProjectId} = input;
  return workspace === 'data'
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
}

/** The path shown beside the context, when it has one. */
export function statusContextPath(input: ShellContextInput): string | undefined {
  const {workspace, session, project, studioProject} = input;
  return workspace === 'data'
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
}
