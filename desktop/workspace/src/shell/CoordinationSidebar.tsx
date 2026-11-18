import {X} from 'lucide-react';
import {lazy, Suspense} from 'react';
import {Button} from '../components/ui/button';
import {useAuth} from './AuthGate';
import type {Coordination} from './use-coordination';
import {useWorkspaceData} from './WorkspaceDataProvider';

const SidebarTaskSupervisor = lazy(() => import('../features/TaskSupervisor'));

/** The Subagents panel beside the conversation. The orchestrator renders its content into `coordination.target`. */
export function CoordinationSidebar({coordination}: {coordination: Coordination}) {
  const {visible, retained} = coordination;
  return (
    <aside
      id="workspace-subagents"
      ref={coordination.asideRef}
      className="workspace-coordination-sidebar"
      aria-label="Subagents"
      data-state={visible ? 'open' : 'closed'}
      hidden={!visible && !retained}
      aria-hidden={!visible || undefined}
      inert={!visible}
      onKeyDown={event => {
        if (event.key === 'Escape' && !event.defaultPrevented) {
          event.preventDefault();
          coordination.show(false);
        }
      }}
    >
      <header className="workspace-coordination-header">
        <strong>Subagents</strong>
        <Button variant="ghost" size="icon" aria-label="Close subagents" onClick={() => coordination.show(false)}>
          <X size={16} />
        </Button>
      </header>
      <div className="workspace-coordination-content" ref={coordination.setTarget} />
    </aside>
  );
}

type SupervisorProps = {
  coordination: Coordination;
  /** The task supervisor is only interactive while the Agent workspace is showing. */
  active: boolean;
  onError: (reason: unknown) => void;
};

/** The dialog for a subagent's task, shown only for the conversation and principal it was opened from. */
export function InspectedTaskSupervisor({coordination, active, onError}: SupervisorProps) {
  const {auth} = useAuth();
  const {selected} = useWorkspaceData();
  const task = coordination.inspected;
  if (!task || task.owner !== auth.principal.id || task.sessionId !== selected) return null;
  return (
    <Suspense fallback={null}>
      <SidebarTaskSupervisor
        key={auth.principal.id + ':' + task.id}
        taskId={task.id}
        visible={active}
        onClose={coordination.stopInspecting}
        onError={onError}
      />
    </Suspense>
  );
}
