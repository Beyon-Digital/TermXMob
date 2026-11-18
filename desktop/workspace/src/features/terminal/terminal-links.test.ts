import {afterEach, describe, expect, it, vi} from 'vitest';
import {openTerminalLink} from './terminal-links';
import {isTerminalFind} from './terminal-keys';

afterEach(() => vi.unstubAllGlobals());

describe('terminal links', () => {
  it('opens an https link on Command-click without an opener', () => {
    const open = vi.fn(() => ({opener: {}}));
    vi.stubGlobal('open', open);
    expect(openTerminalLink({metaKey: true, ctrlKey: false}, 'https://example.com/docs?q=1')).toBe(true);
    expect(open).toHaveBeenCalledWith('https://example.com/docs?q=1', '_blank', 'noopener,noreferrer');
  });

  it('opens on Control-click too', () => {
    const open = vi.fn(() => null);
    vi.stubGlobal('open', open);
    expect(openTerminalLink({metaKey: false, ctrlKey: true}, 'http://localhost:5173/')).toBe(true);
    expect(open).toHaveBeenCalledOnce();
  });

  it('ignores a plain click, so selecting text never navigates', () => {
    const open = vi.fn();
    vi.stubGlobal('open', open);
    expect(openTerminalLink({metaKey: false, ctrlKey: false}, 'https://example.com')).toBe(false);
    expect(open).not.toHaveBeenCalled();
  });

  it.each(['javascript:alert(1)', 'file:///etc/passwd', 'https://user:pass@example.com', 'https://exa mple.com', 'ftp://example.com'])('refuses %s', (uri) => {
    const open = vi.fn();
    vi.stubGlobal('open', open);
    expect(openTerminalLink({metaKey: true, ctrlKey: false}, uri)).toBe(false);
    expect(open).not.toHaveBeenCalled();
  });
});

describe('terminal find shortcut', () => {
  const key = (init: Partial<KeyboardEvent>) => ({key: 'f', metaKey: false, ctrlKey: false, shiftKey: false, altKey: false, ...init});
  it('is Command-F, or Control-Shift-F, and leaves plain Control-F to the shell', () => {
    expect(isTerminalFind(key({metaKey: true}))).toBe(true);
    expect(isTerminalFind(key({ctrlKey: true, shiftKey: true}))).toBe(true);
    expect(isTerminalFind(key({ctrlKey: true}))).toBe(false);
    expect(isTerminalFind(key({}))).toBe(false);
    expect(isTerminalFind(key({metaKey: true, altKey: true}))).toBe(false);
    expect(isTerminalFind(key({key: 'g', metaKey: true}))).toBe(false);
  });
});
