import {vi} from 'vitest';
import type {Project, Session} from '../lib/api';
import type {SessionChangeFile, SessionChangesSource} from '../lib/session-changes';

/** Fixtures for the Source control view and the diff tab: a project, a conversation, changed files and a fake delivery host. */

export const project: Project = {id: 'project', name: 'Project', path: '/work'};

export const session = (extra: Partial<Session> = {}): Session => ({
  id: 'chat',
  project_id: 'project',
  cwd: '/work',
  title: 'Review',
  engine: 'internal',
  mode: 'agent',
  pinned: false,
  archived: false,
  draft_text: '',
  revision: 3,
  updated_at: 0,
  scroll: 0,
  ...extra,
});

export function file(path: string, index: string, worktree: string, extra: Partial<SessionChangeFile> = {}): SessionChangeFile {
  const untracked = index === '?';
  return {
    path,
    status: untracked || index === 'A' ? 'added' : index === 'D' || worktree === 'D' ? 'deleted' : 'modified',
    index,
    worktree,
    staged: index !== ' ' && index !== '?',
    untracked,
    additions: 1,
    deletions: 0,
    binary: false,
    reviewable: true,
    ...extra,
  };
}

export function source(files: SessionChangeFile[], extra: Partial<SessionChangesSource> = {}): SessionChangesSource {
  return {
    scope: 's',
    available: true,
    unavailableReason: null,
    loading: false,
    error: null,
    refresh: vi.fn(async () => {}),
    data: {repo: true, branch: 'main', head: 'abc', files, additions: 0, deletions: 0, counts_complete: true, truncated: false},
    ...extra,
  };
}

export type Call = {path: string; method: string; body: Record<string, unknown> | undefined};

type HostOptions = {
  branch?: string;
  ahead?: number;
  behind?: number;
  remotes?: string[];
  worktrees?: {id: string; path: string; branch: string}[];
  records?: unknown[];
  /** Outcome of every execute, by operation. */
  outcome?: Record<string, {status: string; result: unknown}>;
};

const NEEDS_CONFIRMATION = new Set(['push', 'pr-create', 'pr-update', 'pr-comment', 'pr-review', 'worktree-remove']);

/** A fake delivery host: answers the status, prepare and execute calls and records every call. */
export function deliveryHost(options: HostOptions = {}) {
  const calls: Call[] = [];
  const prepared = new Map<string, {operation: string; arguments: Record<string, unknown>}>();
  const handler = async (path: string, init?: RequestInit) => {
    const body = init?.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : undefined;
    calls.push({path, method: init?.method ?? 'GET', body});
    if (/\/delivery(\?|$)/.test(path)) {
      return {
        status: {branch: options.branch ?? 'main', ahead: options.ahead ?? 0, behind: options.behind ?? 0, files: []},
        worktrees: options.worktrees ?? [],
        remotes: options.remotes ?? ['origin'],
      };
    }
    if (path.endsWith('/delivery/actions')) return options.records ?? [];
    if (path.endsWith('/prepare')) {
      const operation = String(body?.operation);
      const id = 'a' + (prepared.size + 1);
      const args = (body?.arguments ?? {}) as Record<string, unknown>;
      prepared.set(id, {operation, arguments: args});
      return {id, operation, arguments: args, head: 'abcdef0123456', requires_confirmation: NEEDS_CONFIRMATION.has(operation)};
    }
    const execute = /\/actions\/(a\d+)\/execute$/.exec(path);
    if (execute) {
      const operation = prepared.get(execute[1])?.operation ?? '';
      return options.outcome?.[operation] ?? {status: 'completed', result: operation === 'pr-create' ? {number: 12, html_url: 'https://example.test/pull/12'} : {}};
    }
    if (path.startsWith('/api/workspace/sessions/')) return {};
    throw new Error('Unexpected request ' + path);
  };
  const operations = () =>
    calls.filter(call => call.path.endsWith('/prepare')).map(call => ({operation: call.body?.operation as string, arguments: call.body?.arguments as Record<string, unknown>}));
  const executed = () => calls.filter(call => call.path.endsWith('/execute')).map(call => call.body);
  return {handler, calls, operations, executed};
}
