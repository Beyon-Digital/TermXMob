import {cleanup, render, screen, waitFor} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {readFileSync} from 'node:fs';
import {resolve} from 'node:path';
import {afterEach, beforeAll, describe, expect, it, vi} from 'vitest';
import {deliveryHost, file, project, session, source} from '../../test-utils/scm';
import {clearDrafts} from './commit-drafts';
import DiffTab from './DiffTab';
import ScmView from './ScmView';

const request = vi.hoisted(() => vi.fn());
vi.mock('../../lib/api', async original => ({...(await original<typeof import('../../lib/api')>()), request}));

/**
 * Capability parity. The old Changes panel (ChangeReview) and the old Delivery form are not mounted in the new layout; this
 * lists every button, field, heading and message they have, reads them out of their source so a new one cannot be added without
 * a line here, and asserts that each has a counterpart in the new Source control view, confirmation dialog or diff tab.
 */

type Where = 'scm' | 'clean' | 'ahead' | 'states' | 'dialogs' | 'diff';
type Entry = {to: string[]; where: Where} | {dropped: string};

const root = process.cwd();
const read = (name: string) => readFileSync(resolve(root, 'src/features', name), 'utf8');

/** Every label a person can see or hear in an old component's source. */
function labelsOf(text: string): string[] {
  const out = new Set<string>();
  const add = (value: string) => {
    const label = value.replace(/\s+/g, ' ').trim();
    if (label && /[A-Za-z]/.test(label)) out.add(label);
  };
  for (const match of text.matchAll(/aria-label="([^"]+)"/g)) add(match[1]);
  for (const match of text.matchAll(/\slabel="([^"]+)"/g)) add(match[1]);
  for (const match of text.matchAll(/aria-label=\{([^}]*)\}/g)) for (const quoted of match[1].matchAll(/'([^']+)'/g)) add(quoted[1]);
  for (const match of text.matchAll(/>([^<>{}=]*[A-Za-z][^<>{}=]*)<\/(?:Button|button|summary|h3|h4|strong|small|span|p|option|a)>/g)) add(match[1]);
  for (const match of text.matchAll(/<label>([^<{]+)</g)) add(match[1]);
  for (const match of text.matchAll(/>\{[^{}]*\?'([^']+)':'([^']+)'\}</g)) {
    add(match[1]);
    add(match[2]);
  }
  return [...out].sort();
}

const REVIEW: Record<string, Entry> = {
  'Change review': {dropped: 'The panel is gone. A diff tab holds one file, and the changed files are the side bar lists.'},
  'Uncommitted changes': {where: 'scm', to: ['Staged changes', 'Changes']},
  'Changed files': {where: 'scm', to: ['Staged changes', 'Changes']},
  'Hide changed files': {dropped: 'The file list is the side bar, which has its own toggle (Mod+B, the title bar button).'},
  'Show changed files': {dropped: 'The file list is the side bar, which has its own toggle (Mod+B, the title bar button).'},
  'Refresh changes': {where: 'scm', to: ['Refresh source control']},
  'Refresh review': {where: 'diff', to: ['Refresh diff']},
  'Review actions': {where: 'diff', to: ['Diff view']},
  Unified: {where: 'diff', to: ['Inline']},
  'Unified diff': {where: 'diff', to: ['Inline diff for']},
  Split: {where: 'diff', to: ['Side by side']},
  split: {where: 'diff', to: ['Side by side']},
  'Split diff': {where: 'diff', to: ['Side by side diff for']},
  'diff for': {where: 'diff', to: ['diff for src/a.ts']},
  Before: {where: 'diff', to: ['Before']},
  After: {where: 'diff', to: ['After']},
  'Open file in editor': {where: 'diff', to: ['Open file in editor']},
  'Try again': {where: 'diff', to: ['Try again']},
  'Loading changes…': {where: 'states', to: ['Loading changes…']},
  'Loading file diff…': {where: 'diff', to: ['Loading diff…']},
  'Refreshing…': {dropped: 'A reload keeps the diff on screen and replaces it when the new patch arrives; there is no second status line.'},
  'No uncommitted changes': {where: 'clean', to: ['No changes. The working tree matches the last commit.']},
  'Changes in this conversation’s checkout will appear here.': {where: 'clean', to: ['The working tree matches the last commit.']},
  'This conversation folder is not a Git repository.': {where: 'states', to: ['This folder is not a Git repository.']},
  'Binary file changed': {where: 'diff', to: ['Binary file changed']},
  'A text preview is unavailable for this file.': {where: 'diff', to: ['A text preview is not available.']},
  'This file is too large to preview.': {where: 'diff', to: ['This diff is too large to show here.']},
  'Preview is limited to the first 1 MB. Open the file in the editor to inspect the rest.': {where: 'diff', to: ['This diff is too large to show here.']},
  'No text changes against HEAD.': {where: 'diff', to: ['No unstaged changes in this file.', 'Nothing is staged for this file.']},
  'File mode changes and empty files may have no changed lines.': {dropped: 'The empty-diff sentence stands alone; the detail added nothing a person could act on.'},
  'Showing the first 2,000 changed files.': {where: 'states', to: ['Showing the first 2,000 changed files.']},
  'This file points outside the conversation checkout and cannot be previewed.': {where: 'states', to: ['This file points outside the checkout']},
  partial: {dropped: 'The totals line is gone: each group shows its file count and a diff tab shows its own +/− totals.'},
  M: {where: 'scm', to: ['modified']},
};

