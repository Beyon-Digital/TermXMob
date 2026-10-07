import {render,screen,cleanup,fireEvent} from '@testing-library/react';
import {afterEach,it,expect,vi} from 'vitest';
import ToolActivities from './ToolActivities';
import {toolActivities} from '../lib/tool-activity';
vi.mock('../lib/api',()=>({gql:vi.fn(async()=>({engine_models:[]})),request:vi.fn(async()=>({extensions:[],runners:[],accounts:[]})),json:(method:string,value:unknown)=>({method,body:JSON.stringify(value)})}));
afterEach(cleanup);
it('shows one completed friendly action for an actual started/finished call and retains exact inspectable payloads',()=>{const events=[{type:'tool.started',sequence:1,payload:{call:{name:'browser_action',call_id:'action-exact',arguments:{action:'click',args:{selector:'button[aria-label="Send message"]'}}}}},{type:'tool.finished',sequence:2,payload:{call_id:'action-exact',result:{ok:true,lease_revision:4}}}];render(<ToolActivities events={events}/>);expect(screen.getAllByText('Click Send message')).toHaveLength(1);expect(screen.getByText('Completed')).toBeVisible();expect(screen.queryByText('tool.started')).toBeNull();fireEvent.click(screen.getByText('Click Send message'));expect(screen.getByText(/action-exact/)).toBeVisible();expect(toolActivities(events)).toHaveLength(1)});
