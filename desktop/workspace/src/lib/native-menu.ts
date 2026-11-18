import {getCurrentWindow} from '@tauri-apps/api/window';
import type {UnlistenFn} from '@tauri-apps/api/event';
import {nativeWorkspace} from './native';

/**
 * The native menu (`desktop/src-tauri/src/menu.rs`) never runs anything itself. A menu item that belongs to the
 * workspace emits this event to the focused workspace window, carrying a command id from the registry
 * (`shell/commands.ts`); the shell runs it through the same path as the palette. In a browser there is no native menu,
 * so listening is a no-op.
 */
export const NATIVE_MENU_EVENT = 'termx-menu-command';

export async function listenNativeMenu(handler: (commandId: string) => void): Promise<UnlistenFn> {
  if (!nativeWorkspace()) return () => {};
  try {
    return await getCurrentWindow().listen<{id: string}>(NATIVE_MENU_EVENT, event => {
      const id = event.payload?.id;
      if (typeof id === 'string') handler(id);
    });
  } catch {
    // No window bridge (an embedded or test webview): the menu then simply does nothing here.
    return () => {};
  }
}
