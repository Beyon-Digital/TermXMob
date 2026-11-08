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
 id:'b1',revision:3,name:'Alpha',state:'active',template:null,description:'Passes messages.',instructions:'Pass.',
 project_id:null,conversation_id:'c1',custom_agent_id:'agent.bot-1',model:null,provider_id:null,tools:['read_inbox','send_message'],approval_mode:'standard',sandbox_profile:'agent',limits:{},
 ceiling:{tools:['read_inbox','send_message'],approval_mode:'standard',sandbox_profile:'agent',deny_capabilities:deny},peers:[],
 effective:{tools:['read_inbox','send_message'],approval_mode:'standard',sandbox_profile:'agent',deny_capabilities:deny},
 violations:[],rules:[],needs_you:0,active_tasks:0,last_task:null,created_by:'user',config_changed_at:null,created_at:1,updated_at:2,...extra});
const beta=bot({id:'b2',name:'Beta',conversation_id:'c2'});
const party=(kind:string,name:string,id:string|null=null)=>({kind,id,name});
const message=(id:string,extra:Record<string,unknown>={})=>({id,thread_id:'t1',sender:party('bot','Alpha','b1'),recipient:party('orchestrator','Main agent'),kind:'request',subject:'Build status',body:'Is the build green?',
 task_ref:null,source_task_id:null,hops:1,status:'unread',reason:null,automatic:false,in_reply_to:null,created_at:1700000000,updated_at:1700000000,...extra});
const schedule=(extra:Record<string,unknown>={})=>({id:'s1',revision:2,bot_id:'b1',conversation_id:'c1',prompt:'Check the repo',enabled:true,state:'enabled',reason:null,spec:{kind:'interval',seconds:3600},
 trigger:{kind:'time'},report_to:{kind:'orchestrator',id:null},webhook_path:null,webhook_secret:null,next_run:1800000000,max_runs:10,runs_started:0,grant_expires_at:1900000000,needs_reauthorization:false,paused_by_bot:false,...extra});
const needsItem=(extra:Record<string,unknown>={})=>({id:'message:m1',kind:'message',bot_id:'b1',bot_name:'Alpha',title:'Message: Deploy is blocked',detail:'Staging is down.',scheduled:false,
 task_id:null,approval_id:null,run_id:null,schedule_id:null,memory_id:null,message_id:'m1',created_at:1700000000,...extra});

