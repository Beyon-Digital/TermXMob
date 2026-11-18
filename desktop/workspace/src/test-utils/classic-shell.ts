import {SHELL_ENGINE_KEY} from '../lib/shell-engine';

/**
 * Pins the deprecated classic dock for a test file that exercises it. The IDE layout is the default; the classic code is
 * kept for one release, so its behaviour keeps its tests until it is removed. Call it after `localStorage.clear()`.
 */
export function pinClassicShell() {
  localStorage.setItem(SHELL_ENGINE_KEY, 'off');
}
