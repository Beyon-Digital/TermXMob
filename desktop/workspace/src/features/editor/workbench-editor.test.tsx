import {act, cleanup, fireEvent, render, screen, waitFor} from '@testing-library/react';
import {afterEach, expect, it, vi} from 'vitest';
import {EditorView} from '@codemirror/view';
import {foldAll, language} from '@codemirror/language';
import {searchPanelOpen} from '@codemirror/search';
import Workbench from '../Workbench';
import {request, type Session} from '../../lib/api';
import {expectNoAxeViolations} from '../../test-axe';

vi.mock('../../lib/api', () => ({gql: vi.fn(), request: vi.fn(), json: (method: string, data: unknown) => ({method, body: JSON.stringify(data)})}));
vi.mock('../TerminalPanel', () => ({default: () => null}));
vi.mock('../Debugger', () => ({default: () => null}));
vi.mock('../Delivery', () => ({default: () => null}));
vi.mock('../Preview', () => ({default: () => null}));
vi.mock('../../lib/lsp', () => ({LanguageClient: class {async open() {} async change() {} close() {}}, fileUri: () => '', positionAt: () => ({line: 0, character: 0})}));

afterEach(() => { cleanup(); vi.clearAllMocks(); delete document.documentElement.dataset.theme; });

const project = {id: 'project', name: 'Fixture', path: '/fixture'};
const session: Session = {id: 'chat', cwd: '/fixture', project_id: 'project', worktree_id: null, title: 'Chat', engine: 'internal', mode: 'ask', pinned: false, archived: false, draft_text: '', revision: 1, updated_at: 0, scroll: 0};
const files: Record<string, string> = {
  'src/greet.ts': 'export function greet(name: string) {\n  if (name) {\n    return `hi ${name}`;\n  }\n  return "hello";\n}\n',
  'docs/NOTES.md': '# Notes\n\nSome **bold** text.\n',
};

function serve() {
  vi.mocked(request).mockImplementation(async (path: string) => {
    if (path.includes('/delivery')) return {worktrees: []} as never;
    const params = new URLSearchParams(path.split('?')[1]);
    if (params.get('operation') === 'read') return {path: params.get('path'), content: files[params.get('path')!], revision: 'v1', editable: params.get('path') !== 'docs/NOTES.md'} as never;
    return {entries: Object.keys(files).map((file) => ({name: file.split('/').at(-1), path: file, directory: false}))} as never;
  });
}

async function openFile(name: string) {
  fireEvent.click(await screen.findByRole('button', {name}));
}
const editorView = (container: HTMLElement) => EditorView.findFromDOM(container.querySelector<HTMLElement>('.cm-editor')!)!;

it('highlights a TypeScript file by extension and shows its breadcrumb path', async () => {
  serve();
  const {container} = render(<Workbench project={project} session={session} visible bottom={false} onError={() => {}} />);
  await openFile('greet.ts');
  await waitFor(() => expect(container.querySelector('.cm-content')).toHaveTextContent('export function greet'));
  await waitFor(() => expect(editorView(container).state.facet(language)?.name).toBe('typescript'));
  expect(screen.getByRole('navigation', {name: 'File path'})).toHaveTextContent('srcgreet.ts');
  expect(container.querySelector('.cm-lineNumbers')).not.toBeNull();
  expect(container.querySelector('.cm-foldGutter')).not.toBeNull();
});

it('opens find and replace with the keyboard, folds a block and stays accessible', async () => {
  serve();
  const {container} = render(<Workbench project={project} session={session} visible bottom={false} onError={() => {}} />);
  await openFile('greet.ts');
  await waitFor(() => expect(editorView(container).state.facet(language)?.name).toBe('typescript'));
  const view = editorView(container);
  view.contentDOM.focus();
  fireEvent.keyDown(view.contentDOM, {key: 'f', ctrlKey: true});
  await waitFor(() => expect(searchPanelOpen(view.state)).toBe(true));
  expect(container.querySelector('.cm-search input[name=replace]')).not.toBeNull();
  fireEvent.keyDown(view.contentDOM, {key: 'f', ctrlKey: true, altKey: true});
  expect(document.activeElement).toBe(container.querySelector('.cm-search input[name=replace]'));
  act(() => { foldAll(view); });
  expect(container.querySelector('.cm-foldPlaceholder')).not.toBeNull();
  // The editor column: tab strip, breadcrumbs, text area and find panel. The explorer aside is outside this slice.
  await expectNoAxeViolations(container.querySelector('.editor-column')!);
});

it('loads markdown by extension and keeps a read-only file read only', async () => {
  serve();
  const {container} = render(<Workbench project={project} session={session} visible bottom={false} onError={() => {}} />);
  await openFile('NOTES.md');
  await waitFor(() => expect(container.querySelector('.cm-content')).toHaveTextContent('Some **bold** text.'));
  await waitFor(() => expect(editorView(container).state.facet(language)?.name).toBe('markdown'));
  expect(screen.getByRole('navigation', {name: 'File path'})).toHaveTextContent('Read only');
  expect(editorView(container).state.readOnly).toBe(true);
});

it('switches between light and dark without re-creating the editor', async () => {
  serve();
  const {container} = render(<Workbench project={project} session={session} visible bottom={false} onError={() => {}} />);
  await openFile('greet.ts');
  await waitFor(() => expect(editorView(container).state.facet(language)?.name).toBe('typescript'));
  const view = editorView(container);
  const dom = view.dom;
  const state = view.state;
  document.documentElement.dataset.theme = 'dark';
  await act(async () => { await Promise.resolve(); });
  document.documentElement.dataset.theme = 'light';
  await act(async () => { await Promise.resolve(); });
  expect(container.querySelector('.cm-editor')).toBe(dom);
  expect(editorView(container)).toBe(view);
  expect(view.state).toBe(state);
});
