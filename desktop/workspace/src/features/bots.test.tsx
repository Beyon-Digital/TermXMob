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
 id:'b1',revision:3,name:'Inbox triager',state:'active',template:'inbox_triager',description:'Sorts what is sent to it.',instructions:'Triage items.',
 project_id:null,conversation_id:'c1',custom_agent_id:'agent.bot-1',model:null,provider_id:null,tools:['read_file','list_files'],approval_mode:'standard',sandbox_profile:'agent',limits:{},
 ceiling:{tools:['read_file','list_files','search_project'],approval_mode:'standard',sandbox_profile:'agent',deny_capabilities:deny},peers:[],
 effective:{tools:['read_file','list_files'],approval_mode:'standard',sandbox_profile:'agent',deny_capabilities:deny},
 violations:[],rules:[],needs_you:0,active_tasks:0,last_task:null,created_by:'user',config_changed_at:null,created_at:1,updated_at:2,...extra});
const templates=[
 {id:'inbox_triager',name:'Inbox triager',description:'Sorts what is sent to it.',requires_project:false,tools:['read_file','list_files'],instructions:'Triage.'},
 {id:'repo_watcher',name:'Read-only repo watcher',description:'Looks over a project.',requires_project:true,tools:['read_file','git_diff'],instructions:'Watch.'},
 {id:'daily_report',name:'Daily report',description:'Writes a short report.',requires_project:false,tools:['read_file'],instructions:'Report.'},
];
type Body={query?:string;variables?:Record<string,unknown>};
type Handler=(body:Body)=>unknown;

function serve(bots:ReturnType<typeof bot>[],onGql?:Handler,extra?:(path:string,init?:RequestInit)=>unknown){
 mocked.mockImplementation((async(path:string,init?:RequestInit)=>{
  if(path==='/auth/me')return {principal:{id:'owner',display_name:'Owner',scopes:['host-admin']},session_id:'s1'};
  const supplied=extra?.(path,init);
  if(supplied!==undefined)return supplied;
  if(path.startsWith('/api/workspace/automations'))return {schedule:[],schedule_run:[],goal:[],delegation:[]};
  if(/^\/api\/workspace\/sessions(\?|$)/.test(path))return {sessions:[]};
  if(path==='/graphql'){
   const body:Body=JSON.parse(String(init!.body));
   const written=onGql?.(body);
   if(written!==undefined)return written;
   if(body.query?.includes('bot_templates'))return {data:{bots,bot_templates:templates,projects:[{id:'p1',name:'Website',path:'/w'}]}};
   if(body.query?.startsWith('query { bots'))return {data:{bots}};
   return {data:{}};
  }
  return {};
 }) as typeof request);
}
const calls=()=>mocked.mock.calls.filter(([path])=>path==='/graphql').map(([,init])=>JSON.parse(String(init!.body)) as Body);
const sent=(name:string)=>calls().find(body=>body.query?.includes(name));
const open=()=>render(<Managers embedded section="bots" projectId={null} sessionId={null}/>);
const openBot=async(row=bot())=>{
 serve([row]);
 const view=open();
 fireEvent.click(await screen.findByRole('button',{name:new RegExp(row.name)}));
 return view;
};

it('says what to do when there are no bots and creates one from a template',async()=>{
 let created=false;
 serve([],body=>{
  if(body.query?.includes('create_bot')){created=true;return {data:{create_bot:bot()}}}
  return undefined;
 });
 const {container}=open();
 expect(await screen.findByText(/No bots yet\. Create one from a template/)).toBeInTheDocument();
 await expectNoAxeViolations(container);
 fireEvent.click(screen.getByRole('button',{name:'New bot'}));
 const dialog=within(await screen.findByRole('dialog'));
 expect(dialog.getAllByRole('radio').map(radio=>(radio.closest('label') as HTMLElement).textContent)).toEqual([
  expect.stringContaining('Inbox triager'),expect.stringContaining('Read-only repo watcher'),expect.stringContaining('Daily report'),expect.stringContaining('Custom')]);
 expect(dialog.getByText(/Can use: Read files, List files/)).toBeInTheDocument();
 fireEvent.click(dialog.getByRole('button',{name:'Create bot'}));
 expect(await dialog.findByText('Give the bot a name.')).toBeInTheDocument();
 expect(created).toBe(false);
 fireEvent.change(dialog.getByLabelText(/^Name/),{target:{value:'Morning triage'}});
 fireEvent.click(dialog.getByRole('button',{name:'Create bot'}));
 await waitFor(()=>expect(created).toBe(true));
 expect(sent('create_bot')!.variables).toEqual({input:{name:'Morning triage',template:'inbox_triager',instructions:null,project_id:null}});
 expect(await screen.findByText('Bot created')).toBeInTheDocument();
 expect(location.search).toBe('?bot=b1');
});

