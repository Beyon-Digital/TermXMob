import {useCallback, useEffect, useRef, useState} from 'react';
import {json, request, type Project, type Session} from '../../lib/api';
import type {SessionChangesSource} from '../../lib/session-changes';
import {changesSignature} from './groups';
import {finish, loadActions, loadDelivery, readOutcome, readPull, runOperation} from './scm-api';
import {notifyScmChanged} from './scm-bus';
import type {ActionRecord, DeliveryMeta, Prepared, PullRequest} from './scm-types';

type Options = {
  project: Project | null;
  session: Session | null;
  source: SessionChangesSource;
  /** The conversation's checkout changed (another worktree, a switched branch): reload what depends on it. */
  onCheckoutChanged: () => void | Promise<void>;
};

export type ScmError = {message: string; retry?: () => void};

const DONE: Record<string, string> = {
  stage: '',
  unstage: '',
  discard: 'Changes discarded.',
  commit: 'Committed.',
  fetch: 'Fetched.',
  pull: 'Pulled.',
  push: 'Pushed.',
  branch: 'Branch updated.',
  'worktree-create': 'Worktree created.',
  'worktree-remove': 'Worktree removed. Its branch was kept.',
  'pr-create': 'Draft pull request created.',
  'pr-update': 'Pull request updated.',
  'pr-comment': 'Comment posted.',
  'pr-review': 'Review comment posted.',
};

const reasonOf = (reason: unknown) => (reason instanceof Error ? reason.message : String(reason));

/**
 * Everything the Source control view does against the host: the delivery status (ahead and behind, remotes, worktrees, recent
 * operations), one operation at a time (prepare, then run), the confirmation a publishing operation waits for, and the inline
 * error with a way to retry. The changed files themselves come from `source`, the checkout's change list.
 */
