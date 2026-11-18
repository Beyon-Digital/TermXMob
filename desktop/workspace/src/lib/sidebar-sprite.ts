import {useSyncExternalStore} from 'react';

/**
 * Whether the new workspace layout shows a character beside the Main agent row. It is off by default: the floating,
 * walking habitat belongs to the old layout, and the new one has no looping animation. The choice is per device, stored in
 * `localStorage`, and changed from Settings > Characters.
 */
export const SIDEBAR_SPRITE_KEY = 'termx-sidebar-sprite';
const CHANGED = 'termx-sidebar-sprite-changed';

export function readSidebarSprite(): boolean {
  try {
    return localStorage.getItem(SIDEBAR_SPRITE_KEY) === 'on';
  } catch {
    return false;
  }
}

export function writeSidebarSprite(on: boolean) {
  try {
    if (on) localStorage.setItem(SIDEBAR_SPRITE_KEY, 'on');
    else localStorage.removeItem(SIDEBAR_SPRITE_KEY);
  } catch {
    // Private mode: the choice lasts until the page reloads.
  }
  window.dispatchEvent(new Event(CHANGED));
}

function subscribe(onChange: () => void) {
  window.addEventListener(CHANGED, onChange);
  window.addEventListener('storage', onChange);
  return () => {
    window.removeEventListener(CHANGED, onChange);
    window.removeEventListener('storage', onChange);
  };
}

export function useSidebarSprite(): boolean {
  return useSyncExternalStore(subscribe, readSidebarSprite, () => false);
}
