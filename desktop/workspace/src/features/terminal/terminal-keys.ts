/** Command-F (macOS) or Control-Shift-F opens find inside the terminal. Plain Control-F stays shell input (readline forward-char). */
export function isTerminalFind(event: Pick<KeyboardEvent, 'key' | 'metaKey' | 'ctrlKey' | 'shiftKey' | 'altKey'>) {
  if (event.key.toLowerCase() !== 'f' || event.altKey) return false;
  return (event.metaKey && !event.ctrlKey) || (event.ctrlKey && event.shiftKey && !event.metaKey);
}