const DELIVERY: Record<string, Entry> = {
  'Git delivery': {where: 'scm', to: ['Commit message']},
  'Changes & delivery': {where: 'scm', to: ['Staged changes']},
  Refresh: {where: 'scm', to: ['Refresh source control']},
  'Git checkout': {where: 'scm', to: ['Checkout']},
  'Review worktree cleanup': {where: 'scm', to: ['Remove worktree beta']},
  Stage: {where: 'scm', to: ['Stage notes.md']},
  Unstage: {where: 'scm', to: ['Unstage src/one.ts']},
  Details: {dropped: 'A pull request check keeps its Details link (same text); this is that link.'},
  'Selected Git patch': {dropped: 'A hand-edited patch is retired: a hunk is chosen in the diff tab and sent as it is.'},
  'Selected patch': {dropped: 'A hand-edited patch is retired: a hunk is chosen in the diff tab and sent as it is.'},
  'Stage selected patch': {where: 'diff', to: ['Stage hunk']},
  'Unstage selected patch': {where: 'diff', to: ['Unstage hunk']},
  'Commit message': {where: 'scm', to: ['Commit message']},
  'Commit staged changes': {where: 'scm', to: ['Commit']},
  'Publication remote': {where: 'scm', to: ['Remote for push']},
  Fetch: {where: 'scm', to: ['Fetch']},
  'Pull fast forward': {where: 'scm', to: ['Pull (fast-forward only)']},
  'Review push': {where: 'ahead', to: ['Push']},
  'Branches & parallel worktrees': {where: 'scm', to: ['Branches and worktrees']},
  'Branch name': {where: 'scm', to: ['Branch name']},
  'Base ref': {where: 'scm', to: ['Base ref']},
  'Create branch': {where: 'scm', to: ['Create branch']},
  'Switch branch': {where: 'scm', to: ['Switch branch']},
  'Create isolated worktree': {where: 'scm', to: ['New worktree', 'Use worktree beta']},
  'Pull request & checks': {where: 'scm', to: ['Pull request']},
  Title: {where: 'scm', to: ['Title']},
  Description: {where: 'scm', to: ['Description']},
  'Review draft PR': {where: 'dialogs', to: ['Create a draft pull request']},
  'Pull request number': {where: 'scm', to: ['Pull request number']},
  'Inspect PR & checks': {where: 'scm', to: ['Inspect PR and checks']},
  'Review PR update': {where: 'scm', to: ['Update PR']},
  'Review comment': {where: 'scm', to: ['Post comment']},
  'Review feedback': {where: 'scm', to: ['Review feedback', 'Post review comment']},
  'Recent delivery operations': {where: 'scm', to: ['Recent operations']},
  'Inspect exact operation': {where: 'scm', to: ['Review exact operation']},
  'Outcome uncertain. Inspect the repository and remote before starting a new operation.': {where: 'scm', to: ['Outcome uncertain.']},
  'Exact delivery confirmation': {where: 'dialogs', to: ['This confirmation applies only to the details below.']},
  'Confirm this operation': {where: 'dialogs', to: ['Push', 'Create draft PR', 'Remove worktree']},
  Cancel: {where: 'dialogs', to: ['Cancel']},
  'Reconcile outcome': {where: 'dialogs', to: ['Check outcome']},
  'No uncommitted changes.': {where: 'clean', to: ['No changes. The working tree matches the last commit.']},
  'Select a project to inspect delivery.': {where: 'states', to: ['Choose a project conversation to review changes.']},
};

