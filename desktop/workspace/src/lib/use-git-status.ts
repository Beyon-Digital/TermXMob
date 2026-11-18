import {useState} from 'react';
import {gql, type Project} from './api';
import {usePolling} from './use-polling';

export type GitFile = {path: string; staged: boolean; index: string; worktree: string};
export type GitStatus = {repo: boolean; branch?: string; files: GitFile[]};

const QUERY = 'query($id:String!){git_status(project_id:$id){repo branch files{path staged index worktree}}}';

/**
 * The branch and changed files of a project's checkout, for the title bar, the status bar and the Source control list.
 * It polls the same `git_status` query the editor uses, and stops while `enabled` is false or the tab is hidden. A failed
 * read keeps the last known value: the chrome shows nothing rather than an error for a missing repository.
 */
export function useGitStatus(project: Project | null, enabled: boolean): GitStatus | null {
  const [status, setStatus] = useState<{project: string; value: GitStatus} | null>(null);
  usePolling(
    async () => (await gql<{git_status: GitStatus}>(QUERY, {id: project!.id})).git_status,
    {
      interval: 15000,
      maxInterval: 60000,
      enabled: enabled && !!project,
      resetKey: project?.id,
      onResult: value => {
        if (value) setStatus({project: project!.id, value});
      },
      onError: () => {},
    },
  );
  return status && project && status.project === project.id ? status.value : null;
}
