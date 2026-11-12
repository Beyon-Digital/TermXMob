import {useEffect, useId, useState} from 'react';
import {Link2, Plus, X} from 'lucide-react';
import {Button} from '../../components/ui/button';
import {Popover, PopoverContent, PopoverTrigger} from '../../components/ui/popover';
import {Select} from '../../components/ui/select';
import {fetchRelations} from '../../lib/data-query';
import {dataError} from '../../lib/data-studio';
import {MAX_JOINS, humanize, specAliases, uniqueAlias} from '../../lib/query-builder';
import type {Join, JoinInfo, QuerySpec, Relation, SchemaTable} from '../../lib/query-spec';

type Props = {
  workspaceId: string; connectionId: string; spec: QuerySpec; tables: SchemaTable[]; onAdd: (join: Join) => void;
};
const LIMIT_REASON = 'At most 4 tables can be combined.';
const tableName = (table: SchemaTable) => table.name;

/** Turn a suggested link into a join from the base table. */
export function joinFromRelation(spec: QuerySpec, relation: Relation): Join | null {
  const base = spec.from.table, taken = specAliases(spec);
  const other = relation.child_table === base ? relation.parent_table : relation.parent_table === base ? relation.child_table : null;
  if (!other) return null;
  const alias = uniqueAlias(other, taken), mine = relation.child_table === base ? relation.child_columns : relation.parent_columns, theirs = relation.child_table === base ? relation.parent_columns : relation.child_columns;
  return {kind: 'left', table: other, alias, on: mine.map((column, index) => ({left: {table_alias: spec.from.alias, column}, right: {table_alias: alias, column: theirs[index]}}))};
}

