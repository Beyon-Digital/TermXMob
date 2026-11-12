import {afterEach, beforeEach, expect, it, vi} from 'vitest';
import {act, cleanup, configure, fireEvent, render, screen, waitFor, within} from '@testing-library/react';
import App from './App';
import {LayoutPresetStore} from './lib/layout-presets';
import {listSessions,SessionCache} from './lib/workspace-data';
import {useBotsNeedYouCount} from './features/bot-attention';
import type {Session,Task} from './lib/api';

const fixture = vi.hoisted(() => ({
  owner: 'workspace-mode-owner', ensure: vi.fn(), realObservation: false, coordinationUpdate: undefined as undefined|((value:any)=>void), coordinationTarget: null as HTMLElement|null, coordinationTasks: [] as Task[],
  session: {id: 'conversation', title: 'Kept conversation', project_id: 'project', cwd: '/workspace/project', worktree_id: null, revision: 1, turns: [], engine: 'internal', mode: 'agent'},
  project: {id: 'project', name: 'Project', path: '/workspace/project'},
  otherProject: {id: 'other-project', name: 'Other project', path: '/workspace/other'},
  otherSession: {id: 'other', title: 'Other conversation', project_id: 'other-project', cwd: '/workspace/other', worktree_id: null, revision: 1, turns: [], engine: 'internal', mode: 'agent'},
}));
vi.mock('./lib/compatibility', () => ({checkCompatibility: vi.fn(async () => {})}));
vi.mock('./lib/use-observation-active', async original => {const actual = await original<typeof import('./lib/use-observation-active')>(); return {useObservationActive: (visible = true) => fixture.realObservation ? actual.useObservationActive(visible) : visible}});
vi.mock('./lib/workspace-fullscreen', () => ({workspaceFullscreen: vi.fn(async () => false)}));
vi.mock('./lib/native', async original => ({...await original<typeof import('./lib/native')>(), nativeWorkspace: () => false, isDetachedWindow: () => false, listenNativeLocks: async () => () => {}, listenNativeRedock: async () => () => {}}));
vi.mock('./lib/window-journal', () => ({restoreWindowDrafts: async () => {}, persistWindowRecord: async () => {}, persistWindowDraft: async () => {}, restoreArtifactDrafts: async () => [], recoverableWindows: async () => [], bufferCount: async () => 0, detachedBrowser: false}));
vi.mock('./lib/api', async original => ({...await original<typeof import('./lib/api')>(),
  request: vi.fn(async (path: string) => {
    if (path === '/auth/methods') return {configured: true, methods: []};
    if (path === '/auth/me') return {principal: {id: fixture.owner, display_name: 'Owner', scopes: ['desktop-view']}, session_id: 'authenticated-session'};
    if (path === '/api/media/studio/projects') return {projects: []};
    if (path === '/api/workspace/project-pins') return {revision: 0, projects: []};
    if (path.startsWith('/api/workspace/presets')) return {presets: []};
    if (path === '/api/workspace/orchestrator/ensure') return fixture.ensure();
    throw Error('Unexpected request: ' + path);
  }),
  gql: vi.fn(async () => ({projects: [fixture.project, fixture.otherProject], engines: [], agent_providers: []})),
}));
vi.mock('./lib/workspace-data', () => ({listSessions: vi.fn(async () => [fixture.session]), SessionCache: class {async read(id: string) {return id === 'other' ? fixture.otherSession : fixture.session} clear() {}}}));
vi.mock('./lib/session-changes', () => ({useSessionChanges: () => ({scope: 'fixture', available: true, unavailableReason: null, data: null, error: null, loading: false, refresh: async () => {}})}));
vi.mock('./features/CompanionHabitat', () => ({default: () => null}));
vi.mock('./features/bot-attention', () => ({useBotsNeedYouCount: vi.fn(() => 0)}));
vi.mock('./features/OrchestratorStatus', () => ({default: ({onSummary,sidebarTarget,selectedTasks}:{onSummary:(value:any)=>void;sidebarTarget:HTMLElement|null;selectedTasks:Task[]}) => {fixture.coordinationUpdate=onSummary;fixture.coordinationTarget=sidebarTarget;fixture.coordinationTasks=selectedTasks;return null}}));
vi.mock('./features/CaptureStatus', () => ({default: ({enabled}: {enabled: boolean}) => enabled ? <p>Active capture warning</p> : null}));
vi.mock('./features/Chat', () => ({default: ({visible, onSelect}: {visible: boolean; onSelect: (id: string) => void}) => <section data-testid="agent-chat" data-active={visible}><textarea aria-label="Agent draft" defaultValue=""/><button onClick={() => onSelect('other')}>Open other-project chat</button></section>}));
vi.mock('./features/Workbench', () => ({default: ({visible}: {visible: boolean}) => <section data-testid="editor-fixture" data-active={visible}><textarea aria-label="Unsaved editor buffer" defaultValue="original"/></section>}));
vi.mock('./features/MediaWorkspace', () => ({default: ({visible, observing, presentation, projectId, section, onContext, onConfigureProvider}: {onConfigureProvider: () => void; visible: boolean; observing: boolean; presentation: string; projectId: string | null; section: string; onContext: (text: string, attachments: unknown[]) => void}) => <section aria-label="Studio fixture" data-active={visible} data-observing={observing} data-presentation={presentation} data-project={projectId} data-section={section}><div hidden={section==='settings'}><textarea aria-label="Studio prompt" defaultValue=""/><button onClick={onConfigureProvider}>Configure provider</button><button onClick={() => onContext('Studio media', [])}>Attach Studio media</button></div>{section==='settings'&&<section aria-label="Studio Settings fixture"><input aria-label="Settings draft" defaultValue=""/></section>}</section>}));
vi.mock('./features/Media', () => ({default: () => <section aria-label="Legacy artifacts fixture"><textarea aria-label="Artifact draft" defaultValue=""/></section>}));
vi.mock('./features/SettingsSurface', () => ({default: ({onClose}: {onClose: () => void}) => <section aria-label="Settings fixture"><button onClick={onClose}>Close settings</button></section>}));

