import {describe, expect, it} from 'vitest';
import {formatShortcut} from './shortcut-label';

describe('formatShortcut', () => {
  it('uses symbols on macOS', () => {
    expect(formatShortcut('Mod+Alt+B', true)).toBe('⌘⌥B');
    expect(formatShortcut('Mod+Shift+T', true)).toBe('⌘⇧T');
    expect(formatShortcut('Ctrl+Meta+F', true)).toBe('⌃⌘F');
  });
  it('spells keys out elsewhere', () => {
    expect(formatShortcut('Mod+Alt+B', false)).toBe('Ctrl+Alt+B');
    expect(formatShortcut('Mod+w', false)).toBe('Ctrl+W');
    expect(formatShortcut('Mod+1', false)).toBe('Ctrl+1');
  });
});
