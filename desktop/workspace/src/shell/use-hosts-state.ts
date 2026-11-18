import {useState} from 'react';
import {useStudioProjectGuard} from '../lib/studio-draft-state';
import {useStudioProjects} from '../lib/studio-projects';
import {useObservationActive} from '../lib/use-observation-active';
import {readStudioSection, readWorkspaceMode, type StudioSection, type WorkspaceMode} from '../lib/workspace-mode';
import {useAuth} from './AuthGate';
import {workspaceRoute} from './routes';
import {useWorkspaceData} from './WorkspaceDataProvider';

type Options = {workspace: WorkspaceMode; manager: string};

/**
 * What the workspace hosts remember between visits: Studio and Data stay mounted once opened (their drafts and jobs
 * survive switching), the Workflows page too, and Studio keeps its project and section. Plain state plus what is derived
 * from it; the moves between hosts are in `use-workspace-navigation.ts`.
 */
export function useHostsState({workspace, manager}: Options) {
  const {auth, locked} = useAuth();
  const {projects} = useWorkspaceData();
  const [studioVisited, setStudioVisited] = useState(() => readWorkspaceMode() === 'studio');
  const [dataVisited, setDataVisited] = useState(() => readWorkspaceMode() === 'data');
  const [studioProjectId, setStudioProjectId] = useState('');
  const [studioSection, setStudioSection] = useState<StudioSection>(() => readStudioSection());
  const [studioSidebarOpen, setStudioSidebarOpen] = useState(() => innerWidth > 850);
  const [dataSidebarOpen, setDataSidebarOpen] = useState(() => innerWidth > 850);
  const [dataSidebarHost, setDataSidebarHost] = useState<HTMLElement | null>(null);
  const [controlSidebarOpen, setControlSidebarOpen] = useState(false);
  const [workflowsVisited, setWorkflowsVisited] = useState(() => workspaceRoute().manager === 'workflows');
  const [workflowRunId, setWorkflowRunId] = useState<string | null>(null);
  const [settingsProjectId, setSettingsProjectId] = useState('');
  const [managerScope, setManagerScope] = useState<'global' | 'workspace'>('global');

  const studioVisible = !locked && !manager && workspace === 'studio';
  const dataVisible = !locked && !manager && workspace === 'data';
  // Data polls its own jobs; window focus must not tear down forms or SQL drafts.
  const studioActive = useObservationActive(studioVisible);
  const studioProjects = useStudioProjects({ownerId: auth?.principal.id, enabled: studioVisible});
  const studioProjectGuard = useStudioProjectGuard(auth?.principal.id, studioProjectId || null, locked);
  const studioProject =
    studioProjects.projects.find(row => row.id === studioProjectId) ||
    projects.find(row => row.id === studioProjectId) ||
    null;
  const studioProjectReadOnly = !!studioProject && 'archived' in studioProject && studioProject.archived;

  return {
    studioVisited,
    setStudioVisited,
    dataVisited,
    setDataVisited,
    studioProjectId,
    setStudioProjectId,
    studioSection,
    setStudioSection,
    studioSidebarOpen,
    setStudioSidebarOpen,
    dataSidebarOpen,
    setDataSidebarOpen,
    dataSidebarHost,
    setDataSidebarHost,
    controlSidebarOpen,
    setControlSidebarOpen,
    workflowsVisited,
    setWorkflowsVisited,
    workflowRunId,
    setWorkflowRunId,
    settingsProjectId,
    setSettingsProjectId,
    managerScope,
    setManagerScope,
    studioVisible,
    dataVisible,
    studioActive,
    studioProjects,
    studioProjectGuard,
    studioProject,
    studioProjectReadOnly,
  };
}

export type HostsState = ReturnType<typeof useHostsState>;