it('asks for a project before creating a project template, and for instructions on a custom bot',async()=>{
 serve([]);
 open();
 fireEvent.click(await screen.findByRole('button',{name:'New bot'}));
 const dialog=within(await screen.findByRole('dialog'));
 fireEvent.click(dialog.getByRole('radio',{name:/Read-only repo watcher/}));
 fireEvent.change(dialog.getByLabelText(/^Name/),{target:{value:'Watcher'}});
 fireEvent.click(dialog.getByRole('button',{name:'Create bot'}));
 expect(await dialog.findByText('This template watches a project. Choose one.')).toBeInTheDocument();
 fireEvent.click(dialog.getByRole('radio',{name:/Custom/}));
 fireEvent.click(dialog.getByRole('button',{name:'Create bot'}));
 expect(await dialog.findByText('Say what the bot should do each time it runs.')).toBeInTheDocument();
 expect(sent('create_bot')).toBeUndefined();
});

it('lists bots with their state and how many need you, and opens one from the list',async()=>{
 serve([bot({needs_you:2,last_task:{id:'t',status:'awaiting_approval',updated_at:100}}),bot({id:'b2',name:'Weekly report',state:'paused'})]);
 const {container}=open();
 const list=within(await screen.findByRole('list',{name:'Bots'}));
 expect(list.getByText('2 need you')).toBeInTheDocument();
 expect(list.getByText('Active')).toBeInTheDocument();
 expect(list.getByText('Paused')).toBeInTheDocument();
 expect(list.getByText('No runs yet')).toBeInTheDocument();
 await expectNoAxeViolations(container);
 fireEvent.click(list.getByRole('button',{name:/Weekly report/}));
 expect(await screen.findByRole('heading',{name:'Weekly report'})).toBeInTheDocument();
 await waitFor(()=>expect(screen.getByRole('heading',{name:'Weekly report'})).toHaveFocus());
 expect(location.search).toBe('?bot=b2');
 fireEvent.click(screen.getByRole('button',{name:'All bots'}));
 expect(await screen.findByRole('list',{name:'Bots'})).toBeInTheDocument();
 expect(location.search).toBe('');
});

it('opens the bot named in the address and says so when it is gone',async()=>{
 history.replaceState(null,'','/?bot=b1');
 serve([bot()]);
 const {container}=open();
 expect(await screen.findByRole('heading',{name:'Inbox triager'})).toBeInTheDocument();
 expect(screen.getByRole('tab',{name:'Overview',selected:true})).toBeInTheDocument();
 await expectNoAxeViolations(container);
 cleanup();
 history.replaceState(null,'','/?bot=gone');
 open();
 expect(await screen.findByText(/This bot no longer exists/)).toBeInTheDocument();
});

it('moves between tabs with the arrow keys and lists every section',async()=>{
 await openBot();
 const tabs=screen.getAllByRole('tab');
 expect(tabs.map(tab=>tab.textContent)).toEqual(['Overview','Prompt','Permissions','Schedules & triggers','Messages','Memory','Skills','Tasks','Activity']);
 tabs[0].focus();
 fireEvent.keyDown(tabs[0],{key:'ArrowRight'});
 expect(screen.getByRole('tab',{name:'Prompt'})).toHaveAttribute('aria-selected','true');
 expect(screen.getByRole('tab',{name:'Prompt'})).toHaveFocus();
 fireEvent.keyDown(screen.getByRole('tab',{name:'Prompt'}),{key:'End'});
 expect(screen.getByRole('tab',{name:'Activity'})).toHaveAttribute('aria-selected','true');
 fireEvent.click(screen.getByRole('tab',{name:'Schedules & triggers'}));
 expect(await screen.findByRole('heading',{name:'Schedules'})).toBeInTheDocument();
 expect(screen.getByText(/No scheduled tasks/)).toBeInTheDocument();
 fireEvent.click(screen.getByRole('tab',{name:'Messages'}));
 expect(await screen.findByText(/No messages yet/)).toBeInTheDocument();
});

