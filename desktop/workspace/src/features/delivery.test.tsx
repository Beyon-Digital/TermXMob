import {afterEach,beforeEach,expect,it,vi} from 'vitest';
import {act,cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import Delivery from './Delivery';
import {request} from '../lib/api';
vi.mock('../lib/api',()=>({request:vi.fn(),json:(method:string,data:unknown)=>({method,body:JSON.stringify(data)})}));
const api=vi.mocked(request);const project={id:'project',name:'Project',path:'/project'};
beforeEach(()=>{api.mockReset();api.mockImplementation(async(path:string,init?:RequestInit)=>{
 if(path.endsWith('/delivery'))return {status:{branch:'codex/change',files:[]},worktrees:[]};
 if(path.endsWith('/actions'))return [];
 if(path.endsWith('/prepare'))return {id:'action',operation:'push',arguments:{},head:'head-sha',requires_confirmation:true};
 if(path.endsWith('/execute'))return {status:'completed',result:{}};
 throw new Error(path);
})});
afterEach(()=>cleanup());
it('requires a concrete exact-operation decision before a push',async()=>{
 render(<Delivery project={project}/>);expect(screen.getByRole('combobox',{name:/^Git checkout$/})).toBeTruthy();expect(screen.getByRole('combobox',{name:/^Publication remote$/})).toBeTruthy();const push=await screen.findByRole('button',{name:'Review push'});await waitFor(()=>expect((push as HTMLButtonElement).disabled).toBe(false));fireEvent.click(push);
 await screen.findByRole('region',{name:'Exact delivery confirmation'});
 expect(api.mock.calls.some(([path])=>path.endsWith('/execute'))).toBe(false);
 fireEvent.click(screen.getByRole('button',{name:'Confirm this operation'}));
 await waitFor(()=>expect(api).toHaveBeenCalledWith('/api/development/projects/project/delivery/actions/action/execute',expect.objectContaining({body:'{"confirmed":true}'})));
});
it('reconciles uncertain delivery after reload without replaying it',async()=>{
 api.mockImplementation(async(path:string)=>path.endsWith('/actions')?[{id:'unknown',operation:'push',status:'unknown',arguments:'{}',head:'sha',created:1,result:null}]:{status:{branch:'main',files:[]},worktrees:[]});
 render(<Delivery project={project}/>);fireEvent.click(await screen.findByText('Recent delivery operations'));
 await screen.findByText(/Outcome uncertain/);expect(api.mock.calls.some(([,init])=>init?.method==='POST')).toBe(false);
});
it('keeps the selected Git checkout when an older checkout read returns late',async()=>{
 let resolveOld!:(value:unknown)=>void;
 api.mockImplementation(async(path:string)=>{
  if(path.endsWith('/actions'))return [];
  if(path.includes('worktree_id=isolated'))return {status:{branch:'codex/isolated',files:[]},worktrees:[{id:'isolated',branch:'codex/isolated',path:'/isolated'}]};
  return await new Promise(resolve=>{resolveOld=resolve});
 });
 const view=render(<Delivery project={project}/>);
 await waitFor(()=>expect(resolveOld).toBeDefined());
 view.rerender(<Delivery project={project} initialWorktreeId="isolated"/>);
 await screen.findByText('Head codex/isolated → main. New pull requests start as drafts.');
 await act(async()=>resolveOld({status:{branch:'main',files:[]},worktrees:[]}));
 await waitFor(()=>expect(screen.getByText('Head codex/isolated → main. New pull requests start as drafts.')).toBeTruthy());
 expect((screen.getByLabelText('Git checkout') as HTMLSelectElement).value).toBe('isolated');
});

it.each(['completed','failed'])('returns the delivery selector to project only after %s cleanup, retaining dirty refusal',async outcome=>{
 let removed=false;const errors=vi.fn();
 api.mockImplementation(async(path:string)=>{
  if(path.endsWith('/actions'))return [];
  if(path.endsWith('/prepare'))return {id:'cleanup',operation:'worktree-remove',arguments:{worktree_id:'isolated'},head:'sha',requires_confirmation:true};
  if(path.endsWith('/execute')){removed=outcome==='completed';return {status:outcome,result:removed?{}:{error:'Dirty worktree retained'}}}
  if(path.includes('worktree_id=isolated')){if(removed)throw new Error('Worktree not found');return {status:{branch:'codex/isolated',files:[]},worktrees:[{id:'isolated',branch:'codex/isolated',path:'/isolated'}]}}
  return {status:{branch:'main',files:[]},worktrees:removed?[]:[{id:'isolated',branch:'codex/isolated',path:'/isolated'}]};
 });
 render(<Delivery project={project} initialWorktreeId="isolated" onError={errors}/>);
 const cleanup=await screen.findByRole('button',{name:'Review worktree cleanup'});await waitFor(()=>expect((cleanup as HTMLButtonElement).disabled).toBe(false));fireEvent.click(cleanup);
 fireEvent.click(await screen.findByRole('button',{name:'Confirm this operation'}));
 if(outcome==='completed'){await waitFor(()=>expect((screen.getByRole('combobox',{name:/^Git checkout$/}) as HTMLSelectElement).value).toBe(''));await screen.findByText('No uncommitted changes.');expect(screen.queryByRole('alert')).toBeNull();expect(errors).not.toHaveBeenCalled()}
 else {await screen.findByText('Dirty worktree retained');expect((screen.getByRole('combobox',{name:/^Git checkout$/}) as HTMLSelectElement).value).toBe('isolated')}
});

it.each(['success','error'])('ignores a previous project PR inspection %s after selecting another project',async outcome=>{
 let resolvePull!:(value:unknown)=>void,rejectPull!:(reason:Error)=>void;
 api.mockImplementation(async(path:string)=>{
  if(path.includes('/pulls/'))return await new Promise((resolve,reject)=>{resolvePull=resolve;rejectPull=reject});
  if(path.endsWith('/actions'))return [];
  return {status:{branch:'main',files:[]},worktrees:[]};
 });
 const errors=vi.fn(),view=render(<Delivery project={project} onError={errors}/>);
 const inspect=screen.getByRole('button',{name:'Inspect PR & checks'});
 fireEvent.change(screen.getByLabelText('Pull request number'),{target:{value:'12'}});
 await waitFor(()=>expect(inspect).toBeEnabled());fireEvent.click(inspect);
 await waitFor(()=>expect(resolvePull).toBeDefined());
 view.rerender(<Delivery project={{...project,id:'other'}} onError={errors}/>);
 await waitFor(()=>expect(screen.getByRole('button',{name:'Review push'})).toBeEnabled());
 await act(async()=>{if(outcome==='success')resolvePull({number:12,title:'Old project PR',url:'https://example.com/old',statusCheckRollup:[],comments:[],reviews:[]});else rejectPull(new Error('Old project failure'))});
 expect(screen.queryByText(/Old project/)).toBeNull();expect(errors).not.toHaveBeenCalled();
});

it('prevents duplicate delivery requests before a pending operation completes',async()=>{
 let resolvePrepare!:(value:unknown)=>void;
 api.mockImplementation(async(path:string)=>{
  if(path.endsWith('/prepare'))return await new Promise(resolve=>{resolvePrepare=resolve});
  if(path.endsWith('/actions'))return [];
  return {status:{branch:'main',files:[]},worktrees:[]};
 });
 render(<Delivery project={project}/>);
 const push=screen.getByRole('button',{name:'Review push'});
 await waitFor(()=>expect(push).toBeEnabled());
 act(()=>{push.click();push.click()});
 expect(api.mock.calls.filter(([path])=>path.endsWith('/prepare'))).toHaveLength(1);
 await act(async()=>resolvePrepare({id:'one',operation:'push',arguments:{},head:'sha',requires_confirmation:true}));
 await screen.findByRole('region',{name:'Exact delivery confirmation'});
});
