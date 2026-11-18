/**
 * The workspace layout model: a recursive split tree whose leaves are tab groups, plus a bottom panel and a side bar.
 *
 * Everything in `lib/layout/` is pure: no React, no storage, no DOM. The reducer (`reduce.ts`) edits these shapes, the
 * migration (`migrate-v1.ts`) produces them from the old preset snapshots, and `rects.ts` turns them into geometry.
 *
 * A `Group` is the only leaf. A `Split` has `children` (two or more) and `sizes`, one fraction per child summing to 1.
 */

export const TAB_KINDS = [
  'chat',
  'file',
  'diff',
  'terminal',
  'cli',
  'browser',
  'computer',
  'artifacts',
  'data',
  'studio',
  'settings',
  'projects',
  'workflows',
  'problems',
  'preview',
  'debug',
] as const;
export type TabKind = (typeof TAB_KINDS)[number];

export type Tab = {
  id: string;
  kind: TabKind;
  /** Which one: a session id, a file path, a settings category, a control-plane page, a run id. */
  ref?: string;
  pinned?: boolean;
};

export type Group = {id: string; tabs: Tab[]; active: string | null};
export type Split = {id: string; dir: 'row' | 'column'; sizes: number[]; children: Node[]};
export type Node = Split | Group;

export const SIDE_BAR_VIEWS = ['agents', 'explorer', 'search', 'scm', 'sites', 'data', 'studio'] as const;
export type SideBarView = (typeof SIDE_BAR_VIEWS)[number];

export type Layout = {
  version: 2;
  root: Node;
  /** The bottom panel: its own tab group, a height in pixels and whether it shows. */
  panel: {group: Group; size: number; visible: boolean};
  /** The side bar: which view, a width in pixels and whether it shows. */
  sideBar: {view: SideBarView; size: number; visible: boolean};
  /** The group that has keyboard focus: an editor-area group or the panel's group. */
  focus: string;
  /** The group filling the whole workspace, or `null`. */
  maximized: string | null;
};

export type NamedLayout = {id: string; name: string; layout: Layout; updated: number};
export type Book = {version: 2; current: Layout; named: NamedLayout[]};

export const LIMITS = {
  depth: 6,
  groups: 12,
  tabs: 64,
  named: 30,
  bytes: 256 * 1024,
  nameLength: 80,
  refLength: 512,
  /** No pane is ever squeezed below this fraction of its split. */
  minFraction: 0.05,
  sideBar: {min: 200, max: 420, default: 260},
  panel: {min: 120, max: 700, default: 260},
} as const;

/**
 * Kinds that can have several tabs at once, one per `ref`: a file tab per open file (`ref` is the buffer key), a terminal
 * tab per shell (`ref` is the PTY session id) and a diff tab per file side (`ref` is `staged:path` or `worktree:path`).
 * Every other kind is a singleton, because its host is one component with its
 * own state: Chat follows the selected conversation (hosting one Chat per conversation would duplicate its subscriptions
 * and drafts), the browser keeps its own inner tabs, and so on. Opening a singleton that is already open reveals it. The
 * reducer and the validator support multi-instance kinds fully (the reducer takes the set as an option).
 */
export const MULTI_INSTANCE_KINDS: ReadonlySet<TabKind> = new Set<TabKind>(['file', 'terminal', 'diff']);

/** Kinds that belong in the bottom panel by default. */
const PANEL_KINDS: ReadonlySet<TabKind> = new Set<TabKind>(['terminal', 'problems', 'preview', 'debug']);

/** Where a new tab of this kind goes when nothing says otherwise. */
export function defaultZone(kind: TabKind): 'editor' | 'panel' {
  return PANEL_KINDS.has(kind) ? 'panel' : 'editor';
}

const ID = /^[A-Za-z0-9_-]{1,64}$/;

export function newId(prefix: string): string {
  return prefix + '-' + Math.random().toString(36).slice(2, 10).padEnd(8, '0');
}

export const isSplit = (node: Node): node is Split => 'children' in node;
export const isGroup = (node: Node): node is Group => !isSplit(node);
export const isTabKind = (value: unknown): value is TabKind =>
  typeof value === 'string' && (TAB_KINDS as readonly string[]).includes(value);

export function newTab(kind: TabKind, ref?: string): Tab {
  return ref === undefined ? {id: newId('t'), kind} : {id: newId('t'), kind, ref};
}

