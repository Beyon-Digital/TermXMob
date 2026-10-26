import {act,cleanup,fireEvent,render,screen,waitFor,within} from '@testing-library/react';
import {afterEach,beforeEach,expect,it,vi} from 'vitest';
const api=vi.hoisted(()=>({request:vi.fn(),gql:vi.fn(),saved:vi.fn(),beginSave:vi.fn()}));
vi.mock('../lib/api',()=>({request:api.request,gql:api.gql,json:(method:string,value:unknown)=>({method,body:JSON.stringify(value)})}));
vi.mock('./SettingsSurface',()=>({useSettingsSave:()=>api.beginSave}));
import HookSettings from './HookSettings';

type Hook={id:string;project_id:string;revision:number;event:string;argv:string[];cwd:string;timeout_s:number;capabilities:string[];enabled:boolean};
const projects=[{id:'alpha',name:'Alpha',path:'/host/alpha'},{id:'beta',name:'Beta',path:'/host/beta'}];
const scopes=['files-read','files-write','agent-view','agent-control'];
let number=0,actor:{principal:{id:string;scopes:string[]}},access:{role:string;projects:{project_id:string;scopes:string[];expires?:number}[]},hooks:Hook[],unavailable:Set<string>;
function hook(id:string,project='alpha'):Hook{return {id,project_id:project,revision:3,event:'before_turn',argv:['python','scripts/check.py'],cwd:'/host/'+project,timeout_s:10,capabilities:['files-read','files-write'],enabled:false}}
const writes=()=>api.request.mock.calls.filter(([,init])=>['POST','PATCH','DELETE'].includes(init?.method||''));
beforeEach(()=>{
 vi.clearAllMocks();api.beginSave.mockReturnValue(api.saved);
 actor={principal:{id:'hook-test-owner-'+(++number),scopes:[...scopes]}};access={role:'owner',projects:[]};hooks=[hook('alpha-hook'),hook('beta-hook','beta')];unavailable=new Set();
 api.gql.mockResolvedValue({projects});
 api.request.mockImplementation(async(path:string,init:RequestInit={})=>{
  if(path==='/auth/me')return structuredClone(actor);
  if(path==='/auth/access')return structuredClone(access);
  if(path.startsWith('/api/workspace/hooks?')){const id=decodeURIComponent(path.split('project_id=')[1]);if(unavailable.has(id))throw Object.assign(Error('Project access unavailable'),{status:403});return {hooks:structuredClone(hooks.filter(row=>row.project_id===id))}}
  if(path.startsWith('/api/workspace/hook-runs?'))return {runs:[]};
  if(path==='/api/workspace/hooks'&&init.method==='POST'){
   const body=JSON.parse(String(init.body)),saved={...body,id:'created-hook',revision:1,capabilities:['files-read','files-write']};hooks.push(saved);return structuredClone(saved);
  }
  const id=decodeURIComponent(path.split('/').at(-1)||''),row=hooks.find(value=>value.id===id);
  if(row&&init.method==='PATCH'){const body=JSON.parse(String(init.body));if(body.revision!==row.revision)throw Object.assign(Error('Hook revision changed'),{status:409});row.enabled=body.enabled;row.revision++;return structuredClone(row)}
  if(row&&init.method==='DELETE'){hooks=hooks.filter(value=>value.id!==id);return {deleted:true}}
  throw Error('Unexpected fixture hook request '+path);
 });
});
afterEach(()=>{cleanup();vi.restoreAllMocks()});
async function ready(){await screen.findByRole('article',{name:'Hook in Alpha'})}
function openDraft(){fireEvent.click(screen.getByText('Add a hook',{selector:'summary'}));return within(screen.getByRole('form',{name:'Create lifecycle hook'}))}
function fillDraft(project='beta'){
 const form=openDraft();fireEvent.change(form.getByRole('combobox',{name:'Run in project'}),{target:{value:project}});fireEvent.change(form.getByLabelText('Executable and arguments (one per line)'),{target:{value:'python\nscripts/fresh-check.py'}});return form;
}

