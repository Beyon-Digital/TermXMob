import {webcrypto} from 'node:crypto';
import {afterEach,beforeEach,describe,expect,it,vi} from 'vitest';
import {act,cleanup,fireEvent,render,screen,waitFor,within} from '@testing-library/react';
import McpConnectionForm from './McpConnectionForm';
import McpCredentialForm from './McpCredentialForm';
import McpManager from './McpManager';
import SettingsSurface from './SettingsSurface';
import {request} from '../lib/api';
vi.mock('../lib/api',()=>({request:vi.fn(),json:(method:string,value:unknown)=>({method,body:JSON.stringify(value)})}));
const definition={id:'connection.fixture',label:'Fixture tools',transport:'http',url:'https://tools.example/mcp',owner:'user',allowed_projects:['project-a'],approved_tools:[],trust:'untrusted',enabled:true,auth:{method:'env'}};
beforeEach(()=>{cleanup();sessionStorage.clear();vi.stubGlobal('crypto',webcrypto);vi.mocked(request).mockReset()});
afterEach(()=>{cleanup();vi.unstubAllGlobals();vi.restoreAllMocks()});

const projects=[{id:'project-a',name:'A'},{id:'project-b',name:'B'},{id:'project-c',name:'C'}];
const catalog={tools:[{name:'read',description:'Read one record',inputSchema:{type:'object',properties:{id:{type:'string'}}}}]};
const connection=(allowed=['project-a'],changes:Record<string,unknown>={})=>({id:definition.id,label:definition.label,definition:{...definition,allowed_projects:allowed,approved_tools:['read'],trust:'trusted'},definition_digest:'revision',credential_configured:true,runtime:{connected:false},transport:'http',trust:'trusted',allowed_projects:allowed,catalog,...changes});
function deferred<T>(){let resolve!:(value:T)=>void;const promise=new Promise<T>(done=>{resolve=done});return {promise,resolve};}
function operationCalls(name:string){return vi.mocked(request).mock.calls.filter(([,init])=>JSON.parse(String(init?.body)).query.includes(name));}
function respond(rows=[connection()],mutate:(query:string,variables:Record<string,unknown>)=>Promise<unknown>=async query=>query.includes('call_mcp_tool')?{call_mcp_tool:{text:'Fixture result'}}:{connect_mcp_connection:{status:'connected',catalog}}){
 vi.mocked(request).mockImplementation(async(_path,init)=>{const body=JSON.parse(String(init?.body));return {data:body.query.startsWith('mutation')?await mutate(body.query,body.variables):{mcp_connections:rows,projects}}});
}
async function inspectTool(){fireEvent.click(await screen.findByRole('button',{name:'Inspect tools and permissions'}));fireEvent.change(screen.getByRole('combobox',{name:'Tool to inspect'}),{target:{value:'read'}});}
function openAdvanced(){fireEvent.click(screen.getByText('Advanced · run this tool',{selector:'summary'}));}
async function reviewAndConfirm(text='{}'){await inspectTool();openAdvanced();fireEvent.change(screen.getByRole('textbox',{name:'Tool arguments'}),{target:{value:text}});fireEvent.click(screen.getByRole('button',{name:'Call selected tool'}));fireEvent.click(screen.getByRole('button',{name:'Confirm tool call'}));}
function acknowledgeNewInstruction(){fireEvent.click(screen.getByRole('checkbox',{name:'I reviewed this exact call and authorize a new instruction that may repeat its effects.'}));fireEvent.click(screen.getByRole('button',{name:'Acknowledge new instruction'}));}

