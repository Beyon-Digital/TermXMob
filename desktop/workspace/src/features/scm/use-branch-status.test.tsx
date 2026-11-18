import {act, cleanup, renderHook, waitFor} from '@testing-library/react';
import {afterEach, describe, expect, it, vi} from 'vitest';
import {project} from '../../test-utils/scm';
import {notifyScmChanged} from './scm-bus';
import {useBranchStatus} from './use-branch-status';

const gql = vi.hoisted(() => vi.fn());
vi.mock('../../lib/api', async original => ({...(await original<typeof import('../../lib/api')>()), gql}));

afterEach(() => {
  cleanup();
  gql.mockReset();
});

const row = (extra: Record<string, unknown> = {}) => ({git_status: {repo: true, branch: 'main', ahead: 2, behind: 1, files: [{path: 'a.ts', staged: false, index: ' ', worktree: 'M'}], ...extra}});

describe('useBranchStatus', () => {
  it('reports the branch, whether it is dirty, and how far it is ahead of and behind its upstream', async () => {
    gql.mockResolvedValue(row());
    const {result} = renderHook(() => useBranchStatus(project, true));
    await waitFor(() => expect(result.current).not.toBeNull());
    expect(result.current).toMatchObject({branch: 'main', dirty: true, ahead: 2, behind: 1});
    expect(gql).toHaveBeenCalledWith(expect.stringContaining('ahead behind'), {id: 'project'});
  });

  it('is clean with no files, and counts a missing upstream as zero', async () => {
    gql.mockResolvedValue(row({files: [], ahead: null, behind: null}));
    const {result} = renderHook(() => useBranchStatus(project, true));
    await waitFor(() => expect(result.current).not.toBeNull());
    expect(result.current).toMatchObject({dirty: false, ahead: 0, behind: 0});
  });

  it('reads again at once when source control reports a change', async () => {
    gql.mockResolvedValue(row());
    const {result} = renderHook(() => useBranchStatus(project, true));
    await waitFor(() => expect(result.current?.dirty).toBe(true));
    gql.mockResolvedValue(row({files: []}));
    act(() => notifyScmChanged());
    await waitFor(() => expect(result.current?.dirty).toBe(false));
  });

  it('reads nothing while disabled or without a project, and keeps the last value on a failed read', async () => {
    const {result, rerender} = renderHook(({enabled, current}) => useBranchStatus(current, enabled), {initialProps: {enabled: false, current: project as typeof project | null}});
    expect(result.current).toBeNull();
    expect(gql).not.toHaveBeenCalled();
    gql.mockResolvedValueOnce(row());
    rerender({enabled: true, current: project});
    await waitFor(() => expect(result.current?.branch).toBe('main'));
    gql.mockRejectedValue(new Error('offline'));
    act(() => notifyScmChanged());
    await waitFor(() => expect(gql.mock.calls.length).toBeGreaterThan(1));
    expect(result.current?.branch).toBe('main');
    rerender({enabled: true, current: null});
    expect(result.current).toBeNull();
  });
});