it('loads actual authorized project inventories sequentially and requires an explicit create target',async()=>{
 render(<HookSettings/>);await ready();
 expect(screen.getByRole('article',{name:'Hook in Beta'})).toBeVisible();
 const form=openDraft();expect(form.getByRole('combobox',{name:'Run in project'})).toHaveValue('');expect(form.getByLabelText('Working folder')).toHaveValue('');
 expect(form.getByRole('button',{name:'Review disabled hook'})).toBeDisabled();expect(writes()).toHaveLength(0);
 const reads=api.request.mock.calls.map(([path])=>path).filter(path=>path.includes('/hooks?')||path.includes('/hook-runs?'));
 expect(reads).toEqual(['/api/workspace/hooks?project_id=alpha','/api/workspace/hook-runs?project_id=alpha','/api/workspace/hooks?project_id=beta','/api/workspace/hook-runs?project_id=beta']);
 expect(screen.queryByRole('button',{name:'Run now'})).not.toBeInTheDocument();
});

it('reviews exact creation target and arguments and saves only one disabled hook after confirmation',async()=>{
 render(<HookSettings/>);await ready();const form=fillDraft();
 expect(form.getByLabelText('Working folder')).toHaveValue('/host/beta');
 fireEvent.click(form.getByRole('button',{name:'Review disabled hook'}));
 const review=screen.getByRole('dialog',{name:'Save this disabled hook?'});
 expect(review).toHaveTextContent('Project: Beta');expect(review).toHaveTextContent('Folder: /host/beta');expect(review).toHaveTextContent('read and write project files');expect(review).toHaveTextContent('saved disabled');
 expect(writes()).toHaveLength(0);
 const save=within(review).getByRole('button',{name:'Save disabled hook'});fireEvent.click(save);fireEvent.click(save);
 await waitFor(()=>expect(writes()).toHaveLength(1));
 expect(writes()[0][0]).toBe('/api/workspace/hooks');expect(JSON.parse(String(writes()[0][1]?.body))).toEqual({project_id:'beta',event:'before_turn',argv:['python','scripts/fresh-check.py'],cwd:'/host/beta',timeout_s:10,enabled:false,capabilities:[]});
 await screen.findByText('Hook saved disabled. Review it again before enabling lifecycle execution.');
 expect(api.saved).toHaveBeenCalledOnce();expect(hooks.find(row=>row.id==='created-hook')?.enabled).toBe(false);
});

it('enables only the reviewed original hook with its current revision and preserves another hook draft',async()=>{
 render(<HookSettings/>);await ready();const form=fillDraft('alpha');
 fireEvent.click(within(screen.getByRole('article',{name:'Hook in Beta'})).getByRole('button',{name:'Enable'}));
 const review=screen.getByRole('dialog',{name:'Enable this lifecycle hook?'});
 expect(review).toHaveTextContent('Project: Beta');expect(review).toHaveTextContent('Folder: /host/beta');expect(within(review).getByLabelText('Exact hook command')).toHaveTextContent('scripts/check.py');
 expect(writes()).toHaveLength(0);expect(api.beginSave).not.toHaveBeenCalled();
 fireEvent.click(within(review).getByRole('button',{name:'Confirm enable'}));
 await waitFor(()=>expect(writes()).toHaveLength(1));expect(writes()[0][0]).toBe('/api/workspace/hooks/beta-hook');expect(JSON.parse(String(writes()[0][1]?.body))).toEqual({enabled:true,revision:3});
 await screen.findByText('Hook enabled for future lifecycle events.');await ready();
 expect(screen.getByLabelText('Executable and arguments (one per line)')).toHaveValue('python\nscripts/fresh-check.py');expect(screen.getByLabelText('Working folder')).toHaveValue('/host/alpha');expect(api.saved).not.toHaveBeenCalled();
});

it('keeps viewers and projects without required grants read only',async()=>{
 access={role:'operator',projects:[{project_id:'alpha',scopes:[...scopes]},{project_id:'beta',scopes:['files-read','agent-view']}]};
 render(<HookSettings/>);await ready();
 const beta=within(screen.getByRole('article',{name:'Hook in Beta'}));expect(beta.getByRole('button',{name:'Enable'})).toBeDisabled();expect(beta.getByRole('button',{name:'Remove hook'})).toBeDisabled();expect(beta.getByText('Read only in this project.')).toBeVisible();
 const form=openDraft();expect(form.getByRole('option',{name:'Beta · Read only or not loaded'})).toBeDisabled();expect(form.getByRole('option',{name:'Alpha'})).not.toBeDisabled();expect(writes()).toHaveLength(0);
});

