import {cleanup, render, screen} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest';
import {expectNoAxeViolations} from '../../test-axe';
import SearchView from './SearchView';

const request = vi.hoisted(() => vi.fn());
const gql = vi.hoisted(() => vi.fn());
vi.mock('../../lib/api', async original => ({...(await original<typeof import('../../lib/api')>()), request, gql}));

afterEach(cleanup);
beforeEach(() => {
  request.mockReset();
  gql.mockReset();
});

const project = {id: 'p1', name: 'Project', path: '/work/project'};
const session = {id: 's1', cwd: '/work/project', worktree_id: 'w1', runner_id: null};

describe('SearchView', () => {
  it('asks the conversation checkout and opens a result at its line', async () => {
    const user = userEvent.setup();
    const onOpenFile = vi.fn();
    request.mockResolvedValue({results: [{path: 'src/api.ts', line: 12, text: 'export function greet()'}, {path: 'README.md'}]});
    const {container} = render(<SearchView project={project} session={session as never} onOpenFile={onOpenFile} onError={vi.fn()} />);
    await user.type(screen.getByRole('textbox', {name: 'Search in files'}), 'greet{Enter}');
    expect(request).toHaveBeenCalledTimes(1);
    const path = String(request.mock.calls[0][0]);
    expect(path).toContain('/api/workspace/sessions/s1/files?');
    expect(path).toContain('operation=search');
    expect(path).toContain('query=greet');
    expect(path).toContain('worktree_id=w1');
    const hit = await screen.findByRole('button', {name: /src\/api\.ts:12/});
    expect(hit).toHaveTextContent('export function greet()');
    await user.click(hit);
    expect(onOpenFile).toHaveBeenCalledWith('src/api.ts', 12);
    await user.click(screen.getByRole('button', {name: 'README.md'}));
    expect(onOpenFile).toHaveBeenLastCalledWith('README.md', undefined);
    await expectNoAxeViolations(container);
  });

  it('falls back to the project search when there is no conversation', async () => {
    const user = userEvent.setup();
    gql.mockResolvedValue({project_search: {results: []}});
    render(<SearchView project={project} session={null} onOpenFile={vi.fn()} onError={vi.fn()} />);
    await user.type(screen.getByRole('textbox', {name: 'Search in files'}), 'nothing{Enter}');
    expect(gql).toHaveBeenCalledWith(expect.stringContaining('project_search'), {id: 'p1', input: {query: 'nothing'}});
    expect(await screen.findByText(/No results for/)).toBeInTheDocument();
  });

  it('reports a failed search and offers no search without a project', async () => {
    const user = userEvent.setup();
    const onError = vi.fn();
    request.mockRejectedValue(new Error('Search is unavailable'));
    const {rerender} = render(<SearchView project={project} session={session as never} onOpenFile={vi.fn()} onError={onError} />);
    await user.type(screen.getByRole('textbox', {name: 'Search in files'}), 'x{Enter}');
    await vi.waitFor(() => expect(onError).toHaveBeenCalledWith(expect.objectContaining({message: 'Search is unavailable'})));
    rerender(<SearchView project={null} session={null} onOpenFile={vi.fn()} onError={onError} />);
    expect(screen.getByText('Open a project to search its files.')).toBeInTheDocument();
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
  });

  it('does not search for an empty query', async () => {
    const user = userEvent.setup();
    render(<SearchView project={project} session={session as never} onOpenFile={vi.fn()} onError={vi.fn()} />);
    expect(screen.getByRole('button', {name: 'Run search'})).toBeDisabled();
    await user.type(screen.getByRole('textbox', {name: 'Search in files'}), '   {Enter}');
    expect(request).not.toHaveBeenCalled();
  });
});
