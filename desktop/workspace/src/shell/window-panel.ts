import type {TabKind} from '../lib/layout/model';
import type {WindowPanel} from '../lib/window-journal';

/**
 * The detach and redock handshake is fixed by the native shell: `workspace_detach` / `workspace_redock*` in
 * `desktop/src-tauri/src/workspace.rs` accept only these seven `valid_panel` strings, and the redock payload is
 * `{version: 1, sessionId, buffers (at most 100), draft}`. A tab can move to a new window only if its kind maps to one of
 * them; the mapping lives here so neither the layout model nor the Rust side has to know about the other.
 */

const PANEL_OF_KIND: Partial<Record<TabKind, WindowPanel>> = {
  chat: 'chat',
  file: 'workbench',
  diff: 'review',
  terminal: 'terminal',
  cli: 'terminal',
  browser: 'browser',
  computer: 'computer',
  artifacts: 'artifacts',
};

/** The `valid_panel` string for a tab kind, or `null` when the kind cannot be moved to its own window. */
export function windowPanelForKind(kind: TabKind): WindowPanel | null {
  return PANEL_OF_KIND[kind] ?? null;
}

const KIND_OF_PANEL: Record<WindowPanel, TabKind> = {
  chat: 'chat',
  workbench: 'file',
  review: 'diff',
  terminal: 'terminal',
  browser: 'browser',
  computer: 'computer',
  artifacts: 'artifacts',
};

/** The tab a detached window opens with. */
export function kindForWindowPanel(panel: WindowPanel): TabKind {
  return KIND_OF_PANEL[panel];
}

/** Every panel string the native shell accepts, for tests that pin the mapping to it. */
export const WINDOW_PANELS = Object.keys(KIND_OF_PANEL) as WindowPanel[];