beforeEach(() => {
  configure({asyncUtilTimeout: 10000});
  localStorage.clear(); sessionStorage.clear(); history.replaceState(null, '', '/?session=conversation&layout=chat');
  vi.mocked(listSessions).mockClear();
  fixture.realObservation = false; fixture.coordinationUpdate=undefined;fixture.coordinationTarget=null;fixture.coordinationTasks=[];
  fixture.ensure.mockReset(); fixture.ensure.mockResolvedValue({session: fixture.session});
  vi.stubGlobal('innerWidth', 1280);
  vi.stubGlobal('matchMedia', vi.fn(() => ({matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn()})));
  vi.stubGlobal('ResizeObserver', class {observe() {} unobserve() {} disconnect() {}});
});
afterEach(() => {cleanup(); vi.restoreAllMocks(); configure({asyncUtilTimeout: 1000}); vi.unstubAllGlobals(); localStorage.clear(); history.replaceState(null, '', '/')});
const mode = (name: 'Agent' | 'Studio' | 'Data') => within(screen.getByRole('group', {name: 'Workspace'})).getByRole('button', {name});

it('shows one workspace while keeping Agent drafts, visited editors and dock arrangement', async () => {
  render(<App/>);
  const draft = await screen.findByRole('textbox', {name: 'Agent draft'});
  fireEvent.change(draft, {target: {value: 'Unsent request'}});
  fireEvent.click(screen.getByRole('button', {name: 'Workspace actions'}));
  fireEvent.click(within(screen.getByRole('dialog', {name: 'Workspace actions'})).getByRole('button', {name: 'Files & code'}));
  const buffer = await screen.findByRole('textbox', {name: 'Unsaved editor buffer'});
  fireEvent.change(buffer, {target: {value: 'Unsaved code'}});
  const before = new LayoutPresetStore(fixture.owner).arrangement('workbench');
  fireEvent.click(mode('Studio'));
  const prompt = await screen.findByRole('textbox', {name: 'Studio prompt'});
  fireEvent.change(prompt, {target: {value: 'A quiet film scene'}});
  expect(document.getElementById('agent-workspace')).toHaveAttribute('hidden');
  expect(document.getElementById('agent-workspace')).toHaveAttribute('inert');
  expect(draft).not.toBeVisible(); expect(buffer).not.toBeVisible();
  expect(screen.getByTestId('agent-chat')).toHaveAttribute('data-active', 'false');
  expect(screen.getByRole('region', {name: 'Studio fixture'})).toHaveAttribute('data-active', 'true');
  expect(screen.getByRole('region', {name: 'Studio fixture'})).toHaveAttribute('data-presentation', 'standalone');
  expect(screen.queryByRole('button', {name: 'New chat'})).not.toBeInTheDocument();
  expect(screen.queryByText('Recent chats')).not.toBeInTheDocument();
  const showSidebar = screen.queryByRole('button', {name: 'Show sidebar'});
  if (showSidebar) fireEvent.click(showSidebar);
  expect(screen.getByRole('navigation', {name: 'Studio projects'})).toBeVisible();
  expect(screen.getByText('Active capture warning')).toBeVisible();
  fireEvent.click(mode('Agent'));
  expect(screen.getByRole('textbox', {name: 'Agent draft'})).toBe(draft); expect(draft).toHaveValue('Unsent request');
  expect(screen.getByRole('textbox', {name: 'Unsaved editor buffer'})).toBe(buffer); expect(buffer).toHaveValue('Unsaved code');
  expect(new LayoutPresetStore(fixture.owner).arrangement('workbench').dock).toEqual(before.dock);
  expect(prompt).not.toBeVisible();
  fireEvent.click(mode('Studio'));
  expect(screen.getByRole('textbox', {name: 'Studio prompt'})).toBe(prompt); expect(prompt).toHaveValue('A quiet film scene');
});

