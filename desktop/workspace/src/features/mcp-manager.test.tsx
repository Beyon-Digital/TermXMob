import {beforeEach,describe,expect,it,vi} from 'vitest';
import {cleanup,fireEvent,render,screen,waitFor,within} from '@testing-library/react';
import McpConnectionForm from './McpConnectionForm';
import McpCredentialForm from './McpCredentialForm';
import McpManager from './McpManager';
import SettingsSurface from './SettingsSurface';
import {request} from '../lib/api';
vi.mock('../lib/api',()=>({request:vi.fn(),json:(method:string,value:unknown)=>({method,body:JSON.stringify(value)})}));
const definition={id:'connection.fixture',label:'Fixture tools',transport:'http',url:'https://tools.example/mcp',owner:'user',allowed_projects:['project-a'],approved_tools:[],trust:'untrusted',enabled:true,auth:{method:'env'}};
beforeEach(()=>{cleanup();vi.mocked(request).mockReset()});
describe('project-scoped MCP controls',()=>{it('saves explicit project allowlist without connecting or trusting and preserves empty tool denylist',async()=>{const onSave=vi.fn().mockResolvedValue(true);render(<McpConnectionForm connection={definition} projects={[{id:'project-a',name:'A'},{id:'project-b',name:'B'}]} busy={false} onSave={onSave}/>);fireEvent.click(screen.getByRole('checkbox',{name:/^B$/}));fireEvent.click(screen.getByRole('button',{name:'Save connection changes'}));await waitFor(()=>expect(onSave).toHaveBeenCalledTimes(1));expect(onSave.mock.calls[0][0]).toMatchObject({allowed_projects:['project-a','project-b'],approved_tools:[],trust:'untrusted'});expect(request).not.toHaveBeenCalled()});it('refuses selected-project mode with no selected project',async()=>{const onSave=vi.fn();render(<McpConnectionForm projects={[{id:'project-a',name:'A'}]} busy={false} onSave={onSave}/>);fireEvent.change(screen.getByLabelText('Connection key (generated automatically)'),{target:{value:'new'}});fireEvent.change(screen.getByLabelText('Connection name'),{target:{value:'New'}});fireEvent.change(screen.getByLabelText('Server address'),{target:{value:'https://tools.example/mcp'}});fireEvent.click(screen.getByRole('radio',{name:'Only selected projects'}));fireEvent.submit(screen.getByRole('form',{name:'Add tool connection'}));expect(await screen.findByRole('alert')).toHaveTextContent('Choose at least one');expect(onSave).not.toHaveBeenCalled()});it('credential is write-only and cleared immediately even when the request remains pending',()=>{const onSave=vi.fn(()=>new Promise<boolean>(()=>{}));render(<McpCredentialForm connection={definition} busy={false} onSave={onSave}/>);fireEvent.change(screen.getByLabelText('New secret value'),{target:{value:'fixture-secret-never-echoed'}});fireEvent.click(screen.getByRole('button',{name:'Save credential binding'}));expect(onSave).toHaveBeenCalledWith('Authorization','fixture-secret-never-echoed','header');expect(screen.getByLabelText('New secret value')).toHaveValue('');expect(document.body).not.toHaveTextContent('fixture-secret-never-echoed')});it('keeps host settings independent of the selected workspace and preserves exact permission inspection',async()=>{vi.mocked(request).mockResolvedValue({data:{mcp_connections:[{id:definition.id,label:definition.label,definition,definition_digest:'revision',credential_configured:true,runtime:{connected:false},transport:'http',trust:'trusted',allowed_projects:['project-a']}],projects:[{id:'project-a',name:'A'},{id:'project-b',name:'B'}]}});render(<McpManager projectId="project-b" admin={true} search="" version={0} onChanged={vi.fn()}/>);await screen.findByRole('heading',{name:'Fixture tools'});expect(screen.getByRole('button',{name:'Connect'})).toBeEnabled();expect(JSON.parse(String(vi.mocked(request).mock.calls[0][1]?.body)).variables).toEqual({project:null});fireEvent.click(screen.getByRole('button',{name:'Inspect tools and permissions'}));fireEvent.click(screen.getByText('Exact connection configuration'));expect(screen.getByRole('region',{name:'Connection configuration'})).toHaveTextContent('project-a');expect(screen.getByRole('region',{name:'Connection configuration'})).toHaveAttribute('tabindex','0')})});


