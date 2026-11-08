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
const bot=(extra:Record<string,unknown>={},tools=['read_file','list_files'],ceilingTools=['read_file','list_files','read_skill'])=>({
 id:'b1',revision:3,name:'Inbox triager',state:'active',template:'inbox_triager',description:'Sorts what is sent to it.',instructions:'Triage items.',
 project_id:null,conversation_id:'c1',custom_agent_id:'agent.bot-1',model:null,provider_id:null,tools,approval_mode:'standard',sandbox_profile:'agent',limits:{},
 ceiling:{tools:ceilingTools,approval_mode:'standard',sandbox_profile:'agent',deny_capabilities:deny},peers:[],
 effective:{tools,approval_mode:'standard',sandbox_profile:'agent',deny_capabilities:deny},
 violations:[],rules:[],needs_you:0,active_tasks:0,last_task:null,created_by:'user',config_changed_at:null,created_at:1,updated_at:2,...extra});
const memory=(extra:Record<string,unknown>={})=>({id:'m1',revision:1,content:'Staging deploys on Fridays',provenance:'Owner told the bot',retention_days:30,excluded:false,project_id:null,scope:'agent',agent_id:'agent.bot-1',author:'user',state:'active',expires_at:1900000000,...extra});

type Body={query?:string;variables?:Record<string,any>};
function serve(bots:ReturnType<typeof bot>[],extra?:(path:string,init?:RequestInit,body?:Body)=>unknown){
 mocked.mockImplementation((async(path:string,init?:RequestInit)=>{
  if(path==='/auth/me')return {principal:{id:'owner',display_name:'Owner',scopes:['host-admin']},session_id:'s1'};
  const body:Body|undefined=init?.body?JSON.parse(String(init.body)):undefined;
  const supplied=extra?.(path,init,body);
  if(supplied!==undefined)return supplied;
  if(path.startsWith('/api/workspace/automations'))return {schedule:[],schedule_run:[],goal:[],delegation:[]};
  if(/^\/api\/workspace\/sessions(\?|$)/.test(path))return {sessions:[]};
  if(path==='/graphql'){
   const q=body?.query||'';
   if(q.includes('bot_templates'))return {data:{bots,bot_templates:[],projects:[],needs_you:[]}};
   if(q.startsWith('query { bots'))return {data:{bots,needs_you:[]}};
   if(q.includes('schedules(conversation_id'))return {data:{schedules:[]}};
   return {data:{}};
  }
  return {};
 }) as typeof request);
}
const gqls=()=>mocked.mock.calls.filter(([path])=>path==='/graphql').map(([,init])=>JSON.parse(String(init!.body)) as Body);
const open=(onSection?:(name:string)=>void)=>render(<Managers embedded section="bots" projectId={null} sessionId={null} onSection={onSection}/>);
async function openTab(name:string,row=bot()){
 fireEvent.click(await screen.findByRole('button',{name:new RegExp(row.name)}));
 fireEvent.click(await screen.findByRole('tab',{name}));
}

// -- Memory tab --------------------------------------------------------------------------------------------------

it('shows the bot’s own memory in a tab without a second page header',async()=>{
 const requested:string[]=[];
 serve([bot()],path=>{
  if(path.startsWith('/api/workspace/memory')){requested.push(path);return {memory:[memory()]}}
  return undefined;
 });
 const {container}=open();
 await openTab('Memory');
 expect(await screen.findByText('Staging deploys on Fridays')).toBeInTheDocument();
 expect(requested[0]).toBe('/api/workspace/memory?agent_id=agent.bot-1');
 const panel=screen.getByRole('tabpanel');
 expect(within(panel).queryByRole('heading',{level:2})).toBeNull();
 expect(within(panel).queryByRole('button',{name:'Refresh'})).toBeNull();
 expect(within(panel).getByRole('heading',{name:'Memory with provenance'}).tagName).toBe('H4');
 expect(within(panel).getByRole('combobox',{name:/Applies to/})).toHaveDisplayValue('This agent only');
 await expectNoAxeViolations(container);
});

