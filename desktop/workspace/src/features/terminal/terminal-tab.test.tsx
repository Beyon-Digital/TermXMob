import {act, cleanup, render, screen, waitFor} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {forwardRef, useImperativeHandle} from 'react';
import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest';
import {expectNoAxeViolations} from '../../test-axe';
import TerminalTab from './TerminalTab';

const api = vi.hoisted(() => ({listPtys: vi.fn(), createPty: vi.fn(), deletePty: vi.fn()}));
vi.mock('./terminal-api', () => api);
vi.mock('./TerminalSession', () => ({
  default: forwardRef(function Session(props: {id: string; active: boolean; authorized: boolean; onStatus: (id: string, status: {state: string; connected: boolean}) => void}, ref) {
    useImperativeHandle(ref, () => ({focus() {}, interrupt() {}, findNext: () => true, findPrevious: () => true, clearSearch() {}}));
    return (
      <div data-testid="session" data-id={props.id} data-active={props.active} data-authorized={props.authorized}>
        <button onClick={() => props.onStatus(props.id, {state: 'Connected', connected: true})}>connect</button>
      </div>
    );
  }),
}));

const project = {id: 'p1', name: 'Project', path: '/work'};
const session = {id: 'chat', cwd: '/work'} as never;
const pty = (id: string) => ({id, title: 'Project', cwd: '/work', exited: false});

function tab(extra: Partial<React.ComponentProps<typeof TerminalTab>> = {}) {
  const handlers = {onAttach: vi.fn(), onEnded: vi.fn(), onError: vi.fn()};
  const view = render(
    <TerminalTab ptyId={undefined} tabDomId="tab-t1" visible ownerId="owner" project={project} session={session} taken={new Set()} {...handlers} {...extra} />,
  );
  return {...handlers, ...view};
}

beforeEach(() => {
  api.listPtys.mockReset();
  api.createPty.mockReset();
  api.deletePty.mockReset();
});
afterEach(cleanup);

describe('TerminalTab', () => {
  it('shows one session for the shell it is attached to, once the host lists it', async () => {
    api.listPtys.mockResolvedValue([pty('pty-1'), pty('pty-2')]);
    const {container} = tab({ptyId: 'pty-2'});
    const session = await screen.findByTestId('session');
    expect(session).toHaveAttribute('data-id', 'pty-2');
    await waitFor(() => expect(session).toHaveAttribute('data-authorized', 'true'));
    // One xterm per tab, not one per shell on the host.
    expect(screen.getAllByTestId('session')).toHaveLength(1);
    await expectNoAxeViolations(container);
  });

  it('does not connect until the host has answered, and says the shell ended when the host no longer lists it', async () => {
    let answer: (rows: unknown[]) => void = () => {};
    api.listPtys.mockReturnValue(new Promise(resolve => (answer = resolve)));
    const {onEnded} = tab({ptyId: 'pty-9'});
    expect(screen.getByTestId('session')).toHaveAttribute('data-authorized', 'false');
    await act(async () => answer([pty('pty-1')]));
    expect(await screen.findByText('This shell has ended.')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', {name: 'Close tab'}));
    expect(onEnded).toHaveBeenCalled();
  });

  it('attaches an unattached tab to a running shell that no other tab shows', async () => {
    api.listPtys.mockResolvedValue([pty('pty-1'), pty('pty-2')]);
    const {onAttach} = tab({taken: new Set(['pty-1'])});
    await waitFor(() => expect(onAttach).toHaveBeenCalledWith('pty-2'));
  });

  it('offers a new terminal when none is running, and attaches the one it starts', async () => {
    const user = userEvent.setup();
    api.listPtys.mockResolvedValue([]);
    api.createPty.mockResolvedValue(pty('pty-7'));
    const {onAttach} = tab();
    expect(await screen.findByText('No terminal is running here.')).toBeInTheDocument();
    await user.click(screen.getByRole('button', {name: 'New terminal'}));
    expect(api.createPty).toHaveBeenCalledWith(project, 'chat');
    await waitFor(() => expect(onAttach).toHaveBeenCalledWith('pty-7'));
  });

  it('ends the shell and closes the tab from Close terminal', async () => {
    const user = userEvent.setup();
    api.listPtys.mockResolvedValue([pty('pty-1')]);
    api.deletePty.mockResolvedValue(undefined);
    const {onEnded} = tab({ptyId: 'pty-1'});
    await user.click(await screen.findByRole('button', {name: 'Close terminal'}));
    expect(api.deletePty).toHaveBeenCalledWith('pty-1');
    await waitFor(() => expect(onEnded).toHaveBeenCalled());
  });

  it('reports a status from its session and keeps Interrupt and find in a toolbar', async () => {
    const user = userEvent.setup();
    api.listPtys.mockResolvedValue([pty('pty-1')]);
    tab({ptyId: 'pty-1'});
    await user.click(await screen.findByRole('button', {name: 'connect'}));
    expect(screen.getByText('Connected')).toBeInTheDocument();
    expect(screen.getByRole('region', {name: 'Terminal'})).toHaveAttribute('data-connected-session', 'pty-1');
    expect(screen.getByRole('button', {name: 'Interrupt'})).toBeInTheDocument();
    await user.click(screen.getByRole('button', {name: 'Find in terminal'}));
    expect(screen.getByRole('search', {name: 'Find in terminal'})).toBeInTheDocument();
  });

  it('asks for a project when there is none', () => {
    tab({project: null});
    expect(screen.getByText(/Terminals run inside a project folder/)).toBeInTheDocument();
    expect(api.listPtys).not.toHaveBeenCalled();
  });
});
