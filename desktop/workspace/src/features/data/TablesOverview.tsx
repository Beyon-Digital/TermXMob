import {useEffect, useRef, useState} from 'react';
import {LayoutGrid, Link2, List, MoreHorizontal, Pencil, Trash2} from 'lucide-react';
import {Button} from '../../components/ui/button';
import {DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger} from '../../components/ui/dropdown-menu';
import {runSpecToCompletion} from '../../lib/data-query';
import {dataError, type DataConnection, type DataEngine, type DataRole} from '../../lib/data-studio';
import {emptySpec} from '../../lib/query-builder';
import type {SchemaTable} from '../../lib/query-spec';
import {tableKey, tableLabel} from './DataNavigation';
import type {SourceController} from './use-source';

export type OverviewMode = 'list' | 'grid';
export const overviewModeKey = (ownerId?: string) => 'termx.data.overview.' + (ownerId || 'managed');
export function readOverviewMode(ownerId?: string): OverviewMode {
  try {return localStorage.getItem(overviewModeKey(ownerId)) === 'grid' ? 'grid' : 'list';} catch {return 'list';}
}
function rememberOverviewMode(ownerId: string | undefined, mode: OverviewMode) {
  try {localStorage.setItem(overviewModeKey(ownerId), mode);} catch {/* storage may be blocked */}
}
const count = (value: number, noun: string) => value.toLocaleString() + ' ' + noun + (value === 1 ? '' : 's');
const rows = (table: SchemaTable) => 'About ' + (table.row_estimate ?? 0).toLocaleString();

/**
 * Rows of one table. A source that gives an estimate shows it; otherwise "Count rows" runs a count and puts the number
 * in the same place (announced politely). A source that needs a test first, or a failed count, says so with a retry.
 */
function RowCount({workspaceId, connectionId, table}: {workspaceId: string; connectionId: string; table: SchemaTable}) {
  const [state, setState] = useState<{phase: 'idle' | 'counting' | 'done' | 'error'; value?: number; error?: string}>({phase: 'idle'});
  const [abort] = useState(() => ({current: null as AbortController | null}));
  useEffect(() => () => abort.current?.abort(), [abort]);
  async function count() {
    abort.current?.abort();
    const controller = abort.current = new AbortController();
    setState({phase: 'counting'});
    try {
      const job = await runSpecToCompletion(workspaceId, {...emptySpec(connectionId, table.name), aggregates: [{fn: 'count'}], limit: 1}, {signal: controller.signal});
      if (controller.signal.aborted) return;
      const cell = job.result?.rows?.[0]?.[0];
      if (job.status === 'completed' && typeof cell === 'number') setState({phase: 'done', value: cell});
      else setState({phase: 'error', error: job.error || 'The count could not be completed.'});
    } catch (reason) {
      if (!controller.signal.aborted) setState({phase: 'error', error: dataError(reason)});
    }
  }
  if (table.readable === false) return null;
  let body;
  if (typeof table.row_estimate === 'number') body = table.row_estimate.toLocaleString();
  else if (state.phase === 'done') body = (state.value ?? 0).toLocaleString();
  else if (state.phase === 'error') body = <><span role="alert">{state.error}</span> <Button variant="ghost" size="sm" onClick={() => void count()}>Try again</Button></>;
  else body = <Button variant="ghost" size="sm" aria-label={'Count rows in ' + tableLabel(table)} disabled={state.phase === 'counting'} onClick={() => void count()}>{state.phase === 'counting' ? 'Counting…' : 'Count rows'}</Button>;
  return <span className="data-row-count" aria-live="polite">{body}</span>;
}

/** Secondary table actions. Pivot joins this menu in the next release. */
function TableMenu({table, onCombine}: {table: SchemaTable; onCombine?: (table: SchemaTable) => void}) {
  const chosen = useRef(false);
  if (!onCombine) return null;
  return <DropdownMenu>
    <DropdownMenuTrigger asChild><Button variant="ghost" size="icon" aria-label={'More actions for ' + tableLabel(table)} disabled={table.readable === false}><MoreHorizontal size={16} strokeWidth={1.5}/></Button></DropdownMenuTrigger>
    <DropdownMenuContent aria-label={'Actions for ' + tableLabel(table)} onCloseAutoFocus={event => {if (chosen.current) {event.preventDefault(); chosen.current = false;}}}>
      {/* Focus moves to the table that opens, so the menu must not pull it back to this row. */}
      <DropdownMenuItem onSelect={() => {chosen.current = true; onCombine(table);}}><Link2 size={16} strokeWidth={1.5}/>Combine with another table</DropdownMenuItem>
    </DropdownMenuContent>
  </DropdownMenu>;
}