describe('host connection setup',()=>{
 const connection=(allowed:string[],catalog?:unknown)=>({id:definition.id,label:definition.label,definition:{...definition,allowed_projects:allowed,approved_tools:['read'],trust:'trusted'},definition_digest:'revision',credential_configured:true,runtime:{connected:false},transport:'http',trust:'trusted',allowed_projects:allowed,catalog});
 const projects=[{id:'project-a',name:'A'},{id:'project-b',name:'B'},{id:'project-c',name:'C'}];
 function respond(row:ReturnType<typeof connection>){vi.mocked(request).mockImplementation(async(_path,init)=>{const body=JSON.parse(String(init?.body));return body.query.startsWith('mutation')?{data:{connect_mcp_connection:{status:'connected'}}}:{data:{mcp_connections:[row],projects}}})}
 it('uses the sole allowed execution target without a workspace scope selector',async()=>{
  respond(connection(['project-a']));render(<McpManager projectId={null} admin search="" version={0} onChanged={vi.fn()}/>);
  fireEvent.click(await screen.findByRole('button',{name:'Connect'}));
  await waitFor(()=>expect(vi.mocked(request)).toHaveBeenCalledTimes(2));
  expect(JSON.parse(String(vi.mocked(request).mock.calls[1][1]?.body)).variables).toEqual({id:definition.id,project:'project-a'});
  expect(screen.queryByText(/Select a project before using/)).not.toBeInTheDocument();
 });
 it('requires a per-connection target and offers only allowed projects',async()=>{
  respond(connection(['project-a','project-b']));render(<McpManager projectId={null} admin search="" version={0} onChanged={vi.fn()}/>);
  const target=await screen.findByRole('combobox',{name:'Connection target for Fixture tools'});
  expect(screen.getByRole('button',{name:'Connect'})).toBeDisabled();
  expect(screen.queryByRole('option',{name:'C'})).not.toBeInTheDocument();
  fireEvent.change(target,{target:{value:'project-b'}});fireEvent.click(screen.getByRole('button',{name:'Connect'}));
  await waitFor(()=>expect(vi.mocked(request)).toHaveBeenCalledTimes(2));
  expect(JSON.parse(String(vi.mocked(request).mock.calls[1][1]?.body)).variables).toEqual({id:definition.id,project:'project-b'});
 });
 it('keeps tool inspection read-only',async()=>{
  respond(connection(['project-a'],{tools:[{name:'read',description:'Read one record',inputSchema:{type:'object',properties:{id:{type:'string'}}}}]}));
  render(<McpManager projectId={null} admin search="" version={0} onChanged={vi.fn()}/>);
  fireEvent.click(await screen.findByRole('button',{name:'Inspect tools and permissions'}));fireEvent.change(screen.getByRole('combobox',{name:'Tool to inspect'}),{target:{value:'read'}});
  expect(screen.getByRole('region',{name:'Selected tool input schema'})).toHaveTextContent('object');
  expect(screen.queryByRole('button',{name:'Call selected tool'})).not.toBeInTheDocument();expect(screen.queryByLabelText('Tool arguments')).not.toBeInTheDocument();
  expect(vi.mocked(request)).toHaveBeenCalledTimes(1);
 });

 it.each([false,true])('acknowledges saved connection edits only on success (failure=%s)',async(failure)=>{
  const row=connection(['project-a']);
  vi.mocked(request).mockImplementation(async(_path,init)=>{const body=JSON.parse(String(init?.body));if(body.query.startsWith('mutation')){if(failure)throw new Error('Connection changed on the host');return {data:{create_mcp_connection:{}}}}return {data:{mcp_connections:[row],projects}}});
  render(<SettingsSurface category="connections" onSelect={vi.fn()} onClose={vi.fn()}><McpManager projectId={null} admin search="" version={0} onChanged={vi.fn()}/></SettingsSurface>);
  fireEvent.click(await screen.findByRole('button',{name:'Edit connection'}));const form=screen.getByRole('form',{name:'Edit tool connection'});
  fireEvent.change(within(form).getByLabelText('Connection name'),{target:{value:'Updated tools'}});expect(screen.getByText('Unsaved settings changes')).toBeInTheDocument();
  fireEvent.click(within(form).getByRole('button',{name:'Save connection changes'}));
  if(failure){expect(await screen.findByRole('alert')).toHaveTextContent('Connection changed on the host');expect(screen.getByText('Unsaved settings changes')).toBeInTheDocument()}
  else await waitFor(()=>expect(screen.queryByText('Unsaved settings changes')).not.toBeInTheDocument());
 });
 it('does not widen a host-wide definition for a non-administrator',async()=>{
  respond(connection([]));render(<McpManager projectId={null} admin={false} search="" version={0} onChanged={vi.fn()}/>);
  await screen.findByRole('heading',{name:'Fixture tools'});expect(screen.queryByRole('button',{name:'Connect'})).not.toBeInTheDocument();expect(screen.getByText(/Host-wide · administrator only/)).toBeInTheDocument();
 });
});
