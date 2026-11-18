import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type Dispatch,
  type MutableRefObject,
  type ReactNode,
  type SetStateAction,
} from 'react';
import {gql, type Engine, type Project, type Provider, type Session} from '../lib/api';
import {changeProjectPin, loadProjectPins, type ProjectPins} from '../lib/project-pins';
import {useObservationActive} from '../lib/use-observation-active';
import {usePolling} from '../lib/use-polling';
import {useSessionSnapshot} from '../lib/use-session-snapshot';
import type {WorkspaceMode} from '../lib/workspace-mode';
import {listSessions, SessionCache} from '../lib/workspace-data';
import {useAuth} from './AuthGate';
import {useReport} from './errors';
import {useUpdateEffect} from './use-update-effect';

/**
 * What the workspace knows about the host: projects, the conversation list, engines and providers, the selected
 * conversation and its live snapshot, and the pinned projects. It loads them, polls the list while the Agent workspace
 * is being observed, and keeps everything keyed to the signed-in principal (it lives inside `AuthGate`, so a new
 * sign-in starts from nothing).
 *
 * Where the person is going (which conversation, which page) is the shell's decision; it tells this provider the
 * current `workspace` and `manager` so observation pauses when the Agent workspace is not what is on screen.
 */
export type LoadState = 'loading' | 'ready' | 'failed';

export interface WorkspaceData {
  projects: Project[];
  sessions: Session[];
  engines: Engine[];
  providers: Provider[];
  sessionsState: LoadState;
  catalogState: LoadState;
  configurationState: LoadState;
  /** The selected conversation id, `''` for none. */
  selected: string;
  setSelected: Dispatch<SetStateAction<string>>;
  /** The latest `selected` for async callbacks. */
  selection: MutableRefObject<string>;
  /** The project chosen in the sidebar or a project workbench, `''` for none. */
  projectId: string;
  setProjectId: Dispatch<SetStateAction<string>>;
  session: Session | null;
  setSession: Dispatch<SetStateAction<Session | null>>;
  sessionCache: MutableRefObject<SessionCache>;
  archived: boolean;
  setArchived: Dispatch<SetStateAction<boolean>>;
  /** The Agent workspace is on screen, the window is in use and nothing covers it. */
  workspaceActive: boolean;
  load: () => Promise<void>;
  /** Reload everything, then the selected conversation. */
  refresh: () => Promise<void>;
  projectPins: ProjectPins;
  pinsReady: boolean;
  pinBusy: string;
  pinProject: (id: string) => Promise<void>;
}

const DataContext = createContext<WorkspaceData | null>(null);

export function useWorkspaceData(): WorkspaceData {
  const value = useContext(DataContext);
  if (!value) throw new Error('useWorkspaceData needs a WorkspaceDataProvider');
  return value;
}

const noPins: ProjectPins = {revision: 0, projects: []};

type ProviderProps = {
  workspace: WorkspaceMode;
  manager: string;
  children: ReactNode;
};