it('pauses, resumes and runs a bot from Overview, and disables Run now while paused',async()=>{
 serve([bot()],body=>body.query?.includes('set_bot_state')||body.query?.includes('bot_run_now')?{data:{}}:undefined);
 open();
 fireEvent.click(await screen.findByRole('button',{name:/Inbox triager/}));
 fireEvent.click(await screen.findByRole('button',{name:'Run now'}));
 await waitFor(()=>expect(sent('bot_run_now')).toBeDefined());
 expect(sent('bot_run_now')!.variables).toEqual({id:'b1',report_to:'owner'});
 expect(await screen.findByText(/Run started/)).toBeInTheDocument();
 fireEvent.click(screen.getByRole('button',{name:'Pause bot'}));
 await waitFor(()=>expect(sent('set_bot_state')).toBeDefined());
 expect(sent('set_bot_state')!.variables).toEqual({id:'b1',state:'paused',revision:3});
 expect(await screen.findByText('Bot paused')).toBeInTheDocument();
});

it('shows a paused bot as paused and offers Resume bot',async()=>{
 await openBot(bot({state:'paused'}));
 expect(screen.getByRole('button',{name:'Run now'})).toBeDisabled();
 expect(screen.getByRole('button',{name:'Resume bot'})).toBeInTheDocument();
});

it('returns focus to the bot that was open when going back to the list, or to New bot when it was deleted',async()=>{
 const rows=[bot(),bot({id:'b2',name:'Weekly report'})];
 serve(rows,body=>{if(!body.query?.includes('delete_bot'))return undefined;rows.splice(1,1);return {data:{delete_bot:{id:'b2'}}}});
 open();
 const row=await screen.findByRole('button',{name:/Weekly report/});
 fireEvent.click(row);
 fireEvent.click(await screen.findByRole('button',{name:'All bots'}));
 await waitFor(()=>expect(screen.getByRole('button',{name:/Weekly report/})).toHaveFocus());
 fireEvent.click(screen.getByRole('button',{name:/Weekly report/}));
 fireEvent.click(await screen.findByRole('button',{name:'Delete bot'}));
 fireEvent.click(within(await screen.findByRole('dialog')).getByRole('button',{name:'Delete bot'}));
 await waitFor(()=>expect(screen.getByRole('button',{name:'New bot'})).toHaveFocus());
});

it('confirms before deleting with the same verb',async()=>{
 serve([bot()],body=>body.query?.includes('delete_bot')?{data:{delete_bot:{id:'b1'}}}:undefined);
 open();
 fireEvent.click(await screen.findByRole('button',{name:/Inbox triager/}));
 fireEvent.click(await screen.findByRole('button',{name:'Delete bot'}));
 const dialog=within(await screen.findByRole('dialog'));
 expect(sent('delete_bot')).toBeUndefined();
 fireEvent.click(dialog.getByRole('button',{name:'Delete bot'}));
 await waitFor(()=>expect(sent('delete_bot')).toBeDefined());
 expect(sent('delete_bot')!.variables).toEqual({id:'b1'});
 expect(await screen.findByText('Bot deleted')).toBeInTheDocument();
 await waitFor(()=>expect(location.search).toBe(''));
});

it('saves the prompt against the revision it loaded and asks for a name and instructions',async()=>{
 serve([bot()],body=>body.query?.includes('patch_bot')?{data:{patch_bot:bot({revision:4})}}:undefined);
 open();
 fireEvent.click(await screen.findByRole('button',{name:/Inbox triager/}));
 fireEvent.click(screen.getByRole('tab',{name:'Prompt'}));
 const form=within(await screen.findByRole('form',{name:'Bot prompt'}));
 fireEvent.change(form.getByLabelText(/^Instructions/),{target:{value:' '}});
 fireEvent.click(form.getByRole('button',{name:'Save prompt'}));
 expect(await form.findByText('Say what the bot should do each time it runs.')).toBeInTheDocument();
 expect(sent('patch_bot')).toBeUndefined();
 fireEvent.change(form.getByLabelText(/^Instructions/),{target:{value:'Be brief.'}});
 fireEvent.click(form.getByRole('button',{name:'Save prompt'}));
 await waitFor(()=>expect(sent('patch_bot')).toBeDefined());
 expect(sent('patch_bot')!.variables).toEqual({id:'b1',input:{revision:3,name:'Inbox triager',description:'Sorts what is sent to it.',model:'',instructions:'Be brief.'}});
 expect(await screen.findByText('Prompt saved')).toBeInTheDocument();
});

