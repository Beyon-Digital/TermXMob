import {useMemo, useState, type KeyboardEvent} from 'react';
import {Plus} from 'lucide-react';
import {Button} from '../../components/ui/button';
import {Input} from '../../components/ui/input';
import {Popover, PopoverContent, PopoverTrigger} from '../../components/ui/popover';
import {Select} from '../../components/ui/select';
import {pivotViewTabId, segmentTabId} from '../../lib/data-tabs';
import type {ViewSummary} from '../../lib/data-views';
import type {ViewsController} from './use-views';
import type {DataConnection, DataQuery, DataRole, DataView, DataWorkspace} from '../../lib/data-studio';
import type {SchemaTable} from '../../lib/query-spec';
import type {SourceController} from './use-source';

export type NamedRecord = {id: string; name: string};
type Props = {
  loading: boolean; workspaces: DataWorkspace[]; workspaceId: string; workspaceBusy: boolean; onWorkspace: (id: string) => void; onCreateWorkspace: () => void;
  role: DataRole; connections: DataConnection[]; connectionId: string; onConnection: (id: string) => void; onConnect: () => void;
  source: SourceController; queries: DataQuery[]; reports: NamedRecord[]; dashboards: NamedRecord[];
  view: DataView; activeTabId: string;
  /** Saved pivots and segments. Absent when the server has no views. */ views?: ViewsController;
  onOpenPivot?: (view: ViewSummary) => void; onOpenSegment?: (view: ViewSummary) => void; onNewPivot?: (table: SchemaTable) => void;
  onOpenOverview: () => void; onOpenTable: (table: SchemaTable) => void; onOpenQuery: (query: DataQuery) => void; onNewQuery: () => void; onOpenView: (view: 'bi' | 'team') => void;
};
export const TABLE_PAGE = 200;
/** Identity of a table inside a source. */
export const tableKey = (table: SchemaTable) => (table.schema ? table.schema + '.' : '') + table.name;
/** What people read: the default schema (main, public) is implied. */
export const tableLabel = (table: Pick<SchemaTable, 'name' | 'schema'>) => table.schema && !DEFAULT_SCHEMAS.includes(table.schema) ? table.schema + '.' + table.name : table.name;
const DEFAULT_SCHEMAS = ['main', 'public'];
const matches = (text: string, needle: string) => text.toLowerCase().includes(needle);

