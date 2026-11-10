import {afterEach,expect,it,vi} from 'vitest';
import {cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import {expectNoAxeViolations} from '../test-axe';
import Chat from './Chat';
import {draftSubmissionOwners,draftWrites,sessionDrafts,submittingDrafts} from '../lib/drafts';
import {gql,request,type Session,type Provider} from '../lib/api';
import {AUTO_CEILING_DEFAULTS,type TaskMeter} from '../lib/agentic';

vi.mock('../lib/api',()=>({gql:vi.fn(),request:vi.fn(),json:(method:string,value:unknown)=>({method,body:JSON.stringify(value)})}));
afterEach(()=>{cleanup();vi.unstubAllGlobals();sessionDrafts.clear();draftWrites.clear();submittingDrafts.clear();draftSubmissionOwners.clear();vi.clearAllMocks()});
const provider:Provider={id:'fixture-account',name:'Fixture account',model:'fixture-model',secret_configured:true,capabilities:['functions']};
const props={engines:[],providers:[provider],onSelect:vi.fn(),onRefresh:vi.fn(),onError:vi.fn()};
const session=(patch:Partial<Session>={},running=false):Session=>({id:'s1',title:'s1',engine:'internal',provider_id:provider.id,model:'fixture-model',mode:'agent',pinned:false,archived:false,draft_text:'',revision:1,updated_at:1,scroll:0,worktree_id:'wt',
 turns:running?[{id:'turn',prompt:'Go',sequence:1,task:{id:'task-1',prompt:'Go',status:'running',engine:'internal',events:[]}}]:[],...patch});
const meter:TaskMeter={auto:{enabled:false,ceilings:null,used:{}},usage:{tokens:2000,cost_usd:0.01},context:{used:1500,window:128000,limit:96000,window_known:true}};
function host(row:Session){
 vi.mocked(gql).mockImplementation((async(query:string)=>query.includes('agent_task(')?{agent_task:meter}:query.includes('set_task_auto')?{set_task_auto:{id:'task-1'}}:{engine_models:[]}) as never);
 vi.mocked(request).mockImplementation((async(path:string)=>path==='/api/runners'?[]:path.includes('runner-agents')?{accounts:[]}:path.endsWith('/queue')?{items:[]}:path.endsWith('/turns')?{ok:true}:{...row}) as never);
}
const openPicker=()=>fireEvent.click(screen.getByRole('button',{name:'Session mode'}));

it('sends Auto with the turn when it is on in a worktree conversation',async()=>{
 const row=session();host(row);render(<Chat session={row} {...props}/>);
 openPicker();fireEvent.click(screen.getByRole('switch',{name:'Auto'}));
 fireEvent.change(screen.getByRole('textbox',{name:'Message'}),{target:{value:'Refactor the parser'}});fireEvent.click(screen.getByRole('button',{name:'Send message'}));
 await waitFor(()=>expect(vi.mocked(request).mock.calls.some(([path])=>path.endsWith('/turns'))).toBe(true));
 const call=vi.mocked(request).mock.calls.find(([path])=>path.endsWith('/turns'))!;
 expect(JSON.parse((call[1] as RequestInit).body as string).auto).toEqual({enabled:true,ceilings:AUTO_CEILING_DEFAULTS});
});
it('does not send Auto when it is off or the mode is not Agent',async()=>{
 const row=session({mode:'plan'});host(row);render(<Chat session={row} {...props}/>);
 fireEvent.change(screen.getByRole('textbox',{name:'Message'}),{target:{value:'Plan the parser work'}});fireEvent.click(screen.getByRole('button',{name:'Send message'}));
 await waitFor(()=>expect(vi.mocked(request).mock.calls.some(([path])=>path.endsWith('/turns'))).toBe(true));
 expect(JSON.parse((vi.mocked(request).mock.calls.find(([path])=>path.endsWith('/turns'))![1] as RequestInit).body as string)).not.toHaveProperty('auto');
});
it('switches Auto on a running task through set_task_auto',async()=>{
 const row=session({},true);host(row);render(<Chat session={row} {...props}/>);
 openPicker();fireEvent.click(screen.getByRole('switch',{name:'Auto'}));
 await waitFor(()=>expect(vi.mocked(gql).mock.calls.some(([query])=>String(query).includes('set_task_auto'))).toBe(true));
 const call=vi.mocked(gql).mock.calls.find(([query])=>String(query).includes('set_task_auto'))!;
 expect(call[1]).toEqual({id:'task-1',input:{enabled:true,ceilings:AUTO_CEILING_DEFAULTS}});
});
it('reports a refused switch and returns to the task state',async()=>{
 const row=session({},true);host(row);
 vi.mocked(gql).mockImplementation((async(query:string)=>{if(query.includes('set_task_auto'))throw new Error('Auto mode needs an isolated worktree');return query.includes('agent_task(')?{agent_task:meter}:{engine_models:[]}}) as never);
 render(<Chat session={row} {...props}/>);openPicker();fireEvent.click(screen.getByRole('switch',{name:'Auto'}));
 await waitFor(()=>expect(props.onError).toHaveBeenCalled());expect(screen.getByRole('switch',{name:'Auto'})).not.toBeChecked();
});
it('explains why Auto is off when the conversation has no worktree',()=>{
 const row=session({worktree_id:null});host(row);render(<Chat session={row} {...props}/>);openPicker();
 expect(screen.getByRole('switch',{name:'Auto'})).toBeDisabled();expect(screen.getByText(/Project actions, Session checkout/)).toBeInTheDocument();
});
it('shows context and usage for the latest task, with Unknown where the host has no value',async()=>{
 const row=session({},true);host(row);
 vi.mocked(gql).mockImplementation((async(query:string)=>query.includes('agent_task(')?{agent_task:{...meter,usage:{tokens:2000,cost_usd:null}}}:{engine_models:[]}) as never);
 const {container}=render(<Chat session={row} {...props}/>);
 expect(await screen.findByText('Context 1.5K of 128K')).toBeInTheDocument();expect(screen.getByText('2K tokens · Unknown')).toBeInTheDocument();
 openPicker();await expectNoAxeViolations(container);
});
// jsdom has no layout, so the virtualized transcript needs measurable rows to render the plan card.
function measureRows(){Object.defineProperty(HTMLElement.prototype,'getBoundingClientRect',{configurable:true,value(){return {width:800,height:this.classList?.contains('message-row')?140:600,top:0,left:0,right:800,bottom:600}}});Object.defineProperty(HTMLElement.prototype,'offsetHeight',{configurable:true,get(){return this.classList?.contains('message-row')?140:600}});Object.defineProperty(HTMLElement.prototype,'offsetWidth',{configurable:true,get(){return 800}});Object.defineProperty(HTMLElement.prototype,'clientHeight',{configurable:true,get(){return 600}});vi.stubGlobal('ResizeObserver',class{constructor(private callback:ResizeObserverCallback){}observe(target:Element){queueMicrotask(()=>{if(this.active)this.callback([{target,contentRect:target.getBoundingClientRect(),borderBoxSize:[{inlineSize:800,blockSize:target.getBoundingClientRect().height}]}] as unknown as ResizeObserverEntry[],this as unknown as ResizeObserver)})}active=true;unobserve(){}disconnect(){this.active=false}})}
it('shows Agent in the mode picker once the host refreshes after Approve plan',async()=>{
 const approval={id:'approval-1',status:'pending',kind:'plan',payload:{plan:{summary:'Add a flag',markdown:'## Plan\n\n1. Add the flag'}}};
 const waiting:Session=session({mode:'plan',turns:[{id:'turn',prompt:'Plan it',sequence:1,task:{id:'task-1',prompt:'Plan it',status:'awaiting_approval',engine:'internal',events:[],approvals:[approval]}}] as Session['turns']});
 measureRows();host(waiting);
 let view!:ReturnType<typeof render>;
 // The host switches the conversation to Agent mode as part of resolving the approval; the refresh then reads it back.
 const refresh=vi.fn(()=>view.rerender(<Chat session={session({mode:'agent'})} {...props} onRefresh={refresh}/>));
 view=render(<Chat session={waiting} {...props} onRefresh={refresh}/>);
 expect(screen.getByRole('button',{name:'Session mode'})).toHaveTextContent('Plan');
 fireEvent.click(await screen.findByRole('button',{name:'Approve plan'}));
 await waitFor(()=>expect(refresh).toHaveBeenCalled());
 expect(vi.mocked(gql).mock.calls.some(([query,variables])=>String(query).includes('resolve_agent_approval')&&(variables as {input:{decision:string}}).input.decision==='approved')).toBe(true);
 expect(screen.getByRole('button',{name:'Session mode'})).toHaveTextContent('Agent');
});
