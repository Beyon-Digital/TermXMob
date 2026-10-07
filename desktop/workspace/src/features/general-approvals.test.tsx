import {fireEvent,render,screen,waitFor,cleanup} from '@testing-library/react';
import {afterEach,beforeEach,describe,expect,it,vi} from 'vitest';
import {GeneralApprovalCard} from './GeneralApprovalCard';
import {GeneralPolicyManager} from './GeneralPolicyManager';
const mocked=vi.hoisted(()=>({request:vi.fn()}));vi.mock('../lib/api',()=>mocked);
afterEach(cleanup);beforeEach(()=>{mocked.request.mockReset()});
describe('host-advertised coding consent',()=>{
 it('sends only the selected advertised conversation scope and exact approval',async()=>{
  mocked.request.mockResolvedValue({});const onDone=vi.fn();render(<GeneralApprovalCard taskId="task-a" approval={{id:'approval-a',payload:{remember_options:['conversation','invented','project'],reason:'Exact publication'}}} onDone={onDone} onError={vi.fn()}/>);
  expect(screen.queryByText('This task')).toBeNull();expect(screen.queryByText('invented')).toBeNull();
  fireEvent.change(screen.getByRole('combobox',{name:'Remember approval scope'}),{target:{value:'conversation'}});fireEvent.click(screen.getByRole('button',{name:'Allow and remember'}));
  await waitFor(()=>expect(onDone).toHaveBeenCalledOnce());expect(mocked.request.mock.calls[0][0]).toBe('/api/workspace/tasks/task-a/approvals/approval-a/resolve');expect(JSON.parse(mocked.request.mock.calls[0][1].body)).toEqual({decision:'approved',remember:'conversation'});
 });
 it('falls back visibly to once when a scope is withdrawn from the same approval',async()=>{
  mocked.request.mockResolvedValue({});const props={taskId:'t',onDone:vi.fn(),onError:vi.fn()};
  const view=render(<GeneralApprovalCard {...props} approval={{id:'a',payload:{remember_options:['project']}}}/>);
  fireEvent.change(screen.getByRole('combobox',{name:'Remember approval scope'}),{target:{value:'project'}});
  view.rerender(<GeneralApprovalCard {...props} approval={{id:'a',payload:{remember_options:[]}}}/>);
  fireEvent.click(screen.getByRole('button',{name:'Allow once'}));await waitFor(()=>expect(mocked.request).toHaveBeenCalledOnce());
  expect(JSON.parse(mocked.request.mock.calls[0][1].body)).toEqual({decision:'approved'});
 });
 it('native approvals without declared scopes expose only exact one-time consent',async()=>{
  mocked.request.mockResolvedValue({});render(<GeneralApprovalCard taskId="native" approval={{id:'a',payload:{tool:'native permission'}}} onDone={vi.fn()} onError={vi.fn()}/>);
  expect(screen.queryByRole('combobox')).toBeNull();fireEvent.click(screen.getByRole('button',{name:'Deny'}));await waitFor(()=>expect(mocked.request).toHaveBeenCalled());expect(JSON.parse(mocked.request.mock.calls[0][1].body)).toEqual({decision:'denied'});
 });
 it('fences a late response after approval replacement and prevents duplicate dispatch',async()=>{
  let finish!:(value:unknown)=>void;mocked.request.mockImplementation(()=>new Promise(resolve=>{finish=resolve}));const done=vi.fn(),error=vi.fn();
  const view=render(<GeneralApprovalCard taskId="task-a" approval={{id:'a',payload:{}}} onDone={done} onError={error}/>);
  const allow=screen.getByRole('button',{name:'Allow once'});fireEvent.click(allow);fireEvent.click(allow);expect(mocked.request).toHaveBeenCalledOnce();
  view.rerender(<GeneralApprovalCard taskId="task-b" approval={{id:'b',payload:{}}} onDone={done} onError={error}/>);finish({});await waitFor(()=>expect(screen.getByRole('button',{name:'Allow once'})).not.toBeDisabled());expect(done).not.toHaveBeenCalled();expect(error).not.toHaveBeenCalled();
 });
 it('failed decisions preserve the exact request and show the host rejection',async()=>{
  mocked.request.mockRejectedValue(new Error('Policy changed; request fresh consent'));render(<GeneralApprovalCard taskId="t" approval={{id:'a',payload:{remember_options:['task']}}} onDone={()=>{}} onError={()=>{}}/>);
  fireEvent.click(screen.getByRole('button',{name:'Allow once'}));await screen.findByRole('alert');expect(screen.getByRole('alert').textContent).toContain('Policy changed');expect(screen.getByRole('button',{name:'Allow once'})).not.toBeDisabled();
 });
 it('keeps stale conflict edits and never sends matcher or binding changes',async()=>{
  const row={id:'r',version:3,effect:'allow',scope_type:'project',scope_id:'p',tool:'run_shell',display:'exact command',expires_at:Date.now()/1000+1000,revoked:false,editable:true,policy_source:'Your bounded remembered consent',binding:{principal_id:'alice'}};
  mocked.request.mockImplementation((path:string)=>path==='/api/workspace/coding-policies'?Promise.resolve({rules:[row]}):Promise.reject(new Error('Coding policy changed; refresh before editing')));
  render(<GeneralPolicyManager/>);fireEvent.click(await screen.findByRole('button',{name:'Edit coding policy'}));fireEvent.change(screen.getByRole('combobox',{name:'Coding decision'}),{target:{value:'deny'}});fireEvent.click(screen.getByRole('button',{name:'Save coding policy'}));await screen.findByRole('alert');expect((screen.getByRole('combobox',{name:'Coding decision'}) as HTMLSelectElement).value).toBe('deny');expect(JSON.parse(mocked.request.mock.calls.at(-1)![1].body)).toEqual({version:3,effect:'deny',expires_in:3600});
 });
});

