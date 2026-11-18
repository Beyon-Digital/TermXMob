import {
  LIMITS,
  defaultLayout,
  newGroup,
  newId,
  newTab,
  normalizeLayout,
  normalizeSizes,
  validateLayout,
  type Book,
  type Group,
  type Layout,
  type NamedLayout,
  type Node,
  type Tab,
  type TabKind,
} from './model';

/**
 * Migration from the v1 docking model (`termx-layout-presets-v1`) to the layout tree.
 *
 * v1 placed seven single-instance panels into regions (`left`, `main`, `split`, `right`, `bottom`, `hidden`), sized by
 * pixels, with three visibility booleans. Here regions left to right become a row of groups, the bottom region becomes
 * the panel, pixels become fractions of a reference window, and the booleans become visibility. The v1 key is only ever
 * read: it stays in place so a rollback finds it unchanged.
 */

export type V1Panel = 'editor' | 'conversation' | 'review' | 'browser' | 'terminal' | 'computer' | 'artifacts';
export type V1Region = 'left' | 'main' | 'split' | 'right' | 'bottom' | 'hidden';
export type V1Layout = 'chat' | 'workbench' | 'browser' | 'computer' | 'artifacts' | 'review' | 'terminal' | 'focus';

export type V1Snapshot = {
  layout: V1Layout;
  dock: Record<V1Panel, V1Region>;
  /** The chats sidebar. (The `left` dock region is a different thing: a column of panels.) */
  left: boolean;
  right: boolean;
  bottom: boolean;
  dockGeometry: {left: number; split: number; bottom: number};
  sidebarWidth: number;
  rightWidth: number;
};

const PANELS: V1Panel[] = ['editor', 'conversation', 'review', 'browser', 'terminal', 'computer', 'artifacts'];
const REGIONS: V1Region[] = ['left', 'main', 'split', 'right', 'bottom', 'hidden'];
const LAYOUTS: V1Layout[] = ['chat', 'workbench', 'browser', 'computer', 'artifacts', 'review', 'terminal', 'focus'];

export const PANEL_KIND: Record<V1Panel, TabKind> = {
  editor: 'file',
  conversation: 'chat',
  review: 'diff',
  browser: 'browser',
  terminal: 'terminal',
  computer: 'computer',
  artifacts: 'artifacts',
};

/** The panel a v1 arrangement is about: the one that is `main` in the arrangement the layout name stands for. */
const PRIMARY: Record<V1Layout, V1Panel | null> = {
  chat: 'conversation',
  workbench: 'editor',
  browser: 'browser',
  computer: 'computer',
  artifacts: 'artifacts',
  review: 'review',
  terminal: 'terminal',
  focus: null,
};

/** A reference window for turning pixels into fractions: 1280 wide, less the activity rail and the sidebar. */
const REFERENCE_WIDTH = 1280;
const RAIL = 52;

/** The eight built-in v1 arrangements (`layouts` in `lib/docking.ts`), as the presets the migration seeds. */
const hidden = Object.fromEntries(PANELS.map(panel => [panel, 'hidden'])) as Record<V1Panel, V1Region>;
export const V1_PRESETS: Record<V1Layout, Record<V1Panel, V1Region>> = {
  chat: {...hidden, conversation: 'main'},
  workbench: {...hidden, editor: 'main', conversation: 'right'},
  browser: {...hidden, browser: 'main', conversation: 'right'},
  computer: {...hidden, computer: 'main', conversation: 'right'},
  artifacts: {...hidden, artifacts: 'main', conversation: 'right'},
  review: {...hidden, review: 'main', conversation: 'right'},
  terminal: {...hidden, terminal: 'main', conversation: 'right'},
  focus: {...hidden, editor: 'main'},
};

export const V1_PRESET_NAMES: Record<V1Layout, string> = {
  chat: 'Chat',
  workbench: 'Code + chat',
  browser: 'Browser + chat',
  computer: 'Computer + chat',
  artifacts: 'Artifacts + chat',
  review: 'Review + chat',
  terminal: 'Terminal + chat',
  focus: 'Focus',
};

const number = (value: unknown, fallback: number, min: number, max: number) =>
  typeof value === 'number' && Number.isFinite(value) ? Math.max(min, Math.min(max, value)) : fallback;

/** The arrangement a built-in v1 layout starts from (`defaultLayout` in `lib/layout-presets.ts`). */
export function v1Default(layout: V1Layout): V1Snapshot {
  return {
    layout,
    dock: {...V1_PRESETS[layout]},
    left: !['workbench', 'review'].includes(layout),
    right: true,
    bottom: true,
    dockGeometry: {left: 220, split: 420, bottom: 260},
    sidebarWidth: 240,
    rightWidth: 380,
  };
}