/** Studio-pattern sidebar for Data: scope switchers, search, then Tables, Saved queries, Reports, Dashboards and Team. */
export default function DataNavigation(props: Props) {
  const {loading, workspaces, workspaceId, workspaceBusy, role, connections, connectionId, source, queries, reports, dashboards, view, activeTabId} = props;
  const writable = role !== 'viewer', [search, setSearch] = useState(''), [shown, setShown] = useState(TABLE_PAGE), needle = search.trim().toLowerCase();
  const tables = useMemo(() => (source.schema?.tables || []).filter(table => !needle || matches(tableKey(table), needle)), [source.schema, needle]);
  const savedQueries = useMemo(() => queries.filter(row => row.connection_id === connectionId && (!needle || matches(row.name, needle))), [queries, connectionId, needle]);
  const viewRows = (kind: 'pivot' | 'segment') => (props.views?.views || []).filter(row => row.kind === kind && row.connection_id === connectionId && (!needle || matches(row.name, needle)));
  const pivots = viewRows('pivot'), segments = viewRows('segment');
  const current = (id: string) => view === 'database' && activeTabId === id ? 'page' : undefined;
  return <div className="data-navigation">
    <div className="data-nav-scope">
      <div className="data-nav-field"><label htmlFor="data-nav-workspace">Workspace</label>
        <div className="data-nav-field-row">
          <Select id="data-nav-workspace" className="data-compact" aria-label="Data workspace" value={workspaceId} onChange={event => props.onWorkspace(event.target.value)} disabled={!workspaces.length || workspaceBusy}>
            {!workspaces.length && <option value="">No workspaces</option>}
            {workspaces.map(row => <option value={row.id} key={row.id}>{row.name}</option>)}
          </Select>
          <Button variant="ghost" size="icon" className="data-compact" aria-label="New workspace" title="New workspace" onClick={props.onCreateWorkspace} disabled={loading}><Plus size={16}/></Button>
        </div>
      </div>
      {workspaceId && <div className="data-nav-field"><label htmlFor="data-nav-source">Source</label>
        <div className="data-nav-field-row">
          <Select id="data-nav-source" className="data-compact" aria-label="Data source" value={connectionId} onChange={event => props.onConnection(event.target.value)} disabled={!connections.length}>
            {!connections.length && <option value="">No sources yet</option>}
            {connections.map(row => <option value={row.id} key={row.id}>{row.name}</option>)}
          </Select>
          {writable && connections.length > 0 && <Button variant="ghost" size="icon" className="data-compact" aria-label="Connect database" title="Connect database" onClick={props.onConnect}><Plus size={16}/></Button>}
        </div>
      </div>}
      {connectionId && writable && <div className="data-nav-field"><label htmlFor="data-nav-search">Search tables</label>
        <Input id="data-nav-search" className="data-compact" type="search" autoComplete="off" value={search} onChange={event => {setSearch(event.target.value); setShown(TABLE_PAGE);}}/>
        {needle && <p className="visually-hidden" role="status">{tables.length} {tables.length === 1 ? 'table matches' : 'tables match'}, {props.views ? pivots.length + (pivots.length === 1 ? ' pivot, ' : ' pivots, ') + segments.length + (segments.length === 1 ? ' segment, ' : ' segments, ') : ''}{savedQueries.length} saved {savedQueries.length === 1 ? 'query' : 'queries'}</p>}
      </div>}
    </div>
    <div className="data-nav-groups">
      {workspaceId && connectionId && <nav className="data-nav-group" aria-label="Tables" aria-busy={source.schemaStatus === 'loading'}>
        <h2 className="data-nav-heading">Tables</h2>
        {!writable ? <p className="data-nav-note">Ask an editor to share a table with you.</p> : <>
          <button type="button" className="data-nav-row" aria-current={current('overview')} onClick={props.onOpenOverview}>All tables</button>
          {source.schemaStatus === 'idle' && <Button variant="ghost" className="data-nav-row data-nav-action" onClick={source.loadSchema}>Load schema</Button>}
          {source.schemaStatus === 'loading' && <div className="data-skeleton-list" role="status" aria-label="Loading tables">{[0, 1, 2, 3].map(index => <span className="data-skeleton-row" key={index}/>)}</div>}
          {source.schemaStatus === 'error' && <div className="data-nav-note"><p role="alert">{source.schemaError}</p>{source.needsTest ? <Button variant="ghost" size="sm" onClick={source.testConnection} disabled={source.testing}>{source.testing ? 'Testing…' : 'Test connection'}</Button> : <Button variant="ghost" size="sm" onClick={source.loadSchema}>Try again</Button>}</div>}
          {source.schemaStatus === 'ready' && (!source.schema?.tables.length ? <p className="data-nav-note">No visible tables.</p> : !tables.length ? <p className="data-nav-note">No tables match.</p> : <>
            {tables.slice(0, shown).map(table => {
              const id = 'table:' + connectionId + ':' + tableKey(table), blocked = table.readable === false;
              return <button type="button" key={id} className="data-nav-row" aria-current={current(id)} disabled={blocked} title={blocked ? table.reason || 'This table cannot be read.' : tableLabel(table)} onClick={() => props.onOpenTable(table)}><span>{tableLabel(table)}</span></button>;
            })}
            {tables.length > shown && <Button variant="ghost" className="data-nav-row data-nav-action" onClick={() => setShown(value => value + TABLE_PAGE)}>Show more tables</Button>}
          </>)}
        </>}
      </nav>}
      {workspaceId && connectionId && props.views && <nav className="data-nav-group" aria-label="Pivots" aria-busy={props.views.status === 'loading'}>
        <div className="data-nav-heading-row"><h2 className="data-nav-heading">Pivots</h2>{writable && props.onNewPivot && <NewPivot tables={source.schema?.tables || []} status={source.schemaStatus} onLoad={source.loadSchema} onPick={props.onNewPivot}/>}</div>
        {props.views.status === 'loading' && !pivots.length && <div className="data-skeleton-list" role="status" aria-label="Loading pivots">{[0, 1].map(index => <span className="data-skeleton-row" key={index}/>)}</div>}
        {pivots.map(row => <button type="button" key={row.id} className="data-nav-row" aria-current={current(pivotViewTabId(row.id))} title={row.name} onClick={() => props.onOpenPivot?.(row)}><span>{row.name}</span></button>)}
        {props.views.status === 'error' && <div className="data-nav-note"><p role="alert">{props.views.error}</p><Button variant="ghost" size="sm" onClick={props.views.refresh}>Try again</Button></div>}
        {props.views.status === 'ready' && !pivots.length && <p className="data-nav-note">{needle ? 'No pivots match.' : writable ? 'No pivots yet. Start one from a table.' : 'No pivots shared with you yet.'}</p>}
      </nav>}
      {workspaceId && connectionId && props.views && <nav className="data-nav-group" aria-label="Segments" aria-busy={props.views.status === 'loading'}>
        <h2 className="data-nav-heading">Segments</h2>
        {props.views.status === 'loading' && !segments.length && <div className="data-skeleton-list" role="status" aria-label="Loading segments">{[0, 1].map(index => <span className="data-skeleton-row" key={index}/>)}</div>}
        {segments.map(row => <button type="button" key={row.id} className="data-nav-row" aria-current={current(segmentTabId(row.id))} title={row.name} onClick={() => props.onOpenSegment?.(row)}><span>{row.name}</span>{row.used_by_count > 0 && <small className="data-nav-meta">Used in {row.used_by_count}</small>}</button>)}
        {props.views.status === 'ready' && !segments.length && <p className="data-nav-note">{needle ? 'No segments match.' : writable ? 'No segments yet. Add filters to a table, then choose Save as segment.' : 'No segments shared with you yet.'}</p>}
      </nav>}
      {workspaceId && connectionId && writable && <nav className="data-nav-group" aria-label="Saved queries">
        <div className="data-nav-heading-row"><h2 className="data-nav-heading">Saved queries</h2><Button variant="ghost" size="icon" className="data-compact" aria-label="New query" title="New query" onClick={props.onNewQuery}><Plus size={16}/></Button></div>
        {savedQueries.map(row => <button type="button" key={row.id} className="data-nav-row" aria-current={current('query:' + row.id)} onClick={() => props.onOpenQuery(row)}><span>{row.name}</span></button>)}
        {!savedQueries.length && <p className="data-nav-note">{needle ? 'No saved queries match.' : 'No saved queries.'}</p>}
      </nav>}
      {workspaceId && <nav className="data-nav-group" aria-label="Reports">
        <h2 className="data-nav-heading">Reports</h2>
        {reports.slice(0, 8).map(row => <button type="button" key={row.id} className="data-nav-row" aria-current={view === 'bi' ? 'page' : undefined} onClick={() => props.onOpenView('bi')}><span>{row.name}</span></button>)}
        {!reports.length && <button type="button" className="data-nav-row data-nav-muted" aria-current={view === 'bi' ? 'page' : undefined} onClick={() => props.onOpenView('bi')}>No reports yet</button>}
      </nav>}
      {workspaceId && <nav className="data-nav-group" aria-label="Dashboards">
        <h2 className="data-nav-heading">Dashboards</h2>
        {dashboards.slice(0, 8).map(row => <button type="button" key={row.id} className="data-nav-row" onClick={() => props.onOpenView('bi')}><span>{row.name}</span></button>)}
        {!dashboards.length && <button type="button" className="data-nav-row data-nav-muted" onClick={() => props.onOpenView('bi')}>No dashboards yet</button>}
      </nav>}
      {workspaceId && <nav className="data-nav-group" aria-label="Team">
        <h2 className="data-nav-heading">Team</h2>
        <button type="button" className="data-nav-row" aria-current={view === 'team' ? 'page' : undefined} onClick={() => props.onOpenView('team')}>Members</button>
      </nav>}
    </div>
  </div>;
}