const inventory = (node: ParentNode = document.body) =>
  [
    node.textContent ?? '',
    ...Array.from(node.querySelectorAll('[aria-label]')).map(item => item.getAttribute('aria-label') ?? ''),
    ...Array.from(node.querySelectorAll('[title]')).map(item => item.getAttribute('title') ?? ''),
  ].join('\n');

const FILES = [file('src/one.ts', 'M', 'M'), file('notes.md', '?', '?'), file('src/clash.ts', 'U', 'U', {status: 'conflict'}), file('outside.ts', ' ', 'M', {reviewable: false})];
const props = {onOpenDiff: vi.fn(), onOpenFile: vi.fn(), onCheckoutChanged: vi.fn()};

const surfaces: Record<Where, string> = {scm: '', clean: '', ahead: '', states: '', dialogs: '', diff: ''};

async function scmSurface() {
  const user = userEvent.setup();
  const host = deliveryHost({
    worktrees: [{id: 'a', path: '/wt/a', branch: 'alpha'}, {id: 'b', path: '/wt/b', branch: 'beta'}],
    records: [{id: 'u', operation: 'pr-create', status: 'unknown', arguments: '{}', head: 'abcdef0', created: 1, result: null}, {id: 'p', operation: 'push', status: 'prepared', arguments: '{}', head: 'abcdef0', created: 1, result: null}],
  });
  request.mockImplementation(host.handler);
  const view = render(<ScmView project={project} session={session({worktree_id: 'a'})} source={source(FILES)} {...props} />);
  await screen.findByRole('button', {name: 'Remove worktree beta'}, {timeout: 500}).catch(() => undefined);
  for (const name of ['Pull request', 'Branches and worktrees', 'Recent operations']) await user.click(await screen.findByRole('button', {name}));
  await user.click(screen.getByRole('button', {name: 'More source control actions'}));
  await screen.findByRole('menuitem', {name: 'Fetch'});
  const text = inventory();
  await user.keyboard('{Escape}');
  view.unmount();
  return text;
}

async function dialogSurface() {
  const user = userEvent.setup();
  const out: string[] = [];
  const host = deliveryHost({branch: 'feature', ahead: 1, worktrees: [{id: 'b', path: '/wt/b', branch: 'beta'}]});
  request.mockImplementation(host.handler);
  const clean = render(<ScmView project={project} session={session()} source={source([])} {...props} />);
  const push = await screen.findByRole('button', {name: 'Push'});
  await waitFor(() => expect(push).toBeEnabled());
  await user.click(push);
  await screen.findByRole('dialog');
  out.push(inventory());
  await user.click(screen.getByRole('button', {name: 'Cancel'}));
  clean.unmount();
  const form = render(<ScmView project={project} session={session()} source={source(FILES)} {...props} />);
  await user.click(await screen.findByRole('button', {name: 'Pull request'}));
  await user.type(screen.getByLabelText('Title'), 'Add it');
  await user.click(screen.getByRole('button', {name: 'Create draft PR'}));
  await screen.findByRole('dialog');
  out.push(inventory());
  await user.click(screen.getByRole('button', {name: 'Cancel'}));
  await user.click(screen.getByRole('button', {name: 'Branches and worktrees'}));
  await user.click(screen.getByRole('button', {name: 'Remove worktree beta'}));
  await screen.findByRole('dialog');
  out.push(inventory());
  form.unmount();
  return out.join('\n');
}

async function stateSurface() {
  const out: string[] = [];
  const states = [
    source([], {data: null}),
    source([], {data: {repo: false, branch: null, head: null, files: [], additions: 0, deletions: 0, counts_complete: true, truncated: false}}),
    source([], {available: false, unavailableReason: 'Choose a project conversation to review changes.', data: null}),
    source(FILES, {data: {repo: true, branch: 'main', head: 'h', files: FILES, additions: 0, deletions: 0, counts_complete: true, truncated: true}}),
  ];
  for (const state of states) {
    request.mockImplementation(deliveryHost().handler);
    const view = render(<ScmView project={project} session={session()} source={state} {...props} />);
    out.push(inventory());
    view.unmount();
  }
  return out.join('\n');
}

const PATCH = 'diff --git a/src/a.ts b/src/a.ts\n--- a/src/a.ts\n+++ b/src/a.ts\n@@ -1,2 +1,2 @@\n keep\n-old\n+new\n@@ -9,1 +9,1 @@\n-x\n+y\n';

