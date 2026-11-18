import {lazy} from 'react';
import type {WorkspaceMode} from '../lib/workspace-mode';
import {useAuth} from './AuthGate';

const DataStudio = lazy(() => import('../features/data/DataStudio'));

type Props = {
  workspace: WorkspaceMode;
  manager: string;
  /** Data has been opened at least once, so it stays mounted from now on. */
  visited: boolean;
  visible: boolean;
  /** Where Data draws its navigation: the shell sidebar. */
  navigationHost: HTMLElement | null;
  /** A navigation inside Data was chosen; on a narrow window the sidebar should close. */
  onNavigate: () => void;
};

/** The Data workspace. It polls its own jobs, so it stays mounted once opened. */
export default function DataHost({workspace, manager, visited, visible, navigationHost, onNavigate}: Props) {
  const {auth, locked} = useAuth();
  return (
    <div
      id="data-workspace"
      className="workspace-data-host"
      role="region"
      aria-label="Data workspace"
      hidden={workspace !== 'data'}
      inert={workspace !== 'data' || !!manager || locked}
      aria-hidden={workspace !== 'data' || !!manager || locked ? true : undefined}
    >
      {visited && (
        <DataStudio
          key={auth.principal.id}
          ownerId={auth.principal.id}
          visible={visible}
          observing={visible}
          locked={locked}
          navigationHost={navigationHost}
          onNavigate={onNavigate}
        />
      )}
    </div>
  );
}
