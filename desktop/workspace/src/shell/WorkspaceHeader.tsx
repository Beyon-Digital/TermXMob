import {
  Boxes,
  ChevronRight,
  Clock,
  Code2,
  Command,
  ExternalLink,
  Folder,
  GitCompareArrows,
  Globe,
  LayoutDashboard,
  Lock,
  Maximize2,
  MessageSquare,
  Minimize2,
  Monitor,
  PanelBottom,
  PanelRight,
  Plus,
  Shield,
  SlidersHorizontal,
  Sun,
  TerminalSquare,
} from 'lucide-react';
import {Button} from '../components/ui/button';
import WorkspaceMenu from '../components/WorkspaceMenu';
import type {Project} from '../lib/api';
import type {Bindings} from '../lib/keybindings';
import {isDetachedWindow, nativeWorkspace} from '../lib/native';
import {formatShortcut} from '../lib/shortcut-label';
import type {Layout} from '../lib/use-layout';
import type {StudioSection, WorkspaceMode} from '../lib/workspace-mode';
import {detachedBrowser} from '../lib/window-journal';
import {useAuth} from './AuthGate';
import {managerTitle} from './manager-titles';
import type {Coordination} from './use-coordination';
import {useWorkspaceData} from './WorkspaceDataProvider';

type Props = {
  workspace: WorkspaceMode;
  studioSection: StudioSection;
  /** The open manager or settings page, `''` for none. */
  manager: string;
  /** The page shown behind settings, or the manager itself when it is not settings. */
  shellManager: string;
  managerReturnLabel: string;
  project: Project | null;
  projectWorkbenchId: string;
  primaryId: string;
  mainState: 'loading' | 'ready' | 'failed';
  layout: Layout;
  online: boolean;
  /** Where this window is, in words: the project, Studio project or Data workspace. */
  statusContext: string;
  fullscreen: boolean;
  registering: boolean;
  redocking: boolean;
  /** The sidebar is pinned open (`left`) or floats on hover. */
  left: boolean;
  sidebarHover: boolean;
  bindings: Bindings;
  coordination: Coordination;
  onToggleFullscreen: () => void;
  onNewChat: () => void;
  onNewChatWithOptions: () => void;
  onManager: (value: string) => void;
  onReturnFromManager: () => void;
  onBackToChat: () => void;
  onOpenFiles: () => void;
  onOpenSurface: (surface: 'review' | 'browser' | 'terminal' | 'computer' | 'artifacts') => void;
  onSidebarBehavior: (value: 'pinned' | 'collapsed' | 'hover') => void;
  onToggleSidePanel: () => void;
  onToggleBottomPanel: () => void;
  onOpenSavedLayouts: () => void;
  onRedock: () => void;
  onRecoverWindows: () => void;
  onDetach: () => void;
  onOpenPalette: () => void;
};

