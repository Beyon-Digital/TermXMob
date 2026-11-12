import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest';
import {act, cleanup, fireEvent, render, screen, waitFor, within} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import ControlPlane, {controlRows} from './ControlPlane';
import {ApiError, type Session} from '../lib/api';

const mocks = vi.hoisted(() => ({list: vi.fn(), read: vi.fn(), clear: vi.fn()}));
vi.mock('../lib/workspace-data', () => ({listSessions: mocks.list, SessionCache: class {read = mocks.read; clear = mocks.clear;}}));
const rows = [
  {id:'blocked', title:'Fix routing', project_id:'p1', cwd:'/projects/app', engine:'internal', model:'model-a', latest_status:'recovery_confirmation_required', updated_at:10},
  {id:'active', title:'Build preview', project_id:'p2', cwd:'/projects/web', engine:'internal', latest_status:'recovering', updated_at:20},
] as Session[];
const props = () => ({page:'overview' as const, ownerId:'owner', enabled:true, projects:[{id:'p1', name:'App', path:'/projects/app'}, {id:'p2', name:'Web', path:'/projects/web'}], engines:[], providers:[], catalogState:'ready' as const, onPage:vi.fn(), onManage:vi.fn(), onNew:vi.fn(), onAddProject:vi.fn(), onOpen:vi.fn(), onProject:vi.fn(), onRefresh:vi.fn()});
beforeEach(() => {vi.clearAllMocks(); mocks.list.mockResolvedValue(rows); mocks.read.mockImplementation(async id => ({...rows.find(row => row.id === id), turns:[]}));});
afterEach(cleanup);

