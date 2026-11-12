import {afterEach,expect,it,vi} from 'vitest';
import {cleanup,fireEvent,render,screen} from '@testing-library/react';
import {expectNoAxeViolations} from '../test-axe';
import ConversationMessage from './ConversationMessage';
import type {Task} from '../lib/api';

vi.mock('../lib/agentic',async importOriginal=>({...await importOriginal<typeof import('../lib/agentic')>(),resolvePlan:vi.fn(async()=>undefined)}));
afterEach(cleanup);
const plan={id:'a1',status:'pending',kind:'plan',payload:{plan:{summary:'Add a button',markdown:'## Plan\n\n- Add the button'}}};
const base:Task={id:'t1',status:'awaiting_approval',prompt:'Add a button',engine:'internal',approvals:[plan]};
const handlers={onSupervise:vi.fn(),onRefresh:vi.fn(),onError:vi.fn()};

it('shows the plan review card for a plan-mode task and keeps the general card for one-shot plans',async()=>{
 const view=render(<ConversationMessage role="assistant" text="" task={base} {...handlers}/>);
 expect(screen.getByRole('button',{name:'Approve plan'})).toBeInTheDocument();expect(screen.getByRole('button',{name:'Request changes'})).toBeInTheDocument();
 view.rerender(<ConversationMessage role="assistant" text="" task={{...base,approvals:[{...plan,payload:{plan:{summary:'x',steps:[]}}}]}} {...handlers}/>);
 expect(screen.queryByRole('button',{name:'Approve plan'})).toBeNull();expect(screen.getByRole('button',{name:'Review action'})).toBeInTheDocument();
});
it('lists plan and Auto notes as quiet lines in the activity disclosure',async()=>{
 const events=[{sequence:1,type:'plan.revised',payload:{feedback:'Skip the migration'}},{sequence:2,type:'auto.decision',payload:{tool:'read_file'}},{sequence:3,type:'auto.decision',payload:{tool:'edit_file'}},{sequence:4,type:'auto.ceiling',payload:{ceiling:'tool_calls',limit:100,used:100}}];
 const {container}=render(<ConversationMessage role="assistant" text="" task={{...base,status:'running',approvals:[],events}} {...handlers}/>);
 fireEvent.click(screen.getByRole('button',{name:/Working on your request/}));
 const list=screen.getByRole('list',{name:'Plan and Auto notes'});
 expect([...list.querySelectorAll('li')].map(item=>item.textContent)).toEqual(['You asked for changes: Skip the migration','Auto approved 2 calls: read_file, edit_file','Auto paused at a previous tool-call limit (100 of 100).']);
 await expectNoAxeViolations(container);
});
