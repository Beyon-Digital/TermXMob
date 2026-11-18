import {act, cleanup, render, screen, waitFor, within} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest';
import {expectNoAxeViolations} from '../../test-axe';
import {project, session} from '../../test-utils/scm';
import DiffTab from './DiffTab';
import {notifyScmChanged, requestChangeStep} from './scm-bus';

const request = vi.hoisted(() => vi.fn());
vi.mock('../../lib/api', async original => ({...(await original<typeof import('../../lib/api')>()), request}));

const HEADER = 'diff --git a/src/a.ts b/src/a.ts\nindex 111..222 100644\n--- a/src/a.ts\n+++ b/src/a.ts\n';
const HUNK_ONE = '@@ -2,3 +2,3 @@ function one()\n keep one\n-old two\n+new two\n keep three\n';
const HUNK_TWO = '@@ -20,2 +20,3 @@ function two()\n keep twenty\n+added twenty-one\n keep twenty-two\n';
const HUNK_THREE = '@@ -40,3 +40,2 @@\n keep forty\n-removed forty-one\n keep forty-two\n';
const PATCH = HEADER + HUNK_ONE + HUNK_TWO + HUNK_THREE;
const TEXT = Array.from({length: 100}, (_, index) => 'line ' + (index + 1)).join('\n') + '\n';

type Reply = {diff?: string; review?: {diff: string; binary?: boolean; truncated?: boolean}; text?: string; ops?: string[]};

function host(reply: Reply = {}) {
  const calls: {path: string; init?: RequestInit}[] = [];
  request.mockImplementation(async (path: string, init?: RequestInit) => {
    calls.push({path, init});
    if (path.includes('/delivery/diff?')) return {path: 'x', staged: false, diff: reply.diff ?? PATCH};
    if (path.includes('/changes/diff?')) return {path: 'x', diff: reply.review?.diff ?? '', binary: !!reply.review?.binary, truncated: !!reply.review?.truncated, additions: 1, deletions: 0};
    if (path.includes('/files?')) return {path: 'src/a.ts', content: reply.text ?? TEXT, revision: 'r', editable: true};
    if (path.endsWith('/prepare')) return {id: 'a1', operation: JSON.parse(String(init?.body)).operation, arguments: JSON.parse(String(init?.body)).arguments, head: 'abc', requires_confirmation: false};
    if (path.endsWith('/execute')) return {status: 'completed', result: {}};
    throw new Error('Unexpected request ' + path);
  });
  return {
    calls,
    prepared: () => calls.filter(call => call.path.endsWith('/prepare')).map(call => JSON.parse(String(call.init?.body)) as {operation: string; arguments: Record<string, unknown>}),
  };
}

function tab(extra: Partial<React.ComponentProps<typeof DiffTab>> = {}) {
  const props = {
    tabRef: 'worktree:src/a.ts',
    visible: true,
    project,
    session: session(),
    onOpenFile: vi.fn(),
    onRetarget: vi.fn(),
    onClose: vi.fn(),
    ...extra,
  };
  return {props, ...render(<DiffTab {...props} />)};
}

beforeEach(() => localStorage.clear());
afterEach(() => {
  cleanup();
  request.mockReset();
  vi.unstubAllGlobals();
});

describe('DiffTab inline', () => {
  it('shows the patch with both line numbers, a sign and words for added and removed lines', async () => {
    host();
    const {container} = tab();
    const region = await screen.findByRole('region', {name: 'Inline diff for src/a.ts'});
    expect(within(region).getByText('new two')).toBeInTheDocument();
    const added = within(region).getByText('new two').closest('.diff-row')!;
    expect(added).toHaveClass('diff-addition');
    expect(added.querySelector('.visually-hidden')).toHaveTextContent('Added');
    expect(added.querySelectorAll('.diff-num')[1]).toHaveTextContent('3');
    const removed = within(region).getByText('old two').closest('.diff-row')!;
    expect(removed).toHaveClass('diff-deletion');
    expect(removed.querySelectorAll('.diff-num')[0]).toHaveTextContent('3');
    expect(removed.querySelector('.diff-sign')).toHaveTextContent('−');
    expect(screen.getByText('+2')).toBeInTheDocument();
    expect(screen.getByText('−2')).toBeInTheDocument();
    expect(screen.getByText('Changes')).toBeInTheDocument();
    expect(screen.getByText('function one()')).toBeInTheDocument();
    await expectNoAxeViolations(container);
  });

  it('reads the unstaged side from the index and the staged side from HEAD', async () => {
    const {calls} = host();
    tab({tabRef: 'staged:src/a.ts'});
    await screen.findByRole('region', {name: 'Inline diff for src/a.ts'});
    expect(calls[0].path).toContain('staged=true');
    expect(screen.getByText('Staged')).toBeInTheDocument();
    expect(calls.some(call => call.path.includes('/files?'))).toBe(false);
  });
});