/** The title bar: where you are, the way back, Subagents, full screen and the Workspace actions menu. */
export default function WorkspaceHeader(props: Props) {
  const {
    workspace,
    studioSection,
    manager,
    shellManager,
    managerReturnLabel,
    project,
    projectWorkbenchId,
    primaryId,
    mainState,
    layout,
    online,
    statusContext,
    fullscreen,
    registering,
    redocking,
    left,
    sidebarHover,
    bindings,
    coordination,
    onToggleFullscreen,
    onNewChat,
    onNewChatWithOptions,
    onManager,
    onReturnFromManager,
    onBackToChat,
    onOpenFiles,
    onOpenSurface,
    onSidebarBehavior,
    onToggleSidePanel,
    onToggleBottomPanel,
    onOpenSavedLayouts,
    onRedock,
    onRecoverWindows,
    onDetach,
    onOpenPalette,
  } = props;
  const {beginLock} = useAuth();
  const {session, selected} = useWorkspaceData();
  return (
    <header className="workspace-header">
      <div className="breadcrumb">
        <span>
          {shellManager
            ? 'TermX'
            : workspace === 'data'
              ? 'Data'
              : workspace === 'studio'
                ? 'Studio'
                : selected === primaryId
                  ? 'Main agent'
                  : project?.name || 'Chat'}
        </span>
        <ChevronRight size={12} />
        <strong>
          {shellManager
            ? managerTitle[shellManager] || 'Control plane'
            : workspace === 'data'
              ? 'Database & BI'
              : workspace === 'studio'
                ? {
                    create: 'Create',
                    library: 'Library',
                    assets: 'Saved assets',
                    settings: 'Studio Settings',
                    music: 'Music',
                  }[studioSection]
                : session?.title ||
                  (!selected && projectWorkbenchId
                    ? project
                      ? 'Files'
                      : 'Project unavailable'
                    : !selected && project
                      ? 'Files'
                      : mainState === 'loading'
                        ? 'Opening main agent…'
                        : 'Main agent')}
        </strong>
      </div>
      {workspace === 'agent' && !online && (
        <span className="workspace-connection-alert" role="status">
          Offline
        </span>
      )}
      <span className="flex-spacer" />
      {shellManager ? (
        <Button
          className="control-return"
          size="sm"
          variant="ghost"
          aria-label={managerReturnLabel}
          onClick={onReturnFromManager}
        >
          <>
            <MessageSquare size={14} aria-hidden="true" />
            <span className="workspace-return-label">{managerReturnLabel}</span>
          </>
        </Button>
      ) : workspace === 'agent' && layout !== 'chat' ? (
        <Button
          variant="ghost"
          size="sm"
          className="workspace-chat-return"
          aria-label="Back to chat"
          onClick={onBackToChat}
        >
          <MessageSquare size={14} aria-hidden="true" />
          <span className="workspace-return-label">Back to chat</span>
        </Button>
      ) : null}
      {workspace === 'agent' && !manager && (
        <>
          <Button
            ref={coordination.toggleRef}
            className="workspace-subagents-toggle"
            variant="ghost"
            size="sm"
            aria-label="Subagents"
            aria-describedby="workspace-subagents-summary"
            title="Subagents"
            aria-controls="workspace-subagents"
            aria-expanded={coordination.open}
            onClick={() => coordination.show(!coordination.open)}
          >
            <PanelRight size={16} />
            <span className="workspace-subagents-label">Subagents</span>
            {(coordination.summary.active > 0 || coordination.summary.attention > 0) && (
              <span className="workspace-subagents-count" aria-hidden="true">
                {coordination.summary.active || coordination.summary.attention}
              </span>
            )}
          </Button>
          <span id="workspace-subagents-summary" className="visually-hidden">
            {coordination.summary.active} active, {coordination.summary.attention} need attention,{' '}
            {coordination.summary.done} done
          </span>
        </>
      )}
      <div className="workspace-view-actions" role="group" aria-label="Window view">
        <Button
          variant="ghost"
          size="icon"
          aria-label={fullscreen ? 'Exit full screen' : 'Enter full screen'}
          title="Toggle window full screen"
          onClick={onToggleFullscreen}
        >
          {fullscreen ? <Minimize2 size={15} /> : <Maximize2 size={15} />}
        </Button>
      </div>
      <WorkspaceMenu label="Workspace actions">
        <div className="workspace-menu-context" role="status">
          {online ? 'Connected' : 'Offline'}
          {statusContext ? ' · ' + statusContext : ''}
        </div>
        {workspace === 'agent' && (
          <>
            <span className="workspace-menu-label">Chat</span>
            <button data-close-menu disabled={registering} onClick={onNewChat}>
              <Plus size={16} />
              New chat
            </button>
            <button data-close-menu onClick={onNewChatWithOptions}>
              <SlidersHorizontal size={16} />
              New chat with options
            </button>
            <button
              data-close-menu
              aria-pressed={coordination.open}
              onClick={() => coordination.show(!coordination.open)}
            >
              <PanelRight size={16} />
              Subagents
            </button>
            <hr />
            <span className="workspace-menu-label">Work</span>
            <button data-close-menu onClick={() => onManager('projects')}>
              <Folder size={16} />
              Projects
            </button>
            <button data-close-menu onClick={() => onManager('overview')}>
              <LayoutDashboard size={16} />
              Overview
            </button>
            <button data-close-menu onClick={() => onManager('tasks')}>
              <Clock size={16} />
              Activity
            </button>
            <hr />
            <span className="workspace-menu-label">Open view</span>
            <button data-close-menu onClick={onOpenFiles}>
              <Code2 size={14} />
              Files & code
            </button>
            {(['browser', 'terminal', 'computer', 'artifacts', 'review'] as const).map(view => (
              <button key={view} data-close-menu onClick={() => onOpenSurface(view)}>
                {
                  {
                    browser: <Globe size={14} />,
                    terminal: <TerminalSquare size={14} />,
                    computer: <Monitor size={14} />,
                    artifacts: <Boxes size={14} />,
                    review: <GitCompareArrows size={14} />,
                  }[view]
                }
                {
                  {
                    browser: 'Browser',
                    terminal: 'Terminal',
                    computer: 'Computer',
                    artifacts: 'Artifacts',
                    review: 'Review changes',
                  }[view]
                }
              </button>
            ))}
            <hr />
            <span className="workspace-menu-label">Layout</span>
            <span className="workspace-menu-label" id="sidebar-behavior-label">
              Sidebar
            </span>
            <div role="group" aria-labelledby="sidebar-behavior-label" className="workspace-menu-choices">
              {(['pinned', 'collapsed', 'hover'] as const).map(mode => (
                <button
                  key={mode}
                  data-close-menu
                  aria-pressed={(sidebarHover ? 'hover' : left ? 'pinned' : 'collapsed') === mode}
                  onClick={() => onSidebarBehavior(mode)}
                >
                  {{pinned: 'Pinned', collapsed: 'Collapsed', hover: 'Float on hover'}[mode]}
                </button>
              ))}
            </div>
            <button data-close-menu onClick={onToggleSidePanel}>
              <PanelRight size={14} />
              Toggle side panel<kbd>{formatShortcut(bindings.right)}</kbd>
            </button>
            <button data-close-menu onClick={onToggleBottomPanel}>
              <PanelBottom size={14} />
              Toggle bottom panel<kbd>{formatShortcut(bindings.bottom)}</kbd>
            </button>
            <button data-close-menu onClick={onOpenSavedLayouts}>
              <LayoutDashboard size={14} />
              Saved layouts
            </button>
          </>
        )}
        <hr />
        {workspace === 'agent' && (isDetachedWindow() || (!nativeWorkspace() && detachedBrowser)) && (
          <button data-close-menu disabled={!selected || redocking} onClick={onRedock}>
            Return to main workspace
          </button>
        )}
        {!nativeWorkspace() && (
          <button data-close-menu onClick={onRecoverWindows}>
            <Clock size={14} />
            Recover unsaved windows
          </button>
        )}
        {workspace === 'agent' && (
          <button data-close-menu onClick={onDetach}>
            <ExternalLink size={14} />
            Detach workspace
          </button>
        )}
        <hr />
        <button data-close-menu onClick={onOpenPalette}>
          <Command size={16} />
          Commands
        </button>
        <button data-close-menu onClick={() => onManager('safety')}>
          <Shield size={14} />
          Action review
        </button>
        <button data-close-menu onClick={() => onManager('appearance')}>
          <Sun size={14} />
          Appearance
        </button>
        <button data-close-menu onClick={() => void beginLock()}>
          <Lock size={14} />
          Lock workspace
        </button>
      </WorkspaceMenu>
    </header>
  );
}
