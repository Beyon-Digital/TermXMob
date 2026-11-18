/**
 * "Open this file at that line" from outside the editor (a Search result, a file reference in chat, Go to definition).
 *
 * The file tab for a buffer may not be mounted yet, so the line is remembered per buffer key until the tab takes it, and an
 * event wakes a tab that is already there.
 */

import type {LayoutAction} from '../../lib/layout/reduce';
import {bufferKey, type FileTarget} from './file-io';

export const FILE_LINE_EVENT = 'termx-file-line';

const pending = new Map<string, number>();

export function requestFileLine(key: string, line: number) {
  pending.set(key, line);
  window.dispatchEvent(new CustomEvent(FILE_LINE_EVENT, {detail: {key}}));
}

/** Take the line queued for a buffer, if there is one. */
export function takeFileLine(key: string): number | undefined {
  const line = pending.get(key);
  pending.delete(key);
  return line;
}

/**
 * Open a file in a tab of its own, or reveal the tab that already shows it, at a line when one is given. A file is one tab
 * per buffer key, so opening the same file twice never makes a second tab.
 */
export function openFileTab(dispatch: (action: LayoutAction) => void, target: FileTarget, path: string, line?: number) {
  const key = bufferKey(target, path);
  if (line) requestFileLine(key, line);
  dispatch({type: 'openTab', kind: 'file', ref: key});
}
