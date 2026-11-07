import {cleanup,fireEvent,render,screen,waitFor,within} from '@testing-library/react';
import {afterEach,beforeEach,expect,it,vi} from 'vitest';
import Managers from './Managers';
import ModelRoutingForm from './ModelRoutingForm';
import SettingsSurface from './SettingsSurface';
import {request} from '../lib/api';
import type {AgentProvider} from '../lib/host-types';
import {expectNoAxeViolations} from '../test-axe';
vi.mock('../lib/api',()=>({request:vi.fn(),json:(method:string,value:unknown)=>({method,body:JSON.stringify(value)})}));
const mocked=vi.mocked(request);
afterEach(cleanup);
beforeEach(()=>{mocked.mockReset()});

const local:AgentProvider={id:'local',name:'Local Ollama',kind:'openai-compatible',models:['llama3','qwen'],model_catalog:{llama3:{context_window:8192,price_in:null,price_out:null},qwen:{}},model_overrides:{}};
const cloud:AgentProvider={id:'cloud',name:'Cloud API',kind:'openai-compatible',models:['big'],model_catalog:{big:{context_window:200000,price_in:3,price_out:15}},model_overrides:{}};
const select=(name:string)=>screen.getByRole('combobox',{name}) as HTMLSelectElement;

it('shows one row per role with provider, model and fallback pickers, and no route set',()=>{
 render(<ModelRoutingForm providers={[local,cloud]} routing={{}} disabled={false} onSave={vi.fn()}/>);
 for(const role of ['Orchestrator','Worker','Planner','Summariser']){
  expect(select(`${role} provider`)).toHaveValue('');
  expect(select(`${role} model`)).toBeDisabled();
  expect(select(`${role} fallback`)).toBeDisabled();
 }
 expect(screen.getAllByRole('listitem')).toHaveLength(4);
});

it('labels every picker in view, and keeps the role in its accessible name',()=>{
 render(<ModelRoutingForm providers={[local,cloud]} routing={{}} disabled={false} onSave={vi.fn()}/>);
 expect(screen.getAllByText('Provider')).toHaveLength(4);
 expect(screen.getAllByText('Model')).toHaveLength(4);
 expect(screen.getAllByText('Fallback')).toHaveLength(4);
 expect(select('Worker model')).toBeInTheDocument();
});

it('lists plain model ids for the model and adds the context window to fallbacks where known',()=>{
 render(<ModelRoutingForm providers={[local,cloud]} routing={{worker:{provider_id:'local',model:'',fallbacks:[]}}} disabled={false} onSave={vi.fn()}/>);
 const model=within(select('Worker model'));
 expect(model.getByRole('option',{name:'llama3'})).toBeInTheDocument();
 expect(model.getByRole('option',{name:'qwen'})).toBeInTheDocument();
 const fallback=within(select('Worker fallback'));
 expect(fallback.getByRole('option',{name:'llama3 · 8,192 tokens'})).toBeInTheDocument();
 expect(fallback.getByRole('option',{name:'qwen'})).toBeInTheDocument();
 expect(fallback.getByRole('option',{name:'big · 200,000 tokens'})).toBeInTheDocument();
});

it('shows the chosen model’s context window under the pickers',()=>{
 render(<ModelRoutingForm providers={[local,cloud]} routing={{worker:{provider_id:'local',model:'llama3',fallbacks:[]}}} disabled={false} onSave={vi.fn()}/>);
 expect(screen.getByText('Context window 8,192 tokens · Input Unknown · Output Unknown')).toBeInTheDocument();
});

it('saves the picked routes, one fallback each, and omits roles left on the session default',async()=>{
 const save=vi.fn().mockResolvedValue({});
 render(<ModelRoutingForm providers={[local,cloud]} routing={{}} disabled={false} onSave={save}/>);
 fireEvent.change(select('Orchestrator provider'),{target:{value:'cloud'}});
 fireEvent.change(select('Orchestrator model'),{target:{value:'big'}});
 fireEvent.change(select('Orchestrator fallback'),{target:{value:JSON.stringify(['local','qwen'])}});
 fireEvent.change(select('Worker provider'),{target:{value:'local'}});
 fireEvent.submit(screen.getByRole('form',{name:'Model routing'}));
 await waitFor(()=>expect(save).toHaveBeenCalledOnce());
 expect(save).toHaveBeenCalledWith({
  orchestrator:{provider_id:'cloud',model:'big',fallbacks:[{provider_id:'local',model:'qwen'}]},
  worker:{provider_id:'local',model:'',fallbacks:[]},
 });
});

