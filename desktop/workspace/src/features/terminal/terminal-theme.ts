import type {ITheme} from '@xterm/xterm';

/* The terminal palette is read from the same CSS variables as the rest of the UI (tokens.css),
   so it follows light, dark and system themes. xterm needs concrete colour strings, so values
   that are still expressions (the bright ANSI colours and the selection are `color-mix`) are
   resolved to a colour by the browser before they are handed over. */

export const ANSI_COLOURS = [
  ['black', '--ansi-black'], ['red', '--ansi-red'], ['green', '--ansi-green'], ['yellow', '--ansi-yellow'],
  ['blue', '--ansi-blue'], ['magenta', '--ansi-magenta'], ['cyan', '--ansi-cyan'], ['white', '--ansi-white'],
  ['brightBlack', '--ansi-bright-black'], ['brightRed', '--ansi-bright-red'], ['brightGreen', '--ansi-bright-green'], ['brightYellow', '--ansi-bright-yellow'],
  ['brightBlue', '--ansi-bright-blue'], ['brightMagenta', '--ansi-bright-magenta'], ['brightCyan', '--ansi-bright-cyan'], ['brightWhite', '--ansi-bright-white'],
] as const satisfies readonly (readonly [keyof ITheme, string])[];

/** Cell height follows the font's own line box (about 16px at 12px mono), so the line-height multiplier stays 1. */
export const TERMINAL_FONT_FALLBACK = '"JetBrains Mono Variable", "JetBrains Mono", ui-monospace, SFMono-Regular, monospace';
export const TERMINAL_SIZE_FALLBACK = 12;

type Resolver = (value: string) => string;

let probe: CanvasRenderingContext2D | null | undefined;

/** Turns any CSS colour (including color-mix and color()) into the browser's own #rrggbb or rgba() form. */
export function resolveColour(value: string): string {
  const text = value.trim();
  if (!text || !/color-mix\(|color\(|var\(/.test(text)) return text;
  try {
    probe ??= document.createElement('canvas').getContext('2d');
    if (!probe) return '';
    probe.fillStyle = 'white';
    probe.fillStyle = text;
    const onWhite = String(probe.fillStyle);
    probe.fillStyle = 'black';
    probe.fillStyle = text;
    return onWhite === String(probe.fillStyle) ? onWhite : '';
  } catch {
    return '';
  }
}

/** The xterm theme for the current document: ANSI 16, foreground, background, cursor and selection. */
export function readTerminalTheme(root: HTMLElement = document.documentElement, resolve: Resolver = resolveColour): ITheme {
  const css = getComputedStyle(root);
  const read = (name: string) => resolve(css.getPropertyValue(name)) || undefined;
  const theme: ITheme = {
    background: read('--bg'),
    foreground: read('--text'),
    cursor: read('--accent'),
    cursorAccent: read('--bg'),
    selectionBackground: read('--selection'),
    selectionInactiveBackground: read('--surface-2'),
  };
  for (const [key, name] of ANSI_COLOURS) theme[key] = read(name);
  for (const key of Object.keys(theme) as (keyof ITheme)[]) if (theme[key] === undefined) delete theme[key];
  return theme;
}

/** Font family and size from the tokens, falling back to the bundled mono stack. */
export function readTerminalFont(root: HTMLElement = document.documentElement) {
  const css = getComputedStyle(root);
  const size = parseFloat(css.getPropertyValue('--term-size'));
  return {fontFamily: css.getPropertyValue('--font-mono').trim() || TERMINAL_FONT_FALLBACK, fontSize: Number.isFinite(size) && size > 0 ? size : TERMINAL_SIZE_FALLBACK};
}

/** Highlight colours for the search addon (xterm requires #rrggbb here). */
export function readSearchDecorations(root: HTMLElement = document.documentElement, resolve: Resolver = resolveColour) {
  const css = getComputedStyle(root);
  const read = (name: string) => resolve(css.getPropertyValue(name));
  const match = read('--border-strong') || undefined;
  const active = read('--accent') || undefined;
  return {matchBackground: match, matchOverviewRuler: match ?? '', activeMatchBackground: active, activeMatchColorOverviewRuler: active ?? ''};
}

const ATTRIBUTES = ['data-theme', 'class', 'style', 'data-app-workspace'];

/**
 * Calls `apply` whenever the theme can have changed: the data-theme attribute, the `.dark` class,
 * inline custom properties, or the system colour scheme while no explicit theme is chosen.
 * Returns a function that stops observing.
 */
export function observeTerminalTheme(apply: () => void, root: HTMLElement = document.documentElement) {
  const observer = new MutationObserver(apply);
  observer.observe(root, {attributes: true, attributeFilter: ATTRIBUTES});
  const scheme = typeof window.matchMedia === 'function' ? window.matchMedia('(prefers-color-scheme: dark)') : null;
  scheme?.addEventListener?.('change', apply);
  return () => {
    observer.disconnect();
    scheme?.removeEventListener?.('change', apply);
  };
}