/** Read one stored v1 snapshot, or `null`. The same checks and clamps as v1's own `layoutSnapshot`. */
export function readV1Snapshot(value: unknown): V1Snapshot | null {
  if (!value || typeof value !== 'object') return null;
  const row = value as Record<string, unknown>;
  if (!LAYOUTS.includes(row.layout as V1Layout) || row.layout === 'focus') return null;
  const dock = row.dock as Partial<Record<V1Panel, V1Region>> | undefined;
  if (!dock || typeof dock !== 'object') return null;
  if (!(['editor', 'conversation', 'browser'] as const).every(panel => REGIONS.includes(dock[panel] as V1Region))) return null;
  for (const panel of ['computer', 'artifacts', 'review', 'terminal'] as const) {
    if (dock[panel] !== undefined && !REGIONS.includes(dock[panel] as V1Region)) return null;
  }
  const restored = Object.fromEntries(PANELS.map(panel => [panel, dock[panel] || 'hidden'])) as Record<V1Panel, V1Region>;
  // Two older shapes: the Computer and Review layouts used to be arrangements of Browser and Editor.
  if (dock.computer === undefined && row.layout === 'computer') {
    restored.computer = restored.browser;
    restored.browser = 'hidden';
  }
  if (dock.review === undefined && row.layout === 'review') {
    restored.review = 'main';
    restored.editor = 'hidden';
    restored.browser = 'hidden';
  }
  const geometry = (row.dockGeometry ?? {}) as Record<string, unknown>;
  return {
    layout: row.layout as V1Layout,
    dock: restored,
    left: row.left !== false,
    right: row.right !== false,
    bottom: row.bottom !== false,
    dockGeometry: {
      left: number(geometry.left, 220, 180, 800),
      split: number(geometry.split, 420, 180, 800),
      bottom: number(geometry.bottom, 260, 120, 700),
    },
    sidebarWidth: number(row.sidebarWidth, 244, 200, 420),
    rightWidth: number(row.rightWidth, 380, 240, 800),
  };
}

function tabFor(panel: V1Panel): Tab {
  return newTab(PANEL_KIND[panel]);
}

/** One v1 arrangement as a layout tree. Pure apart from the random ids. */
export function snapshotToLayout(snapshot: V1Snapshot): Layout {
  const primary = PRIMARY[snapshot.layout] ?? PANELS.find(panel => snapshot.dock[panel] === 'main') ?? null;
  const columns: {region: V1Region; px: number}[] = [
    {region: 'left', px: snapshot.dockGeometry.left},
    {region: 'main', px: 0},
    {region: 'split', px: snapshot.dockGeometry.split},
    {region: 'right', px: snapshot.right ? snapshot.rightWidth : 0},
  ];
  const groups: {group: Group; px: number; region: V1Region}[] = [];
  for (const column of columns) {
    if (column.region === 'right' && !snapshot.right) continue;
    const members = PANELS.filter(panel => snapshot.dock[panel] === column.region);
    if (!members.length) continue;
    const tabs = members.map(tabFor);
    const group = newGroup(tabs);
    const lead = primary && members.includes(primary) ? members.indexOf(primary) : 0;
    group.active = tabs[lead].id;
    groups.push({group, px: column.px, region: column.region});
  }
  const sidebar = snapshot.left ? snapshot.sidebarWidth + RAIL : RAIL;
  const available = Math.max(480, REFERENCE_WIDTH - sidebar);
  const fixed = groups.filter(item => item.region !== 'main').reduce((sum, item) => sum + item.px, 0);
  for (const item of groups) if (item.region === 'main') item.px = Math.max(240, available - fixed);

  let root: Node;
  if (groups.length === 0) {
    root = newGroup([newTab('chat')]);
  } else if (groups.length === 1) {
    root = groups[0].group;
  } else {
    root = {
      id: newId('s'),
      dir: 'row',
      sizes: normalizeSizes(
        groups.map(item => item.px),
        groups.length,
      ),
      children: groups.map(item => item.group),
    };
  }

  const bottomTabs = PANELS.filter(panel => snapshot.dock[panel] === 'bottom').map(tabFor);
  const panelGroup = newGroup(bottomTabs.length ? bottomTabs : [newTab('terminal')]);
  const home = groups.find(item => item.region === 'main') ?? groups[0];
  const layout: Layout = {
    version: 2,
    root,
    panel: {
      group: panelGroup,
      size: Math.round(number(snapshot.dockGeometry.bottom, LIMITS.panel.default, LIMITS.panel.min, LIMITS.panel.max)),
      visible: snapshot.bottom && bottomTabs.length > 0,
    },
    sideBar: {
      view: 'agents',
      size: Math.round(number(snapshot.sidebarWidth, LIMITS.sideBar.default, LIMITS.sideBar.min, LIMITS.sideBar.max)),
      visible: snapshot.left,
    },
    focus: home?.group.id ?? '',
    maximized: snapshot.layout === 'focus' && home ? home.group.id : null,
  };
  return normalizeLayout(layout);
}

