import {useEffect, useMemo, useRef, useState} from 'react';
import type {Project, Session} from '../../lib/api';
import type {SessionChangesSource} from '../../lib/session-changes';
import BranchesSection from './BranchesSection';
import CommitBox, {type Primary} from './CommitBox';
import {readDraft, writeDraft} from './commit-drafts';
import {groupChanges} from './groups';
import OperationDialog from './OperationDialog';
import OperationsSection from './OperationsSection';
import PullRequestSection from './PullRequestSection';
import ScmGroup from './ScmGroup';
import ScmHeader from './ScmHeader';
import {SCM_COMMAND_EVENT, takeScmCommand, type ScmCommand} from './scm-bus';
import type {ScmEntry, Side} from './scm-types';
import {useScm} from './use-scm';
import './scm.css';
import './scm-forms.css';

type Props = {
  project: Project | null;
  session: Session | null;
  /** The checkout's changed files, from the same source the rest of the shell reads. */
  source: SessionChangesSource;
  onOpenDiff: (path: string, side: Side) => void;
  onOpenFile: (path: string) => void;
  /** The conversation's checkout changed: reload what depends on it. */
  onCheckoutChanged: () => void | Promise<void>;
};

/**
 * Source control: the branch, a commit box, and the changed files as Merge conflicts, Staged changes and Changes. The commit
 * button is the view's one primary button; it becomes Push when the checkout is clean and ahead of its upstream. Pull requests,
 * branches and worktrees stay collapsed below.
 */
