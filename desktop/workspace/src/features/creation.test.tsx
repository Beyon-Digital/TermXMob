import {afterEach,beforeEach,expect,it,vi} from 'vitest';
import {cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import Media from './Media';
import Runners from './Runners';
import {request,gql} from '../lib/api';
import {webcrypto} from 'node:crypto';
vi.mock('../lib/api',()=>({request:vi.fn(),gql:vi.fn(),json:(method:string,data:unknown)=>({method,body:JSON.stringify(data)})}));
vi.mock('../lib/transfers',()=>({download:vi.fn(),transfer:vi.fn()}));
const api=vi.mocked(request),graph=vi.mocked(gql);
beforeEach(()=>{api.mockReset();graph.mockReset();graph.mockResolvedValue({agent_providers:[{id:'account',name:'Configured API',model:'configured-model',capabilities:['image','audio']}]});api.mockImplementation(async(path:string)=>{if(path==='/api/runners')return [];if(path.startsWith('/api/media/artifacts?')||path.startsWith('/api/media/operations'))return [];throw new Error('Unexpected request '+path)})});
afterEach(()=>{cleanup();vi.unstubAllGlobals()});
it('requires explicit provider billing consent before generation',async()=>{
 render(<Media projectId="project"/>);await screen.findByRole('option',{name:'Configured API'});
 const generate=screen.getByRole('button',{name:'Run selected capability'});expect(generate).toBeDisabled();
 fireEvent.change(screen.getByLabelText('Configured account'),{target:{value:'account'}});expect(generate).toBeDisabled();
 fireEvent.click(screen.getByRole('checkbox'));expect(generate).toBeEnabled();
 expect(api).not.toHaveBeenCalledWith('/api/media/generate',expect.anything());
});
it('preserves edited content when the server reports a version conflict',async()=>{
 const artifact={id:'artifact',kind:'document',title:'Notes',version:1,content:'Original'};
 api.mockImplementation(async(path:string,init?:RequestInit)=>{if(path.startsWith('/api/media/operations'))return [];if(path.startsWith('/api/media/artifacts?'))return [artifact];if(path.endsWith('/versions'))return [{version:1,created:1}];if(path==='/api/media/artifacts/artifact'&&init?.method==='PUT')throw new Error('Resource changed; reload before saving');if(path==='/api/media/artifacts/artifact')return artifact;throw new Error(path)});
 render(<Media projectId="project"/>);fireEvent.click(await screen.findByRole('button',{name:/Notes/}));const editor=await screen.findByRole('textbox',{name:'Artifact content'});
 fireEvent.change(editor,{target:{value:'Keep my edit'}});fireEvent.click(screen.getByRole('button',{name:'Save new version'}));await screen.findByRole('alert');expect(editor).toHaveValue('Keep my edit');
});
it('enrolls only the selected project and explicit execution location',async()=>{
 render(<Runners ownerId="owner" projectId="project"/>);await screen.findByText('Enroll a dedicated runner for this project to start remote work.');
 fireEvent.click(screen.getByRole('button',{name:'Enroll runner'}));fireEvent.change(screen.getByLabelText('Container image'),{target:{value:'alpine:latest'}});
 fireEvent.change(screen.getByLabelText('Execution host'),{target:{value:'ssh://user@runner.example'}});
 api.mockImplementation(async(path:string,init?:RequestInit)=>path==='/api/runners'&&init?.method==='POST'?{id:'runner',configuration:{image:'alpine:latest'}}:[]);
 fireEvent.click(screen.getByRole('button',{name:'Create scoped runner'}));await waitFor(()=>expect(api).toHaveBeenCalledWith('/api/runners',expect.objectContaining({body:expect.stringContaining('"project_id":"project"')})));
 const call=api.mock.calls.find(([,init])=>init?.method==='POST');expect(JSON.parse(String(call?.[1]?.body))).toMatchObject({endpoint:'ssh://user@runner.example',network:'none',lease_seconds:3600});
});
it('reuses the exact runner job ID after a response is lost and the view reloads',async()=>{
 vi.stubGlobal('crypto',webcrypto);localStorage.clear();let attempts=0;
 const row={id:'runner',project:'project',status:'ready',expires:Date.now()/1000+3600,configuration:{image:'existing-image',cpu:1,memory_mb:512,network:'none'}};
 api.mockImplementation(async(path:string,init?:RequestInit)=>{
  if(path==='/api/runners')return [row];
  if(path==='/api/runners/runner/jobs'&&init?.method==='POST'){attempts++;if(attempts===1)throw new Error('Response lost');return {id:'same-job',status:'running'}}
  if(path==='/api/runners/runner/jobs')return [];
  throw new Error(path);
 });
 async function submit(){
  fireEvent.click(await screen.findByRole('button',{name:'View activity'}));
  fireEvent.change(await screen.findByLabelText('Command arguments'),{target:{value:'["python","main.py"]'}});
  fireEvent.click(screen.getByRole('button',{name:'Run on this runner'}));
 }
 const view=render(<Runners ownerId="owner" projectId="project"/>);await submit();await screen.findByRole('alert');view.unmount();
 render(<Runners ownerId="owner" projectId="project"/>);await submit();await waitFor(()=>expect(attempts).toBe(2));
 const calls=api.mock.calls.filter(([path,init])=>path==='/api/runners/runner/jobs'&&init?.method==='POST');
 const first=JSON.parse(String(calls[0][1]?.body)),second=JSON.parse(String(calls[1][1]?.body));
 expect(second).toEqual(first);expect(second.argv).toEqual(['python','main.py']);
});