it('saves a note for this bot only from the Memory tab',async()=>{
 const saved:Record<string,unknown>[]=[];
 serve([bot()],(path,init,body)=>{
  if(path==='/api/workspace/memory'&&init?.method==='POST'){saved.push(body as never);return {id:'m2'}}
  if(path.startsWith('/api/workspace/memory'))return {memory:[]};
  return undefined;
 });
 open();
 await openTab('Memory');
 expect(await screen.findByText(/No memory saved/)).toBeInTheDocument();
 fireEvent.change(screen.getByLabelText(/Fact or decision/),{target:{value:'Reports go to #ops'}});
 fireEvent.change(screen.getByLabelText(/Source or reason/),{target:{value:'Owner request'}});
 fireEvent.click(screen.getByRole('button',{name:'Save memory'}));
 await waitFor(()=>expect(saved).toHaveLength(1));
 expect(saved[0]).toMatchObject({scope:'agent',agent_id:'agent.bot-1',project_id:null,content:'Reports go to #ops'});
});

// -- Skills tab --------------------------------------------------------------------------------------------------

const extensions=[
 {id:'lint',version:'1.0.0',description:'Checks style',permissions:[]},
 {id:'notes-helper',version:'2.1.0',description:'Works in Notes',permissions:['desktop-control'],computer_apps:['Notes']},
 {id:'any-app',version:'0.3.0',description:'',permissions:['desktop-control']},
];
const skillsWorld=(chosen:string[])=>(path:string,init?:RequestInit)=>{
 if(path.startsWith('/api/workspace/sessions/c1')&&init?.method==='PATCH')return {id:'c1',revision:8,extension_ids:chosen};
 if(path.startsWith('/api/workspace/sessions/c1'))return {id:'c1',revision:7,extension_ids:chosen,turns:[]};
 if(path.startsWith('/api/workspace/available-extensions'))return {extensions};
 return undefined;
};

it('lists each skill with its enabled, reviewed and reach, links to import, and explains large skills',async()=>{
 const sections:string[]=[];
 serve([bot()],skillsWorld([]));
 const {container}=open(name=>sections.push(name));
 await openTab('Skills');
 const lint=await screen.findByLabelText(/lint/);
 expect(lint.closest('label')).toHaveTextContent('Version 1.0.0');
 expect(lint.closest('label')).not.toHaveTextContent('Installed and reviewed');  // said once in the help text, not on every row
 expect(screen.getByText(/installed, reviewed and switched on/)).toBeInTheDocument();
 expect(screen.getByLabelText(/notes-helper/).closest('label')).toHaveTextContent('Permissions: control the computer');
 expect(lint.closest('label')).toHaveTextContent('No extra permissions');
 expect(screen.getByLabelText(/notes-helper/).closest('label')).toHaveTextContent('Controls only: Notes');
 expect(screen.getByLabelText(/any-app/).closest('label')).toHaveTextContent('Controls whichever application is in front');
 expect(screen.getByText(/A large skill is listed by file instead/)).toBeInTheDocument();
 const link=screen.getByRole('link',{name:'Import or install skills'});
 expect(link).toHaveAttribute('href','/manage/extensions');
 fireEvent.click(link);
 expect(sections).toEqual(['extensions']);
 await expectNoAxeViolations(container);
});

it('warns when skills are chosen but the bot cannot read them on demand, and points to Permissions',async()=>{
 serve([bot()],skillsWorld(['lint']));
 open();
 await openTab('Skills');
 expect(await screen.findByText(/cannot read large skills yet/)).toBeInTheDocument();
 fireEvent.click(screen.getByRole('button',{name:'Turn it on under Permissions'}));
 expect(await screen.findByRole('form',{name:'Bot permissions'})).toBeInTheDocument();
});

