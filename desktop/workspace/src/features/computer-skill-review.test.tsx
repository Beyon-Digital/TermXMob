import {cleanup,fireEvent,render,screen} from '@testing-library/react';
import {afterEach,beforeEach,describe,expect,it,vi} from 'vitest';
import ExtensionManager from './ExtensionManager';
import {request} from '../lib/api';
vi.mock('../lib/api',()=>({request:vi.fn()}));
const mocked=vi.mocked(request);
afterEach(cleanup);
beforeEach(()=>{mocked.mockReset()});

function review(bundle:Record<string,unknown>){
  mocked.mockImplementation(async(path:string)=>{
    if(path==='/api/workspace/extensions/preview')return {id:'review-1',digest:'d',permission_diff:{added:['desktop-control'],removed:[]},dependencies:[],bundle:{id:'notes-helper',version:'1',permissions:['desktop-control'],files:{},...bundle}} as never;
    if(path.includes('/compatibility'))return {sources:[]} as never;
    return {sources:[],roots:[],diagnostics:[],versions:[]} as never;
  });
  render(<ExtensionManager data={{extensions:[],registries:[],formats:['termx-bundle/v1']}} projectId={null} onChanged={vi.fn()}/>);
  fireEvent.click(screen.getByText('Import portable bundle'));
  fireEvent.change(screen.getByLabelText('Bundle manifest'),{target:{value:'{}'}});
  fireEvent.click(screen.getByRole('button',{name:'Review bundle'}));
}

describe('computer skill review',()=>{
  it('names the applications a skill is limited to before install',async()=>{
    review({computer:{apps:['Notes','com.apple.Notes']}});
    expect(await screen.findByText('Controls this computer, limited to: Notes, com.apple.Notes.')).toBeVisible();
  });
  it('warns when a skill controls the computer with no application list',async()=>{
    review({});
    expect(await screen.findByText(/Controls this computer with no application list/)).toBeVisible();
    expect(screen.getByText(/Controls this computer with no application list/)).toHaveTextContent('any application you have allowed it to control');
    expect(document.body.textContent).not.toMatch(/in front/);
  });
});
