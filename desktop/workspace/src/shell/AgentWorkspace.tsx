import {lazy} from 'react';
import {Button} from '../components/ui/button';
import DockWorkspace from '../components/DockWorkspace';
import OrchestratorStatus from '../features/OrchestratorStatus';
import type {Project} from '../lib/api';
import {contextItem, type IncomingContext} from '../lib/context';
import type {DockGeometry, DockMap, DockPanel} from '../lib/docking';
import type {useSessionChanges} from '../lib/session-changes';
import type {WorkspaceMode} from '../lib/workspace-mode';
import {useAuth} from './AuthGate';
import type {Coordination} from './use-coordination';
import {useWorkspaceData} from './WorkspaceDataProvider';

const Chat = lazy(() => import('../features/Chat'));
const Workbench = lazy(() => import('../features/Workbench'));
const ChangeReview = lazy(() => import('../features/ChangeReview'));
const TerminalPanel = lazy(() => import('../features/TerminalPanel'));
const BrowserSurface = lazy(() => import('../features/BrowserSurface'));
const ComputerSurface = lazy(() => import('../features/ComputerSurface'));
const Artifacts = lazy(() => import('../features/Media'));

export type FileRequest = {id: number; sessionId: string; root: string; path: string; line?: number};

type Props = {
  workspace: WorkspaceMode;
  manager: string;
  project: Project | null;
  /** Panels that were ever shown stay mounted, so a terminal or an editor keeps its state when hidden. */
  visited: Set<string>;
  visiblePanels: DockPanel[];
  onVisiblePanels: (panels: DockPanel[]) => void;
  dock: DockMap;
  onDockChange: (map: DockMap) => void;
  right: boolean;
  bottom: boolean;
  rightWidth: number;
  onRightWidth: (width: number) => void;
  dockGeometry: DockGeometry;
  onGeometry: (geometry: DockGeometry) => void;
  onResizeStart: () => void;
  panelActivation: {panel: DockPanel; nonce: number} | undefined;
  onFullscreen: () => void;
  onDetach: (panel: DockPanel) => void;
  fileRequest: FileRequest | null;
  changesSource: ReturnType<typeof useSessionChanges>;
  /** The running browser task, if any, for the Browser surface to follow. */
  taskId: string | null;
  incoming: IncomingContext | null;
  onIncoming: (value: IncomingContext) => void;
  mainState: 'loading' | 'ready' | 'failed';
  primaryId: string;
  onPrimary: (id: string) => void;
  coordination: Coordination;
  onAddProject: () => void;
  onOpenConversation: (id: string) => void;
  onOpenMainAgent: () => void;
  onManager: (page: string) => void;
  onOpenSurface: (surface: 'review' | 'browser' | 'terminal' | 'editor' | 'computer' | 'artifacts') => void;
  onOpenFile: (path: string, line?: number) => void;
  /** Show the conversation panel (opening the side panel) so attached context can be seen. */
  onRevealConversation: () => void;
  onShowChat: () => void;
  onError: (reason: unknown) => void;
};

