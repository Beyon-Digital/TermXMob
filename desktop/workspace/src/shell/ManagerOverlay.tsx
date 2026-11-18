import {Button} from '../components/ui/button';
import ControlPlane, {type ControlPage} from '../features/ControlPlane';
import {lazy} from 'react';
import type {Layout} from '../lib/use-layout';
import type {Project} from '../lib/api';
import {useAuth} from './AuthGate';
import type {ManagerScope} from './SettingsHost';
import {useWorkspaceData} from './WorkspaceDataProvider';

const Runners = lazy(() => import('../features/Runners'));
const Managers = lazy(() => import('../features/Managers'));

type Props = {
  /** The open manager page. Settings categories and Workflows are not shown here. */
  manager: string;
  /** Overview, Projects and Activity are control-plane pages; the rest are manager pages with a Back button. */
  controlPage: boolean;
  scope: ManagerScope;
  settingsProjectId: string;
  project: Project | null;
  projectWorkbenchId: string;
  onPage: (page: string) => void;
  onManage: (section: string) => void;
  onNew: (projectId?: string) => void;
  onAddProject: () => void;
  onOpen: (sessionId: string, view: Layout) => void;
  onProject: (project: Project) => void;
  onSection: (section: string) => void;
  onBack: () => void;
};

/** Control-plane pages (Overview, Projects, Activity) and the manager pages that cover the workspace. */
export default function ManagerOverlay(props: Props) {
  const {manager, controlPage, scope, settingsProjectId, project, projectWorkbenchId} = props;
  const {onPage, onManage, onNew, onAddProject, onOpen, onProject, onSection, onBack} = props;
  const {auth, locked} = useAuth();
  const {projects, engines, providers, catalogState, configurationState, selected, load} = useWorkspaceData();
  return (
    <div className={'manager-overlay' + (controlPage ? ' control-overlay' : '')}>
      {controlPage ? (
        <ControlPlane
          key={auth.principal.id}
          page={manager as ControlPage}
          ownerId={auth.principal.id}
          enabled={!locked}
          projects={projects}
          engines={engines}
          providers={providers}
          catalogState={catalogState}
          configurationState={configurationState}
          currentProjectId={project?.id || projectWorkbenchId}
          onPage={onPage}
          onManage={onManage}
          onRefresh={() => void load()}
          onNew={onNew}
          onAddProject={onAddProject}
          onOpen={onOpen}
          onProject={onProject}
        />
      ) : manager === 'runners' ? (
        <Runners
          ownerId={auth.principal.id}
          projectId={scope === 'global' ? null : settingsProjectId || project?.id || null}
        />
      ) : (
        <Managers
          section={manager}
          projectId={scope === 'global' ? null : settingsProjectId || project?.id || null}
          sessionId={scope === 'global' ? null : selected || null}
          onSection={onSection}
          onCapabilitiesChanged={() => void load()}
        />
      )}
      {!controlPage && (
        <Button className="manager-back" size="sm" variant="outline" onClick={onBack}>
          Back
        </Button>
      )}
    </div>
  );
}
