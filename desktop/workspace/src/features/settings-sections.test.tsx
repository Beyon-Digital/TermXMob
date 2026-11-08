import {cleanup,fireEvent,render,screen,waitFor,within} from '@testing-library/react';
import {afterEach,beforeEach,expect,it,vi} from 'vitest';
import Managers from './Managers';
import SettingsSurface from './SettingsSurface';
import {request} from '../lib/api';
import {expectNoAxeViolations} from '../test-axe';
vi.mock('../lib/api',()=>({request:vi.fn(),json:(method:string,value:unknown)=>({method,body:JSON.stringify(value)})}));
const mocked=vi.mocked(request);
afterEach(cleanup);
beforeEach(()=>{mocked.mockReset()});

type Handler=(path:string,body:{query?:string;variables?:Record<string,unknown>}&Record<string,unknown>)=>unknown;
function serve(scopes:string[],handler:Handler){
 mocked.mockImplementation((async(path:string,init?:RequestInit)=>{
  if(path==='/auth/me')return {principal:{id:'owner',display_name:'Owner',scopes},session_id:'s1',host_id:'h1'};
  return handler(path,init?.body?JSON.parse(String(init.body)):{});
 }) as typeof request);
}
const calls=()=>mocked.mock.calls.map(([path,init])=>({path,body:init?.body?JSON.parse(String(init.body)):{}}));
const provider={id:'local',name:'Local Ollama',kind:'openai-compatible',base_url:'http://localhost:11434/v1',model:'',secret_configured:true,capabilities:['shell'],
 model_catalog:{'llama3':{context_window:8192,price_in:null,price_out:null},'qwen':{}},model_overrides:{},catalog_refreshed_at:null,catalog_error:'Connection refused'};

it('asks the host for the discovery fields and refreshes the catalog from the account form',async()=>{
 let catalogError:string|null='Connection refused';
 serve(['host-admin'],(path,body)=>{
  if(body.query?.includes('refresh_provider_models')){
   if(!/refresh_provider_models\([^)]*\)\s*\{[^}]+\}/.test(body.query))return {errors:[{message:"Field 'refresh_provider_models' of type 'AgentProvider!' must have a selection of subfields."}]};
   catalogError=null;return {data:{refresh_provider_models:{id:'local',model:'llama3'}}}}
  return {data:{engines:[],agent_providers:[{...provider,catalog_error:catalogError}],acp_registry:[]}};
 });
 const {container}=render(<Managers embedded section="models" projectId={null} sessionId={null}/>);
 fireEvent.click(await screen.findByRole('button',{name:'Edit account'}));
 const form=within(await screen.findByRole('form',{name:'Edit provider account'}));
 const query=calls().find(call=>call.body.query?.includes('agent_providers'))!.body.query!;
 for(const field of ['model_catalog','model_overrides','catalog_refreshed_at','catalog_error'])expect(query).toContain(field);
 expect(form.getByText(/Connection refused/)).toHaveAttribute('role','alert');
 expect(form.getByText('Models have not been discovered yet. Choose Refresh models to load them.')).toBeInTheDocument();
 expect(form.getByRole('checkbox',{name:'llama3'})).toBeChecked();
 await expectNoAxeViolations(container);
 fireEvent.click(form.getByRole('button',{name:'Refresh models'}));
 await waitFor(()=>expect(calls().some(call=>call.body.query?.includes('refresh_provider_models')&&call.body.variables?.id==='local')).toBe(true));
 await waitFor(()=>expect(screen.queryByText(/Connection refused/)).toBeNull());
 expect(screen.getAllByRole('status').some(node=>node.textContent==='Model catalog refreshed')).toBe(true);
});

