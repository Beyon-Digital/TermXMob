import {cleanup,fireEvent,render,screen,waitFor,within} from '@testing-library/react';
import {afterEach,beforeEach,expect,it,vi} from 'vitest';
import Managers from './Managers';
import {MemoryEditor} from './MemoryEditor';
import {request} from '../lib/api';
import {expectNoAxeViolations} from '../test-axe';
vi.mock('../lib/api',()=>({request:vi.fn(),json:(method:string,value:unknown)=>({method,body:JSON.stringify(value)})}));
const mocked=vi.mocked(request);
afterEach(cleanup);
beforeEach(()=>{mocked.mockReset()});

const row=(extra:Record<string,unknown>)=>({id:'m',revision:1,content:'Note',provenance:'review',retention_days:30,excluded:false,project_id:null,scope:'user',agent_id:null,author:'user',state:'active',...extra});
type Body={query?:string;variables?:Record<string,unknown>}&Record<string,unknown>;
function serve(memory:ReturnType<typeof row>[],onWrite?:(path:string,body:Body)=>unknown){
 mocked.mockImplementation((async(path:string,init?:RequestInit)=>{
  if(path==='/auth/me')return {principal:{id:'owner',display_name:'Owner',scopes:[]},session_id:'s1'};
  const body:Body=init?.body?JSON.parse(String(init.body)):{};
  if(init?.method==='POST'||body.query)return onWrite?.(path,body)??{};
  return {memory};
 }) as typeof request);
}
const posts=()=>mocked.mock.calls.filter(([path,init])=>path==='/api/workspace/memory'&&init?.method==='POST').map(([,init])=>JSON.parse(String(init!.body)));

it('lists proposals separately and keeps one with the Keep memory action',async()=>{
 const proposal=row({id:'p1',content:'The API pages at 50',provenance:'Written by agent Helper in task 1a2b3c4d',scope:'agent',agent_id:'helper',author:'agent',state:'proposed'});
 let kept=false;
 serve([row({id:'u1',content:'Prefer small diffs'}),proposal],(path,body)=>{
  if(body.query?.includes('resolve_memory_proposal')){kept=true;return {data:{resolve_memory_proposal:{id:'p1'}}}}
  return {};
 });
 const {container}=render(<Managers embedded section="memory" projectId={null} sessionId={null}/>);
 const proposed=within(await screen.findByRole('region',{name:'Proposed memory'}));
 expect(proposed.getByText('The API pages at 50')).toBeInTheDocument();
 expect(proposed.getByText(/not used until you keep it/)).toBeInTheDocument();
 expect(screen.getByText('Prefer small diffs')).toBeInTheDocument();
 expect(within(screen.getByRole('region',{name:'Saved memory'})).queryByText('The API pages at 50')).toBeNull();
 await expectNoAxeViolations(container);
 fireEvent.click(proposed.getByRole('button',{name:'Keep memory'}));
 await waitFor(()=>expect(kept).toBe(true));
 const call=mocked.mock.calls.map(([,init])=>init?.body?JSON.parse(String(init.body)):{}).find(body=>body.query?.includes('resolve_memory_proposal'))!;
 expect(call.variables).toEqual({id:'p1',keep:true});
 expect(await screen.findByText('Memory kept')).toBeInTheDocument();
});

it('discards a proposal with the Discard memory action and says so',async()=>{
 serve([row({id:'p2',content:'Drop this',author:'agent',state:'proposed',scope:'agent',agent_id:'helper'})],(path,body)=>
  body.query?.includes('resolve_memory_proposal')?{data:{resolve_memory_proposal:null}}:{});
 render(<Managers embedded section="memory" projectId={null} sessionId={null}/>);
 fireEvent.click(await screen.findByRole('button',{name:'Discard memory'}));
 expect(await screen.findByText('Memory discarded')).toBeInTheDocument();
 const call=mocked.mock.calls.map(([,init])=>init?.body?JSON.parse(String(init.body)):{}).find(body=>body.query?.includes('resolve_memory_proposal'))!;
 expect(call.variables).toEqual({id:'p2',keep:false});
});

