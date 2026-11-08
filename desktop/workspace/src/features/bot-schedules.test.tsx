import {cleanup,fireEvent,render,screen,waitFor,within} from '@testing-library/react';
import {afterEach,beforeEach,expect,it,vi} from 'vitest';
import Managers from './Managers';
import {request} from '../lib/api';
import {expectNoAxeViolations} from '../test-axe';
vi.mock('../lib/api',()=>({request:vi.fn(),json:(method:string,value:unknown)=>({method,body:JSON.stringify(value)})}));
const mocked=vi.mocked(request);
afterEach(cleanup);
beforeEach(()=>{mocked.mockReset();history.replaceState(null,'','/')});

const deny=['net.outbound:any','git.publish','package.publish','external.submit','computer.control'];
const bot=(extra:Record<string,unknown>={})=>({
 id:'b1',revision:3,name:'Repo watcher',state:'active',template:'repo_watcher',description:'Watches the repo.',instructions:'Watch.',
 project_id:null,conversation_id:'c1',custom_agent_id:'agent.bot-1',model:null,provider_id:null,tools:['read_file'],approval_mode:'standard',sandbox_profile:'agent',limits:{},
 ceiling:{tools:['read_file'],approval_mode:'standard',sandbox_profile:'agent',deny_capabilities:deny},peers:[],
 effective:{tools:['read_file'],approval_mode:'standard',sandbox_profile:'agent',deny_capabilities:deny},
 violations:[],rules:[],needs_you:0,active_tasks:0,last_task:null,created_by:'user',config_changed_at:null,created_at:1,updated_at:2,...extra});
const item=(kind:string,extra:Record<string,unknown>={})=>({id:kind+':1',kind,bot_id:'b1',bot_name:'Repo watcher',title:'',detail:'',scheduled:false,task_id:null,approval_id:null,run_id:null,schedule_id:null,memory_id:null,created_at:1700000000,...extra});
const approval=item('approval',{title:'Run git status',detail:'Reads the repository state.',task_id:'t1',approval_id:'a1',scheduled:true});
const run=item('run',{title:'A scheduled run needs your attention',detail:'Task ledger unavailable',run_id:'r1',schedule_id:'s1',scheduled:true});
const parked=item('schedule',{title:'A schedule is paused until you re-authorize it',detail:'The prompt changed.',schedule_id:'s1',scheduled:true});
const memory=item('memory',{title:'The bot proposed a memory note',detail:'Prefers short reports',memory_id:'m1'});
const schedule=(extra:Record<string,unknown>={})=>({id:'s1',revision:2,bot_id:'b1',conversation_id:'c1',prompt:'Check the repo',enabled:true,state:'enabled',reason:null,next_run:1800000000,max_runs:10,runs_started:0,grant_expires_at:1900000000,needs_reauthorization:false,paused_by_bot:false,...extra});
const row=(id:string,goal:string,prompt:string)=>({id,revision:1,goal_id:goal,grant_id:'g'+id,prompt,spec:{kind:'interval',seconds:3600},enabled:true,state:'enabled',next_run:1800000000,missed:'skip',overlap:'skip'});