describe('DiffTab side by side', () => {
  it('pairs removed and added lines in two columns, and remembers the choice on this device', async () => {
    const user = userEvent.setup();
    host();
    const first = tab();
    await screen.findByRole('region', {name: 'Inline diff for src/a.ts'});
    expect(screen.getByRole('button', {name: 'Inline'})).toHaveAttribute('aria-pressed', 'true');
    await user.click(screen.getByRole('button', {name: 'Side by side'}));
    const region = screen.getByRole('region', {name: 'Side by side diff for src/a.ts'});
    const rows = region.querySelectorAll('.diff-split-row');
    const pair = Array.from(rows).find(row => row.textContent?.includes('old two'))!;
    expect(pair).toHaveTextContent('new two');
    expect(pair.querySelector('.diff-deletion')).not.toBeNull();
    expect(pair.querySelector('.diff-addition')).not.toBeNull();
    expect(localStorage.getItem('termx-diff-mode')).toBe('split');
    await expectNoAxeViolations(first.container);
    first.unmount();
    tab();
    expect(await screen.findByRole('region', {name: 'Side by side diff for src/a.ts'})).toBeInTheDocument();
    expect(screen.getByRole('button', {name: 'Side by side'})).toHaveAttribute('aria-pressed', 'true');
  });

  it('falls back to inline, and says why, when the tab is too narrow for two columns', async () => {
    localStorage.setItem('termx-diff-mode', 'split');
    let notify: (width: number) => void = () => {};
    vi.stubGlobal(
      'ResizeObserver',
      class {
        constructor(callback: ResizeObserverCallback) {
          notify = width => callback([{contentRect: {width}} as ResizeObserverEntry], this as unknown as ResizeObserver);
        }
        observe() {}
        disconnect() {}
      },
    );
    host();
    tab();
    await screen.findByRole('region', {name: 'Side by side diff for src/a.ts'});
    act(() => notify(400));
    expect(screen.getByRole('region', {name: 'Inline diff for src/a.ts'})).toBeInTheDocument();
    const wide = screen.getByRole('button', {name: 'Side by side'});
    expect(wide).toBeDisabled();
    expect(wide).toHaveAttribute('title', 'Side by side needs a wider tab');
    act(() => notify(900));
    expect(screen.getByRole('region', {name: 'Side by side diff for src/a.ts'})).toBeInTheDocument();
  });
});

