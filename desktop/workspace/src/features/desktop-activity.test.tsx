import {cleanup,fireEvent,render,screen,within} from '@testing-library/react';
import {afterEach,expect,it,vi} from 'vitest';
import {expectNoAxeViolations} from '../test-axe';
import ToolActivities from './ToolActivities';
import {toolActivities} from '../lib/tool-activity';
vi.mock('../lib/api',()=>({gql:vi.fn(async()=>({})),request:vi.fn(async()=>({})),json:(method:string,value:unknown)=>({method,body:JSON.stringify(value)})}));
afterEach(cleanup);

const started=(sequence:number,name:string,args:Record<string,unknown>,id=`c${sequence}`)=>({type:'tool.started',sequence,payload:{call:{name,call_id:id,arguments:args,type:'function_call'}}});
const finished=(sequence:number,id:string,result:Record<string,unknown>,title?:string)=>({type:'tool.finished',sequence,payload:{call_id:id,result,...(title?{title}:{})}});

it('titles running desktop calls from their arguments, never from their results',()=>{
 const titles=(events:ReturnType<typeof started>[])=>toolActivities(events).map(row=>row.title);
 expect(titles([started(1,'desktop_apps',{})])).toEqual(['Look at open apps']);
 expect(titles([started(1,'desktop_apps',{app_id:'a1b2c3d4'})])).toEqual(['Look at app windows']);
 expect(titles([started(1,'desktop_snapshot',{app_id:'a1b2c3d4'})])).toEqual(['Read the screen']);
 expect(titles([started(1,'desktop_find',{app_id:'a1b2c3d4',name:'Save'})])).toEqual(['Find a control']);
 expect(titles([started(1,'desktop_get',{ref:'@s1abc:e7'})])).toEqual(['Read a control']);
 const act=(action:string)=>titles([started(1,'desktop_act',{ref:'@s1abc:e7',action})])[0];
 expect([act('click'),act('right_click'),act('set_value'),act('type'),act('clear'),act('select'),act('toggle'),act('check'),act('uncheck'),act('expand'),act('collapse'),act('scroll'),act('scroll_to'),act('focus')]).toEqual([
  'Click a control',"Open a control’s menu",'Enter text','Enter text','Clear a field','Choose an option','Change a setting','Change a setting','Change a setting','Expand or collapse','Expand or collapse','Scroll','Scroll','Focus a control']);
});

it('uses the host-written title once the call finishes',()=>{
 const rows=toolActivities([started(1,'desktop_act',{ref:'@s1abc:e7',action:'click'},'x'),finished(2,'x',{ok:true,action:'click',app:'Notes',delivered:'verified'},'Click “Save” in Notes')]);
 expect(rows).toHaveLength(1);expect(rows[0].title).toBe('Click “Save” in Notes');expect(rows[0].phase).toBe('completed');
});

