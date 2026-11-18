import {EditorView} from '@codemirror/view';
import {act, cleanup, fireEvent, render, screen, waitFor, within} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {useReducer, type ReactNode} from 'react';
import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest';
import TabGroups from '../../components/shell/TabGroups';
import {allTabs, defaultLayout, editorGroups, findTab, newGroup, newTab, type Tab} from '../../lib/layout/model';
import {createState, reduceLayout, type LayoutAction, type LayoutState} from '../../lib/layout/reduce';
import {snapshotBuffers} from '../../lib/window-state';
import {snapshotWindow} from '../../lib/redock-state';
import {expectNoAxeViolations} from '../../test-axe';
import {BufferProvider, useBuffers} from './buffer-store';
import {bufferKey} from './file-io';
import FileTab from './FileTab';
import {requestFileLine} from './file-navigation';
import {requestSaveFile} from './save-request';

const request = vi.hoisted(() => vi.fn());
const gql = vi.hoisted(() => vi.fn());
const report = vi.hoisted(() => vi.fn());
const fixture = vi.hoisted(() => ({
  session: {id: 'chat', cwd: '/work', project_id: 'p1', worktree_id: null as string | null, runner_id: null as string | null},
}));
vi.mock('../../lib/api', async original => ({...(await original<typeof import('../../lib/api')>()), request, gql}));
vi.mock('../../shell/WorkspaceDataProvider', () => ({useWorkspaceData: () => ({session: fixture.session})}));
vi.mock('../../shell/errors', () => ({useReport: () => report}));
vi.mock('../../lib/lsp', () => ({LanguageClient: class {async open() {} async change() {} close() {}}, fileUri: () => '', positionAt: () => ({line: 0, character: 0}), relativeFileUri: () => null}));
// jsdom has no IndexedDB: nothing is restored or persisted, but publishing for a handoff stays real.
vi.mock('../../lib/window-state', async original => ({
  ...(await original<typeof import('../../lib/window-state')>()),
  restoreBuffers: async () => [],
  persistBuffers: async () => {},
}));

// The published buffer snapshot lives at module level, so each test is its own principal.
let OWNER = 'owner';
let owners = 0;
const project = {id: 'p1', name: 'Project', path: '/work'};
const target = {project, session: fixture.session as never};
const KEY_A = bufferKey(target, 'notes/a.md');
const KEY_B = bufferKey(target, 'notes/b.md');
const files: Record<string, {content: string; editable: boolean}> = {
  'notes/a.md': {content: '# Alpha\n', editable: true},
  'notes/b.md': {content: '# Beta\n', editable: false},
};

beforeEach(() => {
  OWNER = 'owner-' + ++owners;
  request.mockReset();
  gql.mockReset();
  report.mockReset();
  request.mockImplementation(async (path: string, init?: {method?: string; body?: string}) => {
    if (init?.method === 'PUT') return {revision: 'v2'};
    const params = new URLSearchParams(path.split('?')[1]);
    const file = files[params.get('path') ?? ''];
    return {path: params.get('path'), content: file.content, revision: 'v1', editable: file.editable};
  });
});
afterEach(() => {
  cleanup();
  delete document.documentElement.dataset.theme;
});

const editorOf = (node: ParentNode) => EditorView.findFromDOM(node.querySelector<HTMLElement>('.cm-editor')!)!;

function Dirty({tabs}: {tabs: readonly Tab[]}) {
  const store = useBuffers();
  return <>{tabs.map(tab => (tab.ref && store.get(tab.ref)?.dirty ? <i key={tab.id} data-testid="dirty" data-key={tab.ref} /> : null))}</>;
}

type Harness = {current: LayoutState};

function Host({initial, probe, children}: {initial: LayoutState; probe: Harness; children?: ReactNode}) {
  const [state, dispatch] = useReducer((value: LayoutState, action: LayoutAction) => reduceLayout(value, action), initial);
  probe.current = state;
  const tabs = allTabs(state.book.current);
  return (
    <BufferProvider ownerId={OWNER} session={fixture.session as never} onError={report}>
      <TabGroups
        layout={state.book.current}
        dispatch={dispatch}
        size={{width: 1000, height: 600}}
        phone={false}
        titleOf={tab => (tab.ref ? tab.ref.split('/').at(-1)! : tab.kind)}
        render={(tab, {visible}) => <FileTab fileKey={tab.ref} visible={visible} project={project} onFindFile={() => {}} onOpenFile={() => {}} onClose={() => dispatch({type: 'closeTab', tab: tab.id})} />}
      />
      <Dirty tabs={tabs} />
      {children}
    </BufferProvider>
  );
}

function stateWith(...refs: string[]): LayoutState {
  const base = defaultLayout();
  const group = newGroup(refs.map(ref => newTab('file', ref)));
  return createState({version: 2, current: {...base, root: group, focus: group.id}, named: []});
}

