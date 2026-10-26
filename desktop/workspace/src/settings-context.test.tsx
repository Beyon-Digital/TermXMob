import {afterEach,beforeEach,expect,it,vi} from 'vitest';
import {cleanup,configure,fireEvent,render,screen,waitFor,within} from '@testing-library/react';
import App from './App';

const fixture=vi.hoisted(()=>({
 owner:'settings-context-owner',
 session:{id:'conversation',title:'Surface navigation',project_id:'project',cwd:'/workspace/project',worktree_id:null,revision:1,turns:[],engine:'internal',mode:'agent'},
 project:{id:'project',name:'Project',path:'/workspace/project'},
}));

// Keep App's layout persistence and DockWorkspace's actual close/activation flow.
// Feature contents and transport are independent of the navigation regression.
vi.mock('./lib/compatibility',()=>({checkCompatibility:vi.fn(async()=>{})}));
vi.mock('./lib/use-observation-active',()=>({useObservationActive:(visible=true)=>visible}));
vi.mock('./lib/workspace-fullscreen',()=>({workspaceFullscreen:vi.fn(async()=>false)}));
vi.mock('./lib/native',async original=>({...await original<typeof import('./lib/native')>(),nativeWorkspace:()=>true,isDetachedWindow:()=>false,listenNativeLocks:async()=>()=>{},listenNativeRedock:async()=>()=>{}}));
vi.mock('./lib/api',async original=>({...await original<typeof import('./lib/api')>(),
 request:vi.fn(async(path:string)=>{
  if(path==='/auth/methods')return {configured:true,methods:[]};
  if(path==='/auth/me')return {principal:{id:fixture.owner,display_name:'Owner',scopes:[]},session_id:'authenticated-session'};
  if(path==='/api/workspace/project-pins')return {revision:0,projects:[]};
  throw Error('Unexpected request: '+path);
 }),
 gql:vi.fn(async()=>({projects:[fixture.project,{id:'other',name:'Other project',path:'/workspace/other'}],engines:[],agent_providers:[]})),
}));
vi.mock('./lib/workspace-data',()=>({listSessions:vi.fn(async()=>[fixture.session]),SessionCache:class{async read(){return fixture.session}clear(){}}}));
vi.mock('./lib/session-changes',()=>({useSessionChanges:()=>({scope:'fixture',available:true,unavailableReason:null,data:null,error:null,loading:false,refresh:async()=>{}})}));
vi.mock('./features/CompanionHabitat',()=>({default:()=>null}));
vi.mock('./features/CaptureStatus',()=>({default:()=>null}));
vi.mock('./features/Chat',()=>({default:({session,onOpenSurface,onOpenFile}:{session:unknown;onOpenSurface:(surface:'editor')=>void;onOpenFile:(path:string,line?:number)=>void})=><div><button disabled={!session} onClick={()=>onOpenSurface('editor')}>Open editor</button><button disabled={!session} onClick={()=>onOpenFile('src/app.ts',7)}>Open src/app.ts:7</button></div>}));
vi.mock('./features/Workbench',()=>({default:({visible,fileRequest}:{visible:boolean;fileRequest?:{path:string;line?:number}})=><section aria-label="Editor fixture" style={{display:visible?'block':'none'}}><textarea aria-label="Unsaved editor buffer" defaultValue="original"/>{fileRequest&&<p>Requested {fileRequest.path}:{fileRequest.line}</p>}</section>}));
vi.mock('./features/BrowserSurface',()=>({default:()=>null}));
vi.mock('./features/SettingsGeneral',()=>({default:()=> <h2>General fixture</h2>}));
vi.mock('./features/Managers',()=>({default:({section,projectId,sessionId}:{section:string;projectId:string|null;sessionId:string|null})=><section><h2>{section} fixture</h2><span data-testid="manager-project">{projectId||'account'}</span><span data-testid="manager-session">{sessionId||'none'}</span></section>}));


beforeEach(()=>{
 configure({asyncUtilTimeout:10000});
 localStorage.clear();history.replaceState(null,'','/?session=conversation&layout=chat');
 vi.stubGlobal('innerWidth',1280);
 vi.stubGlobal('matchMedia',vi.fn(()=>({matches:false,addEventListener:vi.fn(),removeEventListener:vi.fn()})));
 vi.stubGlobal('ResizeObserver',class{observe(){}unobserve(){}disconnect(){}});
});
afterEach(()=>{cleanup();configure({asyncUtilTimeout:1000});vi.unstubAllGlobals();localStorage.clear();history.replaceState(null,'','/')});


it('keeps account-only managers global while retaining explicit project scopes and valid session ownership',async()=>{
 render(<App/>);await waitFor(()=>expect(screen.getByRole('button',{name:'Open editor'})).toBeEnabled());
 fireEvent.click(within(screen.getByRole('navigation',{name:'Workspace navigation'})).getByRole('button',{name:'Settings'}));
 const dialog=await screen.findByRole('dialog',{name:'Settings'});await within(dialog).findByRole('heading',{name:'General fixture'});
 expect(within(dialog).queryByRole('combobox',{name:'Settings scope'})).toBeNull();
 const category=(name:string)=>fireEvent.click(within(dialog).getByRole('navigation',{name:'Settings categories'}).querySelector(`button[aria-label="${name}"]`)||within(dialog).getByRole('button',{name}));
 category('Memory');await within(dialog).findByRole('heading',{name:'memory fixture'});
 const scope=within(dialog).getByRole('combobox',{name:'Settings scope'});expect(scope).toHaveValue('');
 fireEvent.change(scope,{target:{value:'other'}});await waitFor(()=>expect(screen.getByTestId('manager-project')).toHaveTextContent('other'));expect(screen.getByTestId('manager-session')).toHaveTextContent('none');
 for(const name of ['Models & engines','Tools & connections','Hooks']){
  category(name);await waitFor(()=>expect(within(dialog).queryByRole('combobox',{name:'Settings scope'})).toBeNull());expect(screen.getByTestId('manager-project')).toHaveTextContent('account');expect(screen.getByTestId('manager-session')).toHaveTextContent('none');
 }
 category('Memory');await within(dialog).findByRole('heading',{name:'memory fixture'});expect(within(dialog).getByRole('combobox',{name:'Settings scope'})).toHaveValue('other');
 fireEvent.change(within(dialog).getByRole('combobox',{name:'Settings scope'}),{target:{value:'project'}});await waitFor(()=>expect(screen.getByTestId('manager-project')).toHaveTextContent('project'));expect(screen.getByTestId('manager-session')).toHaveTextContent('conversation');
 fireEvent.keyDown(dialog,{key:'Escape'});await waitFor(()=>expect(screen.queryByRole('dialog',{name:'Settings'})).toBeNull());expect(screen.getByRole('button',{name:'Open editor'})).toBeEnabled();expect(new URLSearchParams(location.search).get('session')||location.pathname).toContain('conversation');
});