it('restores modes through real browser back and forward without losing the kept conversation', async () => {
  render(<App/>); await screen.findByRole('textbox', {name: 'Agent draft'});
  await waitFor(() => expect(location.pathname).toBe('/session/conversation'));
  fireEvent.click(mode('Studio')); await screen.findByRole('textbox', {name: 'Studio prompt'});
  expect(new URLSearchParams(location.search).get('session')).toBe('conversation');
  fireEvent.click(mode('Agent')); await waitFor(() => expect(location.pathname).toBe('/session/conversation'));
  await act(async () => {history.back()});
  await waitFor(() => expect(mode('Studio')).toHaveAttribute('aria-pressed', 'true'));
  expect(screen.queryByRole('textbox', {name: 'Agent draft'})).not.toBeInTheDocument();
  await act(async () => {history.forward()});
  await waitFor(() => expect(mode('Agent')).toHaveAttribute('aria-pressed', 'true'));
  expect(screen.getByRole('textbox', {name: 'Agent draft'})).toBeVisible();
});

it('opens Studio Settings in its own route and retains a direct-link composer through history', async () => {
  history.replaceState(null, '', '/?workspace=studio');
  render(<App/>); const prompt = await screen.findByRole('textbox', {name: 'Studio prompt'});
  expect(fixture.ensure).not.toHaveBeenCalled();
  fireEvent.change(prompt, {target: {value: 'Keep this studio prompt'}});
  fireEvent.click(screen.getAllByRole('button', {name: 'Settings'}).at(-1)!);
  expect(await screen.findByRole('region', {name: 'Studio Settings fixture'})).toBeVisible();
  expect(screen.queryByRole('region', {name: 'Settings fixture'})).not.toBeInTheDocument();
  expect(screen.getByRole('button', {name: 'Settings'})).toHaveAttribute('aria-current', 'page');
  expect(new URLSearchParams(location.search).get('workspace')).toBe('studio');
  expect(new URLSearchParams(location.search).get('studio')).toBe('settings');
  await act(async () => {history.back()});
  await waitFor(() => expect(screen.queryByRole('region', {name: 'Studio Settings fixture'})).not.toBeInTheDocument());
  expect(mode('Studio')).toHaveAttribute('aria-pressed', 'true');
  expect(screen.getByRole('textbox', {name: 'Studio prompt'})).toBe(prompt); expect(prompt).toHaveValue('Keep this studio prompt');
  expect(fixture.ensure).not.toHaveBeenCalled();
});

