import {useEffect, useLayoutEffect, useRef} from 'react';
import {shortcutSurface, strokeMatches, type Bindings, type CommandId} from '../lib/keybindings';
import type {ShellEngine} from '../lib/shell-engine';
import {keyLive, keyedSpecs} from './commands';

type ShortcutOptions = {
  bindings: Bindings;
  /** The layout engine in force. A key that belongs to the other engine is left alone for the page and the shell. */
  engine?: ShellEngine;
  /** True while the workspace is locked: no key does anything. */
  suspended: () => boolean;
  run: (id: CommandId, terminalFocused: boolean) => void;
  /** Escape, before commands. Return true when it was handled (Focus mode restoring the layout). */
  escape?: (event: KeyboardEvent) => boolean;
};

const CHORD_TIMEOUT = 900;
const MODIFIER_KEYS = new Set(['Meta', 'Control', 'Alt', 'Shift', 'AltGraph', 'OS']);

const steps = (binding: string) => binding.trim().split(/\s+/);

/**
 * The one keyboard dispatcher. Single keys and two-step chords come from the user's bindings; what each key does comes from
 * the command registry.
 *
 * Which keys apply depends on where focus is:
 * - Browser and Computer remote input keeps every key.
 * - A terminal keeps every key except the commands that opt in (`terminal: true`): the palette, the panel toggles, tab
 *   switching and focus-terminal. Even for those, `Mod` means Command on macOS and Control elsewhere only, so Ctrl+B or
 *   Ctrl+J still reach the shell on a Mac.
 * - Anywhere else every binding applies.
 */
export function useShortcuts(options: ShortcutOptions) {
  const latest = useRef(options);
  useLayoutEffect(() => {
    latest.current = options;
  });
  const {bindings} = options;
  const engine = options.engine ?? 'legacy';

  useEffect(() => {
    let prefix = '';
    let pending: ReturnType<typeof setTimeout> | undefined;
    const shortcuts = (event: KeyboardEvent) => {
      const current = latest.current;
      if (current.suspended() || event.defaultPrevented || event.isComposing || event.repeat) return;
      // Pressing a modifier again between the two steps of a chord (Command K, then Command + backslash) is not a stroke.
      if (MODIFIER_KEYS.has(event.key)) return;
      const surface = shortcutSurface(event.target);
      if (surface === 'remote') return;
      const terminal = surface === 'terminal';
      if (!terminal && event.key === 'Escape' && current.escape?.(event)) {
        event.preventDefault();
        return;
      }
      const entries = (Object.entries(bindings) as [CommandId, string][]).filter(
        ([id]) => keyLive(keyedSpecs[id], engine) && (!terminal || keyedSpecs[id].terminal),
      );
      const matches = (stroke: string) => strokeMatches(stroke, event, {terminal});
      const run = (id: CommandId) => current.run(id, terminal);
      if (prefix) {
        const candidate = entries.find(([, binding]) => {
          const parts = steps(binding);
          return parts.length === 2 && parts[0] === prefix && matches(parts[1]);
        });
        clearTimeout(pending);
        prefix = '';
        if (candidate) {
          event.preventDefault();
          run(candidate[0]);
          return;
        }
      }
      const match = entries.find(([, binding]) => matches(steps(binding)[0]));
      if (!match) return;
      event.preventDefault();
      const stroke = steps(match[1])[0];
      const chord = entries.some(([, binding]) => binding.startsWith(stroke + ' '));
      if (chord) {
        prefix = stroke;
        pending = setTimeout(() => {
          prefix = '';
          const single = entries.find(([, binding]) => binding === stroke);
          if (single) run(single[0]);
        }, CHORD_TIMEOUT);
      } else {
        run(match[0]);
      }
    };
    window.addEventListener('keydown', shortcuts);
    return () => {
      clearTimeout(pending);
      window.removeEventListener('keydown', shortcuts);
    };
  }, [bindings, engine]);
}
