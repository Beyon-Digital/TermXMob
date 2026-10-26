import {afterEach,beforeEach,expect,it,vi} from 'vitest';
import {act,cleanup,fireEvent,render,screen,waitFor,within} from '@testing-library/react';
import App from './App';
import {type Session} from './lib/api';
import {consumedTransfers,draftSubmissionOwners,draftWrites,sessionDrafts,submittingDrafts} from './lib/drafts';

const mocks=vi.hoisted(()=>({request:vi.fn(),gql:vi.fn()}));
vi.mock('./lib/api',()=>({request:mocks.request,gql:mocks.gql,json:(method:string,data:unknown)=>({method,body:JSON.stringify(data)}),ApiError:class extends Error{},lockState:vi.fn(async()=>({locked:true,principal:{id:'owner',display_name:'Owner'},session_id:'auth',methods:[]})),signOut:vi.fn(),login:vi.fn(),lockWorkspace:vi.fn()}));
vi.mock('./lib/compatibility',()=>({checkCompatibility:vi.fn(async()=>{})}));
vi.mock('./lib/native',()=>({nativeWorkspace:()=>false,nativeLogin:vi.fn(),isDetachedWindow:()=>false,listenNativeLocks:async()=>()=>{},listenNativeRedock:async()=>()=>{},detachWorkspace:vi.fn(),requestRedock:vi.fn(),acceptRedock:vi.fn(),rejectRedock:vi.fn(),commitRedock:vi.fn(),cancelRedock:vi.fn()}));
vi.mock('./lib/window-journal',()=>({restoreWindowDrafts:async()=>{},persistWindowRecord:async()=>{},persistWindowDraft:async()=>{},restoreArtifactDrafts:async()=>[],recoverableWindows:async()=>[],bufferCount:async()=>0,detachedBrowser:false}));
vi.mock('./lib/project-pins',()=>({loadProjectPins:async()=>({revision:0,projects:[]}),orderProjectGroups:(rows:unknown)=>rows,changeProjectPin:vi.fn()}));
vi.mock('./lib/session-changes',()=>({useSessionChanges:()=>({data:null,loading:false})}));
vi.mock('./lib/workspace-fullscreen',()=>({workspaceFullscreen:async()=>false}));
vi.mock('./features/OrchestratorStatus',()=>({default:({onOpen}:{onOpen:(id:string)=>void})=><button onClick={()=>onOpen('other-child')}>Inspect other child</button>}));
vi.mock('./features/SettingsSurface',()=>({default:({children}:{children:React.ReactNode})=><section>{children}</section>}));
vi.mock('./features/Managers',()=>({default:()=>null}));
vi.mock('./features/CaptureStatus',()=>({default:()=>null}));
vi.mock('./features/CompanionHabitat',()=>({default:()=>null}));
vi.mock('./components/LockScreen',()=>({default:()=>null}));
vi.mock('./components/DockWorkspace',()=>({default:({children}:{children:Record<string,React.ReactNode>})=><div>{children.conversation}</div>}));
vi.mock('./components/WorkspaceNavigation',()=>({default:({rail,children,footer}:{rail:React.ReactNode;children:React.ReactNode;footer:React.ReactNode})=><nav>{rail}{children}{footer}</nav>}));
vi.mock('@tanstack/react-virtual',()=>({useVirtualizer:()=>({getTotalSize:()=>0,getVirtualItems:()=>[],scrollToIndex:vi.fn(),scrollToOffset:vi.fn(),measureElement:vi.fn()})}));
// App, Chat, the composer submission adapter, listSessions and SessionCache are
// real. These deterministic HTTP/GraphQL boundaries perform no inference.