describe('unconfirmed direct tool call receipts',()=>{
 it('blocks a canonical identical repeat after an effect followed by a lost response and stores no plaintext arguments or identity',async()=>{
  let effects=0;respond([connection()],async()=>{effects++;throw Error('Response lost after dispatch')});render(<McpManager ownerKey="https://fixture-host|owner-a" projectId={null} admin search="" version={0} onChanged={vi.fn()}/>);
  await reviewAndConfirm('{"token":"synthetic-private-tool-value","nested":{"b":2,"a":1}}');expect(await screen.findByRole('status',{name:'Unconfirmed tool call'})).toBeVisible();expect(effects).toBe(1);
  const stored=Array.from({length:sessionStorage.length},(_,index)=>{const key=sessionStorage.key(index)!;return [key,sessionStorage.getItem(key)]});expect(JSON.stringify(stored)).not.toMatch(/synthetic-private-tool-value|owner-a|fixture-host|token|nested/);expect(stored).toHaveLength(1);expect(stored[0][0]).toMatch(/^termx-mcp-call-v1:[a-f0-9]{64}:[a-f0-9]{64}$/);
  fireEvent.change(screen.getByRole('textbox',{name:'Tool arguments'}),{target:{value:'{"nested":{"a":1,"b":2},"token":"synthetic-private-tool-value"}'}});fireEvent.click(screen.getByRole('button',{name:'Call selected tool'}));fireEvent.click(screen.getByRole('button',{name:'Confirm tool call'}));expect(await screen.findByRole('status',{name:'Unconfirmed tool call'})).toBeVisible();expect(effects).toBe(1);expect(operationCalls('call_mcp_tool')).toHaveLength(1);
 });
 it('retains an old unconfirmed receipt after remount without expiring it into a silent repeat',async()=>{
  respond([connection()],async()=>{throw Error('Host timeout')});const view=render(<McpManager ownerKey="host|owner" projectId={null} admin search="" version={0} onChanged={vi.fn()}/>);await reviewAndConfirm('{"id":"same"}');await screen.findByRole('status',{name:'Unconfirmed tool call'});
  const key=sessionStorage.key(0)!,saved=JSON.parse(sessionStorage.getItem(key)!);sessionStorage.setItem(key,JSON.stringify({...saved,started_at:1}));view.unmount();respond();render(<McpManager ownerKey="host|owner" projectId={null} admin search="" version={0} onChanged={vi.fn()}/>);await reviewAndConfirm('{"id":"same"}');expect(await screen.findByRole('status',{name:'Unconfirmed tool call'})).toBeVisible();expect(operationCalls('call_mcp_tool')).toHaveLength(1);
 });
 it('isolates receipt matching by actor and host',async()=>{
  respond([connection()],async()=>{throw Error('Lost response')});const view=render(<McpManager ownerKey="host-one|owner-a" projectId={null} admin search="" version={0} onChanged={vi.fn()}/>);await reviewAndConfirm();await screen.findByRole('status',{name:'Unconfirmed tool call'});view.unmount();
  respond();const other=render(<McpManager ownerKey="host-one|owner-b" projectId={null} admin search="" version={0} onChanged={vi.fn()}/>);await reviewAndConfirm();expect(await screen.findByRole('region',{name:'Tool result'})).toHaveTextContent('Fixture result');expect(operationCalls('call_mcp_tool')).toHaveLength(2);other.unmount();
  render(<McpManager ownerKey="host-two|owner-a" projectId={null} admin search="" version={0} onChanged={vi.fn()}/>);await reviewAndConfirm();await screen.findByRole('region',{name:'Tool result'});expect(operationCalls('call_mcp_tool')).toHaveLength(3);
 });
 it('requires explicit possible-repeat acknowledgment and a fresh review before dispatching a new instruction',async()=>{
  let effects=0;respond([connection()],async()=>{if(++effects===1)throw Error('Response lost after dispatch');return {call_mcp_tool:{text:'New instruction result'}}});render(<McpManager ownerKey="host|owner" projectId={null} admin search="" version={0} onChanged={vi.fn()}/>);await reviewAndConfirm('{"id":"same"}');await screen.findByRole('status',{name:'Unconfirmed tool call'});
  expect(screen.getByRole('button',{name:'Acknowledge new instruction'})).toBeDisabled();expect(screen.getByRole('button',{name:'Call selected tool'})).toBeDisabled();acknowledgeNewInstruction();expect(effects).toBe(1);expect(screen.queryByRole('button',{name:'Confirm tool call'})).toBeNull();
  fireEvent.click(screen.getByRole('button',{name:'Call selected tool'}));expect(effects).toBe(1);fireEvent.click(screen.getByRole('button',{name:'Confirm tool call'}));expect(await screen.findByRole('region',{name:'Tool result'})).toHaveTextContent('New instruction result');expect(effects).toBe(2);expect(sessionStorage.length).toBe(0);
 });
 it('cannot clear a newer unconfirmed instruction when an older remounted call eventually succeeds',async()=>{
  const old=deferred<{call_mcp_tool:{text:string}}>();let effects=0;respond([connection()],async()=>{if(++effects===1)return old.promise;throw Error('New instruction response lost')});const first=render(<McpManager ownerKey="host|owner" projectId={null} admin search="" version={0} onChanged={vi.fn()}/>);await reviewAndConfirm();await waitFor(()=>expect(operationCalls('call_mcp_tool')).toHaveLength(1));first.unmount();
  const second=render(<McpManager ownerKey="host|owner" projectId={null} admin search="" version={0} onChanged={vi.fn()}/>);await reviewAndConfirm();await screen.findByRole('status',{name:'Unconfirmed tool call'});acknowledgeNewInstruction();fireEvent.click(screen.getByRole('button',{name:'Call selected tool'}));fireEvent.click(screen.getByRole('button',{name:'Confirm tool call'}));await screen.findByRole('status',{name:'Unconfirmed tool call'});expect(effects).toBe(2);
  await act(async()=>old.resolve({call_mcp_tool:{text:'Old result'}}));second.unmount();render(<McpManager ownerKey="host|owner" projectId={null} admin search="" version={0} onChanged={vi.fn()}/>);await reviewAndConfirm();await screen.findByRole('status',{name:'Unconfirmed tool call'});expect(effects).toBe(2);
 });
 it('fails before dispatch if receipt storage cannot be written',async()=>{
  respond();vi.spyOn(Storage.prototype,'setItem').mockImplementation(()=>{throw Error('Storage unavailable')});render(<McpManager ownerKey="host|owner" projectId={null} admin search="" version={0} onChanged={vi.fn()}/>);await reviewAndConfirm();expect(await screen.findByRole('alert')).toHaveTextContent('Storage unavailable');expect(operationCalls('call_mcp_tool')).toHaveLength(0);
 });
 it('bounds retained receipts without silently deleting uncertain history',async()=>{
  for(let index=0;index<64;index++)sessionStorage.setItem('termx-mcp-call-v1:fixture-'+index,JSON.stringify({state:'uncertain',started_at:1,attempt:'prior-'+index}));
  respond();render(<McpManager ownerKey="host|owner" projectId={null} admin search="" version={0} onChanged={vi.fn()}/>);await reviewAndConfirm();expect(await screen.findByRole('alert')).toHaveTextContent('64 unconfirmed tool calls');expect(operationCalls('call_mcp_tool')).toHaveLength(0);expect(sessionStorage.length).toBe(64);
 });
 it('keeps inspection available but disables direct execution without a verified owner scope',async()=>{
  respond();render(<McpManager projectId={null} admin search="" version={0} onChanged={vi.fn()}/>);await inspectTool();openAdvanced();expect(screen.getByRole('button',{name:'Call selected tool'})).toBeDisabled();expect(screen.getByText('Tool execution requires a verified account and host identity.')).toBeVisible();expect(operationCalls('call_mcp_tool')).toHaveLength(0);
 });
});

