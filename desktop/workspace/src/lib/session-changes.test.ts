import {renderHook,waitFor,act,cleanup} from '@testing-library/react';
import {afterEach,it,expect,vi} from 'vitest';
import {useSessionChanges,sessionChangesPath} from './session-changes';
import {request,type Session} from './api';
vi.mock('./api',()=>({request:vi.fn()}));
afterEach(()=>{cleanup();vi.useRealTimers();vi.clearAllMocks()});
const project={id:'project',name:'Project',path:'/main'};
const session:Session={id:'chat',project_id:'project',cwd:'/isolated',worktree_id:'tree',title:'Review',engine:'internal',mode:'agent',pinned:false,archived:false,draft_text:'',revision:1,updated_at:0,scroll:0};
const data={repo:true,branch:'branch',head:'head',files:[],additions:0,deletions:0,counts_complete:true,truncated:false};
it('shares checkout metadata and discards responses after target changes',async()=>{
 let release!:(value:unknown)=>void;vi.mocked(request).mockImplementation(async path=>path.includes('/chat/')?new Promise(resolve=>{release=resolve}):{...data,branch:'new checkout'} as any);
 const view=renderHook(({selected})=>useSessionChanges({project,session:selected}),{initialProps:{selected:session}});
 await waitFor(()=>expect(release).toBeDefined());view.rerender({selected:{...session,id:'next',worktree_id:'other'}});
 await waitFor(()=>expect(view.result.current.data?.branch).toBe('new checkout'));
 await act(async()=>release({...data,branch:'stale checkout'}));expect(view.result.current.data?.branch).toBe('new checkout');
 expect(sessionChangesPath(session,'src/file with space.ts')).toBe('/api/workspace/sessions/chat/changes/diff?worktree_id=tree&path=src%2Ffile+with+space.ts');
});
it('does not read cloud or hidden workspaces and clears previews on lock',async()=>{
 vi.mocked(request).mockResolvedValue(data);const view=renderHook(({selected,visible})=>useSessionChanges({project,session:selected,visible}),{initialProps:{selected:{...session,runner_id:'cloud'} as Session,visible:true}});
 expect(view.result.current.available).toBe(false);expect(vi.mocked(request)).not.toHaveBeenCalled();
 view.rerender({selected:session,visible:false});expect(vi.mocked(request)).not.toHaveBeenCalled();view.rerender({selected:session,visible:true});
 await waitFor(()=>expect(view.result.current.data).not.toBeNull());
 act(()=>window.dispatchEvent(new Event('termx-session-locked')));expect(view.result.current.data).toBeNull();expect(view.result.current.available).toBe(false);
});

it('polls at five-second intervals only while the selected workspace is visible',async()=>{
 vi.useFakeTimers();vi.mocked(request).mockResolvedValue(data);
 const view=renderHook(({visible})=>useSessionChanges({project,session,visible}),{initialProps:{visible:true}});
 await act(async()=>{await Promise.resolve();await Promise.resolve()});expect(vi.mocked(request)).toHaveBeenCalledTimes(1);
 await act(async()=>{await vi.advanceTimersByTimeAsync(4999)});expect(vi.mocked(request)).toHaveBeenCalledTimes(1);
 await act(async()=>{await vi.advanceTimersByTimeAsync(1)});expect(vi.mocked(request)).toHaveBeenCalledTimes(2);
 view.rerender({visible:false});await act(async()=>{await vi.advanceTimersByTimeAsync(15000)});expect(vi.mocked(request)).toHaveBeenCalledTimes(2);
});
