import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import Managers from './Managers';
import { request } from '../lib/api';
vi.mock('../lib/api', () => ({ request: vi.fn(), gql:vi.fn().mockResolvedValue({tunnels:{profiles:[]}}), json:(method:string,value:unknown)=>({method,body:JSON.stringify(value)}) }));
const mocked = vi.mocked(request);
afterEach(cleanup);
beforeEach(() => { mocked.mockReset() });
describe('workspace managers', () => {
 it('saves explicit media capabilities and preserves existing declarations when editing without a new key', async()=>{
  const provider={id:'existing',name:'Existing API',kind:'openai-compatible',base_url:'https://api.example/v1',model:'future-default',secret_configured:true,capabilities:['shell','image']};
  mocked.mockImplementation(async(path,init)=>{if(path==='/auth/me')return {principal:{scopes:['host-admin']}} as any;const payload=JSON.parse(String(init?.body||'{}'));return payload.query?.includes('save_agent_provider')?{data:{save_agent_provider:{id:payload.variables.input.id}}}:{data:{engines:[],agent_providers:[provider],acp_registry:[]}} as any});
  const changed=vi.fn();render(<Managers embedded section="models" projectId={null} sessionId={null} onCapabilitiesChanged={changed}/>);
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
  render(<Managers embedded section="memory" projectId="project" sessionId={null}/>);
  expect(await screen.findByText('Project decision')).toBeInTheDocument();
  expect(screen.getByText(/Excluded from execution/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button',{name:'Include in context'}));
  await waitFor(()=>expect(mocked).toHaveBeenCalledWith('/api/workspace/memory',expect.objectContaining({method:'POST'})));
  const payload=mocked.mock.calls.find(([path,init])=>path==='/api/workspace/memory'&&init?.method==='POST')![1]!;
  expect(JSON.parse(payload.body as string)).toMatchObject({identifier:'fact',revision:3,project_id:'project',excluded:false});
 });
 it('shows member sessions without loading administrator configuration', async () => {
  mocked.mockImplementation(async(path)=>path==='/auth/me'? {principal:{scopes:['agent-view']},session_id:'own'} : path==='/auth/access'?{role:'viewer'}:path==='/auth/sessions'?[{id:'own',device_name:'My laptop',strength:'password',expires:100}]:{} as any);
  render(<Managers embedded section="access" projectId={null} sessionId={null}/>);
  expect(await screen.findByText('My laptop')).toBeInTheDocument();
  expect(screen.queryByText('People and permissions')).not.toBeInTheDocument();
  expect(mocked.mock.calls.some(([path])=>path.startsWith('/auth/admin/'))).toBe(false);
 });
 it('offers retry after a backend permission error', async()=>{
  let failures=0; mocked.mockImplementation(async(path)=>{if(path==='/auth/me')return {principal:{scopes:[]}} as any;if(failures++===0)throw new Error('Project access revoked');return {memory:[]} as any});
  render(<Managers embedded section="memory" projectId="project" sessionId={null}/>);
  expect(await screen.findByText('Project access revoked')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button',{name:'Retry'}));
  await waitFor(()=>expect(mocked.mock.calls.filter(([path])=>path==='/api/workspace/memory?project_id=project')).toHaveLength(2));
 });
});

it('preserves an unsaved provider form while testing and refreshing another account',async()=>{const provider={id:'existing',name:'Existing API',kind:'openai-compatible',base_url:'https://api.example/v1',model:'model',secret_configured:true,capabilities:['shell']};mocked.mockImplementation(async(path,init)=>{if(path==='/auth/me')return {principal:{scopes:['host-admin']}} as any;const body=JSON.parse(String(init?.body||'{}'));return body.query?.includes('test_agent_provider')?{data:{test_agent_provider:{ok:true}}}:{data:{engines:[],agent_providers:[provider],acp_registry:[]}} as any});render(<Managers embedded section="models" projectId={null} sessionId={null}/>);await screen.findByRole('button',{name:'Test connection'});await waitFor(()=>expect(screen.getByRole('button',{name:'Refresh'})).toBeEnabled());fireEvent.click(screen.getByText('Add provider account',{selector:'summary'}));const form=within(screen.getByRole('form',{name:'Add provider account'}));fireEvent.change(form.getByLabelText('Display name'),{target:{value:'Unsaved provider draft'}});fireEvent.click(screen.getByRole('button',{name:'Test connection'}));await screen.findByText('Provider connection tested');await waitFor(()=>expect(screen.getByRole('button',{name:'Refresh'})).toBeEnabled());expect(within(screen.getByRole('form',{name:'Add provider account'})).getByLabelText('Display name')).toHaveValue('Unsaved provider draft')});
it('preserves another memory draft through an inclusion mutation and catalog refresh',async()=>{mocked.mockImplementation(async(path)=>path==='/auth/me'?{principal:{scopes:[]}}:{memory:[{id:'fact',revision:3,content:'Existing memory',provenance:'review',retention_days:30,excluded:true}]} as any);render(<Managers embedded section="memory" projectId={null} sessionId={null}/>);await screen.findByText('Existing memory');fireEvent.click(screen.getByText('Add memory',{selector:'summary'}));fireEvent.change(screen.getByLabelText('Fact or decision'),{target:{value:'Unsaved memory draft'}});fireEvent.change(screen.getByLabelText('Source or reason'),{target:{value:'Draft source'}});fireEvent.click(screen.getByRole('button',{name:'Include in context'}));await screen.findByText('Memory scope updated');await waitFor(()=>expect(screen.getByRole('button',{name:'Refresh'})).toBeEnabled());expect(screen.getByLabelText('Fact or decision')).toHaveValue('Unsaved memory draft');expect(screen.getByLabelText('Source or reason')).toHaveValue('Draft source')});


it('manages hooks across the active host without a settings project and requires an explicit command target',async()=>{
 const projects=[{id:'alpha',name:'Alpha',path:'/host/alpha'},{id:'beta',name:'Beta',path:'/host/beta'}];
 mocked.mockImplementation(async(path,init)=>{
  if(path==='/auth/me')return {principal:{scopes:['agent-view','agent-control']}} as any;
  if(path==='/graphql')return {data:{projects}} as any;
  if(init?.method==='POST')return {id:'saved'} as any;
  return path.includes('hook-runs')?{runs:[]}: {hooks:[{id:path.includes('alpha')?'alpha-hook':'beta-hook',project_id:path.includes('alpha')?'alpha':'beta',event:'before_turn',argv:['echo','ready'],cwd:'/host',timeout_s:10,enabled:false}]} as any;
 });
 render(<Managers embedded section="hooks" projectId={null} sessionId={null}/>);
 expect(await screen.findByText('Alpha · Disabled')).toBeInTheDocument();expect(screen.getByText('Beta · Disabled')).toBeInTheDocument();
 expect(screen.queryByText('Select a project to configure lifecycle hooks.')).not.toBeInTheDocument();
 fireEvent.click(screen.getByText('Add a hook',{selector:'summary'}));
 expect(screen.getByRole('button',{name:'Save disabled hook'})).toBeDisabled();
 fireEvent.change(screen.getByLabelText('Run in project'),{target:{value:'beta'}});
 fireEvent.change(screen.getByLabelText('Executable and arguments (one per line)'),{target:{value:'python\nscripts/check.py'}});
 expect(screen.getByLabelText('Working folder')).toHaveValue('/host/beta');
 fireEvent.click(screen.getByRole('button',{name:'Save disabled hook'}));
 await waitFor(()=>expect(mocked).toHaveBeenCalledWith('/api/workspace/hooks',expect.objectContaining({method:'POST'})));
 const saved=mocked.mock.calls.find(([path,init])=>path==='/api/workspace/hooks'&&init?.method==='POST')!;
 expect(JSON.parse(String(saved[1]?.body))).toEqual({project_id:'beta',event:'before_turn',argv:['python','scripts/check.py'],cwd:'/host/beta',timeout_s:10,enabled:false,capabilities:[]});
});

it('shows the exact hook command and file authority before enabling it',async()=>{
 mocked.mockImplementation(async(path)=>path==='/auth/me'?{principal:{scopes:[]}}:path==='/graphql'?{data:{projects:[{id:'alpha',name:'Alpha',path:'/host/alpha'}]}}:path.includes('hook-runs')?{runs:[]}:{hooks:[{id:'hook',revision:3,project_id:'alpha',event:'before_turn',argv:['python','check.py'],cwd:'/host/alpha',timeout_s:10,enabled:false}]} as any);
 const confirmation=vi.spyOn(window,'confirm').mockReturnValueOnce(false).mockReturnValueOnce(true);
 render(<Managers embedded section="hooks" projectId={null} sessionId={null}/>);
 fireEvent.click(await screen.findByRole('button',{name:'Enable'}));
 expect(confirmation).toHaveBeenCalledWith(expect.stringContaining('Command: python check.py\nWorking folder: /host/alpha'));
 expect(confirmation).toHaveBeenCalledWith(expect.stringContaining('read and write project files'));
 expect(mocked.mock.calls.some(([,init])=>init?.method==='PATCH')).toBe(false);
 fireEvent.click(screen.getByRole('button',{name:'Enable'}));
 await waitFor(()=>expect(mocked).toHaveBeenCalledWith('/api/workspace/hooks/hook',expect.objectContaining({method:'PATCH'})));
 confirmation.mockRestore();
});

it('opens access on devices and loads host setup only when requested',async()=>{
 mocked.mockImplementation(async(path)=>{
  if(path==='/auth/me')return {principal:{id:'owner',display_name:'Owner',scopes:['host-admin']},host_id:'host',session_id:'own'} as any;
  if(path==='/auth/sessions')return [{id:'own',device_name:'My laptop',strength:'password',expires:100}] as any;
  if(path==='/graphql')return {data:{projects:[]}} as any;
  if(path==='/auth/lan-tls')return {configuration:{enabled:false,host:'0.0.0.0',port:8788,names:[]},active:{enabled:false},restart_required:false} as any;
  if(path==='/api/host/service')return {installed:false,running:false,ready:false,supported:true,backend:'supervisor',managed:false} as any;
  return path.endsWith('/principals')||path.endsWith('/adapters')?[]:{} as any;
 });
 render(<Managers embedded section="access" projectId={null} sessionId={null}/>);
 await screen.findByText('My laptop');await waitFor(()=>expect(screen.getByRole('button',{name:'Refresh'})).toBeEnabled());
 expect(screen.queryByText('Device permissions')).not.toBeInTheDocument();
 expect(mocked.mock.calls.some(([path])=>path==='/auth/lan-tls'||path==='/api/host/service')).toBe(false);
 const connectivity=screen.getByText('Host connectivity',{selector:'summary'});
 fireEvent.click(connectivity);fireEvent(connectivity.closest('details')!,new Event('toggle'));
 await screen.findByText('Local network HTTPS is off');
 expect(mocked).toHaveBeenCalledWith('/api/host/service');
});

it('explains an empty filtered memory list without opening its creation form',async()=>{
 mocked.mockImplementation(async(path)=>path==='/auth/me'?{principal:{scopes:[]}}:{memory:[{id:'fact',content:'Remember this',provenance:'review',retention_days:30}]} as any);
 const view=render(<Managers embedded section="memory" projectId={null} sessionId={null}/>);await screen.findByText('Remember this');
 fireEvent.change(screen.getByLabelText('Search settings'),{target:{value:'unmatched'}});
 expect(screen.getByText('No matches. Try another search.')).toBeInTheDocument();
 expect(screen.getByText('Add memory',{selector:'summary'}).closest('details')).not.toHaveAttribute('open');
 view.rerender(<Managers embedded section="agents" projectId={null} sessionId={null}/>);
 expect(screen.getByLabelText('Search settings')).toHaveValue('');
});


it('offers only advertised ACP sign-in methods and sends the selected exact method ID',async()=>{
 mocked.mockImplementation(async(path,init)=>{
  if(path==='/auth/me')return {principal:{scopes:[]}} as any;
  const payload=JSON.parse(String(init?.body||'{}'));
  if(payload.query?.includes('authenticate_engine'))return {data:{authenticate_engine:{ok:true}}} as any;
  return {data:{engines:[{id:'acp',label:'ACP worker',installed:true,auth_state:'unknown',protocol:{name:'acp',auth_methods:[{id:'browser-account',name:'Continue in browser'},{id:'device-code',label:'Use a device code'}]}}],agent_providers:[]}} as any;
 });
 render(<Managers embedded section="models" projectId={null} sessionId={null}/>);
 fireEvent.click(await screen.findByRole('button',{name:'Use a device code'}));
 await waitFor(()=>expect(mocked.mock.calls.some(([,init])=>String(init?.body).includes('authenticate_engine'))).toBe(true));
 const call=mocked.mock.calls.find(([,init])=>String(init?.body).includes('authenticate_engine'))!;
 expect(JSON.parse(String(call[1]?.body)).variables).toEqual({id:'acp',method:'device-code'});
 expect(screen.queryByPlaceholderText('Method ID from engine account settings')).not.toBeInTheDocument();
 expect(mocked.mock.calls.some(([,init])=>{const payload=JSON.parse(String(init?.body||'{}'));return payload.query?.includes('engines')&&payload.query.includes('protocol')&&payload.query.includes('auth_detail')})).toBe(true);
});

it('does not offer unsupported or uninstalled engine sign-in actions',async()=>{
 mocked.mockImplementation(async(path)=>path==='/auth/me'?{principal:{scopes:[]}}:{data:{engines:[
  {id:'codex',label:'Codex',installed:true,auth_state:'unauthenticated',protocol:{name:'codex-app-server',auth_methods:[{id:'unsupported',name:'Unsupported login'}]}},
  {id:'claude',label:'Claude',installed:true,auth_state:'unauthenticated',auth_detail:'Sign in through the Claude CLI.',protocol:{name:'claude-agent-sdk'}},
  {id:'ready',label:'Signed-in worker',installed:true,auth_state:'authenticated',auth_detail:'Already signed in',protocol:{name:'claude-agent-sdk'}},
  {id:'missing',label:'Missing ACP',installed:false,auth_state:'unauthenticated',protocol:{name:'acp',auth_methods:[{id:'browser',name:'Install-only login'}]}},
 ],agent_providers:[]}} as any);
 render(<Managers embedded section="models" projectId={null} sessionId={null}/>);
 expect(await screen.findByText('Manage sign-in in Codex on this host.')).toBeInTheDocument();
 expect(screen.getByText('Sign in through the Claude CLI.')).toBeInTheDocument();
 expect(screen.queryByText('Already signed in')).not.toBeInTheDocument();
 expect(screen.queryByText('Manage sign-in in Missing ACP on this host.')).not.toBeInTheDocument();
 expect(screen.queryByRole('button',{name:/Unsupported login|Install-only login|^Sign in$/})).not.toBeInTheDocument();
 expect(screen.queryByRole('textbox',{name:/Sign-in method/})).not.toBeInTheDocument();
 expect(mocked.mock.calls.some(([,init])=>String(init?.body).includes('authenticate_engine'))).toBe(false);
});
