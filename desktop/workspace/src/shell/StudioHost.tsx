import {lazy} from 'react';
import type {StudioProjectActivity} from '../lib/studio-draft-state';
import type {StudioSection, WorkspaceMode} from '../lib/workspace-mode';
import {useAuth} from './AuthGate';
import {useWorkspaceData} from './WorkspaceDataProvider';

const MediaWorkspace = lazy(() => import('../features/MediaWorkspace'));

type Props = {
  workspace: WorkspaceMode;
  manager: string;
  /** Studio has been opened at least once, so it stays mounted from now on. */
  visited: boolean;
  section: StudioSection;
  onSectionChange: (section: StudioSection) => void;
  /** The Studio project (an Agent project or a Studio collection), `''` for none. */
  projectId: string;
  project: {name: string} | null;
  readOnly: boolean;
  onProjectActivity: (activity: StudioProjectActivity) => void;
  visible: boolean;
  observing: boolean;
  onConfigureProvider: () => void;
  onContext: (text: string, attachments: unknown[]) => void;
};

/** The Studio workspace. It stays mounted once opened so its drafts and jobs survive switching workspaces. */
export default function StudioHost(props: Props) {
  const {workspace, manager, visited, section, onSectionChange, projectId, project, readOnly} = props;
  const {onProjectActivity, visible, observing, onConfigureProvider, onContext} = props;
  const {auth, locked} = useAuth();
  const {load} = useWorkspaceData();
  return (
    <div
      id="studio-workspace"
      className="workspace-studio-host"
      role="region"
      aria-label="Studio workspace"
      hidden={workspace !== 'studio'}
      inert={workspace !== 'studio' || !!manager}
      aria-hidden={workspace !== 'studio' || !!manager ? true : undefined}
    >
      {visited && (
        <MediaWorkspace
          section={section}
          onSectionChange={onSectionChange}
          presentation="standalone"
          key={auth.principal.id}
          ownerId={auth.principal.id}
          projectId={projectId || null}
          projectName={project?.name}
          readOnly={readOnly}
          onProjectActivity={onProjectActivity}
          visible={visible}
          observing={observing}
          locked={locked}
          onConfigureProvider={onConfigureProvider}
          onProvidersChanged={() => void load()}
          onContext={onContext}
        />
      )}
    </div>
  );
}