it('shows a failed write as an alert with Retry while the status region stays mounted',async()=>{
 serve(['host-admin'],(path,body)=>{
  if(body.query?.includes('delete_agent_provider'))throw new Error('Host refused');
  return {data:{engines:[],agent_providers:[provider],acp_registry:[]}};
 });
 render(<SettingsSurface category="models" onSelect={vi.fn()} onClose={vi.fn()}><Managers embedded section="models" projectId={null} sessionId={null}/></SettingsSurface>);
 await screen.findByRole('heading',{name:'Local Ollama'});
 const region=screen.getAllByRole('status').find(node=>node.className.includes('manager-notice'))!;
 expect(region).toBeEmptyDOMElement();
 expect(screen.getByText(/All discovered models/)).toBeInTheDocument();
 fireEvent.click(screen.getByRole('button',{name:'Remove account'}));
 fireEvent.click(await screen.findByRole('button',{name:'Confirm remove account'}));
 expect((await screen.findByText('Host refused')).closest('[role=alert]')).not.toBeNull();
 expect(screen.getByRole('button',{name:'Retry'})).toBeInTheDocument();
 expect(screen.getAllByRole('status').find(node=>node.className.includes('manager-notice'))).toBe(region);
});

it('saves the memory form from the surface footer and clears its unsaved state',async()=>{
 serve([],path=>path.startsWith('/api/workspace/memory')?{memory:[]}:{});
 const {container}=render(<SettingsSurface category="memory" onSelect={vi.fn()} onClose={vi.fn()}><Managers embedded section="memory" projectId={null} sessionId={null}/></SettingsSurface>);
 await screen.findByText(/No memory saved/);
 fireEvent.change(screen.getByLabelText(/^Fact or decision/),{target:{value:'Use pnpm'}});
 fireEvent.change(screen.getByLabelText(/^Source or reason/),{target:{value:'Team rule'}});
 await waitFor(()=>expect(document.querySelector('.settings-draft-status')).toHaveTextContent('Unsaved changes'));
 await expectNoAxeViolations(container);
 fireEvent.click(screen.getByRole('button',{name:'Save'}));
 await waitFor(()=>expect(calls().some(call=>call.path==='/api/workspace/memory'&&call.body.content==='Use pnpm'&&call.body.provenance==='Team rule'&&call.body.retention_days===30)).toBe(true));
 await waitFor(()=>expect(document.querySelector('.settings-draft-status')).toBeEmptyDOMElement());
 expect(screen.getByLabelText(/^Fact or decision/)).toHaveValue('');
});

it('keeps a memory draft when saving fails',async()=>{
 serve([],(path,body)=>{if(path==='/api/workspace/memory'&&body.content)throw new Error('Disk full');return {memory:[]}});
 render(<Managers embedded section="memory" projectId={null} sessionId={null}/>);
 await screen.findByText(/No memory saved/);
 fireEvent.change(screen.getByLabelText(/^Fact or decision/),{target:{value:'Keep me'}});fireEvent.change(screen.getByLabelText(/^Source or reason/),{target:{value:'Because'}});
 fireEvent.click(screen.getByRole('button',{name:'Save memory'}));
 expect(await screen.findByText('Disk full')).toHaveAttribute('role','alert');
 expect(screen.getByLabelText(/^Fact or decision/)).toHaveValue('Keep me');
});

it('creates an agent preset with numeric budgets and passes axe',async()=>{
 serve(['host-admin'],(path,body)=>{
  if(body.query?.includes('create_custom_agent'))return {data:{create_custom_agent:{id:'new'}}};
  return {data:{engines:[{id:'internal',label:'TermX agent',installed:true}],custom_agents:[]}};
 });
 const {container}=render(<Managers embedded section="agents" projectId={null} sessionId={null}/>);
 const form=within(await screen.findByRole('form',{name:'Create agent preset'}));
 fireEvent.change(form.getByLabelText(/^Name/),{target:{value:'Reviewer'}});
 await expectNoAxeViolations(container);
 fireEvent.click(form.getByRole('button',{name:'Save preset'}));
 await waitFor(()=>expect(calls().some(call=>call.body.query?.includes('create_custom_agent'))).toBe(true));
 const input=calls().find(call=>call.body.query?.includes('create_custom_agent'))!.body.variables!.input as {limits:Record<string,unknown>};
 expect(input.limits.max_steps).toBe(256);expect(typeof input.limits.max_steps).toBe('number');
});

it('renders the access page for a member without administrator sections and passes axe',async()=>{
 serve([],(path)=>{
  if(path==='/auth/access')return {role:'Member',runtime_boundary:'Host policy'};
  if(path==='/auth/sessions')return [{id:'s1',device_name:'Laptop',strength:'strong',expires:2000000000,revoked:false}];
  return {};
 });
 const {container}=render(<Managers embedded section="access" projectId={null} sessionId={null}/>);
 expect(await screen.findByText('Laptop')).toBeInTheDocument();
 expect(screen.getByText(/This session/)).toBeInTheDocument();
 expect(screen.queryByText('People and project access')).toBeNull();
 expect(screen.getAllByRole('heading',{level:2})).toHaveLength(1);
 await expectNoAxeViolations(container);
});

