import {cleanup, render, screen, waitFor, within} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest';
import {defaults} from '../../lib/keybindings';
import {expectNoAxeViolations} from '../../test-axe';
import {findCommand} from '../commands';
import DiscardFilesDialog from './DiscardFilesDialog';
import QuickOpenDialog from './QuickOpenDialog';

const request = vi.hoisted(() => vi.fn());
vi.mock('../../lib/api', async original => ({...(await original<typeof import('../../lib/api')>()), request}));

beforeEach(() => {
  request.mockReset();
});
afterEach(cleanup);

const target = {project: {id: 'p1', name: 'Project', path: '/work'}, session: {id: 's1', cwd: '/work', worktree_id: null, runner_id: null} as never};
const context = {workspace: 'agent' as const, hostAdmin: false, engine: 'tree' as const};

function dialog(extra: Partial<React.ComponentProps<typeof QuickOpenDialog>> = {}) {
  const handlers = {onOpenChange: vi.fn(), onRun: vi.fn(), onOpenFile: vi.fn()};
  const view = render(<QuickOpenDialog open commandsFirst={false} target={target} context={context} bindings={defaults} {...handlers} {...extra} />);
  return {...handlers, ...view};
}

describe('QuickOpenDialog', () => {
  it('finds files in this checkout by name and opens the chosen one', async () => {
    const user = userEvent.setup();
    request.mockResolvedValue({results: [{path: 'src/api.ts'}, {path: 'README.md'}], truncated: false});
    const {onOpenFile, onOpenChange} = dialog();
    const input = screen.getByRole('combobox', {name: 'Search files and commands'});
    await user.type(input, 'api');
    const options = await screen.findAllByRole('option', {name: /api\.ts/});
    expect(options[0]).toHaveTextContent('Files');
    const path = String(request.mock.calls.at(-1)![0]);
    expect(path).toContain('operation=quick-open');
    expect(path).toContain('query=api');
    await user.click(options[0]);
    expect(onOpenFile).toHaveBeenCalledWith('src/api.ts');
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it('chooses with the arrow keys and Enter, and lists a few matching commands beside the files', async () => {
    const user = userEvent.setup();
    request.mockResolvedValue({results: [{path: 'split.md'}], truncated: false});
    const {onOpenFile} = dialog();
    await user.type(screen.getByRole('combobox'), 'split');
    await screen.findByRole('option', {name: /split\.md/});
    expect(screen.getByRole('option', {name: /Split group right/})).toBeInTheDocument();
    const input = screen.getByRole('combobox');
    expect(input).toHaveAttribute('aria-activedescendant', expect.stringMatching(/-0$/));
    await user.keyboard('{Enter}');
    expect(onOpenFile).toHaveBeenCalledWith('split.md');
  });

  it('lists only commands after a > and runs the one chosen', async () => {
    const user = userEvent.setup();
    const {onRun} = dialog({commandsFirst: true});
    const input = screen.getByRole('combobox');
    expect(input).toHaveValue('>');
    expect(request).not.toHaveBeenCalled();
    await user.type(input, 'lock');
    await user.click(await screen.findByRole('option', {name: /Lock workspace/}));
    expect(onRun).toHaveBeenCalledWith(findCommand('lock'));
  });

  it('says why files cannot be listed, and still offers commands', async () => {
    const user = userEvent.setup();
    request.mockImplementation(async () => {
      throw new Error('Quick open targets local checkouts.');
    });
    dialog();
    await user.type(screen.getByRole('combobox'), 'lock');
    expect(await screen.findByRole('option', {name: /Lock workspace/})).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole('listbox')).toBeInTheDocument());
  });

  it('has no axe violations', async () => {
    request.mockResolvedValue({results: [{path: 'a.py'}], truncated: false});
    dialog();
    await screen.findByRole('option', {name: /a\.py/});
    await expectNoAxeViolations(document.body);
  });
});

describe('DiscardFilesDialog', () => {
  it('names the file and offers Keep editing and Discard changes', async () => {
    const user = userEvent.setup();
    const onCancel = vi.fn();
    const onDiscard = vi.fn();
    render(<DiscardFilesDialog paths={['src/api.ts']} onCancel={onCancel} onDiscard={onDiscard} />);
    const dialogNode = await screen.findByRole('dialog', {name: 'Discard unsaved changes?'});
    expect(dialogNode).toHaveTextContent('src/api.ts has edits that are not saved');
    await user.click(within(dialogNode).getByRole('button', {name: 'Discard changes'}));
    expect(onDiscard).toHaveBeenCalled();
    await user.click(within(dialogNode).getByRole('button', {name: 'Keep editing'}));
    expect(onCancel).toHaveBeenCalled();
    await expectNoAxeViolations(document.body);
  });

  it('lists several files, and closes with Escape as a cancel', async () => {
    const user = userEvent.setup();
    const onCancel = vi.fn();
    render(<DiscardFilesDialog paths={Array.from({length: 10}, (_, index) => 'f' + index + '.ts')} onCancel={onCancel} onDiscard={vi.fn()} />);
    expect(await screen.findByText('10 files have edits that are not saved. Closing drops them.')).toBeInTheDocument();
    expect(screen.getByText('and 2 more')).toBeInTheDocument();
    await user.keyboard('{Escape}');
    expect(onCancel).toHaveBeenCalled();
  });

  it('renders nothing when there is nothing to discard', () => {
    render(<DiscardFilesDialog paths={[]} onCancel={vi.fn()} onDiscard={vi.fn()} />);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });
});
