import {render,screen,fireEvent,waitFor,cleanup,act} from '@testing-library/react';
import {afterEach,it,expect,vi} from 'vitest';
import BudgetApproval from './BudgetApproval';
import {request} from '../lib/api';
vi.mock('../lib/api',()=>({request:vi.fn(async()=>({})),json:(method:string,value:unknown)=>({method,body:JSON.stringify(value)})}));
afterEach(()=>{cleanup();vi.clearAllMocks()});
const task={id:'task',status:'awaiting_approval',prompt:'Continue the requested implementation',limits:{max_steps:1,max_seconds:1}};
const approval={id:'old-budget-request',status:'pending',kind:'budget',payload:{suggested_limits:{max_steps:2},tree_budget:{max_steps:1,max_execution_seconds:1}}};
function open(){fireEvent.click(screen.getByRole('button',{name:'Review continuation'}))}
it('continues an exact legacy paused task without numeric quota payloads',async()=>{
 const done=vi.fn();render(<BudgetApproval task={task} approval={approval} onDone={done} onError={vi.fn()}/>);open();
 expect(screen.queryByRole('spinbutton')).not.toBeInTheDocument();expect(screen.getByText(task.prompt)).toBeInTheDocument();
 fireEvent.click(screen.getByRole('button',{name:'Continue task'}));await waitFor(()=>expect(done).toHaveBeenCalledOnce());
 expect(request).toHaveBeenCalledWith('/api/workspace/tasks/task/approvals/old-budget-request/resolve',expect.objectContaining({body:JSON.stringify({decision:'approved'})}));
});
it('stops the exact pending task only after the explicit stop decision',async()=>{
 const done=vi.fn();render(<BudgetApproval task={task} approval={approval} onDone={done} onError={vi.fn()}/>);open();
 expect(request).not.toHaveBeenCalled();fireEvent.click(screen.getByRole('button',{name:'Stop task'}));await waitFor(()=>expect(done).toHaveBeenCalledOnce());
 expect(request).toHaveBeenCalledWith('/api/workspace/tasks/task/approvals/old-budget-request/resolve',expect.objectContaining({body:JSON.stringify({decision:'denied'})}));
});
it('deduplicates a pending decision and ignores it after the approval identity changes',async()=>{
 let finish!:(value:unknown)=>void;vi.mocked(request).mockImplementationOnce(()=>new Promise(resolve=>{finish=resolve}));const done=vi.fn(),error=vi.fn();
 const view=render(<BudgetApproval task={task} approval={approval} onDone={done} onError={error}/>);open();
 const allow=screen.getByRole('button',{name:'Continue task'});fireEvent.click(allow);fireEvent.click(allow);expect(request).toHaveBeenCalledOnce();
 view.rerender(<BudgetApproval task={{...task,id:'next'}} approval={{...approval,id:'next-approval'}} onDone={done} onError={error}/>);open();
 expect(screen.getByRole('button',{name:'Continue task'})).toBeEnabled();await act(async()=>finish({}));expect(done).not.toHaveBeenCalled();expect(error).not.toHaveBeenCalled();
});
it('fences an in-flight decision on lock and allows a fresh exact review after unlock',async()=>{
 let finish!:(value:unknown)=>void;vi.mocked(request).mockImplementationOnce(()=>new Promise(resolve=>{finish=resolve}));const done=vi.fn(),error=vi.fn();
 render(<BudgetApproval task={task} approval={approval} onDone={done} onError={error}/>);open();
 fireEvent.click(screen.getByRole('button',{name:'Continue task'}));fireEvent(window,new Event('termx-locked'));
 expect(screen.queryByRole('dialog')).not.toBeInTheDocument();expect(screen.getByRole('button',{name:'Review continuation'})).toBeDisabled();
 await act(async()=>finish({}));expect(done).not.toHaveBeenCalled();expect(error).not.toHaveBeenCalled();
 fireEvent(window,new Event('termx-session-unlocked'));open();expect(screen.getByRole('button',{name:'Continue task'})).toBeEnabled();expect(screen.queryByRole('spinbutton')).not.toBeInTheDocument();
});