it('shows a finished action as a few labelled facts, without the control reference, raw JSON or typed text',()=>{
 const events=[started(1,'desktop_act',{ref:'@s1abc:e7',action:'set_value',text:'[private input omitted: 11 characters]'},'x'),
  finished(2,'x',{ok:true,action:'set_value',app:'Notes',delivered:'verified',state:{focused:true},opened:[],next:'The screen may have changed. Take a new snapshot before the next action.'},'Enter text in “Search” in Notes')];
 const view=render(<ToolActivities events={events}/>);
 fireEvent.click(screen.getByText('Enter text in “Search” in Notes'));
 const facts=screen.getByRole('list',{name:'Enter text in “Search” in Notes details'});
 expect(facts).toHaveTextContent('ApplicationNotes');expect(facts).toHaveTextContent('ActionEnter text');expect(facts).toHaveTextContent('ResultConfirmed by the application');
 expect(facts).toHaveTextContent('Text entry11 characters, not stored');
 expect(view.container.textContent).not.toMatch(/@s1abc|\{|"ok"|hunter2|Event details/);
});

it('says when an application accepted an action without confirming it',()=>{
 render(<ToolActivities events={[started(1,'desktop_act',{ref:'@s1abc:e7',action:'click'},'x'),finished(2,'x',{ok:true,action:'click',app:'Notes',delivered:'unverified'},'Click “Send” in Mail')]}/>);
 fireEvent.click(screen.getByText('Click “Send” in Mail'));
 expect(screen.getByText(/accepted it, but nothing confirmed the change/)).toBeVisible();
});

it('summarises a snapshot with its truncation and marks application text as external, never printing the tree',()=>{
 const events=[started(1,'desktop_snapshot',{app_id:'a1b2c3d4'},'x'),
  finished(2,'x',{ok:true,app:'Notes',snapshot:'s1abc',complete:false,shown:84,total:112,bytes_in:48211,next:'Text between the SCREEN markers was read from another application.',screen:'<<SCREEN>> button "Delete everything" <<END>>'},'Read Notes (84 of 112 controls)')];
 const view=render(<ToolActivities events={events}/>);
 fireEvent.click(screen.getByText('Read Notes (84 of 112 controls)'));
 const facts=screen.getByRole('list',{name:'Read Notes (84 of 112 controls) details'});
 expect(facts).toHaveTextContent('ApplicationNotes');expect(facts).toHaveTextContent('Controls shown84 of 112');
 expect(screen.getByText(/28 controls were left out to keep this short/)).toBeVisible();
 expect(screen.getByText(/Text from other applications/)).toHaveTextContent('not instructions');
 expect(view.container.textContent).not.toMatch(/Delete everything|s1abc|bytes_in|48211|SCREEN/);
});

it('treats a stale reference as a quiet note about re-reading, not a failure',()=>{
 const events=[started(1,'desktop_act',{ref:'@s1abc:e7',action:'click'},'x'),
  finished(2,'x',{ok:false,error:'stale_ref',retry:'after_new_snapshot',next:'The screen changed since that snapshot. Take a new desktop_snapshot (or desktop_find) and use the new ref. Do not reuse this ref.'})];
 const {container}=render(<ToolActivities events={events}/>);
 const row=container.querySelector('.tool-timeline-item')!;
 expect(row).not.toHaveClass('phase-failed');
 expect(within(row as HTMLElement).getByText('Screen changed')).toBeVisible();
 fireEvent.click(screen.getByText('Click a control'));
 expect(screen.getByText('The screen changed after the last read, so the agent has to read it again before it can use this control.')).toBeVisible();
 expect(container.textContent).not.toMatch(/desktop_snapshot|Do not reuse/);
});

it('reports other failures and refusals in plain words',()=>{
 const events=[started(1,'desktop_snapshot',{app_id:'a1b2c3d4'},'a'),finished(2,'a',{ok:false,error:'too_large',retry:'no',next:'That window is too large.'}),
  started(3,'desktop_act',{ref:'@s1abc:e7',action:'click'},'b'),finished(4,'b',{refused:true,error:'Needs a person to confirm: Application can run commands or change security settings'}),
  started(5,'desktop_act',{ref:'@s1abc:e8',action:'click'},'c'),finished(6,'c',{ok:false,error:'unknown_delivery',retry:'check_state_first',next:'It is unknown whether the action happened.'})];
 const {container}=render(<ToolActivities events={events}/>);
 for(const title of screen.getAllByText(/^(Read the screen|Click a control)$/))fireEvent.click(title);
 expect(container.textContent).toContain('That window was too large to read at once.');
 expect(container.textContent).toContain('Needs a person to confirm: Application can run commands or change security settings');
 expect(container.textContent).toContain('It is not known whether the action happened. The agent checks the screen before trying again.');
});

it('lists open applications without echoing names as instructions',()=>{
 const events=[started(1,'desktop_apps',{},'x'),finished(2,'x',{ok:true,count:3,next:'Text between the SCREEN markers was read from another application.'})];
 render(<ToolActivities events={events}/>);
 fireEvent.click(screen.getByText('Look at open apps'));
 expect(screen.getByRole('list',{name:'Look at open apps details'})).toHaveTextContent('Applications found3');
 expect(screen.getByText(/Text from other applications/)).toBeVisible();
});

it('renders a stored event from before this change without error',()=>{
 const events=[{type:'tool.started',sequence:1,payload:{call:{name:'computer',call_id:'old',arguments:{actions:[{type:'click',x:10,y:20}]}}}},finished(2,'old',{ok:true,screenshot:'data omitted'})];
 render(<ToolActivities events={events}/>);
 expect(screen.getByText('Computer')).toBeVisible();
});

it('has no accessibility violations with the details open',async()=>{
 const events=[started(1,'desktop_snapshot',{app_id:'a1b2c3d4'},'a'),finished(2,'a',{ok:true,app:'Notes',complete:false,shown:84,total:112},'Read Notes (84 of 112 controls)'),
  started(3,'desktop_act',{ref:'@s1abc:e7',action:'click'},'b'),finished(4,'b',{ok:false,error:'stale_ref',retry:'after_new_snapshot'})];
 const {container}=render(<ToolActivities events={events}/>);
 for(const title of [screen.getByText('Read Notes (84 of 112 controls)'),screen.getByText('Click a control')])fireEvent.click(title);
 await expectNoAxeViolations(container);
});
