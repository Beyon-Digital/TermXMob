import {afterEach, expect, it, vi} from 'vitest';
import {cleanup, fireEvent, render, renderHook, screen, within} from '@testing-library/react';
import {useState} from 'react';
import WorkspaceModeSwitch from './components/WorkspaceModeSwitch';
import {readWorkspaceMode, readStudioSection, workspaceHistoryTarget, type WorkspaceMode} from './lib/workspace-mode';
import {useWorkspaceHistory,useWorkspaceThemeScope,sessionListPreview} from './App';
import {nativeWorkspace} from './lib/native';

vi.mock('./lib/native', async original => ({...await original<typeof import('./lib/native')>(), nativeWorkspace: vi.fn()}));
afterEach(() => {cleanup(); history.replaceState(null, '', '/'); vi.clearAllMocks()});

it('provides named, selected workspaces with keyboard focus and one tab stop', () => {
  function Fixture() {const [value, setValue] = useState<WorkspaceMode>('agent'); return <WorkspaceModeSwitch value={value} onChange={setValue}/>}
  render(<Fixture/>);
  const group = screen.getByRole('group', {name: 'Workspace'});
  const agent = within(group).getByRole('button', {name: 'Agent'}), studio = within(group).getByRole('button', {name: 'Studio'}), data = within(group).getByRole('button', {name: 'Data'});
  expect(agent).toHaveAttribute('aria-pressed', 'true');
  expect(studio).toHaveAttribute('tabindex', '-1');
  agent.focus(); fireEvent.keyDown(agent, {key: 'ArrowRight'});
  expect(studio).toHaveFocus(); expect(studio).toHaveAttribute('aria-pressed', 'true'); expect(agent).toHaveAttribute('tabindex', '-1');
  fireEvent.keyDown(studio, {key: 'Home'});
  expect(agent).toHaveFocus(); expect(agent).toHaveAttribute('aria-pressed', 'true');
  fireEvent.keyDown(agent, {key: 'End'});
  expect(data).toHaveFocus(); expect(data).toHaveAttribute('aria-controls', 'data-workspace');
  fireEvent.keyDown(data, {key: 'ArrowLeft'});
  expect(studio).toHaveFocus();
  fireEvent.keyDown(studio, {key: 'ArrowLeft'});
  expect(agent).toHaveFocus(); expect(agent).toHaveAttribute('aria-controls', 'agent-workspace');
});

it('keeps existing session/manage routes and accepts Studio and legacy media links', () => {
  expect(readWorkspaceMode('/', '?workspace=studio&session=child')).toBe('studio');
  expect(readWorkspaceMode('/manage/media', '')).toBe('studio');
  expect(readWorkspaceMode('/session/child', '?workspace=unknown')).toBe('agent');
  expect(workspaceHistoryTarget('', 'child with space', 'agent')).toBe('/session/child%20with%20space');
  expect(workspaceHistoryTarget('preferences', '', 'agent')).toBe('/manage/preferences');
  expect(workspaceHistoryTarget('', 'child with space', 'studio')).toBe('/?workspace=studio&session=child+with+space');
  expect(workspaceHistoryTarget('preferences', 'child', 'studio')).toBe('/manage/preferences?workspace=studio&session=child');
});

it('retains detached-window context when navigating between browser workspaces', () => {
  const context = '?layout=artifacts&detached=true&parent-window=parent&handoff-token=fixture';
  const studio = workspaceHistoryTarget('', 'child', 'studio', context);
  const params = new URL(studio, location.origin).searchParams;
  expect(params.get('detached')).toBe('true'); expect(params.get('parent-window')).toBe('parent'); expect(params.get('handoff-token')).toBe('fixture');
  const agent = workspaceHistoryTarget('', 'child', 'agent', new URL(studio, location.origin).search);
  expect(agent).toBe('/session/child' + context);
});

it('preserves an explicitly opened project across utility and Studio routes without replacing session context', () => {
  expect(workspaceHistoryTarget('', '', 'agent', '?detached=true', 'create', 'project with space')).toBe('/?detached=true&project=project+with+space');
  expect(workspaceHistoryTarget('projects', '', 'agent', '', 'create', 'project')).toBe('/manage/projects?project=project');
  expect(workspaceHistoryTarget('', '', 'studio', '', 'create', 'project')).toBe('/?project=project&workspace=studio');
  expect(workspaceHistoryTarget('', 'child', 'agent', '?project=previous', 'create', 'project')).toBe('/session/child');
});

it('writes project-only browser history while leaving native workspace URLs untouched', () => {
  vi.mocked(nativeWorkspace).mockReturnValue(false);
  const view = renderHook(({project}) => useWorkspaceHistory(true, '', '', 'agent', 'create', project), {initialProps: {project: 'first'}});
  expect(location.pathname + location.search).toBe('/?project=first');
  view.rerender({project: 'second'});
  expect(location.search).toBe('?project=second');
  vi.mocked(nativeWorkspace).mockReturnValue(true);
  history.replaceState(null, '', '/?window=trusted');
  view.rerender({project: 'third'});
  expect(location.pathname + location.search).toBe('/?window=trusted');
});