describe('DiffTab change navigation', () => {
  it('moves between changes with F7 and Shift+F7, wrapping, and the toolbar buttons', async () => {
    const user = userEvent.setup();
    host();
    tab();
    const region = await screen.findByRole('region', {name: 'Inline diff for src/a.ts'});
    const hunks = () => Array.from(region.querySelectorAll<HTMLElement>('[data-hunk]'));
    expect(hunks().map(node => node.getAttribute('aria-label'))).toEqual(['Change 1 of 3, lines 2 to 4', 'Change 2 of 3, lines 20 to 22', 'Change 3 of 3, lines 40 to 41']);
    region.focus();
    await user.keyboard('{F7}');
    expect(hunks()[0]).toHaveFocus();
    await user.keyboard('{F7}');
    expect(hunks()[1]).toHaveFocus();
    await user.keyboard('{Shift>}{F7}{/Shift}');
    expect(hunks()[0]).toHaveFocus();
    await user.keyboard('{Shift>}{F7}{/Shift}');
    expect(hunks()[2]).toHaveFocus();
    await user.keyboard('{F7}');
    expect(hunks()[0]).toHaveFocus();
    await user.click(screen.getByRole('button', {name: 'Next change'}));
    expect(hunks()[1]).toHaveFocus();
    await user.click(screen.getByRole('button', {name: 'Previous change'}));
    expect(hunks()[0]).toHaveFocus();
  });

  it('answers the Go to next change command from anywhere, in one visible tab only', async () => {
    host();
    const second = document.createElement('div');
    document.body.append(second);
    tab();
    const region = await screen.findByRole('region', {name: 'Inline diff for src/a.ts'});
    act(() => requestChangeStep('next'));
    expect(region.querySelector('[data-hunk="0"]')).toHaveFocus();
    act(() => requestChangeStep('previous'));
    expect(region.querySelector('[data-hunk="2"]')).toHaveFocus();
    second.remove();
  });

  it('ignores the command in a tab that is not shown', async () => {
    host();
    tab({visible: false});
    act(() => requestChangeStep('next'));
    expect(document.activeElement).toBe(document.body);
  });
});

describe('DiffTab hunk and file actions', () => {
  it('stages one hunk as a patch of its own and reloads', async () => {
    const user = userEvent.setup();
    const {prepared, calls} = host();
    tab();
    const region = await screen.findByRole('region', {name: 'Inline diff for src/a.ts'});
    const second = within(within(region).getByRole('group', {name: /Change 2 of 3/})).getByRole('button', {name: 'Stage hunk'});
    const before = calls.filter(call => call.path.includes('/delivery/diff?')).length;
    await user.click(second);
    await waitFor(() => expect(prepared()).toHaveLength(1));
    expect(prepared()[0]).toEqual({operation: 'hunk', arguments: {patch: HEADER + HUNK_TWO, stage: true}});
    await waitFor(() => expect(calls.filter(call => call.path.includes('/delivery/diff?')).length).toBeGreaterThan(before));
  });

  it('unstages a hunk from the staged side', async () => {
    const user = userEvent.setup();
    const {prepared} = host();
    tab({tabRef: 'staged:src/a.ts'});
    const region = await screen.findByRole('region', {name: 'Inline diff for src/a.ts'});
    await user.click(within(within(region).getByRole('group', {name: /Change 1 of 3/})).getByRole('button', {name: 'Unstage hunk'}));
    await waitFor(() => expect(prepared()).toHaveLength(1));
    expect(prepared()[0]).toEqual({operation: 'hunk', arguments: {patch: HEADER + HUNK_ONE, stage: false}});
  });

  it('stages and unstages the whole file from the bar, and reports a failure inline', async () => {
    const user = userEvent.setup();
    const {prepared} = host();
    tab();
    await user.click(await screen.findByRole('button', {name: 'Stage file'}));
    await waitFor(() => expect(prepared()).toEqual([{operation: 'stage', arguments: {paths: ['src/a.ts']}}]));
    request.mockImplementation(async (path: string) => {
      if (path.includes('/delivery/diff?')) return {diff: PATCH};
      if (path.includes('/files?')) return {content: TEXT};
      throw new Error('The index is locked');
    });
    await user.click(screen.getByRole('button', {name: 'Stage file'}));
    expect(await screen.findByRole('alert')).toHaveTextContent('Change failed: The index is locked.');
  });

  it('refreshes when source control reports a change, and opens the file in the editor', async () => {
    const user = userEvent.setup();
    const {calls} = host();
    const {props} = tab();
    await screen.findByRole('region', {name: 'Inline diff for src/a.ts'});
    const reads = () => calls.filter(call => call.path.includes('/delivery/diff?')).length;
    const first = reads();
    act(() => notifyScmChanged());
    await waitFor(() => expect(reads()).toBe(first + 1));
    await user.click(screen.getByRole('button', {name: 'Open file in editor'}));
    expect(props.onOpenFile).toHaveBeenCalledWith('src/a.ts');
    await user.click(screen.getByRole('button', {name: 'Refresh diff'}));
    await waitFor(() => expect(reads()).toBe(first + 2));
  });
});

