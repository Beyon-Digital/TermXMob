import {json, request, type Project} from '../../lib/api';
import type {ActionRecord, DeliveryMeta, Prepared, PullRequest, Worktree} from './scm-types';

/**
 * The host's delivery API, as the old Delivery panel used it: every write is prepared first (the host records the exact
 * operation and the checkout it saw), then executed once. Publishing operations also wait for a confirmation.
 */

const base = (project: Project) => '/api/development/projects/' + project.id + '/delivery';

const withTree = (worktreeId: string) => (worktreeId ? '?worktree_id=' + encodeURIComponent(worktreeId) : '');

type StatusResponse = {
  status: {branch?: string; ahead?: number; behind?: number; files: unknown[]};
  worktrees: Worktree[];
  remotes?: string[];
};

export async function loadDelivery(project: Project, worktreeId: string): Promise<DeliveryMeta> {
  const data = await request<StatusResponse>(base(project) + withTree(worktreeId));
  return {
    branch: data.status.branch,
    ahead: data.status.ahead ?? 0,
    behind: data.status.behind ?? 0,
    remotes: data.remotes ?? [],
    worktrees: data.worktrees,
  };
}

export function loadActions(project: Project): Promise<ActionRecord[]> {
  return request<ActionRecord[]>(base(project) + '/actions');
}

export function prepareOperation(project: Project, worktreeId: string, operation: string, args: Record<string, unknown> = {}) {
  const body = {operation, arguments: {...(worktreeId ? {worktree_id: worktreeId} : {}), ...args}};
  return request<Prepared>(base(project) + '/prepare', json('POST', body));
}

export type Outcome = {status: string; result: {error?: string} | null};

export function executeOperation(project: Project, action: Prepared, confirmed: boolean) {
  return request<Outcome>(base(project) + '/actions/' + action.id + '/execute', json('POST', {confirmed}));
}

export function readOutcome(project: Project, id: string) {
  return request<{status: string; result: string | null}>(base(project) + '/actions/' + id);
}

export function readPull(project: Project, number: string) {
  return request<PullRequest>(base(project) + '/pulls/' + number);
}

export async function readDeliveryDiff(project: Project, worktreeId: string, path: string, staged: boolean): Promise<string> {
  const query = 'path=' + encodeURIComponent(path) + '&staged=' + staged + (worktreeId ? '&worktree_id=' + encodeURIComponent(worktreeId) : '');
  const data = await request<{diff: string}>(base(project) + '/diff?' + query);
  return data.diff;
}

/** Prepare and, when nothing needs a confirmation, run an operation. A rejected outcome throws with the host's reason. */
export async function runOperation(project: Project, worktreeId: string, operation: string, args: Record<string, unknown> = {}) {
  const action = await prepareOperation(project, worktreeId, operation, args);
  if (action.requires_confirmation) return {action, result: undefined as unknown};
  return {action: undefined, result: await finish(project, action, false)};
}

/** Run a prepared operation and check its recorded outcome. */
export async function finish(project: Project, action: Prepared, confirmed: boolean) {
  const outcome = await executeOperation(project, action, confirmed);
  if (outcome.status !== 'completed') {
    throw new Error(outcome.result?.error || 'The host reports "' + outcome.status + '". Check the repository before trying again.');
  }
  return outcome.result;
}
