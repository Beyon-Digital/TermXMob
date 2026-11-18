import {lazy} from 'react';
import type {Project, Session} from '../lib/api';
import type {WorkspaceMode} from '../lib/workspace-mode';
import {useAuth} from './AuthGate';
import {useWorkspaceData} from './WorkspaceDataProvider';

const Workflows = lazy(() => import('../features/Workflows'));

type Props = {
  workspace: WorkspaceMode;
  manager: string;
  project: Project | null;
  projectWorkbenchId: string;
  /** A run to open, set when something outside links to one. */
  runId: string | null;
  onOpenSession: (id: string) => void;
  onNavigate: (page: string) => void;
};

/** The conversation a workflow can attach to: the selected one, if it is a plain agent session on this host. */
export function workflowSessionId(session: Session | null, selected: string): string | null {
  return session?.id === selected && session.mode === 'agent' && !session.runner_id && session.workflow !== 'browser'
    ? session.id
    : null;
}

/** The Workflows page. It stays mounted once opened, hidden and inert when another page is showing. */
export default function WorkflowsHost(props: Props) {
  const {workspace, manager, project, projectWorkbenchId, runId, onOpenSession, onNavigate} = props;
  const {auth, locked} = useAuth();
  const {projects, session, selected} = useWorkspaceData();
  return (
    <div
      className="workspace-workflows-host manager-overlay control-overlay"
      hidden={workspace !== 'agent' || manager !== 'workflows'}
      inert={workspace !== 'agent' || manager !== 'workflows' || locked}
    >
      <Workflows
        key={auth.principal.id}
        ownerId={auth.principal.id}
        projectId={project?.id || projectWorkbenchId || null}
        projects={projects}
        sessionId={workflowSessionId(session, selected)}
        runId={runId}
        visible={workspace === 'agent' && manager === 'workflows'}
        locked={locked}
        onOpenSession={onOpenSession}
        onNavigate={onNavigate}
      />
    </div>
  );
}