it('keeps dock artifacts separate and sends the existing media command to dedicated Studio', async () => {
  render(<App/>); await screen.findByRole('textbox', {name: 'Agent draft'});
  fireEvent.click(screen.getByRole('button', {name: 'Workspace actions'}));
  fireEvent.click(within(screen.getByRole('dialog', {name: 'Workspace actions'})).getByRole('button', {name: 'Artifacts'}));
  const artifact = await screen.findByRole('textbox', {name: 'Artifact draft'});
  fireEvent.change(artifact, {target: {value: 'Unsaved artifact'}});
  expect(screen.queryByRole('region', {name: 'Studio fixture'})).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', {name: 'Commands'}));
  fireEvent.click(await screen.findByRole('button', {name: 'Media studio & artifacts'}));
  expect(await screen.findByRole('textbox', {name: 'Studio prompt'})).toBeVisible();
  expect(screen.getAllByRole('region', {name: 'Studio fixture', hidden: true})).toHaveLength(1);
  fireEvent.click(mode('Agent'));
  expect(screen.getByRole('textbox', {name: 'Artifact draft'})).toBe(artifact); expect(artifact).toHaveValue('Unsaved artifact');
});

it('does not let a pending automatic main-agent response replace an explicit Studio selection', async () => {
  history.replaceState(null, '', '/');
  let resolve!: (value: unknown) => void;
  fixture.ensure.mockReturnValue(new Promise(done => {resolve = done}));
  render(<App/>); await waitFor(() => expect(fixture.ensure).toHaveBeenCalledOnce());
  fireEvent.click(mode('Studio')); await screen.findByRole('textbox', {name: 'Studio prompt'});
  await act(async () => resolve({session: fixture.session}));
  expect(mode('Studio')).toHaveAttribute('aria-pressed', 'true');
  expect(screen.getByRole('textbox', {name: 'Studio prompt'})).toBeVisible();
});

it('does not let Studio keyboard shortcuts close hidden Agent tabs or expose Agent dock commands', async () => {
  render(<App/>); await screen.findByRole('textbox', {name: 'Agent draft'});
  fireEvent.click(screen.getByRole('button', {name: 'Workspace actions'}));
  fireEvent.click(within(screen.getByRole('dialog', {name: 'Workspace actions'})).getByRole('button', {name: 'Artifacts'}));
  const artifact = await screen.findByRole('textbox', {name: 'Artifact draft'});
  fireEvent.click(mode('Studio')); await screen.findByRole('textbox', {name: 'Studio prompt'});
  fireEvent.keyDown(window, {key: 'w', ctrlKey: true});
  expect(mode('Studio')).toHaveAttribute('aria-pressed', 'true');
  fireEvent.click(mode('Agent'));
  expect(screen.getByRole('textbox', {name: 'Artifact draft'})).toBe(artifact);
  expect(artifact).toBeVisible();
  fireEvent.click(mode('Studio'));
  fireEvent.click(screen.getByRole('button', {name: 'Commands'}));
  const commands = await screen.findByRole('dialog', {name: 'Workspace commands'});
  expect(within(commands).queryByRole('button', {name: /^Saved layout presets/})).not.toBeInTheDocument();
  expect(within(commands).queryByRole('button', {name: /^Close current tab/})).not.toBeInTheDocument();
  expect(within(commands).queryByRole('button', {name: /^Toggle side panel/})).not.toBeInTheDocument();
  expect(within(commands).getByRole('button', {name: /^Workbench/})).toBeVisible();
});

