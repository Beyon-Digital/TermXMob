import {afterEach,expect,it,vi} from 'vitest';
import {cleanup,renderHook} from '@testing-library/react';
import {useWorkspaceHistory,workspaceRoute} from './App';
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

// The sign-out event resets the control plane while retaining the URL. Signing
// back in must restore the requested surface before browser history resumes.
it.each([
 ['/?session=conversation', {selected:'conversation',manager:''}],
 ['/session/conversation%20with%20spaces', {selected:'conversation with spaces',manager:''}],
 ['/?layout=workbench', {selected:'',manager:''}],
 ['/manage/agents', {selected:'',manager:'agents'}],
 ['/', {selected:'',manager:''}],
])('restores the requested authenticated surface after sign-out: %s',(initial,expected)=>{
 history.replaceState(null,'',initial);
 expect(workspaceRoute()).toEqual(expected);
});

it('retains the requested URL while authenticated route state is still being restored',()=>{
 vi.mocked(nativeWorkspace).mockReturnValue(false);
 history.replaceState(null,'','/?session=requested');
 const {rerender}=renderHook(({ready,manager,selected})=>useWorkspaceHistory(ready,manager,selected),{initialProps:{ready:false,manager:'overview',selected:''}});
 expect(location.search).toBe('?session=requested');
 const route=workspaceRoute();
 rerender({ready:true,...route});
 expect(location.pathname).toBe('/session/requested');
});
