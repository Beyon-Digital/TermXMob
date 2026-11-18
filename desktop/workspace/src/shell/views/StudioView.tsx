import StudioNavigation from '../../components/StudioNavigation';
import {useAuth} from '../AuthGate';
import type {HostsState} from '../use-hosts-state';
import type {WorkspaceNavigation} from '../use-workspace-navigation';
import {useWorkspaceData} from '../WorkspaceDataProvider';
import './views.css';

type Props = {hosts: HostsState; nav: WorkspaceNavigation};

/** Studio: the same navigation the old side bar showed (sections, project, saved collections). Choosing a section opens the Studio tab. */
export default function StudioView({hosts, nav}: Props) {
  const {auth} = useAuth();
  const {projects, catalogState} = useWorkspaceData();
  return (
    <div className="studio-sidebar-context">
      <StudioNavigation
        key={auth.principal.id}
        section={hosts.studioSection}
        onSection={nav.openStudioSection}
        projects={projects}
        projectId={hosts.studioProjectId || null}
        onProject={value => {
          void nav.selectStudioProject(value);
        }}
        managedProjects={hosts.studioProjects}
        projectChangeBlocked={hosts.studioProjectGuard.activity.busy ? hosts.studioProjectGuard.activity.reason : undefined}
        onBeforeArchive={row => (row.id === hosts.studioProjectId ? nav.selectStudioProject('') : Promise.resolve(true))}
        loading={catalogState === 'loading'}
      />
    </div>
  );
}
