import {existsSync, readFileSync, writeFileSync} from 'node:fs';
import {join} from 'node:path';
import {afterEach, describe, expect, it, vi} from 'vitest';
import {focusDockMap, layouts as oldPresets} from '../docking';
import {LayoutPresetStore, defaultLayout as oldDefault, layoutSnapshot as oldSnapshot, persistentLayouts} from '../layout-presets';
import {
  V1_PRESETS,
  V1_PRESET_NAMES,
  builtInNamed,
  migrateV1,
  presetLayout,
  readV1Book,
  readV1Snapshot,
  snapshotToLayout,
  v1Default,
  type V1Snapshot,
} from './migrate-v1';
import {LIMITS, allTabs, editorGroups, isSplit, validateBook, validateLayout, type Group, type Layout, type Split} from './model';
import {LayoutBookStore, V1_PREFIX, V2_PREFIX} from './store';

/* The fixtures are produced by the v1 code itself (`LayoutPresetStore`, `defaultLayout`, `focusDockMap`), never written by
   hand, and a test below fails when the committed file drifts from what that code produces. Regenerate with
   UPDATE_LAYOUT_FIXTURES=1 pnpm --dir desktop/workspace test migrate-v1 */

const FIXTURES = join(process.cwd(), 'src/lib/layout/fixtures/v1-books.json');
const OWNER = 'owner-1';
const DEVICE = 'device-0001';

class MemoryStorage implements Storage {
  private data = new Map<string, string>();
  get length() {
    return this.data.size;
  }
  clear() {
    this.data.clear();
  }
  getItem(key: string) {
    return this.data.get(key) ?? null;
  }
  key(index: number) {
    return [...this.data.keys()][index] ?? null;
  }
  removeItem(key: string) {
    this.data.delete(key);
  }
  setItem(key: string, value: string) {
    this.data.set(key, value);
  }
}

type Fixtures = {
  owner: string;
  device: string;
  /** What the v1 `layouts` table maps each built-in name to. */
  presets: Record<string, Record<string, string>>;
  /** The arrangement a layout button opens: the v1 `defaultLayout(name)`. */
  snapshots: Record<string, V1Snapshot>;
  /** The raw localStorage value `termx-layout-presets-v1:...` after remembering that arrangement. */
  books: Record<string, string>;
};

function buildFixtures(): Fixtures {
  // The v1 store stamps saved layouts with the clock and a random id; pin both so the fixture is reproducible.
  vi.spyOn(Date, 'now').mockReturnValue(1_700_000_000_000);
  vi.spyOn(crypto, 'randomUUID').mockReturnValue('00000000-0000-4000-8000-000000000001');
  try {
    return buildFixturesPinned();
  } finally {
    vi.restoreAllMocks();
  }
}

function buildFixturesPinned(): Fixtures {
  const presets = JSON.parse(JSON.stringify(oldPresets));
  const snapshots: Record<string, V1Snapshot> = {};
  const books: Record<string, string> = {};
  for (const name of persistentLayouts) {
    const storage = new MemoryStorage();
    const store = new LayoutPresetStore(OWNER, storage, DEVICE);
    store.remember(oldDefault(name));
    snapshots[name] = JSON.parse(JSON.stringify(oldDefault(name)));
    books[name] = storage.getItem(store.key)!;
  }
  // Focus mode is a temporary arrangement; it is never stored, but it is what the Focus preset and toggle produce.
  snapshots.focus = JSON.parse(
    JSON.stringify({...oldDefault('chat'), layout: 'focus', dock: focusDockMap('editor'), left: false, right: false, bottom: false}),
  );
  // A customised profile: Workbench with the terminal docked at the bottom and the right column hidden, Chat with
  // the browser on the right, a saved layout, last used Workbench.
  const storage = new MemoryStorage();
  const store = new LayoutPresetStore(OWNER, storage, DEVICE);
  store.remember({...oldDefault('chat'), dock: {...oldDefault('chat').dock, browser: 'right'}, rightWidth: 500});
  store.save('Review layout', {...oldDefault('review'), bottom: false, dock: {...oldDefault('review').dock, editor: 'split'}});
  store.remember({
    ...oldDefault('workbench'),
    dock: {...oldDefault('workbench').dock, terminal: 'bottom', review: 'split', conversation: 'right'},
    right: true,
    left: true,
    dockGeometry: {left: 300, split: 500, bottom: 320},
    sidebarWidth: 300,
    rightWidth: 360,
  });
  books.custom = storage.getItem(store.key)!;
  return {owner: OWNER, device: DEVICE, presets, snapshots, books};
}

