import {afterEach,beforeEach,expect,it,vi} from 'vitest';
import {act,cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import TaskSupervisor from './TaskSupervisor';
import {request} from '../lib/api';
vi.mock('../lib/api',()=>({request:vi.fn(),json:(method:string,value:unknown)=>({method,body:JSON.stringify(value)})}));
const root={id:'root',prompt:'Coordinate implementation',status:'running',engine:'internal',mode:'agent',limits:{max_parallel_subagents:8,max_subagents_total:32},children:[]};
const props={taskId:'root',onClose:vi.fn(),onError:vi.fn()};
beforeEach(()=>{vi.mocked(request).mockResolvedValue(root)});
afterEach(()=>{cleanup();vi.resetAllMocks();vi.useRealTimers()});
async function builder(){render(<TaskSupervisor {...props}/>);fireEvent.click(await screen.findByRole('button',{name:'Build agent team'}));}
function receipt(init?:RequestInit){const body=JSON.parse(String(init?.body));return {ok:true,members:body.members.map((member:{agent:string},index:number)=>({agent:member.agent,ok:true,task_id:body.request_id+':worker'+index}))}}
function tenMembers(){for(let count=2;count<10;count++)fireEvent.click(screen.getByRole('button',{name:'Add worker'}));for(let index=1;index<=10;index++)fireEvent.change(screen.getByLabelText('Worker '+index+' task'),{target:{value:'Independent task '+index}})}
it('validates unique names and tasks, defaults to read-only and submits explicitly isolated editors',async()=>{
 await builder();const create=screen.getByRole('button',{name:'Create team'});expect(create).toBeDisabled();
 for(const index of [1,2]){expect(screen.getByLabelText('Worker '+index+' access')).toHaveValue('ask');fireEvent.change(screen.getByLabelText('Worker '+index+' task'),{target:{value:' Investigate '+index+' '}})}
 fireEvent.change(screen.getByLabelText('Worker 2 name'),{target:{value:' researcher '}});expect(create).toBeDisabled();
 fireEvent.change(screen.getByLabelText('Worker 2 name'),{target:{value:' Editor '}});fireEvent.change(screen.getByLabelText('Worker 2 access'),{target:{value:'agent'}});expect(create).toBeEnabled();
 vi.mocked(request).mockImplementationOnce(async(_path,init)=>receipt(init));fireEvent.click(create);await screen.findByText('Team created. Select a worker to inspect or steer it.');
 const call=vi.mocked(request).mock.calls.find(([path])=>path.endsWith('/swarm'))!;const body=JSON.parse(call[1]!.body as string);expect(body.request_id).toEqual(expect.any(String));expect(body.members.map((member:any)=>[member.agent,member.task,member.mode])).toEqual([['Researcher','Investigate 1','ask'],['Editor','Investigate 2','agent']]);
});
it('retains idempotency after HTTP failure and changes it only after editing team input',async()=>{
 await builder();for(const index of [1,2])fireEvent.change(screen.getByLabelText('Worker '+index+' task'),{target:{value:'Investigate '+index}});
 vi.mocked(request).mockImplementation(async(path)=>{if(path.endsWith('/swarm'))throw new Error('Connection interrupted');return root});
 for(const count of [1,2]){fireEvent.click(screen.getByRole('button',{name:count===1?'Create team':'Retry pending batch'}));await waitFor(()=>expect(props.onError).toHaveBeenCalledTimes(count))}
 fireEvent.change(screen.getByLabelText('Worker 1 task'),{target:{value:'Updated task'}});fireEvent.click(screen.getByRole('button',{name:'Create team'}));await waitFor(()=>expect(props.onError).toHaveBeenCalledTimes(3));
 const ids=vi.mocked(request).mock.calls.filter(([path])=>path.endsWith('/swarm')).map(([,init])=>JSON.parse(init!.body as string).request_id);expect(ids[0]).toBe(ids[1]);expect(ids[2]).not.toBe(ids[1]);
});
it('routes steering and cancellation to the selected child',async()=>{
 const child={...root,id:'child',parent_id:'root',agent_label:'Reviewer',prompt:'Review accessibility',children:[]};vi.mocked(request).mockResolvedValue({...root,children:[child]});render(<TaskSupervisor {...props}/>);fireEvent.click(await screen.findByRole('button',{name:/Reviewer · Review accessibility/}));
 fireEvent.change(screen.getByLabelText('Steer selected task'),{target:{value:' Check keyboard support '}});fireEvent.click(screen.getByRole('button',{name:'Steer task'}));await waitFor(()=>expect(request).toHaveBeenCalledWith('/api/workspace/tasks/child/steer',expect.objectContaining({body:JSON.stringify({message:'Check keyboard support'})})));
 await waitFor(()=>expect(screen.getByRole('button',{name:'Cancel task and descendants'})).toBeEnabled());fireEvent.click(screen.getByRole('button',{name:'Cancel task and descendants'}));await waitFor(()=>expect(request).toHaveBeenCalledWith('/api/workspace/tasks/child/cancel',expect.objectContaining({method:'POST'})));
});
it.each(['recovering','recovery_confirmation_required','cancelling'])('prevents retry and steering in %s',async(status)=>{
 vi.mocked(request).mockResolvedValue({...root,status});render(<TaskSupervisor {...props}/>);await screen.findByRole('navigation',{name:'Task tree'});expect(screen.queryByRole('button',{name:'Retry as a new turn'})).not.toBeInTheDocument();expect(screen.queryByLabelText('Steer selected task')).not.toBeInTheDocument();expect(screen.queryByRole('button',{name:'Build agent team'})).not.toBeInTheDocument();
});
it('reloads a failed initial task fetch',async()=>{
 vi.mocked(request).mockRejectedValueOnce(new Error('Task unavailable'));render(<TaskSupervisor {...props}/>);fireEvent.click(await screen.findByRole('button',{name:'Reload task tree'}));await screen.findByRole('navigation',{name:'Task tree'});expect(screen.queryByRole('alert')).not.toBeInTheDocument();
});
it('aborts stale loads after changing task and stops polling on unmount',async()=>{
 vi.useFakeTimers();let settle:(value:unknown)=>void=()=>{};const pending=new Promise(resolve=>settle=resolve);vi.mocked(request).mockImplementation(path=>path.endsWith('/root')?pending as any:Promise.resolve({...root,id:'new',prompt:'New coordinator'}) as any);
 const {rerender,unmount}=render(<TaskSupervisor {...props}/>);const firstSignal=vi.mocked(request).mock.calls[0][1]!.signal!;rerender(<TaskSupervisor {...props} taskId="new"/>);await act(async()=>{});expect(firstSignal.aborted).toBe(true);
 await act(async()=>{settle(root);await pending});expect(screen.queryByRole('button',{name:/Coordinate implementation/})).not.toBeInTheDocument();expect(screen.getByRole('button',{name:/New coordinator/})).toBeInTheDocument();
 const lastSignal=vi.mocked(request).mock.calls[1][1]!.signal!;unmount();expect(lastSignal.aborted).toBe(true);await act(async()=>{vi.advanceTimersByTime(10000)});expect(request).toHaveBeenCalledTimes(2);
});
it('resets pending controls on task changes and ignores a stale A to B to A mutation',async()=>{
 let reject:(error:Error)=>void=()=>{};const mutation=new Promise((_,failed)=>reject=failed);
 vi.mocked(request).mockImplementation((path,init)=>init?.method==='POST'?mutation as any:Promise.resolve({...root,id:path.split('/').at(-1)}) as any);
 const {rerender}=render(<TaskSupervisor {...props}/>);await screen.findByLabelText('Steer selected task');fireEvent.change(screen.getByLabelText('Steer selected task'),{target:{value:'Old instruction'}});fireEvent.click(screen.getByRole('button',{name:'Steer task'}));expect(screen.getByLabelText('Steer selected task')).toBeDisabled();
 rerender(<TaskSupervisor {...props} taskId="other"/>);await waitFor(()=>expect(screen.getByLabelText('Steer selected task')).toBeEnabled());rerender(<TaskSupervisor {...props}/>);await waitFor(()=>expect(screen.getByLabelText('Steer selected task')).toBeEnabled());
 await act(async()=>{reject(new Error('Old mutation failure'));await mutation.catch(()=>{})});expect(props.onError).not.toHaveBeenCalled();
});
it('requires explicit confirmation before recovering ambiguous unfinished effects',async()=>{
 vi.mocked(request).mockResolvedValue({...root,status:'recovery_confirmation_required'});render(<TaskSupervisor {...props}/>);const recover=await screen.findByRole('button',{name:'Confirm and recover task'});expect(recover).toBeDisabled();fireEvent.click(screen.getByRole('checkbox',{name:'I understand recovery may repeat an unfinished action'}));expect(recover).toBeEnabled();fireEvent.click(recover);await waitFor(()=>expect(request).toHaveBeenCalledWith('/api/workspace/tasks/root/recover',expect.objectContaining({method:'POST',body:JSON.stringify({confirm:true})})));
});
it('keeps enrolled workers cancellable while team creation is still pending and retains the locked form across reopen',async()=>{
 vi.useFakeTimers();let settle:(value:unknown)=>void=()=>{};const pending=new Promise(resolve=>settle=resolve);let enrolled=false;
 const child={...root,id:'planning-child',parent_id:'root',agent_label:'Researcher',prompt:'Inspect code',status:'planning',children:[]};
 vi.mocked(request).mockImplementation(async(path,init)=>{if(path.endsWith('/swarm')){enrolled=true;return pending}if(init?.method==='POST')return {ok:true};return {...root,children:enrolled?[child]:[]}});
 render(<TaskSupervisor {...props}/>);await act(async()=>{});fireEvent.click(screen.getByRole('button',{name:'Build agent team'}));
 for(const index of [1,2])fireEvent.change(screen.getByLabelText('Worker '+index+' task'),{target:{value:'Inspect code'}});
 fireEvent.click(screen.getByRole('button',{name:'Create team'}));expect(screen.getByRole('button',{name:'Creating workers…'})).toBeDisabled();expect(screen.getByLabelText('Worker 1 task')).toBeDisabled();expect(screen.getByRole('button',{name:'Cancel task and descendants'})).toBeEnabled();
 fireEvent.click(screen.getByRole('button',{name:'Close team builder'}));fireEvent.click(screen.getByRole('button',{name:'Build agent team'}));expect(screen.getByLabelText('Worker 1 task')).toHaveValue('Inspect code');expect(screen.getByLabelText('Worker 1 task')).toBeDisabled();
 await act(async()=>{await vi.advanceTimersByTimeAsync(1500)});fireEvent.click(screen.getByRole('button',{name:/Researcher · Inspect code/}));expect(screen.getByRole('button',{name:'Cancel task and descendants'})).toBeEnabled();fireEvent.click(screen.getByRole('button',{name:'Cancel task and descendants'}));await act(async()=>{});
 expect(request).toHaveBeenCalledWith('/api/workspace/tasks/planning-child/cancel',expect.objectContaining({method:'POST'}));expect(vi.mocked(request).mock.calls.filter(([path])=>path.endsWith('/swarm'))).toHaveLength(1);
 await act(async()=>{settle({ok:false,members:[{agent:'Researcher',ok:false,error:'Cancelled during planning'}]});await pending});
});
it('ignores legacy task quotas and lets the user assemble more than eight workers',async()=>{
 vi.mocked(request).mockResolvedValue({...root,limits:{max_parallel_subagents:1,max_subagents_total:1},tree_budget:{root_task_id:'root',max_steps:1,used_steps:1,max_execution_seconds:1,used_execution_seconds:1,active_workers:1},children:[{...root,id:'existing',parent_id:'root',prompt:'Existing worker',children:[]}]});
 await builder();
 expect(screen.getByLabelText('Worker 2 name')).toHaveValue('Reviewer');
 expect(screen.queryByText(/available slots|parallel \/|Individual task ceiling|Shared parent and child allowance/)).not.toBeInTheDocument();
 for(let count=2;count<10;count++)fireEvent.click(screen.getByRole('button',{name:'Add worker'}));
 for(let index=1;index<=10;index++)fireEvent.change(screen.getByLabelText('Worker '+index+' task'),{target:{value:'Independent task '+index}});
 expect(screen.getByRole('button',{name:'Add worker'})).toBeEnabled();expect(screen.getByRole('button',{name:'Create team'})).toBeEnabled();
 vi.mocked(request).mockImplementation(async(path,init)=>path.endsWith('/swarm')?receipt(init):root);fireEvent.click(screen.getByRole('button',{name:'Create team'}));
 await screen.findByText('Team created. Select a worker to inspect or steer it.');
 const calls=vi.mocked(request).mock.calls.filter(([address])=>address.endsWith('/swarm')).map(([,init])=>JSON.parse(String(init?.body)));
 expect(calls.map(call=>call.members.length)).toEqual([8,2]);expect(calls[0].request_id).toMatch(/:0$/);expect(calls[1].request_id).toBe(calls[0].request_id.replace(/:0$/,':1'));
 expect(screen.getAllByRole('button',{name:'Inspect worker'})).toHaveLength(10);
});

it('pauses hidden task polling and restores unsent steering and team forms on return',async()=>{
 vi.useFakeTimers();
 const view=render(<TaskSupervisor {...props} visible={false}/>);
 expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
 await act(async()=>{await vi.advanceTimersByTimeAsync(10000)});expect(request).not.toHaveBeenCalled();
 view.rerender(<TaskSupervisor {...props} visible/>);await act(async()=>{});
 expect(request).toHaveBeenCalledOnce();
 fireEvent.change(screen.getByLabelText('Steer selected task'),{target:{value:'Keep this instruction'}});
 fireEvent.click(screen.getByRole('button',{name:'Build agent team'}));
 fireEvent.change(screen.getByLabelText('Worker 1 task'),{target:{value:'Inspect keyboard navigation'}});
 fireEvent.change(screen.getByLabelText('Worker 2 name'),{target:{value:'Accessibility reviewer'}});
 const signal=vi.mocked(request).mock.calls[0][1]!.signal!;
 view.rerender(<TaskSupervisor {...props} visible={false}/>);
 expect(signal.aborted).toBe(true);expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
 await act(async()=>{await vi.advanceTimersByTimeAsync(15000)});expect(request).toHaveBeenCalledOnce();
 view.rerender(<TaskSupervisor {...props} visible/>);await act(async()=>{});
 expect(request).toHaveBeenCalledTimes(2);
 expect(screen.getByLabelText('Steer selected task')).toHaveValue('Keep this instruction');
 expect(screen.getByLabelText('Worker 1 task')).toHaveValue('Inspect keyboard navigation');
 expect(screen.getByLabelText('Worker 2 name')).toHaveValue('Accessibility reviewer');
 expect(props.onClose).not.toHaveBeenCalled();expect(vi.mocked(request).mock.calls.some(([,init])=>init?.method==='POST')).toBe(false);
});


it('stops after a partial batch receipt and shows the workers already created without replay',async()=>{
 await builder();tenMembers();
 vi.mocked(request).mockImplementation(async(path,init)=>{if(!path.endsWith('/swarm'))return root;const result=receipt(init);return {...result,ok:false,members:result.members.map((member:any,index:number)=>index===7?{agent:member.agent,ok:false,error:'Could not start'}:member)}});
 fireEvent.click(screen.getByRole('button',{name:'Create team'}));
 await screen.findByText('Some workers could not start. Inspect the created workers before trying again.');
 expect(vi.mocked(request).mock.calls.filter(([path])=>path.endsWith('/swarm'))).toHaveLength(1);
 expect(screen.getAllByRole('button',{name:'Inspect worker'})).toHaveLength(7);
 expect(screen.queryByRole('button',{name:'Retry pending batch'})).not.toBeInTheDocument();
 fireEvent.submit(screen.getByRole('button',{name:'Create team'}).closest('form')!);
 await act(async()=>{});expect(vi.mocked(request).mock.calls.filter(([path])=>path.endsWith('/swarm'))).toHaveLength(1);
});

it('retries only an explicitly selected unknown batch with its stable ID and retains earlier receipts',async()=>{
 await builder();tenMembers();let attempts=0;
 vi.mocked(request).mockImplementation(async(path,init)=>{if(!path.endsWith('/swarm'))return root;attempts++;if(attempts===2)throw new Error('Second batch response lost');return receipt(init)});
 fireEvent.click(screen.getByRole('button',{name:'Create team'}));
 await screen.findByText('Latest batch unconfirmed. Review the workers before retrying.');
 expect(screen.getAllByRole('button',{name:'Inspect worker'})).toHaveLength(8);
 expect(attempts).toBe(2);expect(screen.getByRole('button',{name:'Create team'})).toBeDisabled();
 await act(async()=>{});expect(attempts).toBe(2);
 fireEvent.click(screen.getByRole('button',{name:'Retry pending batch'}));
 await screen.findByText('Team created. Select a worker to inspect or steer it.');
 const calls=vi.mocked(request).mock.calls.filter(([path])=>path.endsWith('/swarm')).map(([,init])=>JSON.parse(String(init?.body)));
 expect(calls.map(call=>call.members.length)).toEqual([8,2,2]);expect(calls[2]).toEqual(calls[1]);expect(calls[1].request_id).not.toBe(calls[0].request_id);
 expect(screen.getAllByRole('button',{name:'Inspect worker'})).toHaveLength(10);
});

it('does not dispatch a later batch after changing tasks',async()=>{
 let release!:(value:unknown)=>void,first:RequestInit|undefined;
 vi.mocked(request).mockImplementation(async(path,init)=>path.endsWith('/swarm')?new Promise(resolve=>{release=resolve;first=init}):{...root,id:path.endsWith('/other')?'other':'root'});
 const view=render(<TaskSupervisor {...props}/>);fireEvent.click(await screen.findByRole('button',{name:'Build agent team'}));tenMembers();fireEvent.click(screen.getByRole('button',{name:'Create team'}));
 view.rerender(<TaskSupervisor {...props} taskId="other"/>);await screen.findByLabelText('Steer selected task');
 await act(async()=>release(receipt(first)));
 expect(vi.mocked(request).mock.calls.filter(([path])=>path.endsWith('/swarm'))).toHaveLength(1);
 expect(screen.queryByText('Team created. Select a worker to inspect or steer it.')).not.toBeInTheDocument();
});

it('pauses between batches while hidden and continues only after an explicit action',async()=>{
 let release!:(value:unknown)=>void,first:RequestInit|undefined,calls=0;
 vi.mocked(request).mockImplementation(async(path,init)=>{if(!path.endsWith('/swarm'))return root;calls++;if(calls===1)return new Promise(resolve=>{release=resolve;first=init});return receipt(init)});
 const view=render(<TaskSupervisor {...props}/>);fireEvent.click(await screen.findByRole('button',{name:'Build agent team'}));tenMembers();fireEvent.click(screen.getByRole('button',{name:'Create team'}));
 view.rerender(<TaskSupervisor {...props} visible={false}/>);await act(async()=>release(receipt(first)));expect(calls).toBe(1);
 view.rerender(<TaskSupervisor {...props}/>);expect(await screen.findByText('Team creation paused.')).toBeInTheDocument();expect(calls).toBe(1);
 fireEvent.click(screen.getByRole('button',{name:'Continue team creation'}));await screen.findByText('Team created. Select a worker to inspect or steer it.');expect(calls).toBe(2);
});