async function diffSurface() {
  const user = userEvent.setup();
  const out: string[] = [];
  const reply = (diff: string) =>
    request.mockImplementation(async (path: string) => {
      if (path.includes('/delivery/diff?')) return {diff};
      if (path.includes('/changes/diff?')) return {diff: '', binary: false, truncated: false};
      if (path.includes('/files?')) return {content: 'keep\nold\n'};
      throw new Error('Unexpected ' + path);
    });
  const open = (ref = 'worktree:src/a.ts') => render(<DiffTab tabRef={ref} visible project={project} session={session()} onOpenFile={vi.fn()} onRetarget={vi.fn()} onClose={vi.fn()} />);
  reply(PATCH);
  let view = open();
  const loading = inventory();
  await screen.findByRole('region', {name: 'Inline diff for src/a.ts'});
  out.push(loading, inventory());
  await user.click(screen.getByRole('button', {name: 'Side by side'}));
  out.push(inventory());
  view.unmount();
  const cases = [
    ['Binary files a/x and b/x differ\n', 'worktree:src/a.ts'],
    [HEADER_ONLY + 'x'.repeat(1_100_000), 'worktree:src/a.ts'],
    ['', 'staged:src/a.ts'],
    ['', 'worktree:src/a.ts'],
    [PATCH, 'staged:src/a.ts'],
  ];
  for (const [diff, ref] of cases) {
    reply(diff);
    view = open(ref);
    await waitFor(() => expect(document.body.textContent).not.toContain('Loading diff…'));
    out.push(inventory());
    view.unmount();
  }
  request.mockRejectedValue(new Error('offline'));
  view = open();
  await screen.findByRole('alert');
  out.push(inventory());
  view.unmount();
  return out.join('\n');
}
const HEADER_ONLY = 'diff --git a/a b/a\n--- a/a\n+++ b/a\n@@ -1 +1 @@\n+';

describe('Source control keeps every capability of the old Delivery and Changes panels', () => {
  beforeAll(async () => {
    clearDrafts();
    surfaces.scm = await scmSurface();
    cleanup();
    request.mockImplementation(deliveryHost({ahead: 2}).handler);
    const ahead = render(<ScmView project={project} session={session()} source={source([])} {...props} />);
    await screen.findByRole('button', {name: 'Push'});
    surfaces.ahead = inventory();
    ahead.unmount();
    request.mockImplementation(deliveryHost().handler);
    const clean = render(<ScmView project={project} session={session()} source={source([])} {...props} />);
    surfaces.clean = inventory();
    clean.unmount();
    surfaces.states = await stateSurface();
    surfaces.dialogs = await dialogSurface();
    cleanup();
    surfaces.diff = await diffSurface();
  });
  afterEach(() => request.mockReset());

  const table = (name: string, entries: Record<string, Entry>, extracted: string[]) => {
    it(name + ': every label in the old source is accounted for, and nothing in the table is stale', () => {
      expect(extracted.filter(label => !(label in entries)), 'add these to the parity table').toEqual([]);
      expect(Object.keys(entries).filter(label => !extracted.includes(label)), 'these are no longer in the old source').toEqual([]);
    });
    it(name + ': each label has its counterpart in the new interface', () => {
      const missing: string[] = [];
      for (const [label, entry] of Object.entries(entries)) {
        if ('dropped' in entry) {
          expect(entry.dropped.length, label).toBeGreaterThan(20);
          continue;
        }
        for (const counterpart of entry.to) if (!surfaces[entry.where].includes(counterpart)) missing.push(label + ' -> ' + counterpart + ' (' + entry.where + ')');
      }
      expect(missing).toEqual([]);
    });
  };

  table('ChangeReview', REVIEW, labelsOf(read('ChangeReview.tsx')));
  table('Delivery', DELIVERY, labelsOf(read('Delivery.tsx')));

  it('keeps the new views free of the old components', () => {
    const files = ['scm/ScmView.tsx', 'scm/DiffTab.tsx', '../shell/tab-content.tsx', '../shell/IdeFrame.tsx'];
    for (const name of files) expect(read(name), name).not.toMatch(/features\/(ChangeReview|Delivery)|from '\.\.?\/(ChangeReview|Delivery)'/);
  });

  it('is complete: the table covers everything but the dropped rows, which each say why', () => {
    const dropped = [...Object.entries(REVIEW), ...Object.entries(DELIVERY)].filter(([, entry]) => 'dropped' in entry).map(([label]) => label);
    expect(dropped.sort()).toEqual(['Change review', 'Details', 'File mode changes and empty files may have no changed lines.', 'Hide changed files', 'Refreshing…', 'Selected Git patch', 'Selected patch', 'Show changed files', 'partial']);
  });
});
