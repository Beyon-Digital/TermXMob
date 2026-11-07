import {act,cleanup,renderHook,waitFor} from '@testing-library/react';
import {afterEach,expect,it,vi} from 'vitest';
import {useManagerLoad,useManagerMutation} from './use-manager';
vi.mock('../lib/api',()=>({request:vi.fn(),json:(method:string,value?:unknown)=>({method,body:JSON.stringify(value)})}));
afterEach(cleanup);

it('resolves a write that replies with null as done, and a thrown write as null with the message',async()=>{
 const reload=vi.fn();const {result}=renderHook(()=>useManagerMutation(reload));
 let done:unknown;
 await act(async()=>{done=await result.current.mutate(async()=>null,'Saved')});
 expect(done).not.toBeNull();expect(result.current.notice).toBe('Saved');expect(reload).toHaveBeenCalledOnce();
 await act(async()=>{done=await result.current.mutate(async()=>{throw new Error('Nope')},'Saved')});
 expect(done).toBeNull();expect(result.current.error).toBe('Nope');expect(result.current.notice).toBe('');expect(reload).toHaveBeenCalledOnce();
});
it('skips the reload for a write that only inspects, and tells the surface what changed',async()=>{
 const reload=vi.fn(),changed=vi.fn();const {result}=renderHook(()=>useManagerMutation(reload,changed));
 await act(async()=>{await result.current.mutate(async()=>'x','Inspected',{refresh:false})});
 expect(reload).not.toHaveBeenCalled();expect(changed).toHaveBeenCalledOnce();
});
it('keeps loaded data while a reload runs and reports a failed load without discarding it',async()=>{
 const load=vi.fn().mockResolvedValueOnce('first').mockRejectedValueOnce(new Error('Offline'));
 const {result}=renderHook(()=>useManagerLoad(load));
 await waitFor(()=>expect(result.current.data).toBe('first'));
 act(()=>result.current.reload());
 await waitFor(()=>expect(result.current.error).toBe('Offline'));
 expect(result.current.data).toBe('first');expect(result.current.loading).toBe(false);
});