it('uses Studio project context and omits unsupported Agent detach actions', async () => {
  render(<App/>); await screen.findByRole('textbox', {name: 'Agent draft'});
  fireEvent.click(mode('Studio')); await screen.findByRole('textbox', {name: 'Studio prompt'});
  fireEvent.click(within(screen.getByRole('navigation', {name: 'Studio projects'})).getByRole('button', {name: 'No project'}));
  await waitFor(()=>expect(document.querySelector('.status-context')).toHaveTextContent('No project'));
  expect(document.querySelector('.status-context')).not.toHaveAttribute('title');
  fireEvent.click(screen.getByRole('button', {name: 'Workspace actions'}));
  const menu = screen.getByRole('dialog', {name: 'Workspace actions'});
  expect(within(menu).queryByRole('button', {name: 'Detach workspace'})).not.toBeInTheDocument();
  expect(within(menu).queryByRole('button', {name: 'Toggle side panel'})).not.toBeInTheDocument();
});

it.each(['Saved layout presets', 'New chat with options'])('closes the Agent-only %s dialog when history selects Studio', async name => {
  render(<App/>); await screen.findByRole('textbox', {name: 'Agent draft'});
  if (name === 'Saved layout presets') {
    fireEvent.click(screen.getByRole('button', {name: 'Commands'}));
    fireEvent.click(await screen.findByRole('button', {name: /^Saved layout presets/}));
  } else {fireEvent.click(screen.getByRole('button',{name:'Workspace actions'}));fireEvent.click(within(screen.getByRole('dialog',{name:'Workspace actions'})).getByRole('button',{name:'New chat with options'}));}
  expect(await screen.findByRole('dialog', {name})).toBeVisible();
  await act(async () => {history.replaceState(null, '', '/?workspace=studio&session=conversation'); window.dispatchEvent(new PopStateEvent('popstate'))});
  await waitFor(() => expect(screen.queryByRole('dialog', {name})).not.toBeInTheDocument());
  expect(await screen.findByRole('textbox', {name: 'Studio prompt'})).toBeVisible();
  expect(mode('Studio')).toHaveAttribute('aria-pressed', 'true');
});

it('keeps the Studio project independent when a different Agent chat changes execution context', async () => {
  render(<App/>); await screen.findByRole('textbox', {name: 'Agent draft'});
  await waitFor(() => expect(document.querySelector('.status-context')).toHaveTextContent('Project'));
  fireEvent.click(mode('Studio'));
  const prompt = await screen.findByRole('textbox', {name: 'Studio prompt'});
  expect(screen.getByRole('region', {name: 'Studio fixture'})).toHaveAttribute('data-project', 'project');
  fireEvent.change(prompt, {target: {value: 'Keep project A composition'}});
  fireEvent.click(mode('Agent'));
  fireEvent.click(screen.getByRole('button', {name: 'Open other-project chat'}));
  await waitFor(() => expect(document.querySelector('.status-context')).toHaveTextContent('Other project'));
  fireEvent.click(mode('Studio'));
  expect(screen.getByRole('region', {name: 'Studio fixture'})).toHaveAttribute('data-project', 'project');
  expect(screen.getByRole('textbox', {name: 'Studio prompt'})).toBe(prompt);
  expect(prompt).toHaveValue('Keep project A composition');
});

it('controls Create, Library and Saved assets from a slim Studio sidebar without changing Agent geometry', async () => {
  render(<App/>); await screen.findByRole('textbox', {name: 'Agent draft'});
  const agentSidebar = screen.getByRole('complementary', {name: 'Chats and agents'});
  const width = agentSidebar.style.width;
  fireEvent.click(mode('Studio')); await screen.findByRole('textbox', {name: 'Studio prompt'});
  expect(screen.getByRole('complementary', {name: 'Studio navigation'})).toHaveStyle({width: '240px'});
  const navigation = screen.getByRole('navigation', {name: 'Studio views'});
  fireEvent.click(within(navigation).getByRole('button', {name: 'Library'}));
  expect(within(navigation).getByRole('button', {name: 'Library'})).toHaveAttribute('aria-current', 'page');
  expect(screen.getByRole('region', {name: 'Studio fixture'})).toHaveAttribute('data-section', 'library');
  fireEvent.click(within(navigation).getByRole('button', {name: 'Saved assets'}));
  expect(screen.getByRole('region', {name: 'Studio fixture'})).toHaveAttribute('data-section', 'assets');
  fireEvent.click(mode('Agent'));
  expect(screen.getByRole('complementary', {name: 'Chats and agents'})).toHaveStyle({width});
  fireEvent.click(mode('Studio'));
  expect(screen.getByRole('region', {name: 'Studio fixture'})).toHaveAttribute('data-section', 'assets');
});

