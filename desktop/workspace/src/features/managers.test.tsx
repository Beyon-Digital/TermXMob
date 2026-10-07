import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import Managers from './Managers';
import { request } from '../lib/api';
vi.mock('../lib/api', () => ({ request: vi.fn() }));
const mocked = vi.mocked(request);
afterEach(cleanup);
beforeEach(() => mocked.mockReset());
describe('workspace managers', () => {
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
