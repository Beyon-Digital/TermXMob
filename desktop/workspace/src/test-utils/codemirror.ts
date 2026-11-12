import {EditorView} from '@codemirror/view';
import {act, screen, waitFor} from '@testing-library/react';

// CodeMirror measures text with Range geometry that jsdom does not implement.
if (typeof Range !== 'undefined') {
  const empty = () => ({top: 0, left: 0, right: 0, bottom: 0, width: 0, height: 0, x: 0, y: 0, toJSON() {return {};}});
  if (!Range.prototype.getClientRects) Range.prototype.getClientRects = (() => ({length: 0, item: () => null, [Symbol.iterator]: function* () {}})) as unknown as Range['getClientRects'];
  if (!Range.prototype.getBoundingClientRect) Range.prototype.getBoundingClientRect = empty as unknown as Range['getBoundingClientRect'];
}

const editor = async (label: string) => {
  const content = await waitFor(() => {
    const node = screen.getByLabelText(label);
    if (!node.classList.contains('cm-content')) throw new Error('SQL editor is still loading');
    return node;
  });
  const view = EditorView.findFromDOM(content.closest('.cm-editor') as HTMLElement);
  if (!view) throw new Error('No editor view for ' + label);
  return view;
};
/** Replace the SQL in the CodeMirror editor labelled `label`. */
export async function typeSql(label: string, text: string) {
  const view = await editor(label);
  await act(async () => {view.dispatch({changes: {from: 0, to: view.state.doc.length, insert: text}});});
}
/** Current text of the CodeMirror editor labelled `label`. */
export async function sqlValue(label: string) {
  return (await editor(label)).state.doc.toString();
}
