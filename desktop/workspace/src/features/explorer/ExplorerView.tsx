import {RefreshCw} from 'lucide-react';
import {useEffect, useState} from 'react';
import {Button} from '../../components/ui/button';
import {Select} from '../../components/ui/select';
import {json, request, type Project, type Session} from '../../lib/api';
import type {GitStatus} from '../../lib/use-git-status';
import ExplorerTree from './ExplorerTree';
import {useFileTree} from './use-file-tree';
import './explorer.css';

type Worktree = {id: string; path: string; branch: string};

type Props = {
  project: Project | null;
  projects: readonly Project[];
  session: Session | null;
  git: GitStatus | null;
  /** The path of the file in the focused editor tab. */
  activePath?: string;
  onOpenFile: (path: string) => void;
  onOpenProject: (project: Project) => void;
  onAddProject: () => void;
  /** The conversation's checkout changed; reload what depends on it. */
  onCheckoutChanged: () => void | Promise<void>;
  onError: (reason: unknown) => void;
};

/** The letter source control shows for a changed file: the working-tree state, else the index state. */
export function gitLetter(file: {index: string; worktree: string}): string {
  const code = (file.worktree.trim() || file.index.trim() || '?').charAt(0).toUpperCase();
  return code === '?' ? 'U' : code;
}

/**
 * The Explorer side bar view: the project and checkout the conversation works in, and its files. A folder's children load when
 * it opens. Choosing a file opens it in a file tab; changed files carry their source control letter.
 */
export default function ExplorerView(props: Props) {
  const {project, projects, session, git} = props;
  const tree = useFileTree(project, session, props.onError);
  const [worktrees, setWorktrees] = useState<Worktree[]>([]);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!project || !session) {
      setWorktrees([]);
      return;
    }
    let stopped = false;
    request<{worktrees: Worktree[]}>('/api/development/projects/' + project.id + '/delivery')
      .then(data => {
        if (!stopped) setWorktrees(data.worktrees);
      })
      .catch(props.onError);
    return () => {
      stopped = true;
    };
  }, [project?.id, session?.id, session?.worktree_id]);

  async function chooseCheckout(id: string) {
    if (!session) return;
    setBusy(true);
    try {
      await request('/api/workspace/sessions/' + session.id, json('PATCH', {revision: session.revision, changes: {worktree_id: id || null}}));
      await props.onCheckoutChanged();
    } catch (reason) {
      props.onError(reason);
    } finally {
      setBusy(false);
    }
  }

  if (!project) {
    return (
      <div className="explorer-empty">
        <p>No project is open.</p>
        <Button onClick={props.onAddProject}>Add project</Button>
      </div>
    );
  }
  const decorations = Object.fromEntries((git?.files ?? []).map(file => [file.path, gitLetter(file)]));
  return (
    <div className="explorer-view">
      <div className="explorer-controls">
        <label>
          <span>Project</span>
          <Select
            aria-label="Project"
            value={project.id}
            onChange={event => {
              const next = projects.find(row => row.id === event.target.value);
              if (next) props.onOpenProject(next);
            }}
          >
            {projects.map(row => (
              <option key={row.id} value={row.id}>
                {row.name}
              </option>
            ))}
          </Select>
        </label>
        {session && (
          <label>
            <span>Checkout</span>
            <Select
              aria-label="Session checkout"
              disabled={busy || !!session.runner_id}
              value={session.worktree_id || ''}
              onChange={event => void chooseCheckout(event.target.value)}
            >
              <option value="">Project checkout</option>
              {worktrees.map(item => (
                <option key={item.id} value={item.id}>
                  {item.branch}
                </option>
              ))}
            </Select>
          </label>
        )}
        <Button variant="ghost" size="icon" aria-label="Refresh explorer" title="Refresh explorer" disabled={busy} onClick={tree.refresh}>
          <RefreshCw size={16} strokeWidth={1.5} aria-hidden="true" />
        </Button>
      </div>
      {tree.loading && !tree.rows.length ? (
        <p className="explorer-note" role="status">
          Loading files…
        </p>
      ) : tree.rows.length ? (
        <ExplorerTree rows={tree.rows} activePath={props.activePath} decorations={decorations} disabled={busy} onToggle={tree.toggle} onOpen={props.onOpenFile} />
      ) : (
        <p className="explorer-note">No files in this folder.</p>
      )}
    </div>
  );
}
