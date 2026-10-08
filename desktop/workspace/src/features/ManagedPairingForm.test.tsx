import {cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import {afterEach,beforeEach,describe,expect,it,vi} from 'vitest';
import {ManagedPairingForm} from './ManagedPairingForm';
import {request} from '../lib/api';
vi.mock('../lib/api',()=>({request:vi.fn(),json:(method:string,data:unknown)=>({method,body:JSON.stringify(data)})}));
const mocked=vi.mocked(request);
const actor={principal:{id:'owner',display_name:'Synthetic owner',scopes:['machine-view','files-read','agent-view','host-admin']},session_id:'fixture-session',host_id:'fixture-host'};
const proof={ticket:'synthetic-pair-proof-not-a-real-credential',host_id:'fixture-host',scopes:['machine-view','files-read','agent-view'],expires_at:Date.now()/1000+300,expires_in:300};
afterEach(cleanup);
beforeEach(()=>{mocked.mockReset();mocked.mockResolvedValue(proof as never)});
describe('managed mobile pairing',()=>{
 it('issues explicit account-scoped permissions without automatically granting administration',async()=>{
  render(<ManagedPairingForm actor={actor}/>);expect(screen.getByLabelText('Host administration')).not.toBeChecked();fireEvent.click(screen.getByRole('button',{name:'Create one-use pairing ticket'}));
  await screen.findByLabelText('One-use pairing ticket');expect(mocked).toHaveBeenCalledWith('/auth/pair/issue',expect.objectContaining({method:'POST',body:JSON.stringify({scopes:['machine-view','files-read','agent-view']})}));
  expect(screen.getByLabelText('Host ID')).toHaveValue('fixture-host');expect(screen.getByText(/This session: fixture-session/)).toBeInTheDocument();
 });
 it('requires separate legacy consent and clears supplied evidence after issue',async()=>{
  render(<ManagedPairingForm actor={actor}/>);fireEvent.click(screen.getByLabelText('Associate an existing legacy device with this account'));fireEvent.change(screen.getByLabelText('Legacy device token'),{target:{value:'legacy-fixture-only'}});expect(screen.getByRole('button',{name:'Create one-use pairing ticket'})).toBeDisabled();fireEvent.click(screen.getByLabelText(/I explicitly associate/));fireEvent.click(screen.getByRole('button',{name:'Create one-use pairing ticket'}));
  await screen.findByLabelText('One-use pairing ticket');expect(JSON.parse(String(mocked.mock.calls[0][1]?.body))).toMatchObject({legacy_token:'legacy-fixture-only',associate_legacy:true});expect(screen.getByLabelText('Legacy device token')).toHaveValue('');
 });
 it('uses an HTTPS fragment link and revokes a discarded ticket',async()=>{
  render(<ManagedPairingForm actor={actor}/>);fireEvent.click(screen.getByRole('button',{name:'Create one-use pairing ticket'}));await screen.findByLabelText('One-use pairing ticket');
  fireEvent.change(screen.getByLabelText('Machine HTTPS address'),{target:{value:'http://host.example'}});expect(screen.queryByLabelText('One-use pairing link')).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText('Machine HTTPS address'),{target:{value:'https://host.example'}});const link=new URL((screen.getByLabelText('One-use pairing link') as HTMLInputElement).value);expect(link.search).toBe('');expect(new URLSearchParams(link.hash.slice(1)).get('termx_pair')).toBe(proof.ticket);
  fireEvent.click(screen.getByRole('button',{name:'Revoke and clear pairing ticket'}));await waitFor(()=>expect(screen.queryByLabelText('One-use pairing ticket')).not.toBeInTheDocument());expect(mocked).toHaveBeenCalledWith('/auth/pair/revoke',expect.objectContaining({body:JSON.stringify({ticket:proof.ticket})}));
 });
 it('hides pending old-account responses after session change',async()=>{
  let resolve!:(value:unknown)=>void;mocked.mockImplementation(()=>new Promise(done=>{resolve=done}) as never);
  const view=render(<ManagedPairingForm actor={actor}/>);fireEvent.click(screen.getByRole('button',{name:'Create one-use pairing ticket'}));view.rerender(<ManagedPairingForm actor={{...actor,session_id:'new-session'}}/>);resolve(proof);await Promise.resolve();expect(screen.queryByLabelText('One-use pairing ticket')).not.toBeInTheDocument();
 });
 it('offers only current scopes and no administrator legacy association to a viewer',()=>{
  render(<ManagedPairingForm actor={{...actor,principal:{...actor.principal,scopes:['machine-view']}}}/>);expect(screen.queryByLabelText('Host administration')).not.toBeInTheDocument();expect(screen.queryByLabelText(/Associate an existing/)).not.toBeInTheDocument();
 });
});
