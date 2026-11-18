import {describe, expect, it} from 'vitest';
import {
  LIMITS,
  TAB_KINDS,
  allGroups,
  allTabs,
  collapseTree,
  defaultLayout,
  editorGroups,
  isSplit,
  normalizeLayout,
  normalizeSizes,
  treeDepth,
  validateBook,
  validateLayout,
  validateNamed,
  type Group,
  type Layout,
  type Node,
  type Split,
  type Tab,
} from './model';

const tab = (id: string, kind: Tab['kind'] = 'file', ref?: string): Tab => (ref === undefined ? {id, kind} : {id, kind, ref});
const group = (id: string, ...tabs: Tab[]): Group => ({id, tabs, active: tabs[0]?.id ?? null});
const split = (id: string, dir: 'row' | 'column', children: Node[], sizes?: number[]): Split => ({
  id,
  dir,
  children,
  sizes: sizes ?? children.map(() => 1 / children.length),
});
const layout = (root: Node, extra: Partial<Layout> = {}): Layout => ({
  version: 2,
  root,
  panel: {group: group('panel'), size: 260, visible: false},
  sideBar: {view: 'agents', size: 260, visible: true},
  focus: editorGroups(root)[0].id,
  maximized: null,
  ...extra,
});

describe('defaultLayout', () => {
  it('opens the side bar, one chat group and a hidden panel', () => {
    const value = defaultLayout();
    expect(isSplit(value.root)).toBe(false);
    const root = value.root as Group;
    expect(root.tabs.map(item => item.kind)).toEqual(['chat']);
    expect(root.active).toBe(root.tabs[0].id);
    expect(value.sideBar).toEqual({view: 'agents', size: 260, visible: true});
    expect(value.panel.visible).toBe(false);
    expect(value.maximized).toBeNull();
    expect(value.focus).toBe(root.id);
  });

  it('is a fixed point of validation', () => {
    const value = defaultLayout();
    expect(validateLayout(value)).toEqual(value);
  });

  it('knows sixteen kinds: the thirteen panels and pages plus the bottom panel\'s Problems, Preview and Run and debug', () => {
    expect(TAB_KINDS).toHaveLength(16);
  });
});

describe('normalizeSizes', () => {
  it('keeps fractions that already sum to one', () => {
    expect(normalizeSizes([0.25, 0.75], 2)).toEqual([0.25, 0.75]);
  });
  it('rescales to sum to one', () => {
    const sizes = normalizeSizes([2, 6], 2);
    expect(sizes[0]).toBeCloseTo(0.25);
    expect(sizes[1]).toBeCloseTo(0.75);
  });
  it('falls back to an even split for bad input', () => {
    expect(normalizeSizes([1], 2)).toEqual([0.5, 0.5]);
    expect(normalizeSizes([1, NaN], 2)).toEqual([0.5, 0.5]);
    expect(normalizeSizes([1, -1], 2)).toEqual([0.5, 0.5]);
    expect(normalizeSizes([0, 1], 2)).toEqual([0.5, 0.5]);
    expect(normalizeSizes(['a', 1] as unknown[], 2)).toEqual([0.5, 0.5]);
  });
  it('never squeezes a pane below the floor and still sums to one', () => {
    const sizes = normalizeSizes([0.001, 0.999], 2);
    expect(sizes[0]).toBeCloseTo(LIMITS.minFraction);
    expect(sizes[0] + sizes[1]).toBeCloseTo(1);
  });
});

describe('collapseTree', () => {
  it('removes empty groups and unwraps a one-child split', () => {
    const result = collapseTree(split('s', 'row', [group('a', tab('1')), group('b')]));
    expect(result).toEqual(group('a', tab('1')));
  });
  it('merges a split into a parent of the same direction, scaling sizes', () => {
    const inner = split('in', 'row', [group('b', tab('2')), group('c', tab('3'))], [0.5, 0.5]);
    const result = collapseTree(split('out', 'row', [group('a', tab('1')), inner], [0.5, 0.5])) as Split;
    expect(result.children.map(item => item.id)).toEqual(['a', 'b', 'c']);
    expect(result.sizes[0]).toBeCloseTo(0.5);
    expect(result.sizes[1]).toBeCloseTo(0.25);
    expect(result.sizes[2]).toBeCloseTo(0.25);
  });
  it('keeps a split of a different direction', () => {
    const inner = split('in', 'column', [group('b', tab('2')), group('c', tab('3'))]);
    const result = collapseTree(split('out', 'row', [group('a', tab('1')), inner])) as Split;
    expect(result.children).toHaveLength(2);
    expect((result.children[1] as Split).dir).toBe('column');
  });
  it('returns null when nothing is left', () => {
    expect(collapseTree(split('s', 'row', [group('a'), group('b')]))).toBeNull();
  });
  it('can keep a named empty group', () => {
    expect(collapseTree(group('a'), new Set(['a']))).toEqual(group('a'));
  });
});