const provider={id:'fixture-provider',name:'Configured fixture',model:'fixture-model',capabilities:['chat'],secret_configured:true};
const auth={principal:{id:'owner',display_name:'Owner',scopes:[]},session_id:'auth'};
function chat(id:string,draft=''):Session{return {id,title:id,engine:'internal',provider_id:provider.id,model:'fixture-model',mode:'agent',cwd:'/synthetic/'+id,pinned:false,archived:false,draft_text:draft,draft_context:[],revision:1,updated_at:1,scroll:0,turns:[]};}
function deferred<T>(){let resolve!:(value:T)=>void;const promise=new Promise<T>(done=>{resolve=done});return {promise,resolve};}
const emptyTurns=()=>({edges:[],pageInfo:{hasNextPage:false,hasPreviousPage:false,startCursor:null,endCursor:null}});
let rows:Map<string,Session>,focusedHistory:ReturnType<typeof deferred<ReturnType<typeof emptyTurns>>>,historyReleased:boolean;
const turnCalls=()=>mocks.request.mock.calls.filter(([path,init])=>typeof path==='string'&&path.endsWith('/turns')&&(init as RequestInit|undefined)?.method==='POST');

beforeEach(()=>{
 vi.clearAllMocks();localStorage.clear();sessionStorage.clear();sessionDrafts.clear();draftWrites.clear();submittingDrafts.clear();draftSubmissionOwners.clear();consumedTransfers.clear();
 history.replaceState(null,'','/session/previous');
 vi.stubGlobal('matchMedia',()=>({matches:false,addEventListener:vi.fn(),removeEventListener:vi.fn()}));
 rows=new Map([['previous',chat('previous','Previous conversation draft')],['other-child',chat('other-child','Saved child draft')]]);
 focusedHistory=deferred<ReturnType<typeof emptyTurns>>();historyReleased=false;
 mocks.gql.mockImplementation(async(query:string,variables:Record<string,unknown>={})=>{
  if(query.includes('workspace_sessions('))return {workspace_sessions:{edges:[...rows.values()].map(snapshot=>({node:{snapshot:structuredClone(snapshot)}})),pageInfo:{hasNextPage:false,endCursor:null}}};
  if(query.includes('nodes(ids:')){
   const id=atob((variables.ids as string[])[0]).slice('WorkspaceSessionNode:'.length),snapshot=rows.get(id);
   if(!snapshot)throw Error('Unknown fixture conversation '+id);return {nodes:[{snapshot:structuredClone(snapshot)}]};
  }
  if(query.includes('workspace_turns('))return {workspace_turns:variables.id==='focused'&&!historyReleased?await focusedHistory.promise:emptyTurns()};
  if(query.includes('agent_providers'))return {projects:[],engines:[],agent_providers:[provider]};
  if(query.includes('engine_models'))return {engine_models:['fixture-model']};
  throw Error('Unexpected fixture GraphQL query '+query);
 });
 mocks.request.mockImplementation(async(path:string,init:RequestInit={})=>{
  if(path==='/auth/methods')return {configured:true,methods:[]};
  if(path==='/auth/me')return auth;
  if(path.startsWith('/api/workspace/presets'))return {presets:[]};
  if(path==='/api/runners')return [];
  if(path==='/api/runner-agents/capabilities')return {accounts:[]};
  if(path==='/api/workspace/sessions'&&init.method==='POST'){const created=chat('focused');rows.set(created.id,created);return structuredClone(created);}
  const match=path.match(/^\/api\/workspace\/sessions\/([^/?]+)(\/turns|\/queue)?(?:\?turns=false)?$/);
  if(match){
   const id=match[1],row=rows.get(id);if(!row)throw Error('Unknown fixture conversation '+id);
   if(match[2]==='/queue'&&!init.method)return {items:[]};
   if(match[2]==='/turns'&&init.method==='POST'){const body=JSON.parse(init.body as string);row.draft_text='';row.draft_context=[];row.revision++;return {id:'task-'+id,prompt:body.prompt,status:'completed',result:'Synthetic dispatch accepted'};}
   if(init.method==='PATCH'){const body=JSON.parse(init.body as string);if(body.revision!==row.revision)throw Error('Fixture revision conflict');Object.assign(row,body.changes);row.revision++;}
   return structuredClone(row);
  }
  throw Error('Unexpected fixture HTTP request '+path);
 });
});
afterEach(async()=>{
 cleanup();historyReleased=true;focusedHistory.resolve(emptyTurns());await Promise.resolve();sessionDrafts.clear();draftWrites.clear();submittingDrafts.clear();draftSubmissionOwners.clear();consumedTransfers.clear();vi.unstubAllGlobals();history.replaceState(null,'','/');
});

