import {cleanup, fireEvent, render, screen, waitFor, within} from '@testing-library/react';
import {afterEach, describe, expect, it, vi} from 'vitest';
import {commandAvailable, commands, findCommand} from '../commands';
import AddProjectHost from './AddProjectHost';
import CommandPaletteDialog from './CommandPaletteDialog';
import LayoutsDialog from './LayoutsDialog';
import NewChatDialog from './NewChatDialog';
import RecoverWindowsDialog from './RecoverWindowsDialog';

const mocks = vi.hoisted(() => ({
  load: vi.fn(async () => {}),
  lockedNow: {current: false},
  registered: undefined as undefined | ((project: unknown, isCurrent: () => boolean) => Promise<void>),
}));

vi.mock('../AuthGate', () => ({
  useAuth: () => ({auth: {principal: {id: 'owner'}}, locked: false, lockedNow: mocks.lockedNow}),
}));
vi.mock('../WorkspaceDataProvider', () => ({
  useWorkspaceData: () => ({projects: [{id: 'p1', name: 'One', path: '/one'}], load: mocks.load}),
}));
vi.mock('../../components/AddProjectDialog', () => ({
  default: (props: {open: boolean; onRegistered: typeof mocks.registered}) => {
    mocks.registered = props.onRegistered;
    return props.open ? <p>Add project open</p> : null;
  },
}));
vi.mock('../../components/HostPathPicker', () => ({
  HostPathField: ({
    label,
    value,
    onChange,
    disabled,
  }: {
    label: string;
    value: string;
    onChange: (v: string) => void;
    disabled: boolean;
  }) => (
    <label>
      {label}
      <input value={value} disabled={disabled} onChange={event => onChange(event.target.value)} />
    </label>
  ),
}));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  mocks.lockedNow.current = false;
});

describe('CommandPaletteDialog', () => {
  const agent = {workspace: 'agent' as const, hostAdmin: true};

  it('lists exactly the registry for the context', () => {
    render(
      <CommandPaletteDialog open onOpenChange={() => {}} context={agent} bindings={{}} onRun={() => {}} />,
    );
    const rows = screen.getAllByRole('button').filter(button => button.classList.contains('command-row'));
    expect(rows).toHaveLength(commands.filter(command => commandAvailable(command, agent)).length);
    expect(rows.map(row => row.querySelector('span')?.textContent)).toEqual(
      commands.filter(command => commandAvailable(command, agent)).map(command => command.title),
    );
  });

  it('leaves out Agent-only and host-admin commands elsewhere and shows stored shortcuts', () => {
    render(
      <CommandPaletteDialog
        open
        onOpenChange={() => {}}
        context={{workspace: 'data', hostAdmin: false}}
        bindings={{palette: 'Mod+Shift+P'}}
        onRun={() => {}}
      />,
    );
    expect(screen.queryByRole('button', {name: /Stop this host/})).toBeNull();
    expect(screen.queryByRole('button', {name: /Toggle bottom panel/})).toBeNull();
    expect(screen.getByRole('button', {name: /Open Settings/})).toBeInTheDocument();
    expect(
      within(screen.getByRole('button', {name: /Workspace commands/})).getByText(/P/, {selector: 'kbd'}),
    ).toBeInTheDocument();
  });

  it('runs the chosen command and then closes', () => {
    const onRun = vi.fn();
    const onOpenChange = vi.fn();
    render(
      <CommandPaletteDialog open onOpenChange={onOpenChange} context={agent} bindings={{}} onRun={onRun} />,
    );
    fireEvent.change(screen.getByRole('searchbox', {name: 'Search commands'}), {
      target: {value: 'refresh conversations'},
    });
    fireEvent.keyDown(screen.getByRole('searchbox', {name: 'Search commands'}), {key: 'Enter'});
    expect(onRun).toHaveBeenCalledWith(findCommand('refresh'));
    expect(onOpenChange).toHaveBeenCalledWith(false);
    expect(onRun.mock.invocationCallOrder[0]).toBeLessThan(onOpenChange.mock.invocationCallOrder[0]);
  });
});

describe('RecoverWindowsDialog', () => {
  const row = (extra = {}) => ({
    slot: 'a',
    ownerId: 'owner',
    sessionId: 'chat-1',
    panel: 'chat' as const,
    parent: null,
    token: null,
    updated: 0,
    drafts: 1,
    buffers: 2,
    artifacts: 3,
    ...extra,
  });

  it('says when there is nothing to recover', () => {
    render(<RecoverWindowsDialog open onOpenChange={() => {}} rows={[]} sessions={[]} onReopen={() => {}} />);
    expect(screen.getByText('No other unsaved windows are stored for this account.')).toBeInTheDocument();
  });

  it('describes each window and reopens it, except one without a conversation', () => {
    const onReopen = vi.fn();
    const rows = [row(), row({slot: 'b', sessionId: null})];
    render(
      <RecoverWindowsDialog
        open
        onOpenChange={() => {}}
        rows={rows}
        sessions={[{id: 'chat-1', title: 'Fix the build'}]}
        onReopen={onReopen}
      />,
    );
    expect(
      screen.getByText(/Fix the build · 2 unsaved files · 1 drafts · 3 unsaved artifacts/),
    ).toBeInTheDocument();
    expect(screen.getByText(/Preserved workspace · 2 unsaved files/)).toBeInTheDocument();
    const [first, second] = screen.getAllByRole('button', {name: 'Reopen window'});
    expect(second).toBeDisabled();
    fireEvent.click(first);
    expect(onReopen).toHaveBeenCalledWith(rows[0]);
  });
});

