import {render,screen,fireEvent,waitFor,cleanup} from '@testing-library/react';
import {afterEach,it,expect,vi} from 'vitest';
import BudgetApproval from './BudgetApproval';
function openReview(){fireEvent.click(screen.getByRole('button',{name:/^Review (budget extension|approved continuation)$/}))}
import {request} from '../lib/api';
vi.mock('../lib/api',()=>({request:vi.fn(async()=>({})),json:(method:string,value:unknown)=>({method,body:JSON.stringify(value)})}));
afterEach(()=>{cleanup();vi.clearAllMocks()});
it('sends only the explicitly edited total task budget with the exact pending approval',async()=>{const done=vi.fn();render(<BudgetApproval task={{id:'task',status:'awaiting_approval',prompt:'Bounded task',limits:{max_steps:256,max_seconds:120}}} approval={{id:'budget-request',status:'pending',kind:'budget',payload:{suggested_limits:{max_steps:512}}}} onDone={done} onError={()=>{}}/>);openReview();fireEvent.change(screen.getByLabelText('Step limit (tool calls and turns)'),{target:{value:'600'}});fireEvent.click(screen.getByRole('button',{name:'Approve budget extension'}));await waitFor(()=>expect(done).toHaveBeenCalledOnce());expect(request).toHaveBeenCalledWith('/api/workspace/tasks/task/approvals/budget-request/resolve',expect.objectContaining({body:JSON.stringify({decision:'approved',limits:{max_steps:600,max_seconds:120}})}))});
it('discloses the inspected shared grant and preserves its exact approval binding',async()=>{
 const done=vi.fn();render(<BudgetApproval task={{id:'child',status:'awaiting_approval',prompt:'Child',limits:{max_steps:24,max_seconds:60}}} approval={{id:'tree-grant',status:'pending',kind:'budget',payload:{suggested_limits:{max_steps:30,max_seconds:120},tree_budget:{root_task_id:'parent',version:3,used_steps:25,max_steps:25,used_execution_seconds:60,max_execution_seconds:60}}}} onDone={done} onError={()=>{}}/>);openReview();
 expect(screen.getByText(/Root parent · grant version 3/)).toBeTruthy();expect(screen.getByText(/Each paused task needs an explicit continue/)).toBeTruthy();
 expect(screen.getByLabelText('Total shared tool-call ceiling (calls)')).toHaveAttribute('min','26');
 fireEvent.click(screen.getByRole('button',{name:'Approve budget extension'}));await waitFor(()=>expect(done).toHaveBeenCalledOnce());
 expect(request).toHaveBeenCalledWith('/api/workspace/tasks/child/approvals/tree-grant/resolve',expect.objectContaining({body:JSON.stringify({decision:'approved',limits:{max_steps:30,max_seconds:120}})}));
});
it('fences delayed decisions and resets the next task budget without duplicate requests',async()=>{
 let finish!:(value:unknown)=>void;vi.mocked(request).mockImplementationOnce(()=>new Promise(resolve=>{finish=resolve}));const done=vi.fn(),error=vi.fn();
 const a={id:'a',status:'awaiting_approval',prompt:'A',limits:{max_steps:10,max_seconds:20}},b={...a,id:'b',limits:{max_steps:40,max_seconds:60}};
 const approval={id:'approval-a',status:'pending',kind:'budget',payload:{suggested_limits:{max_steps:20}}};
 const view=render(<BudgetApproval task={a} approval={approval} onDone={done} onError={error}/>);openReview();
 const allow=screen.getByRole('button',{name:'Approve budget extension'});fireEvent.click(allow);fireEvent.click(allow);expect(request).toHaveBeenCalledOnce();
 view.rerender(<BudgetApproval task={b} approval={{...approval,id:'approval-b',payload:{suggested_limits:{max_steps:80}}}} onDone={done} onError={error}/>);openReview();
 expect(screen.getByLabelText('Step limit (tool calls and turns)')).toHaveValue(80);expect(screen.getByRole('button',{name:'Approve budget extension'})).not.toBeDisabled();
 finish({});await waitFor(()=>expect(done).not.toHaveBeenCalled());expect(error).not.toHaveBeenCalled();
});
it('restart continuation offers the existing grant without fresh budget inputs',async()=>{
 const done=vi.fn();render(<BudgetApproval task={{id:'recovered',status:'awaiting_approval',prompt:'Recovered task',limits:{max_steps:30,max_seconds:60}}} approval={{id:'resume',status:'pending',kind:'budget',payload:{reuse_grant:true,suggested_limits:{max_steps:100,max_seconds:120},tree_budget:{root_task_id:'parent',version:2,used_steps:10,max_steps:30,used_execution_seconds:2,max_execution_seconds:60}}}} onDone={done} onError={()=>{}}/>);openReview();
 expect(screen.queryByRole('spinbutton')).toBeNull();expect(screen.getByText(/this does not reset time/)).toBeTruthy();
 fireEvent.click(screen.getByRole('button',{name:'Continue within approved budget'}));await waitFor(()=>expect(done).toHaveBeenCalledOnce());
 expect(request).toHaveBeenCalledWith('/api/workspace/tasks/recovered/approvals/resume/resolve',expect.objectContaining({body:JSON.stringify({decision:'approved'})}));
});