/** The Agent workspace: the dock of panels (Chat, Files, Changes, Terminal, Browser, Computer, Artifacts). */
export default function AgentWorkspace(props: Props) {
  const {workspace, manager, project, visited, visiblePanels, onVisiblePanels, dock, onDockChange} = props;
  const {right, bottom, rightWidth, onRightWidth, dockGeometry, onGeometry, onResizeStart} = props;
  const {panelActivation, onFullscreen, onDetach, fileRequest, changesSource, taskId, incoming} = props;
  const {onIncoming, mainState, primaryId, onPrimary, coordination, onAddProject} = props;
  const {onOpenConversation, onOpenMainAgent, onManager, onOpenSurface, onOpenFile} = props;
  const {onRevealConversation, onShowChat} = props;
  const {auth, authEpoch, locked} = useAuth();
  const {
    projects,
    engines,
    providers,
    selected,
    selection,
    session,
    setSession,
    sessionCache,
    workspaceActive,
    refresh,
  } = useWorkspaceData();
  const report = props.onError;
  return (
    <div
      id="agent-workspace"
      className="workspace-dock-host"
      role="region"
      aria-label="Agent workspace"
      hidden={workspace !== 'agent'}
      inert={!!manager || workspace !== 'agent'}
      aria-hidden={manager || workspace !== 'agent' ? true : undefined}
    >
      <DockWorkspace
        active={workspace === 'agent' && !manager && !locked}
        activation={panelActivation}
        onFullscreen={onFullscreen}
        onVisiblePanels={onVisiblePanels}
        map={dock}
        onChange={onDockChange}
        rightVisible={right}
        bottomVisible={bottom}
        rightWidth={rightWidth}
        onRightWidth={onRightWidth}
        geometry={dockGeometry}
        onGeometry={onGeometry}
        onResizeStart={onResizeStart}
        onDetach={onDetach}
        children={{
          editor: visited.has('editor') ? (
            <Workbench
              ownerId={auth.principal.id}
              project={project}
              onAddProject={onAddProject}
              session={session}
              onTargetChange={refresh}
              fileRequest={fileRequest}
              reviewMode={false}
              visible={workspaceActive && visiblePanels.includes('editor')}
              bottom={bottom}
              onError={report}
            />
          ) : null,
          conversation: (
            <div className="conversation-home">
              <OrchestratorStatus
                presentation="sidebar"
                sidebarTarget={coordination.target}
                selectedTasks={coordination.tasks}
                sidebarVisible={coordination.visible}
                onInspectTask={coordination.inspect}
                onSummary={coordination.receiveSummary}
                quietWhenIdle
                ownerId={auth.principal.id}
                selected={selected}
                enabled={workspaceActive && (visiblePanels.includes('conversation') || coordination.open)}
                onPrimary={onPrimary}
                onOpen={onOpenConversation}
              />
              {mainState === 'failed' && !selected && (
                <div className="main-agent-error" role="alert">
                  <p>Your main agent could not be opened. Your existing chats are still available.</p>
                  <Button variant="outline" onClick={onOpenMainAgent}>
                    Retry main agent
                  </Button>
                </div>
              )}
              <Chat
                coordinating={selected === primaryId}
                onConfigureAgent={() => onManager('models')}
                onOpenVoiceSettings={() => onManager('voice')}
                visible={workspaceActive && visiblePanels.includes('conversation')}
                ownerId={auth.principal.id}
                session={session}
                engines={engines}
                providers={providers}
                projects={projects}
                onHistory={() => {
                  const epoch = authEpoch.current,
                    id = selected;
                  if (id)
                    sessionCache.current
                      .history(id)
                      .then(value => {
                        if (epoch === authEpoch.current && selection.current === id) setSession(value);
                      })
                      .catch(reason => {
                        if (epoch === authEpoch.current) report(reason);
                      });
                }}
                onSelect={onOpenConversation}
                onRefresh={refresh}
                onError={report}
                incoming={incoming}
                onOpenSurface={onOpenSurface}
                onOpenFile={onOpenFile}
                changes={
                  changesSource.data
                    ? {
                        files: changesSource.data.files.length,
                        ...(changesSource.data.counts_complete
                          ? {
                              additions: changesSource.data.additions,
                              deletions: changesSource.data.deletions,
                            }
                          : {}),
                        loading: changesSource.loading,
                      }
                    : undefined
                }
              />
            </div>
          ),
          review: visited.has('review') ? (
            <ChangeReview
              ownerId={auth.principal.id}
              project={project}
              session={session}
              visible={workspaceActive && visiblePanels.includes('review')}
              source={changesSource}
              onError={report}
              onOpenFile={onOpenFile}
            />
          ) : null,
          terminal: visited.has('terminal') ? (
            <TerminalPanel
              ownerId={auth.principal.id}
              project={project}
              workspaceSession={session?.id}
              executionRoot={session?.cwd || project?.path}
              onError={report}
            />
          ) : null,
          browser: visited.has('browser') ? (
            <BrowserSurface
              visible={workspaceActive && visiblePanels.includes('browser')}
              locked={locked}
              projectId={project?.id}
              taskId={taskId}
              onContext={context => {
                const item = contextItem(context);
                if (!selected || !item) {
                  report(new Error('Select a conversation before attaching browser context.'));
                  return;
                }
                onIncoming({
                  id: crypto.randomUUID(),
                  sessionId: selected,
                  text: '',
                  attachments: [],
                  context: [item],
                });
                onRevealConversation();
              }}
            />
          ) : null,
          computer: visited.has('computer') ? (
            <ComputerSurface
              visible={workspaceActive && visiblePanels.includes('computer')}
              onError={report}
              onSection={onManager}
            />
          ) : null,
          artifacts: visited.has('artifacts') ? (
            <Artifacts
              key={auth.principal.id}
              ownerId={auth.principal.id}
              projectId={project?.id || null}
              visible={workspaceActive && visiblePanels.includes('artifacts')}
              onContext={(text, attachments) => {
                onIncoming({id: crypto.randomUUID(), sessionId: selected, text, attachments});
                onShowChat();
              }}
            />
          ) : null,
        }}
      />
    </div>
  );
}