describe('explicit connection execution targets',()=>{
 it('connects the sole accessible allowlisted project instead of the selected workspace',async()=>{
  respond();render(<McpManager ownerKey="https://fixture-host|owner-a" projectId="project-b" admin search="" version={0} onChanged={vi.fn()}/>);
  fireEvent.click(await screen.findByRole('button',{name:'Connect'}));await waitFor(()=>expect(operationCalls('connect_mcp_connection')).toHaveLength(1));
  expect(JSON.parse(String(operationCalls('connect_mcp_connection')[0][1]?.body)).variables).toEqual({id:definition.id,project:'project-a'});
 });
 it('requires a choice for each connection and offers no project outside its allowlist',async()=>{
  const second=connection(['project-a','project-c'],{id:'connection.second',label:'Second tools'});respond([connection(['project-a','project-b']),second]);
  render(<McpManager ownerKey="https://fixture-host|owner-a" projectId="project-c" admin search="" version={0} onChanged={vi.fn()}/>);
  const target=await screen.findByRole('combobox',{name:'Connection target for Fixture tools'});
  expect(within(target).queryByRole('option',{name:'C'})).toBeNull();expect(screen.getAllByRole('button',{name:'Connect'}).every(button=>(button as HTMLButtonElement).disabled)).toBe(true);
  fireEvent.change(target,{target:{value:'project-b'}});
  expect(screen.getByRole('combobox',{name:'Connection target for Second tools'})).toHaveValue('');
  fireEvent.click(screen.getAllByRole('button',{name:'Connect'})[0]);await waitFor(()=>expect(operationCalls('connect_mcp_connection')).toHaveLength(1));
  expect(JSON.parse(String(operationCalls('connect_mcp_connection')[0][1]?.body)).variables.project).toBe('project-b');
 });
 it('keeps inaccessible project definitions inspectable without allowing a connect',async()=>{
  respond([connection(['inaccessible'])]);render(<McpManager ownerKey="https://fixture-host|owner-a" projectId={null} admin search="" version={0} onChanged={vi.fn()}/>);
  expect(await screen.findByText('No accessible project is in this connection’s allowlist.')).toBeVisible();expect(screen.getByRole('button',{name:'Connect'})).toBeDisabled();
  await inspectTool();openAdvanced();expect(screen.getByRole('button',{name:'Call selected tool'})).toBeDisabled();expect(operationCalls('mutation')).toHaveLength(0);
 });
 it('allows a host-wide connection only for an administrator and sends an explicit null target',async()=>{
  respond([connection([])]);const view=render(<McpManager ownerKey="https://fixture-host|owner-a" projectId="project-b" admin={false} search="" version={0} onChanged={vi.fn()}/>);
  expect(await screen.findByRole('button',{name:'Connect'})).toBeDisabled();await inspectTool();openAdvanced();expect(screen.getByRole('button',{name:'Call selected tool'})).toBeDisabled();
  view.rerender(<McpManager ownerKey="https://fixture-host|owner-a" projectId="project-b" admin search="" version={0} onChanged={vi.fn()}/>);
  await waitFor(()=>expect(screen.getByRole('button',{name:'Connect'})).toBeEnabled());fireEvent.click(screen.getByRole('button',{name:'Connect'}));
  await waitFor(()=>expect(operationCalls('connect_mcp_connection')).toHaveLength(1));expect(JSON.parse(String(operationCalls('connect_mcp_connection')[0][1]?.body)).variables.project).toBeNull();
 });
 it('preserves project-scoped tool use for a non-administrator while restricting host disconnect',async()=>{
  respond([connection(['project-a'],{runtime:{connected:true}})]);render(<McpManager ownerKey="https://fixture-host|owner-a" projectId="project-a" admin={false} search="" version={0} onChanged={vi.fn()}/>);await inspectTool();expect(screen.queryByRole('button',{name:'Disconnect'})).toBeNull();openAdvanced();expect(screen.getByRole('button',{name:'Call selected tool'})).toBeEnabled();fireEvent.click(screen.getByRole('button',{name:'Call selected tool'}));fireEvent.click(screen.getByRole('button',{name:'Confirm tool call'}));await waitFor(()=>expect(operationCalls('call_mcp_tool')).toHaveLength(1));expect(JSON.parse(String(operationCalls('call_mcp_tool')[0][1]?.body)).variables.project).toBe('project-a');
 });
});

