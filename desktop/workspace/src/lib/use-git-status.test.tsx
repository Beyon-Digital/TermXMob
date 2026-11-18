import {cleanup, render, screen, waitFor} from '@testing-library/react';
import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest';
import {useGitStatus} from './use-git-status';

const gql = vi.hoisted(() => vi.fn());
vi.mock('./api', async original => ({...(await original<typeof import('./api')>()), gql}));

beforeEach(() => {
  gql.mockReset();
});
afterEach(cleanup);

const project = {id: 'p1', name: 'Project', path: '/work'};

function Probe({enabled = true, current = project}: {enabled?: boolean; current?: typeof project | null}) {
  const status = useGitStatus(current, enabled);
  return <output aria-label="git">{status ? status.branch + ':' + status.files.length : 'none'}</output>;
}

describe('useGitStatus', () => {
  it('reads the branch and changed files of the project', async () => {
    gql.mockResolvedValue({git_status: {repo: true, branch: 'main', files: [{path: 'a', staged: false, index: ' ', worktree: 'M'}]}});
    render(<Probe />);
    await waitFor(() => expect(screen.getByLabelText('git')).toHaveTextContent('main:1'));
    expect(gql).toHaveBeenCalledWith(expect.stringContaining('git_status'), {id: 'p1'});
  });

  it('asks nothing while disabled or without a project, and shows nothing for a failed read', async () => {
    const {rerender} = render(<Probe enabled={false} />);
    rerender(<Probe current={null} />);
    expect(gql).not.toHaveBeenCalled();
    gql.mockRejectedValue(new Error('not a repository'));
    rerender(<Probe />);
    await waitFor(() => expect(gql).toHaveBeenCalled());
    expect(screen.getByLabelText('git')).toHaveTextContent('none');
  });
});