it('rechecks grants and the exact hook before an enable rather than trusting its earlier review',async()=>{
 render(<HookSettings/>);await ready();fireEvent.click(within(screen.getByRole('article',{name:'Hook in Alpha'})).getByRole('button',{name:'Enable'}));
 access={role:'viewer',projects:[{project_id:'alpha',scopes:['files-read','agent-view']}]};
 fireEvent.click(screen.getByRole('button',{name:'Confirm enable'}));
 expect(await screen.findByRole('alert')).toHaveTextContent('current account cannot');expect(writes()).toHaveLength(0);expect(hooks[0].enabled).toBe(false);
});

it('rejects a changed hook revision without sending the stale command again',async()=>{
 render(<HookSettings/>);await ready();fireEvent.click(within(screen.getByRole('article',{name:'Hook in Alpha'})).getByRole('button',{name:'Enable'}));
 hooks[0]={...hooks[0],revision:4,argv:['python','different-command.py']};
 fireEvent.click(screen.getByRole('button',{name:'Confirm enable'}));
 expect(await screen.findByRole('alert')).toHaveTextContent('hook changed');expect(writes()).toHaveLength(0);
});

it('keeps an uncertain create blocked across remount and reconciles only a new matching host record',async()=>{
 let reject!:(reason:Error)=>void,body:any;
 const previous=api.request.getMockImplementation()!;
 api.request.mockImplementation((path:string,init:RequestInit={})=>{if(path==='/api/workspace/hooks'&&init.method==='POST'){body=JSON.parse(String(init.body));return new Promise((_done,fail)=>{reject=fail})}return previous(path,init)});
 const view=render(<HookSettings/>);await ready();const form=fillDraft();fireEvent.click(form.getByRole('button',{name:'Review disabled hook'}));fireEvent.click(screen.getByRole('button',{name:'Save disabled hook'}));await waitFor(()=>expect(writes()).toHaveLength(1));
 view.unmount();render(<HookSettings/>);await ready();await act(async()=>reject(Error('Response lost after possible save')));
 expect(screen.getByText(/A hook save is unconfirmed/)).toBeVisible();const newer=openDraft();expect(newer.getByRole('button',{name:'Check pending save outcome'})).toBeDisabled();expect(writes()).toHaveLength(1);
 hooks.push({...body,id:'confirmed-new-hook',revision:1,capabilities:['files-read','files-write']});
 fireEvent.click(screen.getByRole('button',{name:'Check host state'}));await screen.findByText('The host confirms this hook was saved disabled.');
 expect(writes()).toHaveLength(1);expect(screen.queryByText(/A hook save is unconfirmed/)).not.toBeInTheDocument();
});

it('reports a forbidden source without dropping another project or a typed draft and retries reads only',async()=>{
 render(<HookSettings/>);await ready();fillDraft();unavailable.add('beta');
 fireEvent.click(screen.getByRole('button',{name:'Check host state'}));
 expect(await screen.findByRole('alert')).toHaveTextContent('unavailable for Beta');expect(screen.getByRole('article',{name:'Hook in Alpha'})).toBeVisible();expect(screen.queryByRole('article',{name:'Hook in Beta'})).not.toBeInTheDocument();
 expect(screen.getByLabelText('Executable and arguments (one per line)')).toHaveValue('python\nscripts/fresh-check.py');expect(screen.getByLabelText('Working folder')).toHaveValue('/host/beta');
 unavailable.clear();fireEvent.click(screen.getByRole('button',{name:'Check host state'}));await screen.findByRole('article',{name:'Hook in Beta'});expect(writes()).toHaveLength(0);
});

