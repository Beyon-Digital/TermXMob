import {useSyncExternalStore} from 'react';

/**
 * Which layout engine the workspace shell runs. `tree` is the tab-group IDE layout (`lib/layout/`) and is the default.
 * `legacy` is the classic dock of seven panels and eight presets, kept for one release as a rollback path.
 *
 * @deprecated The `legacy` engine (and the Settings toggle that selects it) is deprecated: removal scheduled for the
 * release after this one. It is chosen by `?shell=classic` in the address, or by the stored value `off` behind
 * "Use the classic workspace layout (deprecated)" in Settings > General.
 */

export type ShellEngine = 'legacy' | 'tree';

/** The localStorage key behind the Settings toggle. `off` selects the classic dock; anything else is the IDE layout. */
export const SHELL_ENGINE_KEY = 'termx-new-workspace-layout';
const CHANGED = 'termx-shell-engine-changed';

/** `?shell=ide` forces the IDE layout and `?shell=classic` forces the classic dock; the address wins over the toggle. */
export function urlShellEngine(search: string = location.search): ShellEngine | null {
  const value = new URLSearchParams(search).get('shell');
  if (value === 'ide') return 'tree';
  if (value === 'classic' || value === 'legacy') return 'legacy';
  return null;
}

export function storedShellEngine(storage: Pick<Storage, 'getItem'> | null = safeStorage()): ShellEngine {
  try {
    return storage?.getItem(SHELL_ENGINE_KEY) === 'off' ? 'legacy' : 'tree';
  } catch {
    return 'tree';
  }
}

export function readShellEngine(search: string = location.search, storage: Pick<Storage, 'getItem'> | null = safeStorage()): ShellEngine {
  return urlShellEngine(search) ?? storedShellEngine(storage);
}

export function writeShellEngine(engine: ShellEngine, storage: Pick<Storage, 'setItem' | 'removeItem'> | null = safeStorage()) {
  try {
    if (engine === 'legacy') storage?.setItem(SHELL_ENGINE_KEY, 'off');
    else storage?.removeItem(SHELL_ENGINE_KEY);
    memory = null;
  } catch {
    // Private mode or a full disk: the choice then lasts for this page only.
    memory = engine;
  }
  window.dispatchEvent(new Event(CHANGED));
}

function safeStorage(): Storage | null {
  try {
    return localStorage;
  } catch {
    return null;
  }
}

let memory: ShellEngine | null = null;

function snapshot(): ShellEngine {
  return urlShellEngine() ?? memory ?? storedShellEngine();
}

function subscribe(listener: () => void) {
  window.addEventListener(CHANGED, listener);
  window.addEventListener('storage', listener);
  return () => {
    window.removeEventListener(CHANGED, listener);
    window.removeEventListener('storage', listener);
  };
}

/** The engine in force now, live: the Settings toggle takes effect without a reload. */
export function useShellEngine(): ShellEngine {
  return useSyncExternalStore(subscribe, snapshot, () => 'tree');
}

/** The toggle is shown but cannot win while the address forces an engine. */
export function shellEngineLockedByUrl(): boolean {
  return urlShellEngine() !== null;
}

/** For tests: forget the in-memory choice. */
export function resetShellEngineMemory() {
  memory = null;
}
