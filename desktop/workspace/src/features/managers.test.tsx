import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import Managers from './Managers';
import { request } from '../lib/api';
vi.mock('../lib/api', () => ({ request: vi.fn() }));
const mocked = vi.mocked(request);
afterEach(cleanup);
beforeEach(() => mocked.mockReset());
describe('workspace managers', () => {
 it('saves explicit media capabilities and preserves existing declarations when editing without a new key', async()=>{
  const provider={id:'existing',name:'Existing API',kind:'openai-compatible',base_url:'https://api.example/v1',model:'future-default',secret_configured:true,capabilities:['shell','image']};
  mocked.mockImplementation(async(path,init)=>{if(path==='/auth/me')return {principal:{scopes:['host-admin']}} as any;const payload=JSON.parse(String(init?.body||'{}'));return payload.query?.includes('save_agent_provider')?{data:{save_agent_provider:{id:payload.variables.input.id}}}:{data:{engines:[],agent_providers:[provider],acp_registry:[]}} as any});
  const changed=vi.fn();render(<Managers section="models" projectId={null} sessionId={null} onCapabilitiesChanged={changed}/>);
  await screen.findByRole('button',{name:'Edit account'});
  // Resolving host-admin access refreshes the catalog. Its old button can be
  // detached between findByRole resolving and the click; wait for that refresh
  // and query the current control instead of retaining the transient element.
  await waitFor(()=>expect(screen.getByRole('button',{name:'Refresh'})).toBeEnabled());
  fireEvent.click(screen.getByRole('button',{name:'Edit account'}));
  const edit=within(await screen.findByRole('form',{name:'Edit provider account'}));expect(edit.getByLabelText('Image generation and editing')).toBeChecked();fireEvent.click(edit.getByLabelText('Audio transcription and speech'));fireEvent.click(screen.getByRole('button',{name:'Save account changes'}));
  await waitFor(()=>expect(mocked.mock.calls.some(([,init])=>String(init?.body).includes('save_agent_provider'))).toBe(true));const mutation=mocked.mock.calls.find(([,init])=>String(init?.body).includes('save_agent_provider'))!;expect(JSON.parse(String(mutation[1]?.body)).variables.input).toMatchObject({id:'existing',api_key:null,capabilities:['shell','image','audio'],model:'future-default'});expect(screen.queryByText('active engine',{exact:false})).not.toBeInTheDocument();await waitFor(()=>expect(changed).toHaveBeenCalledOnce());
 });
 it('keeps excluded memory inspectable and preserves its scope and revision on inclusion', async () => {
  mocked.mockImplementation(async (path) => path === '/auth/me' ? { principal:{scopes:[]} } : { memory:[{id:'fact',revision:3,content:'Project decision',provenance:'review',project_id:'project',retention_days:30,excluded:true}] } as any);
  render(<Managers section="memory" projectId="project" sessionId={null}/>);
  expect(await screen.findByText('Project decision')).toBeInTheDocument();
  expect(screen.getByText(/Excluded from execution/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button',{name:'Include in context'}));
  await waitFor(()=>expect(mocked).toHaveBeenCalledWith('/api/workspace/memory',expect.objectContaining({method:'POST'})));
  const payload=mocked.mock.calls.find(([path,init])=>path==='/api/workspace/memory'&&init?.method==='POST')![1]!;
  expect(JSON.parse(payload.body as string)).toMatchObject({identifier:'fact',revision:3,project_id:'project',excluded:false});
 });
 it('shows member sessions without loading administrator configuration', async () => {
  mocked.mockImplementation(async(path)=>path==='/auth/me'? {principal:{scopes:['agent-view']},session_id:'own'} : path==='/auth/access'?{role:'viewer'}:path==='/auth/sessions'?[{id:'own',device_name:'My laptop',strength:'password',expires:100}]:{} as any);
  render(<Managers section="access" projectId={null} sessionId={null}/>);
  expect(await screen.findByText('My laptop')).toBeInTheDocument();
  expect(screen.queryByText('People and project access')).not.toBeInTheDocument();
  expect(mocked.mock.calls.some(([path])=>path.startsWith('/auth/admin/'))).toBe(false);
 });
 it('offers retry after a backend permission error', async()=>{
  let failures=0; mocked.mockImplementation(async(path)=>{if(path==='/auth/me')return {principal:{scopes:[]}} as any;if(failures++===0)throw new Error('Project access revoked');return {memory:[]} as any});
  render(<Managers section="memory" projectId="project" sessionId={null}/>);
  expect(await screen.findByText('Project access revoked')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button',{name:'Retry'}));
  await waitFor(()=>expect(mocked.mock.calls.filter(([path])=>path==='/api/workspace/memory?project_id=project')).toHaveLength(2));
 });
});

it('preserves an unsaved provider form while testing and refreshing another account',async()=>{const provider={id:'existing',name:'Existing API',kind:'openai-compatible',base_url:'https://api.example/v1',model:'model',secret_configured:true,capabilities:['shell']};mocked.mockImplementation(async(path,init)=>{if(path==='/auth/me')return {principal:{scopes:['host-admin']}} as any;const body=JSON.parse(String(init?.body||'{}'));return body.query?.includes('test_agent_provider')?{data:{test_agent_provider:{ok:true}}}:{data:{engines:[],agent_providers:[provider],acp_registry:[]}} as any});render(<Managers section="models" projectId={null} sessionId={null}/>);await screen.findByRole('button',{name:'Test connection'});await waitFor(()=>expect(screen.getByRole('button',{name:'Refresh'})).toBeEnabled());const form=within(screen.getByRole('form',{name:'Add provider account'}));fireEvent.change(form.getByLabelText('Display name'),{target:{value:'Unsaved provider draft'}});fireEvent.click(screen.getByRole('button',{name:'Test connection'}));fireEvent.click(screen.getByRole('button',{name:'Send test request'}));await screen.findByText('Provider connection tested');await waitFor(()=>expect(screen.getByRole('button',{name:'Refresh'})).toBeEnabled());expect(within(screen.getByRole('form',{name:'Add provider account'})).getByLabelText('Display name')).toHaveValue('Unsaved provider draft')});
it('preserves another memory draft through an inclusion mutation and catalog refresh',async()=>{mocked.mockImplementation(async(path)=>path==='/auth/me'?{principal:{scopes:[]}}:{memory:[{id:'fact',revision:3,content:'Existing memory',provenance:'review',retention_days:30,excluded:true}]} as any);render(<Managers section="memory" projectId={null} sessionId={null}/>);await screen.findByText('Existing memory');fireEvent.change(screen.getByLabelText('Fact or decision'),{target:{value:'Unsaved memory draft'}});fireEvent.change(screen.getByLabelText('Source or reason'),{target:{value:'Draft source'}});fireEvent.click(screen.getByRole('button',{name:'Include in context'}));await screen.findByText('Memory scope updated');await waitFor(()=>expect(screen.getByRole('button',{name:'Refresh'})).toBeEnabled());expect(screen.getByLabelText('Fact or decision')).toHaveValue('Unsaved memory draft');expect(screen.getByLabelText('Source or reason')).toHaveValue('Draft source')});

it('reviews the exact billable provider test before dispatch and cancels without a model request',async()=>{
 const provider={id:'api',name:'Fixture API',kind:'openai-compatible',base_url:'https://provider.example/v1',model:'fixture-model',secret_configured:true,capabilities:['shell']};
 mocked.mockImplementation(async(path,init)=>{if(path==='/auth/me')return {principal:{scopes:['host-admin']}} as any;const payload=JSON.parse(String(init?.body||'{}'));return payload.query?.includes('test_agent_provider')?{data:{test_agent_provider:{status:'connected',message:'OK'}}}:{data:{engines:[],agent_providers:[provider],acp_registry:[]}} as any});
 render(<Managers embedded section="models" projectId={null} sessionId={null}/>);
 await screen.findByRole('button',{name:'Test connection'});await waitFor(()=>expect(screen.getByRole('button',{name:'Refresh'})).toBeEnabled());
 fireEvent.click(screen.getByRole('button',{name:'Test connection'}));
 const review=screen.getByRole('dialog',{name:'Test Fixture API?'});
 expect(review).toHaveTextContent('Provider charges may apply');expect(review).toHaveTextContent(provider.base_url);expect(review).toHaveTextContent(provider.model);
 expect(mocked.mock.calls.some(([,init])=>String(init?.body).includes('test_agent_provider'))).toBe(false);
 fireEvent.click(screen.getByRole('button',{name:'Cancel test'}));expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
 fireEvent.click(screen.getByRole('button',{name:'Test connection'}));fireEvent.click(screen.getByRole('button',{name:'Send test request'}));
 await screen.findByText('Provider connection tested');
 expect(mocked.mock.calls.filter(([,init])=>String(init?.body).includes('test_agent_provider'))).toHaveLength(1);
});
it('does not claim success when the provider returns a failed connection result',async()=>{
 mocked.mockImplementation(async(path,init)=>{if(path==='/auth/me')return {principal:{scopes:[]}} as any;const payload=JSON.parse(String(init?.body||'{}'));return payload.query?.includes('test_agent_provider')?{data:{test_agent_provider:{ok:false,message:'Provider rejected the model'}}}:{data:{engines:[],agent_providers:[{id:'api',name:'Fixture API',model:'fixture-model'}]}} as any});
 render(<Managers embedded section="models" projectId={null} sessionId={null}/>);
 fireEvent.click(await screen.findByRole('button',{name:'Test connection'}));fireEvent.click(screen.getByRole('button',{name:'Send test request'}));
 expect(await screen.findByRole('alert')).toHaveTextContent('Provider rejected the model');expect(screen.queryByText('Provider connection tested')).not.toBeInTheDocument();
});

it('offers advertised ACP sign-in methods and sends the selected exact method once',async()=>{
 mocked.mockImplementation(async(path,init)=>{
  if(path==='/auth/me')return {principal:{scopes:['agent-run']}} as any;
  const payload=JSON.parse(String(init?.body||'{}'));
  if(payload.query?.includes('authenticate_engine'))return {data:{authenticate_engine:{ok:true,method:payload.variables.method}}} as any;
  return {data:{engines:[{id:'acp',label:'ACP worker',installed:true,auth_state:'unknown',protocol:{name:'acp',auth_methods:[
   {id:'browser-account',name:'Continue in browser'},{id:'device-code',label:'Use a device code'},
   {id:'device-code',name:'Duplicate method'},{id:''},{id:'  '},{id:7},null,
  ]}}],agent_providers:[]}} as any;
 });
 const changed=vi.fn();render(<Managers embedded section="models" projectId={null} sessionId={null} onCapabilitiesChanged={changed}/>);
 const methods=within(await screen.findByRole('group',{name:'Sign-in methods for ACP worker'}));
 expect(methods.getAllByRole('button')).toHaveLength(2);
 expect(screen.queryByPlaceholderText('Method ID from engine account settings')).not.toBeInTheDocument();
 expect(mocked.mock.calls.some(([,init])=>String(init?.body).includes('authenticate_engine'))).toBe(false);
 fireEvent.click(methods.getByRole('button',{name:'Use a device code'}));
 await screen.findByText('Engine sign-in completed');
 const calls=mocked.mock.calls.filter(([,init])=>String(init?.body).includes('authenticate_engine'));
 expect(calls).toHaveLength(1);expect(JSON.parse(String(calls[0][1]?.body)).variables).toEqual({id:'acp',method:'device-code'});
 expect(changed).toHaveBeenCalledOnce();
 expect(mocked.mock.calls.some(([,init])=>{const payload=JSON.parse(String(init?.body||'{}'));return payload.query?.includes('engines')&&payload.query.includes('auth_detail')&&payload.query.includes('protocol')})).toBe(true);
});

it('keeps unsupported and uninstalled engine authentication out of the action surface',async()=>{
 mocked.mockImplementation(async(path)=>path==='/auth/me'?{principal:{scopes:['agent-run']}}:{data:{engines:[
  {id:'codex',label:'Codex',installed:true,auth_state:'unauthenticated',protocol:{name:'codex-app-server',auth_methods:[{id:'unsupported',name:'Unsupported sign-in'}]}},
  {id:'claude',label:'Claude',installed:true,auth_state:'unknown',protocol:{name:'claude-agent-sdk'}},
  {id:'ready',label:'Ready worker',installed:true,auth_state:'authenticated',protocol:{name:'claude-agent-sdk'}},
  {id:'missing',label:'Missing worker',installed:false,auth_state:'unknown',protocol:{name:'acp',auth_methods:[{id:'browser',name:'Install-only sign-in'}]}},
  {id:'no-methods',label:'Other worker',installed:true,auth_state:'unknown',protocol:{name:'acp',auth_methods:[]}},
 ],agent_providers:[]}} as any);
 render(<Managers embedded section="models" projectId={null} sessionId={null}/>);
 expect(await screen.findByText('Manage sign-in in the Codex CLI on this host, then refresh to check the account.')).toBeInTheDocument();
 expect(screen.getByText('Manage sign-in in the Claude CLI on this host, then refresh to check the account.')).toBeInTheDocument();
 expect(screen.getByText('This engine has no sign-in method available in TermX. Manage its account on this host, then refresh.')).toBeInTheDocument();
 expect(screen.queryByRole('button',{name:/Unsupported sign-in|Install-only sign-in|^Sign in$/})).not.toBeInTheDocument();
 expect(screen.queryByRole('textbox',{name:/Sign-in method/})).not.toBeInTheDocument();
 const ready=screen.getByRole('heading',{name:'Ready worker'}).closest('article')!;
 expect(within(ready).queryByText(/Manage sign-in/)).not.toBeInTheDocument();
 expect(mocked.mock.calls.some(([,init])=>String(init?.body).includes('authenticate_engine'))).toBe(false);
});

it('does not offer an engine sign-in mutation without host run permission',async()=>{
 mocked.mockImplementation(async(path)=>path==='/auth/me'?{principal:{scopes:['agent-view']}}:{data:{engines:[{id:'acp',label:'ACP worker',installed:true,protocol:{name:'acp',auth_methods:[{id:'browser',name:'Continue in browser'}]}}],agent_providers:[]}} as any);
 render(<Managers embedded section="models" projectId={null} sessionId={null}/>);
 expect(await screen.findByText('Ask the host owner to connect this engine.')).toBeInTheDocument();
 expect(screen.queryByRole('button',{name:'Continue in browser'})).not.toBeInTheDocument();
 expect(mocked.mock.calls.some(([,init])=>String(init?.body).includes('authenticate_engine'))).toBe(false);
});

it('keeps a failed ACP authentication result visible without claiming sign-in succeeded',async()=>{
 mocked.mockImplementation(async(path,init)=>{
  if(path==='/auth/me')return {principal:{scopes:['agent-run']}} as any;
  const payload=JSON.parse(String(init?.body||'{}'));
  return payload.query?.includes('authenticate_engine')?{data:{authenticate_engine:{ok:false,message:'The sign-in request expired'}}}:{data:{engines:[{id:'acp',label:'ACP worker',installed:true,protocol:{name:'acp',auth_methods:[{id:'device',name:'Use a device code'}]}}],agent_providers:[]}} as any;
 });
 render(<Managers embedded section="models" projectId={null} sessionId={null}/>);
 fireEvent.click(await screen.findByRole('button',{name:'Use a device code'}));
 expect(await screen.findByRole('alert')).toHaveTextContent('The sign-in request expired');
 expect(screen.queryByText('Engine sign-in completed')).not.toBeInTheDocument();
});

it('puts account setup before engines and keeps model favorites collapsed without hiding engine sign-in',async()=>{
 mocked.mockImplementation(async(path)=>path==='/auth/me'?{principal:{id:'setup-owner',scopes:['host-admin','agent-run']}}:{data:{engines:[{id:'acp',label:'ACP worker',installed:true,protocol:{name:'acp',auth_methods:[{id:'browser',name:'Continue in browser'}]}}],agent_providers:[],acp_registry:[]}} as any);
 render(<Managers embedded section="models" projectId={null} sessionId={null}/>);
 await screen.findByRole('button',{name:'Continue in browser'});
 const accounts=screen.getByRole('heading',{name:'Provider accounts',exact:true});
 const engines=screen.getByRole('heading',{name:'Installed engines',exact:true});
 const add=screen.getByText('Add provider account',{selector:'summary'});
 expect(accounts.compareDocumentPosition(engines)&Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
 expect(add.compareDocumentPosition(engines)&Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
 expect(screen.getByText('Model favorites',{selector:'summary'}).closest('details')).not.toHaveAttribute('open');
 expect(screen.getByRole('button',{name:'Continue in browser'})).toBeVisible();
});