it('shows why a stale save failed and keeps what was typed',async()=>{
 serve([bot()],body=>{if(body.query?.includes('patch_bot'))throw new Error('Bot changed; reload before saving')});
 open();
 fireEvent.click(await screen.findByRole('button',{name:/Inbox triager/}));
 fireEvent.click(screen.getByRole('tab',{name:'Prompt'}));
 const area=await screen.findByLabelText(/^Instructions/);
 fireEvent.change(area,{target:{value:'Mine'}});
 fireEvent.click(screen.getByRole('button',{name:'Save prompt'}));
 expect(await screen.findByText('Bot changed; reload before saving')).toBeInTheDocument();
 expect(screen.getByLabelText(/^Instructions/)).toHaveValue('Mine');
});

it('lists only tools inside the ceiling, saves a narrower set, and explains the ceiling',async()=>{
 serve([bot()],body=>body.query?.includes('patch_bot')?{data:{patch_bot:bot({revision:4})}}:undefined);
 open();
 fireEvent.click(await screen.findByRole('button',{name:/Inbox triager/}));
 fireEvent.click(screen.getByRole('tab',{name:'Permissions'}));
 const form=within(await screen.findByRole('form',{name:'Bot permissions'}));
 expect(form.getAllByRole('checkbox').map(box=>(box.closest('label') as HTMLElement).textContent)).toEqual(['Read files','List files','Search the project']);
 expect(form.getByLabelText('Read files')).toBeChecked();
 expect(form.getByLabelText('Search the project')).not.toBeChecked();
 expect(form.queryByLabelText('Run shell commands')).toBeNull();
 expect([...(form.getByLabelText('Approvals') as HTMLSelectElement).options].map(option=>option.value)).toEqual(['standard']);
 expect(screen.getByText(/never has more access than your own account, and the main agent cannot raise it/)).toBeInTheDocument();
 fireEvent.click(form.getByLabelText('List files'));
 fireEvent.click(form.getByRole('button',{name:'Save permissions'}));
 await waitFor(()=>expect(sent('patch_bot')).toBeDefined());
 expect(sent('patch_bot')!.variables).toEqual({id:'b1',input:{revision:3,tools:['read_file'],approval_mode:'standard',sandbox_profile:'agent'}});
});

it('refuses to save permissions with no tool chosen',async()=>{
 await openBot();
 fireEvent.click(screen.getByRole('tab',{name:'Permissions'}));
 const form=within(await screen.findByRole('form',{name:'Bot permissions'}));
 fireEvent.click(form.getByLabelText('Read files'));
 fireEvent.click(form.getByLabelText('List files'));
 fireEvent.click(form.getByRole('button',{name:'Save permissions'}));
 expect(await screen.findAllByText('Choose at least one tool.')).not.toHaveLength(0);
 expect(sent('patch_bot')).toBeUndefined();
});

it('shows a bot that exceeds its ceiling as not running and offers the fix',async()=>{
 await openBot(bot({violations:['tools outside the ceiling: run_shell'],effective:{tools:['read_file'],approval_mode:'standard',sandbox_profile:'agent',deny_capabilities:deny}}));
 expect(screen.getByRole('button',{name:'Run now'})).toBeDisabled();
 fireEvent.click(screen.getByRole('tab',{name:'Permissions'}));
 const alert=await screen.findByText(/This bot will not run/);
 expect(alert.closest('[role=alert]')).toHaveTextContent('tools outside the ceiling: run_shell');
 expect(alert.closest('[role=alert]')).toHaveTextContent('Save permissions');
});

