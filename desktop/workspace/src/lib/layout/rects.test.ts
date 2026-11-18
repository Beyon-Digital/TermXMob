import {describe, expect, it} from 'vitest';
import {defaultLayout, editorGroups, newGroup, newTab, type Group, type Layout, type Node, type Split, type Tab} from './model';
import {bodyRect, dropIndex, dropTarget, layoutRects, panelHeight, phoneStack, type Rect} from './rects';

const tab = (id: string, kind: Tab['kind'] = 'file'): Tab => ({id, kind});
const group = (id: string, ...tabs: Tab[]): Group => ({id, tabs, active: tabs[0]?.id ?? null});
const split = (id: string, dir: 'row' | 'column', children: Node[], sizes: number[]): Split => ({id, dir, children, sizes});
const layout = (root: Node, extra: Partial<Layout> = {}): Layout => ({
  ...defaultLayout(),
  root,
  focus: editorGroups(root)[0].id,
  ...extra,
});

const area = (rect: Rect) => rect.w * rect.h;

describe('layoutRects', () => {
  it('gives a single group the whole area', () => {
    const rects = layoutRects(layout(group('a', tab('1'))), 1000, 700);
    expect(rects.groups).toEqual({a: {x: 0, y: 0, w: 1000, h: 700}});
    expect(rects.handles).toEqual([]);
  });

  it('splits a row by its fractions and puts a handle on the boundary', () => {
    const rects = layoutRects(layout(split('s', 'row', [group('a', tab('1')), group('b', tab('2'))], [0.25, 0.75])), 1000, 700);
    expect(rects.groups.a).toEqual({x: 0, y: 0, w: 250, h: 700});
    expect(rects.groups.b).toEqual({x: 250, y: 0, w: 750, h: 700});
    expect(rects.handles).toHaveLength(1);
    expect(rects.handles[0]).toMatchObject({kind: 'split', split: 's', index: 0, dir: 'row', span: {start: 0, length: 1000}});
    expect(rects.handles[0].rect).toEqual({x: 246, y: 0, w: 8, h: 700});
  });

  it('splits a column and nests', () => {
    const root = split('r', 'row', [group('a', tab('1')), split('c', 'column', [group('b', tab('2')), group('c', tab('3'))], [0.4, 0.6])], [0.5, 0.5]);
    const rects = layoutRects(layout(root), 800, 600);
    expect(rects.groups.a).toEqual({x: 0, y: 0, w: 400, h: 600});
    expect(rects.groups.b).toEqual({x: 400, y: 0, w: 400, h: 240});
    expect(rects.groups.c).toEqual({x: 400, y: 240, w: 400, h: 360});
    expect(rects.handles.map(handle => handle.id).sort()).toEqual(['c:0', 'r:0']);
  });

  it('tiles the area exactly, with no gaps or overlaps, at awkward sizes', () => {
    const root = split('r', 'row', [group('a', tab('1')), group('b', tab('2')), group('c', tab('3'))], [0.333, 0.333, 0.334]);
    const rects = layoutRects(layout(root), 1001, 333);
    const total = Object.values(rects.groups).reduce((sum, rect) => sum + area(rect), 0);
    expect(total).toBe(1001 * 333);
    const xs = Object.values(rects.groups).map(rect => rect.x + rect.w);
    expect(Math.max(...xs)).toBe(1001);
  });

  it('reserves the panel at the bottom and clamps its height', () => {
    const base = layout(group('a', tab('1')), {panel: {group: group('p', tab('9', 'terminal')), size: 260, visible: true}});
    const rects = layoutRects(base, 1000, 700);
    expect(rects.groups.a).toEqual({x: 0, y: 0, w: 1000, h: 440});
    expect(rects.groups.p).toEqual({x: 0, y: 440, w: 1000, h: 260});
    expect(rects.handles.find(handle => handle.kind === 'panel')?.rect).toEqual({x: 0, y: 436, w: 1000, h: 8});
    expect(panelHeight(base, 300)).toBe(180);
    expect(panelHeight({...base, panel: {...base.panel, size: 700}}, 700)).toBe(489);
    expect(panelHeight({...base, panel: {...base.panel, visible: false}}, 700)).toBe(0);
  });

  it('omits the panel when hidden', () => {
    const rects = layoutRects(layout(group('a', tab('1'))), 1000, 700);
    expect(Object.keys(rects.groups)).toEqual(['a']);
  });

  it('gives a maximized group everything and hides the rest', () => {
    const root = split('s', 'row', [group('a', tab('1')), group('b', tab('2'))], [0.5, 0.5]);
    const rects = layoutRects(layout(root, {maximized: 'b'}), 1000, 700);
    expect(rects.groups).toEqual({b: {x: 0, y: 0, w: 1000, h: 700}});
    expect(rects.handles).toEqual([]);
  });

  it('ignores a maximize id that points at nothing', () => {
    const rects = layoutRects(layout(group('a', tab('1')), {maximized: 'gone'}), 1000, 700);
    expect(rects.groups.a).toBeDefined();
  });

  it('survives a zero-size container', () => {
    const rects = layoutRects(layout(split('s', 'row', [group('a', tab('1')), group('b', tab('2'))], [0.5, 0.5])), 0, 0);
    expect(rects.groups.a).toEqual({x: 0, y: 0, w: 0, h: 0});
  });
});