it('says the ceiling must change first when it does not include reading skills',async()=>{
 serve([bot({},['read_file'],['read_file'])],skillsWorld(['lint']));
 open();
 await openTab('Skills');
 expect(await screen.findByText(/its ceiling does not include “Read skills on demand”/)).toBeInTheDocument();
});

it('does not warn when the bot can read skills on demand',async()=>{
 serve([bot({},['read_file','read_skill'])],skillsWorld(['lint']));
 open();
 await openTab('Skills');
 await screen.findByLabelText(/lint/);
 expect(screen.queryByText(/cannot read large skills/)).toBeNull();
});

// -- Tool names from the host ------------------------------------------------------------------------------------

it('builds the ceiling editor from the host’s tool names, with wording for the ones it knows',async()=>{
 serve([bot()],(path,init,body)=>path==='/graphql'&&body?.query?.includes('agent_tool_names')?{data:{agent_tool_names:['read_file','read_skill','brand_new_tool']}}:undefined);
 open();
 await openTab('Permissions');
 const ceiling=within(await screen.findByRole('form',{name:'Bot ceiling'}));
 await waitFor(()=>expect(ceiling.getByLabelText('Brand new tool')).toBeInTheDocument());
 expect(ceiling.getByLabelText('Read skills on demand')).toBeInTheDocument();
 expect(ceiling.queryByLabelText('Run shell commands')).toBeNull();
});

it('falls back to the built-in tool list when the host query fails',async()=>{
 serve([bot()],(path,init,body)=>{
  if(path==='/graphql'&&body?.query?.includes('agent_tool_names'))throw new Error('Cannot query field "agent_tool_names"');
  return undefined;
 });
 open();
 await openTab('Permissions');
 const ceiling=within(await screen.findByRole('form',{name:'Bot ceiling'}));
 await waitFor(()=>expect(gqls().some(body=>body.query?.includes('agent_tool_names'))).toBe(true));
 expect(ceiling.getByLabelText('Run shell commands')).toBeInTheDocument();
 expect(ceiling.getByLabelText('Control this computer')).toBeInTheDocument();
});

// -- Computer control on a schedule -----------------------------------------------------------------------------

const computerBot=()=>bot({},['read_file','computer'],['read_file','computer']);
const allowedBot=()=>({...computerBot(),ceiling:{...computerBot().ceiling,deny_capabilities:deny.filter(name=>name!=='computer.control')}});
const sched=(extra:Record<string,unknown>={})=>({id:'s1',revision:2,goal_id:'g1',grant_id:'d1',prompt:'Tidy notes',spec:{kind:'interval',seconds:3600},enabled:true,archived:false,state:'enabled',next_run:1800000000,missed:'skip',overlap:'skip',...extra});
const catalog=(permission?:Record<string,unknown>)=>({schedule:[sched()],schedule_run:[],goal:[{id:'g1',conversation_id:'c1',success_criteria:'Done',runs_started:0,max_runs:10}],
 delegation:[{id:'d1',expires_at:Date.now()/1000+7*86400,used_runs:0,max_runs:10,revoked:false,...(permission?{computer_permission:permission}:{})}]});

it('keeps computer control off by default and says how to turn it on for a bot that may not',async()=>{
 serve([bot()],skillsWorld([]));
 open();
 await openTab('Schedules & triggers');
 fireEvent.click(await screen.findByRole('button',{name:'New scheduled task'}));
 const form=within(await screen.findByRole('form',{name:'Create scheduled task'}));
 expect(form.getByText(/This bot cannot control the computer/)).toBeInTheDocument();
 expect(form.queryByRole('checkbox',{name:/control apps/})).toBeNull();
 fireEvent.click(form.getByRole('button',{name:'Permissions'}));
 expect(await screen.findByRole('form',{name:'Bot permissions'})).toBeInTheDocument();
});