describe('FileTab', () => {
  it('reads its file when the tab is restored, and shows it in one CodeMirror editor with its path', async () => {
    const probe: Harness = {current: stateWith(KEY_A)};
    const {container} = render(<Host initial={probe.current} probe={probe} />);
    await waitFor(() => expect(container.querySelector('.cm-content')).toHaveTextContent('# Alpha'));
    expect(screen.getByRole('navigation', {name: 'File path'})).toHaveTextContent('notesa.md');
    expect(container.querySelectorAll('.cm-editor')).toHaveLength(1);
    expect(request).toHaveBeenCalledWith(expect.stringContaining('operation=read'));
    await expectNoAxeViolations(container);
  });

  it('shows a file that belongs to another checkout as one sentence with one action', async () => {
    const probe: Harness = {current: stateWith('p1:/elsewhere:notes/a.md')};
    render(<Host initial={probe.current} probe={probe} />);
    expect(await screen.findByRole('alert')).toHaveTextContent('opened in another conversation or checkout');
    expect(screen.getByRole('button', {name: 'Close tab'})).toBeInTheDocument();
    expect(request).not.toHaveBeenCalled();
  });

  it('shows an empty Editor tab as one sentence and one action', () => {
    const probe: Harness = {current: stateWith()};
    const base = probe.current.book.current;
    const loose = newGroup([newTab('file')]);
    probe.current = createState({version: 2, current: {...base, root: loose, focus: loose.id}, named: []});
    render(<Host initial={probe.current} probe={probe} />);
    expect(screen.getByText('Open a file to start editing.')).toBeInTheDocument();
    expect(screen.getByRole('button', {name: 'Find a file'})).toBeInTheDocument();
  });

  it('marks a file dirty when it is edited and saves it with Mod+S, then clears the mark', async () => {
    const probe: Harness = {current: stateWith(KEY_A)};
    const {container} = render(<Host initial={probe.current} probe={probe} />);
    await waitFor(() => expect(container.querySelector('.cm-content')).toHaveTextContent('# Alpha'));
    const view = editorOf(container);
    await act(async () => view.dispatch({changes: {from: view.state.doc.length, insert: 'more\n'}}));
    expect(screen.getByTestId('dirty')).toBeInTheDocument();
    expect(screen.getByRole('button', {name: 'Save file'})).toBeEnabled();
    view.contentDOM.focus();
    fireEvent.keyDown(view.contentDOM, {key: 's', ctrlKey: true});
    await waitFor(() => expect(screen.queryByTestId('dirty')).not.toBeInTheDocument());
    const put = request.mock.calls.find(call => call[1]?.method === 'PUT')!;
    expect(String(put[0])).toContain('/api/workspace/sessions/chat/files');
    expect(JSON.parse(put[1].body)).toMatchObject({path: 'notes/a.md', content: '# Alpha\nmore\n', revision: 'v1'});
    expect(screen.getByRole('button', {name: 'Save file'})).toBeDisabled();
  });

  it('saves the visible dirty file when the native menu asks for File > Save', async () => {
    const probe: Harness = {current: stateWith(KEY_A)};
    const {container} = render(<Host initial={probe.current} probe={probe} />);
    await waitFor(() => expect(container.querySelector('.cm-content')).toHaveTextContent('# Alpha'));
    const view = editorOf(container);
    await act(async () => view.dispatch({changes: {from: view.state.doc.length, insert: 'menu\n'}}));
    expect(screen.getByTestId('dirty')).toBeInTheDocument();
    await act(async () => requestSaveFile());
    await waitFor(() => expect(screen.queryByTestId('dirty')).not.toBeInTheDocument());
    const put = request.mock.calls.find(call => call[1]?.method === 'PUT')!;
    expect(JSON.parse(put[1].body)).toMatchObject({path: 'notes/a.md', content: '# Alpha\nmenu\n'});
  });

  it('is read only when the host says so', async () => {
    const probe: Harness = {current: stateWith(KEY_B)};
    const {container} = render(<Host initial={probe.current} probe={probe} />);
    await waitFor(() => expect(container.querySelector('.cm-content')).toHaveTextContent('# Beta'));
    expect(screen.getByRole('navigation', {name: 'File path'})).toHaveTextContent('Read only');
    expect(container.querySelector('.cm-content')).toHaveAttribute('contenteditable', 'false');
  });

  it('keeps the editor, its content and its dirty mark when the tab splits to another group and moves back', async () => {
    const probe: Harness = {current: stateWith(KEY_A, KEY_B)};
    const {container} = render(<Host initial={probe.current} probe={probe} />);
    await waitFor(() => expect(container.querySelector('.cm-content')).toHaveTextContent('# Alpha'));
    const view = editorOf(container);
    const dom = view.dom;
    await act(async () => view.dispatch({changes: {from: 0, insert: 'edited '}}));
    expect(screen.getByTestId('dirty')).toHaveAttribute('data-key', KEY_A);
    const user = userEvent.setup();

    // Split: the active tab moves to a new group on the right.
    await user.pointer({keys: '[MouseRight]', target: screen.getByRole('tab', {name: /a\.md/})});
    await user.click(await screen.findByRole('menuitem', {name: 'Split right'}));
    expect(screen.getAllByRole('tablist')).toHaveLength(2);
    expect(editorGroups(probe.current.book.current.root)).toHaveLength(2);
    expect(screen.getByTestId('dirty')).toBeInTheDocument();
    expect(editorOf(document.body).dom).toBe(dom);
    expect(editorOf(document.body).state.doc.toString()).toBe('edited # Alpha\n');

    // And back to the first group.
    const [left, right] = editorGroups(probe.current.book.current.root);
    const moving = right.tabs.find(tab => tab.ref === KEY_A)!;
    await act(async () => {});
    await user.pointer({keys: '[MouseRight]', target: screen.getByRole('tab', {name: /a\.md/})});
    await user.click(await screen.findByRole('menuitem', {name: 'Move to other group'}));
    expect(findTab(probe.current.book.current, moving.id)!.group.id).toBe(left.id);
    expect(editorOf(document.body).dom).toBe(dom);
    expect(screen.getByTestId('dirty')).toBeInTheDocument();
    expect(editorGroups(probe.current.book.current.root)).toHaveLength(1);
  });

  it('carries every open buffer in the unchanged window-handoff payload', async () => {
    const probe: Harness = {current: stateWith(KEY_A)};
    const {container} = render(<Host initial={probe.current} probe={probe} />);
    await waitFor(() => expect(container.querySelector('.cm-content')).toHaveTextContent('# Alpha'));
    const view = editorOf(container);
    await act(async () => view.dispatch({changes: {from: 0, insert: 'x'}}));
    await waitFor(() => expect(snapshotBuffers(OWNER)[0]?.dirty).toBe(true));
    const state = snapshotWindow(OWNER, 'chat');
    expect(Object.keys(state).sort()).toEqual(['artifacts', 'buffers', 'draft', 'sessionId', 'terminalId', 'version'].filter(key => key in state));
    expect(state.version).toBe(1);
    expect(state.buffers).toHaveLength(1);
    expect(Object.keys(state.buffers[0]).sort()).toEqual(['content', 'dirty', 'editable', 'key', 'path', 'project_id', 'revision', 'root', 'workspaceSession', 'worktree']);
    expect(state.buffers[0]).toMatchObject({key: KEY_A, path: 'notes/a.md', content: 'x# Alpha\n', dirty: true, editable: true, project_id: 'p1', workspaceSession: 'chat'});
  });

  it('opens at a requested line, whether the request came before or after the tab', async () => {
    const probe: Harness = {current: stateWith(KEY_A)};
    requestFileLine(KEY_A, 1);
    const {container} = render(<Host initial={probe.current} probe={probe} />);
    await waitFor(() => expect(container.querySelector('.cm-content')).toHaveTextContent('# Alpha'));
    await waitFor(() => expect(editorOf(container).hasFocus).toBe(true));
    const view = editorOf(container);
    await act(async () => view.dispatch({changes: {from: view.state.doc.length, insert: 'second\nthird\n'}}));
    requestFileLine(KEY_A, 3);
    await waitFor(() => expect(view.state.doc.lineAt(view.state.selection.main.head).number).toBe(3));
  });

  it('reports the caret as line, column and language to the status bar', async () => {
    const probe: Harness = {current: stateWith(KEY_A)};
    function Cursor() {
      const store = useBuffers();
      const cursor = store.cursors[KEY_A];
      return <output aria-label="cursor">{cursor ? cursor.line + ':' + cursor.column + ':' + cursor.language : ''}</output>;
    }
    const {container} = render(
      <Host initial={probe.current} probe={probe}>
        <Cursor />
      </Host>,
    );
    await waitFor(() => expect(container.querySelector('.cm-content')).toHaveTextContent('# Alpha'));
    await waitFor(() => expect(within(container).getByLabelText('cursor').textContent).toMatch(/^1:1:/));
  });
});

describe('two buffers, one window', () => {
  it('keeps unrelated tabs untouched when one file is edited', async () => {
    const probe: Harness = {current: stateWith(KEY_A, KEY_B)};
    const {container} = render(<Host initial={probe.current} probe={probe} />);
    await waitFor(() => expect(container.querySelector('.cm-content')).toHaveTextContent('# Alpha'));
    const user = userEvent.setup();
    await user.click(screen.getByRole('tab', {name: /b\.md/}));
    await waitFor(() => expect(container.querySelectorAll('.cm-editor').length).toBe(2));
    expect(screen.queryByTestId('dirty')).not.toBeInTheDocument();
  });
});

