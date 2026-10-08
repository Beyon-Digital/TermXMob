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
  const changed=vi.fn();render(<Managers section="models" projectId={null} sessionId={null} onCapabilitiesChanged={changed}/>);fireEvent.click(await screen.findByRole('button',{name:'Edit account'}));const edit=within(screen.getByRole('form',{name:'Edit provider account'}));expect(edit.getByLabelText('Image generation and editing')).toBeChecked();fireEvent.click(edit.getByLabelText('Audio transcription and speech'));fireEvent.click(screen.getByRole('button',{name:'Save account changes'}));
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
