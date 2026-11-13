import {beforeEach, expect, it} from 'vitest';
import {dataTabsKey, drillTabId, initialTabs, openTab, pivotDraftTabId, pivotViewTabId, readTabs, rememberTabs, segmentTabId, type DataTab} from './data-tabs';

beforeEach(() => localStorage.clear());

it('builds stable ids for pivots, segments and drill tabs', () => {
  expect(pivotViewTabId('v1')).toBe('pivot:view:v1');
  expect(segmentTabId('v1')).toBe('segment:view:v1');
  expect(pivotDraftTabId('src', 'orders', 2)).toBe('pivot:new:src:orders:2');
  expect(drillTabId(3)).toBe('drill:3');
});
it('stores only ids for views and drops drill tabs and memory-only fields', () => {
  const pivot: DataTab = {id: pivotViewTabId('v1'), kind: 'pivot', viewId: 'v1', title: 'Secret title'};
  const segment: DataTab = {id: segmentTabId('v2'), kind: 'segment', viewId: 'v2'};
  const draft: DataTab = {id: pivotDraftTabId('src', 'orders', 1), kind: 'pivot', connectionId: 'src', table: 'orders', segmentId: 'v9', note: 'x'};
  const drill: DataTab = {id: drillTabId(1), kind: 'drill', connectionId: 'src', table: 'orders', spec: {version: 1, source: 'src', from: {table: 'orders', alias: 'orders'}, joins: [], select: [],
    filters: {op: 'and', items: [{field: {table_alias: 'orders', column: 'status'}, op: 'eq', value: 'private'}]}, group_by: [], aggregates: [], sort: [], limit: 200, offset: 0}};
  const state = [pivot, segment, draft, drill].reduce(openTab, initialTabs());
  rememberTabs('me', 'w1', state);
  const raw = localStorage.getItem(dataTabsKey('me', 'w1'))!;
  for (const secret of ['private', 'Secret title', 'v9', 'drill']) expect(raw).not.toContain(secret);
  expect(readTabs('me', 'w1').tabs.map(tab => tab.id)).toEqual(['overview', pivot.id, segment.id, draft.id]);
  expect(readTabs('me', 'w1').tabs[3]).toEqual({id: draft.id, kind: 'pivot', connectionId: 'src', table: 'orders'});
});
it('ignores view tabs whose id does not match their view', () => {
  localStorage.setItem(dataTabsKey('me', 'w1'), JSON.stringify({tabs: [{id: 'pivot:view:other', kind: 'pivot', viewId: 'v1'}, {id: 'segment:view:v1', kind: 'segment'}, {id: 'drill:1', kind: 'drill'}, {id: 'pivot:new:x', kind: 'pivot', connectionId: 'x', table: 't'}], activeId: 'drill:1'}));
  expect(readTabs('me', 'w1')).toEqual(initialTabs());
});
