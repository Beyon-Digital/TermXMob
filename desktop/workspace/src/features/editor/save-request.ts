/**
 * File > Save and the Save key ask the editor, not the shell, to save: buffers and their dirty state belong to the file
 * tabs. The tab that holds focus saves; with focus elsewhere, every visible file tab does.
 */
export const SAVE_FILE_EVENT = 'termx-save-file';

export function requestSaveFile(): void {
  window.dispatchEvent(new Event(SAVE_FILE_EVENT));
}

/** Whether this file tab should answer a save request, given where focus is. */
export function answersSaveRequest(tab: Element | null, visible: boolean, active: Element | null = document.activeElement): boolean {
  if (!tab || !visible) return false;
  const focused = active?.closest('.file-tab') ?? null;
  return focused ? focused === tab : true;
}