type Props = {
  workspaceId: string; ownerId?: string; role: DataRole; hasConnections: boolean; source?: DataConnection; engine?: DataEngine; controller: SourceController; canEditSource: boolean;
  onConnect: () => void; onEditSource: () => void; onArchiveSource: () => void; onOpenTable: (table: SchemaTable) => void;
  /** Opens the table with its Combine panel showing. */ onCombineTable?: (table: SchemaTable) => void;
};
/** Home tab: the source and every table in it, as a list or as tiles. */
export default function TablesOverview({workspaceId, ownerId, role, hasConnections, source, engine, controller, canEditSource, onConnect, onEditSource, onArchiveSource, onOpenTable, onCombineTable}: Props) {
  const writable = role !== 'viewer', [mode, setMode] = useState<OverviewMode>(() => readOverviewMode(ownerId));
  function choose(next: OverviewMode) {setMode(next); rememberOverviewMode(ownerId, next);}
  if (!source) return <div className="data-empty">
    <h1>Connect your database</h1>
    <p>{writable ? 'Choose a source on the server host to browse its tables.' : 'A workspace editor can connect a database.'}</p>
    {writable && <Button onClick={onConnect}>Connect database</Button>}
    {!writable && hasConnections && <p>Choose a source in the sidebar.</p>}
  </div>;
  const tables = controller.schema?.tables || [], status = controller.schemaStatus;
  return <section className="data-overview" aria-label="Tables overview" aria-busy={status === 'loading'}>
    <header className="data-source-header">
      <div><h1>{source.name}</h1><p className="data-muted">{engine?.label || source.engine} · {engine?.ready ? engine.status === 'qualified' ? 'Read-only access' : engine.reason || 'Driver installed' : engine?.reason || 'Driver unavailable'}</p></div>
      {writable && <div className="data-toolbar">
        <Button variant="ghost" size="sm" disabled={controller.testing || !engine?.ready} onClick={controller.testConnection}>{controller.testing ? 'Testing…' : 'Test connection'}</Button>
        <Button variant="ghost" size="icon" aria-label="Edit connection" title="Edit connection" disabled={!canEditSource} onClick={onEditSource}><Pencil size={16}/></Button>
        <Button variant="ghost" size="icon" aria-label="Archive connection" title="Archive connection" disabled={!canEditSource} onClick={onArchiveSource}><Trash2 size={16}/></Button>
      </div>}
    </header>
    {controller.testMessage && <p role="status">{controller.testMessage}</p>}
    {controller.testError && <p role="alert">{controller.testError}</p>}
    <div className="data-section-heading">
      <h2>Tables</h2>
      {status === 'ready' && tables.length > 0 && <div className="data-toggle" role="group" aria-label="Table layout">
        <Button variant="ghost" size="icon" aria-label="List view" aria-pressed={mode === 'list'} onClick={() => choose('list')}><List size={16}/></Button>
        <Button variant="ghost" size="icon" aria-label="Grid view" aria-pressed={mode === 'grid'} onClick={() => choose('grid')}><LayoutGrid size={16}/></Button>
      </div>}
    </div>
    {!writable && <p className="data-muted">Your viewer role can open published reports in Reports.</p>}
    {writable && status === 'idle' && <div className="data-empty-inline"><p>Load the schema to see the tables in this source.</p><Button onClick={controller.loadSchema} disabled={!engine?.ready || !engine.capabilities.schema}>Load schema</Button></div>}
    {writable && status === 'loading' && <div className={mode === 'grid' ? 'data-tiles' : 'data-list'} role="status" aria-label="Loading tables">{[0, 1, 2, 3, 4, 5].map(index => <span className={mode === 'grid' ? 'data-skeleton-tile' : 'data-skeleton-row'} key={index}/>)}</div>}
    {writable && status === 'error' && <div className="data-inline-error"><p role="alert">{controller.schemaError}</p>{controller.needsTest ? <Button variant="secondary" size="sm" onClick={controller.testConnection} disabled={controller.testing}>{controller.testing ? 'Testing…' : 'Test connection'}</Button> : <Button variant="secondary" size="sm" onClick={controller.loadSchema}>Try again</Button>}</div>}
    {writable && status === 'ready' && !tables.length && <div className="data-empty-inline"><p>This source has no visible tables.</p><Button variant="secondary" onClick={controller.loadSchema}>Load schema</Button></div>}
    {writable && status === 'ready' && tables.length > 0 && (mode === 'list'
      ? <table className="data-table-list">
        <thead><tr><th scope="col">Table</th><th scope="col" className="data-number">Columns</th><th scope="col" className="data-number">Rows</th><th scope="col"><span className="visually-hidden">Actions</span></th></tr></thead>
        <tbody>{tables.map(table => <tr key={tableKey(table)}>
          <th scope="row"><span className="data-table-name">{tableLabel(table)}</span>{table.readable === false && <small>{table.reason || 'This table cannot be read.'}</small>}</th>
          <td className="data-number">{table.columns.length.toLocaleString()}</td><td className="data-number"><RowCount workspaceId={workspaceId} connectionId={source.id} table={table}/></td>
          <td className="data-row-action"><Button variant="ghost" size="sm" aria-label={'Open ' + tableLabel(table)} disabled={table.readable === false} onClick={() => onOpenTable(table)}>Open</Button><TableMenu table={table} onCombine={onCombineTable}/></td>
        </tr>)}</tbody>
      </table>
      : <ul className="data-tiles">{tables.map(table => <li key={tableKey(table)} className="data-tile">
        <h3>{tableLabel(table)}</h3>
        <p className="data-muted">{count(table.columns.length, 'column')}</p>
        <p className="data-muted">{table.readable === false ? table.reason || 'This table cannot be read.' : typeof table.row_estimate === 'number' ? rows(table) + ' rows' : <RowCount workspaceId={workspaceId} connectionId={source.id} table={table}/>}</p>
        <div className="data-tile-actions"><Button variant="secondary" size="sm" aria-label={'Open ' + tableLabel(table)} disabled={table.readable === false} onClick={() => onOpenTable(table)}>Open</Button><TableMenu table={table} onCombine={onCombineTable}/></div>
      </li>)}</ul>)}
  </section>;
}
