import {cleanup, render, screen, waitFor, within} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {useState} from 'react';
import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest';
import {expectNoAxeViolations} from '../../test-axe';
import ExplorerTree from './ExplorerTree';
import ExplorerView, {gitLetter} from './ExplorerView';
import type {TreeRow} from './use-file-tree';

const request = vi.hoisted(() => vi.fn());
const gql = vi.hoisted(() => vi.fn());
vi.mock('../../lib/api', async original => ({...(await original<typeof import('../../lib/api')>()), request, gql}));

afterEach(cleanup);

const entry = (path: string, directory: boolean) => ({path, name: path.split('/').at(-1)!, directory});

/** The tree as the hook flattens it: src (open or not) with two files, then a README at the top level. */
function rowsFor(open: boolean): TreeRow[] {
  return [
    {entry: entry('src', true), depth: 0, directory: true, expanded: open, name: 'src'},
    ...(open
      ? [
          {entry: entry('src/api.ts', false), depth: 1, directory: false, expanded: false, name: 'api.ts'},
          {entry: entry('src/main.ts', false), depth: 1, directory: false, expanded: false, name: 'main.ts'},
        ]
      : []),
    {entry: entry('README.md', false), depth: 0, directory: false, expanded: false, name: 'README.md'},
  ];
}

function Host({onOpen = vi.fn(), activePath}: {onOpen?: (path: string) => void; activePath?: string}) {
  const [open, setOpen] = useState(true);
  return <ExplorerTree rows={rowsFor(open)} activePath={activePath} decorations={{'src/api.ts': 'M'}} onToggle={() => setOpen(value => !value)} onOpen={onOpen} />;
}

describe('ExplorerTree', () => {
  it('is an ARIA tree with levels, expansion and the selected file', async () => {
    const {container} = render(<Host activePath="src/main.ts" />);
    const tree = screen.getByRole('tree', {name: 'Files'});
    const items = within(tree).getAllByRole('treeitem');
    expect(items.map(item => item.getAttribute('aria-level'))).toEqual(['1', '2', '2', '1']);
    expect(items[0]).toHaveAttribute('aria-expanded', 'true');
    expect(items[1]).not.toHaveAttribute('aria-expanded');
    expect(items[2]).toHaveAttribute('aria-selected', 'true');
    expect(items[1]).toHaveAttribute('aria-selected', 'false');
    // One tab stop for the whole tree.
    expect(items.filter(item => item.tabIndex === 0)).toHaveLength(1);
    await expectNoAxeViolations(container);
  });

  it('says what changed in words as well as a letter', () => {
    render(<Host />);
    const row = screen.getByRole('treeitem', {name: /api\.ts/});
    expect(row).toHaveTextContent('M');
    expect(row).toHaveTextContent('modified');
  });

  it('moves with the arrows, steps in and out of folders, and jumps with Home and End', async () => {
    const user = userEvent.setup();
    render(<Host />);
    const names = () => screen.getAllByRole('treeitem').map(item => item.textContent);
    expect(names()[0]).toContain('src');
    screen.getAllByRole('treeitem')[0].focus();
    await user.keyboard('{ArrowDown}');
    await waitFor(() => expect(screen.getAllByRole('treeitem')[1]).toHaveFocus());
    await user.keyboard('{ArrowLeft}');
    await waitFor(() => expect(screen.getAllByRole('treeitem')[0]).toHaveFocus());
    await user.keyboard('{ArrowRight}');
    await waitFor(() => expect(screen.getAllByRole('treeitem')[1]).toHaveFocus());
    await user.keyboard('{End}');
    await waitFor(() => expect(screen.getAllByRole('treeitem').at(-1)).toHaveFocus());
    await user.keyboard('{Home}');
    await waitFor(() => expect(screen.getAllByRole('treeitem')[0]).toHaveFocus());
  });

  it('opens a file with Enter and a click, and toggles a folder', async () => {
    const user = userEvent.setup();
    const onOpen = vi.fn();
    render(<Host onOpen={onOpen} />);
    screen.getByRole('treeitem', {name: /main\.ts/}).focus();
    await user.keyboard('{Enter}');
    expect(onOpen).toHaveBeenCalledWith('src/main.ts');
    await user.click(screen.getByRole('treeitem', {name: /README/}));
    expect(onOpen).toHaveBeenLastCalledWith('README.md');
    await user.click(screen.getByRole('treeitem', {name: /^src/}));
    expect(screen.queryByRole('treeitem', {name: /api\.ts/})).not.toBeInTheDocument();
  });
});

describe('ExplorerView', () => {
  beforeEach(() => {
    request.mockReset();
    gql.mockReset();
  });

  const project = {id: 'p1', name: 'Project', path: '/work/project'};
  const base = {
    project,
    projects: [project],
    session: null,
    git: null,
    onOpenFile: vi.fn(),
    onOpenProject: vi.fn(),
    onAddProject: vi.fn(),
    onCheckoutChanged: vi.fn(),
    onError: vi.fn(),
  };

  it('offers one action when no project is open', async () => {
    const user = userEvent.setup();
    const onAddProject = vi.fn();
    render(<ExplorerView {...base} project={null} onAddProject={onAddProject} />);
    expect(screen.getByText('No project is open.')).toBeInTheDocument();
    await user.click(screen.getByRole('button', {name: 'Add project'}));
    expect(onAddProject).toHaveBeenCalled();
  });

  it('lists the project root, opens a folder on demand and opens a file as a path', async () => {
    const user = userEvent.setup();
    const onOpenFile = vi.fn();
    gql.mockImplementation(async (_query: string, variables: {path: string}) => ({
      project_tree: {entries: variables.path === 'src' ? [entry('src/app.py', false)] : [entry('src', true), entry('setup.py', false)]},
    }));
    const {container} = render(<ExplorerView {...base} onOpenFile={onOpenFile} git={{repo: true, branch: 'main', files: [{path: 'setup.py', staged: false, index: ' ', worktree: 'M'}]}} />);
    const root = await screen.findByRole('tree', {name: 'Files'});
    expect(within(root).getAllByRole('treeitem').map(item => item.getAttribute('data-path'))).toEqual(['src', 'setup.py']);
    expect(screen.getByRole('treeitem', {name: /setup\.py/})).toHaveTextContent('modified');
    await user.click(screen.getByRole('treeitem', {name: /^src/}));
    await user.click(await screen.findByRole('treeitem', {name: /app\.py/}));
    expect(onOpenFile).toHaveBeenCalledWith('src/app.py');
    await expectNoAxeViolations(container);
  });

  it('maps git state to one letter', () => {
    expect(gitLetter({index: ' ', worktree: 'M'})).toBe('M');
    expect(gitLetter({index: 'A', worktree: ' '})).toBe('A');
    expect(gitLetter({index: '?', worktree: '?'})).toBe('U');
    expect(gitLetter({index: 'D', worktree: ' '})).toBe('D');
  });
});
