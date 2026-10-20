import {render,screen,cleanup,fireEvent} from '@testing-library/react';
import {afterEach,it,expect,vi} from 'vitest';
import ToolActivities from './ToolActivities';
import {toolActivities} from '../lib/tool-activity';
vi.mock('../lib/api',()=>({gql:vi.fn(async()=>({engine_models:[]})),request:vi.fn(async()=>({extensions:[],runners:[],accounts:[]})),json:(method:string,value:unknown)=>({method,body:JSON.stringify(value)})}));
afterEach(cleanup);
it('shows one collapsed friendly action for an actual started/finished call and retains exact inspectable payloads',async()=>{const events=[{type:'tool.started',sequence:1,payload:{call:{name:'browser_action',call_id:'action-exact',arguments:{action:'click',args:{selector:'button[aria-label="Send message"]'}}}}},{type:'tool.finished',sequence:2,payload:{call_id:'action-exact',result:{ok:true,lease_revision:4}}}];render(<ToolActivities events={events}/>);expect(screen.getAllByText('Click Send message')).toHaveLength(1);expect(screen.getByText('Completed')).toBeVisible();expect(screen.queryByText('tool.started')).toBeNull();fireEvent.click(screen.getByText('Click Send message'));expect(await screen.findByText(/action-exact/)).toBeVisible();expect(toolActivities(events)).toHaveLength(1)});

it('collapses a long tool stream into one summary and keeps failures apparent',async()=>{
 const events=Array.from({length:12},(_,index)=>({type:index===11?'tool.failed':'tool.finished',sequence:index+1,payload:{call:{name:'read_file',call_id:'read-'+index,arguments:{path:'src/file-'+index+'.ts'}},result:{ok:index!==11}}}));
 const {container}=render(<ToolActivities events={events}/>);
 expect(screen.getByText('12 actions')).toBeVisible();expect(screen.getByText('1 failed')).toBeVisible();
 expect(container.querySelector('.task-activity')).not.toHaveAttribute('open');expect(container.querySelector('.tool')).not.toBeInTheDocument();
 fireEvent.click(screen.getByText('12 actions'));
 expect(await screen.findByRole('button',{name:/Read file · src\/file-0.ts/})).toBeVisible();
});
