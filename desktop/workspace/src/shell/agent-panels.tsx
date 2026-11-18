import {lazy, type ReactNode} from 'react';
import {Button} from '../components/ui/button';
import OrchestratorStatus from '../features/OrchestratorStatus';
import type {Project} from '../lib/api';
import {contextItem, type IncomingContext} from '../lib/context';
import type {DockPanel} from '../lib/docking';
import type {useSessionChanges} from '../lib/session-changes';
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

/** What the Agent panels need from the shell, whichever layout engine shows them. */
export type AgentPanelProps = {
  project: Project | null;
  /** The bottom dock (old layout) or the bottom panel (new layout) is showing; the editor sizes its terminal drawer by it. */
  bottom: boolean;
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

/**
 * The seven Agent panels (Chat, Editor, Changes, Terminal, Browser, Computer, Artifacts) as elements. The old dock and the
 * tab-group layout both place these same components; only where they sit differs.
 *
 * `visible` is whether the panel is on screen now. Panels stay mounted while hidden, so a terminal or an editor keeps its
 * state; each one uses `visible` to pause what it does not need while hidden.
 */
export function useAgentPanels(props: AgentPanelProps) {
  const {project, bottom, fileRequest, changesSource, taskId, incoming, onIncoming, mainState, primaryId} = props;
  const {onPrimary, coordination, onAddProject, onOpenConversation, onOpenMainAgent, onManager} = props;
  const {onOpenSurface, onOpenFile, onRevealConversation, onShowChat} = props;
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

  return function panel(name: DockPanel, panelVisible: boolean): ReactNode {
    const visible = workspaceActive && panelVisible;
    switch (name) {
      case 'editor':
        return (
          <Workbench
            ownerId={auth.principal.id}
            project={project}
            onAddProject={onAddProject}
            session={session}
            onTargetChange={refresh}
            fileRequest={fileRequest}
            reviewMode={false}
            visible={visible}
            bottom={bottom}
            onError={report}
          />
        );
      case 'conversation':
        return (
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
              enabled={visible || (workspaceActive && coordination.open)}
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
              visible={visible}
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
        );
      case 'review':
        return (
          <ChangeReview
            ownerId={auth.principal.id}
            project={project}
            session={session}
            visible={visible}
            source={changesSource}
            onError={report}
            onOpenFile={onOpenFile}
          />
        );
      case 'terminal':
        return (
          <TerminalPanel
            ownerId={auth.principal.id}
            project={project}
            workspaceSession={session?.id}
            executionRoot={session?.cwd || project?.path}
            onError={report}
          />
        );
      case 'browser':
        return (
          <BrowserSurface
            visible={visible}
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
        );
      case 'computer':
        return <ComputerSurface visible={visible} onError={report} onSection={onManager} />;
      case 'artifacts':
        return (
          <Artifacts
            key={auth.principal.id}
            ownerId={auth.principal.id}
            projectId={project?.id || null}
            visible={visible}
            onContext={(text, attachments) => {
              onIncoming({id: crypto.randomUUID(), sessionId: selected, text, attachments});
              onShowChat();
            }}
          />
        );
    }
  };
}
