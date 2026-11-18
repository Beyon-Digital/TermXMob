import {cleanup, render, screen, within} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {afterEach, describe, expect, it, vi} from 'vitest';
import ContentLayer from '../../components/shell/ContentLayer';
import type {Tab} from '../../lib/layout/model';
import type {Placement} from '../../lib/layout/rects';
import {tabTitle} from '../../shell/tab-titles';
import {project, session} from '../../test-utils/scm';
import DiffTab from './DiffTab';

const request = vi.hoisted(() => vi.fn());
vi.mock('../../lib/api', async original => ({...(await original<typeof import('../../lib/api')>()), request}));

afterEach(() => {
  cleanup();
  request.mockReset();
  localStorage.clear();
});

const PATCH = 'diff --git a/a.ts b/a.ts\n--- a/a.ts\n+++ b/a.ts\n@@ -2,1 +2,1 @@\n-one\n+two\n';

describe('a diff tab in the content layer', () => {
  it('stays mounted, with its state, when the tab moves to another group', async () => {
    const user = userEvent.setup();
    request.mockImplementation(async (path: string) => {
      if (path.includes('/delivery/diff?')) return {diff: PATCH};
      if (path.includes('/files?')) return {content: 'zero\none\nthree\nfour\n'};
      throw new Error(path);
    });
    const tab: Tab = {id: 't-diff', kind: 'diff', ref: 'worktree:a.ts'};
    const place = (group: string, x: number): Placement[] => [{tab, group, rect: {x, y: 0, w: 600, h: 400}}];
    const render_ = (placements: Placement[]) => (
      <ContentLayer
        placements={placements}
        onFocusGroup={() => {}}
        render={(item, {visible}) => <DiffTab tabRef={item.ref} visible={visible} project={project} session={session()} onOpenFile={() => {}} onRetarget={() => {}} onClose={() => {}} />}
      />
    );
    const view = render(render_(place('g1', 0)));
    const region = await screen.findByRole('region', {name: 'Inline diff for a.ts'});
    await user.click(screen.getByRole('button', {name: 'Side by side'}));
    const split = screen.getByRole('region', {name: 'Side by side diff for a.ts'});
    await user.click(within(split).getByRole('button', {name: 'Show 1 unchanged line'}));
    expect(within(split).getAllByText('zero')).toHaveLength(2);
    const reads = request.mock.calls.filter(call => String(call[0]).includes('/delivery/diff?')).length;

    view.rerender(render_(place('g2', 600)));
    const moved = screen.getByRole('region', {name: 'Side by side diff for a.ts'});
    expect(moved).toBe(split);
    expect(region).toBeDefined();
    expect(within(moved).getAllByText('zero')).toHaveLength(2);
    expect(request.mock.calls.filter(call => String(call[0]).includes('/delivery/diff?')).length).toBe(reads);
    expect(document.querySelector('[data-tab="t-diff"]')).toHaveStyle({left: '600px'});

    // Hidden behind another tab it is still there, and inert.
    view.rerender(render_([{tab, group: 'g2', rect: null}]));
    const hidden = document.querySelector('[data-tab="t-diff"]')!;
    expect(hidden).toHaveAttribute('hidden');
    expect(hidden).toHaveAttribute('inert');
    expect(hidden.contains(split)).toBe(true);
  });
});

describe('diff tab titles', () => {
  const facts = {tabs: [], primaryId: '', selected: '', sessionTitle: undefined, file: () => undefined};

  it('names the tab for its file and marks the staged side', () => {
    expect(tabTitle({id: 'a', kind: 'diff', ref: 'worktree:src/a.ts'}, facts)).toBe('a.ts');
    expect(tabTitle({id: 'a', kind: 'diff', ref: 'staged:src/a.ts'}, facts)).toBe('a.ts (staged)');
    expect(tabTitle({id: 'a', kind: 'diff'}, facts)).toBe('Changes');
  });
});
