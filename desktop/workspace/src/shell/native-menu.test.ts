import {existsSync, readFileSync, writeFileSync} from 'node:fs';
import {join} from 'node:path';
import {describe, expect, it} from 'vitest';
import {findCommand} from './commands';
import {NATIVE_MENU_COMMANDS, isNativeMenuCommand, nativeMenuManifest, resolveMenuCommand} from './native-menu';

/* `native-menu.json` is what the Rust side builds its accelerators from; `menu.rs` names the same commands in the order the
   menus show them. Regenerate the file with UPDATE_NATIVE_MENU=1 pnpm --dir desktop/workspace test native-menu */

const MANIFEST = join(process.cwd(), 'src/shell/native-menu.json');
const MENU_RS = join(process.cwd(), '../src-tauri/src/menu.rs');

function manifestText() {
  return JSON.stringify(nativeMenuManifest(), null, 2) + '\n';
}

describe('the native menu commands', () => {
  it('are all commands of the registry, each once', () => {
    expect(new Set(NATIVE_MENU_COMMANDS).size).toBe(NATIVE_MENU_COMMANDS.length);
    for (const id of NATIVE_MENU_COMMANDS) expect(findCommand(id), id).toBeDefined();
  });

  it('accept only the ids the menu lists', () => {
    expect(isNativeMenuCommand('settings')).toBe(true);
    expect(isNativeMenuCommand('stop-host')).toBe(false);
    expect(isNativeMenuCommand('')).toBe(false);
  });

  it('run only while the workspace is unlocked, and only when the menu lists them', () => {
    expect(resolveMenuCommand('newConversation', false)?.id).toBe('newConversation');
    expect(resolveMenuCommand('newConversation', true)).toBeUndefined();
    expect(resolveMenuCommand('stop-host', false)).toBeUndefined();
    expect(resolveMenuCommand('unknown', false)).toBeUndefined();
  });

  it('carry the default key of the registry, written once in native-menu.json', () => {
    if (process.env.UPDATE_NATIVE_MENU) writeFileSync(MANIFEST, manifestText());
    expect(existsSync(MANIFEST)).toBe(true);
    expect(readFileSync(MANIFEST, 'utf8')).toBe(manifestText());
    const manifest = nativeMenuManifest();
    for (const {id, keys} of manifest.commands) expect(keys, id).toBe(findCommand(id)?.defaultKeys);
  });

  it('are the commands menu.rs builds, in the same order, so the two cannot drift', () => {
    const source = readFileSync(MENU_RS, 'utf8');
    const built = [...source.matchAll(/command_item\(\s*app,\s*"([^"]+)"/g)].map(match => match[1]);
    expect(built).toEqual([...NATIVE_MENU_COMMANDS]);
  });
});