it('requires explicit removal review and acknowledges only that persistent change',async()=>{
 render(<HookSettings/>);await ready();fillDraft();fireEvent.click(within(screen.getByRole('article',{name:'Hook in Alpha'})).getByRole('button',{name:'Remove hook'}));
 const review=screen.getByRole('dialog',{name:'Remove this lifecycle hook?'});expect(review).toHaveTextContent('command already running may finish');expect(writes()).toHaveLength(0);
 fireEvent.click(within(review).getByRole('button',{name:'Confirm removal'}));await waitFor(()=>expect(writes()).toHaveLength(1));expect(writes()[0][0]).toBe('/api/workspace/hooks/alpha-hook');expect(writes()[0][1]?.method).toBe('DELETE');await screen.findByText('Hook removed. A command already running may finish.');expect(api.saved).toHaveBeenCalledOnce();
 expect(screen.getByLabelText('Executable and arguments (one per line)')).toHaveValue('python\nscripts/fresh-check.py');
});

it('bounds the initial catalog page and exposes additional authorized projects explicitly',async()=>{
 const all=Array.from({length:21},(_,index)=>({id:'project-'+index,name:'Project '+index,path:'/host/'+index}));api.gql.mockResolvedValue({projects:all});hooks=[];
 render(<HookSettings/>);await screen.findByText(/Showing 20 of 21 authorized projects/);
 expect(api.request.mock.calls.filter(([path])=>path.startsWith('/api/workspace/hooks?'))).toHaveLength(20);expect(api.request.mock.calls.some(([path])=>path.endsWith('project_id=project-20'))).toBe(false);
 fireEvent.click(screen.getByRole('button',{name:'Load more projects'}));await waitFor(()=>expect(api.request.mock.calls.some(([path])=>path.endsWith('project_id=project-20'))).toBe(true));expect(writes()).toHaveLength(0);
});

it.each([
 ['create','unmount'],['create','lock'],['enable','unmount'],['enable','lock'],
])('releases a stale %s preparation receipt after %s without dispatching or blocking the remounted review',async(kind,boundary)=>{
 const previous=api.request.getMockImplementation()!;
 let hold=false,settle!: (value:{hooks:Hook[]})=>void,signal:AbortSignal|undefined;
 api.request.mockImplementation((path:string,init:RequestInit={})=>{
  if(hold&&path==='/api/workspace/hooks?project_id=alpha'){
   hold=false;signal=init.signal||undefined;
   return new Promise(resolve=>{settle=resolve});
  }
  return previous(path,init);
 });
 const view=render(<HookSettings/>);await ready();
 if(kind==='create'){
  const form=fillDraft('alpha');fireEvent.click(form.getByRole('button',{name:'Review disabled hook'}));
 }else fireEvent.click(within(screen.getByRole('article',{name:'Hook in Alpha'})).getByRole('button',{name:'Enable'}));
 hold=true;fireEvent.click(screen.getByRole('button',{name:kind==='create'?'Save disabled hook':'Confirm enable'}));
 await waitFor(()=>expect(settle).toBeTypeOf('function'));
 if(boundary==='lock')await act(async()=>window.dispatchEvent(new Event('termx-session-locked')));
 else view.unmount();
 expect(signal?.aborted).toBe(true);expect(writes()).toHaveLength(0);
 await act(async()=>settle({hooks:structuredClone(hooks.filter(row=>row.project_id==='alpha'))}));
 if(boundary==='lock')view.unmount();
 render(<HookSettings/>);await ready();
 expect(screen.queryByText(/A hook save is pending|A hook save is unconfirmed/)).not.toBeInTheDocument();
 if(kind==='create'){
  const form=fillDraft('alpha');const retry=form.getByRole('button',{name:'Review disabled hook'});expect(retry).toBeEnabled();fireEvent.click(retry);
  expect(screen.getByRole('dialog',{name:'Save this disabled hook?'})).toBeVisible();
 }else{
  const retry=within(screen.getByRole('article',{name:'Hook in Alpha'})).getByRole('button',{name:'Enable'});expect(retry).toBeEnabled();fireEvent.click(retry);
  expect(screen.getByRole('dialog',{name:'Enable this lifecycle hook?'})).toBeVisible();
 }
 expect(writes()).toHaveLength(0);
});
