import {EditorView} from '@codemirror/view';
import {HighlightStyle, syntaxHighlighting} from '@codemirror/language';
import type {Extension} from '@codemirror/state';
import {tags as t} from '@lezer/highlight';

/* Every colour below is a CSS variable from src/styles/tokens.css, so the editor follows
   light, dark and system themes without being reconfigured or re-created. Do not add a
   literal colour here; editor-theme.test.ts fails on one.

   CodeMirror injects its own unlayered stylesheet, which beats any @layer rule, so the
   chrome of the editor, its search panel, tooltips and folds is themed here rather than
   in editor.css. */

const mono = 'var(--font-mono)';
const focusRing = '2px solid var(--accent)';

export const editorChromeSpec = {
  '&': {
    color: 'var(--text)',
    backgroundColor: 'var(--bg)',
    fontSize: 'var(--code-size)',
    height: '100%',
  },
  /* The focus ring is drawn on the focusable inner element, for keyboard focus only. */
  '&.cm-focused': {outlineWidth: '0'},
  '.cm-content:focus-visible, .cm-scroller:focus-visible': {outline: focusRing, outlineOffset: '-2px'},
  '.cm-scroller': {fontFamily: mono, lineHeight: 'var(--code-line)'},
  '.cm-content': {caretColor: 'var(--text)', padding: 'var(--space-8) 0', fontFamily: mono},
  '.cm-line': {padding: '0 var(--space-16)'},
  '.cm-cursor, .cm-dropCursor': {borderLeftColor: 'var(--text)'},
  '&.cm-focused > .cm-scroller > .cm-selectionLayer .cm-selectionBackground, .cm-selectionBackground, .cm-content ::selection': {
    backgroundColor: 'var(--selection)',
  },
  '.cm-activeLine': {backgroundColor: 'var(--surface-2)'},
  '.cm-selectionMatch': {backgroundColor: 'var(--surface-2)'},
  '&.cm-focused .cm-matchingBracket': {backgroundColor: 'var(--selection)', outline: '1px solid var(--border-strong)'},
  '&.cm-focused .cm-nonmatchingBracket': {backgroundColor: 'var(--diff-del)', color: 'var(--danger)'},

  '.cm-gutters': {
    backgroundColor: 'var(--bg)',
    color: 'var(--text-3)',
    border: 'none',
    fontFamily: mono,
  },
  '.cm-lineNumbers .cm-gutterElement': {padding: '0 var(--space-8) 0 var(--space-16)', minWidth: 'var(--space-32)'},
  '.cm-activeLineGutter': {backgroundColor: 'var(--surface-2)', color: 'var(--text)'},
  '.cm-foldGutter .cm-gutterElement': {padding: '0 var(--space-4)', cursor: 'pointer', color: 'var(--text-3)'},
  '.cm-foldGutter .cm-gutterElement:hover': {color: 'var(--text)'},
  '.cm-foldPlaceholder': {
    backgroundColor: 'var(--surface-2)',
    color: 'var(--text-2)',
    border: '1px solid var(--border)',
    borderRadius: 'var(--radius)',
    padding: '0 var(--space-8)',
    margin: '0 var(--space-4)',
  },

  /* find and replace */
  '.cm-searchMatch': {backgroundColor: 'var(--surface-2)', outline: '1px solid var(--border-strong)'},
  '.cm-searchMatch.cm-searchMatch-selected': {backgroundColor: 'var(--selection)', outline: '1px solid var(--accent)'},
  '.cm-panels': {backgroundColor: 'var(--surface)', color: 'var(--text)', fontFamily: 'var(--font-sans)'},
  '.cm-panels.cm-panels-top': {borderBottom: '1px solid var(--border)'},
  '.cm-panels.cm-panels-bottom': {borderTop: '1px solid var(--border)'},
  '.cm-panel.cm-search': {
    display: 'flex',
    flexWrap: 'wrap',
    alignItems: 'center',
    gap: 'var(--space-8)',
    padding: 'var(--space-8) var(--space-12)',
    paddingRight: 'var(--space-32)',
    fontSize: 'var(--ide-input-size)',
    lineHeight: 'var(--ide-input-line)',
  },
  '.cm-panel.cm-search br': {display: 'none'},
  '.cm-panel.cm-search input, .cm-panel.cm-search button, .cm-panel.cm-search label': {
    margin: '0',
    fontSize: 'var(--ide-input-size)',
    fontFamily: 'var(--font-sans)',
  },
  '.cm-panel.cm-search .cm-textfield': {
    height: 'var(--ide-input-h)',
    boxSizing: 'border-box',
    padding: '0 var(--space-8)',
    backgroundColor: 'var(--surface)',
    color: 'var(--text)',
    border: '1px solid var(--border-strong)',
    borderRadius: 'var(--radius)',
    width: '12em',
    maxWidth: '100%',
  },
  '.cm-panel.cm-search .cm-textfield:focus-visible': {outline: focusRing, outlineOffset: '2px'},
  '.cm-panel.cm-search .cm-textfield::placeholder': {color: 'var(--text-3)'},
  '.cm-panel.cm-search .cm-button': {
    height: 'var(--ide-input-h)',
    boxSizing: 'border-box',
    padding: '0 var(--space-12)',
    backgroundImage: 'none',
    backgroundColor: 'transparent',
    color: 'var(--text)',
    border: '1px solid var(--border-strong)',
    borderRadius: 'var(--radius)',
    cursor: 'pointer',
  },
  '.cm-panel.cm-search .cm-button:hover': {backgroundColor: 'var(--surface-2)'},
  '.cm-panel.cm-search .cm-button:active': {backgroundImage: 'none', backgroundColor: 'var(--surface-2)'},
  '.cm-panel.cm-search .cm-button:focus-visible': {outline: focusRing, outlineOffset: '2px'},
  '.cm-panel.cm-search label': {display: 'inline-flex', alignItems: 'center', gap: 'var(--space-4)', color: 'var(--text-2)'},
  '.cm-panel.cm-search [name=close]': {
    position: 'absolute',
    top: 'var(--space-8)',
    right: 'var(--space-8)',
    width: 'var(--ide-action)',
    height: 'var(--ide-action)',
    padding: '0',
    border: 'none',
    backgroundColor: 'transparent',
    color: 'var(--text-2)',
    borderRadius: 'var(--radius)',
    cursor: 'pointer',
    fontSize: 'var(--text-base)',
  },
  '.cm-panel.cm-search [name=close]:hover': {backgroundColor: 'var(--surface-2)', color: 'var(--text)'},
  '.cm-panel.cm-search [name=close]:focus-visible': {outline: focusRing, outlineOffset: '2px'},
  '.cm-gotoLine, .cm-panel.cm-gotoLine': {padding: 'var(--space-8) var(--space-12)'},

  /* hover, completion and lint popups */
  '.cm-tooltip': {
    backgroundColor: 'var(--surface)',
    color: 'var(--text)',
    border: '1px solid var(--border)',
    borderRadius: 'var(--radius)',
    boxShadow: 'var(--shadow-overlay)',
  },

  /* diff tints, used by review surfaces that mark changed lines */
  '.cm-line.cm-line-added': {backgroundColor: 'var(--diff-add)'},
  '.cm-line.cm-line-deleted': {backgroundColor: 'var(--diff-del)'},
};