describe('bodyRect', () => {
  it('removes the tab strip from the top', () => {
    expect(bodyRect({x: 10, y: 20, w: 300, h: 200}, 36)).toEqual({x: 10, y: 56, w: 300, h: 164});
    expect(bodyRect({x: 0, y: 0, w: 300, h: 20}, 36)).toEqual({x: 0, y: 20, w: 300, h: 0});
  });
});

describe('dropTarget', () => {
  const root = split('s', 'row', [group('a', tab('1'), tab('2')), group('b', tab('3'))], [0.5, 0.5]);
  const value = layout(root, {panel: {group: group('p', tab('9', 'terminal')), size: 200, visible: true}});
  const rects = layoutRects(value, 1000, 700);
  const strip = 36;

  it('reorders over a tab strip', () => {
    expect(dropTarget(value, rects, 100, 10, strip)).toEqual({group: 'a', zone: 'center', strip: true});
  });
  it('adds to the group in the middle of the body', () => {
    expect(dropTarget(value, rects, 250, 300, strip)).toEqual({group: 'a', zone: 'center', strip: false});
  });
  it('splits the nearest edge in the outer quarter', () => {
    expect(dropTarget(value, rects, 10, 300, strip)).toMatchObject({group: 'a', zone: 'left'});
    expect(dropTarget(value, rects, 490, 300, strip)).toMatchObject({group: 'a', zone: 'right'});
    expect(dropTarget(value, rects, 250, 60, strip)).toMatchObject({group: 'a', zone: 'top'});
    expect(dropTarget(value, rects, 250, 495, strip)).toMatchObject({group: 'a', zone: 'bottom'});
    expect(dropTarget(value, rects, 990, 300, strip)).toMatchObject({group: 'b', zone: 'right'});
  });
  it('only takes the centre of the panel', () => {
    expect(dropTarget(value, rects, 500, 600, strip)).toEqual({group: 'p', zone: 'center', strip: false});
    expect(dropTarget(value, rects, 5, 690, strip)).toEqual({group: 'p', zone: 'center', strip: false});
  });
  it('is null outside every group', () => {
    expect(dropTarget(value, rects, 2000, 2000, strip)).toBeNull();
  });
});

describe('dropIndex', () => {
  const rects = [
    {left: 0, width: 100},
    {left: 100, width: 100},
    {left: 200, width: 100},
  ];
  it('picks the slot by tab midpoints', () => {
    expect(dropIndex(rects, 10)).toBe(0);
    expect(dropIndex(rects, 60)).toBe(1);
    expect(dropIndex(rects, 149)).toBe(1);
    expect(dropIndex(rects, 151)).toBe(2);
    expect(dropIndex(rects, 290)).toBe(3);
    expect(dropIndex([], 5)).toBe(0);
  });
});

describe('phoneStack', () => {
  const left = newGroup([newTab('chat'), newTab('file')]);
  const right = newGroup([newTab('browser')]);
  const base = defaultLayout();
  const value: Layout = {
    ...base,
    root: split('s', 'row', [left, right], [0.5, 0.5]),
    focus: right.id,
  };

  it('flattens every group, then the panel, in reading order', () => {
    const stack = phoneStack(value);
    expect(stack.tabs.map(item => item.tab.kind)).toEqual(['chat', 'file', 'browser', 'terminal']);
    expect(stack.tabs.map(item => item.group)).toEqual([left.id, left.id, right.id, base.panel.group.id]);
  });
  it('starts on the focused group\'s active tab', () => {
    expect(phoneStack(value).active).toBe(right.tabs[0].id);
  });
  it('prefers a tab the phone user chose, if it still exists', () => {
    expect(phoneStack(value, left.tabs[1].id).active).toBe(left.tabs[1].id);
    expect(phoneStack(value, 'gone').active).toBe(right.tabs[0].id);
  });
  it('never mutates the layout', () => {
    const before = JSON.stringify(value);
    phoneStack(value, left.tabs[0].id);
    expect(JSON.stringify(value)).toBe(before);
  });
});
