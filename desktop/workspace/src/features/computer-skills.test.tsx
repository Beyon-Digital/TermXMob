import {cleanup,render,screen,waitFor,within} from '@testing-library/react';
import {afterEach,describe,expect,it,vi} from 'vitest';
import {expectNoAxeViolations} from '../test-axe';
import {request} from '../lib/api';
import ComputerSkills from './ComputerSkills';

vi.mock('../lib/api',()=>({request:vi.fn()}));
afterEach(()=>{cleanup();vi.clearAllMocks()});

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
    expect(within(any).getByText('No application list. It can act in whichever application is in front.')).toBeVisible();
  });

  it('states what the application check can and cannot do',async()=>{
    serve(skills);
    render(<ComputerSkills/>);
    await screen.findByRole('listitem',{name:'notes-helper'});
    const note=screen.getByText(/checked before each batch of input/i);
    expect(note).toHaveTextContent('The check after a batch cannot undo input that was already sent');
    expect(note).toHaveTextContent('Only macOS reports the application in front');
    expect(note).toHaveTextContent('each must name an application the same way');
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
