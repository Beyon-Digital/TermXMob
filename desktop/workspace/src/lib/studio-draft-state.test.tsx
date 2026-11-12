import {act,cleanup,render,renderHook,screen} from '@testing-library/react';
import {afterEach,expect,it,vi} from 'vitest';
import {StudioDraftBoundary,useStudioDraftState,useStudioProjectGuard,useStudioProjectReadOnly} from './studio-draft-state';
afterEach(cleanup);
const activity={ownerId:'owner',projectId:'studio_first',dirty:true,busy:false};
it('requires an explicit decision before a dirty project change and preserves cancellation',async()=>{
 const {result}=renderHook(()=>useStudioProjectGuard('owner','studio_first',false));
 act(()=>result.current.receive(activity));let pending!:Promise<boolean>;
 act(()=>{pending=result.current.permit('studio_second')});expect(result.current.pending).not.toBeNull();
 act(()=>result.current.decide(false));expect(await pending).toBe(false);expect(result.current.activity.dirty).toBe(true);
 act(()=>{pending=result.current.permit('studio_second')});act(()=>result.current.decide(true));expect(await pending).toBe(true);
});
it('blocks pending effects, while clean and same-project selection need no dialog',async()=>{
 const {result}=renderHook(()=>useStudioProjectGuard('owner','studio_first',false));
 expect(await result.current.permit('studio_second')).toBe(true);expect(result.current.pending).toBeNull();
 act(()=>result.current.receive({...activity,busy:true,reason:'Wait for the actual receipt.'}));
 await expect(result.current.permit('studio_second')).rejects.toThrow('Wait for the actual receipt.');
 expect(await result.current.permit('studio_first')).toBe(true);expect(result.current.pending).toBeNull();
});
it('cancels a pending decision when locked or replaced and rejects stale owner/project reports',async()=>{
 const {result,rerender}=renderHook(({owner,project,locked})=>useStudioProjectGuard(owner,project,locked),{initialProps:{owner:'owner',project:'studio_first',locked:false}});
 act(()=>result.current.receive(activity));let pending!:Promise<boolean>;act(()=>{pending=result.current.permit('studio_second')});
 rerender({owner:'owner',project:'studio_first',locked:true});expect(await pending).toBe(false);
 rerender({owner:'other',project:'studio_second',locked:false});act(()=>result.current.receive(activity));
 expect(result.current.activity).toMatchObject({ownerId:'other',projectId:'studio_second',dirty:false,busy:false});
 expect(await result.current.permit('')).toBe(true);
});
function Draft({source,dirty=false,busy=false}:{source:string;dirty?:boolean;busy?:boolean}){useStudioDraftState(source,dirty,busy,busy?'Pending owned effect':undefined);return null;}
function ReadOnly(){return <output>{String(useStudioProjectReadOnly())}</output>;}
it('aggregates retained editor metadata and fences scope changes without storing draft content',()=>{
 const report=vi.fn();const view=render(<StudioDraftBoundary ownerId="owner" projectId="studio_first" onActivity={report}><Draft source="image" dirty/><Draft source="music" busy/><ReadOnly/></StudioDraftBoundary>);
 expect(report).toHaveBeenLastCalledWith({...activity,busy:true,reason:'Pending owned effect'});
 view.rerender(<StudioDraftBoundary ownerId="owner" projectId="studio_second" readOnly onActivity={report}><Draft source="image"/><ReadOnly/></StudioDraftBoundary>);
 expect(report).toHaveBeenLastCalledWith({ownerId:'owner',projectId:'studio_second',dirty:false,busy:false,reason:undefined});
 expect(screen.getByRole('status')).toHaveTextContent('true');
});
