import {Button} from '../../components/ui/button';
import type {SchemaTable} from '../../lib/query-spec';
import {tableKey} from './DataNavigation';
import type {SourceController} from './use-source';

type Props = {name: string; label: string; controller: SourceController; engineCanLoad: boolean; onClose: () => void};
/** Table tab shell: shows the table's columns. The results grid, filters and joins mount here next. */
export default function TableTab({name, label, controller, engineCanLoad, onClose}: Props) {
  const status = controller.schemaStatus, table: SchemaTable | undefined = controller.schema?.tables.find(row => tableKey(row) === name);
  if (status === 'idle') return <div className="data-empty-inline"><p>Load the schema to open {label}.</p><Button onClick={controller.loadSchema} disabled={!engineCanLoad}>Load schema</Button></div>;
  if (status === 'loading') return <div className="data-list" role="status" aria-label={'Loading ' + label}>{[0, 1, 2, 3, 4].map(index => <span className="data-skeleton-row" key={index}/>)}</div>;
  if (status === 'error') return <div className="data-inline-error"><p role="alert">{controller.schemaError}</p>{controller.needsTest ? <Button variant="secondary" size="sm" onClick={controller.testConnection} disabled={controller.testing}>{controller.testing ? 'Testing…' : 'Test connection'}</Button> : <Button variant="secondary" size="sm" onClick={controller.loadSchema}>Try again</Button>}</div>;
  if (!table) return <div className="data-empty-inline"><p>{label} is no longer in this source.</p><Button variant="secondary" onClick={onClose}>Close tab</Button></div>;
  return <section className="data-table-tab" aria-label={'Table ' + label}>
    <header><h1>{label}</h1><p className="data-muted">{table.columns.length.toLocaleString()} {table.columns.length === 1 ? 'column' : 'columns'}{typeof table.row_estimate === 'number' ? ' · About ' + table.row_estimate.toLocaleString() + ' rows' : ''}</p></header>
    {table.readable === false && <p className="data-muted">{table.reason || 'This table cannot be read.'}</p>}
    <table className="data-table-list">
      <thead><tr><th scope="col">Column</th><th scope="col">Type</th><th scope="col">Key</th></tr></thead>
      <tbody>{table.columns.map(column => <tr key={column.name}><th scope="row">{column.name}</th><td>{column.type || 'unknown'}</td><td>{column.primary_key ? 'Primary key' : ''}</td></tr>)}</tbody>
    </table>
  </section>;
}