export function newGroup(tabs: Tab[] = []): Group {
  return {id: newId('g'), tabs, active: tabs[0]?.id ?? null};
}

/** The layout a new profile starts with: side bar open, one group with the Main agent chat, no bottom panel. */
export function defaultLayout(): Layout {
  const chat = newGroup([newTab('chat')]);
  return {
    version: 2,
    root: chat,
    panel: {group: newGroup([newTab('terminal')]), size: LIMITS.panel.default, visible: false},
    sideBar: {view: 'agents', size: LIMITS.sideBar.default, visible: true},
    focus: chat.id,
    maximized: null,
  };
}

// ---- queries ---------------------------------------------------------------------------------------------------

/** Editor-area groups, left to right and top to bottom. */
export function editorGroups(node: Node): Group[] {
  return isSplit(node) ? node.children.flatMap(editorGroups) : [node];
}

/** Every group: the editor area first, then the panel's. */
export function allGroups(layout: Layout): Group[] {
  return [...editorGroups(layout.root), layout.panel.group];
}

export function findGroup(layout: Layout, id: string): Group | undefined {
  return allGroups(layout).find(group => group.id === id);
}

export function findTab(layout: Layout, id: string): {tab: Tab; group: Group; index: number} | undefined {
  for (const group of allGroups(layout)) {
    const index = group.tabs.findIndex(tab => tab.id === id);
    if (index >= 0) return {tab: group.tabs[index], group, index};
  }
  return undefined;
}

export function allTabs(layout: Layout): Tab[] {
  return allGroups(layout).flatMap(group => group.tabs);
}

export function findSplit(node: Node, id: string): Split | undefined {
  if (!isSplit(node)) return undefined;
  if (node.id === id) return node;
  for (const child of node.children) {
    const found = findSplit(child, id);
    if (found) return found;
  }
  return undefined;
}

export function treeDepth(node: Node): number {
  return isSplit(node) ? 1 + Math.max(...node.children.map(treeDepth)) : 1;
}

export function isEditorGroup(layout: Layout, id: string): boolean {
  return editorGroups(layout.root).some(group => group.id === id);
}

/** The group the user would expect a new tab in: the focused editor group, else the first one. */
export function targetEditorGroup(layout: Layout): Group {
  const groups = editorGroups(layout.root);
  return groups.find(group => group.id === layout.focus) ?? groups[0];
}

export function sameTab(a: Pick<Tab, 'kind' | 'ref'>, b: Pick<Tab, 'kind' | 'ref'>, multi: ReadonlySet<TabKind>) {
  return a.kind === b.kind && (!multi.has(a.kind) || a.ref === b.ref);
}

// ---- normalisation -----------------------------------------------------------------------------------------------

/** Fractions that sum to 1 with every pane at least `minFraction`; unusable input becomes an even split. */
export function normalizeSizes(sizes: readonly unknown[], count: number): number[] {
  const even = Array.from({length: count}, () => 1 / count);
  if (count === 0) return [];
  if (sizes.length !== count) return even;
  const values = sizes.map(value => (typeof value === 'number' && Number.isFinite(value) && value > 0 ? value : NaN));
  if (values.some(Number.isNaN)) return even;
  const total = values.reduce((sum, value) => sum + value, 0);
  let result = values.map(value => value / total);
  const floor = Math.min(LIMITS.minFraction, 1 / count);
  if (result.some(value => value < floor)) {
    // Raise the small panes to the floor and take the difference from the large ones, proportionally.
    const small = result.filter(value => value < floor).length;
    const room = result.filter(value => value >= floor).reduce((sum, value) => sum + value, 0);
    const target = 1 - small * floor;
    result = result.map(value => (value < floor ? floor : (value / room) * target));
  }
  return result;
}

/**
 * Remove empty groups (except `keep`), unwrap a split with one child and merge a split into a parent of the same
 * direction. Returns `null` when nothing is left.
 */
