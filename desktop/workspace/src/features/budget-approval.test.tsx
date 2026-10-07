import {render,screen,fireEvent,waitFor,cleanup} from '@testing-library/react';
import {afterEach,it,expect,vi} from 'vitest';
import BudgetApproval from './BudgetApproval';
import {request} from '../lib/api';
vi.mock('../lib/api',()=>({request:vi.fn(async()=>({})),json:(method:string,value:unknown)=>({method,body:JSON.stringify(value)})}));
afterEach(()=>{cleanup();vi.clearAllMocks()});
it('sends only the explicitly edited total task budget with the exact pending approval',async()=>{const done=vi.fn();render(<BudgetApproval task={{id:'task',status:'awaiting_approval',prompt:'Bounded task',limits:{max_steps:256,max_seconds:120}}} approval={{id:'budget-request',status:'pending',kind:'budget',payload:{suggested_limits:{max_steps:512}}}} onDone={done} onError={()=>{}}/>);fireEvent.change(screen.getByLabelText('Extended max_steps'),{target:{value:'600'}});fireEvent.click(screen.getByRole('button',{name:'Approve budget extension'}));await waitFor(()=>expect(done).toHaveBeenCalledOnce());expect(request).toHaveBeenCalledWith('/api/workspace/tasks/task/approvals/budget-request/resolve',expect.objectContaining({body:JSON.stringify({decision:'approved',limits:{max_steps:600,max_seconds:120}})}))});
