import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest';
import {act, cleanup, fireEvent, render, screen, waitFor, within} from '@testing-library/react';
import ControlPlane from './ControlPlane';
import type {Session} from '../lib/api';

const mocks = vi.hoisted(() => ({list: vi.fn(), read: vi.fn(), clear: vi.fn()}));
vi.mock('../lib/workspace-data', () => ({listSessions: mocks.list, SessionCache: class {read = mocks.read; clear = mocks.clear;}}));
const rows = [
  {id:'blocked', title:'Fix routing', project_id:'p1', cwd:'/projects/app', engine:'internal', model:'model-a', latest_status:'recovery_confirmation_required', updated_at:10},
  {id:'active', title:'Build preview', project_id:'p2', cwd:'/projects/web', engine:'internal', latest_status:'recovering', updated_at:20},
] as Session[];
const props = () => ({page:'overview' as const, ownerId:'owner', enabled:true, projects:[{id:'p1', name:'App', path:'/projects/app'}, {id:'p2', name:'Web', path:'/projects/web'}], engines:[], providers:[], catalogState:'ready' as const, onPage:vi.fn(), onManage:vi.fn(), onNew:vi.fn(), onOpen:vi.fn(), onProject:vi.fn(), onRefresh:vi.fn()});
beforeEach(() => {vi.clearAllMocks(); mocks.list.mockResolvedValue(rows); mocks.read.mockImplementation(async id => ({...rows.find(row => row.id === id), turns:[]}));});
afterEach(cleanup);

describe('desktop control plane', () => {
  it('routes project work and operational configuration separately from execution', async () => {
    const callbacks = props(); render(<ControlPlane {...callbacks}/>);
    await screen.findByText('Fix routing');
    expect(mocks.list).toHaveBeenCalledWith(false);
    fireEvent.click(screen.getByRole('button', {name:/Agent presets/}));
    expect(callbacks.onManage).toHaveBeenCalledWith('agents');
    const project = screen.getByRole('heading', {name:'App'}).closest('article')!;
    fireEvent.click(within(project).getByRole('button', {name:'Open workbench'}));
    expect(callbacks.onProject).toHaveBeenCalledWith(callbacks.projects[0]);
    fireEvent.click(within(project).getByRole('button', {name:'New task'}));
    expect(callbacks.onNew).toHaveBeenCalledWith('p1');
  });

  it('filters by project and decision state, then opens the exact checkout for delivery', async () => {
    const callbacks = props(); render(<ControlPlane {...callbacks} page="tasks"/>);
    await screen.findByRole('button', {name:'Inspect Fix routing'});
    fireEvent.change(screen.getByLabelText('Filter runs by status'), {target:{value:'attention'}});
    expect(screen.queryByRole('button', {name:'Inspect Build preview'})).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Filter runs by project'), {target:{value:'p2'}});
    expect(screen.getByText('No runs match these filters.')).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Filter runs by project'), {target:{value:'p1'}});
    fireEvent.click(screen.getByRole('button', {name:'Inspect Fix routing'}));
    await waitFor(() => expect(mocks.read).toHaveBeenCalledWith('blocked'));
    fireEvent.click(screen.getByRole('button', {name:'Changes & delivery'}));
    expect(callbacks.onOpen).toHaveBeenCalledWith('blocked', 'review');
  });

  it('does not describe a failed initial read as an empty healthy queue', async () => {
    mocks.list.mockRejectedValue(new Error('Host unavailable'));
    render(<ControlPlane {...props()}/>);
    expect(await screen.findByRole('alert')).toHaveTextContent('Host unavailable');
    expect(screen.queryByText('No pending decisions')).not.toBeInTheDocument();
    mocks.list.mockResolvedValue(rows);
    fireEvent.click(screen.getByRole('button', {name:'Retry'}));
    await screen.findByText('Fix routing');
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('ignores an earlier inspection response after selecting another run', async () => {
    let complete!: (session: Session) => void;
    mocks.read.mockImplementation(id => id === 'blocked' ? new Promise<Session>(resolve => {complete = resolve;}) : Promise.resolve({...rows[1], turns:[]}));
    render(<ControlPlane {...props()} page="tasks"/>);
    fireEvent.click(await screen.findByRole('button', {name:'Inspect Fix routing'}));
    await waitFor(() => expect(mocks.read).toHaveBeenCalledWith('blocked'));
    fireEvent.click(screen.getByRole('button', {name:'Inspect Build preview'}));
    await act(async () => complete({...rows[0], title:'Obsolete inspection response', turns:[]}));
    expect(within(screen.getByRole('complementary', {name:'Run inspector'})).getByRole('heading', {name:'Build preview'})).toBeInTheDocument();
    expect(screen.queryByText('Obsolete inspection response')).not.toBeInTheDocument();
  });

  it('does not poll while the workspace is locked', async () => {
    render(<ControlPlane {...props()} enabled={false}/>);
    expect(mocks.list).not.toHaveBeenCalled();
  });
});
