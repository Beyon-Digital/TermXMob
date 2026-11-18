import {findCommand, keyedSpecs, sharesContext, type CommandId as KeyedCommandId} from '../shell/commands';
import {isMac} from './shortcut-label';

/*
 * Key grammar and stored bindings. The commands themselves, their titles and their default keys live in the
 * registry (`shell/commands.ts`); this module turns that into the `{id: "Mod+Shift+P"}` map people edit and that is
 * stored under `termx-keybindings:<principal>`. The storage key and format are unchanged: a stored map that lacks a
 * newer command simply gets that command's default.
 */

export type CommandId = KeyedCommandId;
export type Bindings = Record<CommandId, string>;

const ids = Object.keys(keyedSpecs) as CommandId[];

export const defaults = Object.fromEntries(ids.map(id => [id, keyedSpecs[id].defaultKeys])) as Bindings;

export const labels = Object.fromEntries(ids.map(id => [id, keyedSpecs[id].title])) as Record<
  CommandId,
  string
>;

type StrokeOptions = {
  /**
   * The stroke is typed into a terminal. `Mod` then means the platform's own command key only (Command on macOS,
   * Control elsewhere), so Control combinations the shell relies on (Ctrl+B, Ctrl+J, Ctrl+P ...) reach the PTY.
   */
  terminal?: boolean;
};

export function strokeMatches(stroke: string, event: KeyboardEvent, options: StrokeOptions = {}) {
  const pieces = stroke
    .toLowerCase()
    .split('+')
    .map(part => part.trim());
  const key = pieces.pop();
  const mod = pieces.includes('mod');
  const commandKey = options.terminal
    ? isMac()
      ? event.metaKey
      : event.ctrlKey
    : event.ctrlKey || event.metaKey;
  // Shift turns the backtick key into `~`, so it is matched by position as well.
  const keyMatches = event.key.toLowerCase() === key || (key === '`' && event.code === 'Backquote');
  return (
    keyMatches &&
    event.shiftKey === pieces.includes('shift') &&
    event.altKey === pieces.includes('alt') &&
    (mod
      ? commandKey
      : event.ctrlKey === pieces.includes('ctrl') && event.metaKey === pieces.includes('meta'))
  );
}

/** Modifiers, then one key: a letter, digit, `` ` ``, `,`, `\`, Tab, Escape or F1-F24. Chord steps are space separated. */
const STROKE = /^(?:(?:Mod|Ctrl|Meta|Alt|Shift)\+)*(?:[A-Za-z0-9`,\\]|Tab|Escape|F\d{1,2})$/i;

export function validBinding(text: string) {
  return (
    text.length <= 80 &&
    text
      .trim()
      .split(/\s+/)
      .every(stroke => STROKE.test(stroke))
  );
}

export function validateBindings(value: unknown): Bindings {
  const result = {...defaults};
  if (value && typeof value === 'object') {
    for (const id of ids) {
      const text = (value as Record<string, unknown>)[id];
      if (typeof text === 'string' && validBinding(text)) result[id] = text.trim();
    }
  }
  return result;
}

/** Combinations a browser tab keeps for itself: close, new tab, new window, switch tab by number, Ctrl+Tab ... */
const BROWSER_RESERVED = /^(?:mod|ctrl|meta)\+(?:w|t|l|r|n|q|p|[1-9]|[+-]|(?:shift\+)?tab)$/i;

/** The same chord with Alt added before the key, the usual way out of a browser shortcut. */
function altFallback(binding: string) {
  const pieces = binding.trim().split('+');
  const key = pieces.pop() as string;
  return [...pieces, 'Alt', key].join('+');
}

export function bindingWarnings(bindings: Bindings) {
  const seen = new Map<string, string[]>();
  const warnings: string[] = [];
  const taken = new Set(Object.values(bindings).map(binding => binding.toLowerCase()));
  for (const [id, binding] of Object.entries(bindings)) {
    const title = findCommand(id)?.title ?? id;
    const normalized = binding.toLowerCase();
    // Two commands only clash when both keys are live in the same layout engine.
    const earlier = (seen.get(normalized) ?? []).find(other => {
      const a = findCommand(id);
      const b = findCommand(other);
      return !a || !b || sharesContext(a, b);
    });
    if (earlier !== undefined) warnings.push(title + ' conflicts with ' + (findCommand(earlier)?.title ?? earlier));
    seen.set(normalized, [...(seen.get(normalized) ?? []), id]);
    if (BROWSER_RESERVED.test(binding)) {
      const fallback = altFallback(binding);
      const hint = taken.has(fallback.toLowerCase()) ? '' : ' Try ' + fallback + ' instead, or use';
      warnings.push(
        title +
          ': browsers may reserve ' +
          binding +
          '.' +
          (hint || ' Use') +
          ' the Commands menu when the shortcut is unavailable.',
      );
    }
  }
  return warnings;
}

const REMOTE_INPUT = '.computer-surface,.browser-frame,.browser-remote-input,[data-agent-input="true"]';

/** Remote Browser and Computer input, where every key belongs to the controlled page or desktop. */
export function remoteInputSurface(target: EventTarget | null) {
  return target instanceof Element && !!target.closest(REMOTE_INPUT);
}

export function terminalSurface(target: EventTarget | null) {
  return target instanceof Element && !!target.closest('.xterm');
}

/**
 * Where a key press landed, for the shortcut dispatcher: `remote` surfaces keep every key, a `terminal` keeps
 * every key except the commands that opt in with `terminal: true`, and everything else is `page`.
 */
export function shortcutSurface(target: EventTarget | null): 'remote' | 'terminal' | 'page' {
  if (remoteInputSurface(target)) return 'remote';
  return terminalSurface(target) ? 'terminal' : 'page';
}

/** Terminal and remote input: surfaces that keep keys the workspace would otherwise use (Escape, dictation). */
export function protectedSurface(target: EventTarget | null) {
  return remoteInputSurface(target) || terminalSurface(target);
}

export function loadBindings(serialized: string | null) {
  try {
    return validateBindings(JSON.parse(serialized || 'null'));
  } catch {
    return {...defaults};
  }
}