it('reviews a ceiling change before saving it and names what it allows',async()=>{
 serve([bot()],body=>body.query?.includes('set_bot_ceiling')?{data:{set_bot_ceiling:bot({revision:4})}}:undefined);
 open();
 fireEvent.click(await screen.findByRole('button',{name:/Inbox triager/}));
 fireEvent.click(screen.getByRole('tab',{name:'Permissions'}));
 const form=within(await screen.findByRole('form',{name:'Bot ceiling'}));
 fireEvent.click(form.getByLabelText('Run shell commands'));
 fireEvent.click(form.getByLabelText(/Network access/));
 fireEvent.click(form.getByRole('button',{name:'Save ceiling'}));
 const dialog=within(await screen.findByRole('dialog'));
 expect(dialog.getByText('Allows: Run shell commands')).toBeInTheDocument();
 expect(dialog.getByText('Allows network access')).toBeInTheDocument();
 expect(sent('set_bot_ceiling')).toBeUndefined();
 fireEvent.click(dialog.getByRole('button',{name:'Save ceiling'}));
 await waitFor(()=>expect(sent('set_bot_ceiling')).toBeDefined());
 const ceiling=(sent('set_bot_ceiling')!.variables as {ceiling:{tools:string[];deny_capabilities:string[]}}).ceiling;
 expect(ceiling.tools).toContain('run_shell');
 expect(ceiling.deny_capabilities).not.toContain('net.outbound:any');
 expect(ceiling.deny_capabilities).toContain('computer.control');
 expect(await screen.findByText('Ceiling saved')).toBeInTheDocument();
});

it('revokes a remembered approval and says what to do when there are none',async()=>{
 serve([bot({rules:[{id:'r1',effect:'allow',action_type:'tool',tool:'read_file',display:'read_file in /src',times_used:3}]})],
  body=>body.query?.includes('revoke_bot_rule')?{data:{revoke_bot_rule:{id:'b1'}}}:undefined);
 open();
 fireEvent.click(await screen.findByRole('button',{name:/Inbox triager/}));
 fireEvent.click(screen.getByRole('tab',{name:'Permissions'}));
 expect(await screen.findByText('read_file in /src')).toBeInTheDocument();
 expect(screen.getByText('Allowed · used 3 times')).toBeInTheDocument();
 fireEvent.click(screen.getByRole('button',{name:'Revoke read_file in /src'}));
 await waitFor(()=>expect(sent('revoke_bot_rule')).toBeDefined());
 expect(sent('revoke_bot_rule')!.variables).toEqual({id:'b1',rule:'r1'});
 expect(await screen.findByText('Approval revoked')).toBeInTheDocument();
 cleanup();
 history.replaceState(null,'','/');
 await openBot(bot());
 fireEvent.click(screen.getByRole('tab',{name:'Permissions'}));
 expect(await screen.findByText(/No remembered approvals/)).toBeInTheDocument();
});

it('saves peers from the other bots',async()=>{
 serve([bot(),bot({id:'b2',name:'Weekly report'})],body=>body.query?.includes('patch_bot')?{data:{patch_bot:bot({revision:4})}}:undefined);
 open();
 fireEvent.click(await screen.findByRole('button',{name:/Inbox triager/}));
 fireEvent.click(screen.getByRole('tab',{name:'Permissions'}));
 const form=within(await screen.findByRole('form',{name:'Bot peers'}));
 fireEvent.click(form.getByLabelText('Weekly report'));
 fireEvent.click(form.getByRole('button',{name:'Save peers'}));
 await waitFor(()=>expect(sent('patch_bot')).toBeDefined());
 expect(sent('patch_bot')!.variables).toEqual({id:'b1',input:{revision:3,peers:['b2']}});
});

it('has no accessibility violations on the permissions tab',async()=>{
 const {container}=await openBot();
 fireEvent.click(screen.getByRole('tab',{name:'Permissions'}));
 await screen.findByRole('form',{name:'Bot ceiling'});
 await expectNoAxeViolations(container);
});

it('chooses skills from the installed extensions and saves them on the bot conversation',async()=>{
 const patches:unknown[]=[];
 serve([bot()],undefined,(path,init)=>{
  if(path.startsWith('/api/workspace/sessions/c1')&&init?.method==='PATCH'){patches.push(JSON.parse(String(init.body)));return {id:'c1',revision:8,extension_ids:['lint']}}
  if(path.startsWith('/api/workspace/sessions/c1'))return {id:'c1',revision:7,extension_ids:[],turns:[]};
  if(path.startsWith('/api/workspace/available-extensions'))return {extensions:[{id:'lint',version:'1.0.0',description:'Checks style'}]};
  return undefined;
 });
 open();
 fireEvent.click(await screen.findByRole('button',{name:/Inbox triager/}));
 fireEvent.click(screen.getByRole('tab',{name:'Skills'}));
 fireEvent.click(await screen.findByLabelText(/lint/));
 await waitFor(()=>expect(patches).toEqual([{revision:7,changes:{extension_ids:['lint']}}]));
 expect(await screen.findByText('Skills saved')).toBeInTheDocument();
 expect(screen.getByLabelText(/lint/)).toBeChecked();
});