/** The plus next to Pivots: pick the table the new pivot starts from. Type to filter; arrows move; Esc closes and returns focus. */
function NewPivot({tables, status, onLoad, onPick}: {tables: SchemaTable[]; status: SourceController['schemaStatus']; onLoad: () => void; onPick: (table: SchemaTable) => void}) {
  const [open, setOpen] = useState(false), [search, setSearch] = useState('');
  const needle = search.trim().toLowerCase(), list = tables.filter(table => table.readable !== false && (!needle || matches(tableKey(table), needle)));
  function keys(event: KeyboardEvent<HTMLElement>) {
    if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp') return;
    const items = Array.from(event.currentTarget.querySelectorAll<HTMLElement>('input, [data-pivot-table]')), at = items.indexOf(document.activeElement as HTMLElement);
    event.preventDefault();
    items[Math.max(0, Math.min(items.length - 1, at + (event.key === 'ArrowDown' ? 1 : -1)))]?.focus();
  }
  return <Popover open={open} onOpenChange={next => {setOpen(next); if (next) setSearch('');}}>
    <PopoverTrigger asChild><Button variant="ghost" size="icon" className="data-compact" aria-label="New pivot" title="New pivot"><Plus size={16}/></Button></PopoverTrigger>
    <PopoverContent className="data-filter-popover" aria-label="New pivot" onKeyDown={keys}>
      <div className="data-filter-form">
        <h3 className="data-popover-title">New pivot</h3>
        {status === 'idle' && <><p className="data-muted">Load the schema to choose a table.</p><Button variant="secondary" size="sm" onClick={onLoad}>Load schema</Button></>}
        {status === 'loading' && <p className="data-muted" role="status">Loading tables…</p>}
        {status === 'error' && <><p className="data-muted">The tables could not be loaded.</p><Button variant="secondary" size="sm" onClick={onLoad}>Try again</Button></>}
        {status === 'ready' && <>
          <div className="data-field"><label htmlFor="new-pivot-search">Find a table</label><Input id="new-pivot-search" type="search" autoComplete="off" autoFocus value={search} onChange={event => setSearch(event.target.value)}/></div>
          {list.length ? <ul className="data-segment-list">{list.map(table => <li key={tableKey(table)}><button type="button" className="data-segment-option" data-pivot-table onClick={() => {onPick(table); setOpen(false);}}><span>{tableLabel(table)}</span></button></li>)}</ul> : <p className="data-muted">No tables match.</p>}
        </>}
      </div>
    </PopoverContent>
  </Popover>;
}
