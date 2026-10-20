import {render,screen,cleanup,fireEvent} from '@testing-library/react';
import {afterEach,it,expect,vi} from 'vitest';
import ToolActivities from './ToolActivities';
import {toolActivities} from '../lib/tool-activity';
vi.mock('../lib/api',()=>({gql:vi.fn(async()=>({engine_models:[]})),request:vi.fn(async()=>({extensions:[],runners:[],accounts:[]})),json:(method:string,value:unknown)=>({method,body:JSON.stringify(value)})}));
afterEach(cleanup);
it('shows one completed friendly action and exposes its exact call only when inspected',()=>{const events=[{type:'tool.started',sequence:1,payload:{call:{name:'browser_action',call_id:'action-exact',arguments:{action:'click',args:{selector:'button[aria-label="Send message"]'}}}}},{type:'tool.finished',sequence:2,payload:{call_id:'action-exact',result:{ok:true,lease_revision:4}}}];render(<ToolActivities events={events}/>);expect(screen.getAllByText('Click Send message')).toHaveLength(1);expect(screen.getByText('Completed')).toBeVisible();expect(screen.queryByText('action-exact',{exact:true})).toBeNull();fireEvent.click(screen.getByText('Click Send message'));expect(screen.getByText('action-exact',{exact:true})).toBeVisible();expect(screen.getByLabelText('Click Send message result')).toHaveTextContent('lease_revision');expect(toolActivities(events)).toHaveLength(1)});

it('reveals earlier calls on demand and renders bounded native tool content as text',()=>{
 const events=[{type:'engine.tool.completed',sequence:1,payload:{call_id:'early',tool:'Read',output:{value:'Inspected the configuration'}}},{type:'engine.tool.completed',sequence:2,payload:{call_id:'latest',tool:'Check',output:{value:'Tests passed'}}}];
 render(<ToolActivities events={events} limit={1}/>);
 expect(screen.queryByText('Read',{exact:true})).toBeNull();
 fireEvent.click(screen.getByRole('button',{name:'Show earlier actions'}));
 fireEvent.click(screen.getByText('Read',{exact:true}));
 expect(screen.getByLabelText('Read result')).toHaveTextContent('Inspected the configuration');
 expect(screen.getByLabelText('Read result')).not.toHaveTextContent('"value"');
});
