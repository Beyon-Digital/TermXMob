import {createContext, useCallback, useContext, useMemo, useState, type ReactNode} from 'react';
import {
  CLI_EVENTS_QUERY,
  applyCliEvent,
  attentionCount,
  createCliSession,
  interruptCliSession,
  listCliAgents,
  listCliSessions,
  orderSessions,
  resumableCount,
  resumeCliSession,
  sessionPlace,
  stopCliSession,
  type CliAgent,
  type CliSession,
  type CliSessionEvent,
  type NewCliSession,
} from '../../lib/cli-sessions';
import {useGraphqlSubscription} from '../../lib/graphql-subscription';
import {usePolling} from '../../lib/use-polling';
import type {Project} from '../../lib/api';

const message = (reason: unknown) => (reason instanceof Error ? reason.message : String(reason));

export type CliSessionsState = {
  /** Sessions that need the owner first, then the rest. */
  sessions: CliSession[];
  loaded: boolean;
  /** False when the host does not offer CLI sessions to this sign-in (no terminal scope): the views stay quiet. */
  available: boolean;
  error: string;
  attention: number;
  resumable: number;
  get: (id: string) => CliSession | undefined;
  /** Where a session runs, in words: its worktree branch, its project's name, or its folder. */
  placeOf: (session: CliSession) => string;
  /** The installed and missing CLIs, read when the new-session dialog opens. */
  loadAgents: () => Promise<CliAgent[]>;
  create: (body: NewCliSession) => Promise<CliSession>;
  interrupt: (id: string) => Promise<void>;
  stop: (id: string) => Promise<void>;
  resume: (id: string) => Promise<CliSession>;
  refresh: () => void;
};

const empty: CliSessionsState = {
  sessions: [],
  loaded: false,
  available: false,
  error: '',
  attention: 0,
  resumable: 0,
  get: () => undefined,
  placeOf: () => '',
  loadAgents: async () => [],
  create: async () => {
    throw new Error('CLI sessions are not available.');
  },
  interrupt: async () => {},
  stop: async () => {},
  resume: async () => {
    throw new Error('CLI sessions are not available.');
  },
  refresh: () => {},
};

const Context = createContext<CliSessionsState>(empty);

/** The CLI sessions of the signed-in owner, shared by the Agents view, the status counts and every CLI tab. */
export function useCliSessions(): CliSessionsState {
  return useContext(Context);
}

type Props = {enabled: boolean; /** The registered projects, so a session can be labelled by project name. */ projects?: readonly Project[]; children: ReactNode};

/**
 * One list and one subscription for the whole window. The `cli_session_events` stream keeps the list current while it is
 * live and a slow poll stays as the fallback (the native shell has no socket). A sign-in without `terminal-view` gets an
 * unavailable state and no requests.
 */
export function CliSessionsProvider({enabled, projects = [], children}: Props) {
  const [rows, setRows] = useState<CliSession[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [available, setAvailable] = useState(true);
  const [error, setError] = useState('');
  const [live, setLive] = useState(false);
  const [resetKey, setResetKey] = useState(0);
  const refresh = useCallback(() => setResetKey(key => key + 1), []);

  useGraphqlSubscription<CliSessionEvent>(
    {
      query: CLI_EVENTS_QUERY,
      field: 'cli_session_events',
      onEvent: event => setRows(current => applyCliEvent(current, event)),
      onLive: setLive,
    },
    enabled,
  );

  usePolling(() => listCliSessions(), {
    interval: live ? 20000 : 5000,
    backoff: false,
    enabled,
    resetKey,
    onResult: value => {
      setRows(value);
      setLoaded(true);
      setAvailable(true);
      setError('');
    },
    onError: reason => {
      setLoaded(true);
      const text = message(reason);
      if (/forbidden|403|missing .*authority|scope/i.test(text)) setAvailable(false);
      else setError(text);
    },
  });

  const sessions = useMemo(() => orderSessions(rows), [rows]);
  const value = useMemo<CliSessionsState>(
    () => ({
      sessions,
      loaded,
      available: enabled && available,
      error,
      attention: attentionCount(sessions),
      resumable: resumableCount(sessions),
      get: id => rows.find(row => row.id === id),
      placeOf: session => sessionPlace(session, projects.find(project => project.id === session.project_id)?.name),
      loadAgents: listCliAgents,
      create: async body => {
        const session = await createCliSession(body);
        setRows(current => applyCliEvent(current, {type: 'cli.session.created', session}));
        refresh();
        return session;
      },
      interrupt: async id => {
        await interruptCliSession(id);
      },
      stop: async id => {
        await stopCliSession(id);
        refresh();
      },
      resume: async id => {
        const session = await resumeCliSession(id);
        setRows(current => applyCliEvent(current, {type: 'cli.session.created', session}));
        refresh();
        return session;
      },
      refresh,
    }),
    [sessions, rows, projects, loaded, enabled, available, error, refresh],
  );
  return <Context.Provider value={value}>{children}</Context.Provider>;
}
