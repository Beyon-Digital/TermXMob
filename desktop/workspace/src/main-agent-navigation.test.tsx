import {afterEach,beforeEach,expect,it,vi} from 'vitest';
import {act,cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import App from './App';
import {type Session} from './lib/api';

const mocks=vi.hoisted(()=>({request:vi.fn(),ensure:vi.fn(),read:vi.fn()}));
vi.mock('./lib/api',()=>({request:mocks.request,gql:vi.fn(async()=>({projects:[],engines:[],agent_providers:[]})),json:(method:string,data:unknown)=>({method,body:JSON.stringify(data)}),ApiError:class extends Error{},lockState:vi.fn(async()=>({locked:true,principal:{id:'owner',display_name:'Owner'},session_id:'auth',methods:[]})),signOut:vi.fn(),login:vi.fn(),lockWorkspace:vi.fn()}));
vi.mock('./lib/compatibility',()=>({checkCompatibility:vi.fn(async()=>{})}));
vi.mock('./lib/native',()=>({nativeWorkspace:()=>false,nativeLogin:vi.fn(),isDetachedWindow:()=>false,listenNativeLocks:async()=>()=>{},listenNativeRedock:async()=>()=>{},detachWorkspace:vi.fn(),requestRedock:vi.fn(),acceptRedock:vi.fn(),rejectRedock:vi.fn(),commitRedock:vi.fn(),cancelRedock:vi.fn()}));
vi.mock('./lib/window-journal',()=>({restoreWindowDrafts:async()=>{},persistWindowRecord:async()=>{},persistWindowDraft:async()=>{},restoreArtifactDrafts:async()=>[],recoverableWindows:async()=>[],bufferCount:async()=>0,detachedBrowser:false}));
vi.mock('./lib/project-pins',()=>({loadProjectPins:async()=>({revision:0,projects:[]}),orderProjectGroups:(rows:unknown)=>rows,changeProjectPin:vi.fn()}));
vi.mock('./lib/session-changes',()=>({useSessionChanges:()=>({data:null,loading:false})}));
vi.mock('./lib/workspace-fullscreen',()=>({workspaceFullscreen:async()=>false}));
vi.mock('./lib/workspace-data',()=>({listSessions:async()=>[],SessionCache:class {read=mocks.read;clear(){};history=mocks.read}}));
vi.mock('./features/Chat',()=>({default:({session,onConfigureAgent}:{session:Session|null;onConfigureAgent:()=>void})=><div><p data-testid="selected-chat">{session?.id||'none'}</p><button onClick={onConfigureAgent}>Configure agent</button></div>}));
vi.mock('./features/OrchestratorStatus',()=>({default:({onOpen}:{onOpen:(id:string)=>void})=><button onClick={()=>onOpen('child')}>Inspect child</button>}));
vi.mock('./features/SettingsSurface',()=>({default:({children,onClose}:{children:React.ReactNode;onClose:()=>void})=><section><button onClick={onClose}>Close settings</button>{children}</section>}));
vi.mock('./features/Managers',()=>({default:()=> <div data-testid="models-settings">Model settings</div>}));
vi.mock('./features/CaptureStatus',()=>({default:()=>null}));
vi.mock('./features/CompanionHabitat',()=>({default:()=>null}));
vi.mock('./components/LockScreen',()=>({default:()=> <div data-testid="locked">Locked</div>}));
vi.mock('./components/DockWorkspace',()=>({default:({children}:{children:Record<string,React.ReactNode>})=><div>{children.conversation}</div>}));
vi.mock('./components/WorkspaceNavigation',()=>({default:({rail,children,footer}:{rail:React.ReactNode;children:React.ReactNode;footer:React.ReactNode})=><nav>{rail}{children}{footer}</nav>}));
vi.mock('@tanstack/react-virtual',()=>({useVirtualizer:()=>({getTotalSize:()=>0,getVirtualItems:()=>[]})}));

const principal={id:'owner',display_name:'Owner',scopes:[]};
const auth={principal,session_id:'auth'};
function chat(id:string):Session{return {id,title:id,engine:'internal',mode:'agent',pinned:false,archived:false,draft_text:'',revision:1,updated_at:0,scroll:0,turns:[]};}
function deferred<T>(){let resolve!:(value:T)=>void;const promise=new Promise<T>(done=>{resolve=done});return {promise,resolve};}

beforeEach(()=>{
 localStorage.clear();sessionStorage.clear();history.replaceState(null,'','/');
 vi.stubGlobal('matchMedia',()=>({matches:false,addEventListener:vi.fn(),removeEventListener:vi.fn()}));
 mocks.read.mockImplementation(async(id:string)=>chat(id));
 mocks.ensure.mockResolvedValue({session:chat('main')});
 mocks.request.mockImplementation(async(path:string)=>{
  if(path==='/auth/methods')return {configured:true,methods:[]};
  if(path==='/auth/me')return auth;
  if(path==='/api/workspace/orchestrator/ensure')return mocks.ensure();
  if(path.startsWith('/api/workspace/presets'))return {presets:[]};
  throw Error('Unexpected test request '+path);
 });
});
afterEach(()=>{cleanup();vi.clearAllMocks();vi.unstubAllGlobals();history.replaceState(null,'','/')});

it('opens one primary on first entry and preserves an explicitly linked child',async()=>{
 history.replaceState(null,'','/session/child');
 render(<App/>);
 await waitFor(()=>expect(screen.getByTestId('selected-chat')).toHaveTextContent('child'));
 expect(mocks.ensure).not.toHaveBeenCalled();
});

it('does not replace a child selected while the default primary request is pending',async()=>{
 const pending=deferred<{session:Session}>();mocks.ensure.mockReturnValue(pending.promise);
 render(<App/>);
 await waitFor(()=>expect(mocks.ensure).toHaveBeenCalledOnce());
 fireEvent.click(await screen.findByRole('button',{name:'Inspect child'}));
 await waitFor(()=>expect(screen.getByTestId('selected-chat')).toHaveTextContent('child'));
 await act(async()=>pending.resolve({session:chat('main')}));
 expect(screen.getByTestId('selected-chat')).toHaveTextContent('child');
 expect(location.pathname).toBe('/session/child');
});

it('keeps newer settings navigation when a manually opened primary resolves late',async()=>{
 history.replaceState(null,'','/session/child');
 const pending=deferred<{session:Session}>();mocks.ensure.mockReturnValue(pending.promise);
 render(<App/>);
 await waitFor(()=>expect(screen.getByTestId('selected-chat')).toHaveTextContent('child'));
 fireEvent.click(screen.getAllByRole('button',{name:'Main agent'}).at(-1)!);
 await waitFor(()=>expect(mocks.ensure).toHaveBeenCalledOnce());
 fireEvent.click(screen.getAllByRole('button',{name:'Settings'}).at(-1)!);
 expect(await screen.findByRole('heading',{name:/^General$/})).toBeVisible();
 await act(async()=>pending.resolve({session:chat('main')}));
 expect(screen.getByRole('heading',{name:/^General$/})).toBeVisible();
 expect(location.pathname).toBe('/manage/general');
});

it('resolves the main conversation when browser history returns to the root',async()=>{
 history.replaceState(null,'','/session/child');
 render(<App/>);
 await waitFor(()=>expect(screen.getByTestId('selected-chat')).toHaveTextContent('child'));
 await act(async()=>{history.replaceState(null,'','/');window.dispatchEvent(new PopStateEvent('popstate'))});
 await waitFor(()=>expect(screen.getByTestId('selected-chat')).toHaveTextContent('main'));
 expect(mocks.ensure).toHaveBeenCalledOnce();
});

it('retries root resolution after locking invalidates the first primary response',async()=>{
 const pending=deferred<{session:Session}>();mocks.ensure.mockReturnValueOnce(pending.promise).mockResolvedValue({session:chat('main')});
 render(<App/>);
 await waitFor(()=>expect(mocks.ensure).toHaveBeenCalledOnce());
 await act(async()=>window.dispatchEvent(new Event('termx-session-locked')));
 expect(await screen.findByTestId('locked')).toBeVisible();
 await act(async()=>pending.resolve({session:chat('main')}));
 await act(async()=>window.dispatchEvent(new Event('termx-session-unlocked')));
 await waitFor(()=>expect(screen.getByTestId('selected-chat')).toHaveTextContent('main'));
 expect(mocks.ensure).toHaveBeenCalledTimes(2);
});

it('binds a newly created chat before its asynchronous history read can return',async()=>{
 history.replaceState(null,'','/session/previous');
 const historyRead=deferred<Session>();
 mocks.read.mockImplementation((id:string)=>id==='focused'?historyRead.promise:Promise.resolve(chat(id)));
 const original=mocks.request.getMockImplementation()!;
 mocks.request.mockImplementation((path:string,...args:unknown[])=>path==='/api/workspace/sessions'?Promise.resolve(chat('focused')):original(path,...args));
 render(<App/>);
 await waitFor(()=>expect(screen.getByTestId('selected-chat')).toHaveTextContent('previous'));
 fireEvent.click(screen.getByRole('button',{name:'New chat'}));
 const dialog=await screen.findByRole('dialog',{name:'New conversation'});
 await waitFor(()=>expect(dialog.querySelector('button[type="submit"]')).not.toBeDisabled());
 fireEvent.click(dialog.querySelector('button[type="submit"]')!);
 await waitFor(()=>expect(location.pathname).toBe('/session/focused'));
 expect(screen.getByTestId('selected-chat')).toHaveTextContent('focused');
 await act(async()=>historyRead.resolve(chat('focused')));
});
