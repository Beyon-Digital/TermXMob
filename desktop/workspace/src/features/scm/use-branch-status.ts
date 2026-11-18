import {useEffect, useState} from 'react';
import {gql, type Project} from '../../lib/api';
import type {GitStatus} from '../../lib/use-git-status';
import {usePolling} from '../../lib/use-polling';
import {onScmChanged} from './scm-bus';

export type BranchStatus = GitStatus & {
  /** The checkout has uncommitted changes. */
  dirty: boolean;
  /** Commits on this branch the upstream does not have, and the other way round. 0 when there is no upstream. */
  ahead: number;
  behind: number;
};

type Row = GitStatus & {ahead?: number | null; behind?: number | null};

const QUERY = 'query($id:String!){git_status(project_id:$id){repo branch ahead behind files{path staged index worktree}}}';

/**
 * The project's branch, whether it is dirty, and how far ahead of or behind its upstream it is: what the title bar and the
 * status bar show. It polls the host and refreshes at once when source control reports a change, so a commit or a stage in
 * the Source control view shows in the status bar straight away. A failed read keeps the last value.
 */
export function useBranchStatus(project: Project | null, enabled: boolean): BranchStatus | null {
  const [status, setStatus] = useState<{project: string; value: BranchStatus} | null>(null);
  const [tick, setTick] = useState(0);
  useEffect(() => onScmChanged(() => setTick(value => value + 1)), []);
  usePolling(async () => (await gql<{git_status: Row}>(QUERY, {id: project!.id})).git_status, {
    interval: 15000,
    maxInterval: 60000,
    enabled: enabled && !!project,
    resetKey: (project?.id ?? '') + ':' + tick,
    onResult: row => {
      if (!row) return;
      setStatus({
        project: project!.id,
        value: {...row, dirty: row.files.length > 0, ahead: row.ahead ?? 0, behind: row.behind ?? 0},
      });
    },
    onError: () => {},
  });
  return status && project && status.project === project.id ? status.value : null;
}