export function collapseTree(node: Node, keep: ReadonlySet<string> = new Set()): Node | null {
  if (isGroup(node)) return node.tabs.length === 0 && !keep.has(node.id) ? null : node;
  const kept: {node: Node; size: number}[] = [];
  node.children.forEach((child, index) => {
    const next = collapseTree(child, keep);
    if (next) kept.push({node: next, size: node.sizes[index] ?? 0});
  });
  if (kept.length === 0) return null;
  if (kept.length === 1) return kept[0].node;
  const children: Node[] = [];
  const sizes: number[] = [];
  const total = kept.reduce((sum, item) => sum + item.size, 0) || 1;
  for (const item of kept) {
    const share = item.size / total;
    const inner = item.node;
    if (isSplit(inner) && inner.dir === node.dir) {
      inner.children.forEach((grandchild, index) => {
        children.push(grandchild);
        sizes.push(share * inner.sizes[index]);
      });
    } else {
      children.push(item.node);
      sizes.push(share);
    }
  }
  return {...node, sizes: normalizeSizes(sizes, children.length), children};
}

/**
 * Bring a layout back to its invariants after an edit: no empty editor group (except the focused one), a live
 * `focus` and `maximized`, an `active` tab in every group, and never an empty root.
 */
export function normalizeLayout(layout: Layout): Layout {
  const keep = new Set([layout.focus]);
  let root = collapseTree(layout.root, keep);
  if (!root) {
    root = newGroup([newTab('chat')]);
  }
  const panelGroup = fixActive(layout.panel.group);
  const next: Layout = {
    ...layout,
    root: mapGroups(root, fixActive),
    panel: {...layout.panel, group: panelGroup, visible: layout.panel.visible && panelGroup.tabs.length > 0},
  };
  const ids = new Set(allGroups(next).map(group => group.id));
  const groups = editorGroups(next.root);
  const focus = ids.has(next.focus) ? next.focus : groups[0].id;
  const maximized = next.maximized && ids.has(next.maximized) ? next.maximized : null;
  return {...next, focus, maximized};
}

function fixActive(group: Group): Group {
  if (group.active && group.tabs.some(tab => tab.id === group.active)) return group;
  const active = group.tabs[0]?.id ?? null;
  return active === group.active ? group : {...group, active};
}

export function mapGroups(node: Node, change: (group: Group) => Group): Node {
  if (isGroup(node)) return change(node);
  let changed = false;
  const children = node.children.map(child => {
    const next = mapGroups(child, change);
    if (next !== child) changed = true;
    return next;
  });
  return changed ? {...node, children} : node;
}

// ---- validation --------------------------------------------------------------------------------------------------

export type ValidateOptions = {
  /** Whether a tab's `ref` still points at something. A tab for which it returns false is dropped. */
  resolveRef?: (tab: Tab) => boolean;
};

type Seen = {ids: Set<string>; tabs: number; groups: number; tooDeep: boolean};

function id(value: unknown, seen: Set<string>, prefix: string): string {
  const text = typeof value === 'string' && ID.test(value) && !seen.has(value) ? value : newId(prefix);
  seen.add(text);
  return text;
}

function readTab(value: unknown, seen: Seen, options: ValidateOptions): Tab | null {
  if (!value || typeof value !== 'object') return null;
  const row = value as Record<string, unknown>;
  if (!isTabKind(row.kind)) return null;
  if (row.ref !== undefined && (typeof row.ref !== 'string' || row.ref.length > LIMITS.refLength)) return null;
  const tab: Tab = {id: id(row.id, seen.ids, 't'), kind: row.kind};
  if (typeof row.ref === 'string') tab.ref = row.ref;
  if (row.pinned === true) tab.pinned = true;
  if (options.resolveRef && !options.resolveRef(tab)) return null;
  seen.tabs++;
  return tab;
}

function readGroup(value: unknown, seen: Seen, options: ValidateOptions): Group | null {
  if (!value || typeof value !== 'object') return null;
  const row = value as Record<string, unknown>;
  if (!Array.isArray(row.tabs)) return null;
  seen.groups++;
  const groupId = id(row.id, seen.ids, 'g');
  const tabs = row.tabs.flatMap(item => {
    const tab = readTab(item, seen, options);
    return tab ? [tab] : [];
  });
  return {id: groupId, tabs, active: typeof row.active === 'string' && tabs.some(tab => tab.id === row.active) ? row.active : tabs[0]?.id ?? null};
}