describe('read-only inspection and explicit advanced execution',()=>{
 it('inspects saved schemas without connecting, calling, or exposing execution by default',async()=>{
  respond();render(<McpManager ownerKey="https://fixture-host|owner-a" projectId={null} admin search="" version={0} onChanged={vi.fn()}/>);await inspectTool();
  expect(screen.getByRole('region',{name:'Selected tool input schema'})).toHaveTextContent('object');expect(screen.queryByRole('button',{name:'Call selected tool'})).toBeNull();expect(screen.queryByRole('textbox',{name:'Tool arguments'})).toBeNull();expect(request).toHaveBeenCalledTimes(1);
 });
 it('reviews exact target and arguments before one confirmed call, preserving the useful direct-call capability',async()=>{
  respond([connection(['project-a','project-b'])]);render(<McpManager ownerKey="https://fixture-host|owner-a" projectId="project-c" admin search="" version={0} onChanged={vi.fn()}/>);
  fireEvent.change(await screen.findByRole('combobox',{name:'Connection target for Fixture tools'}),{target:{value:'project-b'}});await inspectTool();openAdvanced();
  fireEvent.change(screen.getByRole('textbox',{name:'Tool arguments'}),{target:{value:'{"id":"record-7","limit":2}'}});fireEvent.click(screen.getByRole('button',{name:'Call selected tool'}));
  expect(screen.getByRole('region',{name:'Review tool call'})).toHaveTextContent('project-b');expect(screen.getByRole('region',{name:'Exact tool call arguments'})).toHaveTextContent('record-7');expect(operationCalls('call_mcp_tool')).toHaveLength(0);
  fireEvent.click(screen.getByRole('button',{name:'Confirm tool call'}));await waitFor(()=>expect(operationCalls('call_mcp_tool')).toHaveLength(1));
  expect(JSON.parse(String(operationCalls('call_mcp_tool')[0][1]?.body)).variables).toEqual({id:definition.id,tool:'read',input:{arguments:{id:'record-7',limit:2}},project:'project-b',digest:'revision'});expect(await screen.findByRole('region',{name:'Tool result'})).toHaveTextContent('Fixture result');
 });
 it.each(['untrusted','disabled','unapproved'])('retains %s permission guards inside the advanced action',async(reason)=>{
  const row=connection();if(reason==='untrusted')row.trust='untrusted';if(reason==='disabled')row.definition.enabled=false;if(reason==='unapproved')row.definition.approved_tools=[];
  respond([row]);render(<McpManager ownerKey="https://fixture-host|owner-a" projectId={null} admin search="" version={0} onChanged={vi.fn()}/>);await inspectTool();openAdvanced();expect(screen.getByRole('button',{name:'Call selected tool'})).toBeDisabled();expect(operationCalls('call_mcp_tool')).toHaveLength(0);
 });
 it.each(['[]','null','{"id":'])('rejects invalid or non-object arguments without dispatch (%s)',async(text)=>{
  respond();render(<McpManager ownerKey="https://fixture-host|owner-a" projectId={null} admin search="" version={0} onChanged={vi.fn()}/>);await inspectTool();openAdvanced();fireEvent.change(screen.getByRole('textbox',{name:'Tool arguments'}),{target:{value:text}});fireEvent.click(screen.getByRole('button',{name:'Call selected tool'}));expect(await screen.findByRole('alert')).toBeVisible();expect(screen.queryByRole('button',{name:'Confirm tool call'})).toBeNull();expect(operationCalls('call_mcp_tool')).toHaveLength(0);
 });
 it('invalidates confirmation when the target or arguments change',async()=>{
  respond([connection(['project-a','project-b'])]);render(<McpManager ownerKey="https://fixture-host|owner-a" projectId={null} admin search="" version={0} onChanged={vi.fn()}/>);
  const target=await screen.findByRole('combobox',{name:'Connection target for Fixture tools'});fireEvent.change(target,{target:{value:'project-a'}});await inspectTool();openAdvanced();
  fireEvent.click(screen.getByRole('button',{name:'Call selected tool'}));fireEvent.change(target,{target:{value:'project-b'}});expect(screen.queryByRole('button',{name:'Confirm tool call'})).toBeNull();
  fireEvent.click(screen.getByRole('button',{name:'Call selected tool'}));fireEvent.change(screen.getByRole('textbox',{name:'Tool arguments'}),{target:{value:'{"id":"new"}'}});expect(screen.queryByRole('button',{name:'Confirm tool call'})).toBeNull();expect(operationCalls('call_mcp_tool')).toHaveLength(0);
 });
 it('rejects oversized arguments before confirmation or dispatch',async()=>{
  respond();render(<McpManager ownerKey="https://fixture-host|owner-a" projectId={null} admin search="" version={0} onChanged={vi.fn()}/>);await inspectTool();openAdvanced();fireEvent.change(screen.getByRole('textbox',{name:'Tool arguments'}),{target:{value:JSON.stringify({text:'x'.repeat(64000)})}});fireEvent.click(screen.getByRole('button',{name:'Call selected tool'}));expect(await screen.findByRole('alert')).toHaveTextContent('64,000 bytes');expect(screen.queryByRole('button',{name:'Confirm tool call'})).toBeNull();expect(operationCalls('call_mcp_tool')).toHaveLength(0);
 });
 it('prevents duplicate dispatch and fences a late result after closing inspection',async()=>{
  const pending=deferred<{call_mcp_tool:{text:string}}>();respond([connection()],()=>pending.promise);render(<McpManager ownerKey="https://fixture-host|owner-a" projectId={null} admin search="" version={0} onChanged={vi.fn()}/>);await inspectTool();openAdvanced();fireEvent.click(screen.getByRole('button',{name:'Call selected tool'}));
  const confirm=screen.getByRole('button',{name:'Confirm tool call'});fireEvent.click(confirm);fireEvent.click(confirm);await waitFor(()=>expect(operationCalls('call_mcp_tool')).toHaveLength(1));
  fireEvent.click(screen.getByRole('button',{name:'Close connection inspection'}));await act(async()=>pending.resolve({call_mcp_tool:{text:'Late result'}}));
  await inspectTool();expect(screen.queryByRole('region',{name:'Tool result'})).toBeNull();expect(screen.queryByText('Late result')).toBeNull();
 });
 it('discards pending review and ignores old results when the definition inventory changes',async()=>{
  const pending=deferred<{call_mcp_tool:{text:string}}>();respond([connection(['project-a','project-b'])],()=>pending.promise);const view=render(<McpManager ownerKey="https://fixture-host|owner-a" projectId={null} admin search="" version={0} onChanged={vi.fn()}/>);
  fireEvent.change(await screen.findByRole('combobox',{name:'Connection target for Fixture tools'}),{target:{value:'project-b'}});await inspectTool();openAdvanced();fireEvent.click(screen.getByRole('button',{name:'Call selected tool'}));fireEvent.click(screen.getByRole('button',{name:'Confirm tool call'}));await waitFor(()=>expect(operationCalls('call_mcp_tool')).toHaveLength(1));
  respond([connection(['project-a'],{definition_digest:'new-revision'})]);view.rerender(<McpManager ownerKey="https://fixture-host|owner-a" projectId={null} admin search="" version={1} onChanged={vi.fn()}/>);await screen.findByRole('button',{name:'Inspect tools and permissions'});await act(async()=>pending.resolve({call_mcp_tool:{text:'Old definition result'}}));
  expect(screen.getByRole('combobox',{name:'Connection target for Fixture tools'})).toHaveValue('project-a');expect(screen.queryByRole('button',{name:'Confirm tool call'})).toBeNull();await inspectTool();expect(screen.queryByRole('region',{name:'Tool result'})).toBeNull();
 });
 it('preserves a host permission rejection and does not replay the call',async()=>{
  respond([connection()],async()=>{throw Error('Permission changed on the host')});render(<McpManager ownerKey="https://fixture-host|owner-a" projectId={null} admin search="" version={0} onChanged={vi.fn()}/>);await inspectTool();openAdvanced();fireEvent.click(screen.getByRole('button',{name:'Call selected tool'}));fireEvent.click(screen.getByRole('button',{name:'Confirm tool call'}));expect(await screen.findByRole('alert')).toHaveTextContent('Permission changed on the host');expect(operationCalls('call_mcp_tool')).toHaveLength(1);expect(screen.queryByRole('region',{name:'Tool result'})).toBeNull();
 });
 it.each([false,true])('acknowledges persisted settings edits only after success (failure=%s)',async(failure)=>{
  respond([connection()],async()=>{if(failure)throw Error('Definition changed on the host');return {create_mcp_connection:{}}});render(<SettingsSurface category="connections" onSelect={vi.fn()} onClose={vi.fn()}><McpManager ownerKey="https://fixture-host|owner-a" projectId={null} admin search="" version={0} onChanged={vi.fn()}/></SettingsSurface>);
  fireEvent.click(await screen.findByRole('button',{name:'Edit connection'}));const form=screen.getByRole('form',{name:'Edit tool connection'});fireEvent.change(within(form).getByLabelText('Connection name'),{target:{value:'Updated tools'}});expect(screen.getByText('Unsaved settings changes')).toBeVisible();fireEvent.click(within(form).getByRole('button',{name:'Save connection changes'}));
  if(failure){expect(await screen.findByRole('alert')).toHaveTextContent('Definition changed on the host');expect(screen.getByText('Unsaved settings changes')).toBeVisible()}else await waitFor(()=>expect(screen.queryByText('Unsaved settings changes')).toBeNull());
 });
});
describe('project-scoped MCP controls',()=>{it('saves explicit project allowlist without connecting or trusting and preserves empty tool denylist',async()=>{const onSave=vi.fn().mockResolvedValue(true);render(<McpConnectionForm connection={definition} projects={[{id:'project-a',name:'A'},{id:'project-b',name:'B'}]} busy={false} onSave={onSave}/>);fireEvent.click(screen.getByRole('checkbox',{name:/^B$/}));fireEvent.click(screen.getByRole('button',{name:'Save connection changes'}));await waitFor(()=>expect(onSave).toHaveBeenCalledTimes(1));expect(onSave.mock.calls[0][0]).toMatchObject({allowed_projects:['project-a','project-b'],approved_tools:[],trust:'untrusted'});expect(request).not.toHaveBeenCalled()});it('refuses selected-project mode with no selected project',async()=>{const onSave=vi.fn();render(<McpConnectionForm projects={[{id:'project-a',name:'A'}]} busy={false} onSave={onSave}/>);fireEvent.change(screen.getByLabelText('Connection key (generated automatically)'),{target:{value:'new'}});fireEvent.change(screen.getByLabelText('Connection name'),{target:{value:'New'}});fireEvent.change(screen.getByLabelText('Server address'),{target:{value:'https://tools.example/mcp'}});fireEvent.click(screen.getByRole('radio',{name:'Only selected projects'}));fireEvent.submit(screen.getByRole('form',{name:'Add tool connection'}));expect(await screen.findByRole('alert')).toHaveTextContent('Choose at least one');expect(onSave).not.toHaveBeenCalled()});it('credential is write-only and cleared immediately even when the request remains pending',()=>{const onSave=vi.fn(()=>new Promise<boolean>(()=>{}));render(<McpCredentialForm connection={definition} busy={false} onSave={onSave}/>);fireEvent.change(screen.getByLabelText('New secret value'),{target:{value:'fixture-secret-never-echoed'}});fireEvent.click(screen.getByRole('button',{name:'Save credential binding'}));expect(onSave).toHaveBeenCalledWith('Authorization','fixture-secret-never-echoed','header');expect(screen.getByLabelText('New secret value')).toHaveValue('');expect(document.body).not.toHaveTextContent('fixture-secret-never-echoed')});it('uses its allowed target independently of the selected workspace and keeps exact scope inspection accessible',async()=>{vi.mocked(request).mockResolvedValue({data:{mcp_connections:[{id:definition.id,label:definition.label,definition,definition_digest:'revision',credential_configured:true,runtime:{connected:false},transport:'http',trust:'trusted',allowed_projects:['project-a']}],projects:[{id:'project-a',name:'A'},{id:'project-b',name:'B'}]}});render(<McpManager ownerKey="https://fixture-host|owner-a" projectId="project-b" admin={true} search="" version={0} onChanged={vi.fn()}/>);await screen.findByRole('heading',{name:'Fixture tools'});expect(screen.getByRole('button',{name:'Connect'})).toBeEnabled();expect(screen.getByRole('combobox',{name:'Connection target for Fixture tools'})).toHaveValue('project-a');fireEvent.click(screen.getByRole('button',{name:'Inspect tools and permissions'}));fireEvent.click(screen.getByText('Exact connection configuration'));expect(screen.getByRole('region',{name:'Connection configuration'})).toHaveTextContent('project-a');expect(screen.getByRole('region',{name:'Connection configuration'})).toHaveAttribute('tabindex','0')})});
