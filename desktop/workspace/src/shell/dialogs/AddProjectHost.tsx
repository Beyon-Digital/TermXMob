import AddProjectDialog from '../../components/AddProjectDialog';
import type {Project} from '../../lib/api';
import {useAuth} from '../AuthGate';
import {useWorkspaceData} from '../WorkspaceDataProvider';

type Props = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** The shell's navigation counter, read before and after the reload so a registration never overrides a newer move. */
  navigationIntent: () => number;
  /** Open the newly registered project. Called only if nothing else navigated and the workspace is unlocked. */
  onProjectAdded: (project: Project) => void;
};

/** Add a project folder, then reload the project list and open it as a file workbench. */
export default function AddProjectHost({open, onOpenChange, navigationIntent, onProjectAdded}: Props) {
  const {auth, locked, lockedNow} = useAuth();
  const {projects, load} = useWorkspaceData();
  return (
    <AddProjectDialog
      ownerId={auth.principal.id}
      enabled={!locked}
      projects={projects}
      open={open}
      onOpenChange={onOpenChange}
      onRegistered={async (project, isCurrent) => {
        const intent = navigationIntent();
        await load();
        if (isCurrent() && intent === navigationIntent() && !lockedNow.current) onProjectAdded(project);
      }}
    />
  );
}