describe('DiffTab expand context', () => {
  const NEW = HEADER + '@@ -1,0 +2,1 @@\n+inserted\n';

  it('reveals unchanged lines a step at a time with their old and new numbers', async () => {
    const user = userEvent.setup();
    host();
    tab();
    const region = await screen.findByRole('region', {name: 'Inline diff for src/a.ts'});
    await user.click(await within(region).findByRole('button', {name: 'Show 1 unchanged line'}));
    const top = within(region).getByText('line 1').closest('.diff-row')!;
    expect(Array.from(top.querySelectorAll('.diff-num')).map(node => node.textContent)).toEqual(['1', '1']);
    await user.click(within(region).getByRole('button', {name: 'Show 15 unchanged lines'}));
    expect(within(region).getByText('line 19')).toBeInTheDocument();
    expect(within(region).getByText('line 5')).toBeInTheDocument();
    // After the second hunk the old numbers sit one below the new ones.
    await user.click(within(region).getByRole('button', {name: 'Show 17 unchanged lines'}));
    const after = within(region).getByText('line 23').closest('.diff-row')!;
    expect(Array.from(after.querySelectorAll('.diff-num')).map(node => node.textContent)).toEqual(['22', '23']);
    const tail = within(region).getByRole('button', {name: 'Show 20 more of 59 unchanged lines'});
    await user.click(tail);
    expect(within(region).getByText('line 42')).toBeInTheDocument();
    expect(within(region).queryByText('line 62')).not.toBeInTheDocument();
    expect(within(region).getByRole('button', {name: 'Show 20 more of 39 unchanged lines'})).toBeInTheDocument();
  });

  it('is not offered on the staged side, where the file text is not the index', async () => {
    host();
    tab({tabRef: 'staged:src/a.ts'});
    const region = await screen.findByRole('region', {name: 'Inline diff for src/a.ts'});
    expect(within(region).queryByRole('button', {name: /unchanged/})).not.toBeInTheDocument();
  });

  it('is not offered when the text cannot be read', async () => {
    host();
    const base = request.getMockImplementation()!;
    request.mockImplementation(async (path: string, init?: RequestInit) => {
      if (path.includes('/files?')) throw new Error('unreadable');
      return base(path, init);
    });
    tab();
    const region = await screen.findByRole('region', {name: 'Inline diff for src/a.ts'});
    expect(within(region).queryByRole('button', {name: /unchanged/})).not.toBeInTheDocument();
  });

  it('shows a new file in full with no hunk buttons when it is not tracked yet', async () => {
    host({diff: '', review: {diff: NEW.replace('src/a.ts', 'src/a.ts')}});
    tab();
    const region = await screen.findByRole('region', {name: 'Inline diff for src/a.ts'});
    expect(within(region).getByText('inserted')).toBeInTheDocument();
    expect(within(region).queryByRole('button', {name: /hunk/})).not.toBeInTheDocument();
    expect(within(region).queryByRole('button', {name: /unchanged/})).not.toBeInTheDocument();
  });
});