describe('LayoutsDialog', () => {
  it('shows the saved layouts and passes saving through', () => {
    const onSave = vi.fn();
    render(
      <LayoutsDialog
        open
        onOpenChange={() => {}}
        presets={[]}
        onSave={onSave}
        onRestore={() => {}}
        onRemove={() => {}}
        onReset={() => {}}
      />,
    );
    expect(screen.getByRole('dialog', {name: 'Saved layout presets'})).toBeInTheDocument();
    expect(screen.getByRole('button', {name: /reset/i})).toBeInTheDocument();
  });
});

describe('NewChatDialog', () => {
  const props = (extra = {}) => ({
    open: true,
    onOpenChange: vi.fn(),
    ownerId: 'owner',
    keepFocusOnClose: () => false,
    projects: [{id: 'p1', name: 'One', path: '/one'}],
    projectId: '',
    onProject: vi.fn(),
    presets: [{id: 'preset', name: 'Reviewer', engine: 'internal', workspace_revision: 'r'}],
    presetsLoading: false,
    preset: '',
    onPreset: vi.fn(),
    folder: '',
    onFolder: vi.fn(),
    creating: false,
    locked: false,
    onCreate: vi.fn(),
    ...extra,
  });

  it('creates the chat from the form and passes project, preset and folder choices up', () => {
    const p = props();
    render(<NewChatDialog {...p} />);
    fireEvent.change(screen.getByLabelText('Project'), {target: {value: 'p1'}});
    expect(p.onProject).toHaveBeenCalledWith('p1');
    fireEvent.change(screen.getByLabelText('New conversation agent preset'), {target: {value: 'preset'}});
    expect(p.onPreset).toHaveBeenCalledWith('preset');
    fireEvent.change(screen.getByLabelText('Add a project folder'), {target: {value: '/work/new'}});
    expect(p.onFolder).toHaveBeenCalledWith('/work/new');
    fireEvent.click(screen.getByRole('button', {name: 'Create chat'}));
    expect(p.onCreate).toHaveBeenCalledTimes(1);
  });

  it('cannot be submitted while creating, while presets load, or for a preset that no longer exists', () => {
    const {rerender} = render(<NewChatDialog {...props({creating: true})} />);
    expect(screen.getByRole('button', {name: 'Creating…'})).toBeDisabled();
    expect(screen.getByLabelText('Add a project folder')).toBeDisabled();
    rerender(<NewChatDialog {...props({presetsLoading: true})} />);
    expect(screen.getByRole('button', {name: 'Create chat'})).toBeDisabled();
    rerender(<NewChatDialog {...props({preset: 'gone'})} />);
    expect(screen.getByRole('button', {name: 'Create chat'})).toBeDisabled();
    rerender(<NewChatDialog {...props({locked: true})} />);
    expect(screen.getByLabelText('Add a project folder')).toBeDisabled();
    expect(screen.getByRole('button', {name: 'Create chat'})).toBeEnabled();
  });
});

describe('AddProjectHost', () => {
  const project = {id: 'new', name: 'New', path: '/new'};
  const setup = () => {
    const onProjectAdded = vi.fn();
    let intent = 4;
    render(
      <AddProjectHost
        open
        onOpenChange={() => {}}
        navigationIntent={() => intent}
        onProjectAdded={onProjectAdded}
      />,
    );
    return {onProjectAdded, bump: () => void (intent += 1)};
  };

  it('reloads the projects and opens the new one', async () => {
    const {onProjectAdded} = setup();
    await mocks.registered!(project, () => true);
    expect(mocks.load).toHaveBeenCalledTimes(1);
    expect(onProjectAdded).toHaveBeenCalledWith(project);
  });

  it('does not navigate when the dialog was dismissed, the person went elsewhere, or the workspace locked', async () => {
    const {onProjectAdded, bump} = setup();
    await mocks.registered!(project, () => false);
    mocks.load.mockImplementationOnce(async () => bump());
    await mocks.registered!(project, () => true);
    mocks.load.mockImplementationOnce(async () => {
      mocks.lockedNow.current = true;
    });
    await mocks.registered!(project, () => true);
    await waitFor(() => expect(mocks.load).toHaveBeenCalledTimes(3));
    expect(onProjectAdded).not.toHaveBeenCalled();
  });
});
