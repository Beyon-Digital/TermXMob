import {LIMITS, allGroups, editorGroups, findGroup, isSplit, type Layout, type Node, type Tab} from './model';

/**
 * Geometry for a layout, as pure functions of the layout and the available size. The tab groups, the splitters and the
 * flat content layer are all placed from these numbers, so a tab's content never has to move in the DOM when its group
 * changes: only its rectangle does.
 */

export type Rect = {x: number; y: number; w: number; h: number};

export type Handle = {
  /** `split` handles resize two neighbours of a split; `panel` resizes the bottom panel. */
  kind: 'split' | 'panel';
  id: string;
  /** For a split handle: the split and the index of the child before the handle. */
  split?: string;
  index: number;
  /** `column` means the handle is a horizontal line between rows. */
  dir: 'row' | 'column';
  /** The hit area: 8px across the line. */
  rect: Rect;
  /** The extent of the split being resized along the drag axis, to turn a pointer position into a fraction. */
  span: {start: number; length: number};
};

export type Rects = {
  width: number;
  height: number;
  /** The rectangle of every group that is on screen. Hidden groups are absent. */
  groups: Record<string, Rect>;
  handles: Handle[];
};

export type RectOptions = {
  /** Hit area across a splitter. Default 8. */
  hit?: number;
};

const FLOOR = 120;

export function panelHeight(layout: Layout, height: number): number {
  if (!layout.panel.visible || height <= 0) return 0;
  const cap = Math.max(LIMITS.panel.min, Math.floor(height * 0.7));
  return Math.max(0, Math.min(layout.panel.size, cap, Math.max(0, height - FLOOR)));
}

export function layoutRects(layout: Layout, width: number, height: number, options: RectOptions = {}): Rects {
  const hit = options.hit ?? 8;
  const rects: Rects = {width, height, groups: {}, handles: []};
  const full: Rect = {x: 0, y: 0, w: width, h: height};
  if (layout.maximized && findGroup(layout, layout.maximized)) {
    rects.groups[layout.maximized] = full;
    return rects;
  }
  const bottom = panelHeight(layout, height);
  const editor: Rect = {x: 0, y: 0, w: width, h: height - bottom};
  if (bottom > 0) {
    rects.groups[layout.panel.group.id] = {x: 0, y: height - bottom, w: width, h: bottom};
    rects.handles.push({
      kind: 'panel',
      id: 'panel',
      index: 0,
      dir: 'column',
      rect: {x: 0, y: height - bottom - hit / 2, w: width, h: hit},
      span: {start: 0, length: height},
    });
  }
  place(layout.root, editor, rects, hit);
  return rects;
}

function place(node: Node, rect: Rect, rects: Rects, hit: number) {
  if (!isSplit(node)) {
    rects.groups[node.id] = rect;
    return;
  }
  const row = node.dir === 'row';
  const start = row ? rect.x : rect.y;
  const length = row ? rect.w : rect.h;
  let total = 0;
  const edges = [start];
  node.sizes.forEach((size, index) => {
    total += size;
    edges.push(index === node.sizes.length - 1 ? start + length : Math.round(start + length * total));
  });
  node.children.forEach((child, index) => {
    const from = edges[index];
    const to = edges[index + 1];
    place(
      child,
      row ? {x: from, y: rect.y, w: to - from, h: rect.h} : {x: rect.x, y: from, w: rect.w, h: to - from},
      rects,
      hit,
    );
    if (index < node.children.length - 1) {
      rects.handles.push({
        kind: 'split',
        id: node.id + ':' + index,
        split: node.id,
        index,
        dir: node.dir,
        rect: row
          ? {x: to - hit / 2, y: rect.y, w: hit, h: rect.h}
          : {x: rect.x, y: to - hit / 2, w: rect.w, h: hit},
        span: {start, length},
      });
    }
  });
}

/** The body of a group: its rectangle below the tab strip. */
export function bodyRect(rect: Rect, strip: number): Rect {
  const h = Math.max(0, rect.h - strip);
  return {x: rect.x, y: rect.y + rect.h - h, w: rect.w, h};
}

export type DropZone = 'center' | 'left' | 'right' | 'top' | 'bottom';
export type DropTarget = {
  group: string;
  zone: DropZone;
  /** True over the tab strip, where a drop reorders. */
  strip: boolean;
};

const inside = (rect: Rect, x: number, y: number) =>
  x >= rect.x && x < rect.x + rect.w && y >= rect.y && y < rect.y + rect.h;

/**
 * What a drop at (x, y) would do: over a tab strip it reorders, in the outer quarter of a body it splits that side, in
 * the middle it adds the tab to the group. The panel's group only takes the centre; it cannot be split.
 */
export function dropTarget(layout: Layout, rects: Rects, x: number, y: number, strip: number): DropTarget | null {
  for (const group of allGroups(layout)) {
    const rect = rects.groups[group.id];
    if (!rect || !inside(rect, x, y)) continue;
    if (y < rect.y + strip) return {group: group.id, zone: 'center', strip: true};
    if (!editorGroups(layout.root).some(item => item.id === group.id))
      return {group: group.id, zone: 'center', strip: false};
    const body = bodyRect(rect, strip);
    const fx = (x - body.x) / Math.max(1, body.w);
    const fy = (y - body.y) / Math.max(1, body.h);
    const edges: [DropZone, number][] = [
      ['left', fx],
      ['right', 1 - fx],
      ['top', fy],
      ['bottom', 1 - fy],
    ];
    const [zone, distance] = edges.reduce((best, edge) => (edge[1] < best[1] ? edge : best));
    return {group: group.id, zone: distance < 0.25 ? zone : 'center', strip: false};
  }
  return null;
}

/** The index a tab dropped at pointer position `x` over a strip would take, from the midpoints of the tab rectangles. */
export function dropIndex(tabRects: {left: number; width: number}[], x: number): number {
  const index = tabRects.findIndex(tab => x < tab.left + tab.width / 2);
  return index < 0 ? tabRects.length : index;
}

// ---- the phone projection ---------------------------------------------------------------------------------------

export type PhoneStack = {tabs: {tab: Tab; group: string}[]; active: string | null};

/**
 * One tab stack for a phone: every group's tabs in reading order, then the panel's. It is computed at render time and
 * never written back, so the layout a desktop window stored is the layout it gets back.
 */
export function phoneStack(layout: Layout, preferred: string | null = null): PhoneStack {
  const tabs = allGroups(layout).flatMap(group => group.tabs.map(tab => ({tab, group: group.id})));
  const focus = findGroup(layout, layout.focus);
  const own = focus?.tabs.find(tab => tab.id === focus.active)?.id ?? null;
  const active =
    [preferred, own, tabs[0]?.tab.id ?? null].find(id => id && tabs.some(item => item.tab.id === id)) ?? null;
  return {tabs, active};
}

// ---- placements: where each tab's content goes -----------------------------------------------------------------

export type Placement = {
  tab: Tab;
  group: string;
  /** The content rectangle when the tab is the active one of a group that is on screen, else `null`. */
  rect: Rect | null;
};

/** One placement per tab in the layout. Only the active tab of an on-screen group has a rectangle. */
export function placements(layout: Layout, rects: Rects, strip: number): Placement[] {
  return allGroups(layout).flatMap(group =>
    group.tabs.map(tab => {
      const frame = rects.groups[group.id];
      const body = frame && group.active === tab.id ? bodyRect(frame, strip) : null;
      return {tab, group: group.id, rect: body && body.w > 0 && body.h > 0 ? body : null};
    }),
  );
}