it('says what to do when no skills are installed',async()=>{
 serve([bot()],undefined,path=>path.startsWith('/api/workspace/available-extensions')?{extensions:[]}:path.startsWith('/api/workspace/sessions/c1')?{id:'c1',revision:1,turns:[]}:undefined);
 open();
 fireEvent.click(await screen.findByRole('button',{name:/Inbox triager/}));
 fireEvent.click(screen.getByRole('tab',{name:'Skills'}));
 expect(await screen.findByText(/No skills are installed\. Add some under Plugins & skills/)).toBeInTheDocument();
});

it('runs a task with text and lists recent tasks with their result',async()=>{
 serve([bot()],body=>body.query?.includes('bot_run_now')?{data:{bot_run_now:{task_id:'t9'}}}:undefined,path=>
  path.startsWith('/api/workspace/sessions/c1')?{id:'c1',revision:1,turns:[
   {id:'u1',sequence:1,prompt:'Summarise the inbox',task:{id:'t1',status:'completed',prompt:'x',result:'Three items; one is urgent.'}},
   {id:'u2',sequence:2,prompt:'Check the repo',task:{id:'t2',status:'failed',prompt:'x',error:'Model unavailable'}}]}:undefined);
 const {container}=open();
 fireEvent.click(await screen.findByRole('button',{name:/Inbox triager/}));
 fireEvent.click(screen.getByRole('tab',{name:'Tasks'}));
 const history=within(await screen.findByRole('region',{name:'Recent tasks'}));
 expect(await history.findByText('Three items; one is urgent.')).toBeInTheDocument();
 expect(history.getByText('Model unavailable')).toBeInTheDocument();
 expect(history.getByText('Finished')).toBeInTheDocument();
 expect(history.getByText('Failed')).toBeInTheDocument();
 await expectNoAxeViolations(container);
 fireEvent.change(screen.getByLabelText('Task'),{target:{value:'Summarise today'}});
 fireEvent.click(screen.getByRole('button',{name:'Run task'}));
 await waitFor(()=>expect(sent('bot_run_now')).toBeDefined());
 expect(sent('bot_run_now')!.variables).toEqual({id:'b1',prompt:'Summarise today'});
 expect(await screen.findByText('Task started')).toBeInTheDocument();
});

it('says what to do when a bot has not run yet',async()=>{
 serve([bot()],undefined,path=>path.startsWith('/api/workspace/sessions/c1')?{id:'c1',revision:1,turns:[]}:undefined);
 open();
 fireEvent.click(await screen.findByRole('button',{name:/Inbox triager/}));
 fireEvent.click(screen.getByRole('tab',{name:'Tasks'}));
 expect(await screen.findByText(/No tasks yet\. Run this bot once/)).toBeInTheDocument();
});

it('shows only this bot\'s recorded changes under Activity',async()=>{
 serve([bot()],undefined,path=>path==='/api/workspace/audit'?{events:[
  {seq:3,kind:'bot',resource:'b1',event:'paused',payload:{},created:200},
  {seq:2,kind:'bot',resource:'b2',event:'archived',payload:{},created:150},
  {seq:1,kind:'bot',resource:'b1',event:'created',payload:{by:'orchestrator'},created:100}]}:undefined);
 open();
 fireEvent.click(await screen.findByRole('button',{name:/Inbox triager/}));
 fireEvent.click(screen.getByRole('tab',{name:'Activity'}));
 const list=within(await screen.findByRole('list',{name:'Bot activity'}));
 expect(list.getAllByRole('listitem').map(item=>item.textContent)).toEqual([expect.stringContaining('Paused'),expect.stringContaining('Created')]);
 expect(list.getByText(/by the main agent/)).toBeInTheDocument();
});

it('reports a failed load and offers a retry',async()=>{
 mocked.mockImplementation((async(path:string)=>{
  if(path==='/auth/me')return {principal:{id:'owner',display_name:'Owner',scopes:[]},session_id:'s1'};
  throw new Error('Host unreachable');
 }) as typeof request);
 open();
 expect(await screen.findByText('Host unreachable')).toBeInTheDocument();
 expect(screen.getByRole('button',{name:'Retry'})).toBeInTheDocument();
});

