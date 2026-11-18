import type {Project, Session} from '../lib/api';
import {orderProjectGroups} from '../lib/project-pins';
import {scopeProjects} from '../lib/project-labels';
import {taskState} from '../lib/task-presentation';

/** Only use content already available in the list or the selected snapshot. */
export function sessionListPreview(session: Pick<Session, 'draft_text' | 'turns'>): string {
  const draft = session.draft_text?.replace(/\s+/g, ' ').trim();
  if (draft) return 'Draft: ' + draft.slice(0, 200);
  const turn = [...(session.turns || [])].sort((a, b) => b.sequence - a.sequence)[0];
  return (turn?.task?.result || turn?.prompt || '')
    .replace(/^\s*(?:`{3,}|~{3,})(?:[a-z][\w+-]*)?\s*$/gim, '')
    .replace(/^\s*(?:(?:#{1,6}|>|[-*+]|\d+[.)])\s+)+/gm, '')
    .replace(/`+([^`]+)`+/g, '$1')
    .replace(/\s+/g, ' ')
    .trim()
    .slice(0, 200);
}

export type SessionFilter = 'all' | 'running' | 'attention';

export type SidebarRow =
  | {kind: 'session'; key: string; chat: Session}
  | {kind: 'project'; key: string; name: string; id: string};

type RowInput = {
  sessions: Session[];
  projects: Project[];
  /** The main agent has its own button above the list. */
  primaryId: string;
  groupedChats: boolean;
  query: string;
  sessionFilter: string;
  /** The open conversation's live snapshot: its status is newer than the list's. */
  session: Pick<Session, 'id' | 'latest_status'> | null;
  collapsed: string[];
  pinnedProjects: string[];
  /** The project whose (possibly empty) group stays listed when nothing is filtered. */
  scopeProjectId: string;
};

const byPinnedThenRecent = (a: Session, b: Session) =>
  Number(b.pinned) - Number(a.pinned) || b.updated_at - a.updated_at;

/**
 * What the conversation list shows: a flat list by recent activity, or conversations grouped under their projects
 * (pinned projects first, collapsed groups hidden unless searching). The filter, the search text and the open
 * conversation's fresher status all apply.
 */
export function buildSidebarRows(input: RowInput): SidebarRow[] {
  const {sessions, projects, primaryId, groupedChats, query, sessionFilter, session, collapsed} = input;
  const needle = query.toLowerCase();
  const statusOf = (chat: Session) => (chat.id === session?.id ? session.latest_status : chat.latest_status);
  const matching = sessions.filter(chat => chat.id !== primaryId);
  if (!groupedChats) {
    return matching
      .filter(chat => {
        const text = [chat.title, projects.find(project => project.id === chat.project_id)?.name]
          .join(' ')
          .toLowerCase();
        const status = statusOf(chat);
        return (
          text.includes(needle) &&
          (sessionFilter === 'all' ||
            (sessionFilter === 'attention' && taskState(status || '').attention) ||
            (sessionFilter === 'running' && taskState(status || '').active))
        );
      })
      .sort(byPinnedThenRecent)
      .map(chat => ({kind: 'session' as const, key: chat.id, chat}));
  }
  const groups = new Map<string, Session[]>();
  for (const chat of [...matching].sort(byPinnedThenRecent)) {
    if (!chat.title.toLowerCase().includes(needle)) continue;
    const status = statusOf(chat);
    if (sessionFilter === 'attention' && !taskState(status || '').attention) continue;
    if (sessionFilter === 'running' && !taskState(status || '').active) continue;
    const id = (chat.group_id === undefined ? chat.project_id : chat.group_id) || '';
    groups.set(id, [...(groups.get(id) || []), chat]);
  }
  if (sessionFilter === 'all' && !query) {
    for (const item of scopeProjects(projects, input.scopeProjectId)) {
      if (!groups.has(item.id)) groups.set(item.id, []);
    }
  }
  return orderProjectGroups([...groups.entries()], input.pinnedProjects).flatMap(([id, chats]) => [
    {
      kind: 'project' as const,
      key: 'project:' + id,
      name: projects.find(project => project.id === id)?.name || 'Unassigned',
      id,
    },
    ...(collapsed.includes(id) && !query
      ? []
      : chats.map(chat => ({kind: 'session' as const, key: chat.id, chat}))),
  ]);
}
