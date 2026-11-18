import {cleanup, render, screen, waitFor, within} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest';
import {expectNoAxeViolations} from '../../test-axe';
import {deliveryHost, file, project, session, source} from '../../test-utils/scm';
import {clearDrafts} from './commit-drafts';
import {requestScmCommand} from './scm-bus';
import ScmView from './ScmView';

const request = vi.hoisted(() => vi.fn());
vi.mock('../../lib/api', async original => ({...(await original<typeof import('../../lib/api')>()), request}));

beforeEach(() => {
  localStorage.clear();
  clearDrafts();
});
afterEach(() => {
  cleanup();
  request.mockReset();
});

const FILES = [
  file('src/one.ts', 'M', 'M'),
  file('notes.md', '?', '?'),
  file('old.txt', ' ', 'D'),
  file('both.ts', 'A', ' '),
];

/** The row's main button for a file name. The row's own action buttons carry the path in their names, so a name query would match them too. */
const rowButton = (list: HTMLElement, name: string) =>
  within(list)
    .getAllByRole('listitem')
    .find(item => item.textContent?.includes(name))!
    .querySelector<HTMLButtonElement>('[data-scm-file]')!;

function view(host: ReturnType<typeof deliveryHost>, extra: Partial<React.ComponentProps<typeof ScmView>> = {}) {
  request.mockImplementation(host.handler);
  const props = {
    project,
    session: session(),
    source: source(FILES),
    onOpenDiff: vi.fn(),
    onOpenFile: vi.fn(),
    onCheckoutChanged: vi.fn(),
    ...extra,
  };
  return {props, ...render(<ScmView {...props} />)};
}

describe('ScmView groups', () => {
  it('lists staged changes and changes with a letter, a word and 32px rows, and a file with both edits in both', async () => {
    const host = deliveryHost();
    const {container} = view(host);
    const staged = await screen.findByRole('list', {name: 'Staged changes'});
    const changes = screen.getByRole('list', {name: 'Changes'});
    expect(within(staged).getAllByRole('listitem').map(item => item.textContent)).toEqual([expect.stringContaining('one.ts'), expect.stringContaining('both.ts')]);
    expect(within(changes).getAllByRole('listitem')).toHaveLength(3);
    const modified = within(staged).getAllByRole('listitem')[0];
    expect(modified).toHaveTextContent('modified');
    expect(modified.querySelector('[data-letter]')).toHaveTextContent('M');
    const untracked = within(changes).getAllByRole('listitem').find(item => item.textContent?.includes('notes.md'))!;
    expect(untracked.querySelector('[data-letter]')).toHaveTextContent('U');
    expect(within(changes).getAllByRole('listitem').find(item => item.textContent?.includes('old.txt'))!.querySelector('[data-letter]')).toHaveTextContent('D');
    expect(screen.getByRole('heading', {name: /Staged changes\s*2/})).toBeInTheDocument();
    expect(container.querySelectorAll('.button-primary')).toHaveLength(1);
    await expectNoAxeViolations(container);
  });

  it('lists merge conflicts apart and opens them in the editor', async () => {
    const user = userEvent.setup();
    const conflict = file('src/clash.ts', 'U', 'U', {status: 'conflict'});
    const {props} = view(deliveryHost(), {source: source([conflict])});
    const list = await screen.findByRole('list', {name: 'Merge conflicts'});
    await user.click(rowButton(list, 'clash.ts'));
    expect(props.onOpenFile).toHaveBeenCalledWith('src/clash.ts');
    expect(screen.getByRole('button', {name: 'Mark as resolved src/clash.ts'})).toBeInTheDocument();
    expect(screen.getByText('Resolve the merge conflicts before committing.')).toBeInTheDocument();
  });

  it('opens a diff for the side the row is listed on, and the file itself from the row action', async () => {
    const user = userEvent.setup();
    const {props} = view(deliveryHost());
    const staged = await screen.findByRole('list', {name: 'Staged changes'});
    await user.click(rowButton(staged, 'one.ts'));
    expect(props.onOpenDiff).toHaveBeenCalledWith('src/one.ts', 'staged');
    const changes = screen.getByRole('list', {name: 'Changes'});
    await user.click(rowButton(changes, 'notes.md'));
    expect(props.onOpenDiff).toHaveBeenCalledWith('notes.md', 'worktree');
    await user.click(screen.getByRole('button', {name: 'Open file notes.md'}));
    expect(props.onOpenFile).toHaveBeenCalledWith('notes.md');
    expect(screen.queryByRole('button', {name: 'Open file old.txt'})).not.toBeInTheDocument();
  });

  it('moves between rows with the arrow keys', async () => {
    const user = userEvent.setup();
    view(deliveryHost());
    const changes = await screen.findByRole('list', {name: 'Changes'});
    const rows = within(changes).getAllByRole('listitem').map(item => item.querySelector<HTMLButtonElement>('[data-scm-file]')!);
    rows[0].focus();
    await user.keyboard('{ArrowDown}');
    expect(rows[1]).toHaveFocus();
    await user.keyboard('{End}');
    expect(rows.at(-1)).toHaveFocus();
    await user.keyboard('{Home}');
    expect(rows[0]).toHaveFocus();
  });

  it('says so in one sentence when the checkout is clean, and when there is nothing to read', () => {
    const {unmount} = view(deliveryHost(), {source: source([])});
    expect(screen.getByText('No changes. The working tree matches the last commit.')).toBeInTheDocument();
    unmount();
    view(deliveryHost(), {source: source([], {available: false, unavailableReason: 'Choose a conversation to review its changes.', data: null})});
    expect(screen.getByText('Choose a conversation to review its changes.')).toBeInTheDocument();
  });

  it('offers Add project as its one action when there is no project', async () => {
    const user = userEvent.setup();
    const onAddProject = vi.fn();
    view(deliveryHost(), {project: null, session: null, onAddProject, source: source([], {available: false, unavailableReason: 'Choose a project conversation to review changes.', data: null})});
    await user.click(screen.getByRole('button', {name: 'Add project'}));
    expect(onAddProject).toHaveBeenCalledTimes(1);
  });

  it('names a folder that is not a repository, and offers to retry a failed read', async () => {
    const user = userEvent.setup();
    const {unmount} = view(deliveryHost(), {source: source([], {data: {repo: false, branch: null, head: null, files: [], additions: 0, deletions: 0, counts_complete: true, truncated: false}})});
    expect(screen.getByText('This folder is not a Git repository.')).toBeInTheDocument();
    unmount();
    const refresh = vi.fn(async () => {});
    view(deliveryHost(), {source: source([], {data: null, error: 'Changes could not be read', refresh})});
    expect(screen.getByRole('alert')).toHaveTextContent('Changes could not be read');
    await user.click(screen.getByRole('button', {name: 'Try again'}));
    expect(refresh).toHaveBeenCalled();
  });
});