export function useScm({project, session, source, onCheckoutChanged}: Options) {
  const worktreeId = session?.worktree_id || '';
  const scope = (project?.id ?? '') + '|' + worktreeId;
  const live = useRef(scope);
  live.current = scope;
  const latest = useRef({source, onCheckoutChanged});
  latest.current = {source, onCheckoutChanged};
  const flight = useRef(false);

  const [meta, setMeta] = useState<{scope: string; value: DeliveryMeta} | null>(null);
  const [records, setRecords] = useState<ActionRecord[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ScmError | null>(null);
  const [notice, setNotice] = useState('');
  const [link, setLink] = useState<{label: string; url: string} | null>(null);
  const [pending, setPending] = useState<Prepared | null>(null);
  const [pull, setPull] = useState<PullRequest | null>(null);

  const loadMeta = useCallback(async () => {
    if (!project || !session) return;
    const mine = scope;
    try {
      const [value, rows] = await Promise.all([loadDelivery(project, worktreeId), loadActions(project)]);
      if (live.current !== mine) return;
      setMeta({scope: mine, value});
      setRecords(rows);
    } catch (reason) {
      if (live.current === mine) setError({message: 'Could not read the Git status: ' + reasonOf(reason) + '.', retry: () => void loadMeta()});
    }
  }, [project?.id, session?.id, worktreeId, scope]);

  useEffect(() => {
    setMeta(null);
    setRecords([]);
    setError(null);
    setNotice('');
    setLink(null);
    setPending(null);
    setPull(null);
  }, [scope]);

  const signature = changesSignature(source.data?.files ?? []);
  const seen = useRef<string | null>(null);
  useEffect(() => {
    void loadMeta();
    // The list moved on its own (an edit, a terminal command): tell the status bar and open diffs too.
    if (seen.current !== null && seen.current !== signature) notifyScmChanged();
    seen.current = signature;
  }, [loadMeta, signature, source.data?.head]);

  async function act(label: string, task: () => Promise<string | void>): Promise<boolean> {
    if (flight.current) return false;
    flight.current = true;
    setBusy(true);
    setError(null);
    setNotice('');
    setLink(null);
    const mine = scope;
    try {
      const text = await task();
      if (live.current !== mine) return false;
      await Promise.all([latest.current.source.refresh(), loadMeta()]);
      notifyScmChanged();
      if (text) setNotice(text);
      return true;
    } catch (reason) {
      if (live.current === mine) setError({message: label + ' failed: ' + reasonOf(reason) + '.', retry: () => void act(label, task)});
      return false;
    } finally {
      flight.current = false;
      if (live.current === mine) setBusy(false);
    }
  }

  /** Prepare an operation. One that needs a confirmation is handed to the dialog instead of running. */
  function operate(label: string, operation: string, args: Record<string, unknown> = {}) {
    return act(label, async () => {
      if (!project) return;
      const {action} = await runOperation(project, worktreeId, operation, args);
      if (action) {
        setPending(action);
        return;
      }
      return DONE[operation];
    });
  }

  const confirm = () =>
    act('Confirm', async () => {
      if (!project || !pending) return;
      const result = (await finish(project, pending, true)) as {html_url?: string; number?: number} | null;
      const done = pending;
      setPending(null);
      if (done.operation === 'pr-create' && result?.html_url) setLink({label: 'Open pull request #' + (result.number ?? ''), url: result.html_url});
      if (done.operation === 'worktree-remove' || done.operation === 'worktree-create') await latest.current.onCheckoutChanged();
      return DONE[done.operation];
    });

  const reconcile = () =>
    act('Check outcome', async () => {
      if (!project || !pending) return;
      const record = await readOutcome(project, pending.id);
      if (record.status === 'completed') setPending(null);
      return 'Recorded outcome: ' + record.status + '.';
    });

  const inspectPull = (number: string) =>
    act('Inspect pull request', async () => {
      if (!project) return;
      if (!/^[1-9]\d*$/.test(number)) throw new Error('Enter a pull request number');
      setPull(await readPull(project, number));
    });

  const chooseCheckout = (id: string) =>
    act('Switch checkout', async () => {
      if (!session) return;
      await request('/api/workspace/sessions/' + session.id, json('PATCH', {revision: session.revision, changes: {worktree_id: id || null}}));
      await latest.current.onCheckoutChanged();
    });

  /** Open an operation the host recorded earlier, to see exactly what it would do. */
  const inspectRecord = (record: ActionRecord) =>
    setPending({id: record.id, operation: record.operation, arguments: JSON.parse(record.arguments), head: record.head, requires_confirmation: true});

  const withBranch = async (label: string, operation: string, args: Record<string, unknown>) => {
    const done = await operate(label, operation, args);
    if (done) await latest.current.onCheckoutChanged();
    return done;
  };

  return {
    meta: meta?.scope === scope ? meta.value : null,
    records,
    busy,
    error,
    notice,
    link,
    pending,
    pull,
    dismissError: () => setError(null),
    cancelPending: () => setPending(null),
    confirm,
    reconcile,
    inspectRecord,
    inspectPull,
    chooseCheckout,
    refresh: () => act('Refresh', async () => {}),
    stage: (paths: string[]) => operate('Stage', 'stage', {paths}),
    unstage: (paths: string[]) => operate('Unstage', 'unstage', {paths}),
    hunk: (patch: string, stage: boolean) => operate(stage ? 'Stage hunk' : 'Unstage hunk', 'hunk', {patch, stage}),
    /** Ask the host what a discard would lose; the dialog confirms it. `includeStaged` also drops the staged side. */
    discard: (paths: string[], includeStaged: boolean) => operate('Discard', 'discard', {paths, include_staged: includeStaged}),
    commit: (message: string) => operate('Commit', 'commit', {message}),
    fetch: () => operate('Fetch', 'fetch'),
    pullChanges: () => operate('Pull', 'pull'),
    push: (remote: string, branch: string) => operate('Push', 'push', {remote, branch}),
    switchBranch: (name: string, create: boolean) => withBranch(create ? 'Create branch' : 'Switch branch', 'branch', {name, create}),
    createWorktree: (branch: string, base: string) => withBranch('Create worktree', 'worktree-create', {branch, base}),
    removeWorktree: (id: string) => operate('Remove worktree', 'worktree-remove', {worktree_id: id}),
    createPr: (title: string, body: string, base: string, head: string) => operate('Create draft PR', 'pr-create', {title, body, head, base, draft: true}),
    updatePr: (number: string, title: string, body: string, base: string) => operate('Update pull request', 'pr-update', {number: Number(number), title, body, base}),
    comment: (number: string, body: string) => operate('Post comment', 'pr-comment', {number: Number(number), body}),
    review: (number: string, body: string) => operate('Post review comment', 'pr-review', {number: Number(number), body, event: 'COMMENT'}),
  };
}

export type Scm = ReturnType<typeof useScm>;