describe('coding policy lifecycle',()=>{
 it('reloads fresh policy inventory after unlock and ignores the old observation',async()=>{
  let old!:(value:unknown)=>void;
  const fresh={id:'new',version:1,effect:'deny',scope_type:'project',scope_id:'p',tool:'write_file',display:'Current policy',expires_at:null,revoked:false,editable:false,policy_source:'Current source',binding:{}};
  mocked.request.mockImplementationOnce(()=>new Promise(resolve=>{old=resolve})).mockResolvedValue({rules:[fresh]});
  render(<GeneralPolicyManager/>);window.dispatchEvent(new Event('termx-locked'));window.dispatchEvent(new Event('termx-session-unlocked'));
  await screen.findByText('Current policy');old({rules:[{...fresh,id:'old',display:'Old account policy'}]});
  await waitFor(()=>expect(mocked.request).toHaveBeenCalledTimes(2));expect(screen.queryByText('Old account policy')).toBeNull();expect(screen.getByText('Current policy')).toBeTruthy();
 });
 it('dispatches only one CAS mutation for synchronous duplicate clicks',async()=>{
  const row={id:'r',version:1,effect:'allow',scope_type:'project',scope_id:'p',tool:'write_file',display:'Exact edit',expires_at:Date.now()/1000+300,revoked:false,editable:true,policy_source:'Your bounded consent',binding:{principal_id:'alice'}};
  let finish!:(value:unknown)=>void;mocked.request.mockImplementation((path:string)=>path==='/api/workspace/coding-policies'?Promise.resolve({rules:[row]}):new Promise(resolve=>{finish=resolve}));
  render(<GeneralPolicyManager/>);const revoke=await screen.findByRole('button',{name:'Revoke coding policy'});fireEvent.click(revoke);fireEvent.click(revoke);
  expect(mocked.request.mock.calls.filter(([path])=>path.includes('/r'))).toHaveLength(1);finish({});await waitFor(()=>expect(revoke).not.toBeDisabled());
 });
});