describe('ScmView staging', () => {
  it('stages and unstages one file, then refreshes the list', async () => {
    const user = userEvent.setup();
    const host = deliveryHost();
    const {props} = view(host);
    await user.click(await screen.findByRole('button', {name: 'Stage notes.md'}));
    await waitFor(() => expect(host.executed()).toHaveLength(1));
    expect(host.operations()[0]).toEqual({operation: 'stage', arguments: {paths: ['notes.md']}});
    expect(props.source.refresh).toHaveBeenCalled();
    await user.click(screen.getByRole('button', {name: 'Unstage both.ts'}));
    await waitFor(() => expect(host.executed()).toHaveLength(2));
    expect(host.operations()[1]).toEqual({operation: 'unstage', arguments: {paths: ['both.ts']}});
  });

  it('stages and unstages a whole group, and sends the conversation checkout with every operation', async () => {
    const user = userEvent.setup();
    const host = deliveryHost({worktrees: [{id: 'tree', path: '/wt', branch: 'feature'}]});
    view(host, {session: session({worktree_id: 'tree'})});
    await user.click(await screen.findByRole('button', {name: 'Stage all changes'}));
    await waitFor(() => expect(host.executed()).toHaveLength(1));
    expect(host.operations()[0]).toEqual({operation: 'stage', arguments: {worktree_id: 'tree', paths: ['src/one.ts', 'notes.md', 'old.txt']}});
    await user.click(screen.getByRole('button', {name: 'Unstage all staged changes'}));
    await waitFor(() => expect(host.executed()).toHaveLength(2));
    expect(host.operations()[1].arguments.paths).toEqual(['src/one.ts', 'both.ts']);
    expect(host.calls.find(call => /\/delivery\?/.test(call.path))?.path).toContain('worktree_id=tree');
  });

  it('shows what failed and offers to try again', async () => {
    const user = userEvent.setup();
    const host = deliveryHost({outcome: {stage: {status: 'failed', result: {error: 'pathspec did not match'}}}});
    view(host);
    await user.click(await screen.findByRole('button', {name: 'Stage notes.md'}));
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('Stage failed: pathspec did not match.');
    await user.click(within(alert).getByRole('button', {name: 'Try again'}));
    await waitFor(() => expect(host.executed()).toHaveLength(2));
    await user.click(within(await screen.findByRole('alert')).getByRole('button', {name: 'Dismiss'}));
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

});

describe('ScmView discard', () => {
  it('asks the host what would be lost and runs nothing until the dialog is confirmed', async () => {
    const user = userEvent.setup();
    const host = deliveryHost({discardKinds: {'notes.md': 'untracked', 'old.txt': 'deleted'}});
    const {props} = view(host);
    await user.click(await screen.findByRole('button', {name: 'Discard changes notes.md'}));
    const dialog = await screen.findByRole('dialog', {name: 'Discard changes to notes.md'});
    expect(host.operations()).toEqual([{operation: 'discard', arguments: {paths: ['notes.md'], include_staged: false}}]);
    expect(host.executed()).toHaveLength(0);
    expect(within(dialog).getByText('notes.md')).toBeInTheDocument();
    expect(within(dialog).getByText('untracked file is deleted')).toBeInTheDocument();
    expect(dialog).toHaveTextContent('This cannot be undone.');
    await user.click(within(dialog).getByRole('button', {name: 'Discard changes'}));
    await waitFor(() => expect(host.executed()).toEqual([{confirmed: true}]));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(screen.getByRole('status')).toHaveTextContent('Changes discarded.');
    expect(props.source.refresh).toHaveBeenCalled();
  });

  it('cancels without sending anything, and Escape cancels too', async () => {
    const user = userEvent.setup();
    const host = deliveryHost();
    view(host);
    await user.click(await screen.findByRole('button', {name: 'Discard changes old.txt'}));
    const dialog = await screen.findByRole('dialog');
    await user.click(within(dialog).getByRole('button', {name: 'Cancel'}));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    await user.click(screen.getByRole('button', {name: 'Discard changes old.txt'}));
    await screen.findByRole('dialog');
    await user.keyboard('{Escape}');
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(host.executed()).toHaveLength(0);
  });

  it('discards the whole Changes group with every file listed, and keeps staged changes unless asked', async () => {
    const user = userEvent.setup();
    const host = deliveryHost({worktrees: [{id: 'tree', path: '/wt', branch: 'feature'}]});
    view(host, {session: session({worktree_id: 'tree'})});
    await user.click(await screen.findByRole('button', {name: 'Discard all changes'}));
    const dialog = await screen.findByRole('dialog', {name: 'Discard changes to 3 files'});
    expect(host.operations()[0]).toEqual({operation: 'discard', arguments: {worktree_id: 'tree', paths: ['src/one.ts', 'notes.md', 'old.txt'], include_staged: false}});
    expect(within(within(dialog).getByRole('list', {name: 'Files'})).getAllByRole('listitem')).toHaveLength(3);
  });

  it('discards a staged row together with its unstaged edits, and says so', async () => {
    const user = userEvent.setup();
    const host = deliveryHost({discardKinds: {'both.ts': 'staged'}});
    view(host);
    await user.click(await screen.findByRole('button', {name: 'Discard staged changes both.ts'}));
    const dialog = await screen.findByRole('dialog', {name: 'Discard changes to both.ts'});
    expect(host.operations()[0].arguments).toEqual({paths: ['both.ts'], include_staged: true});
    expect(within(dialog).getByText('staged changes and edits are discarded')).toBeInTheDocument();
  });

  it('is destructive only on the final button, and offers no discard for a conflict', async () => {
    const user = userEvent.setup();
    const host = deliveryHost();
    const conflict = file('src/clash.ts', 'U', 'U', {status: 'conflict'});
    const {container, unmount} = view(host, {source: source([conflict])});
    await screen.findByRole('list', {name: 'Merge conflicts'});
    expect(screen.queryByRole('button', {name: /discard/i})).not.toBeInTheDocument();
    unmount();
    const second = view(host);
    expect(second.container.querySelectorAll('.button-destructive')).toHaveLength(0);
    await user.click(await screen.findByRole('button', {name: 'Discard changes old.txt'}));
    const dialog = await screen.findByRole('dialog');
    expect(dialog.querySelectorAll('.button-destructive')).toHaveLength(1);
    expect(within(dialog).getByRole('button', {name: 'Discard changes'})).toHaveClass('button-destructive');
    expect(dialog.querySelectorAll('.button-primary')).toHaveLength(0);
    void container;
  });

  it('reports a refusal from the host inline and does not close over it silently', async () => {
    const user = userEvent.setup();
    const host = deliveryHost({outcome: {discard: {status: 'failed', result: {error: 'Files changed while preparing'}}}});
    view(host);
    await user.click(await screen.findByRole('button', {name: 'Discard changes old.txt'}));
    await user.click(within(await screen.findByRole('dialog')).getByRole('button', {name: 'Discard changes'}));
    expect(await screen.findByRole('alert')).toHaveTextContent('Confirm failed: Files changed while preparing.');
  });
});

describe('ScmView commit', () => {
  it('is the only primary button and says why it is off', async () => {
    const {container, unmount} = view(deliveryHost(), {source: source([file('a.ts', ' ', 'M')])});
    const commit = await screen.findByRole('button', {name: 'Commit'});
    expect(commit).toBeDisabled();
    expect(screen.getByText('Nothing is staged. Stage changes first.')).toBeInTheDocument();
    expect(commit).toHaveAccessibleDescription('Nothing is staged. Stage changes first.');
    expect(container.querySelectorAll('.button-primary')).toHaveLength(1);
    unmount();
    view(deliveryHost(), {source: source([file('a.ts', 'M', ' ')])});
    expect(await screen.findByText('Enter a commit message.')).toBeInTheDocument();
    expect(screen.getByRole('button', {name: 'Commit'})).toBeDisabled();
    cleanup();
    view(deliveryHost(), {source: source([])});
    expect(screen.getByText('No changes to commit.')).toBeInTheDocument();
  });

  it('commits staged changes with the message and clears the box', async () => {
    const user = userEvent.setup();
    const host = deliveryHost();
    view(host, {source: source([file('a.ts', 'M', ' ')])});
    const box = await screen.findByRole('textbox', {name: 'Commit message'});
    await user.type(box, 'Fix the parser{Enter}Handle empty input');
    expect(screen.getByText('Subject 14 of 72')).toBeInTheDocument();
    await user.click(screen.getByRole('button', {name: 'Commit'}));
    await waitFor(() => expect(host.executed()).toHaveLength(1));
    expect(host.operations()[0]).toEqual({operation: 'commit', arguments: {message: 'Fix the parser\nHandle empty input'}});
    await waitFor(() => expect(box).toHaveValue(''));
    expect(await screen.findByRole('status')).toHaveTextContent('Committed.');
  });

  it('commits with Mod+Enter from the box', async () => {
    const user = userEvent.setup();
    const host = deliveryHost();
    view(host, {source: source([file('a.ts', 'M', ' ')])});
    await user.type(await screen.findByRole('textbox', {name: 'Commit message'}), 'One line');
    await user.keyboard('{Control>}{Enter}{/Control}');
    await waitFor(() => expect(host.operations()).toEqual([{operation: 'commit', arguments: {message: 'One line'}}]));
  });

  it('warns, without blocking, when the subject is longer than 72 characters', async () => {
    const user = userEvent.setup();
    view(deliveryHost(), {source: source([file('a.ts', 'M', ' ')])});
    await user.click(await screen.findByRole('textbox', {name: 'Commit message'}));
    await user.paste('x'.repeat(80));
    expect(screen.getByText('Subject is 80 characters. Aim for 72 or fewer.')).toBeInTheDocument();
    expect(screen.getByRole('button', {name: 'Commit'})).toBeEnabled();
  });

  it('keeps the message and says what to do when the commit fails', async () => {
    const user = userEvent.setup();
    const host = deliveryHost({outcome: {commit: {status: 'failed', result: {error: 'nothing added to commit'}}}});
    view(host, {source: source([file('a.ts', 'M', ' ')])});
    const box = await screen.findByRole('textbox', {name: 'Commit message'});
    await user.type(box, 'Try it');
    await user.click(screen.getByRole('button', {name: 'Commit'}));
    expect(await screen.findByRole('alert')).toHaveTextContent('Commit failed: nothing added to commit.');
    expect(box).toHaveValue('Try it');
  });

  it('keeps the message when the view is closed and reopened', async () => {
    const user = userEvent.setup();
    const host = deliveryHost();
    const first = view(host, {source: source([file('a.ts', 'M', ' ')])});
    await user.type(await screen.findByRole('textbox', {name: 'Commit message'}), 'Half written');
    first.unmount();
    view(host, {source: source([file('a.ts', 'M', ' ')])});
    expect(await screen.findByRole('textbox', {name: 'Commit message'})).toHaveValue('Half written');
  });
});

describe('ScmView push', () => {
  it('becomes Push only when the checkout is clean and ahead, and confirms the exact push first', async () => {
    const user = userEvent.setup();
    const host = deliveryHost({ahead: 2, branch: 'feature'});
    const {container} = view(host, {source: source([])});
    const push = await screen.findByRole('button', {name: 'Push'});
    await waitFor(() => expect(push).toBeEnabled());
    expect(container.querySelectorAll('.button-primary')).toHaveLength(1);
    expect(screen.getByText('2 commits ahead of the upstream branch.')).toBeInTheDocument();
    await user.click(push);
    const dialog = await screen.findByRole('dialog', {name: 'Push feature to origin'});
    expect(within(dialog).getByText('abcdef0', {exact: false})).toBeInTheDocument();
    expect(host.executed()).toHaveLength(0);
    await user.click(within(dialog).getByRole('button', {name: 'Push'}));
    await waitFor(() => expect(host.executed()).toEqual([{confirmed: true}]));
    expect(host.operations()[0]).toEqual({operation: 'push', arguments: {remote: 'origin', branch: 'feature'}});
  });

  it('has no axe violations with the confirmation open', async () => {
    const user = userEvent.setup();
    view(deliveryHost({ahead: 1}), {source: source([])});
    const push = await screen.findByRole('button', {name: 'Push'});
    await waitFor(() => expect(push).toBeEnabled());
    await user.click(push);
    await screen.findByRole('dialog');
    await expectNoAxeViolations(document.body);
  });

  it('stays a disabled Commit while there are changes, even when ahead', async () => {
    view(deliveryHost({ahead: 3}), {source: source([file('a.ts', ' ', 'M')])});
    await screen.findByRole('button', {name: 'Commit'});
    expect(screen.queryByRole('button', {name: 'Push'})).not.toBeInTheDocument();
  });

  it('cancels a confirmation without running anything, and can check the recorded outcome', async () => {
    const user = userEvent.setup();
    const host = deliveryHost({ahead: 1});
    request.mockImplementation(async (path: string, init?: RequestInit) => (path.endsWith('/actions/a2') ? {status: 'completed', result: null} : host.handler(path, init)));
    render(<ScmView project={project} session={session()} source={source([])} onOpenDiff={vi.fn()} onOpenFile={vi.fn()} onCheckoutChanged={vi.fn()} />);
    const push = await screen.findByRole('button', {name: 'Push'});
    await waitFor(() => expect(push).toBeEnabled());
    await user.click(push);
    await user.click(await screen.findByRole('button', {name: 'Cancel'}));
    expect(host.executed()).toHaveLength(0);
    await user.click(push);
    await user.click(await screen.findByRole('button', {name: 'Check outcome'}));
    expect(await screen.findByText('Recorded outcome: completed.')).toBeInTheDocument();
    expect(host.executed()).toHaveLength(0);
  });

  it('runs Fetch and Pull from the menu', async () => {
    const user = userEvent.setup();
    const host = deliveryHost();
    view(host);
    await user.click(await screen.findByRole('button', {name: 'More source control actions'}));
    await user.click(await screen.findByRole('menuitem', {name: 'Fetch'}));
    await waitFor(() => expect(host.operations().map(item => item.operation)).toEqual(['fetch']));
    await user.click(screen.getByRole('button', {name: 'More source control actions'}));
    await user.click(await screen.findByRole('menuitem', {name: 'Pull (fast-forward only)'}));
    await waitFor(() => expect(host.operations().map(item => item.operation)).toEqual(['fetch', 'pull']));
  });

  it('shows the branch with ahead and behind in words', async () => {
    view(deliveryHost({ahead: 2, behind: 1, branch: 'feature'}));
    expect(await screen.findByText('2 ahead, 1 behind')).toBeInTheDocument();
    expect(screen.getByText('feature')).toBeInTheDocument();
  });
});

describe('ScmView pull request, branches and worktrees', () => {
  it('keeps the pull request form collapsed, drafts one and confirms exactly what it sends', async () => {
    const user = userEvent.setup();
    const host = deliveryHost({branch: 'feature'});
    const {container} = view(host);
    const trigger = await screen.findByRole('button', {name: 'Pull request'});
    expect(trigger).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByRole('button', {name: 'Create draft PR'})).not.toBeInTheDocument();
    await user.click(trigger);
    const create = screen.getByRole('button', {name: 'Create draft PR'});
    expect(create).toBeDisabled();
    await user.type(screen.getByLabelText('Title'), 'Add the parser');
    await user.type(screen.getByLabelText('Description'), 'Handles empty input.');
    expect(screen.getByText('Head feature into main. New pull requests start as drafts.')).toBeInTheDocument();
    await expectNoAxeViolations(container);
    await user.click(create);
    const dialog = await screen.findByRole('dialog', {name: 'Create a draft pull request'});
    expect(within(dialog).getByText('feature into main')).toBeInTheDocument();
    expect(within(dialog).queryByText(/[{}]/)).not.toBeInTheDocument();
    await user.click(within(dialog).getByRole('button', {name: 'Create draft PR'}));
    await waitFor(() => expect(host.executed()).toEqual([{confirmed: true}]));
    expect(host.operations()[0]).toEqual({operation: 'pr-create', arguments: {title: 'Add the parser', body: 'Handles empty input.', head: 'feature', base: 'main', draft: true}});
    expect(await screen.findByRole('link', {name: 'Open pull request #12'})).toHaveAttribute('href', 'https://example.test/pull/12');
  });

  it('inspects, updates, comments and reviews a pull request by number', async () => {
    const user = userEvent.setup();
    const host = deliveryHost();
    const base = host.handler;
    request.mockImplementation(async (path: string, init?: RequestInit) =>
      path.endsWith('/pulls/7')
        ? {number: 7, title: 'Seven', body: '', url: 'https://example.test/7', state: 'OPEN', mergeable: 'MERGEABLE', headRefName: 'f', baseRefName: 'main', statusCheckRollup: [{name: 'ci', conclusion: 'SUCCESS'}], comments: [], reviews: []}
        : base(path, init),
    );
    render(<ScmView project={project} session={session()} source={source([])} onOpenDiff={vi.fn()} onOpenFile={vi.fn()} onCheckoutChanged={vi.fn()} />);
    await user.click(await screen.findByRole('button', {name: 'Pull request'}));
    await user.type(screen.getByLabelText('Pull request number'), '7');
    await user.click(screen.getByRole('button', {name: 'Inspect PR and checks'}));
    const article = await screen.findByRole('article', {name: 'Pull request #7'});
    expect(article).toHaveTextContent('ci: SUCCESS');
    await user.type(screen.getByLabelText('Review feedback'), 'Looks right');
    await user.click(screen.getByRole('button', {name: 'Post review comment'}));
    expect(await screen.findByRole('dialog', {name: 'Post a review comment on pull request #7'})).toBeInTheDocument();
    expect(host.operations()[0]).toEqual({operation: 'pr-review', arguments: {number: 7, body: 'Looks right', event: 'COMMENT'}});
  });

  it('creates and switches branches and creates a worktree', async () => {
    const user = userEvent.setup();
    const host = deliveryHost();
    const {props} = view(host);
    await user.click(await screen.findByRole('button', {name: 'Branches and worktrees'}));
    await user.type(screen.getByLabelText('Branch name'), 'feature/x');
    await user.click(screen.getByRole('button', {name: 'Create branch'}));
    await waitFor(() => expect(host.operations()).toEqual([{operation: 'branch', arguments: {name: 'feature/x', create: true}}]));
    await user.click(screen.getByRole('button', {name: 'Switch branch'}));
    await waitFor(() => expect(host.operations()[1]).toEqual({operation: 'branch', arguments: {name: 'feature/x', create: false}}));
    await user.click(screen.getByRole('button', {name: 'New worktree'}));
    await waitFor(() => expect(host.operations()[2]).toEqual({operation: 'worktree-create', arguments: {branch: 'feature/x', base: 'main'}}));
    expect(props.onCheckoutChanged).toHaveBeenCalled();
  });

  it('lists worktrees, switches the conversation to one and confirms removing another', async () => {
    const user = userEvent.setup();
    const host = deliveryHost({worktrees: [{id: 'a', path: '/wt/a', branch: 'alpha'}, {id: 'b', path: '/wt/b', branch: 'beta'}]});
    view(host, {session: session({worktree_id: 'a'})});
    await user.click(await screen.findByRole('button', {name: 'Branches and worktrees'}));
    const list = screen.getByRole('list', {name: 'Worktrees'});
    expect(within(list).getByText('Current')).toBeInTheDocument();
    expect(within(list).queryByRole('button', {name: 'Remove worktree alpha'})).not.toBeInTheDocument();
    await user.click(within(list).getByRole('button', {name: 'Use worktree beta'}));
    await waitFor(() => expect(host.calls.some(call => call.method === 'PATCH' && JSON.stringify(call.body).includes('"worktree_id":"b"'))).toBe(true));
    await user.click(within(list).getByRole('button', {name: 'Remove worktree beta'}));
    const dialog = await screen.findByRole('dialog', {name: 'Remove this worktree'});
    expect(host.operations().at(-1)).toEqual({operation: 'worktree-remove', arguments: {worktree_id: 'b'}});
    expect(within(dialog).getByText('Yes. Only the folder is removed.')).toBeInTheDocument();
    await user.click(within(dialog).getByRole('button', {name: 'Remove worktree'}));
    await waitFor(() => expect(host.executed()).toEqual([{confirmed: true}]));
  });

  it('lets the checkout picker choose the project checkout or a worktree', async () => {
    const user = userEvent.setup();
    const host = deliveryHost({worktrees: [{id: 'a', path: '/wt/a', branch: 'alpha'}]});
    view(host);
    const picker = await screen.findByRole('combobox', {name: 'Checkout'});
    await waitFor(() => expect(within(picker).getAllByRole('option')).toHaveLength(2));
    await user.selectOptions(picker, 'a');
    await waitFor(() => expect(host.calls.some(call => call.method === 'PATCH' && JSON.stringify(call.body).includes('"worktree_id":"a"'))).toBe(true));
  });

  it('lists recent operations, lets one that is waiting be reviewed, and never replays one in doubt', async () => {
    const user = userEvent.setup();
    const host = deliveryHost({
      records: [
        {id: 'old', operation: 'push', status: 'prepared', arguments: '{"remote":"origin","branch":"main"}', head: 'abcdef0123', created: 1, result: null},
        {id: 'unsure', operation: 'pr-create', status: 'unknown', arguments: '{}', head: 'abcdef0123', created: 2, result: null},
      ],
    });
    view(host);
    await user.click(await screen.findByRole('button', {name: 'Recent operations'}));
    expect(screen.getByText(/Outcome uncertain/)).toBeInTheDocument();
    await user.click(screen.getByRole('button', {name: 'Review exact operation'}));
    expect(await screen.findByRole('dialog', {name: 'Push main to origin'})).toBeInTheDocument();
    expect(host.executed()).toHaveLength(0);
  });
});

describe('ScmView commands', () => {
  it('stages everything for a command that arrives before the view is shown', async () => {
    const host = deliveryHost();
    requestScmCommand('stageAll');
    view(host);
    await waitFor(() => expect(host.operations()).toEqual([{operation: 'stage', arguments: {paths: ['src/one.ts', 'notes.md', 'old.txt']}}]));
  });

  it('commits for a command, or focuses the box and says why it cannot', async () => {
    const host = deliveryHost();
    view(host, {source: source([file('a.ts', 'M', ' ')])});
    const box = await screen.findByRole('textbox', {name: 'Commit message'});
    requestScmCommand('commit');
    await waitFor(() => expect(box).toHaveFocus());
    expect(host.operations()).toEqual([]);
    expect(screen.getByText('Enter a commit message.')).toBeInTheDocument();
  });

  it('refreshes for a command', async () => {
    const host = deliveryHost();
    const {props} = view(host);
    await screen.findByRole('list', {name: 'Changes'});
    (props.source.refresh as ReturnType<typeof vi.fn>).mockClear();
    requestScmCommand('refresh');
    await waitFor(() => expect(props.source.refresh).toHaveBeenCalled());
  });
});