export default function ScmView({project, session, source, onOpenDiff, onOpenFile, onCheckoutChanged}: Props) {
  const scm = useScm({project, session, source, onCheckoutChanged});
  const scope = (project?.id ?? '') + '|' + (session?.worktree_id ?? '');
  const [message, setMessage] = useState(() => readDraft(scope));
  const [remote, setRemote] = useState('origin');
  const textarea = useRef<HTMLTextAreaElement | null>(null);
  const files = source.data?.files ?? [];
  const groups = useMemo(() => groupChanges(files), [files]);
  const {meta} = scm;
  const branch = meta?.branch ?? source.data?.branch;

  useEffect(() => setMessage(readDraft(scope)), [scope]);
  useEffect(() => {
    if (meta?.remotes.length && !meta.remotes.includes(remote)) setRemote(meta.remotes[0]);
  }, [meta?.remotes.join('|')]);
  const edit = (next: string) => {
    writeDraft(scope, next);
    setMessage(next);
  };

  const ahead = meta?.ahead ?? 0;
  const clean = files.length === 0;
  const push = clean && ahead > 0 && !!branch && branch !== 'HEAD';
  const commitReason = (() => {
    if (clean) return 'No changes to commit.';
    if (groups.conflicts.length) return 'Resolve the merge conflicts before committing.';
    if (!groups.staged.length) return 'Nothing is staged. Stage changes first.';
    if (!message.trim()) return 'Enter a commit message.';
    return '';
  })();
  const primary: Primary = push
    ? {kind: 'push', label: 'Push', reason: '', note: (ahead === 1 ? '1 commit' : ahead + ' commits') + ' ahead of the upstream branch.'}
    : {kind: 'commit', label: 'Commit', reason: commitReason};

  async function commit() {
    if (commitReason || scm.busy) {
      textarea.current?.focus();
      return;
    }
    if (await scm.commit(message)) edit('');
  }
  const pushNow = () => void (branch && scm.push(remote, branch));
  const stageAll = () => void scm.stage(groups.changes.map(entry => entry.path));

  const run = useRef<(command: ScmCommand) => void>(() => {});
  run.current = command => {
    if (command === 'commit') void commit();
    else if (command === 'stageAll') stageAll();
    else void scm.refresh();
  };
  useEffect(() => {
    const take = () => {
      const command = takeScmCommand();
      if (command) run.current(command);
    };
    take();
    window.addEventListener(SCM_COMMAND_EVENT, take);
    return () => window.removeEventListener(SCM_COMMAND_EVENT, take);
  }, []);

  if (!source.available) return <p className="view-empty view-empty-padded">{source.unavailableReason}</p>;
  if (source.error) {
    return (
      <div className="view-empty-padded scm-problem" role="alert">
        <p className="view-empty">{source.error}</p>
        <button type="button" className="scm-text-action" onClick={() => void source.refresh()}>
          Try again
        </button>
      </div>
    );
  }
  if (!source.data) return <p className="view-empty view-empty-padded" role="status">Loading changes…</p>;
  if (!source.data.repo) return <p className="view-empty view-empty-padded">This folder is not a Git repository.</p>;

  const open = (entry: ScmEntry) => (entry.side === 'conflict' ? onOpenFile(entry.path) : onOpenDiff(entry.path, entry.side));
  const rows = {busy: scm.busy, onOpen: open, onOpenFile, onStage: (entry: ScmEntry) => void scm.stage([entry.path]), onUnstage: (entry: ScmEntry) => void scm.unstage([entry.path])};
  return (
    <div className="scm-view">
      <ScmHeader
        branch={branch}
        meta={meta}
        currentId={session?.worktree_id ?? ''}
        busy={scm.busy}
        disableCheckout={!!session?.runner_id}
        onCheckout={id => void scm.chooseCheckout(id)}
        onRefresh={() => void scm.refresh()}
        onFetch={() => void scm.fetch()}
        onPull={() => void scm.pullChanges()}
        onPush={pushNow}
      />
      <div className="scm-scroll">
        <CommitBox message={message} onMessage={edit} primary={primary} busy={scm.busy} textarea={textarea} onCommit={() => void commit()} onPrimary={push ? pushNow : () => void commit()} />
        {scm.error && (
          <div className="view-section scm-problem" role="alert">
            <p>{scm.error.message}</p>
            <div className="scm-button-row">
              {scm.error.retry && (
                <button type="button" className="scm-text-action" onClick={scm.error.retry}>
                  Try again
                </button>
              )}
              <button type="button" className="scm-text-action" onClick={scm.dismissError}>
                Dismiss
              </button>
            </div>
          </div>
        )}
        {scm.notice && (
          <p className="view-section scm-notice" role="status">
            {scm.notice}
            {scm.link && (
              <>
                {' '}
                <a href={scm.link.url} target="_blank" rel="noreferrer">
                  {scm.link.label}
                </a>
              </>
            )}
          </p>
        )}
        {clean ? (
          <p className="view-empty view-empty-padded">No changes. The working tree matches the last commit.</p>
        ) : (
          <>
            {groups.conflicts.length > 0 && <ScmGroup title="Merge conflicts" entries={groups.conflicts} {...rows} />}
            {groups.staged.length > 0 && (
              <ScmGroup
                title="Staged changes"
                entries={groups.staged}
                {...rows}
                bulk={{label: 'Unstage all', aria: 'Unstage all staged changes', run: () => void scm.unstage(groups.staged.map(entry => entry.path))}}
              />
            )}
            {groups.changes.length > 0 && (
              <ScmGroup title="Changes" entries={groups.changes} {...rows} bulk={{label: 'Stage all', aria: 'Stage all changes', run: stageAll}} />
            )}
          </>
        )}
        {source.data.truncated && <p className="view-empty view-empty-padded">Showing the first 2,000 changed files.</p>}
        <PullRequestSection scm={scm} branch={branch ?? undefined} />
        <BranchesSection scm={scm} meta={meta} currentId={session?.worktree_id ?? ''} remote={remote} onRemote={setRemote} />
        <OperationsSection records={scm.records} busy={scm.busy} onInspect={scm.inspectRecord} />
      </div>
      <OperationDialog action={scm.pending} worktrees={meta?.worktrees ?? []} busy={scm.busy} onConfirm={() => void scm.confirm()} onCancel={scm.cancelPending} onCheck={() => void scm.reconcile()} />
    </div>
  );
}
