import {afterEach,beforeEach,expect,it,vi} from 'vitest';
import {webcrypto} from 'node:crypto';
import {mediaIntent,clearMediaIntent} from './media-intent';
beforeEach(()=>{localStorage.clear();vi.stubGlobal('crypto',webcrypto)});
afterEach(()=>vi.unstubAllGlobals());
it('retains the same opaque billing intent across reloads and hides input content',async()=>{
 const args={prompt:'Sensitive draft',provider:'configured',model:'selected'};
 const first=await mediaIntent('alice','project',args);
 const reloaded=await mediaIntent('alice','project',args);
 expect(reloaded.id).toBe(first.id);
 expect(localStorage.getItem(first.key!)).not.toContain(args.prompt);
 expect((await mediaIntent('bob','project',args)).id).not.toBe(first.id);
 expect((await mediaIntent('alice','other',args)).id).not.toBe(first.id);
 clearMediaIntent(first.key,first.id);
 expect((await mediaIntent('alice','project',args)).id).not.toBe(first.id);
});
it('a stale window cannot erase a newer media intent',async()=>{
 const first=await mediaIntent('alice','project',{prompt:'First'});
 const next=await mediaIntent('alice','project',{prompt:'Second'});
 clearMediaIntent(first.key,first.id);
 expect((await mediaIntent('alice','project',{prompt:'Second'})).id).toBe(next.id);
});
it('keeps a runner command intent distinct from paid creation and retains it across reloads',async()=>{
 const args={runner_id:'runner',argv:['python','main.py'],secrets:{TERMX_SECRET_TOKEN:'credential-reference'}};
 const job=await mediaIntent('alice','project',args,'runner-job');
 expect((await mediaIntent('alice','project',args,'runner-job')).id).toBe(job.id);
 expect((await mediaIntent('alice','project',args)).id).not.toBe(job.id);
 expect(localStorage.getItem(job.key!)).not.toContain('credential-reference');
});

it('retains separate uncertain receipts when another window starts different work',async()=>{
 const first=await mediaIntent('alice','project',{prompt:'Uncertain A'},'studio');
 const other=await mediaIntent('alice','project',{prompt:'Other B'},'studio');
 expect(first.key).not.toBe(other.key);
 expect((await mediaIntent('alice','project',{prompt:'Uncertain A'},'studio')).id).toBe(first.id);
 clearMediaIntent(other.key,other.id);
 expect((await mediaIntent('alice','project',{prompt:'Uncertain A'},'studio')).id).toBe(first.id);
 expect((await mediaIntent('alice','project',{prompt:'Other B'},'studio')).id).not.toBe(other.id);
});
it('treats object key ordering as the same billed operation',async()=>{
 const first=await mediaIntent('alice','project',{model:'selected',settings:{resolution:'1K',ratio:'1:1'},prompt:'A'},'studio');
 const retry=await mediaIntent('alice','project',{prompt:'A',settings:{ratio:'1:1',resolution:'1K'},model:'selected'},'studio');
 expect(retry).toEqual(first);
});
it('migrates a legacy pending receipt without losing it to another request',async()=>{
 const args={prompt:'Legacy pending',model:'selected'};
 const encoded=new TextEncoder().encode(JSON.stringify(args));
 const digest=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',encoded)),byte=>byte.toString(16).padStart(2,'0')).join('');
 const base='termx-studio-intent:alice:project';
 localStorage.setItem(base,JSON.stringify({id:'legacy-pending-id',digest}));
 await mediaIntent('alice','project',{prompt:'A different request'},'studio');
 const migrated=await mediaIntent('alice','project',args,'studio');
 expect(migrated.id).toBe('legacy-pending-id');expect(migrated.key).not.toBe(base);
 expect(localStorage.getItem(base)).toBeNull();
 clearMediaIntent(migrated.key,migrated.id);
 expect((await mediaIntent('alice','project',args,'studio')).id).not.toBe('legacy-pending-id');
});
it('cannot dispatch an untracked intent when receipt storage cannot be written',async()=>{
 vi.spyOn(Storage.prototype,'setItem').mockImplementation(()=>{throw new Error('Storage unavailable')});
 await expect(mediaIntent('alice','project',{prompt:'Do not submit'},'studio')).rejects.toThrow('Storage unavailable');
 vi.restoreAllMocks();
});