it('does not offer the primary model as its own fallback and clears the model when the provider changes',()=>{
 render(<ModelRoutingForm providers={[local,cloud]} routing={{planner:{provider_id:'local',model:'llama3',fallbacks:[]}}} disabled={false} onSave={vi.fn()}/>);
 expect(within(select('Planner fallback')).queryByRole('option',{name:/^llama3/})).toBeNull();
 fireEvent.change(select('Planner provider'),{target:{value:'cloud'}});
 expect(select('Planner model')).toHaveValue('');
});

it('keeps a route to a removed account visible so it can be changed',()=>{
 render(<ModelRoutingForm providers={[cloud]} routing={{planner:{provider_id:'gone',model:'old',fallbacks:[{provider_id:'gone',model:'older'}]}}} disabled={false} onSave={vi.fn()}/>);
 expect(select('Planner provider')).toHaveValue('gone');
 expect(screen.getByRole('option',{name:'Unavailable account (gone)'})).toBeInTheDocument();
 expect(select('Planner model')).toHaveValue('old');
 expect(select('Planner fallback')).toHaveValue(JSON.stringify(['gone','older']));
});

it('keeps further fallbacks held by the host when a role is saved, and says so',async()=>{
 const save=vi.fn().mockResolvedValue({});
 const route={provider_id:'local',model:'llama3',fallbacks:[{provider_id:'cloud',model:'big'},{provider_id:'local',model:'qwen'},{provider_id:'cloud',model:'big'}]};
 render(<ModelRoutingForm providers={[local,cloud]} routing={{worker:route}} disabled={false} onSave={save}/>);
 expect(screen.getByText('2 more fallbacks set through the API are kept when you save.')).toBeInTheDocument();
 fireEvent.change(select('Worker model'),{target:{value:'qwen'}});
 fireEvent.submit(screen.getByRole('form',{name:'Model routing'}));
 await waitFor(()=>expect(save).toHaveBeenCalledOnce());
 expect(save).toHaveBeenCalledWith({worker:{...route,model:'qwen'}});
});

it('drops every fallback when the person chooses no fallback',async()=>{
 const save=vi.fn().mockResolvedValue({});
 render(<ModelRoutingForm providers={[local,cloud]} routing={{worker:{provider_id:'local',model:'llama3',fallbacks:[{provider_id:'cloud',model:'big'},{provider_id:'local',model:'qwen'}]}}} disabled={false} onSave={save}/>);
 fireEvent.change(select('Worker fallback'),{target:{value:''}});
 expect(screen.queryByText(/more fallback/)).toBeNull();
 fireEvent.submit(screen.getByRole('form',{name:'Model routing'}));
 await waitFor(()=>expect(save).toHaveBeenCalledOnce());
 expect(save).toHaveBeenCalledWith({worker:{provider_id:'local',model:'llama3',fallbacks:[]}});
});

it('shows a newer routing from the host while the form is clean, and keeps unsaved picks otherwise',()=>{
 const props={providers:[local,cloud],disabled:false,onSave:vi.fn()};
 const view=render(<ModelRoutingForm {...props} routing={{}}/>);
 view.rerender(<ModelRoutingForm {...props} routing={{worker:{provider_id:'cloud',model:'big',fallbacks:[]}}}/>);
 expect(select('Worker provider')).toHaveValue('cloud');
 fireEvent.change(select('Planner provider'),{target:{value:'local'}});
 view.rerender(<ModelRoutingForm {...props} routing={{worker:{provider_id:'local',model:'qwen',fallbacks:[]}}}/>);
 expect(select('Planner provider')).toHaveValue('local');
 expect(select('Worker provider')).toHaveValue('cloud');
});

it('keeps unsaved picks when saving fails so the person can retry',async()=>{
 const save=vi.fn().mockResolvedValue(null);
 render(<ModelRoutingForm providers={[local]} routing={{}} disabled={false} onSave={save}/>);
 fireEvent.change(select('Worker provider'),{target:{value:'local'}});
 fireEvent.submit(screen.getByRole('form',{name:'Model routing'}));
 await waitFor(()=>expect(save).toHaveBeenCalledOnce());
 expect(select('Worker provider')).toHaveValue('local');
});

it('says how to get models when no provider has any, and offers no pickers',async()=>{
 const {container}=render(<ModelRoutingForm providers={[{...local,models:[],model_catalog:{}}]} routing={{}} disabled={false} onSave={vi.fn()}/>);
 expect(screen.getByText(/No models are listed yet\. Add a provider account above, then choose Refresh models/)).toBeInTheDocument();
 expect(screen.queryByRole('combobox')).toBeNull();
 expect(screen.queryAllByRole('listitem')).toHaveLength(0);
 await expectNoAxeViolations(container);
});

it('tells someone who cannot change settings to ask the host owner when no models are listed',()=>{
 render(<ModelRoutingForm providers={[]} routing={{}} disabled onSave={vi.fn()}/>);
 expect(screen.getByText(/Ask the host owner to add a provider account/)).toBeInTheDocument();
 expect(screen.queryByText(/above/)).toBeNull();
});

