import {afterEach, describe, expect, it, vi} from 'vitest';
import {EditorView} from '@codemirror/view';
import {foldable, foldAll, foldedRanges, language} from '@codemirror/language';
import {searchPanelOpen} from '@codemirror/search';
import {applyLanguage, createEditorState, cursorBlinkRate, openReplace} from './extensions';

const source = 'function greet(name) {\n  if (name) {\n    return "hi " + name;\n  }\n  return "hello";\n}\n';

const views: EditorView[] = [];
function mount(doc = source, path = 'greet.ts', overrides: Partial<Parameters<typeof createEditorState>[0]> = {}) {
  const parent = document.body.appendChild(document.createElement('div'));
  const view = new EditorView({parent, state: createEditorState({doc, path, label: 'Editor for ' + path, onSave: () => {}, onDocChange: () => {}, ...overrides})});
  views.push(view);
  return view;
}
afterEach(() => {
  for (const view of views.splice(0)) { view.dom.parentElement?.remove(); view.destroy(); }
  vi.unstubAllGlobals();
});

describe('editor extensions', () => {
  it('shows line numbers, a fold gutter and the active line', () => {
    const view = mount();
    expect(view.dom.querySelector('.cm-lineNumbers')).not.toBeNull();
    expect(view.dom.querySelector('.cm-foldGutter')).not.toBeNull();
    expect(view.dom.querySelector('.cm-activeLine')).not.toBeNull();
    expect(view.dom.querySelector('.cm-activeLineGutter')).not.toBeNull();
  });

  it('loads the grammar by extension after the editor exists, without recreating it', async () => {
    const view = mount(source, 'greet.ts');
    expect(view.state.facet(language)).toBeNull();
    await applyLanguage(view, 'greet.ts');
    expect(view.state.facet(language)?.name).toBe('typescript');
    expect(views).toHaveLength(1);
  });

  it('leaves an unknown extension as plain text', async () => {
    const view = mount('plain words', 'notes.unknownext');
    await applyLanguage(view, 'notes.unknownext');
    expect(view.state.facet(language)).toBeNull();
  });

  it('ignores a grammar that arrives after the view moved to another buffer', async () => {
    const view = mount('fn main() {}\n', 'moved.rs');
    await applyLanguage(view, 'moved.rs', () => false);
    expect(view.state.facet(language)).toBeNull();
  });

  it('folds a block and shows the fold placeholder', async () => {
    const view = mount(source, 'greet.ts');
    await applyLanguage(view, 'greet.ts');
    expect(foldable(view.state, 0, view.state.doc.line(1).to)).not.toBeNull();
    foldAll(view);
    expect(foldedRanges(view.state).size).toBeGreaterThan(0);
    expect(view.dom.querySelector('.cm-foldPlaceholder')).not.toBeNull();
  });

  it('opens find on Mod-f and focuses replace on Mod-Alt-f', () => {
    const view = mount();
    view.contentDOM.focus();
    view.contentDOM.dispatchEvent(new KeyboardEvent('keydown', {key: 'f', ctrlKey: true, metaKey: false, bubbles: true, cancelable: true}));
    expect(searchPanelOpen(view.state)).toBe(true);
    expect(view.dom.querySelector('.cm-search input[name=search]')).not.toBeNull();
    expect(view.dom.querySelector('.cm-search input[name=replace]')).not.toBeNull();
    openReplace(view);
    expect(document.activeElement).toBe(view.dom.querySelector('.cm-search input[name=replace]'));
  });

  it('marks matching brackets', () => {
    const view = mount('call(a, b)\n', 'a.ts', {});
    view.dispatch({selection: {anchor: 5}});
    expect(view.dom.querySelectorAll('.cm-matchingBracket').length).toBe(2);
  });

  it('runs save and reports document changes', () => {
    const onSave = vi.fn();
    const onDocChange = vi.fn();
    const view = mount('x', 'a.ts', {onSave, onDocChange});
    view.contentDOM.dispatchEvent(new KeyboardEvent('keydown', {key: 's', ctrlKey: true, bubbles: true, cancelable: true}));
    expect(onSave).toHaveBeenCalledOnce();
    view.dispatch({changes: {from: 0, insert: 'y'}});
    expect(onDocChange).toHaveBeenCalledOnce();
  });

  it('is read only when the buffer is not editable', () => {
    const view = mount('x', 'a.ts', {editable: false});
    expect(view.state.readOnly).toBe(true);
    expect(view.contentDOM.getAttribute('contenteditable')).toBe('false');
  });

  it('stops the caret blinking under prefers-reduced-motion', () => {
    expect(cursorBlinkRate()).toBe(1200);
    vi.stubGlobal('matchMedia', (query: string) => ({matches: query.includes('prefers-reduced-motion'), media: query, addEventListener() {}, removeEventListener() {}}));
    expect(cursorBlinkRate()).toBe(0);
  });
});