describe('normalizeLayout', () => {
  it('falls back to a chat tab when the root would be empty', () => {
    const value = normalizeLayout(layout(group('a'), {focus: 'nope'}));
    const root = editorGroups(value.root);
    expect(root).toHaveLength(1);
    expect(root[0].tabs.map(item => item.kind)).toEqual(['chat']);
    expect(value.focus).toBe(root[0].id);
  });
  it('repairs a dangling active tab, focus and maximize', () => {
    const value = normalizeLayout(
      layout({id: 'a', tabs: [tab('1'), tab('2')], active: 'ghost'}, {focus: 'gone', maximized: 'gone'}),
    );
    expect((value.root as Group).active).toBe('1');
    expect(value.focus).toBe('a');
    expect(value.maximized).toBeNull();
  });
  it('hides a panel that has no tabs', () => {
    const value = normalizeLayout(layout(group('a', tab('1')), {panel: {group: group('p'), size: 200, visible: true}}));
    expect(value.panel.visible).toBe(false);
  });
});

describe('validateLayout', () => {
  const valid = () => layout(split('s', 'row', [group('a', tab('1', 'chat')), group('b', tab('2', 'file'))], [0.3, 0.7]));

  it('accepts a valid layout unchanged', () => {
    expect(validateLayout(valid())).toEqual(valid());
  });

  it('rejects non-layouts and other versions', () => {
    expect(validateLayout(null)).toBeNull();
    expect(validateLayout('x')).toBeNull();
    expect(validateLayout({})).toBeNull();
    expect(validateLayout({...valid(), version: 1})).toBeNull();
    expect(validateLayout({...valid(), root: 4})).toBeNull();
    expect(validateLayout({...valid(), panel: undefined})).toBeNull();
    expect(validateLayout({...valid(), sideBar: 3})).toBeNull();
  });

  it('survives a JSON round trip', () => {
    const value = defaultLayout();
    expect(validateLayout(JSON.parse(JSON.stringify(value)))).toEqual(value);
  });

  it('drops tabs of an unknown kind', () => {
    const value = layout(group('a', tab('1', 'chat'), {id: '2', kind: 'hologram' as never}));
    const result = validateLayout(value)!;
    expect(allTabs(result).map(item => item.kind)).toEqual(['chat']);
  });

  it('drops tabs whose ref is not a short string', () => {
    const value = layout(group('a', tab('1', 'chat'), {id: '2', kind: 'file', ref: 7 as never}, tab('3', 'file', 'x'.repeat(513))));
    expect(allTabs(validateLayout(value)!).map(item => item.id)).toEqual(['1']);
  });

  it('drops tabs whose ref no longer resolves', () => {
    const value = layout(group('a', tab('1', 'chat'), tab('2', 'file', '/gone.ts'), tab('3', 'file', '/here.ts')));
    const result = validateLayout(value, {resolveRef: item => item.ref !== '/gone.ts'})!;
    expect(allTabs(result).map(item => item.id)).toEqual(['1', '3']);
  });

  it('collapses a group that lost all its tabs and a split that lost a child', () => {
    const value = layout(split('s', 'row', [group('a', tab('1', 'chat')), group('b', {id: '2', kind: 'nope' as never})]));
    const result = validateLayout(value)!;
    expect(isSplit(result.root)).toBe(false);
    expect((result.root as Group).id).toBe('a');
  });

  it('normalises sizes to sum to one', () => {
    const value = layout(split('s', 'row', [group('a', tab('1')), group('b', tab('2'))], [3, 1]));
    const result = validateLayout(value)!.root as Split;
    expect(result.sizes[0]).toBeCloseTo(0.75);
    expect(result.sizes.reduce((sum, size) => sum + size, 0)).toBeCloseTo(1);
  });

  it('replaces broken sizes by an even split', () => {
    const value = layout(split('s', 'row', [group('a', tab('1')), group('b', tab('2'))], [1]));
    expect((validateLayout(value)!.root as Split).sizes).toEqual([0.5, 0.5]);
  });

  it('gives a duplicate tab or group id a fresh one', () => {
    const value = layout(split('s', 'row', [group('a', tab('x')), group('a', tab('x'))]));
    const result = validateLayout(value)!;
    const ids = [...allGroups(result).map(item => item.id), ...allTabs(result).map(item => item.id)];
    expect(new Set(ids).size).toBe(ids.length);
  });

  it('clamps the side bar and panel sizes and defaults a bad view', () => {
    const result = validateLayout({
      ...valid(),
      sideBar: {view: 'nonsense', size: 9999, visible: false},
      panel: {group: group('p', tab('9', 'terminal')), size: 1, visible: true},
    })!;
    expect(result.sideBar).toEqual({view: 'agents', size: LIMITS.sideBar.max, visible: false});
    expect(result.panel.size).toBe(LIMITS.panel.min);
    expect(result.panel.visible).toBe(true);
  });

  describe('limits', () => {
    const nest = (levels: number): Node => {
      let node: Node = group('leaf', tab('leaf-tab'));
      for (let level = 0; level < levels; level++) {
        node = split('s' + level, level % 2 ? 'row' : 'column', [group('g' + level, tab('t' + level)), node]);
      }
      return node;
    };
    it('allows a tree 6 deep and rejects 7', () => {
      expect(treeDepth(nest(5))).toBe(6);
      expect(validateLayout(layout(nest(5)))).not.toBeNull();
      expect(treeDepth(nest(6))).toBe(7);
      expect(validateLayout(layout(nest(6)))).toBeNull();
    });

    it('allows 12 groups and rejects 13', () => {
      const row = (count: number) =>
        split('s', 'row', Array.from({length: count}, (_, index) => group('g' + index, tab('t' + index))));
      // The panel's group counts too: 11 editor groups plus the panel's is 12.
      expect(validateLayout(layout(row(11)))).not.toBeNull();
      expect(validateLayout(layout(row(12)))).toBeNull();
    });

    it('allows 64 tabs and rejects 65', () => {
      const tabs = (count: number) => Array.from({length: count}, (_, index) => tab('t' + index, 'file', '/f' + index));
      expect(validateLayout(layout(group('a', ...tabs(64))))).not.toBeNull();
      expect(validateLayout(layout(group('a', ...tabs(65))))).toBeNull();
    });

    it('rejects a body of more than 256 KB', () => {
      const huge = {...layout(group('a', tab('1'))), extra: 'y'.repeat(300 * 1024)};
      expect(validateLayout(huge)).toBeNull();
      const fine = {...layout(group('a', tab('1'))), extra: 'y'.repeat(1024)};
      expect(validateLayout(fine)).not.toBeNull();
    });

    it('rejects a cyclic value instead of throwing', () => {
      const cyclic: Record<string, unknown> = {...layout(group('a', tab('1')))};
      cyclic.self = cyclic;
      expect(validateLayout(cyclic)).toBeNull();
    });
  });
});