it('offers no proposal list when nothing is waiting, and marks agent-written notes',async()=>{
 serve([row({id:'a1',content:'Learned a thing',author:'agent',scope:'project',project_id:'project'})]);
 render(<Managers embedded section="memory" projectId="project" sessionId={null}/>);
 expect(await screen.findByText('Learned a thing')).toBeInTheDocument();
 expect(screen.queryByRole('region',{name:'Proposed memory'})).toBeNull();
 expect(screen.getByText(/Written by an agent/)).toBeInTheDocument();
});

it('chooses who a new note applies to and sends scope, project and agent',async()=>{
 serve([]);
 render(<Managers embedded section="memory" projectId="project" sessionId={null}/>);
 await screen.findByRole('form',{name:'Add memory'});
 const select=screen.getByLabelText('Applies to') as HTMLSelectElement;
 expect([...select.options].map(option=>option.text)).toEqual(['This project','All your projects']);
 expect(select.value).toBe('project');
 fireEvent.change(screen.getByLabelText(/^Fact or decision/),{target:{value:'Use pytest'}});
 fireEvent.change(screen.getByLabelText(/^Source or reason/),{target:{value:'AGENTS.md'}});
 fireEvent.click(screen.getByRole('button',{name:'Save memory'}));
 await waitFor(()=>expect(posts()).toHaveLength(1));
 expect(posts()[0]).toMatchObject({scope:'project',project_id:'project',agent_id:null,content:'Use pytest'});
 fireEvent.change(select,{target:{value:'user'}});
 fireEvent.change(screen.getByLabelText(/^Fact or decision/),{target:{value:'Be brief'}});
 fireEvent.change(screen.getByLabelText(/^Source or reason/),{target:{value:'me'}});
 fireEvent.click(screen.getByRole('button',{name:'Save memory'}));
 await waitFor(()=>expect(posts()).toHaveLength(2));
 expect(posts()[1]).toMatchObject({scope:'user',project_id:null,agent_id:null});
});

it('scopes the list and new notes to one agent when given an agent id',async()=>{
 serve([row({id:'a',content:'Only for this bot',scope:'agent',agent_id:'bot-1'})]);
 render(<Managers embedded section="memory" projectId={null} sessionId={null} agentId="bot-1"/>);
 await screen.findByText('Only for this bot');
 expect(mocked.mock.calls.some(([path])=>path==='/api/workspace/memory?agent_id=bot-1')).toBe(true);
 const select=screen.getByLabelText('Applies to') as HTMLSelectElement;
 expect([...select.options].map(option=>option.text)).toEqual(['This agent only','All your projects']);
 expect(select.value).toBe('agent');
 fireEvent.change(screen.getByLabelText(/^Fact or decision/),{target:{value:'Check the inbox first'}});
 fireEvent.change(screen.getByLabelText(/^Source or reason/),{target:{value:'me'}});
 fireEvent.click(screen.getByRole('button',{name:'Save memory'}));
 await waitFor(()=>expect(posts()).toHaveLength(1));
 expect(posts()[0]).toMatchObject({scope:'agent',agent_id:'bot-1',project_id:null});
});

it('fixes Applies to after save and sends the saved scope and agent when editing',async()=>{
 const saved={id:'e',revision:4,content:'Agent note',provenance:'me',project_id:null,retention_days:30,excluded:false,scope:'agent',agent_id:'bot-1',author:'user',state:'active'} as const;
 mocked.mockResolvedValue({});
 const done=vi.fn();
 const {container}=render(<MemoryEditor memory={{...saved}} onSaved={done} onCancel={vi.fn()}/>);
 expect(screen.queryByLabelText('Applies to')).toBeNull();
 expect(screen.getByText(/Applies to: This agent only\. Cannot be changed after saving/i)).toBeInTheDocument();
 await expectNoAxeViolations(container);
 fireEvent.change(screen.getByLabelText('Fact or decision'),{target:{value:'Agent note v2'}});
 fireEvent.click(screen.getByRole('button',{name:'Save memory changes'}));
 await waitFor(()=>expect(done).toHaveBeenCalledOnce());
 expect(JSON.parse(String(mocked.mock.calls[0][1]?.body))).toMatchObject({identifier:'e',revision:4,scope:'agent',agent_id:'bot-1',project_id:null});
});