async function createFocused(){
 await waitFor(()=>expect(screen.getByRole('textbox',{name:'Message'})).toHaveValue('Previous conversation draft'));
 fireEvent.click(screen.getByRole('button',{name:'New chat'}));
 const dialog=await screen.findByRole('dialog',{name:'New conversation'});
 const create=within(dialog).getByRole('button',{name:'Create conversation'});await waitFor(()=>expect(create).toBeEnabled());fireEvent.click(create);
 await waitFor(()=>expect(location.pathname).toBe('/session/focused'));
 await waitFor(()=>expect(mocks.gql.mock.calls.some(([query,variables])=>String(query).includes('workspace_turns(')&&(variables as {id?:string}|undefined)?.id==='focused')).toBe(true));
 // The pending history read must not leave the previous conversation bound to
 // this real composer, even for the first keystroke after creation.
 expect(screen.getByRole('textbox',{name:'Message'})).toHaveValue('');
}

it('sends from a newly created real composer to the new conversation before history resolves',async()=>{
 render(<App/>);await createFocused();
 fireEvent.change(screen.getByRole('textbox',{name:'Message'}),{target:{value:'Work belonging only to the focused chat'}});
 fireEvent.click(screen.getByRole('button',{name:'Send message'}));
 await waitFor(()=>expect(turnCalls()).toHaveLength(1));
 expect(turnCalls()[0][0]).toBe('/api/workspace/sessions/focused/turns');expect(JSON.parse((turnCalls()[0][1] as RequestInit).body as string)).toMatchObject({prompt:'Work belonging only to the focused chat',request_id:expect.any(String)});
 expect(rows.get('previous')?.draft_text).toBe('Previous conversation draft');
 expect(turnCalls().some(([path])=>path==='/api/workspace/sessions/previous/turns')).toBe(false);
 await waitFor(()=>expect(screen.getByRole('textbox',{name:'Message'})).toHaveValue(''));
 historyReleased=true;await act(async()=>focusedHistory.resolve(emptyTurns()));
 expect(location.pathname).toBe('/session/focused');expect(screen.queryByText(/draft changed in another window/i)).toBeNull();
});

it('keeps a newer child selection and real send target after the focused history responds late',async()=>{
 render(<App/>);await createFocused();
 fireEvent.change(screen.getByRole('textbox',{name:'Message'}),{target:{value:'Draft belonging only to focused'}});
 fireEvent.click(screen.getByRole('button',{name:'Inspect other child'}));
 await waitFor(()=>expect(location.pathname).toBe('/session/other-child'));
 await waitFor(()=>expect(screen.getByRole('textbox',{name:'Message'})).toHaveValue('Saved child draft'));
 fireEvent.change(screen.getByRole('textbox',{name:'Message'}),{target:{value:'Run work only in the selected child'}});
 historyReleased=true;await act(async()=>focusedHistory.resolve(emptyTurns()));
 expect(location.pathname).toBe('/session/other-child');expect(screen.getByRole('textbox',{name:'Message'})).toHaveValue('Run work only in the selected child');
 fireEvent.click(screen.getByRole('button',{name:'Send message'}));await waitFor(()=>expect(turnCalls()).toHaveLength(1));
 expect(turnCalls()[0][0]).toBe('/api/workspace/sessions/other-child/turns');expect(JSON.parse((turnCalls()[0][1] as RequestInit).body as string).prompt).toBe('Run work only in the selected child');
 expect(turnCalls().some(([path])=>path==='/api/workspace/sessions/previous/turns'||path==='/api/workspace/sessions/focused/turns')).toBe(false);
 expect(sessionDrafts.get('focused')?.text).toBe('Draft belonging only to focused');expect(rows.get('previous')?.draft_text).toBe('Previous conversation draft');expect(screen.queryByText(/draft changed in another window/i)).toBeNull();
});