describe('DiffTab placeholders', () => {
  it('says a binary file changed, and offers no text actions', async () => {
    host({diff: 'diff --git a/logo.png b/logo.png\nBinary files a/logo.png and b/logo.png differ\n'});
    const {container} = tab({tabRef: 'worktree:logo.png'});
    expect(await screen.findByText('Binary file changed. A text preview is not available.')).toBeInTheDocument();
    expect(screen.queryByRole('button', {name: 'Open file in editor'})).not.toBeInTheDocument();
    expect(screen.queryByRole('button', {name: 'Stage file'})).not.toBeInTheDocument();
    await expectNoAxeViolations(container);
  });

  it('does not draw a patch that is too large, and opens the file instead', async () => {
    const user = userEvent.setup();
    host({diff: HEADER + '@@ -1 +1 @@\n-a\n+' + 'b'.repeat(1_100_000) + '\n'});
    const {props} = tab();
    expect(await screen.findByText('This diff is too large to show here.')).toBeInTheDocument();
    await user.click(screen.getByRole('button', {name: 'Open file in editor'}));
    expect(props.onOpenFile).toHaveBeenCalledWith('src/a.ts');
  });

  it('says when the host truncated the preview of a new file', async () => {
    host({diff: '', review: {diff: HEADER + '@@ -0,0 +1 @@\n+x\n', truncated: true}});
    tab();
    expect(await screen.findByText('This diff is too large to show here.')).toBeInTheDocument();
  });

  it('says nothing is staged, or nothing is unstaged, and offers to close the tab', async () => {
    const user = userEvent.setup();
    host({diff: ''});
    const {props, unmount} = tab({tabRef: 'staged:src/a.ts'});
    expect(await screen.findByText('Nothing is staged for this file.')).toBeInTheDocument();
    await user.click(screen.getByRole('button', {name: 'Close tab'}));
    expect(props.onClose).toHaveBeenCalled();
    unmount();
    tab();
    expect(await screen.findByText('No unstaged changes in this file.')).toBeInTheDocument();
  });

  it('shows a read failure with a way to retry', async () => {
    const user = userEvent.setup();
    host();
    const base = request.getMockImplementation()!;
    let failed = false;
    request.mockImplementation(async (path: string, init?: RequestInit) => {
      if (!failed && path.includes('/delivery/diff?')) {
        failed = true;
        throw new Error('Not a repository');
      }
      return base(path, init);
    });
    tab();
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('Could not read this diff: Not a repository.');
    await user.click(within(alert).getByRole('button', {name: 'Try again'}));
    expect(await screen.findByRole('region', {name: 'Inline diff for src/a.ts'})).toBeInTheDocument();
  });

  it('draws a long diff a step at a time', async () => {
    const user = userEvent.setup();
    const many = Array.from({length: 40}, (_, index) => '@@ -' + (index * 300 + 1) + ',1 +' + (index * 300 + 1) + ',1 @@\n-' + 'x'.repeat(10) + '\n+' + 'y'.repeat(10) + '\n').join('');
    host({diff: HEADER + many, text: ''});
    tab();
    const region = await screen.findByRole('region', {name: 'Inline diff for src/a.ts'});
    expect(region.querySelectorAll('[data-hunk]').length).toBe(40);
    cleanup();
    const huge = Array.from({length: 4}, (_, index) => '@@ -' + (index * 5000 + 1) + ',2500 +' + (index * 5000 + 1) + ',2500 @@\n' + Array.from({length: 2500}, () => '+z').join('\n') + '\n').join('');
    host({diff: HEADER + huge, text: ''});
    tab();
    const big = await screen.findByRole('region', {name: 'Inline diff for src/a.ts'});
    expect(big.querySelectorAll('[data-hunk]').length).toBe(2);
    await user.click(within(big).getByRole('button', {name: 'Show 2 more changes'}));
    expect(big.querySelectorAll('[data-hunk]').length).toBe(4);
  });
});

describe('DiffTab without a file', () => {
  it('takes the first changed file, preferring one with unstaged edits', async () => {
    request.mockResolvedValue({files: [{path: 'a.ts', index: 'M', worktree: ' ', untracked: false, reviewable: true}, {path: 'b.ts', index: ' ', worktree: 'M', untracked: false, reviewable: true}]});
    const {props} = tab({tabRef: undefined});
    await waitFor(() => expect(props.onRetarget).toHaveBeenCalledWith('staged:a.ts'));
  });

  it('says the checkout is clean when nothing changed', async () => {
    request.mockResolvedValue({files: []});
    tab({tabRef: undefined});
    expect(await screen.findByText('No changes. The working tree matches the last commit.')).toBeInTheDocument();
  });

  it('asks for a project conversation when there is none', () => {
    tab({tabRef: undefined, session: null});
    expect(screen.getByText('Open a project conversation to review its changes.')).toBeInTheDocument();
    expect(request).not.toHaveBeenCalled();
  });
});