it('adds a bounded computer permission to a new schedule from the skills’ apps and typed apps',async()=>{
 const created:Record<string,any>[]=[];
 serve([allowedBot()],(path,init,body)=>{
  if(path==='/api/workspace/automations'&&init?.method==='POST'){created.push(body as never);return {}}
  if(path==='/api/workspace/schedules/preview')return {next_runs:[1800000000]};
  return skillsWorld(['notes-helper'])(path,init);
 });
 const {container}=open();
 await openTab('Schedules & triggers');
 fireEvent.click(await screen.findByRole('button',{name:'New scheduled task'}));
 const form=within(await screen.findByRole('form',{name:'Create scheduled task'}));
 const toggle=form.getByRole('checkbox',{name:/Let this schedule control apps/});
 expect(toggle).not.toBeChecked();
 fireEvent.change(form.getByLabelText('Success criteria'),{target:{value:'Notes tidy'}});
 fireEvent.change(form.getByLabelText('Task prompt'),{target:{value:'Tidy my notes'}});
 fireEvent.click(form.getByLabelText(/Allow unattended runs/));
 fireEvent.click(toggle);
 expect(form.getByText('This lets the bot control only these apps while the schedule runs.',{exact:false})).toBeInTheDocument();
 expect(form.queryByText(/Choose or type at least one app/)).toBeNull();  // no nagging before the person has tried
 fireEvent.click(form.getByRole('button',{name:'Authorize and create schedule'}));
 expect(await form.findByText(/Choose or type at least one app/)).toBeInTheDocument();
 expect(created).toHaveLength(0);
 fireEvent.click(await form.findByRole('checkbox',{name:'Notes'}));
 fireEvent.change(form.getByLabelText('Other apps'),{target:{value:'com.apple.Safari, Notes'}});
 fireEvent.change(form.getByLabelText('Computer seconds per run'),{target:{value:'120'}});
 fireEvent.change(form.getByLabelText('Computer control expires after (hours)'),{target:{value:'6'}});
 expect(form.queryByText(/Choose or type at least one app/)).toBeNull();
 await expectNoAxeViolations(container);
 const before=Date.now()/1000;
 fireEvent.click(form.getByRole('button',{name:'Authorize and create schedule'}));
 await waitFor(()=>expect(created).toHaveLength(1));
 const permission=created[0].computer_permission;
 expect(permission.apps).toEqual(['Notes','com.apple.Safari']);
 expect(permission.max_seconds).toBe(120);
 expect(permission.expires_at).toBeGreaterThanOrEqual(Math.floor(before+6*3600));
 expect(permission.expires_at).toBeLessThanOrEqual(Math.ceil(Date.now()/1000+6*3600));
});

it('sends no computer permission unless it was turned on',async()=>{
 const created:Record<string,any>[]=[];
 serve([allowedBot()],(path,init,body)=>{
  if(path==='/api/workspace/automations'&&init?.method==='POST'){created.push(body as never);return {}}
  if(path==='/api/workspace/schedules/preview')return {next_runs:[1800000000]};
  return skillsWorld([])(path,init);
 });
 open();
 await openTab('Schedules & triggers');
 fireEvent.click(await screen.findByRole('button',{name:'New scheduled task'}));
 const form=within(await screen.findByRole('form',{name:'Create scheduled task'}));
 fireEvent.change(form.getByLabelText('Success criteria'),{target:{value:'x'}});
 fireEvent.change(form.getByLabelText('Task prompt'),{target:{value:'Do x'}});
 fireEvent.click(form.getByLabelText(/Allow unattended runs/));
 fireEvent.click(form.getByRole('button',{name:'Authorize and create schedule'}));
 await waitFor(()=>expect(created).toHaveLength(1));
 expect('computer_permission' in created[0]).toBe(false);
});