type Body={query?:string;variables?:Record<string,any>};
interface World { bots:ReturnType<typeof bot>[]; messages:ReturnType<typeof message>[]; schedules:ReturnType<typeof schedule>[]; inbox:ReturnType<typeof needsItem>[]; created?:ReturnType<typeof schedule> }
function serve(world:World){
 mocked.mockImplementation((async(path:string,init?:RequestInit)=>{
  if(path==='/auth/me')return {principal:{id:'owner',display_name:'Owner',scopes:['host-admin']},session_id:'s1'};
  if(path.startsWith('/api/workspace/automations'))return {schedule:[],schedule_run:[],goal:[],delegation:[]};
  if(/^\/api\/workspace\/sessions(\?|$)/.test(path))return {sessions:[]};
  if(path==='/graphql'){
   const body:Body=JSON.parse(String(init!.body)),q=body.query||'';
   if(q.includes('send_bot_message')){world.messages=[...world.messages,message('m9',{sender:party('owner','Owner'),recipient:party('bot','Alpha','b1'),subject:body.variables!.i.subject,body:body.variables!.i.body})];return {data:{send_bot_message:world.messages[world.messages.length-1]}}}
   if(q.includes('mark_message')){const {message_id,status}=body.variables!;world.messages=world.messages.map(m=>m.id===message_id?{...m,status}:m);world.inbox=world.inbox.filter(i=>i.message_id!==message_id);return {data:{mark_message:{id:message_id,status}}}}
   if(q.includes('edit_schedule')){const {s,i}=body.variables!;world.schedules=world.schedules.map(row=>row.id===s?{...row,...(i.report_to?{report_to:{kind:i.report_to.split(':')[0],id:i.report_to.split(':')[1]||null}}:{}),...(i.trigger?{trigger:i.trigger}:{}),...(i.prompt?{prompt:i.prompt}:{}),revision:row.revision+1}:row);return {data:{edit_schedule:world.schedules.find(row=>row.id===s)}}}
   if(q.includes('create_schedule')){const {i}=body.variables!;world.created=schedule({id:'s9',prompt:i.prompt,trigger:i.trigger,report_to:{kind:i.report_to||'orchestrator',id:null},spec:{kind:'event'},next_run:null,enabled:i.trigger.kind!=='webhook',
     webhook_path:i.trigger.kind==='webhook'?'/hooks/bots/b1/s9':null,webhook_secret:i.trigger.kind==='webhook'?'SECRET-ONCE-123':null});world.schedules=[...world.schedules,{...world.created,webhook_secret:null}];return {data:{create_schedule:world.created}}}
   if(q.includes('rotate_webhook_secret'))return {data:{rotate_webhook_secret:{...world.schedules.find(row=>row.id===body.variables!.s),webhook_secret:'ROTATED-SECRET-456'}}};
   if(q.includes('set_schedule_enabled')){const {s,enabled}=body.variables!;world.schedules=world.schedules.map(row=>row.id===s?{...row,enabled,state:enabled?'enabled':'disabled',revision:row.revision+1}:row);return {data:{set_schedule_enabled:world.schedules.find(row=>row.id===s)}}}
   if(q.includes('archive_schedule')){const {s}=body.variables!;world.schedules=world.schedules.filter(row=>row.id!==s);return {data:{archive_schedule:{id:s}}}}
   if(q.includes('bot_run_now'))return {data:{bot_run_now:{task_id:'t1',status:'running'}}};
   if(q.includes('bot_messages(bot_id'))return {data:{bot_messages:world.messages,schedules:world.schedules}};
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
async function openTab(name:string){
 fireEvent.click(await screen.findByRole('button',{name:/Alpha/}));
 fireEvent.click(await screen.findByRole('tab',{name}));
}

// -- Messages tab ---------------------------------------------------------------------------------------------

it('groups messages into threads with sender chips and the route they took, and shows text as text',async()=>{
 const html='<img src=x onerror=alert(1)> Is the build green?';
 serve({bots:[bot()],messages:[
  message('m1',{body:html}),
  message('m2',{sender:party('orchestrator','Main agent'),recipient:party('bot','Alpha','b1'),kind:'reply',subject:'Re: Build status',body:'Yes, all green.',hops:2,created_at:1700000100}),
  message('m3',{thread_id:'t2',sender:party('bot','Alpha','b1'),recipient:party('owner','Owner'),kind:'report',subject:'Daily report',body:'Nothing to flag.',automatic:true,created_at:1700001000})],schedules:[],inbox:[]});
 const {container}=open();
 await openTab('Messages');
 const threads=within(await screen.findByRole('list',{name:'Message threads'}));
 expect(threads.getAllByRole('listitem',{})[0]).toHaveTextContent('Daily report');
 const route=within(threads.getByRole('list',{name:'Route of Build status'}));
 expect(route.getAllByRole('listitem').map(node=>node.textContent)).toEqual(['Alpha','Main agent','Alpha']);
 fireEvent.click(threads.getByRole('button',{name:/Build status/}));
 expect(threads.getByText(html,{exact:false})).toBeInTheDocument();
 expect(container.querySelector('img')).toBeNull();
 expect(screen.getByText(/written by agents/i)).toBeInTheDocument();
 await expectNoAxeViolations(container);
});

it('marks a message to the owner read and dismisses a held one with its reason',async()=>{
 const world:World={bots:[bot()],messages:[
  message('m1',{recipient:party('owner','Owner'),subject:'Deploy is blocked',body:'Staging is down.'}),
  message('m2',{thread_id:'t2',status:'parked',reason:'This thread reached its limit of 3 hops, so the message was held.',subject:'Loop',body:'ping'})],schedules:[],inbox:[]};
 serve(world);
 open();
 await openTab('Messages');
 const threads=await screen.findByRole('list',{name:'Message threads'});
 expect(within(threads).getByText(/reached its limit of 3 hops/)).toBeInTheDocument();
 fireEvent.click(within(threads).getByText('Deploy is blocked'));
 fireEvent.click(await screen.findByRole('button',{name:'Mark as read'}));
 await waitFor(()=>expect(sent('mark_message')).toHaveLength(1));
 expect(sent('mark_message')[0].variables).toEqual({message_id:'m1',status:'read'});
 fireEvent.click(within(threads).getByText('Loop'));
 fireEvent.click(await screen.findByRole('button',{name:'Dismiss message'}));
 await waitFor(()=>expect(sent('mark_message')).toHaveLength(2));
 expect(sent('mark_message')[1].variables).toEqual({message_id:'m2',status:'done'});
});

it('says what to do when there are no messages and sends one to the bot',async()=>{
 const world:World={bots:[bot()],messages:[],schedules:[],inbox:[]};
 serve(world);
 open();
 await openTab('Messages');
 expect(await screen.findByText(/No messages yet/)).toBeInTheDocument();
 fireEvent.click(screen.getByRole('button',{name:'Send message'}));
 expect(await screen.findByText('Write a message to send.')).toBeInTheDocument();
 expect(sent('send_bot_message')).toHaveLength(0);
 fireEvent.change(screen.getByLabelText('Subject'),{target:{value:'Heads up'}});
 fireEvent.change(screen.getByLabelText(/^Your message/),{target:{value:'Please re-run the checks.'}});
 fireEvent.click(screen.getByRole('button',{name:'Send message'}));
 await waitFor(()=>expect(sent('send_bot_message')).toHaveLength(1));
 expect(sent('send_bot_message')[0].variables).toEqual({i:{to:'bot:b1',kind:'request',subject:'Heads up',body:'Please re-run the checks.'}});
 expect(await screen.findByText('Message sent')).toBeInTheDocument();
});

it('lets the owner choose where each schedule reports',async()=>{
 const world:World={bots:[bot(),beta],messages:[],schedules:[schedule()],inbox:[]};
 serve(world);
 const {container}=open();
 await openTab('Messages');
 const select=await screen.findByLabelText(/Check the repo/);
 expect(select).toHaveValue('orchestrator');
 expect(within(select).getAllByRole('option').map(option=>option.textContent)).toEqual(['Main agent','You','Beta']);
 fireEvent.change(select,{target:{value:'owner'}});
 await waitFor(()=>expect(sent('edit_schedule')).toHaveLength(1));
 expect(sent('edit_schedule')[0].variables).toEqual({c:'c1',s:'s1',i:{revision:2,report_to:'owner'}});
 await expectNoAxeViolations(container);
});

it('picks where a one-off run reports',async()=>{
 serve({bots:[bot(),beta],messages:[],schedules:[],inbox:[]});
 open();
 fireEvent.click(await screen.findByRole('button',{name:/Alpha/}));
 const select=await screen.findByLabelText('Send the result to');
 expect(select).toHaveValue('owner');
 fireEvent.change(select,{target:{value:'bot:b2'}});
 fireEvent.click(screen.getByRole('button',{name:'Run now'}));
 await waitFor(()=>expect(sent('bot_run_now')).toHaveLength(1));
 expect(sent('bot_run_now')[0].variables).toEqual({id:'b1',report_to:'bot:b2'});
});

// -- Needs you -------------------------------------------------------------------------------------------------

it('names the item each per-row button acts on, and keeps focus on the list after an answer',async()=>{
 const world:World={bots:[bot({needs_you:1})],messages:[],schedules:[],inbox:[needsItem(),needsItem({id:'message:m2',message_id:'m2',title:'Message: Second'})]};
 serve(world);
 open();
 const inbox=within(await screen.findByRole('region',{name:'Needs you'}));
 const dismiss=inbox.getAllByRole('button',{name:'Dismiss message'});
 expect(dismiss[0]).toHaveAccessibleDescription('Message: Deploy is blocked');
 expect(dismiss[1]).toHaveAccessibleDescription('Message: Second');
 dismiss[0].focus();
 fireEvent.click(dismiss[0]);
 await waitFor(()=>expect(sent('mark_message')).toHaveLength(1));
 expect(screen.getByRole('heading',{name:/Needs you/})).toHaveFocus();
});

it('says when the secret could not be copied',async()=>{
 const writeText=vi.fn().mockRejectedValue(new Error('denied'));
 Object.defineProperty(navigator,'clipboard',{value:{writeText},configurable:true});
 serve({bots:[bot()],messages:[],schedules:[],inbox:[]});
 open();
 await openTab('Schedules & triggers');
 fireEvent.click(await screen.findByRole('button',{name:'Add trigger'}));
 const dialog=within(await screen.findByRole('dialog',{name:'Add trigger'}));
 fireEvent.change(dialog.getByLabelText('Starts when'),{target:{value:'webhook'}});
 fireEvent.change(dialog.getByLabelText(/What the bot does/),{target:{value:'Handle deploys'}});
 fireEvent.click(dialog.getByRole('checkbox',{name:/may run on its own/}));
 fireEvent.click(dialog.getByRole('button',{name:'Create trigger'}));
 const reveal=within(await screen.findByRole('dialog',{name:'Webhook created'}));
 fireEvent.click(reveal.getByRole('button',{name:'Copy secret'}));
 expect(await reveal.findByText(/Could not copy\. Select the secret and copy it yourself\./)).toBeInTheDocument();
});

it('names the trigger each button acts on and moves focus to the heading after archiving',async()=>{
 serve({bots:[bot()],messages:[],inbox:[],schedules:[schedule({id:'s2',prompt:'Triage the inbox',trigger:{kind:'message'},spec:{kind:'event'},next_run:null})]});
 open();
 await openTab('Schedules & triggers');
 const list=within(await screen.findByRole('list',{name:'Triggers'}));
 expect(list.getByRole('button',{name:'Edit'})).toHaveAccessibleDescription(/message/i);
 const archive=list.getByRole('button',{name:'Archive trigger'});
 archive.focus();
 fireEvent.click(archive);
 fireEvent.click(within(await screen.findByRole('dialog',{name:'Archive trigger'})).getByRole('button',{name:'Archive trigger'}));
 await waitFor(()=>expect(sent('archive_schedule')).toHaveLength(1));
 await waitFor(()=>expect(screen.getByRole('heading',{name:'Triggers'})).toHaveFocus());
});

it('moves focus to the thread list after a message is dismissed and leaves the report select usable while saving',async()=>{
 const world:World={bots:[bot()],messages:[message('m2',{thread_id:'t2',status:'parked',reason:'Held.',subject:'Loop',body:'ping'})],schedules:[schedule()],inbox:[]};
 serve(world);
 open();
 await openTab('Messages');
 const threads=await screen.findByRole('list',{name:'Message threads'});
 fireEvent.click(within(threads).getByText('Loop'));
 const dismiss=await screen.findByRole('button',{name:'Dismiss message'});
 expect(dismiss).toHaveAccessibleDescription('ping');
 dismiss.focus();
 fireEvent.click(dismiss);
 await waitFor(()=>expect(sent('mark_message')).toHaveLength(1));
 await waitFor(()=>expect(threads.closest('.bot-message-region')).toHaveFocus());
 const select=screen.getByRole('combobox');
 expect(select).not.toBeDisabled();
});

it('lists owner-directed messages in Needs you and dismisses them in place',async()=>{
 const world:World={bots:[bot({needs_you:1})],messages:[],schedules:[],inbox:[needsItem()]};
 serve(world);
 const {container}=open();
 const inbox=within(await screen.findByRole('region',{name:'Needs you'}));
 expect(inbox.getByText('Message: Deploy is blocked')).toBeInTheDocument();
 expect(inbox.getByText('Staging is down.')).toBeInTheDocument();
 await expectNoAxeViolations(container);
 fireEvent.click(inbox.getByRole('button',{name:'Open messages'}));
 expect(await screen.findByRole('tab',{name:'Messages',selected:true})).toBeInTheDocument();
});

it('dismisses a message from Needs you',async()=>{
 const world:World={bots:[bot({needs_you:1})],messages:[],schedules:[],inbox:[needsItem()]};
 serve(world);
 open();
 const inbox=within(await screen.findByRole('region',{name:'Needs you'}));
 fireEvent.click(inbox.getByRole('button',{name:'Dismiss message'}));
 await waitFor(()=>expect(sent('mark_message')).toHaveLength(1));
 expect(sent('mark_message')[0].variables).toEqual({message_id:'m1',status:'done'});
 expect(await screen.findByText('Message dismissed')).toBeInTheDocument();
});

// -- Triggers ----------------------------------------------------------------------------------------------------

it('lists event triggers in plain words under Schedules & triggers',async()=>{
 serve({bots:[bot(),beta],messages:[],inbox:[],schedules:[
  schedule({id:'s2',prompt:'Triage the inbox',trigger:{kind:'message'},spec:{kind:'event'},next_run:null}),
  schedule({id:'s3',prompt:'Summarise Beta',trigger:{kind:'bot_finished',bot_id:'b2',on:'completed'},spec:{kind:'event'},next_run:null}),
  schedule({id:'s4',prompt:'Review changes',trigger:{kind:'file_change',glob:'src/**/*.py',debounce_s:30},spec:{kind:'event'},next_run:null}),
  schedule({id:'s5',prompt:'Handle deploys',trigger:{kind:'webhook'},spec:{kind:'event'},next_run:null,enabled:false,state:'disabled',webhook_path:'/hooks/bots/b1/s5'})]});
 const {container}=open();
 await openTab('Schedules & triggers');
 const list=within(await screen.findByRole('list',{name:'Triggers'}));
 expect(list.getByText('When a message arrives')).toBeInTheDocument();
 expect(list.getByText('When Beta finishes successfully')).toBeInTheDocument();
 expect(list.getByText(/files matching src\/\*\*\/\*\.py change/)).toBeInTheDocument();
 expect(list.getByText('When a signed webhook is called')).toBeInTheDocument();
 expect(list.getByText(`${location.origin}/hooks/bots/b1/s5`)).toBeInTheDocument();
 expect(list.getAllByText('Off').length).toBeGreaterThan(0);
 await expectNoAxeViolations(container);
});

it('creates a message trigger only after the owner confirms the bot may run on its own',async()=>{
 const world:World={bots:[bot(),beta],messages:[],schedules:[],inbox:[]};
 serve(world);
 open();
 await openTab('Schedules & triggers');
 expect(await screen.findByText(/No triggers yet/)).toBeInTheDocument();
 fireEvent.click(screen.getByRole('button',{name:'Add trigger'}));
 const dialog=within(await screen.findByRole('dialog',{name:'Add trigger'}));
 fireEvent.change(dialog.getByLabelText('Starts when'),{target:{value:'message'}});
 fireEvent.click(dialog.getByRole('button',{name:'Create trigger'}));
 expect(await dialog.findByText('Say what the bot should do when this happens.')).toBeInTheDocument();
 expect(dialog.getByText('Confirm that the bot may run on its own.')).toBeInTheDocument();
 expect(sent('create_schedule')).toHaveLength(0);
 fireEvent.change(dialog.getByLabelText(/What the bot does/),{target:{value:'Triage what arrived'}});
 fireEvent.change(dialog.getByLabelText('Send each result to'),{target:{value:'owner'}});
 fireEvent.click(dialog.getByRole('checkbox',{name:/may run on its own/}));
 fireEvent.click(dialog.getByRole('button',{name:'Create trigger'}));
 await waitFor(()=>expect(sent('create_schedule')).toHaveLength(1));
 const input=sent('create_schedule')[0].variables!.i;
 expect(input).toMatchObject({prompt:'Triage what arrived',trigger:{kind:'message'},report_to:'owner',grant_days:30});
 expect(input.request_id).toMatch(/.{8,}/);
 expect(input.spec).toBeUndefined();
 expect(await screen.findByText('Trigger created')).toBeInTheDocument();
});

it('offers the settings each kind needs and sends them',async()=>{
 serve({bots:[bot(),beta,bot({id:'b3',name:'Gone',state:'archived',conversation_id:'c3'})],messages:[],schedules:[],inbox:[]});
 open();
 await openTab('Schedules & triggers');
 fireEvent.click(await screen.findByRole('button',{name:'Add trigger'}));
 const dialog=within(await screen.findByRole('dialog',{name:'Add trigger'}));
 fireEvent.change(dialog.getByLabelText('Starts when'),{target:{value:'bot_finished'}});
 const watched=dialog.getByLabelText(/^Bot to watch/);
 expect(within(watched).getAllByRole('option').map(option=>option.textContent)).toEqual(['Choose a bot','Beta']);
 fireEvent.change(watched,{target:{value:'b2'}});
 fireEvent.change(dialog.getByLabelText('Run when it'),{target:{value:'any'}});
 fireEvent.change(dialog.getByLabelText(/What the bot does/),{target:{value:'Summarise Beta'}});
 fireEvent.click(dialog.getByRole('checkbox',{name:/may run on its own/}));
 fireEvent.click(dialog.getByRole('button',{name:'Create trigger'}));
 await waitFor(()=>expect(sent('create_schedule')).toHaveLength(1));
 expect(sent('create_schedule')[0].variables!.i.trigger).toEqual({kind:'bot_finished',bot_id:'b2',on:'any'});
});

it('needs a project and a safe glob for file triggers',async()=>{
 serve({bots:[bot({project_id:'p1'})],messages:[],schedules:[],inbox:[]});
 open();
 await openTab('Schedules & triggers');
 fireEvent.click(await screen.findByRole('button',{name:'Add trigger'}));
 const dialog=within(await screen.findByRole('dialog',{name:'Add trigger'}));
 fireEvent.change(dialog.getByLabelText('Starts when'),{target:{value:'file_change'}});
 fireEvent.change(dialog.getByLabelText(/What the bot does/),{target:{value:'Review changes'}});
 fireEvent.click(dialog.getByRole('checkbox',{name:/may run on its own/}));
 fireEvent.click(dialog.getByRole('button',{name:'Create trigger'}));
 expect(await dialog.findByText(/Enter a glob inside the project/)).toBeInTheDocument();
 fireEvent.change(dialog.getByLabelText(/^Files to watch/),{target:{value:'../outside/*'}});
 fireEvent.click(dialog.getByRole('button',{name:'Create trigger'}));
 expect(await dialog.findByText(/Enter a glob inside the project/)).toBeInTheDocument();
 expect(sent('create_schedule')).toHaveLength(0);
 fireEvent.change(dialog.getByLabelText(/^Files to watch/),{target:{value:'src/**/*.py'}});
 fireEvent.change(dialog.getByLabelText(/^Wait until changes settle for/),{target:{value:'45'}});
 fireEvent.click(dialog.getByRole('button',{name:'Create trigger'}));
 await waitFor(()=>expect(sent('create_schedule')).toHaveLength(1));
 expect(sent('create_schedule')[0].variables!.i.trigger).toEqual({kind:'file_change',glob:'src/**/*.py',debounce_s:45});
});

it('explains that file triggers need a project',async()=>{
 serve({bots:[bot()],messages:[],schedules:[],inbox:[]});
 open();
 await openTab('Schedules & triggers');
 fireEvent.click(await screen.findByRole('button',{name:'Add trigger'}));
 const dialog=within(await screen.findByRole('dialog',{name:'Add trigger'}));
 const kind=dialog.getByLabelText('Starts when');
 expect(within(kind).getByRole('option',{name:/Files change/})).toBeDisabled();
 expect(dialog.getByText(/needs a bot that works in a project/)).toBeInTheDocument();
});

it('shows a webhook secret once, then only the address',async()=>{
 const world:World={bots:[bot()],messages:[],schedules:[],inbox:[]};
 serve(world);
 const {container}=open();
 await openTab('Schedules & triggers');
 fireEvent.click(await screen.findByRole('button',{name:'Add trigger'}));
 const dialog=within(await screen.findByRole('dialog',{name:'Add trigger'}));
 fireEvent.change(dialog.getByLabelText('Starts when'),{target:{value:'webhook'}});
 expect(dialog.getByText(/starts off/i)).toBeInTheDocument();
 fireEvent.change(dialog.getByLabelText(/What the bot does/),{target:{value:'Handle deploys'}});
 fireEvent.click(dialog.getByRole('checkbox',{name:/may run on its own/}));
 fireEvent.click(dialog.getByRole('button',{name:'Create trigger'}));
 const reveal=within(await screen.findByRole('dialog',{name:'Webhook created'}));
 expect(reveal.getByText('SECRET-ONCE-123')).toBeInTheDocument();
 expect(reveal.getByText(`${location.origin}/hooks/bots/b1/s9`)).toBeInTheDocument();
 expect(reveal.getByText(/shown once/i)).toBeInTheDocument();
 await expectNoAxeViolations(container);
 fireEvent.click(reveal.getByRole('button',{name:'Done'}));
 await waitFor(()=>expect(screen.queryByText('SECRET-ONCE-123')).toBeNull());
 const list=within(await screen.findByRole('list',{name:'Triggers'}));
 expect(list.getByText(`${location.origin}/hooks/bots/b1/s9`)).toBeInTheDocument();
 expect(list.getByText('Off')).toBeInTheDocument();
 expect(document.body.textContent).not.toContain('SECRET-ONCE-123');
 // Turning it on is a separate, explicit step.
 fireEvent.click(list.getByRole('button',{name:'Turn on'}));
 await waitFor(()=>expect(sent('set_schedule_enabled')).toHaveLength(1));
 expect(sent('set_schedule_enabled')[0].variables).toEqual({c:'c1',s:'s9',enabled:true,r:2});
});

it('rotates a webhook secret and shows the new one once',async()=>{
 serve({bots:[bot()],messages:[],inbox:[],schedules:[schedule({id:'s5',prompt:'Handle deploys',trigger:{kind:'webhook'},spec:{kind:'event'},next_run:null,webhook_path:'/hooks/bots/b1/s5'})]});
 open();
 await openTab('Schedules & triggers');
 fireEvent.click(await screen.findByRole('button',{name:'Rotate secret'}));
 const confirm=within(await screen.findByRole('dialog',{name:'Rotate secret'}));
 expect(confirm.getByText(/old secret stops working/i)).toBeInTheDocument();
 fireEvent.click(confirm.getByRole('button',{name:'Rotate secret'}));
 const reveal=within(await screen.findByRole('dialog',{name:'New webhook secret'}));
 expect(reveal.getByText('ROTATED-SECRET-456')).toBeInTheDocument();
 fireEvent.click(reveal.getByRole('button',{name:'Done'}));
 await waitFor(()=>expect(screen.queryByText('ROTATED-SECRET-456')).toBeNull());
});

it('turns a trigger off and archives it after a confirmation that names the action',async()=>{
 const world:World={bots:[bot()],messages:[],inbox:[],schedules:[schedule({id:'s2',prompt:'Triage the inbox',trigger:{kind:'message'},spec:{kind:'event'},next_run:null})]};
 serve(world);
 open();
 await openTab('Schedules & triggers');
 const list=within(await screen.findByRole('list',{name:'Triggers'}));
 fireEvent.click(list.getByRole('button',{name:'Turn off'}));
 await waitFor(()=>expect(sent('set_schedule_enabled')).toHaveLength(1));
 expect(sent('set_schedule_enabled')[0].variables).toMatchObject({s:'s2',enabled:false});
 fireEvent.click(await within(await screen.findByRole('list',{name:'Triggers'})).findByRole('button',{name:'Archive trigger'}));
 const confirm=within(await screen.findByRole('dialog',{name:'Archive trigger'}));
 fireEvent.click(confirm.getByRole('button',{name:'Keep trigger'}));
 expect(sent('archive_schedule')).toHaveLength(0);
 fireEvent.click(within(await screen.findByRole('list',{name:'Triggers'})).getByRole('button',{name:'Archive trigger'}));
 fireEvent.click(within(await screen.findByRole('dialog',{name:'Archive trigger'})).getByRole('button',{name:'Archive trigger'}));
 await waitFor(()=>expect(sent('archive_schedule')).toHaveLength(1));
});

it('edits a trigger’s settings without changing its kind',async()=>{
 serve({bots:[bot(),beta],messages:[],inbox:[],schedules:[schedule({id:'s3',prompt:'Summarise Beta',trigger:{kind:'bot_finished',bot_id:'b2',on:'completed'},spec:{kind:'event'},next_run:null})]});
 open();
 await openTab('Schedules & triggers');
 fireEvent.click(await screen.findByRole('button',{name:'Edit'}));
 const dialog=within(await screen.findByRole('dialog',{name:'Edit trigger'}));
 expect(dialog.getByLabelText('Starts when')).toBeDisabled();
 expect(dialog.queryByRole('checkbox')).toBeNull();
 fireEvent.change(dialog.getByLabelText('Run when it'),{target:{value:'any'}});
 fireEvent.change(dialog.getByLabelText(/What the bot does/),{target:{value:'Summarise Beta’s outcome'}});
 fireEvent.click(dialog.getByRole('button',{name:'Save trigger'}));
 await waitFor(()=>expect(sent('edit_schedule')).toHaveLength(1));
 expect(sent('edit_schedule')[0].variables).toEqual({c:'c1',s:'s3',i:{revision:2,prompt:'Summarise Beta’s outcome',trigger:{kind:'bot_finished',bot_id:'b2',on:'any'},report_to:'orchestrator'}});
 expect(await screen.findByText('Trigger saved')).toBeInTheDocument();
});
