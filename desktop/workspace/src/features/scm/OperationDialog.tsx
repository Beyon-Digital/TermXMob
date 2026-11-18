import {Button} from '../../components/ui/button';
import {Dialog, DialogContent, DialogDescription, DialogTitle} from '../../components/ui/dialog';
import type {DiscardFile, Prepared, Worktree} from './scm-types';

type Summary = {
  title: string;
  verb: string;
  destructive?: boolean;
  rows: [string, string][];
  /** Files the operation will change, one per line, and a sentence that says it cannot be undone. */
  files?: {path: string; note: string}[];
  warning?: string;
};

const text = (value: unknown) => (typeof value === 'string' ? value : value === undefined || value === null ? '' : String(value));

const KIND_NOTE: Record<DiscardFile['kind'], string> = {
  modified: 'edits are discarded',
  deleted: 'deleted file is restored',
  staged: 'staged changes and edits are discarded',
  untracked: 'untracked file is deleted',
};

/** The dry run of a discard in words: every file, what happens to it, and what stays. */
function discardSummary(action: Prepared): Summary {
  const review = action.review;
  const paths = Array.isArray(action.arguments.paths) ? action.arguments.paths.map(text) : [];
  const files = review ? review.files.map(item => ({path: item.path, note: KIND_NOTE[item.kind] + (item.keeps_staged ? '; staged changes stay' : '')})) : paths.map(path => ({path, note: ''}));
  const rows: [string, string][] = [];
  if (review?.folders.length) rows.push(['Folders', review.folders.join(', ') + ' will be removed with everything untracked in them.']);
  if (review?.already_clean.length) rows.push(['Nothing to discard', review.already_clean.join(', ')]);
  return {
    title: files.length === 0 ? 'Nothing to discard' : files.length === 1 ? 'Discard changes to ' + files[0].path : 'Discard changes to ' + files.length + ' files',
    verb: 'Discard changes',
    destructive: true,
    rows,
    files,
    warning: 'This cannot be undone. Discarded edits and deleted untracked files cannot be recovered from Git.',
  };
}

/** What a confirmation says in words: the operation and every value it will use. No raw JSON. */
export function summarize(action: Prepared, worktrees: readonly Worktree[]): Summary {
  const args = action.arguments;
  const number = text(args.number);
  switch (action.operation) {
    case 'discard':
      return discardSummary(action);
    case 'push':
      return {
        title: 'Push ' + text(args.branch) + ' to ' + text(args.remote),
        verb: 'Push',
        rows: [
          ['Remote', text(args.remote)],
          ['Branch', text(args.branch)],
        ],
      };
    case 'pr-create':
      return {
        title: 'Create a draft pull request',
        verb: 'Create draft PR',
        rows: [
          ['Title', text(args.title)],
          ['Branches', text(args.head) + ' into ' + text(args.base)],
          ['Description', text(args.body) || 'None'],
        ],
      };
    case 'pr-update':
      return {
        title: 'Update pull request #' + number,
        verb: 'Update pull request',
        rows: [
          ['Title', text(args.title)],
          ['Base', text(args.base)],
          ['Description', text(args.body) || 'None'],
        ],
      };
    case 'pr-comment':
      return {title: 'Comment on pull request #' + number, verb: 'Post comment', rows: [['Comment', text(args.body)]]};
    case 'pr-review':
      return {title: 'Post a review comment on pull request #' + number, verb: 'Post review comment', rows: [['Comment', text(args.body)]]};
    case 'worktree-remove': {
      const tree = worktrees.find(item => item.id === args.worktree_id);
      return {
        title: 'Remove this worktree',
        verb: 'Remove worktree',
        destructive: true,
        rows: [
          ['Branch', tree?.branch ?? text(args.worktree_id)],
          ['Folder', tree?.path ?? 'Not listed'],
          ['Branch kept', 'Yes. Only the folder is removed.'],
        ],
      };
    }
    default:
      return {
        title: 'Confirm ' + action.operation,
        verb: 'Confirm',
        rows: Object.entries(args).map(([key, value]) => [key, text(value)] as [string, string]),
      };
  }
}

type Props = {
  action: Prepared | null;
  worktrees: readonly Worktree[];
  busy: boolean;
  /** Why the last attempt failed, shown in the dialog because the page behind it is not reachable. */
  error?: string;
  onConfirm: () => void;
  onCancel: () => void;
  /** Ask the host what became of this operation, when its outcome is in doubt. */
  onCheck: () => void;
};

/** The exact-operation confirmation a publishing or removing operation waits for. Cancel keeps nothing running. */
export default function OperationDialog({action, worktrees, busy, error, onConfirm, onCancel, onCheck}: Props) {
  const summary = action ? summarize(action, worktrees) : null;
  return (
    <Dialog open={!!action} onOpenChange={open => !open && onCancel()}>
      <DialogContent className="scm-dialog" aria-describedby="scm-dialog-note">
        <DialogTitle>{summary?.title ?? ''}</DialogTitle>
        <DialogDescription id="scm-dialog-note">
          The checkout is at {action?.head.slice(0, 7)}. This confirmation applies only to the details below.
        </DialogDescription>
        {summary?.files && (
          <ul className="scm-dialog-files" aria-label="Files">
            {summary.files.map(item => (
              <li key={item.path}>
                <span className="scm-dialog-path">{item.path}</span>
                {item.note && <span className="scm-dialog-detail">{item.note}</span>}
              </li>
            ))}
          </ul>
        )}
        {summary?.warning && (
          <p className="scm-dialog-warning" role="note">
            {summary.warning}
          </p>
        )}
        {error && (
          <p className="scm-dialog-error" role="alert">
            {error}
          </p>
        )}
        <dl className="scm-dialog-rows">
          {summary?.rows.map(([label, value]) => (
            <div key={label}>
              <dt>{label}</dt>
              <dd>{value}</dd>
            </div>
          ))}
        </dl>
        <div className="scm-dialog-actions">
          <Button variant="ghost" onClick={onCancel}>
            Cancel
          </Button>
          <Button variant="outline" disabled={busy} onClick={onCheck}>
            Check outcome
          </Button>
          <Button variant={summary?.destructive ? 'destructive' : 'default'} disabled={busy} onClick={onConfirm}>
            {summary?.verb}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
