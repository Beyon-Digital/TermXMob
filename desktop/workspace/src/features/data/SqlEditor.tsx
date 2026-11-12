import {useEffect, useRef} from 'react';
import {Compartment, EditorState} from '@codemirror/state';
import {EditorView, keymap, drawSelection, placeholder as placeholderExtension} from '@codemirror/view';
import {defaultKeymap, history, historyKeymap} from '@codemirror/commands';
import {HighlightStyle, syntaxHighlighting} from '@codemirror/language';
import {sql} from '@codemirror/lang-sql';
import {tags} from '@lezer/highlight';

type Props = {value: string; onChange: (value: string) => void; onRun?: () => void; disabled?: boolean; placeholder?: string; /** Moves focus into the editor each time this number changes to a positive value. */ focusTick?: number};

// Colours come from the shared tokens, so the editor follows light and dark without a second palette.
const theme = EditorView.theme({
  '&': {color: 'var(--text)', backgroundColor: 'var(--surface)', fontSize: '14px', border: '1px solid var(--border-strong)', borderRadius: '8px'},
  '&.cm-focused': {outline: 'var(--control-focus-ring, 2px solid var(--accent))', outlineOffset: 'var(--control-focus-offset, -2px)'},
  '.cm-scroller': {fontFamily: 'var(--font-mono)', lineHeight: '20px', maxHeight: '320px', padding: '8px 0'},
  '.cm-content': {padding: '0 12px', caretColor: 'var(--text)', minHeight: '144px'},
  '.cm-cursor': {borderLeftColor: 'var(--text)'},
  '&.cm-focused .cm-selectionBackground, .cm-selectionBackground': {backgroundColor: 'var(--surface-2)'},
  '.cm-placeholder': {color: 'var(--text-3)'},
});
const highlight = syntaxHighlighting(HighlightStyle.define([
  {tag: [tags.keyword, tags.modifier, tags.operatorKeyword], color: 'var(--accent)', fontWeight: '500'},
  {tag: [tags.string, tags.regexp], color: 'var(--success)'},
  {tag: [tags.lineComment, tags.blockComment], color: 'var(--text-3)'},
  {tag: [tags.number, tags.bool, tags.null], color: 'var(--text-2)'},
  {tag: tags.invalid, color: 'var(--danger)'},
]));

/** CodeMirror 6 SQL editor. Loaded lazily, so the main bundle does not carry it. */
export default function SqlEditor({value, onChange, onRun, disabled = false, placeholder = 'SELECT …', focusTick = 0}: Props) {
  const host = useRef<HTMLDivElement>(null), view = useRef<EditorView | null>(null), lock = useRef(new Compartment());
  const latest = useRef({onChange, onRun});
  latest.current = {onChange, onRun};
  useEffect(() => {
    if (!host.current) return;
    const editor = new EditorView({
      parent: host.current,
      state: EditorState.create({doc: value, extensions: [
        history(), drawSelection(), sql(), highlight, theme, placeholderExtension(placeholder), EditorView.lineWrapping,
        EditorView.contentAttributes.of({'aria-label': 'SQL query', id: 'data-sql', spellcheck: 'false'}),
        keymap.of([{key: 'Mod-Enter', preventDefault: true, run: () => {latest.current.onRun?.(); return true;}}, ...defaultKeymap, ...historyKeymap]),
        EditorView.updateListener.of(update => {if (update.docChanged) latest.current.onChange(update.state.doc.toString());}),
        lock.current.of([EditorState.readOnly.of(disabled), EditorView.editable.of(!disabled)]),
      ]}),
    });
    view.current = editor;
    return () => {editor.destroy(); view.current = null;};
    // The editor is created once; later value and disabled changes are applied below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  useEffect(() => {if (focusTick > 0) view.current?.focus();}, [focusTick]);
  useEffect(() => {
    const editor = view.current;
    if (editor && editor.state.doc.toString() !== value) editor.dispatch({changes: {from: 0, to: editor.state.doc.length, insert: value}});
  }, [value]);
  useEffect(() => {
    view.current?.dispatch({effects: lock.current.reconfigure([EditorState.readOnly.of(disabled), EditorView.editable.of(!disabled)])});
  }, [disabled]);
  return <div className="data-sql-editor" ref={host}/>;
}