export const editorHighlightSpec = [
  {tag: [t.comment, t.lineComment, t.blockComment, t.docString], color: 'var(--syntax-comment)'},
  {tag: [t.keyword, t.modifier, t.controlKeyword, t.definitionKeyword, t.operatorKeyword, t.moduleKeyword, t.tagName], color: 'var(--syntax-keyword)'},
  {tag: [t.string, t.special(t.string), t.regexp, t.inserted], color: 'var(--syntax-string)'},
  {tag: [t.number, t.bool, t.null, t.atom, t.self, t.constant(t.name), t.escape], color: 'var(--syntax-constant)'},
  {tag: [t.typeName, t.className, t.namespace, t.function(t.variableName), t.function(t.propertyName), t.propertyName, t.attributeName, t.labelName], color: 'var(--syntax-entity)'},
  {tag: [t.punctuation, t.separator, t.bracket, t.operator, t.derefOperator, t.meta, t.processingInstruction], color: 'var(--syntax-punctuation)'},
  {tag: [t.heading, t.strong], color: 'var(--text)', fontWeight: '600'},
  {tag: t.emphasis, fontStyle: 'italic'},
  {tag: [t.link, t.url], color: 'var(--accent)', textDecoration: 'underline'},
  {tag: [t.quote, t.contentSeparator], color: 'var(--text-3)'},
  {tag: t.monospace, color: 'var(--syntax-entity)'},
  {tag: [t.invalid, t.deleted], color: 'var(--danger)'},
];

export const editorHighlightStyle = HighlightStyle.define(editorHighlightSpec);

/** The one editor theme. `dark` stays false because every colour is a variable that already changes with the theme. */
export function editorTheme(): Extension[] {
  return [EditorView.theme(editorChromeSpec, {dark: false}), syntaxHighlighting(editorHighlightStyle)];
}
