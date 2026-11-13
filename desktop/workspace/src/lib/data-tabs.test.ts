import {afterEach, beforeEach, expect, it, vi} from 'vitest';
import {closeTab, dataTabsKey, initialTabs, openTab, pruneTabs, readTabs, rememberTabs, schemaFor, selectTab, tableTabId, queryTabId, type DataTab} from './data-tabs';

const orders: DataTab = {id: tableTabId('src', 'orders'), kind: 'table', connectionId: 'src', table: 'orders'};
const customers: DataTab = {id: tableTabId('src', 'customers'), kind: 'table', connectionId: 'src', table: 'customers'};
const saved: DataTab = {id: queryTabId('q1'), kind: 'query', queryId: 'q1'};

beforeEach(() => localStorage.clear());
afterEach(() => vi.restoreAllMocks());

it('starts with the persistent overview tab selected', () => {
  expect(initialTabs()).toEqual({tabs: [{id: 'overview', kind: 'overview'}], activeId: 'overview'});
});

it('builds stable ids from connection, schema and table', () => {
  expect(tableTabId('src', 'orders')).toBe('table:src:orders');
  expect(tableTabId('src', 'orders', 'public')).toBe('table:src:public.orders');
  expect(queryTabId('q1')).toBe('query:q1');
  expect(queryTabId()).toBe('query:new');
});

it('opens a new tab and selects it', () => {
  const state = openTab(initialTabs(), orders);
  expect(state.tabs.map(tab => tab.id)).toEqual(['overview', orders.id]);
  expect(state.activeId).toBe(orders.id);
});

it('focuses an already open tab instead of duplicating it', () => {
  let state = openTab(openTab(initialTabs(), orders), customers);
  state = openTab(state, orders);
  expect(state.tabs).toHaveLength(3);
  expect(state.activeId).toBe(orders.id);
});

it('closes the active tab and selects its right neighbour, else the left one', () => {
  let state = openTab(openTab(openTab(initialTabs(), orders), customers), saved);
  state = selectTab(state, orders.id);
  state = closeTab(state, orders.id);
  expect(state.activeId).toBe(customers.id);
  state = closeTab(state, saved.id);
  expect(state.activeId).toBe(customers.id);
  state = closeTab(state, customers.id);
  expect(state.activeId).toBe('overview');
});

it('keeps the active tab when another tab closes and never closes the overview', () => {
  let state = openTab(openTab(initialTabs(), orders), customers);
  state = closeTab(state, orders.id);
  expect(state.activeId).toBe(customers.id);
  expect(closeTab(state, 'overview')).toEqual(state);
});

it('ignores selecting an id that is not open', () => {
  const state = openTab(initialTabs(), orders);
  expect(selectTab(state, 'table:src:missing')).toEqual(state);
});

it('prunes tabs that no longer exist and falls back to the overview', () => {
  const state = openTab(openTab(initialTabs(), orders), customers);
  const next = pruneTabs(state, tab => tab.id !== orders.id);
  expect(next.tabs.map(tab => tab.id)).toEqual(['overview', customers.id]);
  expect(next.activeId).toBe(customers.id);
  expect(pruneTabs(openTab(initialTabs(), orders), () => false).activeId).toBe('overview');
});

it('persists identifiers only, never filter values, SQL or results', () => {
  const state = openTab(openTab(initialTabs(), orders), {...saved, sql: 'SELECT secret FROM t', rows: [[1]]} as DataTab);
  rememberTabs('owner', 'workspace', state);
  const raw = localStorage.getItem(dataTabsKey('owner', 'workspace'))!;
  expect(raw).not.toContain('secret');
  expect(raw).not.toContain('rows');
  expect(readTabs('owner', 'workspace')).toEqual({tabs: [{id: 'overview', kind: 'overview'}, orders, saved], activeId: saved.id});
});

it('scopes storage by owner and workspace', () => {
  rememberTabs('owner', 'one', openTab(initialTabs(), orders));
  expect(readTabs('owner', 'two')).toEqual(initialTabs());
  expect(readTabs('other', 'one')).toEqual(initialTabs());
});

it('drops malformed stored entries and falls back safely', () => {
  localStorage.setItem(dataTabsKey('owner', 'workspace'), JSON.stringify({tabs: [{id: 'table:src:orders', kind: 'table', connectionId: 'src', table: 'orders'}, {id: 'bad', kind: 'sql'}, {kind: 'table'}, 7], activeId: 'gone'}));
  expect(readTabs('owner', 'workspace')).toEqual({tabs: [{id: 'overview', kind: 'overview'}, orders], activeId: 'overview'});
  localStorage.setItem(dataTabsKey('owner', 'workspace'), '{not json');
  expect(readTabs('owner', 'workspace')).toEqual(initialTabs());
});

it('tolerates blocked storage', () => {
  vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {throw new Error('blocked');});
  vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {throw new Error('blocked');});
  expect(readTabs('owner', 'workspace')).toEqual(initialTabs());
  expect(() => rememberTabs('owner', 'workspace', initialTabs())).not.toThrow();
});

it('caps the number of open tabs', () => {
  let state = initialTabs();
  for (let index = 0; index < 40; index++) state = openTab(state, {id: tableTabId('src', 't' + index), kind: 'table', connectionId: 'src', table: 't' + index});
  expect(state.tabs.length).toBeLessThanOrEqual(24);
  expect(state.activeId).toBe(tableTabId('src', 't39'));
});

it('finds the schema of a table by its name, so tabs opened from a spec match the schema list (SQLite tables live in "main")', () => {
  const tables = [{name: 'sales', schema: 'main'}, {name: 'plain'}];
  expect(schemaFor(tables, 'sales')).toBe('main');
  expect(schemaFor(tables, 'plain')).toBeUndefined();
  expect(schemaFor(tables, 'missing')).toBeUndefined();
  expect(schemaFor(undefined, 'sales')).toBeUndefined();
});