describe('desktop projects workspace', () => {
  it.each(['projects', 'tasks', 'overview'] as const)('keeps the same visible navigation on %s', async page => {
    const callbacks = props(); render(<ControlPlane {...callbacks} page={page}/>);
    const navigation = screen.getByRole('navigation', {name:'Workspace views'});
    expect(within(navigation).getAllByRole('button').map(button => button.textContent)).toEqual(['Projects', 'Activity', 'Workflows', 'Overview']);
    expect(within(navigation).getByRole('button', {name:page === 'tasks' ? 'Activity' : page === 'projects' ? 'Projects' : 'Overview'})).toHaveAttribute('aria-current', 'page');
    fireEvent.click(within(navigation).getByRole('button', {name:'Activity'}));
    expect(callbacks.onPage).toHaveBeenCalledWith('tasks');
    await waitFor(() => expect(mocks.list).toHaveBeenCalledWith(false));
  });

  it('routes project work and configuration to the existing callbacks', async () => {
    const callbacks = props(); render(<ControlPlane {...callbacks}/>);
    await screen.findByRole('button', {name:'Inspect Fix routing'});
    fireEvent.click(screen.getByRole('button', {name:'Agents'}));
    expect(callbacks.onManage).toHaveBeenCalledWith('agents');
    fireEvent.click(screen.getByRole('button', {name:/App \/projects\/app/}));
    expect(callbacks.onProject).toHaveBeenCalledWith(callbacks.projects[0]);
    const project = screen.getByRole('button', {name:/App \/projects\/app/}).closest('li')!;
    fireEvent.click(within(project).getByRole('button', {name:'New chat'}));
    expect(callbacks.onNew).toHaveBeenCalledWith('p1');
  });

  it('opens the same add-project flow from the project heading and empty state', async () => {
    const callbacks = {...props(), projects: []}; render(<ControlPlane {...callbacks} page="projects"/>);
    await screen.findByText('Add a folder to start a project.');
    const buttons = screen.getAllByRole('button', {name:'Add project'});
    expect(buttons).toHaveLength(2);
    buttons.forEach(button => fireEvent.click(button));
    expect(callbacks.onAddProject).toHaveBeenCalledTimes(2);
    expect(callbacks.onNew).not.toHaveBeenCalled();
  });

  it('filters activity and opens exact session actions from focused details', async () => {
    const user = userEvent.setup(), callbacks = props(); render(<ControlPlane {...callbacks} page="tasks"/>);
    await screen.findByRole('button', {name:'Inspect Fix routing'});
    fireEvent.change(screen.getByLabelText('Filter activity by status'), {target:{value:'attention'}});
    expect(screen.queryByRole('button', {name:'Inspect Build preview'})).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Filter activity by project'), {target:{value:'p2'}});
    expect(screen.getByText('No activity matches these filters.')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', {name:'Clear filters'}));
    for (const [label, view] of [['Open chat', 'chat'], ['Changes', 'review'], ['Files', 'workbench'], ['Browser', 'browser']]) {
      await user.click(screen.getByRole('button', {name:'Inspect Fix routing'}));
      const dialog = await screen.findByRole('dialog', {name:'Fix routing'});
      await waitFor(() => expect(mocks.read).toHaveBeenCalledWith('blocked'));
      await user.click(within(dialog).getByRole('button', {name:label}));
      expect(callbacks.onOpen).toHaveBeenLastCalledWith('blocked', view);
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    }
  });

  it('contains keyboard focus and returns Escape and Close to the actual row', async () => {
    const user = userEvent.setup(); render(<ControlPlane {...props()} page="tasks"/>);
    const trigger = await screen.findByRole('button', {name:'Inspect Fix routing'});
    await user.click(trigger);
    const dialog = await screen.findByRole('dialog', {name:'Fix routing'});
    expect(within(dialog).getByText(new Date(rows[0].updated_at * 1000).toLocaleString())).toBeInTheDocument();
    expect(within(dialog).getByRole('button', {name:'Close'})).toHaveFocus();
    await user.tab();
    expect(within(dialog).getByRole('region', {name:'Activity details content'})).toHaveFocus();
    for (let index = 0; index < 8; index++) { await user.tab(); expect(dialog).toContainElement(document.activeElement as HTMLElement); }
    await user.keyboard('{Escape}');
    await waitFor(() => expect(trigger).toHaveFocus());
    const other = screen.getByRole('button', {name:'Inspect Build preview'});
    await user.click(other);
    await user.click(within(await screen.findByRole('dialog', {name:'Build preview'})).getByRole('button', {name:'Close'}));
    await waitFor(() => expect(other).toHaveFocus());
  });

  it('does not describe a failed initial activity read as healthy or empty', async () => {
    mocks.list.mockRejectedValue(new Error('Host unavailable'));
    render(<ControlPlane {...props()}/>);
    expect(await screen.findByRole('alert')).toHaveTextContent('Host unavailable');
    expect(screen.queryByText('No pending decisions.')).not.toBeInTheDocument();
    mocks.list.mockResolvedValue(rows);
    fireEvent.click(screen.getByRole('button', {name:'Retry'}));
    await screen.findByRole('button', {name:'Inspect Fix routing'});
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('retains received folders and activity during a transient refresh failure', async () => {
    const callbacks = props();
    const {rerender} = render(<ControlPlane {...callbacks} page="projects"/>);
    const folder = screen.getByRole('button', {name:'App'});
    await waitFor(() => expect(folder.parentElement?.querySelector('time')).not.toBeNull());
    mocks.list.mockRejectedValue(new Error('Connection interrupted'));
    fireEvent.click(screen.getByRole('button', {name:'Refresh'}));
    expect(await screen.findByRole('alert')).toHaveTextContent('Showing the last received activity.');
    rerender(<ControlPlane {...callbacks} page="projects" catalogState="failed"/>);
    expect(screen.getByRole('button', {name:'App'})).toBeInTheDocument();
    expect(screen.getByText('Projects could not refresh. Showing the last received folders.')).toBeInTheDocument();
    fireEvent.click(folder);
    expect(screen.getByRole('button', {name:'Open Fix routing'})).toBeInTheDocument();
  });

  it('keeps failed configuration counts separate from a ready project catalog', async () => {
    render(<ControlPlane {...props()} configurationState="failed"/>);
    expect(screen.getByText('Configuration counts unavailable.')).toBeInTheDocument();
    expect(screen.queryByText('0 installed engines · 0 configured accounts')).not.toBeInTheDocument();
    expect(screen.getByRole('button', {name:/App \/projects\/app/})).toBeInTheDocument();
    await screen.findByRole('button', {name:'Inspect Fix routing'});
  });

  it('ignores an earlier detail response after another row opens', async () => {
    let complete!: (session: Session) => void;
    mocks.read.mockImplementation(id => id === 'blocked' ? new Promise<Session>(resolve => {complete = resolve;}) : Promise.resolve({...rows[1], turns:[]}));
    const user = userEvent.setup(); render(<ControlPlane {...props()} page="tasks"/>);
    await user.click(await screen.findByRole('button', {name:'Inspect Fix routing'}));
    await waitFor(() => expect(mocks.read).toHaveBeenCalledWith('blocked'));
    await user.keyboard('{Escape}');
    await user.click(screen.getByRole('button', {name:'Inspect Build preview'}));
    await act(async () => complete({...rows[0], title:'Obsolete inspection response', turns:[]}));
    expect(within(screen.getByRole('dialog', {name:'Build preview'})).getByRole('heading', {name:'Build preview'})).toBeInTheDocument();
    expect(screen.queryByText('Obsolete inspection response')).not.toBeInTheDocument();
  });

  it('drops old owner data and closes details before a new owner catalog arrives', async () => {
    let complete!: (sessions: Session[]) => void;
    const callbacks = props(), user = userEvent.setup();
    const {rerender} = render(<ControlPlane {...callbacks} page="tasks"/>);
    await user.click(await screen.findByRole('button', {name:'Inspect Fix routing'}));
    await screen.findByRole('dialog', {name:'Fix routing'});
    mocks.list.mockImplementation(() => new Promise<Session[]>(resolve => {complete = resolve;}));
    rerender(<ControlPlane {...callbacks} page="tasks" ownerId="other-owner"/>);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(screen.queryByText('Fix routing')).not.toBeInTheDocument();
    await act(async () => complete([{...rows[1], title:'Other owner conversation'}]));
    await screen.findByRole('button', {name:'Inspect Other owner conversation'});
    expect(screen.queryByText('Fix routing')).not.toBeInTheDocument();
  });

  it('closes details on a page change or lock and does not poll while locked', async () => {
    const callbacks = props(), user = userEvent.setup();
    const {rerender} = render(<ControlPlane {...callbacks} page="tasks"/>);
    await user.click(await screen.findByRole('button', {name:'Inspect Fix routing'}));
    rerender(<ControlPlane {...callbacks} page="projects"/>);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    rerender(<ControlPlane {...callbacks} page="tasks"/>);
    await user.click(await screen.findByRole('button', {name:'Inspect Fix routing'}));
    rerender(<ControlPlane {...callbacks} page="tasks" enabled={false}/>);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    const calls = mocks.list.mock.calls.length;
    fireEvent(document, new Event('visibilitychange'));
    expect(mocks.list).toHaveBeenCalledTimes(calls);
    cleanup(); vi.clearAllMocks();
    render(<ControlPlane {...callbacks} enabled={false}/>);
    expect(mocks.list).not.toHaveBeenCalled();
    expect(screen.getByRole('button', {name:/App \/projects\/app/})).toBeDisabled();
  });

  it('does not reopen details after a selected row leaves and returns to the filtered scope', async () => {
    const user = userEvent.setup(); render(<ControlPlane {...props()} page="tasks"/>);
    await user.click(await screen.findByRole('button', {name:'Inspect Fix routing'}));
    await screen.findByRole('dialog', {name:'Fix routing'});
    fireEvent.change(screen.getByLabelText('Filter activity by project'), {target:{value:'p2'}});
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Filter activity by project'), {target:{value:''}});
    expect(screen.getByRole('button', {name:'Inspect Fix routing'})).toBeInTheDocument();
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('clears details and available actions when access to the conversation is revoked', async () => {
    const detail = {...rows[0], model:'private-model', turns:[]};
    mocks.read.mockResolvedValue(detail);
    const user = userEvent.setup(); render(<ControlPlane {...props()} page="tasks"/>);
    await user.click(await screen.findByRole('button', {name:'Inspect Fix routing'}));
    await screen.findByText('private-model');
    mocks.read.mockRejectedValue(new ApiError(403, 'Access revoked'));
    fireEvent(document, new Event('visibilitychange'));
    await screen.findByRole('dialog', {name:'Conversation unavailable'});
    expect(screen.queryByText('private-model')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', {name:'Open chat'})).not.toBeInTheDocument();
    expect(mocks.clear).toHaveBeenCalled();
  });

  it('expands only real, unarchived conversations in their folder', async () => {
    mocks.list.mockResolvedValue([...rows, {...rows[0], id:'archived', title:'Archived conversation', archived:true, updated_at:100}]);
    const callbacks = props(); render(<ControlPlane {...callbacks} page="projects"/>);
    const folder = within(screen.getByRole('list', {name:'Projects'})).getByRole('button', {name:'App'});
    expect(folder).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByRole('button', {name:'Open Fix routing'})).not.toBeInTheDocument();
    await waitFor(() => expect(folder.parentElement?.querySelector('time')).toHaveAttribute('datetime', new Date(10000).toISOString()));
    fireEvent.click(folder);
    const conversations = screen.getByRole('list', {name:'App conversations'});
    expect(within(conversations).queryByRole('button', {name:'Open Build preview'})).not.toBeInTheDocument();
    expect(within(conversations).queryByText('Archived conversation')).not.toBeInTheDocument();
    fireEvent.click(within(conversations).getByRole('button', {name:'Open Fix routing'}));
    expect(callbacks.onOpen).toHaveBeenCalledWith('blocked', 'chat');
    fireEvent.click(folder);
    expect(screen.queryByRole('list', {name:'App conversations'})).not.toBeInTheDocument();
  });

  it('keeps folder actions and their project identity intact', async () => {
    const callbacks = props(); render(<ControlPlane {...callbacks} page="projects"/>);
    fireEvent.click(screen.getByRole('button', {name:'App'}));
    fireEvent.click(screen.getByRole('button', {name:'Open folder'}));
    expect(callbacks.onProject).toHaveBeenCalledWith(callbacks.projects[0]);
    fireEvent.click(within(screen.getByRole('button', {name:'App'}).closest('li')!).getByRole('button', {name:'Activity'}));
    expect(callbacks.onPage).toHaveBeenCalledWith('tasks');
    fireEvent.click(screen.getByRole('button', {name:'New chat'}));
    expect(callbacks.onNew).toHaveBeenCalledWith('p1');
    await screen.findByRole('button', {name:'Open Fix routing'});
  });

  it('searches actual project names and paths and clears without creating records', async () => {
    const callbacks = props(); render(<ControlPlane {...callbacks} page="projects"/>);
    const search = screen.getByRole('searchbox', {name:'Search projects'});
    fireEvent.change(search, {target:{value:' /projects/web '}});
    expect(screen.queryByRole('button', {name:'App'})).not.toBeInTheDocument();
    expect(screen.getByRole('button', {name:'Web'})).toBeInTheDocument();
    fireEvent.change(search, {target:{value:'missing project'}});
    expect(screen.getByText('No projects match “missing project”.')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', {name:'Clear search'}));
    expect(search).toHaveValue('');
    expect(screen.getByRole('button', {name:'App'})).toHaveAttribute('title', '/projects/app');
    fireEvent.click(screen.getByRole('button', {name:'Add project'}));
    expect(callbacks.onAddProject).toHaveBeenCalledOnce();
    expect(callbacks.onNew).not.toHaveBeenCalled();
    await waitFor(() => expect(mocks.list).toHaveBeenCalledWith(false));
  });

  it('keeps generated workspaces discoverable by toggle, explicit search and current context', async () => {
    const managed = {id:'generated', name:'0123456789abcdef', path:'/host/worktrees/0123456789abcdef'};
    const callbacks = {...props(), projects:[...props().projects, managed]};
    const {rerender} = render(<ControlPlane {...callbacks} page="projects"/>);
    expect(screen.queryByRole('button', {name:managed.name})).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', {name:'Show workspaces', pressed:false}));
    expect(screen.getByRole('button', {name:managed.name})).toHaveAttribute('title', managed.path);
    fireEvent.click(screen.getByRole('button', {name:'Hide workspaces', pressed:true}));
    fireEvent.change(screen.getByRole('searchbox', {name:'Search projects'}), {target:{value:'0123456789abcdef'}});
    expect(screen.getByRole('button', {name:managed.name})).toBeInTheDocument();
    fireEvent.change(screen.getByRole('searchbox', {name:'Search projects'}), {target:{value:''}});
    rerender(<ControlPlane {...callbacks} page="projects" currentProjectId={managed.id}/>);
    expect(screen.getByRole('button', {name:managed.name})).toBeInTheDocument();
    expect(callbacks.projects[2]).toEqual(managed);
    await waitFor(() => expect(mocks.list).toHaveBeenCalledWith(false));
  });

  it('does not invent project dates or conversations', async () => {
    mocks.list.mockResolvedValue([]);
    render(<ControlPlane {...props()} page="projects"/>);
    const folder = screen.getByRole('button', {name:'App'});
    await waitFor(() => expect(folder.parentElement?.querySelector('[title="No conversation update available"]')).toHaveTextContent('—'));
    expect(folder.parentElement?.querySelector('time')).toBeNull();
    fireEvent.click(folder);
    expect(screen.getByText('No conversations in this project yet.')).toBeInTheDocument();
    expect(screen.queryByRole('list', {name:'App conversations'})).not.toBeInTheDocument();
  });

  it('retains the existing activity filter semantics including unstarted conversations', () => {
    const unstarted = {...rows[1], id:'unstarted', title:'Draft chat', latest_status:null, updated_at:30};
    const archived = {...rows[0], id:'archived', archived:true};
    const all = [...rows, unstarted, archived];
    expect(controlRows(all, '', '', 'all').map(row => row.id)).toEqual(['blocked', 'unstarted', 'active']);
    expect(controlRows(all, 'model-a routing', 'p1', 'attention').map(row => row.id)).toEqual(['blocked']);
    expect(controlRows(all, '', 'p2', 'running').map(row => row.id)).toEqual(['active']);
    expect(controlRows(all, '', '', 'completed')).toEqual([]);
  });
});