const fixtures: Fixtures = (() => {
  const fresh = buildFixtures();
  if (process.env.UPDATE_LAYOUT_FIXTURES || !existsSync(FIXTURES)) writeFileSync(FIXTURES, JSON.stringify(fresh, null, 2) + '\n');
  return JSON.parse(readFileSync(FIXTURES, 'utf8'));
})();

const kinds = (group: Group) => group.tabs.map(tab => tab.kind);
const rowOf = (layout: Layout) => layout.root as Split;
const sum = (values: number[]) => values.reduce((total, value) => total + value, 0);

afterEach(() => vi.restoreAllMocks());

describe('fixtures', () => {
  it('match what the v1 code produces', () => {
    expect(fixtures).toEqual(buildFixtures());
  });

  it('cover all eight built-in presets', () => {
    expect(Object.keys(fixtures.presets).sort()).toEqual(['artifacts', 'browser', 'chat', 'computer', 'focus', 'review', 'terminal', 'workbench']);
    expect(Object.keys(fixtures.snapshots).sort()).toEqual(Object.keys(fixtures.presets).sort());
  });

  it('agree with the dock maps the migration seeds its presets from', () => {
    for (const [name, dock] of Object.entries(fixtures.presets)) expect(V1_PRESETS[name as keyof typeof V1_PRESETS]).toEqual(dock);
  });
});

describe('readV1Snapshot', () => {
  it('reads every stored snapshot the way the v1 reader does', () => {
    for (const name of persistentLayouts) {
      const stored = JSON.parse(JSON.parse(fixtures.books[name]) && fixtures.books[name]).arrangements[name];
      expect(readV1Snapshot(stored)).toEqual(oldSnapshot(stored));
    }
  });

  it('rejects what the v1 reader rejects', () => {
    for (const value of [null, 3, {}, {layout: 'chat'}, {layout: 'nope', dock: {}}, {layout: 'focus', dock: V1_PRESETS.focus}, {layout: 'chat', dock: {editor: 'x', conversation: 'main', browser: 'hidden'}}]) {
      expect(readV1Snapshot(value)).toBeNull();
      expect(oldSnapshot(value)).toBeNull();
    }
  });

  it('applies the same clamps and the two older shapes', () => {
    const odd = {layout: 'computer', dock: {editor: 'hidden', conversation: 'right', browser: 'main'}, sidebarWidth: 5000, rightWidth: 1, dockGeometry: {left: 1, split: 9999, bottom: 0}};
    expect(readV1Snapshot(odd)).toEqual(oldSnapshot(odd));
    const review = {layout: 'review', dock: {editor: 'main', conversation: 'right', browser: 'split'}};
    expect(readV1Snapshot(review)).toEqual(oldSnapshot(review));
  });
});

describe('snapshotToLayout: the eight built-in arrangements', () => {
  it('Chat is one group holding the chat', () => {
    const layout = snapshotToLayout(fixtures.snapshots.chat);
    expect(isSplit(layout.root)).toBe(false);
    expect(kinds(layout.root as Group)).toEqual(['chat']);
    expect(layout.sideBar).toMatchObject({visible: true, view: 'agents'});
    expect(layout.panel.visible).toBe(false);
    expect(layout.maximized).toBeNull();
  });

  it.each([
    ['workbench', 'file', false],
    ['browser', 'browser', true],
    ['computer', 'computer', true],
    ['artifacts', 'artifacts', true],
    ['review', 'diff', false],
    ['terminal', 'terminal', true],
  ])('%s is a row of %s then the chat on the right', (name, main, sidebar) => {
    const layout = snapshotToLayout(fixtures.snapshots[name]);
    const row = rowOf(layout);
    expect(row.dir).toBe('row');
    expect(row.children.map(child => kinds(child as Group))).toEqual([[main], ['chat']]);
    expect(layout.focus).toBe((row.children[0] as Group).id);
    expect(layout.sideBar.visible).toBe(sidebar);
    expect(sum(row.sizes)).toBeCloseTo(1);
    // The 380px right column becomes a fraction of the reference window, a bit under a third.
    expect(row.sizes[1]).toBeGreaterThan(0.25);
    expect(row.sizes[1]).toBeLessThan(0.4);
  });

  it('Focus (the only main panel, everything else hidden) maximizes its group', () => {
    const layout = snapshotToLayout(fixtures.snapshots.focus);
    expect(kinds(layout.root as Group)).toEqual(['file']);
    expect(layout.maximized).toBe((layout.root as Group).id);
    expect(layout.sideBar.visible).toBe(false);
  });

  it('every converted arrangement validates unchanged', () => {
    for (const snapshot of Object.values(fixtures.snapshots)) {
      const layout = snapshotToLayout(snapshot);
      expect(validateLayout(layout)).toEqual(layout);
    }
  });
});