function readNode(value: unknown, depth: number, seen: Seen, options: ValidateOptions): Node | null {
  if (!value || typeof value !== 'object') return null;
  if (depth > LIMITS.depth) {
    seen.tooDeep = true;
    return null;
  }
  const row = value as Record<string, unknown>;
  if (!Array.isArray(row.children)) return readGroup(value, seen, options);
  if (row.dir !== 'row' && row.dir !== 'column') return null;
  const splitId = id(row.id, seen.ids, 's');
  const entries: {node: Node; size: unknown}[] = [];
  for (const [index, child] of row.children.entries()) {
    const node = readNode(child, depth + 1, seen, options);
    if (node) entries.push({node, size: Array.isArray(row.sizes) ? row.sizes[index] : undefined});
  }
  if (entries.length === 0) return null;
  const sizes = row.sizes && Array.isArray(row.sizes) && row.sizes.length === row.children.length ? entries.map(entry => entry.size) : [];
  return {id: splitId, dir: row.dir, children: entries.map(entry => entry.node), sizes: normalizeSizes(sizes, entries.length)};
}

const clamp = (value: unknown, fallback: number, min: number, max: number) =>
  typeof value === 'number' && Number.isFinite(value) ? Math.max(min, Math.min(max, Math.round(value))) : fallback;

/**
 * Check and repair a stored layout. Returns `null` when it cannot be used: a wrong version, more than 6 levels, 12
 * groups or 64 tabs, or a body of more than 256 KB. Otherwise unknown kinds and unresolvable refs are dropped, sizes are
 * normalised to sum to 1, empty groups collapse and the focus and maximize ids are made live.
 */
export function validateLayout(value: unknown, options: ValidateOptions = {}): Layout | null {
  if (!value || typeof value !== 'object') return null;
  const row = value as Record<string, unknown>;
  if (row.version !== 2) return null;
  try {
    if (JSON.stringify(value).length > LIMITS.bytes) return null;
  } catch {
    return null;
  }
  const panelRow = row.panel as Record<string, unknown> | undefined;
  const sideRow = row.sideBar as Record<string, unknown> | undefined;
  if (!panelRow || typeof panelRow !== 'object' || !sideRow || typeof sideRow !== 'object') return null;
  const seen: Seen = {ids: new Set(), tabs: 0, groups: 0, tooDeep: false};
  const root = readNode(row.root, 1, seen, options);
  const panelGroup = readGroup(panelRow.group, seen, options);
  if (!root || !panelGroup || seen.tooDeep) return null;
  if (seen.groups > LIMITS.groups || seen.tabs > LIMITS.tabs) return null;
  const layout: Layout = {
    version: 2,
    root,
    panel: {
      group: panelGroup,
      size: clamp(panelRow.size, LIMITS.panel.default, LIMITS.panel.min, LIMITS.panel.max),
      visible: panelRow.visible === true,
    },
    sideBar: {
      view: (SIDE_BAR_VIEWS as readonly unknown[]).includes(sideRow.view) ? (sideRow.view as SideBarView) : 'agents',
      size: clamp(sideRow.size, LIMITS.sideBar.default, LIMITS.sideBar.min, LIMITS.sideBar.max),
      visible: sideRow.visible !== false,
    },
    focus: typeof row.focus === 'string' ? row.focus : '',
    maximized: typeof row.maximized === 'string' ? row.maximized : null,
  };
  return normalizeLayout(layout);
}

export function validateNamed(value: unknown, options: ValidateOptions = {}): NamedLayout | null {
  if (!value || typeof value !== 'object') return null;
  const row = value as Record<string, unknown>;
  const name = typeof row.name === 'string' ? row.name.trim() : '';
  if (typeof row.id !== 'string' || !ID.test(row.id)) return null;
  if (!name || name.length > LIMITS.nameLength || /[\x00-\x1f\x7f]/.test(name)) return null;
  if (typeof row.updated !== 'number' || !Number.isFinite(row.updated)) return null;
  const layout = validateLayout(row.layout, options);
  return layout ? {id: row.id, name, layout, updated: row.updated} : null;
}

/** Check a stored book. Named layouts that fail are dropped; a bad `current` makes the whole book unusable. */
export function validateBook(value: unknown, options: ValidateOptions = {}): Book | null {
  if (!value || typeof value !== 'object') return null;
  const row = value as Record<string, unknown>;
  if (row.version !== 2) return null;
  const current = validateLayout(row.current, options);
  if (!current) return null;
  const named: NamedLayout[] = [];
  for (const item of (Array.isArray(row.named) ? row.named : []).slice(0, LIMITS.named)) {
    const entry = validateNamed(item, options);
    if (entry && !named.some(saved => saved.id === entry.id)) named.push(entry);
  }
  return {version: 2, current, named};
}