it('shows a saved schedule’s computer permission and changes or removes it through GraphQL',async()=>{
 const edits:Body[]=[];
 serve([allowedBot()],(path,init,body)=>{
  if(path.startsWith('/api/workspace/automations'))return catalog({apps:['Notes'],expires_at:Date.now()/1000+86400,max_seconds:120});
  if(path==='/graphql'&&body?.query?.includes('edit_schedule')){edits.push(body);return {data:{edit_schedule:{id:'s1'}}}}
  return skillsWorld([])(path,init);
 });
 open();
 await openTab('Schedules & triggers');
 fireEvent.click(await screen.findByRole('button',{name:'Tidy notes'}));
 const panel=within(await screen.findByRole('region',{name:'Computer control for this schedule'}));
 expect(panel.getByText(/Can control only Notes until/)).toBeInTheDocument();
 fireEvent.click(panel.getByRole('button',{name:'Remove computer permission'}));
 await waitFor(()=>expect(edits).toHaveLength(1));
 expect(edits[0].variables).toEqual({c:'c1',s:'s1',i:{revision:2,clear_computer_permission:true}});
 fireEvent.click(panel.getByRole('button',{name:'Change computer permission'}));
 fireEvent.change(panel.getByLabelText('Apps'),{target:{value:'Safari'}});
 fireEvent.click(panel.getByRole('button',{name:'Save computer permission'}));
 await waitFor(()=>expect(edits).toHaveLength(2));
 expect(edits[1].variables!.i.revision).toBe(2);
 expect(edits[1].variables!.i.computer_permission).toMatchObject({apps:['Safari'],max_seconds:300});
 expect(edits[1].variables!.i.clear_computer_permission).toBeUndefined();
});

it('shows an off schedule as off and does not offer a change to a bot that may not control the computer',async()=>{
 serve([bot()],(path,init)=>path.startsWith('/api/workspace/automations')?catalog():skillsWorld([])(path,init));
 open();
 await openTab('Schedules & triggers');
 fireEvent.click(await screen.findByRole('button',{name:'Tidy notes'}));
 const panel=within(await screen.findByRole('region',{name:'Computer control for this schedule'}));
 expect(panel.getByText(/Off\. This schedule cannot control the computer/)).toBeInTheDocument();
 expect(panel.queryByRole('button',{name:'Allow computer control'})).toBeNull();
 expect(panel.getByText(/This bot cannot control the computer/)).toBeInTheDocument();
});

// -- Focus and retries in the computer panel ----------------------------------------------------------------------

const permitted=()=>catalog({apps:['Notes'],expires_at:Date.now()/1000+86400,max_seconds:120});
const editWorld=(edits:Body[],fail:{on:boolean})=>(path:string,init?:RequestInit,body?:Body)=>{
 if(path.startsWith('/api/workspace/automations'))return permitted();
 if(path==='/graphql'&&body?.query?.includes('edit_schedule')){edits.push(body);return fail.on?{errors:[{message:'Schedule changed; refresh before saving'}]}:{data:{edit_schedule:{id:'s1'}}}}
 return skillsWorld([])(path,init);
};
async function openPanel(){
 fireEvent.click(await screen.findByRole('button',{name:'Tidy notes'}));
 return within(await screen.findByRole('region',{name:'Computer control for this schedule'}));
}

it('focuses the first field when the change form opens and the panel heading when it closes',async()=>{
 serve([allowedBot()],editWorld([],{on:false}));
 open();
 await openTab('Schedules & triggers');
 const panel=await openPanel();
 fireEvent.click(panel.getByRole('button',{name:'Change computer permission'}));
 expect(panel.getByLabelText('Apps')).toHaveFocus();
 expect(panel.queryByRole('checkbox',{name:/Let this schedule control apps/})).toBeNull();  // the form only exists to set the permission
 fireEvent.click(panel.getByRole('button',{name:'Cancel'}));
 expect(panel.getByRole('heading',{name:'Computer control'})).toHaveFocus();
});

