/** Canvas tab model for Data Studio. Only identifiers are ever persisted: filters, SQL and results stay in memory. */
export type DataTabKind = 'overview' | 'table' | 'query';
export type DataTab = {id: string; kind: DataTabKind; connectionId?: string; table?: string; schema?: string; queryId?: string};
export type DataTabsState = {tabs: DataTab[]; activeId: string};

export const OVERVIEW_TAB: DataTab = {id: 'overview', kind: 'overview'};
export const MAX_DATA_TABS = 24;
const PERSISTED_KEYS = ['id', 'kind', 'connectionId', 'table', 'schema', 'queryId'] as const;

export const tableTabId = (connectionId: string, table: string, schema?: string) => 'table:' + connectionId + ':' + (schema ? schema + '.' : '') + table;
export const queryTabId = (queryId?: string) => 'query:' + (queryId || 'new');
export const initialTabs = (): DataTabsState => ({tabs: [{...OVERVIEW_TAB}], activeId: OVERVIEW_TAB.id});

export function openTab(state: DataTabsState, tab: DataTab): DataTabsState {
  if (state.tabs.some(row => row.id === tab.id)) return selectTab(state, tab.id);
  let tabs = [...state.tabs, tab];
  while (tabs.length > MAX_DATA_TABS) {
    const drop = tabs.findIndex(row => row.kind !== 'overview' && row.id !== tab.id);
    if (drop < 0) break;
    tabs = tabs.filter((_, index) => index !== drop);
  }
  return {tabs, activeId: tab.id};
}
export function selectTab(state: DataTabsState, id: string): DataTabsState {
  return state.activeId !== id && state.tabs.some(row => row.id === id) ? {...state, activeId: id} : state;
}
export function closeTab(state: DataTabsState, id: string): DataTabsState {
  const index = state.tabs.findIndex(row => row.id === id);
  if (index < 0 || state.tabs[index].kind === 'overview') return state;
  const tabs = state.tabs.filter(row => row.id !== id);
  if (state.activeId !== id) return {...state, tabs};
  return {tabs, activeId: (tabs[index] || tabs[index - 1] || tabs[0]).id};
}
export function pruneTabs(state: DataTabsState, keep: (tab: DataTab) => boolean): DataTabsState {
  const tabs = state.tabs.filter(tab => tab.kind === 'overview' || keep(tab));
  if (tabs.length === state.tabs.length) return state;
  return {tabs, activeId: tabs.some(tab => tab.id === state.activeId) ? state.activeId : OVERVIEW_TAB.id};
}

export const dataTabsKey = (ownerId: string | undefined, workspaceId: string) => 'termx.data.tabs.' + (ownerId || 'managed') + '.' + workspaceId;
function sanitize(value: unknown): DataTab | null {
  if (!value || typeof value !== 'object') return null;
  const row = value as Record<string, unknown>, id = row.id, kind = row.kind;
  if (typeof id !== 'string' || id.length > 600) return null;
  if (kind === 'table') {
    if (typeof row.connectionId !== 'string' || typeof row.table !== 'string' || !row.table) return null;
    const schema = typeof row.schema === 'string' && row.schema ? row.schema : undefined;
    const tab: DataTab = {id: tableTabId(row.connectionId, row.table, schema), kind, connectionId: row.connectionId, table: row.table};
    if (schema) tab.schema = schema;
    return tab.id === id ? tab : null;
  }
  if (kind === 'query') {
    const queryId = typeof row.queryId === 'string' && row.queryId ? row.queryId : undefined;
    return id === queryTabId(queryId) ? (queryId ? {id, kind, queryId} : {id, kind}) : null;
  }
  return null;
}
export function readTabs(ownerId: string | undefined, workspaceId: string): DataTabsState {
  let saved: unknown;
  try {saved = JSON.parse(localStorage.getItem(dataTabsKey(ownerId, workspaceId)) || 'null');} catch {return initialTabs();}
  if (!saved || typeof saved !== 'object' || !Array.isArray((saved as {tabs?: unknown}).tabs)) return initialTabs();
  const seen = new Set<string>(['overview']), tabs: DataTab[] = [{...OVERVIEW_TAB}];
  for (const entry of (saved as {tabs: unknown[]}).tabs) {
    const tab = sanitize(entry);
    if (tab && !seen.has(tab.id) && tabs.length < MAX_DATA_TABS) {seen.add(tab.id); tabs.push(tab);}
  }
  const activeId = (saved as {activeId?: unknown}).activeId;
  return {tabs, activeId: typeof activeId === 'string' && seen.has(activeId) ? activeId : OVERVIEW_TAB.id};
}
export function rememberTabs(ownerId: string | undefined, workspaceId: string, state: DataTabsState) {
  const tabs = state.tabs.map(tab => Object.fromEntries(PERSISTED_KEYS.filter(key => tab[key] !== undefined).map(key => [key, tab[key]])));
  try {localStorage.setItem(dataTabsKey(ownerId, workspaceId), JSON.stringify({tabs, activeId: state.activeId}));} catch {/* storage may be blocked */}
}
