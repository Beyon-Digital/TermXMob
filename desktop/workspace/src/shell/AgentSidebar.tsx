import {useVirtualizer} from '@tanstack/react-virtual';
import {Archive, Home, Pin, Plus, Search, SlidersHorizontal} from 'lucide-react';
import {useEffect, useMemo, useRef, useState} from 'react';
import {Button} from '../components/ui/button';
import {Input} from '../components/ui/input';
import {Select} from '../components/ui/select';
import WorkspaceMenu from '../components/WorkspaceMenu';
import ProjectGroup from '../features/ProjectGroup';
import {json, request, type Session} from '../lib/api';
import {useAuth} from './AuthGate';
import {useReport} from './errors';
import {buildSidebarRows, sessionListPreview} from './sidebar-rows';
import {useWorkspaceData} from './WorkspaceDataProvider';

const COLLAPSED_KEY = 'termx-collapsed:';

/** A row is taller when it shows a preview line, and on touch screens. */
const previewRowHeight = 68;
const rowHeight = (coarse: boolean, compact = false) => (coarse ? 44 : compact ? 36 : 40);

/**
 * The conversation list's own state: search text and whether the search field is open, the state filter, grouping by
 * project, which project groups are collapsed (remembered per principal), and the virtualized rows. It lives with the
 * shell, not inside the sidebar, so a search or filter survives switching workspaces and back.
 */
export function useSidebarState(options: {primaryId: string; scopeProjectId: string; /** 36px rows, for the IDE side bar. */ compact?: boolean}) {
  const {auth} = useAuth();
  const report = useReport();
  const {sessions, projects, session, projectPins, refresh} = useWorkspaceData();
  const [query, setQuery] = useState('');
  const [searchOpen, setSearchOpen] = useState(false);
  const [sessionFilter, setSessionFilter] = useState('all');
  const [groupedChats, setGroupedChats] = useState(false);
  const [collapsed, setCollapsed] = useState<string[]>([]);
  const [coarse, setCoarse] = useState(() => matchMedia('(pointer: coarse)').matches);
  const scrollRef = useRef<HTMLDivElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const ownerId = auth.principal.id;

  useEffect(() => {
    const media = matchMedia('(pointer: coarse)');
    const changed = () => setCoarse(media.matches);
    media.addEventListener('change', changed);
    return () => media.removeEventListener('change', changed);
  }, []);

  useEffect(() => {
    try {
      const groups = JSON.parse(localStorage.getItem(COLLAPSED_KEY + ownerId) || '[]');
      if (Array.isArray(groups)) setCollapsed(groups.filter(value => typeof value === 'string'));
    } catch {}
  }, [ownerId]);
  useEffect(() => {
    localStorage.setItem(COLLAPSED_KEY + ownerId, JSON.stringify(collapsed));
  }, [collapsed, ownerId]);

  const rows = useMemo(
    () =>
      buildSidebarRows({
        sessions,
        projects,
        primaryId: options.primaryId,
        groupedChats,
        query,
        sessionFilter,
        session,
        collapsed,
        pinnedProjects: projectPins.projects,
        scopeProjectId: options.scopeProjectId,
      }),
    [
      sessions,
      projects,
      query,
      collapsed,
      sessionFilter,
      session?.id,
      session?.latest_status,
      projectPins.projects,
      options.primaryId,
      groupedChats,
      options.scopeProjectId,
    ],
  );
  const previews = useMemo(
    () =>
      new Map(
        rows.flatMap(row =>
          row.kind === 'session'
            ? [[row.key, sessionListPreview(row.chat.id === session?.id ? session : row.chat)] as const]
            : [],
        ),
      ),
    [rows, session?.id, session?.turns, session?.draft_text],
  );
  const virtual = useVirtualizer({
    count: rows.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: index => (previews.get(rows[index]?.key) ? previewRowHeight : rowHeight(coarse, options.compact)),
    overscan: 12,
    getItemKey: index => rows[index].key,
  });
  useEffect(() => {
    virtual.measure();
  }, [coarse, previews, virtual]);

  /** Show the search field and put the cursor in it. */
  function openSearch() {
    setSearchOpen(true);
    requestAnimationFrame(() => searchRef.current?.focus());
  }

  function toggleGroup(id: string) {
    setCollapsed(old => (old.includes(id) ? old.filter(value => value !== id) : [...old, id]));
  }

  /** Pin or archive a conversation against its latest revision, then reload the list. */
  async function updateRow(chat: Session, changes: Record<string, unknown>) {
    try {
      const latest = await request<Session>('/api/workspace/sessions/' + chat.id + '?turns=false');
      await request(
        '/api/workspace/sessions/' + chat.id,
        json('PATCH', {revision: latest.revision, changes}),
      );
      await refresh();
    } catch (reason) {
      report(reason);
    }
  }

  return {
    query,
    setQuery,
    searchOpen,
    setSearchOpen,
    sessionFilter,
    setSessionFilter,
    groupedChats,
    setGroupedChats,
    collapsed,
    toggleGroup,
    rows,
    previews,
    virtual,
    scrollRef,
    searchRef,
    openSearch,
    updateRow,
  };
}