type Body={query?:string;variables?:Record<string,any>};
interface World { bots:ReturnType<typeof bot>[]; inbox:ReturnType<typeof item>[]; schedules:ReturnType<typeof schedule>[]; failReauthorize?:boolean }
function serve(world:World){
 mocked.mockImplementation((async(path:string,init?:RequestInit)=>{
  if(path==='/auth/me')return {principal:{id:'owner',display_name:'Owner',scopes:['host-admin']},session_id:'s1'};
  if(path.startsWith('/api/workspace/automations'))return {schedule:[row('s1','goal1','Check the repo'),row('s2','goal2','Someone else’s task')],schedule_run:[],
   goal:[{id:'goal1',conversation_id:'c1',success_criteria:'Report',runs_started:0,max_runs:10},{id:'goal2',conversation_id:'c2',success_criteria:'Other',runs_started:0,max_runs:10}],delegation:[]};
  if(/^\/api\/workspace\/sessions(\?|$)/.test(path))return {sessions:[{id:'c1',title:'Repo watcher'},{id:'c2',title:'Other chat'}]};
  if(path==='/graphql'){
   const body:Body=JSON.parse(String(init!.body));
   const q=body.query||'';
   if(q.includes('reauthorize_schedules')){
    if(world.failReauthorize)return {errors:[{message:'A schedule has no runs left to authorize.'}]};
    world.schedules=world.schedules.map(value=>({...value,needs_reauthorization:false,enabled:true,reason:null}));
    world.inbox=world.inbox.filter(value=>value.kind!=='schedule');
    world.bots=world.bots.map(value=>({...value,needs_you:0}));
    return {data:{reauthorize_schedules:world.schedules}};
   }
   if(q.includes('acknowledge_schedule_run')){world.inbox=world.inbox.filter(value=>value.kind!=='run');return {data:{acknowledge_schedule_run:{id:'r1'}}}}
   if(q.includes('resolve_memory_proposal')){world.inbox=world.inbox.filter(value=>value.kind!=='memory');return {data:{resolve_memory_proposal:null}}}
   if(q.includes('schedules(conversation_id'))return {data:{schedules:world.schedules}};
   if(q.includes('bot_templates'))return {data:{bots:world.bots,bot_templates:[],projects:[],needs_you:world.inbox}};
   if(q.startsWith('query { bots'))return {data:{bots:world.bots,needs_you:world.inbox}};
   return {data:{}};
  }
  return {};
 }) as typeof request);
}
const calls=()=>mocked.mock.calls.filter(([path])=>path==='/graphql').map(([,init])=>JSON.parse(String(init!.body)) as Body);
const sent=(name:string)=>calls().filter(body=>body.query?.includes(name));
const open=()=>render(<Managers embedded section="bots" projectId={null} sessionId={null}/>);

it('shows only this bot’s waiting items on its Overview, without repeating the bot name',async()=>{
 serve({bots:[bot({needs_you:1}),bot({id:'b2',name:'Other bot',conversation_id:'c2'})],inbox:[memory,item('memory',{id:'memory:2',bot_id:'b2',bot_name:'Other bot',memory_id:'m2',detail:'Not yours'})],schedules:[]});
 open();
 fireEvent.click(await screen.findByRole('button',{name:/Repo watcher/}));
 const inbox=within(await screen.findByRole('region',{name:'Needs you'}));
 expect(inbox.getByText('Prefers short reports')).toBeInTheDocument();
 expect(inbox.queryByText('Not yours')).toBeNull();
 expect(inbox.queryByText(/Repo watcher/)).toBeNull();
});

it('lists everything waiting on the owner above the bots and answers each item in place',async()=>{
 const world:World={bots:[bot({needs_you:4})],inbox:[approval,run,parked,memory],schedules:[]};
 serve(world);
 const {container}=open();
 const inbox=within(await screen.findByRole('region',{name:'Needs you'}));
 expect(inbox.getAllByRole('listitem')).toHaveLength(4);
 expect(inbox.getByText('4 need you')).toBeInTheDocument();
 expect(inbox.getByRole('link',{name:'Review approval'})).toHaveAttribute('href','/?session=c1');
 expect(inbox.getByText('Prefers short reports')).toBeInTheDocument();
 await expectNoAxeViolations(container);

 fireEvent.click(inbox.getByRole('button',{name:/^Dismiss run/}));
 await waitFor(()=>expect(sent('acknowledge_schedule_run')).toHaveLength(1));
 expect(sent('acknowledge_schedule_run')[0].variables).toEqual({c:'c1',r:'r1'});
 expect(await screen.findByText('Run dismissed')).toBeInTheDocument();

 fireEvent.click(await screen.findByRole('button',{name:/^Keep memory/}));
 await waitFor(()=>expect(sent('resolve_memory_proposal')).toHaveLength(1));
 expect(sent('resolve_memory_proposal')[0].variables).toEqual({id:'m1',keep:true});
});

it('discards a proposed memory note when asked',async()=>{
 serve({bots:[bot()],inbox:[memory],schedules:[]});
 open();
 fireEvent.click(await screen.findByRole('button',{name:/^Discard memory/}));
 await waitFor(()=>expect(sent('resolve_memory_proposal')[0].variables).toEqual({id:'m1',keep:false}));
});

