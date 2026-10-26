import {afterEach,beforeEach,expect,it,vi} from 'vitest';
import {cleanup,configure,fireEvent,render,screen,waitFor,within} from '@testing-library/react';
import App from './App';
import {defaultLayout,LayoutPresetStore,type LayoutSnapshot} from './lib/layout-presets';

const fixture=vi.hoisted(()=>({
 owner:'surface-navigation-owner',
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
 gql:vi.fn(async()=>({projects:[fixture.project],engines:[],agent_providers:[]})),
}));
vi.mock('./lib/workspace-data',()=>({listSessions:vi.fn(async()=>[fixture.session]),SessionCache:class{async read(){return fixture.session}clear(){}}}));
vi.mock('./lib/session-changes',()=>({useSessionChanges:()=>({scope:'fixture',available:true,unavailableReason:null,data:null,error:null,loading:false,refresh:async()=>{}})}));
vi.mock('./features/CompanionHabitat',()=>({default:()=>null}));
vi.mock('./features/CaptureStatus',()=>({default:()=>null}));
vi.mock('./features/Chat',()=>({default:({session,onOpenSurface,onOpenFile}:{session:unknown;onOpenSurface:(surface:'editor')=>void;onOpenFile:(path:string,line?:number)=>void})=><div><button disabled={!session} onClick={()=>onOpenSurface('editor')}>Open editor</button><button disabled={!session} onClick={()=>onOpenFile('src/app.ts',7)}>Open src/app.ts:7</button></div>}));
vi.mock('./features/Workbench',()=>({default:({visible,fileRequest}:{visible:boolean;fileRequest?:{path:string;line?:number}})=><section aria-label="Editor fixture" style={{display:visible?'block':'none'}}><textarea aria-label="Unsaved editor buffer" defaultValue="original"/>{fileRequest&&<p>Requested {fileRequest.path}:{fileRequest.line}</p>}</section>}));
vi.mock('./features/BrowserSurface',()=>({default:()=>null}));

beforeEach(()=>{
 configure({asyncUtilTimeout:10000});
 localStorage.clear();history.replaceState(null,'','/?session=conversation&layout=chat');
 vi.stubGlobal('innerWidth',1280);
 vi.stubGlobal('matchMedia',vi.fn(()=>({matches:false,addEventListener:vi.fn(),removeEventListener:vi.fn()})));
 vi.stubGlobal('ResizeObserver',class{observe(){}unobserve(){}disconnect(){}});
});
afterEach(()=>{cleanup();configure({asyncUtilTimeout:1000});vi.unstubAllGlobals();localStorage.clear();history.replaceState(null,'','/')});

it.each(['Open editor','Open src/app.ts:7'])('reveals a closed Files tab through %s without replacing unsaved buffers',async(action)=>{
 render(<App/>);
 await waitFor(()=>expect(screen.getByRole('button',{name:action})).toBeEnabled());
 fireEvent.click(screen.getByRole('button',{name:'Workspace actions'}));
 fireEvent.click(within(screen.getByRole('dialog',{name:'Workspace actions'})).getByRole('button',{name:'Files & code'}));
 const buffer=await screen.findByRole('textbox',{name:'Unsaved editor buffer'});
 fireEvent.change(buffer,{target:{value:'unsaved change'}});
 fireEvent.click(within(screen.getByRole('region',{name:'editor panel'})).getByRole('button',{name:'Close Files tab'}));
 await waitFor(()=>expect(buffer).not.toBeVisible());
 expect(new LayoutPresetStore(fixture.owner).arrangement('workbench').dock.editor).toBe('hidden');
 fireEvent.click(screen.getByRole('button',{name:'Back to chat'}));
 fireEvent.click(screen.getByRole('button',{name:action}));
 await waitFor(()=>expect(buffer).toBeVisible());
 expect(screen.getByRole('textbox',{name:'Unsaved editor buffer'})).toBe(buffer);
 expect(buffer).toHaveValue('unsaved change');
 expect(screen.getByRole('region',{name:'conversation panel'})).toHaveStyle({gridArea:'main'});
 expect(screen.getByRole('region',{name:'editor panel'})).toHaveStyle({gridArea:'split'});
 if(action==='Open src/app.ts:7')expect(screen.getByText('Requested src/app.ts:7')).toBeVisible();
});

it.each(['right','bottom'] as const)('reveals the saved %s editor region without moving the other panels',async(region)=>{
 const snapshot:LayoutSnapshot={...defaultLayout('workbench'),right:region!=='right',bottom:region!=='bottom',dock:{...defaultLayout('workbench').dock,editor:region,conversation:'main',browser:'split'}};
 const book=new LayoutPresetStore(fixture.owner);book.remember(snapshot);book.remember(defaultLayout('chat'));
 render(<App/>);
 const opener=await screen.findByRole('button',{name:'Open editor'});
 await waitFor(()=>expect(opener).toBeEnabled());fireEvent.click(opener);
 expect(await screen.findByRole('textbox',{name:'Unsaved editor buffer'})).toBeVisible();
 expect(screen.getByRole('region',{name:'editor panel'})).toHaveStyle({gridArea:region});
 expect(screen.getByRole('region',{name:'conversation panel'})).toHaveStyle({gridArea:'main'});
 expect(screen.getByRole('region',{name:'browser panel'})).toHaveStyle({gridArea:'split'});
 const restored=book.arrangement('workbench');expect(restored.dock).toEqual(snapshot.dock);expect(restored[region]).toBe(true);
});
