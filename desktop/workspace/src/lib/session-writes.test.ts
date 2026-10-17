import {expect,it,vi} from 'vitest';
import {captureSessionWrite,serializeSessionWrite} from './session-writes';

it('serializes optimistic reads and saves for one conversation while another remains independent',async()=>{
 let revision=1,release!:()=>void;
 const gate=new Promise<void>(resolve=>release=resolve),reads:number[]=[];
 const save=async(wait=false)=>{const current=revision;reads.push(current);if(wait)await gate;if(current!==revision)throw new Error('Revision conflict');return ++revision};
 const draft=serializeSessionWrite('same',()=>save(true));
 const model=serializeSessionWrite('same',()=>save());
 const other=serializeSessionWrite('other',async()=>'saved');
 await expect(other).resolves.toBe('saved');expect(reads).toEqual([1]);
 release();await expect(draft).resolves.toBe(2);await expect(model).resolves.toBe(3);expect(reads).toEqual([1,2]);
});

it('surfaces the original conflict once and still permits a later explicit save',async()=>{
 const conflict=Error('Changed in another window'),write=vi.fn(async()=>{throw conflict});
 const first=serializeSessionWrite('conflict',write),next=serializeSessionWrite('conflict',async()=>'reviewed');
 await expect(first).rejects.toBe(conflict);await expect(next).resolves.toBe('reviewed');expect(write).toHaveBeenCalledOnce();
});

it('cancels queued writes when the account signs out',async()=>{
 let release!:()=>void;const gate=new Promise<void>(resolve=>release=resolve),queued=vi.fn(async()=>'must not save');
 const active=serializeSessionWrite('signed-out',()=>gate);await Promise.resolve();
 const next=serializeSessionWrite('signed-out',queued),rejected=expect(next).rejects.toThrow('account changed');
 const activeRejected=expect(active).rejects.toThrow('account changed');
 window.dispatchEvent(new Event('termx-signed-out'));release();await activeRejected;await rejected;expect(queued).not.toHaveBeenCalled();
});

it('invalidates an intent captured before a draft-flush wait and aborts in-flight reads on lock',async()=>{
 const old=captureSessionWrite(),queued=vi.fn(async()=>'must not dispatch');
 const signal=old.signal;window.dispatchEvent(new Event('termx-session-locked'));
 expect(signal.aborted).toBe(true);
 await expect(serializeSessionWrite('after-flush',queued,old)).rejects.toThrow('workspace locked');
 expect(queued).not.toHaveBeenCalled();old.release();
});