it('preserves the expanded Agent panel when Escape belongs to Studio', async () => {
  render(<App/>); await screen.findByRole('textbox', {name: 'Agent draft'});
  fireEvent.click(screen.getByRole('button', {name: 'Workspace actions'}));
  fireEvent.click(within(screen.getByRole('dialog', {name: 'Workspace actions'})).getByRole('button', {name: 'Files & code'}));
  await screen.findByRole('textbox', {name: 'Unsaved editor buffer'});
  fireEvent.click(screen.getByRole('button', {name: 'Expand Files panel'}));
  expect(document.querySelector('.dock-workspace')).toHaveClass('dock-full-view');
  fireEvent.click(mode('Studio')); await screen.findByRole('textbox', {name: 'Studio prompt'});
  fireEvent.keyDown(window, {key: 'Escape'});
  fireEvent.click(mode('Agent'));
  expect(document.querySelector('.dock-workspace')).toHaveClass('dock-full-view');
});

it('keeps Studio media in its project when the selected Agent conversation has a different scope', async () => {
  render(<App/>); await screen.findByRole('textbox', {name: 'Agent draft'});
  fireEvent.click(mode('Studio')); await screen.findByRole('textbox', {name: 'Studio prompt'});
  fireEvent.click(within(screen.getByRole('navigation', {name: 'Studio projects'})).getByRole('button', {name: 'Other project'}));
  await waitFor(()=>expect(document.querySelector('.status-context')).toHaveTextContent('Other project'));
  fireEvent.click(screen.getByRole('button', {name: 'Attach Studio media'}));
  expect(screen.getByRole('alert')).toHaveTextContent('Open an Agent conversation in Other project before attaching Studio media.');
  expect(mode('Studio')).toHaveAttribute('aria-pressed', 'true');
  expect(screen.getByRole('textbox', {name: 'Studio prompt'})).toBeVisible();
});

it('returns to the selected Agent conversation for media from the same Studio project', async () => {
  render(<App/>); await screen.findByRole('textbox', {name: 'Agent draft'});
  await waitFor(() => expect(document.querySelector('.status-context')).toHaveTextContent('Project'));
  fireEvent.click(mode('Studio')); await screen.findByRole('textbox', {name: 'Studio prompt'});
  fireEvent.click(screen.getByRole('button', {name: 'Attach Studio media'}));
  expect(mode('Agent')).toHaveAttribute('aria-pressed', 'true');
  expect(screen.getByRole('textbox', {name: 'Agent draft'})).toBeVisible();
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
});

it('keeps Studio visually rendered through real window blur and document hiding while observation pauses', async () => {
  fixture.realObservation = true;
  vi.spyOn(document, 'hasFocus').mockReturnValue(true);
  const hidden = vi.spyOn(document, 'hidden', 'get').mockReturnValue(false);
  history.replaceState(null, '', '/?workspace=studio');
  render(<App/>);
  const prompt = await screen.findByRole('textbox', {name: 'Studio prompt'});
  fireEvent.change(prompt, {target: {value: 'Keep visible while window is unfocused'}});
  const studio = screen.getByRole('region', {name: 'Studio fixture'});
  expect(studio).toHaveAttribute('data-observing', 'true');
  fireEvent.blur(window);
  expect(studio).toHaveAttribute('data-observing', 'false');
  expect(studio).toHaveAttribute('data-active', 'true');
  expect(prompt).toBeVisible(); expect(prompt).toHaveValue('Keep visible while window is unfocused');
  expect(document.getElementById('studio-workspace')).not.toHaveAttribute('hidden');
  hidden.mockReturnValue(true); fireEvent(document, new Event('visibilitychange'));
  expect(prompt).toBeVisible(); expect(studio).toHaveAttribute('data-active', 'true');
  hidden.mockReturnValue(false); fireEvent.focus(window);
  expect(studio).toHaveAttribute('data-observing', 'true');
  expect(screen.getByRole('textbox', {name: 'Studio prompt'})).toBe(prompt);
  fireEvent.click(mode('Agent'));
  expect(prompt).not.toBeVisible(); expect(studio).toHaveAttribute('data-active', 'false');
});