describe('snapshotToLayout: geometry and visibility', () => {
  const base = v1Default('workbench');

  it('turns pixel widths into fractions of the reference window', () => {
    const layout = snapshotToLayout({...base, left: false, rightWidth: 400});
    // 1280 - 52 rail = 1228; the main column takes 1228 - 400.
    expect(rowOf(layout).sizes[0]).toBeCloseTo(828 / 1228, 2);
    expect(rowOf(layout).sizes[1]).toBeCloseTo(400 / 1228, 2);
  });

  it('subtracts the sidebar when it is open', () => {
    const open = snapshotToLayout({...base, left: true, sidebarWidth: 300});
    expect(open.sideBar).toMatchObject({visible: true, size: 300});
    expect(rowOf(open).sizes[1]).toBeCloseTo(380 / (1280 - 52 - 300), 2);
  });

  it('puts left, main, split and right regions in a row in that order', () => {
    const snapshot: V1Snapshot = {...base, dock: {...base.dock, editor: 'left', conversation: 'right', review: 'split', browser: 'main', terminal: 'bottom'}, left: false};
    const row = rowOf(snapshotToLayout(snapshot));
    expect(row.children.map(child => kinds(child as Group))).toEqual([['file'], ['browser'], ['diff'], ['chat']]);
    expect(sum(row.sizes)).toBeCloseTo(1);
  });

  it('puts several panels of one region in one tab group, active on the layout\'s own panel', () => {
    const snapshot: V1Snapshot = {...base, dock: {...base.dock, editor: 'main', browser: 'main', review: 'main'}};
    const main = rowOf(snapshotToLayout(snapshot)).children[0] as Group;
    expect(kinds(main)).toEqual(['file', 'diff', 'browser']);
    expect(main.tabs.find(tab => tab.id === main.active)?.kind).toBe('file');
  });

  it('maps the bottom region to the panel, its height and its visibility', () => {
    const snapshot: V1Snapshot = {...base, dock: {...base.dock, terminal: 'bottom', artifacts: 'bottom'}, bottom: true, dockGeometry: {...base.dockGeometry, bottom: 320}};
    const layout = snapshotToLayout(snapshot);
    expect(kinds(layout.panel.group)).toEqual(['terminal', 'artifacts']);
    expect(layout.panel).toMatchObject({visible: true, size: 320});
    expect(snapshotToLayout({...snapshot, bottom: false}).panel.visible).toBe(false);
  });

  it('hides the panel when nothing was docked at the bottom, but keeps a terminal ready in it', () => {
    const layout = snapshotToLayout({...base, bottom: true});
    expect(layout.panel.visible).toBe(false);
    expect(kinds(layout.panel.group)).toEqual(['terminal']);
  });

  it('drops the right column when it was hidden', () => {
    const layout = snapshotToLayout({...base, right: false});
    expect(isSplit(layout.root)).toBe(false);
    expect(kinds(layout.root as Group)).toEqual(['file']);
  });

  it('falls back to a chat when no panel is anywhere', () => {
    const layout = snapshotToLayout({...base, dock: V1_PRESETS.focus && Object.fromEntries(Object.keys(base.dock).map(key => [key, 'hidden'])) as V1Snapshot['dock']});
    expect(kinds(layout.root as Group)).toEqual(['chat']);
  });
});

describe('built-in presets as named layouts', () => {
  const named = builtInNamed(1);

  it('become eight named layouts with the v1 intent', () => {
    expect(named.map(item => item.name)).toEqual(Object.values(V1_PRESET_NAMES));
    expect(named.map(item => item.id)).toEqual(Object.keys(V1_PRESET_NAMES).map(name => 'preset-' + name));
    expect(named[0].name).toBe('Chat');
    expect(named[1].name).toBe('Code + chat');
  });

  it('are valid and carry the same tabs as the v1 arrangement', () => {
    for (const item of named) {
      expect(validateLayout(item.layout)).toEqual(item.layout);
    }
    const byName = Object.fromEntries(named.map(item => [item.name, item.layout]));
    expect(editorGroups(byName['Code + chat'].root).map(kinds)).toEqual([['file'], ['chat']]);
    expect(editorGroups(byName['Terminal + chat'].root).map(kinds)).toEqual([['terminal'], ['chat']]);
    expect(editorGroups(byName['Review + chat'].root).map(kinds)).toEqual([['diff'], ['chat']]);
  });

  it('Focus is the editor maximized beside the chat, with no side bar', () => {
    const focus = presetLayout('focus');
    expect(editorGroups(focus.root).map(kinds)).toEqual([['file'], ['chat']]);
    expect(focus.maximized).toBe(editorGroups(focus.root)[0].id);
    expect(focus.sideBar.visible).toBe(false);
  });
});

