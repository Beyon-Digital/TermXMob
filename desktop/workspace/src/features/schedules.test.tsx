import {act,cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import {afterEach,beforeEach,expect,it,vi} from 'vitest';
import ScheduleManager from './ScheduleManager';
import {request} from '../lib/api';
vi.mock('../lib/api',()=>({request:vi.fn(),json:(method:string,body:unknown)=>({method,body:JSON.stringify(body)})}));
const mocked=vi.mocked(request);
const row={id:'schedule',revision:4,goal_id:'goal',grant_id:'grant',prompt:'Run project checks',spec:{kind:'cron',expression:'0 9 * * 1-5',timezone:'UTC'},enabled:true,state:'enabled',next_run:1800000000,missed:'skip',overlap:'skip'};
afterEach(cleanup);
beforeEach(()=>{mocked.mockReset();mocked.mockImplementation(async(path)=>{if(path.includes('/sessions'))return {sessions:[{id:'chat',title:'Project checks'}]} as any;if(path.endsWith('/preview'))return {next_runs:[1800000000]} as any;return {schedule:[row],schedule_run:[],goal:[{id:'goal',conversation_id:'chat',max_runs:10,runs_started:1}],delegation:[{id:'grant',expires_at:1900000000}]} as any})});
it('creates cron schedules only after explicit budget authorization and preserves retry identity',async()=>{
 render(<ScheduleManager projectId={null} sessionId="chat"/>);
 fireEvent.click(await screen.findByRole('button',{name:'New scheduled task'}));
 fireEvent.change(screen.getByLabelText('Success criteria'),{target:{value:'All checks pass'}});
 fireEvent.change(screen.getByLabelText('Task prompt'),{target:{value:'Run tests'}});
 fireEvent.change(screen.getByLabelText('Frequency'),{target:{value:'cron'}});
 expect(screen.getByRole('button',{name:'Authorize and create schedule'})).toBeDisabled();
 fireEvent.click(screen.getByLabelText(/Allow unattended runs/));
 let attempts=0;const base=mocked.getMockImplementation()!;
 mocked.mockImplementation(async(path,init)=>{if(path==='/api/workspace/automations'&&init?.method==='POST'){if(attempts++===0)throw new Error('Response lost; inspect schedules before retrying');return row as any}return base(path,init)});
 fireEvent.click(screen.getByRole('button',{name:'Authorize and create schedule'}));
 expect(await screen.findByRole('alert')).toHaveTextContent('Response lost');
 fireEvent.click(screen.getByRole('button',{name:'Authorize and create schedule'}));
 await waitFor(()=>expect(screen.queryByRole('form',{name:'Create scheduled task'})).not.toBeInTheDocument());
 const calls=mocked.mock.calls.filter(([path,init])=>path==='/api/workspace/automations'&&init?.method==='POST').map(([,init])=>JSON.parse(String(init?.body)));
 expect(calls).toHaveLength(2);expect(calls[0].request_id).toBe(calls[1].request_id);expect(calls[1]).toMatchObject({conversation_id:'chat',spec:{kind:'cron',expression:'0 9 * * 1-5'},max_runs:10,grant_days:7});
});
it('uses optimistic revisions for pause and surfaces run conflicts without repeating dispatch',async()=>{
 render(<ScheduleManager projectId={null} sessionId={null}/>);
 fireEvent.click(await screen.findByRole('button',{name:'Pause'}));
 await waitFor(()=>expect(mocked).toHaveBeenCalledWith('/api/workspace/schedules/schedule',expect.objectContaining({method:'PATCH',body:JSON.stringify({revision:4,enabled:false})})));
 await waitFor(()=>expect(screen.getByRole('button',{name:'Run now'})).toBeEnabled());
 const base=mocked.getMockImplementation()!;mocked.mockImplementation(async(path,init)=>{if(path.endsWith('/run'))throw new Error('This conversation already has an active turn');return base(path,init)});
 fireEvent.click(screen.getByRole('button',{name:'Run now'}));
 expect(await screen.findByRole('alert')).toHaveTextContent('already has an active turn');
 expect(mocked.mock.calls.filter(([path])=>path.endsWith('/run'))).toHaveLength(1);
});

it.each(['success','failure'])('ignores previous-project save %s without closing the current editor',async outcome=>{
 let resolveSave!:(value:unknown)=>void,rejectSave!:(reason:Error)=>void;const base=mocked.getMockImplementation()!;
 mocked.mockImplementation(async(path,init)=>path==='/api/workspace/schedules/schedule'&&init?.method==='PATCH'?await new Promise((resolve,reject)=>{resolveSave=resolve;rejectSave=reject}):base(path,init));
 const view=render(<ScheduleManager projectId="first" sessionId="chat"/>);
 fireEvent.click(await screen.findByRole('button',{name:'Edit'}));fireEvent.click(screen.getByRole('button',{name:'Save schedule'}));
 await waitFor(()=>expect(resolveSave).toBeDefined());expect(screen.getByLabelText('Task prompt')).toBeDisabled();
 view.rerender(<ScheduleManager projectId="second" sessionId="chat"/>);await screen.findByRole('button',{name:'Edit'});
 fireEvent.click(screen.getByRole('button',{name:'New scheduled task'}));fireEvent.change(screen.getByLabelText('Task prompt'),{target:{value:'Current project instruction'}});
 await act(async()=>{if(outcome==='success')resolveSave(row);else rejectSave(new Error('Old project failure'))});
 expect(screen.getByRole('form',{name:'Create scheduled task'})).toBeInTheDocument();expect(screen.getByLabelText('Task prompt')).toHaveValue('Current project instruction');expect(screen.queryByRole('alert')).toBeNull();expect(screen.queryByText('Schedule saved')).toBeNull();
});
it('does not dispatch authorization after preview finishes in another project',async()=>{
 let resolvePreview!:(value:unknown)=>void;const base=mocked.getMockImplementation()!;
 mocked.mockImplementation(async(path,init)=>path.endsWith('/preview')?await new Promise(resolve=>{resolvePreview=resolve}):base(path,init));
 const view=render(<ScheduleManager projectId="first" sessionId="chat"/>);fireEvent.click(await screen.findByRole('button',{name:'New scheduled task'}));
 fireEvent.change(screen.getByLabelText('Success criteria'),{target:{value:'Report results'}});fireEvent.change(screen.getByLabelText('Task prompt'),{target:{value:'Run once'}});fireEvent.click(screen.getByLabelText(/Allow unattended runs/));fireEvent.click(screen.getByRole('button',{name:'Authorize and create schedule'}));
 await waitFor(()=>expect(resolvePreview).toBeDefined());view.rerender(<ScheduleManager projectId="second" sessionId="chat"/>);await act(async()=>resolvePreview({next_runs:[1800000000]}));
 expect(mocked.mock.calls.some(([path,init])=>path==='/api/workspace/automations'&&init?.method==='POST')).toBe(false);
});
it('admits only one save before controls disable',async()=>{
 let resolvePreview!:(value:unknown)=>void;const base=mocked.getMockImplementation()!;
 mocked.mockImplementation(async(path,init)=>path.endsWith('/preview')?await new Promise(resolve=>{resolvePreview=resolve}):base(path,init));
 render(<ScheduleManager projectId={null} sessionId="chat"/>);fireEvent.click(await screen.findByRole('button',{name:'Edit'}));const form=screen.getByRole('form',{name:'Edit scheduled task'});act(()=>{fireEvent.submit(form);fireEvent.submit(form)});
 expect(mocked.mock.calls.filter(([path])=>path.endsWith('/preview'))).toHaveLength(1);await act(async()=>resolvePreview({next_runs:[1800000000]}));
});

it('lets remote users stop a schedule while execution is still preparing',async()=>{
 const base=mocked.getMockImplementation()!;
 mocked.mockImplementation(async(path,init)=>path.includes('/automations')?{schedule:[row],schedule_run:[{id:'pending-run',schedule_id:row.id,status:'dispatching',due:1800000000}],goal:[],delegation:[]}:base(path,init));
 render(<ScheduleManager projectId={null} sessionId="chat"/>);
 fireEvent.click(await screen.findByRole('button',{name:'Run project checks'}));
 expect(screen.getByText('Preparing run')).toBeInTheDocument();
 fireEvent.click(screen.getByRole('button',{name:'Stop run'}));
 await waitFor(()=>expect(mocked).toHaveBeenCalledWith('/api/workspace/schedule-runs/pending-run/stop',expect.objectContaining({method:'POST'})));
});
