import {afterEach,expect,it,vi} from 'vitest';
import {cleanup,renderHook} from '@testing-library/react';
import {useWorkspaceHistory} from './App';
import {nativeWorkspace} from './lib/native';

vi.mock('./lib/native',async original=>({...await original<typeof import('./lib/native')>(),nativeWorkspace:vi.fn()}));
afterEach(()=>{cleanup();history.replaceState(null,'','/');vi.clearAllMocks()});

it.each(['/', '/?session=conversation&layout=chat&window=workspace-fixture'])('native navigation preserves the trusted URL and detached-window context: %s',initial=>{
 vi.mocked(nativeWorkspace).mockReturnValue(true);
 history.replaceState(null,'',initial);
 const {rerender}=renderHook(({authenticated,manager,selected})=>useWorkspaceHistory(authenticated,manager,selected),{initialProps:{authenticated:false,manager:'overview',selected:''}});
 for(const [manager,selected] of [['overview',''],['tasks',''],['','conversation'],['runners','conversation']]){
  rerender({authenticated:true,manager,selected});
  expect(location.pathname+location.search).toBe(initial);
 }
});

it('browser navigation still creates shareable manager and conversation routes after authentication',()=>{
 vi.mocked(nativeWorkspace).mockReturnValue(false);
 const {rerender}=renderHook(({authenticated,manager,selected})=>useWorkspaceHistory(authenticated,manager,selected),{initialProps:{authenticated:false,manager:'overview',selected:''}});
 expect(location.pathname).toBe('/');
 rerender({authenticated:true,manager:'overview',selected:''});
 expect(location.pathname).toBe('/manage/overview');
 rerender({authenticated:true,manager:'',selected:'conversation with spaces'});
 expect(location.pathname).toBe('/session/conversation%20with%20spaces');
 rerender({authenticated:true,manager:'',selected:''});
 expect(location.pathname).toBe('/');
});
