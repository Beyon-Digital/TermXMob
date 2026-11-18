import {findCommand, keyedSpecs, type Command, type CommandId} from './commands';

/**
 * The registry commands the native menu offers, in menu order. `native-menu.json` is generated from this list and the
 * registry's default keys (a test fails when it is stale; regenerate with `UPDATE_NATIVE_MENU=1 pnpm test native-menu`),
 * and the Rust side builds its accelerators from that file, so a key is written down once, in `commands.ts`.
 *
 * A menu item is shown by its place in `menu.rs`; this list is only which command ids may arrive over the menu event.
 */
export const NATIVE_MENU_COMMANDS = [
  'settings',
  'lock',
  'newConversation',
  'open-project',
  'saveFile',
  'closeTab',
  'palette',
  'left',
  'bottom',
  'view-agents',
  'explorer',
  'search',
  'sourceControl',
  'fullscreen',
  'quickFile',
  'nextTab',
  'previousTab',
  'focusGroup1',
  'focusGroup2',
  'focusGroup3',
  'newTerminal',
  'focusTerminal',
  'detach-workspace',
  'return-to-main',
] as const;

const allowed = new Set<string>(NATIVE_MENU_COMMANDS);

export function isNativeMenuCommand(id: string): boolean {
  return allowed.has(id);
}

/** The command a menu event runs: none for an id the menu does not list, or while the workspace is locked. */
export function resolveMenuCommand(id: string, locked: boolean): Command | undefined {
  if (locked || !isNativeMenuCommand(id)) return undefined;
  return findCommand(id);
}

export type NativeMenuEntry = {id: string; keys?: string};

/** What `native-menu.json` holds: each menu command with the registry's default key, when it has one. */
export function nativeMenuManifest(): {commands: NativeMenuEntry[]} {
  return {
    commands: NATIVE_MENU_COMMANDS.map(id => {
      const keys = (keyedSpecs as Record<string, {defaultKeys: string} | undefined>)[id as CommandId]?.defaultKeys;
      return keys ? {id, keys} : {id};
    }),
  };
}
