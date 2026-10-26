import {afterEach,expect,it,vi} from 'vitest';
import {act,cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import Chat from './Chat';
import ChatWelcome from './ChatWelcome';
import {draftSubmissionOwners,draftWrites,sessionDrafts,submittingDrafts} from '../lib/drafts';
import {request,type Session,type Provider} from '../lib/api';

vi.mock('../lib/api',()=>({gql:vi.fn(async()=>({engine_models:[]})),request:vi.fn(),json:(method:string,value:unknown)=>({method,body:JSON.stringify(value)})}));
afterEach(()=>{cleanup();sessionDrafts.clear();draftWrites.clear();submittingDrafts.clear();draftSubmissionOwners.clear();vi.clearAllMocks()});
const row=(id:string,active=false):Session=>({id,title:id,engine:'internal',mode:'agent',pinned:false,archived:false,draft_text:'',revision:1,updated_at:1,scroll:0,turns:active?[{id:id+'-turn',prompt:'Current task',sequence:1,task:{id:id+'-task',prompt:'Current task',status:'running',events:[]}}]:[]});
const props={engines:[],providers:[],onSelect:vi.fn(),onRefresh:vi.fn(),onError:vi.fn()};
function baseResponse(path:string,session:Session){if(path==='/api/runners')return [];if(path.includes('runner-agents'))return {accounts:[]};if(path.endsWith('/queue'))return {items:[]};return {...session}}

it('starts with goals and inserts a suggestion into the draft without dispatching it',()=>{
 const session=row('welcome');vi.mocked(request).mockImplementation(async(path)=>baseResponse(path,session) as any);
 render(<Chat session={session} {...props}/>);
 expect(screen.getByRole('heading',{name:'What can we work on?'})).toBeVisible();
 fireEvent.click(screen.getByRole('button',{name:/Make a plan/}));
 expect(screen.getByRole('textbox',{name:'Message'})).toHaveValue('Help me turn my goal into a practical plan. Ask what you need to know first.');
 expect(screen.getByRole('textbox',{name:'Message'})).toHaveFocus();
 expect(vi.mocked(request).mock.calls.some(([path])=>path.endsWith('/turns'))).toBe(false);
});

it('offers connection setup while preserving a typed first message',()=>{
 const session=row('connection'),configure=vi.fn();vi.mocked(request).mockImplementation(async(path)=>baseResponse(path,session) as any);
 render(<Chat session={session} {...props} onConfigureAgent={configure}/>);
 fireEvent.change(screen.getByRole('textbox',{name:'Message'}),{target:{value:'My first task'}});
 fireEvent.click(screen.getByRole('button',{name:'Set up connection'}));
 expect(configure).toHaveBeenCalledOnce();expect(screen.getByRole('textbox',{name:'Message'})).toHaveValue('My first task');
});

it('does not require a project in the conversation entry state',()=>{
 render(<ChatWelcome session={null} engines={[]} providers={[]} onPrompt={vi.fn()}/>);
 expect(screen.getByRole('heading',{name:'Start with a conversation'})).toBeVisible();expect(screen.queryByText(/choose a project/i)).toBeNull();
});

it('keeps an uncertain send request identity and exact payload across a panel remount',async()=>{
 const session={...row('uncertain'),draft_text:'Original instruction'};let attempts=0;
 vi.mocked(request).mockImplementation(async(path)=>{if(path.endsWith('/turns')){if(attempts++===0)throw Error('Connection dropped after dispatch');return {id:'same-task'}}return baseResponse(path,session) as any});
 const panel=render(<Chat session={session} {...props}/>);fireEvent.click(screen.getByRole('button',{name:'Send message'}));await screen.findByRole('button',{name:'Retry send'});
 const first=JSON.parse(vi.mocked(request).mock.calls.find(([path])=>path.endsWith('/turns'))![1]!.body as string);
 panel.unmount();render(<Chat session={session} {...props}/>);
 expect(screen.getByRole('button',{name:'Retry send'})).toBeVisible();
 fireEvent.change(screen.getByRole('textbox',{name:'Message'}),{target:{value:'New unsent instruction'}});
 fireEvent.click(screen.getByRole('button',{name:'Retry send'}));
 await waitFor(()=>expect(vi.mocked(request).mock.calls.filter(([path])=>path.endsWith('/turns'))).toHaveLength(2));
 const second=JSON.parse(vi.mocked(request).mock.calls.filter(([path])=>path.endsWith('/turns'))[1][1]!.body as string);
 expect(second).toEqual(first);expect(screen.getByRole('textbox',{name:'Message'})).toHaveValue('New unsent instruction');
 await waitFor(()=>expect(sessionDrafts.get(session.id)?.submission).toBeUndefined());
});

it('shows a queue action when writing during a task and dispatches a follow-up once',async()=>{
 const session=row('followup',true);vi.mocked(request).mockImplementation(async(path)=>baseResponse(path,session) as any);
 render(<Chat session={session} {...props}/>);expect(screen.queryByRole('button',{name:'Queue follow-up'})).toBeNull();
 fireEvent.change(screen.getByRole('textbox',{name:'Message'}),{target:{value:'Also check accessibility'}});
 fireEvent.click(screen.getByRole('button',{name:'Queue follow-up'}));
 await waitFor(()=>expect(vi.mocked(request).mock.calls.filter(([path,init])=>path.endsWith('/queue')&&init?.method==='POST')).toHaveLength(1));
 expect(vi.mocked(request).mock.calls.some(([path])=>path.endsWith('/turns'))).toBe(false);
});

it('shows cancellation progress and rejects repeated stop clicks until it completes',async()=>{
 const session=row('stop',true);let release!:(value:unknown)=>void;
 vi.mocked(request).mockImplementation(async(path)=>path.endsWith('/cancel')?await new Promise(resolve=>release=resolve):baseResponse(path,session) as any);
 render(<Chat session={session} {...props}/>);
 const stop=screen.getByRole('button',{name:'Stop task'});fireEvent.click(stop);fireEvent.click(stop);
 expect(stop).toBeDisabled();expect(screen.getByText('Stopping task and its delegated agents…')).toBeVisible();
 expect(vi.mocked(request).mock.calls.filter(([path])=>path.endsWith('/cancel'))).toHaveLength(1);
 await act(async()=>release({cancelled:true}));expect(props.onRefresh).toHaveBeenCalled();
});

it('does not block another conversation while a previous dispatch is in flight',async()=>{
 const a={...row('dispatch-a'),draft_text:'Task A'},b={...row('dispatch-b'),draft_text:'Task B'};let release!:(value:unknown)=>void;
 vi.mocked(request).mockImplementation(async(path)=>path.includes('/dispatch-a/turns')?await new Promise(resolve=>release=resolve):baseResponse(path,path.includes('/dispatch-a')?a:b) as any);
 const panel=render(<Chat session={a} {...props}/>);fireEvent.click(screen.getByRole('button',{name:'Send message'}));
 await waitFor(()=>expect(submittingDrafts.has(a.id)).toBe(true));panel.rerender(<Chat session={b} {...props}/>);
 expect(screen.getByRole('button',{name:'Send message'})).toBeEnabled();fireEvent.click(screen.getByRole('button',{name:'Send message'}));
 await waitFor(()=>expect(vi.mocked(request).mock.calls.some(([path])=>path.includes('/dispatch-b/turns'))).toBe(true));
 await act(async()=>release({id:'task-a'}));
});

it('clears a successful in-flight dispatch after the sending panel was replaced',async()=>{
 const session={...row('dispatch-remount'),draft_text:'Task after replacement'};let release!:(value:unknown)=>void;
 vi.mocked(request).mockImplementation(async(path)=>path.endsWith('/turns')?await new Promise(resolve=>release=resolve):baseResponse(path,session) as any);
 const panel=render(<Chat session={session} {...props}/>);fireEvent.click(screen.getByRole('button',{name:'Send message'}));
 await waitFor(()=>expect(submittingDrafts.has(session.id)).toBe(true));panel.unmount();render(<Chat session={session} {...props}/>);
 expect(screen.getByRole('button',{name:'Send message'})).toBeDisabled();
 await act(async()=>release({id:'same-task'}));
 await waitFor(()=>expect(screen.queryByRole('button',{name:'Retry send'})).toBeNull());
 expect(submittingDrafts.has(session.id)).toBe(false);expect(screen.getByRole('textbox',{name:'Message'})).toHaveValue('');
});

it('retains an in-flight stop guard when the chat panel is replaced',async()=>{
 const session=row('stop-remount',true);let release!:(value:unknown)=>void;
 vi.mocked(request).mockImplementation(async(path)=>path.endsWith('/cancel')?await new Promise(resolve=>release=resolve):baseResponse(path,session) as any);
 const panel=render(<Chat session={session} {...props}/>);fireEvent.click(screen.getByRole('button',{name:'Stop task'}));
 panel.unmount();render(<Chat session={session} {...props}/>);expect(screen.getByRole('button',{name:'Stop task'})).toBeDisabled();
 await act(async()=>release({cancelled:true}));await waitFor(()=>expect(screen.getByRole('button',{name:'Stop task'})).toBeEnabled());
 expect(vi.mocked(request).mock.calls.filter(([path])=>path.endsWith('/cancel'))).toHaveLength(1);
});

it('keeps queue admission distinct from retrying an uncertain send while the original task runs',async()=>{const session={...row('pending-send-queue',true),draft_text:'Newer follow-up'};sessionDrafts.set(session.id,{text:session.draft_text,saved:session.draft_text,dirty:false,submission:{prompt:'Original instruction',key:'original-request',intent:'send',context:[],attachments:[]}});vi.mocked(request).mockImplementation(async(path)=>baseResponse(path,session) as any);render(<Chat session={session} {...props}/>);expect(screen.getByRole('button',{name:'Retry send'})).toBeVisible();expect(screen.queryByRole('button',{name:'Queue follow-up'})).toBeNull();fireEvent.click(screen.getByRole('button',{name:'Running task actions'}));expect(screen.getByRole('button',{name:'Queue next'})).toBeDisabled();expect(screen.getByRole('button',{name:'Interrupt current task'})).toBeDisabled();expect(vi.mocked(request).mock.calls.some(([path,init])=>path.endsWith('/queue')&&init?.method==='POST')).toBe(false)});

it('keeps the main failure review visible after a later direct child report',()=>{
 const session=row('failed-before-report');
 session.turns=[{id:'failed-turn',prompt:'Original main task',sequence:1,task:{id:'failed-task',prompt:'Original main task',status:'failed',error:'Original failure'}},{id:'source-report',prompt:'',sequence:2,task:{id:'report-task',prompt:'',status:'completed',engine:'coordination',coordination_report:{kind:'task.completed',label:'Child agent',source_status:'completed'}}}];
 vi.mocked(request).mockImplementation(async(path)=>baseResponse(path,session) as any);
 render(<Chat session={session} {...props}/>);
 expect(screen.getByRole('button',{name:'Review current task'})).toHaveTextContent('Review error');
});


const connectedAccount:Provider={id:'connected-account',name:'Connected account',model:'model-one, model-two',secret_configured:true,capabilities:['functions']};

it('connects a newly saved account to the same main chat explicitly and sends its preserved draft',async()=>{
 const session={...row('first-main'),draft_text:'Keep this first task'};
 vi.mocked(request).mockImplementation(async(path,init)=>{
  if(path==='/api/workspace/sessions/first-main'&&init?.method==='PATCH'){Object.assign(session,JSON.parse(String(init.body)).changes);session.revision++;return {...session} as any}
  if(path.endsWith('/turns'))return {id:'first-task'} as any;
  return baseResponse(path,session) as any;
 });
 const view=render(<Chat session={{...session}} {...props} coordinating/>);
 const composer=screen.getByRole('textbox',{name:'Message'});
 expect(screen.getByRole('button',{name:'Send message'})).toBeDisabled();
 fireEvent.keyDown(composer,{key:'Enter',code:'Enter'});
 expect(sessionDrafts.get(session.id)?.submission).toBeUndefined();
 expect(vi.mocked(request).mock.calls.some(([path])=>path.endsWith('/turns'))).toBe(false);
 view.rerender(<Chat session={{...session}} {...props} providers={[connectedAccount]} coordinating/>);
 expect(screen.getByRole('combobox',{name:'Chat account'})).toHaveValue(connectedAccount.id);
 expect(screen.getByRole('combobox',{name:'Chat model'})).toHaveValue('model-one');
 expect(session.provider_id).toBeUndefined();
 expect(vi.mocked(request).mock.calls.some(([,init])=>init?.method==='PATCH')).toBe(false);
 fireEvent.change(screen.getByRole('combobox',{name:'Chat model'}),{target:{value:'model-two'}});
 fireEvent.click(screen.getByRole('button',{name:'Use for this chat'}));
 await waitFor(()=>expect(session.model).toBe('model-two'));
 const update=vi.mocked(request).mock.calls.find(([,init])=>init?.method==='PATCH')!;
 expect(update[0]).toBe('/api/workspace/sessions/first-main');
 expect(JSON.parse(String(update[1]?.body))).toEqual({revision:1,changes:{provider_id:connectedAccount.id,model:'model-two',reasoning_config:{}}});
 view.rerender(<Chat session={{...session}} {...props} providers={[connectedAccount]} coordinating/>);
 expect(screen.queryByRole('region',{name:'Connect this chat'})).not.toBeInTheDocument();
 expect(composer).toHaveValue('Keep this first task');
 expect(screen.getByRole('button',{name:'Send message'})).toBeEnabled();
 fireEvent.click(screen.getByRole('button',{name:'Send message'}));
 await waitFor(()=>expect(vi.mocked(request).mock.calls.filter(([path])=>path.endsWith('/turns'))).toHaveLength(1));
 const sent=vi.mocked(request).mock.calls.find(([path])=>path.endsWith('/turns'))!;
 expect(sent[0]).toBe('/api/workspace/sessions/first-main/turns');
 expect(JSON.parse(String(sent[1]?.body)).prompt).toBe('Keep this first task');
});

it('keeps connection repair available after history and preserves a draft when its settings write fails',async()=>{
 const session={...row('repair-main'),draft_text:'Retry my work',provider_id:'removed-account',model:'old-model'};
 session.turns=[{id:'earlier',prompt:'Previous work',sequence:1,task:{id:'earlier-task',prompt:'Previous work',status:'completed',result:'Earlier answer'}}];
 vi.mocked(request).mockImplementation(async(path,init)=>{if(init?.method==='PATCH')throw Error('Settings changed in another window');return baseResponse(path,session) as any});
 render(<Chat session={session} {...props} providers={[connectedAccount]} coordinating/>);
 fireEvent.click(screen.getByRole('button',{name:'Use for this chat'}));
 await screen.findByText('The connection was not saved. Your draft is still here; review the error and try again.');
 expect(screen.getByRole('textbox',{name:'Message'})).toHaveValue('Retry my work');
 expect(screen.getByRole('button',{name:'Use for this chat'})).toBeEnabled();
 expect(screen.getByRole('button',{name:'Send message'})).toBeDisabled();
 expect(sessionDrafts.get(session.id)?.submission).toBeUndefined();
 expect(vi.mocked(request).mock.calls.some(([path])=>path.endsWith('/turns'))).toBe(false);
 expect(props.onError).toHaveBeenCalledWith(expect.objectContaining({message:'Settings changed in another window'}));
});

it('keeps Stop available when a main task is running and its connection is no longer configured',async()=>{
 const session=row('running-without-account',true);
 vi.mocked(request).mockImplementation(async(path)=>baseResponse(path,session) as any);
 render(<Chat session={session} {...props} coordinating/>);
 const stop=screen.getByRole('button',{name:'Stop task'});
 expect(stop).toBeEnabled();fireEvent.click(stop);
 await waitFor(()=>expect(vi.mocked(request).mock.calls.filter(([path])=>path.endsWith('/cancel'))).toHaveLength(1));
});