describe('migrateV1', () => {
  it('without a v1 book gives the default layout and the built-in presets', () => {
    const book = migrateV1(null, OWNER, DEVICE);
    expect(kinds(book.current.root as Group)).toEqual(['chat']);
    expect(book.current.sideBar).toEqual({view: 'agents', size: 260, visible: true});
    expect(book.named).toHaveLength(8);
    expect(validateBook(book)).not.toBeNull();
  });

  it('opens the layout the profile last used', () => {
    const book = migrateV1(fixtures.books.custom, OWNER, DEVICE);
    const groups = editorGroups(book.current.root);
    // Workbench: editor main, review split, conversation right; terminal at the bottom, shown.
    expect(groups.map(kinds)).toEqual([['file'], ['diff'], ['chat']]);
    expect(book.current.panel.visible).toBe(true);
    expect(kinds(book.current.panel.group)).toEqual(['terminal']);
    expect(book.current.panel.size).toBe(320);
    expect(book.current.sideBar).toMatchObject({visible: true, size: 300});
    expect(book.current.focus).toBe(groups[0].id);
  });

  it('keeps a person\'s saved layouts next to the built-in ones', () => {
    const book = migrateV1(fixtures.books.custom, OWNER, DEVICE);
    expect(book.named.map(item => item.name)).toContain('Review layout');
    expect(book.named).toHaveLength(9);
    const mine = book.named.find(item => item.name === 'Review layout')!;
    expect(editorGroups(mine.layout.root).map(kinds)).toEqual([['diff'], ['file'], ['chat']]);
    expect(mine.layout.panel.visible).toBe(false);
  });

  it('a saved layout replaces the built-in one of the same name', () => {
    const storage = new MemoryStorage();
    const store = new LayoutPresetStore(OWNER, storage, DEVICE);
    store.save('Chat', {...oldDefault('browser')});
    const book = migrateV1(storage.getItem(store.key), OWNER, DEVICE);
    expect(book.named.filter(item => item.name === 'Chat')).toHaveLength(1);
    expect(editorGroups(book.named.find(item => item.name === 'Chat')!.layout.root).map(kinds)).toEqual([['browser'], ['chat']]);
  });

  it('migrates each stored default arrangement to the same layout as converting the snapshot', () => {
    for (const name of persistentLayouts) {
      const book = migrateV1(fixtures.books[name], OWNER, DEVICE);
      expect(editorGroups(book.current.root).map(kinds)).toEqual(editorGroups(snapshotToLayout(fixtures.snapshots[name]).root).map(kinds));
    }
  });

  it('ignores a book for another owner or device, or corrupt data', () => {
    for (const raw of [fixtures.books.custom, '{', '"x"', JSON.stringify({version: 1})]) {
      const book = migrateV1(raw, 'someone-else', DEVICE);
      expect(kinds(book.current.root as Group)).toEqual(['chat']);
      expect(book.named).toHaveLength(8);
    }
    expect(migrateV1(fixtures.books.custom, OWNER, 'other-device-1').named).toHaveLength(8);
  });

  it('never exceeds 30 named layouts', () => {
    const storage = new MemoryStorage();
    const store = new LayoutPresetStore(OWNER, storage, DEVICE);
    for (let index = 0; index < 30; index++) store.save('mine ' + index, oldDefault('chat'));
    const book = migrateV1(storage.getItem(store.key), OWNER, DEVICE);
    expect(book.named.length).toBeLessThanOrEqual(LIMITS.named);
  });

  it('reads the same snapshots the v1 store does', () => {
    const storage = new MemoryStorage();
    const store = new LayoutPresetStore(OWNER, storage, DEVICE);
    storage.setItem(store.key, fixtures.books.custom);
    const old = store.read();
    const mine = readV1Book(fixtures.books.custom, OWNER, DEVICE)!;
    expect(mine.last).toBe(old.last);
    expect(mine.named.map(item => item.name)).toEqual(old.named.map(item => item.name));
    for (const name of persistentLayouts) expect(mine.arrangements[name]).toEqual(old.arrangements[name]);
  });
});