it('refreshes models without a discard prompt and keeps the unsaved account edits',async()=>{
 serve(['host-admin'],(path,body)=>{
  if(body.query?.includes('refresh_provider_models'))return {data:{refresh_provider_models:{id:'local',model:''}}};
  return {data:{engines:[],agent_providers:[provider],acp_registry:[]}};
 });
 render(<SettingsSurface category="models" onSelect={vi.fn()} onClose={vi.fn()}><Managers embedded section="models" projectId={null} sessionId={null}/></SettingsSurface>);
 fireEvent.click(await screen.findByRole('button',{name:'Edit account'}));
 const form=within(await screen.findByRole('form',{name:'Edit provider account'}));
 fireEvent.change(form.getByLabelText('Display name'),{target:{value:'Renamed'}});
 expect(await screen.findByText('Unsaved changes')).toBeInTheDocument();
 fireEvent.click(form.getByRole('button',{name:'Refresh models'}));
 await waitFor(()=>expect(calls().some(call=>call.body.query?.includes('refresh_provider_models'))).toBe(true));
 expect(screen.queryByText('Discard unsaved changes?')).toBeNull();
 await waitFor(()=>expect(form.getByLabelText('Display name')).toHaveValue('Renamed'));
});

it('explains that the TermX agent has no model list instead of offering a refresh that fails, and keeps it for installed engines',async()=>{
 serve(['host-admin'],(path,body)=>{
  if(body.query?.includes('engine_models'))return {data:{engine_models:['gpt-fixture']}};
  return {data:{engines:[{id:'internal',label:'TermX agent',installed:true,auth_state:'ready'},{id:'codex',label:'Codex',installed:true,auth_state:'ready'}],agent_providers:[provider],acp_registry:[]}};
 });
 render(<Managers embedded section="models" projectId={null} sessionId={null}/>);
 const internal=within((await screen.findByRole('heading',{name:'TermX agent'})).closest('article') as HTMLElement);
 expect(internal.queryByRole('button',{name:'Refresh models'})).toBeNull();
 expect(internal.getByText(/no model list of its own/)).toBeInTheDocument();
 expect(internal.getByText(/Use Refresh models on an account above/)).toBeInTheDocument();
 const codex=within(screen.getByRole('heading',{name:'Codex'}).closest('article') as HTMLElement);
 fireEvent.click(codex.getByRole('button',{name:'Refresh models'}));
 await waitFor(()=>expect(calls().some(call=>call.body.query?.includes('engine_models')&&call.body.variables?.id==='codex')).toBe(true));
 expect(calls().some(call=>call.body.query?.includes('engine_models')&&call.body.variables?.id==='internal')).toBe(false);
 expect(screen.queryByText(/is not available/)).toBeNull();
});

it('treats the install engine form as an action, not an unsaved settings draft',async()=>{
 serve(['host-admin'],()=>({data:{engines:[],agent_providers:[provider],acp_registry:[]}}));
 render(<SettingsSurface category="models" onSelect={vi.fn()} onClose={vi.fn()}><Managers embedded section="models" projectId={null} sessionId={null}/></SettingsSurface>);
 fireEvent.change(await screen.findByLabelText('Registry package ID'),{target:{value:'acme-agent'}});
 expect(screen.queryByText('Unsaved changes')).toBeNull();
 expect(screen.queryByRole('button',{name:'Save'})).toBeNull();
});

it('lets a provider account without a default model open the connection test',async()=>{
 serve(['host-admin'],()=>({data:{engines:[],agent_providers:[provider],acp_registry:[]}}));
 render(<Managers embedded section="models" projectId={null} sessionId={null}/>);
 fireEvent.click(await screen.findByRole('button',{name:'Test connection'}));
 expect(await screen.findByRole('button',{name:'Send test request'})).toBeEnabled();
 expect(screen.queryByText(/choose a default model/)).toBeNull();
});
