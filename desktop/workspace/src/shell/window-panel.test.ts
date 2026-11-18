import {readFileSync} from 'node:fs';
import {join} from 'node:path';
import {describe, expect, it} from 'vitest';
import {TAB_KINDS} from '../lib/layout/model';
import {WINDOW_PANELS, kindForWindowPanel, windowPanelForKind} from './window-panel';

describe('tab kind to window panel', () => {
  it('maps every detachable kind to a panel string the native shell accepts', () => {
    expect(windowPanelForKind('chat')).toBe('chat');
    expect(windowPanelForKind('file')).toBe('workbench');
    expect(windowPanelForKind('diff')).toBe('review');
    expect(windowPanelForKind('terminal')).toBe('terminal');
    expect(windowPanelForKind('cli')).toBe('terminal');
    expect(windowPanelForKind('browser')).toBe('browser');
    expect(windowPanelForKind('computer')).toBe('computer');
    expect(windowPanelForKind('artifacts')).toBe('artifacts');
  });

  it('does not offer to detach kinds that have no window', () => {
    for (const kind of ['data', 'studio', 'settings', 'projects', 'workflows'] as const) expect(windowPanelForKind(kind)).toBeNull();
  });

  it('covers every kind, one way or the other', () => {
    for (const kind of TAB_KINDS) {
      const panel = windowPanelForKind(kind);
      if (panel) expect(WINDOW_PANELS).toContain(panel);
    }
  });

  it('round-trips a panel to the tab a detached window opens with', () => {
    for (const panel of WINDOW_PANELS) expect(windowPanelForKind(kindForWindowPanel(panel))).toBe(panel);
    expect(kindForWindowPanel('workbench')).toBe('file');
    expect(kindForWindowPanel('review')).toBe('diff');
  });

  it('uses exactly the panel strings desktop/src-tauri valid_panel() accepts', () => {
    const source = readFileSync(join(process.cwd(), '../src-tauri/src/workspace.rs'), 'utf8');
    const body = source.slice(source.indexOf('fn valid_panel'));
    const accepted = [...body.slice(0, body.indexOf('}')).matchAll(/"([a-z]+)"/g)].map(match => match[1]);
    expect(accepted.sort()).toEqual([...WINDOW_PANELS].sort());
  });
});