it('routes Configure provider to Studio Settings while Agent Settings stays independent', async () => {
  render(<App/>); const draft = await screen.findByRole('textbox', {name: 'Agent draft'});
  fireEvent.change(draft, {target: {value: 'Keep Agent draft'}});
  fireEvent.click(within(screen.getByRole('navigation', {name: 'Workspace navigation'})).getByRole('button', {name: 'Settings'}));
  expect(await screen.findByRole('region', {name: 'Settings fixture'})).toBeVisible();
  fireEvent.click(screen.getByRole('button', {name: 'Close settings'}));
  fireEvent.click(mode('Studio'));
  const prompt = await screen.findByRole('textbox', {name: 'Studio prompt'});
  fireEvent.change(prompt, {target: {value: 'Keep Studio draft'}});
  fireEvent.click(screen.getByRole('button', {name: 'Configure provider'}));
  expect(await screen.findByRole('region', {name: 'Studio Settings fixture'})).toBeVisible();
  expect(document.querySelector('.breadcrumb')).toHaveTextContent('Studio Settings');
  fireEvent.click(within(screen.getByRole('navigation', {name: 'Studio views'})).getByRole('button', {name: 'Create'}));
  expect(screen.getByRole('textbox', {name: 'Studio prompt'})).toBe(prompt); expect(prompt).toHaveValue('Keep Studio draft');
  fireEvent.click(mode('Agent'));
  expect(screen.getByRole('textbox', {name: 'Agent draft'})).toBe(draft); expect(draft).toHaveValue('Keep Agent draft');
});

it('keeps keyboard focus in the persistent app rail across workspace changes', async () => {
 render(<App/>);await screen.findByRole('textbox',{name:'Agent draft'});
 const agent=mode('Agent');agent.focus();fireEvent.keyDown(agent,{key:'ArrowDown'});
 await screen.findByRole('textbox',{name:'Studio prompt'});expect(mode('Studio')).toHaveFocus();
 fireEvent.keyDown(mode('Studio'),{key:'ArrowLeft'});
 await screen.findByRole('textbox',{name:'Agent draft'});expect(mode('Agent')).toHaveFocus();
 expect(within(screen.getByRole('navigation',{name:'Workspace navigation'})).getByRole('button',{name:'Projects'})).toBeVisible();
});

it('auto-opens one retained subagent target once and honors a user close across updates and remounts', async () => {
 let view=render(<App/>);await screen.findByRole('textbox',{name:'Agent draft'});
 const target=fixture.coordinationTarget;expect(target).not.toBeNull();expect(target?.isConnected).toBe(true);
 expect(screen.queryByRole('complementary',{name:'Subagents'})).not.toBeInTheDocument();
 const summary={hasContent:true,loading:false,active:1,attention:0,done:0,plans:0,stale:false};
 act(()=>fixture.coordinationUpdate?.(summary));const aside=screen.getByRole('complementary',{name:'Subagents'});
 expect(aside).toBeVisible();expect(fixture.coordinationTarget).toBe(target);
 const close=within(aside).getByRole('button',{name:'Close subagents'});close.focus();fireEvent.click(close);
 expect(aside).toHaveAttribute('inert');expect(screen.getByRole('button',{name:'Subagents'})).toHaveFocus();
 act(()=>fixture.coordinationUpdate?.({...summary,active:2}));expect(screen.queryByRole('complementary',{name:'Subagents'})).not.toBeInTheDocument();
 expect(fixture.coordinationTarget).toBe(target);expect(localStorage.getItem('termx-coordination-sidebar:'+fixture.owner)).toBe('closed');
 view.unmount();view=render(<App/>);await screen.findByRole('textbox',{name:'Agent draft'});
 act(()=>fixture.coordinationUpdate?.(summary));expect(screen.queryByRole('complementary',{name:'Subagents'})).not.toBeInTheDocument();
 fireEvent.click(screen.getByRole('button',{name:'Subagents'}));expect(screen.getByRole('complementary',{name:'Subagents'})).toBeVisible();
});

