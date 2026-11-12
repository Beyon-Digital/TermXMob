import {afterEach,beforeEach,expect,it,vi} from 'vitest';
import {act,cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import App from './App';
import type {Session} from './lib/api';

const mocks=vi.hoisted(()=>({request:vi.fn(),gql:vi.fn(),pins:vi.fn(),list:vi.fn(),ensure:vi.fn()}));
vi.mock('./lib/api',()=>({request:mocks.request,gql:mocks.gql,json:(method:string,data:unknown)=>({method,body:JSON.stringify(data)}),ApiError:class extends Error{},signOut:vi.fn(),login:vi.fn(),lockState:vi.fn(),lockWorkspace:vi.fn()}));
vi.mock('./lib/compatibility',()=>({checkCompatibility:async()=>{}}));
vi.mock('./lib/native',()=>({nativeWorkspace:()=>false,nativeLogin:vi.fn(),isDetachedWindow:()=>false,listenNativeLocks:async()=>()=>{},listenNativeRedock:async()=>()=>{},detachWorkspace:vi.fn(),requestRedock:vi.fn(),acceptRedock:vi.fn(),rejectRedock:vi.fn(),commitRedock:vi.fn(),cancelRedock:vi.fn()}));
vi.mock('./lib/window-journal',()=>({restoreWindowDrafts:async()=>{},persistWindowRecord:async()=>{},persistWindowDraft:async()=>{},recoverableWindows:async()=>[],bufferCount:async()=>0,detachedBrowser:false}));
vi.mock('./lib/project-pins',()=>({loadProjectPins:mocks.pins,orderProjectGroups:(rows:unknown)=>rows,changeProjectPin:vi.fn()}));
vi.mock('./lib/session-changes',()=>({useSessionChanges:()=>({data:null,loading:false})}));
vi.mock('./lib/workspace-fullscreen',()=>({workspaceFullscreen:async()=>false}));
vi.mock('./lib/workspace-data',()=>({listSessions:mocks.list,SessionCache:class {async read(id:string){return chat(id)} clear(){} async history(id:string){return chat(id)}}}));
vi.mock('./features/bot-attention',()=>({useBotsNeedYouCount:()=>0}));
vi.mock('./features/Chat',()=>({default:({session}:{session:Session|null})=><p data-testid="chat">{session?.id||'none'}</p>}));
vi.mock('./features/Workflows',()=>({default:({visible,sessionId,runId,onOpenSession,onNavigate}:{visible:boolean;sessionId?:string|null;runId?:string|null;onOpenSession:(id:string)=>void;onNavigate:(page:string)=>void})=><section data-testid="workflows" hidden={!visible} data-session={sessionId||''} data-run={runId||''}><input aria-label="Workflow draft fixture"/><button onClick={()=>onOpenSession('child')}>Review workflow child</button><button onClick={()=>onNavigate('projects')}>Workflow Projects</button></section>}));
vi.mock('./features/Workbench',()=>({default:({project,visible}:{project:{id:string}|null;visible:boolean})=><p data-testid="project-files" data-active={visible}>{project?.id||'none'}</p>}));
vi.mock('./features/OrchestratorStatus',()=>({default:()=>null}));
vi.mock('./features/CaptureStatus',()=>({default:()=>null}));
vi.mock('./features/CompanionHabitat',()=>({default:()=>null}));
vi.mock('./features/SettingsSurface',()=>({default:({onClose}:{onClose:()=>void})=><section aria-label="Settings fixture"><button onClick={onClose}>Close settings</button></section>}));
vi.mock('./features/ControlPlane',()=>({default:({page,projects,catalogState,onProject,onManage}:{page:string;projects:{id:string;name:string}[];catalogState:string;onProject:(project:unknown)=>void;onManage:(id:string)=>void})=><section><h1>{page}</h1><p data-testid="project-catalog-state">{catalogState}</p>{projects.map(project=><button key={project.id} onClick={()=>onProject(project)}>Open {project.name}</button>)}<button onClick={()=>onManage('models')}>Configure models</button></section>}));
vi.mock('@tanstack/react-virtual',()=>({useVirtualizer:()=>({getTotalSize:()=>0,getVirtualItems:()=>[],measure:vi.fn()})}));
const project={id:'project',name:'Project',path:'/synthetic/project'};
function chat(id:string):Session{return {id,title:id,engine:'internal',mode:'agent',pinned:false,archived:false,draft_text:'',revision:1,updated_at:0,scroll:0,turns:[]};}
beforeEach(()=>{
 localStorage.clear();sessionStorage.clear();history.replaceState(null,'','/manage/projects');
 vi.stubGlobal('innerWidth',1280);
 vi.stubGlobal('matchMedia',()=>({matches:false,addEventListener:vi.fn(),removeEventListener:vi.fn()}));
 mocks.pins.mockResolvedValue({revision:0,projects:[]});mocks.list.mockResolvedValue([]);mocks.ensure.mockResolvedValue({session:chat('main')});
 mocks.gql.mockImplementation(async(query:string)=>query.includes('projects{')?{projects:[project]}:{engines:[],agent_providers:[]});
 mocks.request.mockImplementation(async(path:string)=>{
  if(path==='/auth/methods')return {configured:true,methods:[]};
  if(path==='/auth/me')return {principal:{id:'owner',display_name:'Owner',scopes:[]},session_id:'auth'};
  if(path==='/api/workspace/orchestrator/ensure')return mocks.ensure();
  throw Error('Unexpected request '+path);
 });
});
afterEach(()=>{cleanup();vi.clearAllMocks();vi.unstubAllGlobals();history.replaceState(null,'','/')});

it('opens a project workbench without resolving or bouncing to main chat',async()=>{
 render(<App/>);fireEvent.click(await screen.findByRole('button',{name:'Open Project'}));
 await waitFor(()=>expect(screen.getByTestId('project-files')).toHaveTextContent('project'));
 expect(screen.getByTestId('project-files')).toHaveAttribute('data-active','true');
 expect(location.pathname+location.search).toBe('/?project=project');
 expect(mocks.ensure).not.toHaveBeenCalled();
 fireEvent.click(screen.getByRole('button',{name:'Back to chat'}));
 await waitFor(()=>expect(screen.getByTestId('chat')).toHaveTextContent('main'));
 expect(location.pathname).toBe('/session/main');expect(location.search).toBe('');
});

it('keeps an explicit project when an earlier automatic main-agent request resolves',async()=>{
 history.replaceState(null,'','/');let resolve!:(value:{session:Session})=>void;
 mocks.ensure.mockReturnValue(new Promise(done=>{resolve=done}));
 render(<App/>);await waitFor(()=>expect(mocks.ensure).toHaveBeenCalledOnce());
 fireEvent.click(screen.getByRole('button',{name:'Projects'}));
 fireEvent.click(await screen.findByRole('button',{name:'Open Project'}));
 await act(async()=>resolve({session:chat('main')}));
 await waitFor(()=>expect(screen.getByTestId('project-files')).toHaveAttribute('data-active','true'));
 expect(screen.getByTestId('project-files')).toHaveTextContent('project');
 expect(location.pathname+location.search).toBe('/?project=project');
});

it('restores an explicit project on reload and when browser history returns to its workbench',async()=>{
 history.replaceState(null,'','/?project=project');render(<App/>);
 await waitFor(()=>expect(screen.getByTestId('project-files')).toHaveTextContent('project'));
 expect(screen.getByTestId('project-files')).toHaveAttribute('data-active','true');expect(mocks.ensure).not.toHaveBeenCalled();
 await act(async()=>{history.replaceState(null,'','/manage/projects?project=project');window.dispatchEvent(new PopStateEvent('popstate'))});
 await screen.findByRole('heading',{name:'projects'});
 await act(async()=>{history.replaceState(null,'','/?project=project');window.dispatchEvent(new PopStateEvent('popstate'))});
 await waitFor(()=>expect(screen.getByTestId('project-files')).toHaveAttribute('data-active','true'));
 expect(mocks.ensure).not.toHaveBeenCalled();
});

it('opens the chat sidebar without leaving Projects and restores Projects after settings',async()=>{
 render(<App/>);await screen.findByRole('button',{name:'Open Project'});
 fireEvent.click(screen.getByRole('button',{name:'Show sidebar'}));
 expect(screen.getByRole('heading',{name:'projects'})).toBeVisible();expect(location.pathname).toBe('/manage/projects');
 expect(screen.getByRole('button',{name:'Hide sidebar'})).toHaveAttribute('aria-expanded','true');
 expect(document.querySelector('.breadcrumb')?.tagName).toBe('DIV');
 fireEvent.click(screen.getByRole('button',{name:'Configure models'}));
 await screen.findByRole('region',{name:'Settings fixture'});
 fireEvent.click(screen.getByRole('button',{name:'Close settings'}));
 expect(await screen.findByRole('heading',{name:'projects'})).toBeVisible();expect(location.pathname).toBe('/manage/projects');
});

it.each(['pins','configuration','sessions'])('keeps accessible projects when %s fail independently',async failure=>{
 if(failure==='pins')mocks.pins.mockRejectedValue(Error('Pins unavailable'));
 if(failure==='sessions')mocks.list.mockRejectedValue(Error('Chats unavailable'));
 if(failure==='configuration')mocks.gql.mockImplementation(async(query:string)=>{if(query.includes('projects{'))return {projects:[project]};throw Error('Agent catalog forbidden')});
 render(<App/>);expect(await screen.findByRole('button',{name:'Open Project'})).toBeVisible();
 await waitFor(()=>expect(screen.getByTestId('project-catalog-state')).toHaveTextContent('ready'));
 expect(await screen.findByRole('alert')).toHaveTextContent(/unavailable|forbidden/i);
});


it('opens workflow receipts and preserves the editor while reviewing a child',async()=>{
 history.replaceState(null,'','/manage/workflows');render(<App/>);
 const view=await screen.findByTestId('workflows');expect(view).toBeVisible();expect(screen.getByRole('button',{name:'Projects'})).toHaveAttribute('aria-current','page');
 const input=screen.getByRole('textbox',{name:'Workflow draft fixture'});fireEvent.change(input,{target:{value:'keep this draft'}});
 fireEvent.click(screen.getByRole('button',{name:'Review workflow child'}));
 await waitFor(()=>expect(screen.getByTestId('chat')).toHaveTextContent('child'));expect(view).not.toBeVisible();
 await act(async()=>window.dispatchEvent(new CustomEvent('termx-open-workflow',{detail:{runId:'a'.repeat(32)}})));
 await waitFor(()=>expect(view).toBeVisible());expect(view).toHaveAttribute('data-run','a'.repeat(32));expect(input).toHaveValue('keep this draft');
 fireEvent.click(screen.getByRole('button',{name:'Workflow Projects'}));expect(await screen.findByRole('heading',{name:'projects'})).toBeVisible();
 await act(async()=>window.dispatchEvent(new CustomEvent('termx-open-workflow',{detail:{runId:'a'.repeat(32)}})));
 await waitFor(()=>expect(view).toBeVisible());expect(view).toHaveAttribute('data-run','a'.repeat(32));
});

it('ignores malformed workflow navigation and does not run effects on receipt navigation',async()=>{
 render(<App/>);await screen.findByRole('button',{name:'Open Project'});
 await act(async()=>window.dispatchEvent(new CustomEvent('termx-open-workflow',{detail:{runId:'../../elsewhere'}})));
 expect(screen.queryByTestId('workflows')).not.toBeInTheDocument();expect(location.pathname).toBe('/manage/projects');
 expect(mocks.request.mock.calls.some(([path])=>String(path).includes('/workflows/runs'))).toBe(false);
});
