/**
 * "New terminal" from a command (Control+Shift+backtick). The terminal tab may not be mounted yet, so the request is
 * remembered until a terminal panel takes it, and an event wakes one that is already there.
 */

export const NEW_TERMINAL_EVENT = 'termx-terminal-new';

let pending = false;

export function requestNewTerminal() {
  pending = true;
  window.dispatchEvent(new Event(NEW_TERMINAL_EVENT));
}

/** Take the pending request, if there is one. */
export function takeNewTerminalRequest(): boolean {
  const was = pending;
  pending = false;
  return was;
}