it('starts a generic new chat without a hidden project or folder from a cancelled creation',async()=>{
 const previous=mocks.gql.getMockImplementation()!;
 mocks.gql.mockImplementation((query:string,...args:any[])=>query.includes('agent_providers')?Promise.resolve({projects:[{id:'project-a',name:'Project A',path:'/synthetic/project-a'}],engines:[],agent_providers:[provider]}):previous(query,...args));
 render(<App/>);
 await waitFor(()=>expect(screen.getByRole('textbox',{name:'Message'})).toHaveValue('Previous conversation draft'));
 fireEvent.click(screen.getByRole('button',{name:'New chat'}));
 let dialog=await screen.findByRole('dialog',{name:'New conversation'});
 fireEvent.click(within(dialog).getByText('Project & agent options',{selector:'summary'}));
 fireEvent.change(within(dialog).getByRole('combobox',{name:'Project'}),{target:{value:'project-a'}});
 fireEvent.change(within(dialog).getByLabelText('Add a project folder'),{target:{value:'/synthetic/previous-folder'}});
 fireEvent.click(within(dialog).getByRole('button',{name:/^Close$/}));
 fireEvent.click(screen.getByRole('button',{name:'New chat'}));
 dialog=await screen.findByRole('dialog',{name:'New conversation'});
 fireEvent.click(within(dialog).getByText('Project & agent options',{selector:'summary'}));
 expect(within(dialog).getByRole('combobox',{name:'Project'})).toHaveValue('');
 expect(within(dialog).getByLabelText('Add a project folder')).toHaveValue('');
 expect(mocks.request.mock.calls.some(([path,init])=>path==='/api/workspace/sessions'&&(init as RequestInit)?.method==='POST')).toBe(false);
 expect(screen.getByRole('textbox',{name:'Message'})).toHaveValue('Previous conversation draft');
});


it('a delayed project registration cannot retarget a newer creation dialog',async()=>{
 const registered=deferred<{register_project:{id:string;name:string;path:string}}>();
 const target={id:'late-project',name:'Late project',path:'/synthetic/late-project'};
 const previous=mocks.gql.getMockImplementation()!;
 mocks.gql.mockImplementation((query:string,...args:any[])=>query.includes('register_project')?registered.promise:query.includes('agent_providers')?Promise.resolve({projects:[target],engines:[],agent_providers:[provider]}):previous(query,...args));
 render(<App/>);
 await waitFor(()=>expect(screen.getByRole('textbox',{name:'Message'})).toHaveValue('Previous conversation draft'));
 fireEvent.click(screen.getByRole('button',{name:'New chat'}));
 let dialog=await screen.findByRole('dialog',{name:'New conversation'});
 fireEvent.click(within(dialog).getByText('Project & agent options',{selector:'summary'}));
 fireEvent.change(within(dialog).getByLabelText('Add a project folder'),{target:{value:target.path}});
 const create=within(dialog).getByRole('button',{name:'Create conversation'});
 await waitFor(()=>expect(create).toBeEnabled());fireEvent.click(create);
 await waitFor(()=>expect(mocks.gql.mock.calls.some(([query])=>String(query).includes('register_project'))).toBe(true));
 fireEvent.click(within(dialog).getByRole('button',{name:/^Close$/}));
 fireEvent.click(screen.getByRole('button',{name:'New chat'}));
 dialog=await screen.findByRole('dialog',{name:'New conversation'});
 fireEvent.click(within(dialog).getByText('Project & agent options',{selector:'summary'}));
 await act(async()=>registered.resolve({register_project:target}));
 await waitFor(()=>expect(within(dialog).getByRole('button',{name:'Create conversation'})).toBeEnabled());
 expect(within(dialog).getByRole('combobox',{name:'Project'})).toHaveValue('');
 expect(within(dialog).getByLabelText('Add a project folder')).toHaveValue('');
 expect(location.pathname).toBe('/session/previous');
});