/** A built-in v1 preset as a named layout. Focus keeps the Chat beside the editor and maximizes the editor. */
export function presetLayout(name: V1Layout): Layout {
  const base = snapshotToLayout(v1Default(name));
  if (name !== 'focus') return base;
  const editor = base.root as Group;
  const chat = newGroup([newTab('chat')]);
  return normalizeLayout({
    ...base,
    root: {id: newId('s'), dir: 'row', sizes: [0.62, 0.38], children: [editor, chat]},
    sideBar: {...base.sideBar, visible: false},
    focus: editor.id,
    maximized: editor.id,
  });
}

/** The eight built-in v1 arrangements as named layouts, in the order the v1 layout buttons listed them. */
export function builtInNamed(now = Date.now()): NamedLayout[] {
  return LAYOUTS.map(name => ({id: 'preset-' + name, name: V1_PRESET_NAMES[name], layout: presetLayout(name), updated: now}));
}

// ---- the stored v1 book ------------------------------------------------------------------------------------------

type V1Row = {id: string; name: string; snapshot: V1Snapshot; updated: number};

/**
 * Read a stored v1 book: `termx-layout-presets-v1:<json [owner, device]>`. Returns `null` when there is none, it is
 * for another owner or device, or it is unreadable. Never writes.
 */
export function readV1Book(raw: string | null, owner: string, device: string) {
  if (!raw || raw.length > 256000) return null;
  try {
    const row = JSON.parse(raw);
    if (row.version !== 1 || row.owner !== owner || row.device !== device) return null;
    const arrangements: Partial<Record<V1Layout, V1Snapshot>> = {};
    for (const name of LAYOUTS) {
      const snapshot = readV1Snapshot(row.arrangements?.[name]);
      if (snapshot?.layout === name) arrangements[name] = snapshot;
    }
    const last = LAYOUTS.includes(row.last) && row.last !== 'focus' ? (row.last as V1Layout) : null;
    const named: V1Row[] = [];
    for (const item of (Array.isArray(row.named) ? row.named : []).slice(0, LIMITS.named)) {
      const snapshot = readV1Snapshot(item?.snapshot);
      if (
        snapshot &&
        typeof item.id === 'string' &&
        item.id.length <= 128 &&
        typeof item.name === 'string' &&
        item.name.trim().length > 0 &&
        item.name.length <= LIMITS.nameLength &&
        Number.isFinite(item.updated) &&
        !named.some(saved => saved.id === item.id)
      ) {
        named.push({id: item.id, name: item.name, snapshot, updated: item.updated});
      }
    }
    return {last, arrangements, named};
  } catch {
    return null;
  }
}

/**
 * The v2 book for a profile that has none: the layout it last used (or the default), its saved layouts, and the eight
 * built-in arrangements as named layouts so nothing the v1 layout buttons offered is lost.
 */
export function migrateV1(raw: string | null, owner: string, device: string, now = Date.now()): Book {
  const old = readV1Book(raw, owner, device);
  const named = builtInNamed(now);
  let current = defaultLayout();
  if (old) {
    const snapshot = old.last ? (old.arrangements[old.last] ?? v1Default(old.last)) : undefined;
    if (snapshot) current = snapshotToLayout(snapshot);
    for (const row of old.named) {
      const layout = validateLayout(snapshotToLayout(row.snapshot));
      // Built-in names keep their built-in entry; a person's saved layout with the same name wins.
      const index = named.findIndex(item => item.name === row.name);
      if (index >= 0) named.splice(index, 1);
      if (layout && named.length < LIMITS.named) named.push({id: row.id, name: row.name, layout, updated: row.updated});
    }
  }
  return {version: 2, current: validateLayout(current) ?? defaultLayout(), named: named.slice(0, LIMITS.named)};
}