it('stays out of the way when nothing is waiting',async()=>{
 serve({bots:[bot()],inbox:[],schedules:[]});
 open();
 await screen.findByRole('button',{name:/Repo watcher/});
 expect(screen.queryByRole('region',{name:'Needs you'})).toBeNull();
});

it('opens the Schedules tab from a paused-schedule item, and shows the bot its own items on Overview',async()=>{
 serve({bots:[bot({needs_you:1})],inbox:[parked],schedules:[schedule({needs_reauthorization:true,enabled:false,state:'needs_reauthorization',reason:'The prompt changed.'})]});
 open();
 fireEvent.click(await screen.findByRole('button',{name:'Review schedules'}));
 expect(await screen.findByRole('tab',{name:'Schedules & triggers'})).toHaveAttribute('aria-selected','true');
 expect(location.search).toBe('?bot=b1&tab=schedules');
 expect(await screen.findByRole('region',{name:/Schedules? (is|are) paused|1 schedule is paused/})).toBeInTheDocument();
});

it('opens the next bot on its Overview after leaving a bot some other way than the back button',async()=>{
 serve({bots:[bot({needs_you:1}),bot({id:'b2',name:'Other bot',conversation_id:'c2'})],inbox:[parked],schedules:[schedule({needs_reauthorization:true,enabled:false,state:'needs_reauthorization',reason:'The prompt changed.'})]});
 open();
 fireEvent.click(await screen.findByRole('button',{name:'Review schedules'}));
 expect(await screen.findByRole('tab',{name:'Schedules & triggers'})).toHaveAttribute('aria-selected','true');
 history.pushState(null,'','/');
 fireEvent(window,new PopStateEvent('popstate'));
 fireEvent.click(await screen.findByRole('button',{name:/Other bot/}));
 expect(await screen.findByRole('tab',{name:'Overview'})).toHaveAttribute('aria-selected','true');
});

it('shows only this bot’s schedules and fixes new ones to the bot',async()=>{
 serve({bots:[bot()],inbox:[],schedules:[]});
 open();
 fireEvent.click(await screen.findByRole('button',{name:/Repo watcher/}));
 fireEvent.click(screen.getByRole('tab',{name:'Schedules & triggers'}));
 expect(await screen.findByRole('button',{name:'Check the repo'})).toBeInTheDocument();
 expect(screen.queryByRole('button',{name:'Someone else’s task'})).toBeNull();
 fireEvent.click(screen.getByRole('button',{name:'New scheduled task'}));
 expect(screen.queryByLabelText('Conversation')).toBeNull();
 expect(screen.queryByLabelText('Task type')).toBeNull();
 fireEvent.change(screen.getByLabelText('Success criteria'),{target:{value:'A short report'}});
 fireEvent.change(screen.getByLabelText('Task prompt'),{target:{value:'Look for new commits'}});
 fireEvent.click(screen.getByLabelText(/Allow unattended runs/));
 fireEvent.click(screen.getByRole('button',{name:'Authorize and create schedule'}));
 await waitFor(()=>expect(mocked.mock.calls.some(([path,init])=>path==='/api/workspace/automations'&&init?.method==='POST')).toBe(true));
 const call=mocked.mock.calls.find(([path,init])=>path==='/api/workspace/automations'&&init?.method==='POST')!;
 expect(JSON.parse(String(call[1]!.body))).toMatchObject({conversation_id:'c1',prompt:'Look for new commits'});
 expect(JSON.parse(String(call[1]!.body)).command).toBeUndefined();
 expect(JSON.parse(String(call[1]!.body)).browser_permission).toBeUndefined();
});