it('keeps the change form and what was typed when a save fails, and returns focus once it succeeds',async()=>{
 const edits:Body[]=[],fail={on:true};
 serve([allowedBot()],editWorld(edits,fail));
 open();
 await openTab('Schedules & triggers');
 const panel=await openPanel();
 fireEvent.click(panel.getByRole('button',{name:'Change computer permission'}));
 fireEvent.change(panel.getByLabelText('Apps'),{target:{value:'Safari'}});
 fireEvent.click(panel.getByRole('button',{name:'Save computer permission'}));
 await waitFor(()=>expect(edits).toHaveLength(1));
 expect(await screen.findByText(/Schedule changed; refresh before saving/)).toBeInTheDocument();
 expect(panel.getByLabelText('Apps')).toHaveValue('Safari');
 fail.on=false;
 fireEvent.click(panel.getByRole('button',{name:'Save computer permission'}));
 await waitFor(()=>expect(edits).toHaveLength(2));
 await waitFor(()=>expect(panel.queryByLabelText('Apps')).toBeNull());
 expect(panel.getByRole('heading',{name:'Computer control'})).toHaveFocus();
});

it('says what is wrong, linked to the field, only after the person tries to save',async()=>{
 const edits:Body[]=[];
 serve([allowedBot()],editWorld(edits,{on:false}));
 const {container}=open();
 await openTab('Schedules & triggers');
 const panel=await openPanel();
 fireEvent.click(panel.getByRole('button',{name:'Change computer permission'}));
 const apps=panel.getByLabelText('Apps');
 expect(panel.getByLabelText('Computer control expires after (hours)')).toHaveAttribute('max','167');  // whole hours left of a 7-day authorization
 fireEvent.change(apps,{target:{value:''}});
 expect(apps).not.toHaveAttribute('aria-invalid');
 fireEvent.click(panel.getByRole('button',{name:'Save computer permission'}));
 expect(apps).toHaveAttribute('aria-invalid','true');
 expect(apps).toHaveAccessibleDescription(/Choose or type at least one app/);
 expect(edits).toHaveLength(0);
 await expectNoAxeViolations(container);
});

it('sends the same request, with the same expiry, when a lost create is tried again',async()=>{
 const created:Record<string,any>[]=[];let attempts=0;
 let clock=1_800_000_000_000;
 const spy=vi.spyOn(Date,'now').mockImplementation(()=>clock);
 serve([allowedBot()],(path,init,body)=>{
  if(path==='/api/workspace/automations'&&init?.method==='POST'){created.push(body as never);if(attempts++===0)throw new Error('Response lost');return {}}
  if(path==='/api/workspace/schedules/preview')return {next_runs:[1800000000]};
  return skillsWorld([])(path,init);
 });
 open();
 await openTab('Schedules & triggers');
 fireEvent.click(await screen.findByRole('button',{name:'New scheduled task'}));
 const form=within(await screen.findByRole('form',{name:'Create scheduled task'}));
 fireEvent.change(form.getByLabelText('Success criteria'),{target:{value:'Notes tidy'}});
 fireEvent.change(form.getByLabelText('Task prompt'),{target:{value:'Tidy my notes'}});
 fireEvent.click(form.getByLabelText(/Allow unattended runs/));
 fireEvent.click(form.getByRole('checkbox',{name:/Let this schedule control apps/}));
 fireEvent.change(form.getByLabelText('Apps'),{target:{value:'Notes'}});
 fireEvent.click(form.getByRole('button',{name:'Authorize and create schedule'}));
 expect(await screen.findByText(/Response lost/)).toBeInTheDocument();
 clock+=5000;  // time passes before the person tries again
 fireEvent.click(form.getByRole('button',{name:'Authorize and create schedule'}));
 await waitFor(()=>expect(created).toHaveLength(2));
 expect(created[1].request_id).toBe(created[0].request_id);
 expect(created[1].computer_permission).toEqual(created[0].computer_permission);
 spy.mockRestore();
});
