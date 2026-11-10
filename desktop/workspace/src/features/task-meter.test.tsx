import {afterEach,beforeEach,expect,it,vi} from 'vitest';
import {cleanup,render,screen,waitFor} from '@testing-library/react';
import {expectNoAxeViolations} from '../test-axe';
import TaskMeterLine,{useTaskMeter} from './TaskMeter';
import {loadTaskMeter,type TaskMeter} from '../lib/agentic';
import type {Task} from '../lib/api';

vi.mock('../lib/agentic',async importOriginal=>({...await importOriginal<typeof import('../lib/agentic')>(),loadTaskMeter:vi.fn()}));
const meter:TaskMeter={auto:{enabled:true,ceilings:{tool_calls:100},used:{tool_calls:7}},usage:{tokens:12400,cost_usd:null},context:{used:30000,window:null,limit:120000,window_known:false}};
beforeEach(()=>{vi.mocked(loadTaskMeter).mockReset();vi.mocked(loadTaskMeter).mockResolvedValue(meter)});
afterEach(cleanup);
function Probe({task,live}:{task?:Task;live:boolean}){const value=useTaskMeter(task,live);return value?<TaskMeterLine meter={value}/>:<p>No meter</p>}
const task=(patch:Partial<Task>={}):Task=>({id:'t1',status:'completed',prompt:'x',engine:'internal',...patch});

it('says Unknown for a missing price and names the assumed window',async()=>{
 const {container}=render(<TaskMeterLine meter={meter}/>);
 expect(screen.getByText('Context 30K of an unknown window (120K assumed)')).toBeInTheDocument();
 expect(screen.getByText('12.4K tokens · Unknown')).toBeInTheDocument();expect(screen.getByText('Auto 7 of 100 calls')).toBeInTheDocument();
 await expectNoAxeViolations(container);
});
it('hides the Auto allowance when Auto is off',()=>{
 render(<TaskMeterLine meter={{...meter,auto:{enabled:false,ceilings:null,used:{}}}}/>);expect(screen.queryByText(/Auto/)).toBeNull();
});
it('reads a finished task once instead of polling it',async()=>{
 render(<Probe task={task()} live={false}/>);
 expect(await screen.findByText(/Context 30K/)).toBeInTheDocument();
 await new Promise(resolve=>setTimeout(resolve,50));expect(loadTaskMeter).toHaveBeenCalledTimes(1);expect(vi.mocked(loadTaskMeter).mock.calls[0][0]).toBe('t1');
});
it('does not ask the host about native engine tasks',async()=>{
 render(<Probe task={task({engine:'claude'})} live/>);await new Promise(resolve=>setTimeout(resolve,30));
 expect(loadTaskMeter).not.toHaveBeenCalled();expect(screen.getByText('No meter')).toBeInTheDocument();
});
it('drops the previous task meter while the next one loads',async()=>{
 const view=render(<Probe task={task()} live={false}/>);await screen.findByText(/Context 30K/);
 vi.mocked(loadTaskMeter).mockReturnValue(new Promise(()=>{}));
 view.rerender(<Probe task={task({id:'t2'})} live/>);await waitFor(()=>expect(screen.getByText('No meter')).toBeInTheDocument());
});
it('stops reading a finished task after a failure instead of retrying forever',async()=>{
 vi.mocked(loadTaskMeter).mockRejectedValue(new Error('gone'));
 render(<Probe task={task()} live={false}/>);
 await waitFor(()=>expect(loadTaskMeter).toHaveBeenCalledTimes(1));
 await new Promise(resolve=>setTimeout(resolve,60));expect(loadTaskMeter).toHaveBeenCalledTimes(1);expect(screen.getByText('No meter')).toBeInTheDocument();
});
