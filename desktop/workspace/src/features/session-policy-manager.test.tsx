import {afterEach,expect,it,vi} from 'vitest';
import {act,cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import SessionPolicyManager from './SessionPolicyManager';
import {request} from '../lib/api';
vi.mock('../lib/api',()=>({request:vi.fn(),json:(method:string,data:unknown)=>({method,body:JSON.stringify(data)}),ApiError:class extends Error{constructor(public status:number,message:string){super(message)}}}));
afterEach(()=>{cleanup();vi.resetAllMocks()});
const initial={revision:1,idle_ttl_seconds:86400,absolute_ttl_seconds:2592000,access_ttl_seconds:300};
it('saves exact lifetime fields and reports actual expired device count',async()=>{
 vi.mocked(request).mockImplementation(async(_,init)=>init?.method?{...initial,revision:2,idle_ttl_seconds:600,expired_sessions:2} as never:initial as never);
 render(<SessionPolicyManager onError={vi.fn()}/>);
 await waitFor(()=>expect(screen.getByLabelText('Idle session limit in seconds')).toHaveValue(86400));
 fireEvent.change(screen.getByLabelText('Idle session limit in seconds'),{target:{value:'600'}});
 fireEvent.click(screen.getByRole('button',{name:'Save session limits'}));
 await screen.findByText('Session limits saved. 2 expired device sessions revoked.');
 const saved=vi.mocked(request).mock.calls.find(([,init])=>init?.method==='PUT')!;
 expect(saved[0]).toBe('/auth/admin/session-policy');expect(JSON.parse(saved[1]!.body as string)).toEqual({revision:1,idle_ttl_seconds:600,absolute_ttl_seconds:2592000});
});
it('preserves edited limits across current policy refresh and requires explicit revision review',async()=>{
 let revision=1;
 vi.mocked(request).mockImplementation(async(_,init)=>init?.method?{...initial,revision:3,expired_sessions:0} as never:{...initial,revision,idle_ttl_seconds:revision===1?86400:1800} as never);
 render(<SessionPolicyManager onError={vi.fn()}/>);
 await waitFor(()=>expect(screen.getByLabelText('Idle session limit in seconds')).toHaveValue(86400));
 fireEvent.change(screen.getByLabelText('Idle session limit in seconds'),{target:{value:'600'}});
 revision=2;fireEvent.click(screen.getByRole('button',{name:'Refresh session limits'}));
 await screen.findByRole('alert');expect(screen.getByLabelText('Idle session limit in seconds')).toHaveValue(600);expect(screen.getByRole('button',{name:'Save session limits'})).toBeDisabled();
 fireEvent.click(screen.getByRole('button',{name:'Keep my draft using the reviewed policy revision'}));fireEvent.click(screen.getByRole('button',{name:'Save session limits'}));
 await waitFor(()=>expect(vi.mocked(request).mock.calls.some(([,init])=>init?.method==='PUT')).toBe(true));
 const saved=vi.mocked(request).mock.calls.find(([,init])=>init?.method==='PUT')!;expect(JSON.parse(saved[1]!.body as string).revision).toBe(2);
});
it('does not apply a pending response after Lock or actual unmount',async()=>{
 let resolveLoad!:(value:unknown)=>void;
 vi.mocked(request).mockImplementation(async()=>await new Promise<unknown>(resolve=>{resolveLoad=resolve}) as never);
 const onError=vi.fn(),view=render(<SessionPolicyManager onError={onError}/>);
 act(()=>window.dispatchEvent(new Event('termx-locked')));view.unmount();
 await act(async()=>{resolveLoad(initial);await Promise.resolve()});
 expect(onError).not.toHaveBeenCalled();
});
it('preserves an edited draft on unlock without requiring review of an unchanged revision',async()=>{
 vi.mocked(request).mockResolvedValue(initial as never);
 render(<SessionPolicyManager onError={vi.fn()}/>);
 await waitFor(()=>expect(screen.getByLabelText('Idle session limit in seconds')).toHaveValue(86400));
 fireEvent.change(screen.getByLabelText('Idle session limit in seconds'),{target:{value:'600'}});
 act(()=>window.dispatchEvent(new Event('termx-locked')));act(()=>window.dispatchEvent(new Event('termx-session-unlocked')));
 await waitFor(()=>expect(vi.mocked(request)).toHaveBeenCalledTimes(2));
 expect(screen.getByLabelText('Idle session limit in seconds')).toHaveValue(600);
 expect(screen.queryByRole('alert')).not.toBeInTheDocument();expect(screen.getByRole('button',{name:'Save session limits'})).not.toBeDisabled();
});
