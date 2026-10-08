import {afterEach,beforeEach,expect,it,vi} from 'vitest';
import {act,cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import PromptQueue from './PromptQueue';
import Chat from './Chat';
import {request,gql,type Session} from '../lib/api';
import {sessionDrafts,consumedTransfers} from '../lib/drafts';
vi.mock('../lib/api',()=>({request:vi.fn(),gql:vi.fn(),json:(method:string,data:unknown)=>({method,body:JSON.stringify(data)})}));
vi.mock('./VoiceControls',()=>({default:()=>null}));
const api=vi.mocked(request),graph=vi.mocked(gql),error=vi.fn();
const session={id:'conversation',title:'Current work',project_id:'project',cwd:'/project',engine:'internal',model:'fixture',provider_id:'fixture',mode:'agent',pinned:false,archived:false,revision:1,updated_at:1,scroll:0,draft_text:'',turns:[{id:'turn',prompt:'Active work',sequence:1,task:{id:'active-task',prompt:'Active work',status:'running'}}]} as Session;
beforeEach(()=>{api.mockReset();graph.mockReset();graph.mockResolvedValue({engine_models:[]});error.mockReset();sessionDrafts.clear();consumedTransfers.clear();api.mockImplementation(async(path)=>path.endsWith('/queue')?{items:[]}:path.includes('presets')?{presets:[]}:path.includes('available-extensions')?{extensions:[]}:path.includes('/runners')?{runners:[]}:path.includes('/capabilities')?{accounts:[]}:session);vi.stubGlobal('ResizeObserver',class{observe(){}unobserve(){}disconnect(){}})});
afterEach(()=>{cleanup();vi.unstubAllGlobals();vi.restoreAllMocks()});
const props={session,engines:[],providers:[],onSelect:vi.fn(),onRefresh:vi.fn(),onError:error};
it('names queue and interrupt separately, binds the exact snapshot and preserves edits arriving during queued admission',async()=>{
 let resolve!:(value:unknown)=>void;api.mockImplementation(async(path,init)=>path.endsWith('/queue')&&init?.method==='POST'?new Promise(value=>{resolve=value}):path.endsWith('/queue')?{items:[]}:path.includes('presets')?{presets:[]}:path.includes('available-extensions')?{extensions:[]}:session);
 render(<Chat {...props}/>);const input=screen.getByRole('textbox',{name:'Message'});fireEvent.change(input,{target:{value:'Queued original'}});fireEvent.click(screen.getByRole('button',{name:'Queue next'}));await waitFor(()=>expect(resolve).toBeTypeOf('function'));
 fireEvent.change(input,{target:{value:'New unrelated draft'}});resolve({id:'queued-one',status:'queued'});await waitFor(()=>expect(screen.getByRole('button',{name:'Queue next'})).toBeEnabled());
 expect(input).toHaveValue('New unrelated draft');const sent=api.mock.calls.find(([path,init])=>path.endsWith('/queue')&&init?.method==='POST');expect(JSON.parse(String(sent?.[1]?.body))).toMatchObject({prompt:'Queued original'});expect(api.mock.calls.some(([path])=>path.endsWith('/turns'))).toBe(false);
});
it('interrupt requires an explicit consequence confirmation and names the original active task',async()=>{
 render(<Chat {...props}/>);fireEvent.change(screen.getByRole('textbox',{name:'Message'}),{target:{value:'Do this instead'}});fireEvent.click(screen.getByRole('button',{name:'Interrupt current task'}));expect(api.mock.calls.some(([,init])=>init?.method==='POST')).toBe(false);
 fireEvent.click(screen.getByRole('button',{name:'Stop current task and queue draft'}));await waitFor(()=>expect(api.mock.calls.some(([path,init])=>path.endsWith('/queue')&&init?.method==='POST')).toBe(true));
 const sent=api.mock.calls.find(([path,init])=>path.endsWith('/queue')&&init?.method==='POST');expect(JSON.parse(String(sent?.[1]?.body))).toMatchObject({prompt:'Do this instead',interrupt_task_id:'active-task'});
});
it('blocked queue reviews old and live settings before granting a renewed exact consent',async()=>{
 const item={id:'queue-item',revision:4,prompt:'Exact queued prompt',status:'blocked',reason:'Model changed',target:{model:'old'},target_digest:'old',expires_at:2000};
 api.mockImplementation(async(path)=>path.endsWith('/queue')?{items:[item]}:path.endsWith('/review')?{target:{model:'new'},target_digest:'new-digest'}:{});
 render(<PromptQueue sessionId="conversation" changed={0} onError={error} onRefresh={()=>{}}/>);await screen.findByText('Exact queued prompt');fireEvent.click(screen.getByRole('button',{name:'Review before continuing'}));await screen.findByRole('dialog');expect(screen.getByRole('region',{name:'Original queued settings'})).toHaveTextContent('old');expect(screen.getByRole('region',{name:'Current queued settings'})).toHaveTextContent('new');expect(api.mock.calls.some(([,init])=>init?.method==='POST')).toBe(false);
 fireEvent.click(screen.getByRole('button',{name:'Continue with these settings'}));await waitFor(()=>expect(api.mock.calls.some(([path])=>path.endsWith('/renew'))).toBe(true));expect(JSON.parse(String(api.mock.calls.find(([path])=>path.endsWith('/renew'))?.[1]?.body))).toEqual({revision:4,target_digest:'new-digest'});expect(error).not.toHaveBeenCalled();
});
it('late queue responses from a prior conversation do not replace the current list',async()=>{
 let resolve!:(value:unknown)=>void;api.mockImplementation(async(path)=>path.includes('/old/')?new Promise(value=>{resolve=value}):{items:[]});
 const view=render(<PromptQueue sessionId="old" changed={0} onError={error} onRefresh={()=>{}}/>);view.rerender(<PromptQueue sessionId="new" changed={0} onError={error} onRefresh={()=>{}}/>);
 await act(async()=>resolve({items:[{id:'old',revision:1,prompt:'Foreign old response',status:'queued',expires_at:2000}]}));expect(screen.queryByText('Foreign old response')).not.toBeInTheDocument();
});
it('shows Jump to latest only when the reader moves away, and returns focus to the log',async()=>{
 const history={...session,id:'history',turns:Array.from({length:60},(_,i)=>({id:'turn-'+i,prompt:'Prompt '+i,sequence:i+1,task:{id:'task-'+i,prompt:'Prompt '+i,result:'Answer '+i,status:'completed'}}))};
 render(<Chat {...props} session={history}/>);const log=screen.getByRole('log');Object.defineProperties(log,{scrollHeight:{configurable:true,value:5000},clientHeight:{configurable:true,value:500},scrollTop:{configurable:true,writable:true,value:200}});fireEvent.scroll(log);
 const jump=await screen.findByRole('button',{name:'Jump to latest'});fireEvent.click(jump);expect(log).toHaveFocus();expect(screen.queryByRole('button',{name:'Jump to latest'})).not.toBeInTheDocument();
});

it('retains cancelled and dispatched follow-ups in collapsed history without replaying them',async()=>{
 const inspect=vi.fn();api.mockResolvedValue({items:[{id:'cancelled',prompt:'Cancelled prompt',status:'cancelled',target:{}},{id:'done',prompt:'Dispatched prompt',status:'dispatched',task_id:'task-exact',target:{model:'original'}}]});
 render(<PromptQueue sessionId="conversation" changed={0} onError={error} onRefresh={()=>{}} onTask={inspect}/>);const summary=await screen.findByText('Follow-up history · 2');expect(summary.closest('details')).not.toHaveAttribute('open');fireEvent.click(summary);expect(screen.getByText('Cancelled prompt')).toBeInTheDocument();fireEvent.click(screen.getByRole('button',{name:'Inspect follow-up task'}));expect(inspect).toHaveBeenCalledWith('task-exact');expect(api.mock.calls.some(([,init])=>init?.method==='POST')).toBe(false);
});

it('a pending cancel from an earlier A→B→A mount cannot strand busy or alter the new queue',async()=>{
 let release!:(value:unknown)=>void;const item={id:'item',revision:1,prompt:'Queue A',status:'queued',expires_at:2000,target:{}};
 api.mockImplementation(async(path,init)=>init?.method==='DELETE'?new Promise(value=>{release=value}):{items:[item]});
 const props={changed:0,onError:error,onRefresh:vi.fn()};const view=render(<PromptQueue {...props} sessionId="A"/>);await screen.findByRole('button',{name:'Cancel queued follow-up'});fireEvent.click(screen.getByRole('button',{name:'Cancel queued follow-up'}));expect(screen.getByRole('button',{name:'Cancel queued follow-up'})).toBeDisabled();view.rerender(<PromptQueue {...props} sessionId="B"/>);view.rerender(<PromptQueue {...props} sessionId="A"/>);await waitFor(()=>expect(screen.getByRole('button',{name:'Cancel queued follow-up'})).toBeEnabled());await act(async()=>release({cancelled:true}));expect(props.onRefresh).not.toHaveBeenCalled();expect(error).not.toHaveBeenCalled();
});