export default function WorkspaceDataProvider({workspace, manager, children}: ProviderProps) {
  const {auth, authEpoch, authNow, fence, locked, onUnlocked} = useAuth();
  const report = useReport();
  const [projects, setProjects] = useState<Project[]>([]);
  const [sessions, setSessions] = useState<Session[]>([]);
  const [engines, setEngines] = useState<Engine[]>([]);
  const [providers, setProviders] = useState<Provider[]>([]);
  const [selected, setSelected] = useState('');
  const [projectId, setProjectId] = useState('');
  const [archived, setArchived] = useState(false);
  const [sessionsState, setSessionsState] = useState<LoadState>('loading');
  const [catalogState, setCatalogState] = useState<LoadState>('loading');
  const [configurationState, setConfigurationState] = useState<LoadState>('loading');
  const [projectPins, setProjectPins] = useState<ProjectPins>(noPins);
  const [pinBusy, setPinBusy] = useState('');
  const [pinsReady, setPinsReady] = useState(false);
  const pinFlight = useRef<object | null>(null);
  const sessionCache = useRef(new SessionCache());
  const sessionListFlights = useRef(new Map<string, Promise<Session[]>>());
  const loadGeneration = useRef(0);
  const selection = useRef('');
  const workspaceNow = useRef(workspace);
  useLayoutEffect(() => {
    workspaceNow.current = workspace;
  });
  useLayoutEffect(() => {
    selection.current = selected;
  });

  const workspaceActive = useObservationActive(!locked && !manager && workspace === 'agent');

  const [session, setSession] = useSessionSnapshot({
    selected,
    authenticated: true,
    principalId: auth.principal.id,
    active: workspaceActive,
    read: id => sessionCache.current.read(id),
    onError: report,
  });

  // One list request at a time per principal and archive filter, shared by the load and the poll.
  const fetchSessions = useCallback((archived: boolean, owner: string) => {
    const key = owner + ':' + archived;
    const current = sessionListFlights.current.get(key);
    if (current) return current;
    const pending = listSessions(archived).finally(() => {
      if (sessionListFlights.current.get(key) === pending) sessionListFlights.current.delete(key);
    });
    sessionListFlights.current.set(key, pending);
    return pending;
  }, []);

  const load = useCallback(async () => {
    if (workspaceNow.current === 'data') return;
    const epoch = authEpoch.current;
    const generation = ++loadGeneration.current;
    const current = () => epoch === authEpoch.current && generation === loadGeneration.current;
    setSessionsState('loading');
    setCatalogState('loading');
    setConfigurationState('loading');
    const results = await Promise.allSettled([
      loadProjectPins().then(value => {
        if (current()) {
          setProjectPins(value);
          setPinsReady(true);
        }
      }),
      gql<{projects: Project[]}>('query{projects{id name path}}')
        .then(data => {
          if (current()) {
            setProjects(data.projects);
            setCatalogState('ready');
          }
        })
        .catch(reason => {
          if (current()) setCatalogState('failed');
          throw reason;
        }),
      gql<{engines: (Engine & {label: string; installed: boolean})[]; agent_providers: Provider[]}>(
        'query{engines{id label installed auth_state auth_detail error capabilities} agent_providers{id name model models capabilities secret_configured}}',
      )
        .then(data => {
          if (current()) {
            setEngines(
              data.engines
                .filter(engine => engine.installed)
                .map(engine => ({...engine, name: engine.label, available: engine.installed})),
            );
            setProviders(data.agent_providers);
            setConfigurationState('ready');
          }
        })
        .catch(reason => {
          if (current()) setConfigurationState('failed');
          throw reason;
        }),
      fetchSessions(archived, auth.principal.id)
        .then(chats => {
          if (current()) {
            setSessions(chats);
            setSessionsState('ready');
          }
        })
        .catch(reason => {
          if (current()) setSessionsState('failed');
          throw reason;
        }),
    ]);
    if (current()) {
      const failures = results.filter(
        (result): result is PromiseRejectedResult => result.status === 'rejected',
      );
      if (failures.length) {
        report(
          new Error(
            failures
              .map(result => (result.reason instanceof Error ? result.reason.message : String(result.reason)))
              .join(' · '),
          ),
        );
      }
    }
  }, [archived, report, auth.principal.id, fetchSessions]);

  useEffect(() => onUnlocked(() => void load()), [onUnlocked, load]);
  // The first load belongs to the shell's sign-in effect; these two react to later changes only.
  useUpdateEffect(() => {
    void load();
  }, [archived]);
  useUpdateEffect(() => {
    if (workspace !== 'data') void load();
  }, [workspace]);

  const refresh = useCallback(async () => {
    const epoch = authEpoch.current;
    await load();
    if (epoch !== authEpoch.current) return;
    if (selection.current) {
      const id = selection.current;
      try {
        const value = await sessionCache.current.read(id);
        if (epoch === authEpoch.current && selection.current === id) setSession(value);
      } catch (reason) {
        if (epoch === authEpoch.current) report(reason);
      }
    }
  }, [load, report, setSession, authEpoch]);

  usePolling(() => fetchSessions(archived, auth.principal.id), {
    interval: 15000,
    backoff: false,
    immediate: false,
    enabled: workspaceActive,
    resetKey: auth.principal.id + ':' + archived,
    onResult: chats =>
      setSessions(previous => (JSON.stringify(previous) === JSON.stringify(chats) ? previous : chats)),
    onError: () => {},
  });

  const pinProject = useCallback(
    async (id: string) => {
      if (!pinsReady || pinBusy || !authNow.current) return;
      const pending = fence();
      const ticket = {};
      pinFlight.current = ticket;
      setPinBusy(id);
      try {
        const value = await changeProjectPin(id, !projectPins.projects.includes(id), projectPins.revision);
        if (pending.current()) setProjectPins(value);
      } catch (reason) {
        if (pending.current()) {
          report(reason);
          try {
            const value = await loadProjectPins();
            if (pending.current()) setProjectPins(value);
          } catch (error) {
            if (pending.current()) report(error);
          }
        }
      } finally {
        if (pinFlight.current === ticket) {
          pinFlight.current = null;
          setPinBusy('');
        }
      }
    },
    [pinsReady, pinBusy, projectPins, authNow, fence, report],
  );

  const value = useMemo<WorkspaceData>(
    () => ({
      projects,
      sessions,
      engines,
      providers,
      sessionsState,
      catalogState,
      configurationState,
      selected,
      setSelected,
      selection,
      projectId,
      setProjectId,
      session,
      setSession,
      sessionCache,
      archived,
      setArchived,
      workspaceActive,
      load,
      refresh,
      projectPins,
      pinsReady,
      pinBusy,
      pinProject,
    }),
    [
      projects,
      sessions,
      engines,
      providers,
      sessionsState,
      catalogState,
      configurationState,
      selected,
      projectId,
      session,
      setSession,
      archived,
      workspaceActive,
      load,
      refresh,
      projectPins,
      pinsReady,
      pinBusy,
      pinProject,
    ],
  );
  return <DataContext.Provider value={value}>{children}</DataContext.Provider>;
}
