import {useCallback, useEffect, useMemo, useState} from 'react';
import {Download, Sigma, Code2} from 'lucide-react';
import {Button} from '../../components/ui/button';
import {fetchCapabilities} from '../../lib/data-query';
import {dataDownload, dataError, useDataScope} from '../../lib/data-studio';
import {DEFAULT_PAGE, addJoin, countFilters, emptySpec, humanize, isFilterGroup, kindGroup, nextSort, refKey, removeJoin, sameColumn, summaryFields, type Summary, type TableColumns} from '../../lib/query-builder';
import type {CapabilitiesResult, ColumnRef, FilterGroup, FilterNode, Join, QuerySpec, SchemaTable, SortKey, ValueKind} from '../../lib/query-spec';
import {tableKey} from './DataNavigation';
import ColumnPicker from './ColumnPicker';
import {ColumnFilter, type ColumnOption} from './ColumnFilter';
import FilterChips from './FilterChips';
import JoinPanel, {JoinList} from './JoinPanel';
import QueryGrid, {type GridColumn} from './QueryGrid';
import ShowSql from './ShowSql';
import SummarizePanel from './SummarizePanel';
import {useSpecRun} from './use-spec-run';
import type {SourceController} from './use-source';

type Props = {
  workspaceId: string; connectionId: string; name: string; label: string; controller: SourceController; engineCanLoad: boolean; onClose: () => void;
  /** Opens the Advanced SQL panel, offered when the visual builder does not support the engine. */ onOpenAdvanced?: () => void;
  /** Grows when the person picked Combine from the overview, so the Combine panel opens. */ combineRequest?: number;
};
const SkeletonList = ({label}: {label: string}) => <div className="data-list" role="status" aria-label={label}>{[0, 1, 2, 3, 4].map(index => <span className="data-skeleton-row" key={index}/>)}</div>;
const retry = (controller: SourceController) => controller.needsTest
  ? <Button variant="secondary" size="sm" onClick={controller.testConnection} disabled={controller.testing}>{controller.testing ? 'Testing…' : 'Test connection'}</Button>
  : <Button variant="secondary" size="sm" onClick={controller.loadSchema}>Try again</Button>;

/** Table tab: schema guard around the working view. */
export default function TableTab(props: Props) {
  const {name, label, controller, engineCanLoad, onClose} = props, status = controller.schemaStatus;
  const table: SchemaTable | undefined = controller.schema?.tables.find(row => tableKey(row) === name);
  if (status === 'idle') return <div className="data-empty-inline"><p>Load the schema to open {label}.</p><Button onClick={controller.loadSchema} disabled={!engineCanLoad}>Load schema</Button></div>;
  if (status === 'loading') return <SkeletonList label={'Loading ' + label}/>;
  if (status === 'error') return <div className="data-inline-error"><p role="alert">{controller.schemaError}</p>{retry(controller)}</div>;
  if (!table) return <div className="data-empty-inline"><p>{label} is no longer in this source.</p><Button variant="secondary" onClick={onClose}>Close tab</Button></div>;
  if (table.readable === false) return <section className="data-table-tab" aria-label={'Table ' + label}><header><h1>{label}</h1></header><p className="data-muted">{table.reason || 'This table cannot be read.'}</p></section>;
  return <TableWorkspace {...props} table={table} tables={controller.schema?.tables || []}/>;
}

type View = {joins: Join[]; filters: FilterGroup; sort: SortKey[]; offset: number; visible: ColumnRef[] | null; summary: Summary | null};
const compact = (group: FilterGroup): FilterGroup => ({...group, items: group.items.flatMap((item): FilterNode[] => {
  if (!isFilterGroup(item)) return [item];
  const next = compact(item);
  return next.items.length ? [next] : [];
})});

