import {readFileSync} from 'node:fs';
import {resolve} from 'node:path';
import {afterEach, describe, expect, it, vi} from 'vitest';
import {ANSI_COLOURS, observeTerminalTheme, readSearchDecorations, readTerminalFont, readTerminalTheme, resolveColour} from './terminal-theme';

const tokens = readFileSync(resolve(process.cwd(), 'src/styles/tokens.css'), 'utf8');
const root = document.documentElement;
const names = ['--bg', '--text', '--accent', '--selection', '--surface-2', '--border-strong', '--term-size', '--font-mono', ...ANSI_COLOURS.map(([, name]) => name)];

afterEach(() => {
  for (const name of names) root.style.removeProperty(name);
  root.removeAttribute('data-theme');
  root.className = '';
  vi.unstubAllGlobals();
});

describe('terminal theme', () => {
  it('maps all 16 ANSI colours, and each variable is defined in tokens.css', () => {
    expect(ANSI_COLOURS).toHaveLength(16);
    expect(new Set(ANSI_COLOURS.map(([key]) => key)).size).toBe(16);
    for (const [, name] of ANSI_COLOURS) expect(tokens, name).toMatch(new RegExp(name + '\\s*:'));
  });

  it('reads every ANSI colour, background, foreground, cursor and selection from computed variables', () => {
    ANSI_COLOURS.forEach(([, name], index) => root.style.setProperty(name, `rgb(${index + 1}, 20, 30)`));
    root.style.setProperty('--bg', 'rgb(250, 249, 247)');
    root.style.setProperty('--text', 'rgb(31, 30, 27)');
    root.style.setProperty('--accent', 'rgb(176, 80, 45)');
    root.style.setProperty('--selection', 'rgba(176, 80, 45, 0.24)');
    const theme = readTerminalTheme(root);
    ANSI_COLOURS.forEach(([key], index) => expect(theme[key], key).toBe(`rgb(${index + 1}, 20, 30)`));
    expect(theme).toMatchObject({background: 'rgb(250, 249, 247)', foreground: 'rgb(31, 30, 27)', cursor: 'rgb(176, 80, 45)', cursorAccent: 'rgb(250, 249, 247)', selectionBackground: 'rgba(176, 80, 45, 0.24)'});
  });

  it('hands expressions such as color-mix to the resolver instead of to xterm', () => {
    root.style.setProperty('--ansi-bright-red', 'color-mix(in srgb, red 80%, black)');
    root.style.setProperty('--bg', '#123456');
    const resolveStub = vi.fn((value: string) => (value.includes('color-mix') ? 'resolved-red' : value.trim()));
    const theme = readTerminalTheme(root, resolveStub);
    expect(theme.brightRed).toBe('resolved-red');
    expect(theme.background).toBe('#123456');
  });

  it('omits colours that are not defined, so xterm keeps its own default', () => {
    expect(readTerminalTheme(root)).toEqual({});
  });

  it('leaves a plain colour alone and rejects one the browser cannot parse', () => {
    expect(resolveColour('#abcdef')).toBe('#abcdef');
    expect(resolveColour('')).toBe('');
  });

  it('reads mono family and the 12px terminal size from tokens, with a bundled fallback', () => {
    expect(readTerminalFont(root)).toEqual({fontFamily: expect.stringContaining('JetBrains Mono'), fontSize: 12});
    root.style.setProperty('--term-size', '13px');
    root.style.setProperty('--font-mono', '"Custom Mono", monospace');
    expect(readTerminalFont(root)).toEqual({fontFamily: '"Custom Mono", monospace', fontSize: 13});
  });

  it('derives search highlight colours from tokens', () => {
    root.style.setProperty('--border-strong', 'rgb(1, 2, 3)');
    root.style.setProperty('--accent', 'rgb(4, 5, 6)');
    expect(readSearchDecorations(root)).toEqual({matchBackground: 'rgb(1, 2, 3)', matchOverviewRuler: 'rgb(1, 2, 3)', activeMatchBackground: 'rgb(4, 5, 6)', activeMatchColorOverviewRuler: 'rgb(4, 5, 6)'});
  });
});

describe('observeTerminalTheme', () => {
  it('re-applies when data-theme or the dark class changes, and stops when disposed', async () => {
    const apply = vi.fn();
    const stop = observeTerminalTheme(apply, root);
    root.dataset.theme = 'dark';
    await vi.waitFor(() => expect(apply).toHaveBeenCalledTimes(1));
    root.classList.add('dark');
    await vi.waitFor(() => expect(apply).toHaveBeenCalledTimes(2));
    stop();
    root.dataset.theme = 'light';
    await new Promise((done) => setTimeout(done, 20));
    expect(apply).toHaveBeenCalledTimes(2);
  });

  it('re-applies when the system colour scheme flips', () => {
    let listener: (() => void) | undefined;
    const remove = vi.fn();
    vi.stubGlobal('matchMedia', (query: string) => ({matches: false, media: query, addEventListener: (_: string, fn: () => void) => { listener = fn; }, removeEventListener: remove}));
    const apply = vi.fn();
    const stop = observeTerminalTheme(apply, root);
    listener?.();
    expect(apply).toHaveBeenCalledOnce();
    stop();
    expect(remove).toHaveBeenCalledOnce();
  });
});