describe('validateNamed and validateBook', () => {
  const named = (id: string, name: string) => ({id, name, layout: defaultLayout(), updated: 5});

  it('accepts a named layout and trims its name', () => {
    expect(validateNamed(named('a', '  Mine  '))?.name).toBe('Mine');
  });

  it('rejects bad ids, names and timestamps', () => {
    expect(validateNamed(named('bad id!', 'x'))).toBeNull();
    expect(validateNamed(named('a', ''))).toBeNull();
    expect(validateNamed(named('a', 'x'.repeat(81)))).toBeNull();
    expect(validateNamed(named('a', 'tab\there'))).toBeNull();
    expect(validateNamed({...named('a', 'x'), updated: NaN})).toBeNull();
    expect(validateNamed({...named('a', 'x'), layout: {}})).toBeNull();
  });

  it('keeps at most 30 named layouts and drops duplicates and invalid ones', () => {
    const many = Array.from({length: 40}, (_, index) => named('n' + index, 'Layout ' + index));
    const book = validateBook({version: 2, current: defaultLayout(), named: [...many, named('n0', 'dup'), {junk: true}]})!;
    expect(book.named).toHaveLength(30);
    expect(new Set(book.named.map(item => item.id)).size).toBe(30);
  });

  it('rejects a book whose current layout is unusable', () => {
    expect(validateBook({version: 2, current: {}, named: []})).toBeNull();
    expect(validateBook({version: 1, current: defaultLayout(), named: []})).toBeNull();
    expect(validateBook(null)).toBeNull();
  });
});
