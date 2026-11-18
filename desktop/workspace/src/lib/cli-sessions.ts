import {json, request} from './api';

/** `Session` from docs/cli-sessions.md, narrowed to what the workspace reads. The prompt, argv and tokens are never returned. */
export interface CliSession {
  id: string;
  cli: string;
  label: string | null;
  title: string | null;
  status: string;
  status_source: string | null;
  status_detail: string | null;
  status_updated_at: number | null;
  attention: boolean | null;
  project_id: string | null;
  cwd: string | null;
  isolation: string | null;
  worktree: {id?: string; path?: string; branch?: string} | null;
  pty_session_id: string | null;
  mode: string | null;
  started_at: number | null;
  updated_at: number | null;
  ended_at: number | null;
  exit_code: number | null;
  ended_reason: string | null;
  resumable: boolean | null;
  resumed_from: string | null;
  resumed_to: string | null;
}

/** One entry of `GET /api/cli-agents`. */
export interface CliAgent {
  id: string;
  label: string;
  installed: boolean;
  version: string | null;
  hooks: string;
  resume: boolean;
  modes: string[];
  verified: boolean;
  note: string;
}

export interface CliSessionEvent {
  type: string;
  session: CliSession | null;
}

export interface NewCliSession {
  cli: string;
  project_id?: string;
  prompt?: string;
  isolation: 'none' | 'worktree';
  /** Bypass is never offered by the workspace. */
  mode: 'default' | 'read_only';
}

export const CLI_EVENTS_QUERY =
  'subscription{cli_session_events{type session{id cli label title status status_source status_detail status_updated_at attention project_id cwd isolation worktree pty_session_id mode started_at updated_at ended_at exit_code ended_reason resumable resumed_from resumed_to}}}';

export const listCliAgents = async () => (await request<{agents: CliAgent[]}>('/api/cli-agents')).agents;
export const listCliSessions = async () => (await request<{sessions: CliSession[]}>('/api/cli-sessions')).sessions;
export const createCliSession = (body: NewCliSession) => request<CliSession>('/api/cli-sessions', json('POST', body));
export const interruptCliSession = (id: string) => request<unknown>('/api/cli-sessions/' + encodeURIComponent(id) + '/interrupt', json('POST', {}));
export const stopCliSession = (id: string) => request<unknown>('/api/cli-sessions/' + encodeURIComponent(id) + '/stop', json('POST', {}));
export const resumeCliSession = (id: string) => request<CliSession>('/api/cli-sessions/' + encodeURIComponent(id) + '/resume', json('POST', {}));

export type StatusTone = 'busy' | 'quiet' | 'attention' | 'ok' | 'danger';

export interface StatusPresentation {
  label: string;
  tone: StatusTone;
}

const STATUS: Record<string, StatusPresentation> = {
  starting: {label: 'Starting', tone: 'busy'},
  working: {label: 'Working', tone: 'busy'},
  waiting_input: {label: 'Waiting for input', tone: 'quiet'},
  needs_approval: {label: 'Needs approval', tone: 'attention'},
  idle: {label: 'Idle', tone: 'quiet'},
  done: {label: 'Done', tone: 'ok'},
  failed: {label: 'Failed', tone: 'danger'},
  lost: {label: 'Lost', tone: 'danger'},
};

/** The words and tone for a status. An unknown status from a newer host is shown as it is, quietly. */
export function statusPresentation(status: string): StatusPresentation {
  return STATUS[status] ?? {label: status.replace(/_/g, ' '), tone: 'quiet'};
}

const ENDED = ['done', 'failed', 'lost'];
export const isEnded = (session: Pick<CliSession, 'status'>) => ENDED.includes(session.status);
export const needsPerson = (session: Pick<CliSession, 'status' | 'attention'>) => !!session.attention && !isEnded(session);
export const canResume = (session: CliSession) => isEnded(session) && !!session.resumable && !session.resumed_to;

/** What the session is called in a list: the host's title, else the CLI's label. */
export const sessionName = (session: CliSession) => session.title || session.label || session.cli;

/** Sessions that need the owner first (needs approval before waiting), then the rest, newest first. */
export function orderSessions(sessions: readonly CliSession[]): CliSession[] {
  const rank = (session: CliSession) => (session.status === 'needs_approval' && !isEnded(session) ? 0 : needsPerson(session) ? 1 : isEnded(session) ? 3 : 2);
  return [...sessions].sort((a, b) => rank(a) - rank(b) || (b.updated_at ?? b.started_at ?? 0) - (a.updated_at ?? a.started_at ?? 0) || a.id.localeCompare(b.id));
}

/** Fold one subscription event into the list. */
export function applyCliEvent(sessions: readonly CliSession[], event: CliSessionEvent): CliSession[] {
  const next = event.session;
  if (!next) return sessions as CliSession[];
  return sessions.some(row => row.id === next.id) ? sessions.map(row => (row.id === next.id ? {...row, ...next} : row)) : [next, ...sessions];
}

/** How many sessions are waiting on the owner. Feeds the "N need you" count. */
export const attentionCount = (sessions: readonly CliSession[]) => sessions.filter(needsPerson).length;

/** Resumable lost sessions: the CLI's own conversation can be picked up again. */
export const resumableCount = (sessions: readonly CliSession[]) => sessions.filter(canResume).length;

/**
 * Where a session runs, for the secondary line: its worktree branch, else its project's name, else the last folder of its
 * path (a scratch folder's path is an opaque id, so the project name is the better label whenever there is one).
 */
export function sessionPlace(session: CliSession, projectName?: string): string {
  if (session.worktree?.branch) return session.worktree.branch;
  if (projectName) return projectName;
  const folder = (session.cwd ?? '').split('/').filter(Boolean).slice(-1).join('');
  return /^[a-f0-9]{16,}$/i.test(folder) ? '' : folder;
}
