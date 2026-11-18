import {beforeEach, describe, expect, it, vi} from 'vitest';

const tauri = vi.hoisted(() => ({
  native: true,
  handlers: [] as ((event: {payload: unknown}) => void)[],
  unlisten: vi.fn(),
}));

vi.mock('./native', () => ({nativeWorkspace: () => tauri.native}));
vi.mock('@tauri-apps/api/window', () => ({
  getCurrentWindow: () => ({
    listen: async (_name: string, handler: (event: {payload: unknown}) => void) => {
      tauri.handlers.push(handler);
      return tauri.unlisten;
    },
  }),
}));

import {NATIVE_MENU_EVENT, listenNativeMenu} from './native-menu';

beforeEach(() => {
  tauri.native = true;
  tauri.handlers = [];
  tauri.unlisten.mockClear();
});

describe('listenNativeMenu', () => {
  it('uses the event menu.rs emits', () => {
    expect(NATIVE_MENU_EVENT).toBe('termx-menu-command');
  });

  it('passes the command id of each menu event to the handler and ignores a malformed payload', async () => {
    const seen: string[] = [];
    await listenNativeMenu(id => seen.push(id));
    for (const payload of [{id: 'settings'}, {id: 3}, {}, null]) tauri.handlers[0]({payload});
    expect(seen).toEqual(['settings']);
  });

  it('can be released', async () => {
    const release = await listenNativeMenu(() => {});
    release();
    expect(tauri.unlisten).toHaveBeenCalledOnce();
  });

  it('does nothing in a browser, where there is no native menu', async () => {
    tauri.native = false;
    const release = await listenNativeMenu(() => {});
    expect(tauri.handlers).toEqual([]);
    expect(() => release()).not.toThrow();
  });
});