it('writes browser Studio/settings history while preserving native trusted URLs', () => {
  vi.mocked(nativeWorkspace).mockReturnValue(false);
  const {rerender} = renderHook(({mode, manager}) => useWorkspaceHistory(true, manager, 'child', mode), {initialProps: {mode: 'agent' as WorkspaceMode, manager: ''}});
  expect(location.pathname).toBe('/session/child');
  rerender({mode: 'studio', manager: ''}); expect(location.pathname + location.search).toBe('/?workspace=studio&session=child');
  rerender({mode: 'studio', manager: 'preferences'}); expect(location.pathname + location.search).toBe('/manage/preferences?workspace=studio&session=child');
  rerender({mode: 'agent', manager: ''}); expect(location.pathname + location.search).toBe('/session/child');
  vi.mocked(nativeWorkspace).mockReturnValue(true);
  history.replaceState(null, '', '/?session=child&layout=artifacts&window=workspace-fixture');
  rerender({mode: 'studio', manager: ''});
  expect(location.pathname + location.search).toBe('/?session=child&layout=artifacts&window=workspace-fixture');
});

it('routes Studio sections without leaking the selection into Agent routes', () => {
  expect(readStudioSection('?workspace=studio&studio=settings')).toBe('settings');
  expect(readStudioSection('?studio=library')).toBe('library');
  expect(readStudioSection('?studio=assets')).toBe('assets');
  expect(readStudioSection('?workspace=studio&studio=music')).toBe('music');
  expect(workspaceHistoryTarget('', 'child', 'studio', '', 'music')).toBe('/?workspace=studio&studio=music&session=child');
  expect(readStudioSection('?studio=unknown')).toBe('create');
  expect(workspaceHistoryTarget('', 'child', 'studio', '?detached=true', 'settings')).toBe('/?detached=true&workspace=studio&studio=settings&session=child');
  expect(workspaceHistoryTarget('', 'child', 'agent', '?studio=settings&workspace=studio')).toBe('/session/child');
});

it('scopes portal styling to the active workspace and removes the scope on unmount', () => {
 const view=renderHook(({mode})=>useWorkspaceThemeScope(mode),{initialProps:{mode:'agent' as WorkspaceMode}});
 expect(document.documentElement).toHaveAttribute('data-app-workspace','agent');
 view.rerender({mode:'studio'});expect(document.documentElement).toHaveAttribute('data-app-workspace','studio');
 view.rerender({mode:'agent'});expect(document.documentElement).toHaveAttribute('data-app-workspace','agent');
 view.unmount();expect(document.documentElement).not.toHaveAttribute('data-app-workspace');
});

it('supports vertical keyboard workspace choices with names in the app rail', () => {
 function Fixture(){const [value,setValue]=useState<WorkspaceMode>('agent');return <WorkspaceModeSwitch presentation="rail" value={value} onChange={setValue}/>}
 render(<Fixture/>);const agent=screen.getByRole('button',{name:'Agent'}),studio=screen.getByRole('button',{name:'Studio'});
 expect(agent).toHaveAttribute('title','Agent');agent.focus();fireEvent.keyDown(agent,{key:'ArrowDown'});
 expect(studio).toHaveFocus();expect(studio).toHaveAttribute('aria-pressed','true');
 fireEvent.keyDown(studio,{key:'ArrowUp'});expect(agent).toHaveFocus();expect(agent).toHaveAttribute('tabindex','0');
});
it('shows only truthful available conversation previews and marks unsent drafts', () => {
 expect(sessionListPreview({draft_text:'',turns:undefined})).toBe('');
 expect(sessionListPreview({draft_text:'  Keep\nthis draft  ',turns:[]})).toBe('Draft: Keep this draft');
 expect(sessionListPreview({draft_text:'',turns:[{id:'old',sequence:1,prompt:'Earlier request'},{id:'latest',sequence:2,prompt:'Current request',task:{id:'task',status:'completed',prompt:'Current request',result:'Verified\nresult'}}]})).toBe('Verified result');
 const result='## Local review fixture\n- Reviewed `app.tsx`\n```text\nAll checks passed\n```';
 expect(sessionListPreview({draft_text:'',turns:[{id:'review',sequence:1,prompt:'Review',task:{id:'task',status:'completed',prompt:'Review',result}}]})).toBe('Local review fixture Reviewed app.tsx All checks passed');
 const draft='## Keep my draft';expect(sessionListPreview({draft_text:draft,turns:[]})).toBe('Draft: '+draft);
});


it('routes the independent Data workspace and removes its selection from other workspaces', () => {
 expect(readWorkspaceMode('/', '?workspace=data&data_workspace=one&data_view=bi')).toBe('data');
 expect(readWorkspaceMode('/manage/data', '')).toBe('data');
 const path = workspaceHistoryTarget('', 'agent-chat', 'data', '?data_workspace=one&data_view=bi&data_source=source');
 const url = new URL(path, location.origin);
 expect(url.pathname).toBe('/');expect(url.searchParams.get('workspace')).toBe('data');
 expect(url.searchParams.get('data_workspace')).toBe('one');expect(url.searchParams.get('session')).toBe('agent-chat');
 const back = workspaceHistoryTarget('', 'agent-chat', 'agent', url.search);
 expect(back).toBe('/session/agent-chat');
});