it('disables every picker for someone who cannot change host settings',()=>{
 render(<ModelRoutingForm providers={[local]} routing={{}} disabled onSave={vi.fn()}/>);
 for(const box of screen.getAllByRole('combobox'))expect(box).toBeDisabled();
});

it('has no axe violations with routes set',async()=>{
 const {container}=render(<ModelRoutingForm providers={[local,cloud]} routing={{worker:{provider_id:'local',model:'llama3',fallbacks:[{provider_id:'cloud',model:'big'}]}}} disabled={false} onSave={vi.fn()}/>);
 await expectNoAxeViolations(container);
});

type Body={query?:string;variables?:Record<string,unknown>};
const calls=()=>mocked.mock.calls.map(([,init])=>(init?.body?JSON.parse(String(init.body)):{}) as Body);

it('loads routing with the models, and saves it from the settings footer with set_model_routing',async()=>{
 let routing:Record<string,unknown>={};
 mocked.mockImplementation((async(path:string,init?:RequestInit)=>{
  if(path==='/auth/me')return {principal:{id:'owner',display_name:'Owner',scopes:['host-admin']},session_id:'s1',host_id:'h1'};
  const body:Body=init?.body?JSON.parse(String(init.body)):{};
  if(body.query?.includes('set_model_routing')){routing=body.variables!.input as never;routing=(routing as {routing:Record<string,unknown>}).routing;return {data:{set_model_routing:routing}}}
  return {data:{engines:[],agent_providers:[local],acp_registry:[],model_routing:routing}};
 }) as typeof request);
 const {container}=render(<SettingsSurface category="models" onSelect={vi.fn()} onClose={vi.fn()}><Managers embedded section="models" projectId={null} sessionId={null}/></SettingsSurface>);
 await screen.findByRole('form',{name:'Model routing'});
 const query=calls().find(call=>call.query?.includes('agent_providers'))!.query!;
 for(const field of ['model_routing','models','model_catalog'])expect(query).toContain(field);
 expect(screen.queryByRole('button',{name:'Save'})).toBeNull();
 fireEvent.change(select('Summariser provider'),{target:{value:'local'}});
 fireEvent.change(select('Summariser model'),{target:{value:'qwen'}});
 fireEvent.click(await screen.findByRole('button',{name:'Save'}));
 await waitFor(()=>expect(calls().some(call=>call.query?.includes('set_model_routing'))).toBe(true));
 expect(calls().find(call=>call.query?.includes('set_model_routing'))!.variables).toEqual({input:{routing:{summariser:{provider_id:'local',model:'qwen',fallbacks:[]}}}});
 await waitFor(()=>expect(screen.getAllByRole('status').some(node=>node.textContent==='Model routing saved')).toBe(true));
 await waitFor(()=>expect(screen.queryByRole('button',{name:'Save'})).toBeNull());
 await expectNoAxeViolations(container);
});

it('shows the host’s refusal and keeps the footer Save available',async()=>{
 mocked.mockImplementation((async(path:string,init?:RequestInit)=>{
  if(path==='/auth/me')return {principal:{id:'owner',display_name:'Owner',scopes:['host-admin']},session_id:'s1',host_id:'h1'};
  const body:Body=init?.body?JSON.parse(String(init.body)):{};
  if(body.query?.includes('set_model_routing'))throw new Error('worker: model \'x\' is not available on Local Ollama');
  return {data:{engines:[],agent_providers:[local],acp_registry:[],model_routing:{}}};
 }) as typeof request);
 render(<SettingsSurface category="models" onSelect={vi.fn()} onClose={vi.fn()}><Managers embedded section="models" projectId={null} sessionId={null}/></SettingsSurface>);
 await screen.findByRole('form',{name:'Model routing'});
 fireEvent.change(select('Worker provider'),{target:{value:'local'}});
 fireEvent.click(await screen.findByRole('button',{name:'Save'}));
 expect(await screen.findByText(/is not available on Local Ollama/)).toBeInTheDocument();
 expect(screen.getByRole('button',{name:'Save'})).toBeInTheDocument();
});

it('is read-only for people without host settings access',async()=>{
 mocked.mockImplementation((async(path:string)=>path==='/auth/me'?{principal:{id:'m',display_name:'Member',scopes:['agent-view']},session_id:'s1',host_id:'h1'}:{data:{engines:[],agent_providers:[local],acp_registry:[],model_routing:{}}}) as typeof request);
 render(<Managers embedded section="models" projectId={null} sessionId={null}/>);
 await screen.findByRole('form',{name:'Model routing'});
 expect(select('Worker provider')).toBeDisabled();
});