function TableWorkspace({workspaceId, connectionId, label, controller, table, tables, onOpenAdvanced, combineRequest}: Props & {table: SchemaTable; tables: SchemaTable[]}) {
  const scope = useDataScope();
  const base = useMemo(() => emptySpec(connectionId, table.name), [connectionId, table.name]);
  const [view, setView] = useState<View>({joins: [], filters: base.filters, sort: [], offset: 0, visible: null, summary: null});
  const [panel, setPanel] = useState<{summarize: boolean; sql: boolean}>({summarize: false, sql: false});
  const [capabilities, setCapabilities] = useState<CapabilitiesResult | null>(null), [exporting, setExporting] = useState(false), [exportError, setExportError] = useState('');
  useEffect(() => {
    const abort = new AbortController();
    fetchCapabilities(workspaceId, connectionId, abort.signal).then(result => {if (!abort.signal.aborted) setCapabilities(result);}).catch(() => undefined);
    return () => abort.abort();
  }, [workspaceId, connectionId]);

  const schemaOf = useCallback((tableName: string) => tables.find(row => row.name === tableName), [tables]);
  const aliasTable = useCallback((alias: string) => alias === base.from.alias ? base.from.table : view.joins.find(join => join.alias === alias)?.table || alias, [base, view.joins]);
  const tableColumns = useMemo<TableColumns[]>(() => [{alias: base.from.alias, table: base.from.table}, ...view.joins.map(join => ({alias: join.alias, table: join.table}))]
    .map(entry => ({...entry, columns: schemaOf(entry.table)?.columns || []})), [base, view.joins, schemaOf]);
  const labelFor = useCallback((ref: ColumnRef) => view.joins.length ? humanize(aliasTable(ref.table_alias)) + ' · ' + humanize(ref.column) : humanize(ref.column), [view.joins.length, aliasTable]);
  const kindFor = useCallback((ref: ColumnRef): ValueKind | undefined => schemaOf(aliasTable(ref.table_alias))?.columns.find(column => column.name === ref.column)?.value_kind, [schemaOf, aliasTable]);
  const options = useMemo<ColumnOption[]>(() => tableColumns.flatMap(entry => entry.columns.map(column => {
    const ref = {table_alias: entry.alias, column: column.name};
    return {ref, label: labelFor(ref), kind: column.value_kind};
  })), [tableColumns, labelFor]);
  const allowedOps = capabilities?.dialect?.filter_ops, buckets = capabilities?.dialect?.date_bucket.buckets;

  const visible = view.visible?.filter(ref => tableColumns.some(entry => entry.alias === ref.table_alias));
  const spec = useMemo<QuerySpec>(() => {
    const filters = compact(view.filters);
    if (view.summary) {
      const {group_by, aggregates} = summaryFields(view.summary, labelFor);
      return {...base, joins: view.joins, filters, select: [], group_by, aggregates, sort: view.sort, offset: view.offset};
    }
    return {...base, joins: view.joins, filters, select: visible?.length ? visible : [], sort: view.sort, offset: view.offset};
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [base, view, labelFor]);
  const supported = capabilities ? capabilities.supported : true;
  const {state, reload} = useSpecRun(workspaceId, spec, supported);
  const change = (next: Partial<View>) => setView(previous => ({...previous, offset: 0, ...next}));

  const shown = state.shown, rows = shown ? shown.job.result!.rows : [], limit = spec.limit || DEFAULT_PAGE;
  const pageRows = rows.slice(0, limit), hasMore = !!shown && (shown.job.result!.truncated || rows.length > limit);
  const gridColumns = useMemo<GridColumn[]>(() => {
    const columns = shown?.compile.columns;
    if (!columns) return table.columns.map(column => ({key: column.name, label: column.name, kind: column.value_kind}));
    return columns.map(column => {
      const byColumn = column.role === 'column' && column.table_alias && column.column;
      const key = shown.spec.sort.find(item => byColumn ? 'table_alias' in item && item.table_alias === column.table_alias && item.column === column.column : 'output' in item && item.output === column.label);
      return {key: column.label, label: column.label, kind: column.value_kind, sortable: true, sort: key?.direction};
    });
  }, [shown, table.columns]);
  function sortBy(index: number, additive: boolean) {
    const column = shown?.compile.columns[index];
    if (!column) return;
    const target = column.role === 'column' && column.table_alias && column.column ? {table_alias: column.table_alias, column: column.column} : {output: column.label};
    change({sort: nextSort(view.sort, target, additive)});
  }
  const filterActive = (ref: ColumnRef) => {
    const walk = (group: FilterGroup): boolean => group.items.some(item => isFilterGroup(item) ? walk(item) : sameColumn(item.field, ref));
    return walk(view.filters);
  };
  const clearFilters = () => change({filters: base.filters});
  const loading = state.phase === 'compiling' || state.phase === 'running';
  const filterCause = state.errors.some(error => error.path.startsWith('filters'));

  function toggleSummary() {
    const on = !panel.summarize;
    setPanel(previous => ({...previous, summarize: on}));
    change({summary: on ? {measure: {fn: 'count'}} : null, sort: []});
  }
  async function exportCsv() {
    if (!shown || exporting) return;
    const abort = scope.controller();
    setExporting(true); setExportError('');
    try {await dataDownload(workspaceId, shown.job.id, abort.signal, () => scope.current());}
    catch (reason) {if (scope.current()) setExportError(dataError(reason));}
    finally {scope.release(abort); if (scope.current()) setExporting(false);}
  }

  const meta = typeof table.row_estimate === 'number' ? ' · About ' + table.row_estimate.toLocaleString() + ' rows' : '';
  const first = spec.offset + 1, last = spec.offset + pageRows.length;
  let results;
  if (!supported) results = <div className="data-empty-inline"><p>{capabilities?.reason || 'The table builder does not support this source.'}</p>{onOpenAdvanced && <Button variant="secondary" onClick={onOpenAdvanced}>Open Advanced SQL</Button>}</div>;
  else if (state.phase === 'invalid') results = <div className="data-inline-error"><p role="alert">{state.errors[0]?.message}</p>{filterCause ? <Button variant="secondary" size="sm" onClick={clearFilters}>Clear filters</Button> : <Button variant="secondary" size="sm" onClick={() => {setPanel({summarize: false, sql: false}); setView({joins: [], filters: base.filters, sort: [], offset: 0, visible: null, summary: null});}}>Reset view</Button>}</div>;
  else if (state.phase === 'error') results = <div className="data-inline-error"><p role="alert">{state.error}</p>{state.needsTest ? <Button variant="secondary" size="sm" onClick={controller.testConnection} disabled={controller.testing}>{controller.testing ? 'Testing…' : 'Test connection'}</Button> : <Button variant="secondary" size="sm" onClick={reload}>Try again</Button>}</div>;
  else if (shown && !loading && !pageRows.length) results = spec.offset > 0
    ? <div className="data-empty-inline"><p>There are no more rows.</p><Button variant="secondary" onClick={() => change({offset: Math.max(0, spec.offset - limit)})}>Previous rows</Button></div>
    : <div className="data-empty-inline"><p>{countFilters(spec.filters) ? 'No rows match these filters.' : view.summary ? 'There is nothing to summarize.' : 'This table has no rows yet.'}</p>{countFilters(spec.filters) ? <Button variant="secondary" onClick={clearFilters}>Clear filters</Button> : <Button variant="secondary" onClick={reload}>Refresh</Button>}</div>;
  else results = <>
    <QueryGrid columns={gridColumns} rows={pageRows} loading={loading && !shown} firstRow={first} onSort={shown ? sortBy : undefined}
      renderFilter={!view.summary && shown ? (index, column) => {
        const meta = shown.compile.columns[index];
        if (meta?.role !== 'column' || !meta.table_alias || !meta.column) return null;
        const ref = {table_alias: meta.table_alias, column: meta.column};
        return <ColumnFilter column={{ref, label: column.label, kind: kindFor(ref) ?? column.kind}} columns={options} allowedOps={allowedOps} active={filterActive(ref)} onApply={leaf => change({filters: {...view.filters, items: [...view.filters.items, leaf]}})}/>;
      } : undefined}/>
    {shown && <footer className="data-grid-footer">
      <p className="data-muted" role="status">{pageRows.length ? 'Rows ' + first.toLocaleString() + ' to ' + last.toLocaleString() + (hasMore ? '' : ' of ' + last.toLocaleString()) : ''}</p>
      <div className="data-toolbar">
        <Button variant="ghost" size="sm" disabled={spec.offset === 0 || loading} onClick={() => change({offset: Math.max(0, spec.offset - limit)})}>Previous</Button>
        <Button variant="ghost" size="sm" disabled={!hasMore || loading} onClick={() => change({offset: spec.offset + Math.min(rows.length, limit)})}>Next</Button>
      </div>
    </footer>}
  </>;

  return <section className="data-table-tab" aria-label={'Table ' + label}>
    <header><h1>{label}</h1><p className="data-muted">{table.columns.length.toLocaleString()} {table.columns.length === 1 ? 'column' : 'columns'}{meta}</p></header>
    <div className="data-toolbar data-table-toolbar" role="toolbar" aria-label="Table tools">
      <JoinPanel workspaceId={workspaceId} connectionId={connectionId} spec={spec} tables={tables} openRequest={combineRequest} onAdd={join => change({joins: addJoin({...base, joins: view.joins}, join).joins})}/>
      <Button variant="ghost" size="sm" aria-expanded={panel.summarize} onClick={toggleSummary}><Sigma size={16} strokeWidth={1.5}/>Summarize</Button>
      <ColumnPicker tables={tableColumns} visible={visible?.length ? visible : null} onChange={next => change({visible: next})} disabled={!!view.summary} disabledReason="The summary chooses the columns."/>
      <Button variant="ghost" size="sm" aria-expanded={panel.sql} onClick={() => setPanel(previous => ({...previous, sql: !previous.sql}))}><Code2 size={16} strokeWidth={1.5}/>Show SQL</Button>
      <Button variant="ghost" size="sm" onClick={() => void exportCsv()} disabled={!shown || exporting || loading}><Download size={16} strokeWidth={1.5}/>{exporting ? 'Exporting…' : 'Export CSV'}</Button>
    </div>
    {exportError && <p role="alert">{exportError}</p>}
    <JoinList spec={spec} infos={state.compile?.joins || []} onRemove={alias => change({...removeJoinView(view, base, alias)})}/>
    {panel.summarize && view.summary && <SummarizePanel options={options} summary={view.summary} label={labelFor} buckets={buckets} onChange={summary => change({summary, sort: []})} onClear={() => {setPanel(previous => ({...previous, summarize: false})); change({summary: null, sort: []});}}/>}
    <FilterChips filters={view.filters} context={{label: labelFor, kind: kindFor}} options={options} allowedOps={allowedOps} onChange={filters => change({filters})}/>
    {panel.sql && <ShowSql compile={state.compile} errors={state.errors}/>}
    {state.compile?.warnings.filter(warning => warning.code === 'text_date' || warning.code === 'many_to_many').map((warning, index) => <p className="data-hint" role="status" key={index}>{warning.message}</p>)}
    <section className="data-results" aria-label="Results" aria-busy={loading}>
      {shown?.job.result?.truncated && <p className="data-muted">Preview truncated. CSV contains the available preview.</p>}
      <div className="data-results-body" data-stale={loading && shown ? true : undefined}>{results}</div>
    </section>
  </section>;
}

function removeJoinView(view: View, base: QuerySpec, alias: string): Partial<View> {
  const next = removeJoin({...base, joins: view.joins, filters: view.filters, sort: view.sort, select: view.visible || []}, alias);
  return {joins: next.joins, filters: next.filters, sort: next.sort, visible: next.select.length ? next.select : null};
}