export type SidebarState = ReturnType<typeof useSidebarState>;

type Props = {
  sidebar: SidebarState;
  /** The main agent's conversation id, once known. */
  primaryId: string;
  /** A manager or settings page is open, so no conversation is the current page. */
  manager: string;
  creating: boolean;
  onNewChat: () => void;
  onOpenMainAgent: () => void;
  onOpenConversation: (id: string) => void;
  onFindConversation: () => void;
  /** Show only the conversation list: the IDE side bar has its own New chat and Main agent rows. */
  listOnly?: boolean;
};

/** Chats and agents: New chat, the main agent, filters, search and the virtualized conversation list. */
export default function AgentSidebar(props: Props) {
  const {sidebar, primaryId, manager, creating} = props;
  const {
    session,
    selected,
    setProjectId,
    projectPins,
    pinsReady,
    pinBusy,
    pinProject,
    archived,
    setArchived,
    sessionsState,
  } = useWorkspaceData();
  const {query, rows, previews, virtual} = sidebar;
  const primaryCurrent = !manager && selected === primaryId;
  const statusOf = (chat: Session) => (chat.id === session?.id ? session.latest_status : chat.latest_status);
  return (
    <>
      {!props.listOnly && (
        <>
      <Button
        variant="ghost"
        className="nav-action sidebar-new"
        disabled={creating}
        aria-busy={creating}
        onClick={props.onNewChat}
      >
        <Plus size={16} />
        New chat
      </Button>
      <Button
        variant="ghost"
        aria-label="Main agent"
        aria-current={primaryCurrent ? 'page' : undefined}
        className={'nav-action sidebar-primary' + (primaryCurrent ? ' selected' : '')}
        onClick={props.onOpenMainAgent}
      >
        <Home size={16} aria-hidden="true" />
        <span>Main agent</span>
      </Button>
        </>
      )}
      <div className="sidebar-section-heading">
        <span>{props.listOnly ? 'Chats' : 'Recent chats'}</span>
        <Button
          variant="ghost"
          size="icon"
          aria-label="Find a conversation"
          title="Find a conversation"
          onClick={props.onFindConversation}
        >
          <Search size={16} />
        </Button>
        <WorkspaceMenu label="Conversation filters" icon={<SlidersHorizontal size={16} />}>
          <span className="workspace-menu-label">Conversations</span>
          <label>
            State
            <Select
              aria-label="Conversation state filter"
              value={sidebar.sessionFilter}
              onChange={event => sidebar.setSessionFilter(event.target.value)}
            >
              <option value="all">All conversations</option>
              <option value="running">Running</option>
              <option value="attention">Needs attention</option>
            </Select>
          </label>
          <label>
            Arrange chats
            <Select
              aria-label="Arrange chats"
              value={sidebar.groupedChats ? 'projects' : 'recent'}
              onChange={event => sidebar.setGroupedChats(event.target.value === 'projects')}
            >
              <option value="recent">By recent activity</option>
              <option value="projects">Group by project</option>
            </Select>
          </label>
          <Button variant="ghost" data-close-menu onClick={() => setArchived(value => !value)}>
            <Archive size={16} />
            {archived ? 'Show recent' : 'Show archived'}
          </Button>
        </WorkspaceMenu>
      </div>
      {(sidebar.searchOpen || query) && (
        <Input
          ref={sidebar.searchRef}
          className="workspace-search"
          aria-label="Search conversations"
          placeholder="Find a conversation…"
          value={query}
          onChange={event => sidebar.setQuery(event.target.value)}
          onKeyDown={event => {
            if (event.key === 'Escape') {
              sidebar.setQuery('');
              sidebar.setSearchOpen(false);
              event.stopPropagation();
            }
          }}
        />
      )}
      <div className="sidebar-scroll" ref={sidebar.scrollRef}>
        <div className="session-list" style={{height: virtual.getTotalSize()}}>
          {virtual.getVirtualItems().map(item => {
            const row = rows[item.index];
            return (
              <div
                key={row.key}
                style={{
                  position: 'absolute',
                  height: item.size,
                  transform: `translateY(${item.start}px)`,
                  width: '100%',
                }}
              >
                {row.kind === 'project' ? (
                  <ProjectGroup
                    id={row.id}
                    name={row.name}
                    collapsed={sidebar.collapsed.includes(row.id)}
                    pinned={projectPins.projects.includes(row.id)}
                    busy={!pinsReady || !!pinBusy}
                    onSelect={() => {
                      setProjectId(row.id);
                      sidebar.toggleGroup(row.id);
                    }}
                    onPin={() => void pinProject(row.id)}
                  />
                ) : (
                  <div
                    className={'session-row ' + (selected === row.chat.id ? 'selected' : '')}
                    data-has-preview={!!previews.get(row.key)}
                  >
                    <button
                      aria-current={selected === row.chat.id && !manager ? 'page' : undefined}
                      onClick={() => props.onOpenConversation(row.chat.id)}
                    >
                      <span className="session-row-copy">
                        <span className="session-row-title">{row.chat.title || 'Untitled conversation'}</span>
                        {previews.get(row.key) && (
                          <span className="session-row-preview">{previews.get(row.key)}</span>
                        )}
                      </span>
                      {statusOf(row.chat) && (
                        <span
                          className={'status-dot ' + statusOf(row.chat)}
                          title={statusOf(row.chat)?.replaceAll('_', ' ')}
                        />
                      )}{' '}
                      {row.chat.pinned && <Pin size={11} />}
                    </button>
                    <div className="row-actions">
                      <button
                        aria-label={'Pin ' + row.chat.title}
                        onClick={() => sidebar.updateRow(row.chat, {pinned: !row.chat.pinned})}
                      >
                        <Pin size={12} />
                      </button>
                      <button
                        aria-label={'Archive ' + row.chat.title}
                        onClick={() => sidebar.updateRow(row.chat, {archived: !row.chat.archived})}
                      >
                        <Archive size={12} />
                      </button>
                    </div>
                  </div>
                )}
              </div>
            );
          })}
        </div>
        {!rows.length && (
          <p className="sidebar-empty">
            {sessionsState === 'loading'
              ? 'Loading conversations…'
              : sessionsState === 'failed'
                ? 'Conversations unavailable. Use Refresh in Commands to retry.'
                : query
                  ? 'No matching conversations.'
                  : 'Your other chats will appear here.'}
          </p>
        )}
      </div>
    </>
  );
}