it('explains why schedules paused and re-authorizes them after the owner confirms',async()=>{
 const world:World={bots:[bot()],inbox:[],schedules:[schedule({needs_reauthorization:true,enabled:false,state:'needs_reauthorization',
  reason:'This bot’s prompt or permissions changed after you authorized this schedule, so it is paused.'})]};
 serve(world);
 const {container}=open();
 fireEvent.click(await screen.findByRole('button',{name:/Repo watcher/}));
 fireEvent.click(screen.getByRole('tab',{name:'Schedules & triggers'}));
 const banner=within(await screen.findByRole('region',{name:'1 schedule is paused until you re-authorize it'}));
 expect(banner.getByText(/prompt or permissions changed/)).toBeInTheDocument();
 expect(banner.getByText(/cannot widen its own authority/)).toBeInTheDocument();
 await expectNoAxeViolations(container);

 fireEvent.click(banner.getByRole('button',{name:'Re-authorize schedules'}));
 const dialog=within(await screen.findByRole('dialog'));
 expect(dialog.getByText(/will run 1 schedule on its own as you/)).toBeInTheDocument();
 expect(dialog.getByText('Check the repo')).toBeInTheDocument();
 expect(sent('reauthorize_schedules')).toHaveLength(0); // nothing is granted until the owner confirms
 fireEvent.change(dialog.getByLabelText('Valid for'),{target:{value:'7'}});
 await expectNoAxeViolations(document.body);
 fireEvent.click(dialog.getByRole('button',{name:'Re-authorize schedules'}));
 await waitFor(()=>expect(sent('reauthorize_schedules')).toHaveLength(1));
 expect(sent('reauthorize_schedules')[0].variables).toEqual({c:'c1',days:7});
 await waitFor(()=>expect(screen.queryByRole('region',{name:/schedules? (is|are) paused/})).toBeNull());
 expect(screen.queryByRole('dialog')).toBeNull();
});

it('drops the header “needs you” chip as soon as the schedules are re-authorized',async()=>{
 const world:World={bots:[bot({needs_you:1})],inbox:[parked],schedules:[schedule({needs_reauthorization:true,enabled:false,state:'needs_reauthorization',reason:'Changed.'})]};
 serve(world);
 open();
 fireEvent.click(await screen.findByRole('button',{name:/Repo watcher/}));
 const head=document.querySelector('.bot-detail-head') as HTMLElement;
 expect(within(head).getByText('1 needs you')).toBeInTheDocument();
 fireEvent.click(screen.getByRole('tab',{name:'Schedules & triggers'}));
 fireEvent.click(await screen.findByRole('button',{name:'Re-authorize schedules'}));
 fireEvent.click(within(await screen.findByRole('dialog')).getByRole('button',{name:'Re-authorize schedules'}));
 await waitFor(()=>expect(within(head).queryByText('1 needs you')).toBeNull());
});

it('keeps the dialog open and says what went wrong when re-authorizing fails',async()=>{
 serve({bots:[bot()],inbox:[],schedules:[schedule({needs_reauthorization:true,enabled:false,state:'needs_reauthorization',reason:'Changed.'})],failReauthorize:true});
 open();
 fireEvent.click(await screen.findByRole('button',{name:/Repo watcher/}));
 fireEvent.click(screen.getByRole('tab',{name:'Schedules & triggers'}));
 fireEvent.click(await screen.findByRole('button',{name:'Re-authorize schedules'}));
 const dialog=within(await screen.findByRole('dialog'));
 fireEvent.click(dialog.getByRole('button',{name:'Re-authorize schedules'}));
 expect(await dialog.findByText(/no runs left/)).toHaveAttribute('role','alert');
 expect(screen.getByRole('dialog')).toBeInTheDocument();
});

it('tells the owner a paused bot’s schedules return when the bot resumes, and blocks re-authorizing until then',async()=>{
 serve({bots:[bot({state:'paused'})],inbox:[],schedules:[
  schedule({id:'s1',enabled:false,state:'disabled',paused_by_bot:true}),
  schedule({id:'s3',needs_reauthorization:true,enabled:false,state:'needs_reauthorization',reason:'Changed.'})]});
 open();
 fireEvent.click(await screen.findByRole('button',{name:/Repo watcher/}));
 fireEvent.click(screen.getByRole('tab',{name:'Schedules & triggers'}));
 expect(await screen.findByText(/1 schedule will turn back on when you resume this bot/)).toBeInTheDocument();
 expect(screen.getByRole('button',{name:'Re-authorize schedules'})).toBeDisabled();
 expect(screen.getByText('Resume the bot first.')).toBeInTheDocument();
});
