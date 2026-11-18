import type {Project} from '../../lib/api';
import {relativeFileUri} from '../../lib/lsp';
import {useWorkspaceData} from '../../shell/WorkspaceDataProvider';
import {useBuffers} from './buffer-store';
import './file-tab.css';

type Props = {
  project: Project | null;
  onOpenFile: (path: string, line?: number) => void;
};

/** What the language servers reported for the open files: one row per problem, a click opens the file at its line. */
export default function ProblemsTab({project, onOpenFile}: Props) {
  const {diagnostics} = useBuffers();
  const {session} = useWorkspaceData();
  const root = session?.cwd || project?.path || '';
  const rows = Object.entries(diagnostics).flatMap(([uri, items]) => items.map(item => ({uri, item})));
  if (!rows.length) {
    return (
      <div className="problems-pane" role="status">
        <p>No problems reported by the language server.</p>
      </div>
    );
  }
  return (
    <div className="problems-pane">
      {rows.map(({uri, item}, index) => {
        const path = relativeFileUri(root, uri);
        return (
          <button
            key={index}
            type="button"
            className="problem-row"
            disabled={path === null}
            onClick={() => path !== null && onOpenFile(path, item.range.start.line + 1)}
          >
            <span>{item.message}</span>
            <small>
              {path ?? uri}:{item.range.start.line + 1}
            </small>
          </button>
        );
      })}
    </div>
  );
}