// -- tab, notice and tool labels ---------------------------------------------------------------------------------

it('clears the “saved” notice when another tab opens, so it never describes a tab you have left',async()=>{
 serve([bot()],body=>body.query?.includes('patch_bot')?{data:{patch_bot:bot({revision:4})}}:undefined);
 open();
 fireEvent.click(await screen.findByRole('button',{name:/Inbox triager/}));
 fireEvent.click(screen.getByRole('tab',{name:'Prompt'}));
 fireEvent.change(await screen.findByLabelText(/^Instructions/),{target:{value:'Be brief.'}});
 fireEvent.click(screen.getByRole('button',{name:'Save prompt'}));
 expect(await screen.findByText('Prompt saved')).toBeInTheDocument();
 fireEvent.click(screen.getByRole('tab',{name:'Permissions'}));
 await screen.findByRole('form',{name:'Bot permissions'});
 expect(screen.queryByText('Prompt saved')).toBeNull();
});

it('keeps the selected tab in the address so a reload opens the same tab, and drops it when leaving the bot',async()=>{
 serve([bot()]);
 const first=open();
 fireEvent.click(await screen.findByRole('button',{name:/Inbox triager/}));
 expect(new URLSearchParams(location.search).get('tab')).toBeNull();
 fireEvent.click(screen.getByRole('tab',{name:'Permissions'}));
 expect(new URLSearchParams(location.search).get('bot')).toBe('b1');
 expect(new URLSearchParams(location.search).get('tab')).toBe('permissions');
 const entries=history.length;
 fireEvent.click(screen.getByRole('tab',{name:'Prompt'}));
 expect(new URLSearchParams(location.search).get('tab')).toBe('prompt');
 expect(history.length).toBe(entries); // tab changes replace the entry, they do not pile up history
 first.unmount();
 open(); // a reload
 expect(await screen.findByRole('form',{name:'Bot prompt'})).toBeInTheDocument();
 expect(screen.getByRole('tab',{name:'Prompt'})).toHaveAttribute('aria-selected','true');
 fireEvent.click(screen.getByRole('button',{name:'All bots'}));
 expect(new URLSearchParams(location.search).get('tab')).toBeNull();
});

it('ignores an unknown tab in the address',async()=>{
 history.replaceState(null,'','/?bot=b1&tab=nonsense');
 serve([bot()]);
 open();
 expect(await screen.findByRole('tab',{name:'Overview'})).toHaveAttribute('aria-selected','true');
});

it('gives tools without wording a readable label under an “Other tools” heading and keeps the real id on hover',async()=>{
 serve([bot()],body=>body.query?.includes('agent_tool_names')?{data:{agent_tool_names:['read_file','spawn_subagent','mcp__files__list_dir']}}:undefined);
 open();
 fireEvent.click(await screen.findByRole('button',{name:/Inbox triager/}));
 fireEvent.click(screen.getByRole('tab',{name:'Permissions'}));
 const form=within(await screen.findByRole('form',{name:'Bot ceiling'}));
 const group=await form.findByRole('group',{name:'Other tools'});
 const labels=within(group).getAllByRole('checkbox').map(box=>(box.closest('label') as HTMLElement).textContent);
 expect(labels).toEqual(['Spawn subagent','Files: list dir']);
 expect((within(group).getByLabelText('Spawn subagent').closest('label') as HTMLElement).title).toBe('spawn_subagent');
 expect(within(group).queryByLabelText('spawn_subagent')).toBeNull();
});

it('marks the skills list as self-saving, so the Settings “Unsaved changes” bar does not appear after it saves itself',async()=>{
 serve([bot()],undefined,(path,init)=>{
  if(path.startsWith('/api/workspace/sessions/c1')&&init?.method==='PATCH')return {id:'c1',revision:8,extension_ids:['lint']};
  if(path.startsWith('/api/workspace/sessions/c1'))return {id:'c1',revision:7,extension_ids:[],turns:[]};
  if(path.startsWith('/api/workspace/available-extensions'))return {extensions:[{id:'lint',version:'1.0.0',description:'Checks style'}]};
  return undefined;
 });
 open();
 fireEvent.click(await screen.findByRole('button',{name:/Inbox triager/}));
 fireEvent.click(screen.getByRole('tab',{name:'Skills'}));
 const box=await screen.findByLabelText(/lint/);
 expect(box.closest('[data-settings-autosave]')).not.toBeNull();
});
