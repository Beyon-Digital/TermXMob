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