/** "Combine with another table": suggested links as sentences, then a manual builder. */
export default function JoinPanel({workspaceId, connectionId, spec, tables, onAdd}: Props) {
  const id = useId(), [open, setOpen] = useState(false), [tick, setTick] = useState(0);
  const [relations, setRelations] = useState<{status: 'loading' | 'ready' | 'error'; rows: Relation[]; error: string}>({status: 'loading', rows: [], error: ''});
  const full = spec.joins.length >= MAX_JOINS, aliases = specAliases(spec);
  const named = (alias: string) => alias === spec.from.alias ? spec.from.table : spec.joins.find(join => join.alias === alias)?.table || alias;
  const columnsOf = (name: string) => tables.find(table => table.name === name || tableName(table) === name)?.columns || [];
  const [table, setTable] = useState(''), [from, setFrom] = useState(''), [to, setTo] = useState(''), [kind, setKind] = useState<'left' | 'inner'>('left');
  useEffect(() => {
    if (!open) return;
    const abort = new AbortController();
    setRelations(previous => ({...previous, status: 'loading', error: ''}));
    fetchRelations(workspaceId, connectionId, spec.from.table, abort.signal).then(result => {
      if (!abort.signal.aborted) setRelations({status: 'ready', rows: result.relations, error: ''});
    }).catch(reason => {if (!abort.signal.aborted) setRelations({status: 'error', rows: [], error: dataError(reason)});});
    return () => abort.abort();
  }, [open, workspaceId, connectionId, spec.from.table, tick]);
  const suggestions = relations.rows.map(relation => ({relation, join: joinFromRelation(spec, relation)})).filter((row): row is {relation: Relation; join: Join} => !!row.join);
  const fromOptions = aliases.flatMap(alias => columnsOf(named(alias)).map(column => ({value: alias + '\u0000' + column.name, label: humanize(named(alias)) + ' · ' + column.name})));
  const ready = !!table && !!from && !!to && !full;
  function addManual() {
    if (!ready) return;
    const [leftAlias, leftColumn] = from.split('\u0000'), alias = uniqueAlias(table, aliases);
    onAdd({kind, table, alias, on: [{left: {table_alias: leftAlias, column: leftColumn}, right: {table_alias: alias, column: to}}]});
    setTable(''); setFrom(''); setTo(''); setOpen(false);
  }
  return <Popover open={open} onOpenChange={setOpen}>
    <PopoverTrigger asChild><Button variant="ghost" size="sm"><Link2 size={16} strokeWidth={1.5}/>Combine with another table</Button></PopoverTrigger>
    <PopoverContent className="data-join-popover" aria-label="Combine with another table">
      <h2 className="data-popover-title">Combine with another table</h2>
      {full && <p className="data-muted" role="status">{LIMIT_REASON}</p>}
      <section aria-label="Suggested links" className="data-join-suggestions">
        <h3 className="data-popover-subtitle">Suggested</h3>
        {relations.status === 'loading' && <div role="status" aria-label="Loading suggestions">{[0, 1].map(row => <span className="data-skeleton-row" key={row}/>)}</div>}
        {relations.status === 'error' && <div className="data-inline-error"><p role="alert">{relations.error}</p><Button variant="secondary" size="sm" onClick={() => setTick(value => value + 1)}>Try again</Button></div>}
        {relations.status === 'ready' && !suggestions.length && <p className="data-muted">No suggested links. Choose a table below.</p>}
        {relations.status === 'ready' && <ul className="data-suggestion-list">{suggestions.map(({relation, join}, index) => {
          const added = spec.joins.some(row => row.table === join.table);
          return <li key={index}><span>{relation.sentence}{relation.confidence === 'likely' && <span className="data-muted"> · Likely</span>}</span>
            <Button variant="ghost" size="sm" disabled={full || added} aria-label={'Add: ' + relation.sentence} onClick={() => {onAdd(join); setOpen(false);}}><Plus size={16} strokeWidth={1.5}/>{added ? 'Added' : 'Add'}</Button></li>;
        })}</ul>}
      </section>
      <section aria-label="Choose a link yourself" className="data-join-manual">
        <h3 className="data-popover-subtitle">Choose yourself</h3>
        <div className="data-field"><label htmlFor={id + '-table'}>Table to combine</label>
          <Select id={id + '-table'} value={table} disabled={full} onChange={event => {setTable(event.target.value); setTo('');}}><option value="">Choose a table</option>
            {tables.filter(row => row.readable !== false).map(row => <option key={tableName(row)} value={tableName(row)}>{tableName(row)}</option>)}</Select></div>
        <div className="data-field"><label htmlFor={id + '-from'}>This column</label>
          <Select id={id + '-from'} value={from} disabled={full || !table} onChange={event => setFrom(event.target.value)}><option value="">Choose a column</option>{fromOptions.map(row => <option key={row.value} value={row.value}>{row.label}</option>)}</Select></div>
        <div className="data-field"><label htmlFor={id + '-to'}>Matches this column</label>
          <Select id={id + '-to'} value={to} disabled={full || !table} onChange={event => setTo(event.target.value)}><option value="">Choose a column</option>{columnsOf(table).map(column => <option key={column.name} value={column.name}>{column.name}</option>)}</Select></div>
        <div className="data-field"><label htmlFor={id + '-kind'}>Rows to keep</label>
          <Select id={id + '-kind'} value={kind} disabled={full} onChange={event => setKind(event.target.value as 'left' | 'inner')}>
            <option value="left">Keep all {humanize(spec.from.table).toLowerCase()} rows</option><option value="inner">Only rows that match</option></Select></div>
        <div className="data-popover-actions"><Button variant="secondary" size="sm" disabled={!ready} onClick={addManual}>Add table</Button></div>
      </section>
    </PopoverContent>
  </Popover>;
}

/** Combined tables as readable sentences, each removable, with the row-repeat hint when the server reports one. */
export function JoinList({spec, infos, onRemove}: {spec: QuerySpec; infos: JoinInfo[]; onRemove: (alias: string) => void}) {
  if (!spec.joins.length) return null;
  return <ul className="data-join-list" aria-label="Combined tables">{spec.joins.map(join => {
    const info = infos.find(row => row.alias === join.alias), name = humanize(join.table), pairs = join.on.map(pair => pair.left.column + ' matches ' + pair.right.column).join(' and ');
    return <li key={join.alias}>
      <span>Combined with {name}: {pairs}{join.kind === 'inner' ? ' (only rows that match)' : ''}</span>
      {info?.fan_out === 'many' && info.message && <span className="data-hint" role="status">{info.message}</span>}
      <Button variant="ghost" size="icon" aria-label={'Remove ' + name} title={'Remove ' + name} onClick={() => onRemove(join.alias)}><X size={16} strokeWidth={1.5}/></Button>
    </li>;
  })}</ul>;
}
