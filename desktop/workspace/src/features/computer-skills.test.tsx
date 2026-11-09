import {cleanup,fireEvent,render,screen,waitFor,within} from '@testing-library/react';
import {afterEach,beforeEach,describe,expect,it,vi} from 'vitest';
import {expectNoAxeViolations} from '../test-axe';
import {gql,request} from '../lib/api';
import ComputerSkills from './ComputerSkills';

vi.mock('../lib/api',()=>({request:vi.fn(),gql:vi.fn()}));
afterEach(()=>{cleanup();vi.clearAllMocks()});
beforeEach(()=>{vi.mocked(gql).mockResolvedValue({machine:{capabilities:{agent_desktop:{ready:true,version:'1.2.0',issue:null}}}} as never)});

const skills={extensions:[
  {id:'notes-helper',version:'1',permissions:['desktop-control'],description:'Types into Notes',computer_apps:['Notes','com.apple.Notes']},
  {id:'any-app',version:'2',permissions:['desktop-control'],description:'Drives anything'},
  {id:'linter',version:'1',permissions:['files-read'],description:'Checks style'},
]};
const serve=(value:unknown)=>vi.mocked(request).mockImplementation(async()=>value as never);

describe('computer skills',()=>{
  it('lists only the skills that control the computer, with a badge and their applications',async()=>{
    serve(skills);
    render(<ComputerSkills/>);
    const notes=await screen.findByRole('listitem',{name:'notes-helper'});
    expect(within(notes).getByText('Controls this computer')).toBeVisible();
    expect(within(notes).getByText('Notes')).toBeVisible();
    expect(within(notes).getByText('com.apple.Notes')).toBeVisible();
    expect(screen.queryByText('linter')).toBeNull();
    expect(request).toHaveBeenCalledWith('/api/workspace/available-extensions');
  });

  it('says plainly when a skill has no application list',async()=>{
    serve(skills);
    render(<ComputerSkills/>);
    const any=await screen.findByRole('listitem',{name:'any-app'});
    expect(within(any).getByText('No application list. It can act in any application you have allowed it to control.')).toBeVisible();
  });

  it('states how the application list is enforced and what control by accessibility means',async()=>{
    serve(skills);
    render(<ComputerSkills/>);
    await screen.findByRole('listitem',{name:'notes-helper'});
    const note=screen.getByText(/can only act in the applications listed/i);
    expect(note).toHaveTextContent('checked before each action');
    expect(note).toHaveTextContent('It reads each application’s controls, not its pixels');
    expect(note).toHaveTextContent('applications that draw their own interface may not respond');
    expect(note).toHaveTextContent('each must name an application the same way');
    expect(document.body.textContent).not.toMatch(/in front|batch of input|Only macOS/);
  });

  it('says when desktop control is not set up and opens Settings, Tools & connections',async()=>{
    serve(skills);
    vi.mocked(gql).mockResolvedValue({machine:{capabilities:{agent_desktop:{ready:false,version:null,issue:'not_installed'}}}} as never);
    const popped=vi.fn();window.addEventListener('popstate',popped);
    render(<ComputerSkills/>);
    expect(await screen.findByText('Desktop control is not set up on this computer.')).toBeVisible();
    fireEvent.click(screen.getByRole('link',{name:'Set up desktop control'}));
    expect(window.location.pathname).toBe('/manage/connections');expect(popped).toHaveBeenCalled();
    window.removeEventListener('popstate',popped);window.history.pushState(null,'','/');
  });

  it('stays quiet about setup when desktop control is ready or its state cannot be read',async()=>{
    serve(skills);
    const view=render(<ComputerSkills/>);
    await screen.findByRole('listitem',{name:'notes-helper'});
    await waitFor(()=>expect(gql).toHaveBeenCalled());
    expect(screen.queryByText('Desktop control is not set up on this computer.')).toBeNull();
    view.unmount();
    vi.mocked(gql).mockRejectedValue(new Error('nope'));
    render(<ComputerSkills/>);
    await screen.findByRole('listitem',{name:'notes-helper'});
    expect(screen.queryByText('Desktop control is not set up on this computer.')).toBeNull();
  });

  it('points to the next step when no skill controls the computer',async()=>{
    serve({extensions:[skills.extensions[2]]});
    render(<ComputerSkills/>);
    expect(await screen.findByText(/No computer skills are installed/)).toHaveTextContent('Install one under Plugins & skills');
    expect(screen.queryByRole('list')).toBeNull();
  });

  it('shows what to do when the list cannot be loaded',async()=>{
    vi.mocked(request).mockRejectedValue(new Error('Host unavailable'));
    render(<ComputerSkills/>);
    expect(await screen.findByRole('alert')).toHaveTextContent('Computer skills could not be loaded. Host unavailable');
    expect(screen.getByRole('button',{name:'Reload computer skills'})).toBeEnabled();
  });

  it('reloads after an error',async()=>{
    vi.mocked(request).mockRejectedValueOnce(new Error('Host unavailable')).mockResolvedValue(skills as never);
    render(<ComputerSkills/>);
    await screen.findByRole('alert');
    screen.getByRole('button',{name:'Reload computer skills'}).click();
    expect(await screen.findByRole('listitem',{name:'notes-helper'})).toBeVisible();
    await waitFor(()=>expect(screen.queryByRole('alert')).toBeNull());
  });

  it('has no accessibility violations in any state',async()=>{
    serve(skills);
    const {container}=render(<ComputerSkills/>);
    await screen.findByRole('listitem',{name:'notes-helper'});
    await expectNoAxeViolations(container);
    cleanup();
    vi.mocked(request).mockRejectedValue(new Error('down'));
    const failed=render(<ComputerSkills/>);
    await screen.findByRole('alert');
    await expectNoAxeViolations(failed.container);
  });
});