describe('LayoutBookStore', () => {
  it('keys the book like the v1 presets: prefix plus [owner, device]', () => {
    const store = new LayoutBookStore(OWNER, new MemoryStorage(), DEVICE);
    expect(store.key).toBe(V2_PREFIX + JSON.stringify([OWNER, DEVICE]));
    expect(store.v1Key).toBe(V1_PREFIX + JSON.stringify([OWNER, DEVICE]));
    expect(store.key.startsWith('termx-layout-v2:')).toBe(true);
  });

  it('reads the v1 book when there is no v2 book, without writing anything', () => {
    const storage = new MemoryStorage();
    const old = new LayoutPresetStore(OWNER, storage, DEVICE);
    storage.setItem(old.key, fixtures.books.custom);
    const store = new LayoutBookStore(OWNER, storage, DEVICE);
    const book = store.read();
    expect(editorGroups(book.current.root).map(kinds)).toEqual([['file'], ['diff'], ['chat']]);
    expect(storage.getItem(store.key)).toBeNull();
    expect(storage.getItem(old.key)).toBe(fixtures.books.custom);
  });

  it('round-trips a book and leaves the v1 key alone', () => {
    const storage = new MemoryStorage();
    storage.setItem(V1_PREFIX + JSON.stringify([OWNER, DEVICE]), fixtures.books.custom);
    const store = new LayoutBookStore(OWNER, storage, DEVICE);
    const book = store.read();
    expect(store.write(book)).toBe(true);
    expect(store.read()).toEqual(book);
    expect(storage.getItem(store.v1Key)).toBe(fixtures.books.custom);
  });

  it('prefers a valid v2 book over the v1 one', () => {
    const storage = new MemoryStorage();
    storage.setItem(V1_PREFIX + JSON.stringify([OWNER, DEVICE]), fixtures.books.custom);
    const store = new LayoutBookStore(OWNER, storage, DEVICE);
    const fresh = migrateV1(null, OWNER, DEVICE);
    store.write(fresh);
    expect(kinds(store.read().current.root as Group)).toEqual(['chat']);
  });

  it('falls back to a usable layout when the stored book is corrupt', () => {
    const storage = new MemoryStorage();
    const store = new LayoutBookStore(OWNER, storage, DEVICE);
    storage.setItem(store.key, '{not json');
    expect(kinds(store.read().current.root as Group)).toEqual(['chat']);
    storage.setItem(store.key, JSON.stringify({version: 2, current: {}, named: []}));
    expect(kinds(store.read().current.root as Group)).toEqual(['chat']);
  });

  it('drops tabs whose ref no longer resolves when asked to', () => {
    const storage = new MemoryStorage();
    const writer = new LayoutBookStore(OWNER, storage, DEVICE);
    const book = writer.read();
    const withFile = {...book, current: {...book.current, root: {...(book.current.root as Group), tabs: [...(book.current.root as Group).tabs, {id: 'f1', kind: 'file' as const, ref: '/gone.ts'}]}}};
    writer.write(withFile);
    const reader = new LayoutBookStore(OWNER, storage, DEVICE, {resolveRef: tab => tab.ref !== '/gone.ts'});
    expect(allTabs(reader.read().current).map(tab => tab.kind)).toEqual(['chat', 'terminal']);
  });

  it('write refuses a book over 256 KB and never throws', () => {
    const storage = new MemoryStorage();
    const store = new LayoutBookStore(OWNER, storage, DEVICE);
    const book = store.read();
    const big = {...book, named: Array.from({length: 30}, (_, index) => ({id: 'n' + index, name: 'x'.repeat(70) + index, layout: book.current, updated: 1}))};
    expect(typeof store.write(big)).toBe('boolean');
    const throwing = new LayoutBookStore(OWNER, {...storage, getItem: () => null, setItem: () => {throw new Error('quota')}} as unknown as Storage, DEVICE);
    expect(throwing.write(book)).toBe(false);
  });

  it('shares the device id with the v1 presets', () => {
    const storage = new MemoryStorage();
    const old = new LayoutPresetStore(OWNER, storage);
    const store = new LayoutBookStore(OWNER, storage);
    expect(store.device).toBe(old.device);
  });
});