it('preserves edited grant limits across close and Lock while fencing an in-flight decision',async()=>{
 let finish!:(value:unknown)=>void;vi.mocked(request).mockImplementationOnce(()=>new Promise(resolve=>{finish=resolve}));const done=vi.fn(),error=vi.fn();
 render(<BudgetApproval task={{id:'task',status:'awaiting_approval',prompt:'Bounded task',limits:{max_steps:10,max_seconds:60}}} approval={{id:'grant',status:'pending',kind:'budget',payload:{suggested_limits:{max_steps:20}}}} onDone={done} onError={error}/>);
 expect(screen.queryByRole('dialog')).toBeNull();expect(request).not.toHaveBeenCalled();openReview();
 fireEvent.change(screen.getByLabelText('Step limit (tool calls and turns)'),{target:{value:'35'}});fireEvent.click(screen.getByRole('button',{name:'Close'}));openReview();expect(screen.getByLabelText('Step limit (tool calls and turns)')).toHaveValue(35);
 fireEvent.click(screen.getByRole('button',{name:'Approve budget extension'}));fireEvent(window,new Event('termx-locked'));expect(screen.queryByRole('dialog')).toBeNull();expect(screen.getByRole('button',{name:'Review budget extension'})).toBeDisabled();
 finish({});await waitFor(()=>expect(done).not.toHaveBeenCalled());expect(error).not.toHaveBeenCalled();fireEvent(window,new Event('termx-session-unlocked'));openReview();expect(screen.getByLabelText('Step limit (tool calls and turns)')).toHaveValue(35);expect(screen.getByRole('button',{name:'Approve budget extension'})).not.toBeDisabled();
});
it('says which Auto limit paused the run, in plain units, and leaves the run limits as they were',async()=>{
 const done=vi.fn();render(<BudgetApproval task={{id:'auto-task',status:'awaiting_approval',prompt:'Auto run',limits:{max_steps:64,max_seconds:900,shell_timeout_s:120}}} approval={{id:'auto-pause',status:'pending',kind:'budget',payload:{reason:'auto_steps',completed_steps:1,auto_ceiling:{ceiling:'steps',limit:1,used:1},suggested_limits:{max_steps:257}}}} onDone={done} onError={()=>{}}/>);
 expect(screen.getByRole('heading',{name:'Auto paused at its limit'})).toBeTruthy();openReview();
 expect(screen.getByText(/Auto stopped at its step limit of 1 steps \(1 steps used\)/)).toBeTruthy();
 expect(screen.getByLabelText('Step limit (tool calls and turns)')).toHaveValue(64);
 expect(screen.getByLabelText('Time limit (seconds)')).toHaveValue(900);expect(screen.getByLabelText('Shell command timeout (seconds)')).toHaveValue(120);
 expect(screen.queryByText(/max steps|shell timeout s/)).toBeNull();
});