it('does not publish the previous conversation roots during a delayed history selection', async () => {
 const first={...fixture.session,turns:[{id:'turn-a',sequence:1,prompt:'First fixture run',task:{id:'task-a',status:'completed',prompt:'First fixture run'}}]} as Session;
 const second={...fixture.otherSession,turns:[{id:'turn-b',sequence:1,prompt:'Second fixture run',task:{id:'task-b',status:'running',prompt:'Second fixture run'}}]} as Session;
 let release!: (session:Session)=>void;const delayed=new Promise<Session>(resolve=>{release=resolve});
 const read=vi.spyOn(SessionCache.prototype,'read').mockImplementation(id=>id==='other'?delayed:Promise.resolve(first));
 render(<App/>);await screen.findByRole('textbox',{name:'Agent draft'});
 await waitFor(()=>expect(fixture.coordinationTasks.map(task=>task.id)).toEqual(['task-a']));
 await act(async()=>{history.replaceState(null,'','/session/other');window.dispatchEvent(new PopStateEvent('popstate'))});
 await waitFor(()=>expect(read).toHaveBeenCalledWith('other'));
 expect(fixture.coordinationTasks).toEqual([]);
 await act(async()=>release(second));
 await waitFor(()=>expect(fixture.coordinationTasks.map(task=>task.id)).toEqual(['task-b']));
});


vi.mock('./features/data/DataStudio', () => ({default: ({visible,locked,observing}:{visible:boolean;locked:boolean;observing:boolean}) => <section aria-label="Data fixture" data-active={visible&&!locked} data-observing={observing}><textarea aria-label="Data draft" defaultValue=""/></section>}));
it('opens Data from the shared rail without exposing Agent navigation or losing retained drafts', async () => {
 render(<App/>);const agent=await screen.findByRole('textbox',{name:'Agent draft'});
 fireEvent.change(agent,{target:{value:'Keep the agent task'}});
 fireEvent.click(mode('Data'));const data=await screen.findByRole('textbox',{name:'Data draft'});
 expect(document.getElementById('data-workspace')).not.toHaveAttribute('hidden');
 expect(document.getElementById('agent-workspace')).toHaveAttribute('hidden');
 expect(screen.queryByRole('button',{name:'Show sidebar'})).not.toBeInTheDocument();
 expect(screen.queryByRole('button',{name:'Hide sidebar'})).not.toBeInTheDocument();
 fireEvent.change(data,{target:{value:'SELECT * FROM revenue'}});
 fireEvent.blur(window);
 expect(screen.getByRole('region',{name:'Data fixture'})).toHaveAttribute('data-observing','true');
 expect(data).toHaveValue('SELECT * FROM revenue');
 fireEvent.click(mode('Agent'));expect(agent).toHaveValue('Keep the agent task');expect(data).not.toBeVisible();
 fireEvent.click(mode('Data'));expect(screen.getByRole('textbox',{name:'Data draft'})).toHaveValue('SELECT * FROM revenue');
 expect(new URLSearchParams(location.search).get('workspace')).toBe('data');
});
it('opens a Data deep link directly and keeps native root routing compatible', async () => {
 history.replaceState(null,'','/?workspace=data&data_workspace=team&data_view=bi');
 render(<App/>);await screen.findByRole('textbox',{name:'Data draft'});
 expect(mode('Data')).toHaveAttribute('aria-pressed','true');
 expect(document.getElementById('data-workspace')).not.toHaveAttribute('hidden');
 expect(listSessions).not.toHaveBeenCalled();
 expect(useBotsNeedYouCount).toHaveBeenLastCalledWith(false);
 fireEvent.click(mode('Agent'));
 await waitFor(()=>expect(listSessions).toHaveBeenCalled());
 expect(useBotsNeedYouCount).toHaveBeenLastCalledWith(true);
});
