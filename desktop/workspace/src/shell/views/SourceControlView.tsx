import {Button} from '../../components/ui/button';
import type {SessionChangesSource} from '../../lib/session-changes';
import './views.css';

const LETTER = {added: 'A', modified: 'M', deleted: 'D', renamed: 'R', conflict: 'C'} as const;
const WORD = {added: 'added', modified: 'modified', deleted: 'deleted', renamed: 'renamed', conflict: 'conflict'} as const;

type Props = {
  source: SessionChangesSource;
  branch?: string;
  /** Open a changed file in the editor. */
  onOpenFile: (path: string) => void;
  /** Open the Changes tab, where each file's diff is reviewed. */
  onReview: () => void;
};

/**
 * Source control, read only for now: the files the conversation's checkout changed, from the same source the Changes tab
 * reads. Committing and the pull request arrive with the merged source control view.
 */
export default function SourceControlView({source, branch, onOpenFile, onReview}: Props) {
  const files = source.data?.files ?? [];
  if (!source.available) return <p className="view-empty view-empty-padded">{source.unavailableReason}</p>;
  if (source.error) return <p className="view-empty view-empty-padded">{source.error}</p>;
  if (!source.data) return <p className="view-empty view-empty-padded" role="status">Loading changes…</p>;
  if (!source.data.repo) return <p className="view-empty view-empty-padded">This folder is not a git repository.</p>;
  return (
    <div className="scm-view">
      <div className="view-section view-section-first scm-head">
        <span className="view-row-meta">{branch ? 'On ' + branch : 'Changes'}</span>
        <Button variant="ghost" size="sm" onClick={onReview} disabled={!files.length}>
          Review changes
        </Button>
      </div>
      {files.length === 0 ? (
        <p className="view-empty view-empty-padded">No changes in this checkout.</p>
      ) : (
        <ul className="view-list scm-list" aria-label="Changed files">
          {files.map(file => (
            <li key={file.path}>
              <button type="button" className="view-row scm-row" title={file.path} onClick={() => onOpenFile(file.path)}>
                <span className="view-row-title">{file.path}</span>
                <span className="scm-letter">
                  <span aria-hidden="true">{LETTER[file.status]}</span>
                  <span className="visually-hidden">{WORD[file.status]}</span>
                </span>
                {file.additions !== null && file.deletions !== null && (
                  <span className="view-row-meta scm-counts">
                    +{file.additions} −{file.deletions}
                  </span>
                )}
              </button>
            </li>
          ))}
        </ul>
      )}
      {source.data.truncated && <p className="view-empty view-empty-padded">Showing the first changed files only.</p>}
    </div>
  );
}
