import {Compartment, EditorState, type Extension} from '@codemirror/state';
import {drawSelection, EditorView, highlightActiveLine, highlightActiveLineGutter, keymap, lineNumbers} from '@codemirror/view';
import {defaultKeymap, history, historyKeymap} from '@codemirror/commands';
import {bracketMatching, codeFolding, foldGutter, foldKeymap} from '@codemirror/language';
import {openSearchPanel, search, searchKeymap} from '@codemirror/search';
import {editorTheme} from './editor-theme';
import {cachedLanguage, loadLanguage} from './language';

/** Holds the grammar of one buffer; it is filled in after the grammar loads, without rebuilding the editor. */
export const languageSlot = new Compartment();

export type BufferOptions = {
  doc: string;
  /** Accessible name of the editable region. */
  label: string;
  path: string;
  editable?: boolean;
  onSave: () => void;
  onDocChange: (state: EditorState) => void;
};

const reducedMotion = () => typeof window !== 'undefined' && !!window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;

/** Full caret blink cycle in ms; 0 keeps the caret steady for people who ask the system for less motion. */
export const cursorBlinkRate = () => (reducedMotion() ? 0 : 1200);

/** Opens find and moves focus to the replace field (Mod-Alt-f, which is Option-Command-F on macOS). */
export function openReplace(view: EditorView) {
  openSearchPanel(view);
  const field = view.dom.querySelector<HTMLInputElement>('.cm-search input[name=replace]');
  field?.focus();
  field?.select();
  return true;
}

export function editorExtensions(options: Omit<BufferOptions, 'doc'>): Extension[] {
  const {label, path, editable = true, onSave, onDocChange} = options;
  return [
    EditorView.contentAttributes.of({'aria-label': label}),
    lineNumbers(),
    highlightActiveLineGutter(),
    highlightActiveLine(),
    drawSelection({cursorBlinkRate: cursorBlinkRate()}),
    history(),
    codeFolding(),
    foldGutter(),
    bracketMatching(),
    search({top: true}),
    keymap.of([
      {key: 'Mod-s', run: () => { onSave(); return true; }},
      {key: 'Mod-Alt-f', run: openReplace, preventDefault: true},
      ...searchKeymap,
      ...foldKeymap,
      ...defaultKeymap,
      ...historyKeymap,
    ]),
    editorTheme(),
    languageSlot.of(cachedLanguage(path)),
    EditorView.lineWrapping,
    EditorView.editable.of(editable),
    EditorState.readOnly.of(!editable),
    EditorView.updateListener.of((update) => { if (update.docChanged) onDocChange(update.state); }),
  ];
}

export function createEditorState(options: BufferOptions): EditorState {
  const {doc, ...rest} = options;
  return EditorState.create({doc, extensions: editorExtensions(rest)});
}

/** Loads the grammar for `path` and installs it in the view, unless `stillCurrent` says the view has moved on. */
export async function applyLanguage(view: EditorView, path: string, stillCurrent: () => boolean = () => true) {
  const support = await loadLanguage(path);
  if (!support || !stillCurrent() || !view.dom.isConnected) return;
  view.dispatch({effects: languageSlot.reconfigure(support)});
}
